"""Panel y comandos Telcel: /telcel, /tcm, /tcf, /tcr.

Abierto a usuarios (2026-11-05): megas gratis, factura y recarga con costo
de 10 créditos. Saldo (/tcs) oculto del panel hasta que funcione.
Acceso: membresía activa o admin. Solo super admins exentos de cobro;
admins normales pagan igual que el resto (misma regla que /rfc y /sms).

Fuente: evidencias/apiTelcel (2026-10-20). Factura y recarga se cobran
por créditos (costo definido en bot.config). Flujo de cobro idéntico a /rfc:
1. /tX → valida entrada, muestra costo y saldo, pide confirmar/cancelar.
   /tcm (megas) no pide confirmación: es gratis, ejecuta directo.
2. Confirmar → descuenta créditos y ejecuta el gate en un hilo aparte
   (red + criptografía + WASM: nunca bloquean el event loop).
   Error técnico del gate → reembolso automático.
   Declinación del banco/Telcel → resultado negativo, se mantiene el cobro
   (misma política que cualquier gate de chequeo).
3. Cancelar → no se cobra nada.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Dict, Optional

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest

from bot.config import (
    TELCEL_MEGAS_COST,
    TELCEL_FACTURA_COST,
    TELCEL_RECARGA_COST,
    TELCEL_COOLDOWN_S,
)
from bot.services.admins import is_admin, is_super_admin
from bot.services.credits import deduct_credits, get_balance, refund_credits
from bot.services.memberships import get_membership, is_membership_active
from bot.services.telcel import gate_megas, gate_factura, gate_recarga
from bot.services.users import register_user
from bot.utils.formatting import escape_html, strip_custom_emojis
from bot.utils.bin_lookup import get_card_info
from bot.keyboards import telcel_menu, telcel_gate_menu

router = Router()
logger = logging.getLogger(__name__)

# Costo por gate y acción (leídos de .env con valores por defecto).
# 2026-11-05: megas gratis, factura y recarga a 10 créditos.
_COSTS: Dict[str, int] = {
    "megas": TELCEL_MEGAS_COST,
    "factura": TELCEL_FACTURA_COST,
    "recarga": TELCEL_RECARGA_COST,
}

_HEADER_EMOJI_ID = "5454074580010295588"
_LIVE_EMOJI_ID = "5021905410089550576"
_DEAD_EMOJI_ID = "5019523782004441717"

PENDING_TTL_S = 120
_last_telcel_use: Dict[int, float] = {}
# Solicitudes pendientes de confirmación: user_id -> (monotonic_ts, datos)
_pending_telcel: Dict[int, tuple[float, dict]] = {}


async def _can_use_telcel(user_id: int) -> bool:
    """Admins siempre pueden; el resto necesita membresía activa."""
    if await is_admin(user_id):
        return True
    membership = await get_membership(user_id)
    return is_membership_active(membership)


def _denied_text() -> str:
    return (
        "❌ <b>Acceso denegado</b>\n\n"
        "Los comandos de Telcel requieren una membresía activa.\n\n"
        "Contacta a un administrador para adquirir acceso."
    )


def _te(fallback: str) -> str:
    return f'<tg-emoji emoji-id="{_HEADER_EMOJI_ID}">{fallback}</tg-emoji>'


def _live_emoji(fallback: str = "✅") -> str:
    return f'<tg-emoji emoji-id="{_LIVE_EMOJI_ID}">{fallback}</tg-emoji>'


def _dead_emoji() -> str:
    return f'<tg-emoji emoji-id="{_DEAD_EMOJI_ID}">Dead</tg-emoji>'


def _telcel_panel_text() -> str:
    t = _te
    return (
        "<blockquote>\n"
        f"{t('📱')} <b>TELCEL — HADES</b>\n"
        "Consulta y operaciones de líneas Telcel.\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"{t('📶')} /megas — Consumo de megas — <b>Gratis</b>\n"
        f"{t('🧾')} /factura — Pago de factura con tarjeta — {_COSTS['factura']} créditos\n"
        f"{t('⚡')} /recarga — Recarga con tarjeta — {_COSTS['recarga']} créditos\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Selecciona una opción para ver el formato de uso.\n"
        "</blockquote>"
    )


def _telcel_detail_text(key: str) -> str:
    t = _te
    cost = _COSTS.get(key, 0)
    cost_line = "Gratis." if cost == 0 else f"Costo: {cost} créditos por consulta.\n"
    cost_line_op = "Gratis." if cost == 0 else f"Costo: {cost} créditos por operación.\n"
    details = {
        "megas": (
            "<blockquote>\n"
            f"{t('📶')} <b>CONSUMO DE MEGAS — /megas</b>\n"
            "Consulta los consumos de datos de una línea Telcel.\n\n"
            "Uso:\n"
            "<code>/megas 5512345678</code>\n\n"
            f"Ejemplo:\n<code>/megas 5512345678</code>\n\n"
            "Solo el número (10 dígitos).\n"
            f"{cost_line}"
            "</blockquote>"
        ),
        "factura": (
            "<blockquote>\n"
            f"{t('🧾')} <b>PAGO DE FACTURA — /factura</b>\n"
            "Paga el saldo pendiente (Amigo Paguitos) de una cuenta Telcel\n"
            "con una tarjeta.\n\n"
            "Uso:\n"
            "<code>/factura cc|mes|año|cvv|cuenta</code>\n\n"
            f"Ejemplo:\n"
            "<code>/factura 4532640527811643|12|2025|123|5512340000</code>\n\n"
            "La cuenta es el número de cuenta Telcel (sin letras).\n"
            f"{cost_line_op}"
            "</blockquote>"
        ),
        "recarga": (
            "<blockquote>\n"
            f"{t('⚡')} <b>RECARGA — /recarga</b>\n"
            "Recarga una línea Telcel con tarjeta.\n\n"
            "Uso:\n"
            "<code>/recarga cc|mes|año|cvv|teléfono|tipo|monto</code>\n\n"
            f"Ejemplo:\n<code>/recarga 4532640527811643|12|2025|123|5512345678|saldo|100</code>\n\n"
            "Tipos: saldo, amigo_sin_limite, internet_amigo, internet_tiempo.\n"
            "Montos por tipo (ejemplo): saldo 10..500, amigo_sin_limite "
            "10..2400, internet_amigo 10..500, internet_tiempo 10..99.\n"
            f"{cost_line_op}"
            "</blockquote>"
        ),
    }
    return details.get(key, "")


def _user_error(text: str) -> str:
    return f"<blockquote>\n{text}\n</blockquote>"


def _confirm_text(action_label: str, balance: int) -> str:
    cost = _COSTS[action_label]
    return (
        "<blockquote>\n"
        f"📱 <b>Telcel — {action_label.title()}</b>\n\n"
        f"Esta operación cuesta <b>{cost}</b> créditos.\n"
        f"Tu saldo actual: <b>{balance}</b> créditos.\n"
        f"Saldo después del cobro: <b>{balance - cost}</b> créditos.\n\n"
        "¿Confirmas el cobro?"
        "\n</blockquote>"
    )


def _confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Confirmar", callback_data="telcel_confirm", style="success"
                ),
                InlineKeyboardButton(
                    text="❌ Cancelar", callback_data="telcel_cancel", style="danger"
                ),
            ]
        ]
    )


def _processing(action_label: str) -> str:
    name = {"megas": "Megas", "factura": "Factura", "recarga": "Recarga"}.get(
        action_label, action_label.title()
    )
    verb = {
        "megas": "washando megas de tu número, aguanta men...",
        "factura": "pagando tu factura, aguanta men...",
        "recarga": "metiendo tu recarga, aguanta men...",
    }.get(action_label, "procesando tu solicitud, aguanta men...")
    return (
        f"🔍<b>Verificando con Telcel — {name}...</b>\n"
        f"⠋ {verb}\n"
        f"<code>▓░░░░░░░░░░░</code>"
    )


def _bin_block(card_data: str) -> str:
    parts = card_data.split("|")
    bin_info = get_card_info(parts[0]) if len(parts) == 4 else None
    if not bin_info:
        return ""
    e = _te
    return (
        "\n<blockquote>\n"
        f"{e('💎')} Marca: {escape_html(bin_info.get('Marca', 'Desconocido'))}\n"
        f"{e('💎')} Tipo de tarjeta: {escape_html(bin_info.get('Tipo de tarjeta', 'Desconocido'))}\n"
        f"{e('💎')} Nivel de tarjeta: {escape_html(bin_info.get('Nivel de tarjeta', 'Desconocido'))}\n"
        f"{e('💎')} Banco: {escape_html(bin_info.get('Banco', 'Desconocido'))}\n"
        f"{e('💎')} Teléfono: {escape_html(bin_info.get('Teléfono', 'Desconocido'))}\n"
        f"{e('💎')} País: {escape_html(bin_info.get('País', 'Desconocido'))}\n"
        "</blockquote>"
    )


def _card_gate_result_text(card_data: str, result: dict, gate_label: str, command: str) -> str:
    e = _te
    status_raw = result.get("status", "error")

    if status_raw in ("ok", "approved"):
        status_icon = "✅ Aprobada"
    elif status_raw == "declined":
        status_icon = "❌ Declinada"
    else:
        status_icon = "⚠️ Error"

    lines = [
        f"💳 <b>Tarjeta:</b> <code>{escape_html(card_data)}</code>",
        f"{status_icon.split()[0]} <b>Status:</b> {escape_html(result.get('message', ''))}",
    ]

    # Cadenas informativas específicas de cada gate Telcel.
    if gate_label == "factura":
        cuenta = result.get("cuenta")
        if cuenta:
            lines.append(f"🧾 <b>Cuenta:</b> <code>{escape_html(cuenta)}</code>")
    elif gate_label == "recarga":
        phone = result.get("phone")
        if phone:
            lines.append(f"📱 <b>Teléfono:</b> <code>{escape_html(phone)}</code>")
        tipo = result.get("tipo")
        monto = result.get("monto")
        if tipo:
            lines.append(f"📦 <b>Tipo:</b> {escape_html(str(tipo))}")
        if monto is not None:
            lines.append(f"💵 <b>Monto:</b> {monto} MXN")
    charge_amount = result.get("chargeAmount")
    if charge_amount not in (None, ""):
        lines.append(f"💰 <b>Cobro:</b> {escape_html(str(charge_amount))} MXN")
    folio = result.get("folioProvisioning")
    if folio:
        lines.append(f"🎫 <b>Folio:</b> <code>{escape_html(str(folio))}</code>")
    service = result.get("serviceName")
    if service:
        lines.append(f"🏷️ <b>Servicio:</b> {escape_html(str(service))}")

    title = {"factura": "PAGO DE FACTURA", "recarga": "RECARGA"}.get(gate_label, gate_label.upper())
    icon = {"factura": "🧾", "recarga": "⚡"}.get(gate_label, "📱")

    return (
        "<blockquote>\n"
        f"{e(icon)} <b>{title}</b>\n\n"
        + "\n".join(lines)
        + "\n</blockquote>"
        + _bin_block(card_data)
    )


def _megas_result_text(result: dict) -> str:
    def ce(emoji_id: str, fallback: str) -> str:
        return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'

    phone = result.get("phone", "")
    perfil = result.get("perfil", "")
    consumos = result.get("consumos", {}) or {}
    data = (consumos.get("response") or {}).get("data") or {}

    lista = data.get("consumos") or []
    fecha_act = data.get("fechaActualizacion", "")
    fecha_cons = data.get("fechaConsulta", "")
    mb_fb = data.get("mbUsadosFacebookTwitter", 0.0) or 0.0
    mb_wa = data.get("mbUsadosWhatsapp", 0.0) or 0.0
    tiene_fb = data.get("tieneFacebookTwitter", False)
    tiene_wa = data.get("tieneWhatsapp", False)

    lines = [
        f"{ce('4994498137147048646', '📱')} <b>Teléfono:</b> <code>{escape_html(phone)}</code>",
        f"{ce('5310068003067411632', '👤')} <b>Perfil:</b> {escape_html(str(perfil))}",
        f"{ce('5021905410089550576', '✅')} <b>Status:</b> OK",
    ]
    if fecha_act:
        lines.append(f"{ce('5348289410955754971', '🕒')} <b>Actualizado:</b> {escape_html(str(fecha_act))}")
    if fecha_cons:
        lines.append(f"{ce('5017088445353296841', '🔍')} <b>Consulta:</b> {escape_html(str(fecha_cons))}")
    lines.append("")
    lines.append(f"{ce('5053076169600009433', '📊')} <b>Consumos:</b>")
    if lista:
        for c in lista[:10]:
            nombre = c.get("nombre") or c.get("descripcion") or "Paquete"
            usado = c.get("usado") or c.get("mbUsados") or c.get("consumo") or ""
            total = c.get("total") or c.get("mbTotales") or ""
            detalle = f"{usado}/{total}" if total else str(usado)
            lines.append(f"{ce('5019413195186504264', '▪️')} {escape_html(str(nombre))}: {escape_html(detalle)}")
    else:
        lines.append(f"{ce('5019413195186504264', '▪️')} Sin consumos registrados en este corte.")
    lines.append("")
    lines.append(
        f"{ce('5019284526556250607', '📘')} <b>Facebook/Twitter:</b> {'✅' if tiene_fb else '❌'}"
        f" ({float(mb_fb):.1f} MB)"
    )
    lines.append(
        f"{ce('5294128644992541824', '💬')} <b>WhatsApp:</b> {'✅' if tiene_wa else '❌'}"
        f" ({float(mb_wa):.1f} MB)"
    )

    return (
        "<blockquote>\n"
        f"{ce('5053076169600009433', '📶')} <b>CONSUMO DE MEGAS</b>\n\n"
        + "\n".join(lines)
        + "\n</blockquote>"
    )


def _telcel_error_text() -> str:
    return _user_error(
        "⚠️ <b>No se pudo completar la operación con Telcel.</b>\n\n"
        "Inténtalo de nuevo más tarde. Se reembolsó el cobro si se realizó."
    )


def _parse_telcel_input(command: str, text: str) -> tuple[Optional[dict], Optional[str]]:
    """Valida la entrada del comando. Devuelve (datos, error_msg)."""
    text = text.strip()
    # Mapeo comando -> acción interna (coherente con _run_telcel_gate,
    # _COSTS y _confirm_text, que usan megas/factura/recarga).
    _CMD_TO_ACTION = {"tcm": "megas", "tcf": "factura", "tcr": "recarga"}
    command = _CMD_TO_ACTION.get(command, command)
    if command == "megas":
        num = "".join(c for c in text if c.isdigit())
        if len(num) != 10:
            return None, "número inválido (se esperan 10 dígitos)"
        msisdn = f"+52{num}"
        return {"action": "megas", "phone": num, "msisdn": msisdn}, None

    if command == "factura":
        parts = [p.strip() for p in text.split("|")]
        if len(parts) != 5:
            return None, "formato inválido: cc|mes|año|cvv|cuenta"
        card, cuenta = "|".join(parts[:4]), parts[4]
        if not re.fullmatch(r"\d{8,}", cuenta):
            return None, "cuenta inválida (número de cuenta sin letras)"
        from bot.services.telcel.gates import clean_card

        if clean_card(card) is None:
            return None, "tarjeta inválida"
        return {"action": "factura", "tarjeta": card, "cuenta": cuenta}, None

    if command == "recarga":
        parts = [p.strip() for p in text.split("|")]
        if len(parts) != 7:
            return None, "formato inválido: cc|mes|año|cvv|teléfono|tipo|monto"
        card, phone_raw, tipo, monto_raw = "|".join(parts[:4]), parts[4], parts[5], parts[6]
        if tipo not in ("saldo", "amigo_sin_limite", "internet_amigo", "internet_tiempo"):
            return None, "tipo inválido (saldo, amigo_sin_limite, internet_amigo, internet_tiempo)"
        try:
            monto = int(monto_raw)
        except ValueError:
            return None, "monto inválido (número entero)"
        from bot.services.telcel.gates import clean_card

        if clean_card(card) is None:
            return None, "tarjeta inválida"
        num = "".join(c for c in phone_raw if c.isdigit())
        if len(num) != 10:
            return None, "teléfono inválido (10 dígitos)"
        return {
            "action": "recarga",
            "tarjeta": card,
            "telefono": num,
            "tipo": tipo,
            "monto": monto,
        }, None

    return None, "comando no reconocido"


def _has_photo(message) -> bool:
    return message.photo is not None and len(message.photo) > 0


async def _edit_or_send(callback, text: str, reply_markup=None):
    try:
        if _has_photo(callback.message):
            await callback.message.edit_caption(
                caption=text, parse_mode=ParseMode.HTML, reply_markup=reply_markup
            )
        else:
            await callback.message.edit_text(
                text, parse_mode=ParseMode.HTML, reply_markup=reply_markup
            )
    except TelegramBadRequest as exc:
        if "ENTITY_TEXT_INVALID" in str(exc):
            plain_text = strip_custom_emojis(text)
            if _has_photo(callback.message):
                await callback.message.edit_caption(
                    caption=plain_text, parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup,
                )
            else:
                await callback.message.edit_text(
                    plain_text, parse_mode=ParseMode.HTML, reply_markup=reply_markup
                )
        else:
            raise


async def _run_telcel_gate(datos: dict) -> dict:
    action = datos["action"]
    if action == "megas":
        return await asyncio.to_thread(gate_megas, datos["phone"])
    if action == "factura":
        return await asyncio.to_thread(gate_factura, datos["tarjeta"], datos["cuenta"])
    if action == "recarga":
        return await asyncio.to_thread(
            gate_recarga,
            datos["tarjeta"],
            datos["telefono"],
            datos["tipo"],
            datos["monto"],
        )
    return {"status": "error", "message": "acción desconocida"}


async def _send_result(target_message: Message, datos: dict, result: dict, command: str):
    action = datos["action"]
    gate_label = action
    if action == "megas":
        text = _megas_result_text(result)
    else:
        text = _card_gate_result_text(
            datos["tarjeta"], result, gate_label, command
        )
    try:
        await target_message.answer(text, parse_mode=ParseMode.HTML)
    except TelegramBadRequest:
        await target_message.answer(strip_custom_emojis(text), parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# Comando del panel
# ---------------------------------------------------------------------------
@router.message(Command("telcel"))
async def cmd_telcel_panel(message: Message):
    if not await _can_use_telcel(message.from_user.id):
        await message.answer(_denied_text(), parse_mode=ParseMode.HTML)
        return
    user = message.from_user
    await register_user(user.id, user.username, user.full_name)
    await message.answer(
        _telcel_panel_text(),
        parse_mode=ParseMode.HTML,
        reply_markup=telcel_menu(),
    )


@router.callback_query(F.data == "menu_telcel")
async def cb_telcel_panel(callback: CallbackQuery):
    if not await _can_use_telcel(callback.from_user.id):
        await callback.answer(
            "Necesitas una membresía activa para usar Telcel.", show_alert=True
        )
        return
    await _edit_or_send(callback, _telcel_panel_text(), telcel_menu())
    await callback.answer()


@router.callback_query(F.data.startswith("menu_telg_"))
async def cb_telcel_detail(callback: CallbackQuery):
    if not await _can_use_telcel(callback.from_user.id):
        await callback.answer(
            "Necesitas una membresía activa para usar Telcel.", show_alert=True
        )
        return
    key = callback.data.removeprefix("menu_telg_")
    text = _telcel_detail_text(key)
    if not text:
        await callback.answer()
        return
    await _edit_or_send(callback, text, telcel_gate_menu())
    await callback.answer()


# ---------------------------------------------------------------------------
# Comandos Telcel.
# /megas: gratis → sin confirmación, ejecuta directo.
# /factura y /recarga: paso 1 valida y pide confirmación del cobro.
# ---------------------------------------------------------------------------
async def _telcel_step1(message: Message, command: str):
    if not await _can_use_telcel(message.from_user.id):
        await message.answer(_denied_text(), parse_mode=ParseMode.HTML)
        return
    user = message.from_user
    await register_user(user.id, user.username, user.full_name)

    if not is_super_admin(user.id):
        now = time.monotonic()
        last = _last_telcel_use.get(user.id, 0.0)
        if now - last < TELCEL_COOLDOWN_S:
            espera = int(TELCEL_COOLDOWN_S - (now - last))
            await message.answer(
                _user_error(
                    "⏳ <b>Espera un momento antes de usar este comando de nuevo.</b>\n\n"
                    f"Inténtalo en {espera} segundos."
                ),
                parse_mode=ParseMode.HTML,
            )
            return

    args = message.text.split(maxsplit=1)
    datos, err = _parse_telcel_input(command, args[1] if len(args) > 1 else "")
    if err:
        await message.answer(
            _user_error(f"❌ <b>Entrada inválida.</b>\n\n{err}"),
            parse_mode=ParseMode.HTML,
        )
        return

    # Megas es gratis: ejecuta directo sin confirmación ni cobro.
    if datos["action"] == "megas":
        await _execute_telcel_gate(message, datos)
        return

    balance = await get_balance(user.id)
    cost = _COSTS[datos["action"]]
    if not is_super_admin(user.id) and balance < cost:
        await message.answer(
            _user_error(
                "❌ <b>No tienes créditos suficientes.</b>\n\n"
                f"Esta operación cuesta {cost} créditos y tu saldo es {balance}.\n"
                "Contacta a un administrador para recargar."
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    _pending_telcel[user.id] = (time.monotonic(), datos)
    await message.answer(
        _confirm_text(datos["action"], balance),
        parse_mode=ParseMode.HTML,
        reply_markup=_confirm_kb(),
    )


async def _execute_telcel_gate(message: Message, datos: dict) -> None:
    """Ejecuta el gate sin cobrar (megas gratis y super admins exentos).

    Muestra el mensaje de procesamiento, corre el gate en hilo aparte
    (red pesada + WASM) y envía el resultado. No toca créditos.
    """
    user_id = message.from_user.id
    try:
        processing_msg = await message.answer(
            _processing(datos["action"]), parse_mode=ParseMode.HTML
        )
    except TelegramBadRequest:
        processing_msg = message

    try:
        result = await _run_telcel_gate(datos)
    except Exception:
        logger.exception("Telcel gate %s falló", datos["action"])
        await _edit_or_send_processing(processing_msg, _telcel_error_text())
        return

    ok = result.get("status") in ("ok", "approved")
    if not ok and result.get("status") != "declined":
        await _edit_or_send_processing(processing_msg, _telcel_error_text())
        return

    _last_telcel_use[user_id] = time.monotonic()
    command = {"megas": "tcm", "factura": "tcf", "recarga": "tcr"}[
        datos["action"]
    ]
    try:
        await processing_msg.delete()
    except Exception:
        pass
    await _send_result(message, datos, result, command)


@router.message(Command("megas", "tcm"))
async def cmd_tcm(message: Message):
    await _telcel_step1(message, "tcm")


@router.message(Command("factura", "tcf"))
async def cmd_tcf(message: Message):
    await _telcel_step1(message, "tcf")


@router.message(Command("recarga", "tcr"))
async def cmd_tcr(message: Message):
    await _telcel_step1(message, "tcr")


# ---------------------------------------------------------------------------
# Paso 2: confirmar / cancelar (solo factura y recarga; megas no llega aquí)
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "telcel_confirm")
async def cb_telcel_confirm(callback: CallbackQuery):
    if not await _can_use_telcel(callback.from_user.id):
        await callback.answer(
            "Necesitas una membresía activa para usar Telcel.", show_alert=True
        )
        return
    user_id = callback.from_user.id
    pending = _pending_telcel.pop(user_id, None)
    if pending is None:
        await callback.answer(
            "Sin solicitud pendiente. Usa /megas, /factura o /recarga de nuevo.",
            show_alert=True,
        )
        return
    requested_at, datos = pending
    if time.monotonic() - requested_at > PENDING_TTL_S:
        await callback.answer("La solicitud expiró. Usa el comando de nuevo.", show_alert=True)
        try:
            await callback.message.edit_text(
                _user_error("⌛ <b>La solicitud expiró.</b>\n\nUsa el comando de nuevo."),
                parse_mode=ParseMode.HTML,
            )
        except TelegramBadRequest:
            return

    cost = _COSTS[datos["action"]]
    exempt = is_super_admin(user_id)

    if not exempt:
        success, new_balance = await deduct_credits(
            user_id, cost, f"Uso Telcel {datos['action']}"
        )
        if not success:
            await callback.answer("Sin créditos suficientes.", show_alert=True)
            await callback.message.edit_text(
                _user_error(
                    "❌ <b>No tienes créditos suficientes.</b>\n\n"
                    f"Esta operación cuesta {cost} créditos "
                    f"y tu saldo es {new_balance}.\n"
                    "Contacta a un administrador para recargar."
                ),
                parse_mode=ParseMode.HTML,
            )
            return

    # Ejecutar el gate en hilo aparte (red pesada + WASM).
    try:
        processing_msg = await callback.message.edit_text(
            _processing(datos["action"]), parse_mode=ParseMode.HTML
        )
    except TelegramBadRequest:
        processing_msg = callback.message

    try:
        result = await _run_telcel_gate(datos)
    except Exception:
        logger.exception("Telcel gate %s falló", datos["action"])
        if not exempt:
            await refund_credits(
                user_id, cost, f"Reembolso error Telcel {datos['action']}"
            )
        await _edit_or_send_processing(
            processing_msg, _telcel_error_text()
        )
        await callback.answer()
        return

    ok = result.get("status") in ("ok", "approved")
    if not ok and result.get("status") != "declined":
        # Error técnico: reembolso si se cobró.
        if not exempt:
            await refund_credits(
                user_id, cost, f"Reembolso error Telcel {datos['action']}"
            )
        await _edit_or_send_processing(
            processing_msg, _telcel_error_text()
        )
        await callback.answer()
        return

    _last_telcel_use[user_id] = time.monotonic()
    command = {"megas": "tcm", "factura": "tcf", "recarga": "tcr"}[
        datos["action"]
    ]
    await _send_result(callback.message, datos, result, command)
    try:
        await processing_msg.delete()
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data == "telcel_cancel")
async def cb_telcel_cancel(callback: CallbackQuery):
    if not await _can_use_telcel(callback.from_user.id):
        await callback.answer(
            "Necesitas una membresía activa para usar Telcel.", show_alert=True
        )
        return
    _pending_telcel.pop(callback.from_user.id, None)
    await callback.answer("Solicitud cancelada.")
    try:
        await callback.message.edit_text(
            _user_error("❌ <b>Operación cancelada.</b>\n\nNo se cobró nada."),
            parse_mode=ParseMode.HTML,
        )
    except TelegramBadRequest:
        pass


async def _edit_or_send_processing(message: Message, text: str):
    """Edita el mensaje de procesamiento; si falla, envía uno nuevo."""
    try:
        if message.photo:
            await message.edit_caption(caption=text, parse_mode=ParseMode.HTML)
        else:
            await message.edit_text(text, parse_mode=ParseMode.HTML)
    except TelegramBadRequest:
        pass


def register_telcel_handlers(dp):
    dp.include_router(router)
