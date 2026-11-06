"""Comando /rfc: entrega un registro de RFC/CURP de personal federalizado.

Fuente: evidencias/personal_federalizado_corregido.csv (vía bot/utils/rfc_lookup.py).

Flujo en dos pasos (no-admins):
1. /rfc → muestra costo, saldo actual y botones Confirmar/Cancelar.
2. Confirmar → verifica cooldown, descuenta y entrega el registro.
   Cancelar → descarta la solicitud sin cobrar.

Reglas:
- Cobra RFC_CREDIT_COST créditos (bot.config) a todo usuario que no sea
  super admin, sin importar su membresía o plan (incluidos admins
  normales y miembros Olimpo).
- Solo los super admins están exentos de cobro, confirmación y cooldown.
- Cooldown RFC_COOLDOWN_S segundos por usuario (se cuenta desde la
  entrega, no desde la solicitud).
- Salida en blockquote HTML con CURP y RFC en monospace.
- Errores al usuario en lenguaje humano, sin excepciones ni tecnicismos.
"""
from __future__ import annotations

import logging
import time
from typing import Dict

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

from bot.config import RFC_CREDIT_COST
from bot.services.admins import is_super_admin
from bot.services.credits import deduct_credits, get_balance, refund_credits
from bot.services.users import register_user
from bot.utils.formatting import escape_html, strip_custom_emojis
from bot.utils.rfc_lookup import get_random_record, split_rfc_homoclave, total_registros

router = Router()
logger = logging.getLogger(__name__)

RFC_COOLDOWN_S = 300  # 5 minutos
RFC_PENDING_TTL_S = 120  # la confirmación expira en 2 minutos

_ID_EMOJI_CARD = "5888656404859849688"
_ID_EMOJI_RELOAD = "5454074580010295588"


def _card_emoji(fallback: str) -> str:
    return f'<tg-emoji emoji-id="{_ID_EMOJI_CARD}">{fallback}</tg-emoji>'


def _reload_emoji(fallback: str) -> str:
    return f'<tg-emoji emoji-id="{_ID_EMOJI_RELOAD}">{fallback}</tg-emoji>'

_last_rfc_use: Dict[int, float] = {}
# Solicitudes pendientes de confirmación: user_id -> monotonic de /rfc.
_pending_rfc: Dict[int, float] = {}


def _confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Confirmar", callback_data="rfc_confirm", style="success"
                ),
                InlineKeyboardButton(
                    text="❌ Cancelar", callback_data="rfc_cancel", style="danger"
                ),
            ]
        ]
    )


def _rfc_text(record: Dict[str, str]) -> str:
    """Formatea el registro en el diseño HADES CHK con blockquote."""
    card = _card_emoji("🪪")
    reload = _reload_emoji("🔄")
    nombre = record.get("Nombre", "") or "No disponible"
    paterno = record.get("Apellido_Paterno", "") or "No disponible"
    materno = record.get("Apellido_Materno", "") or "No disponible"
    nombre_completo = " ".join(
        p for p in (nombre.strip(), paterno.strip(), materno.strip()) if p
    ) or "No disponible"
    edad = record.get("Edad_Actual", "") or "No disponible"
    sexo = record.get("Sexo", "") or "No disponible"
    fecha_nac = (record.get("Fecha_Nacimiento", "") or "").replace("/", "-") or "No disponible"
    estado_nac = record.get("Estado_Nacimiento", "") or "No disponible"
    curp = record.get("CURP", "") or "No disponible"
    rfc_full = (record.get("RFC", "") or "").strip() or "No disponible"
    _, homoclave = split_rfc_homoclave(record.get("RFC", ""))
    homoclave = homoclave or "No disponible"

    return (
        "<blockquote>\n"
        f"{reload}<b>HADES CHK</b> {reload}\n\n"
        f"{card} Datos\n\n"
        f"Nombre: <code>{escape_html(nombre)}</code>\n"
        f"Paterno: <code>{escape_html(paterno)}</code>\n"
        f"Materno: <code>{escape_html(materno)}</code>\n"
        f"Nombre completo: <code>{escape_html(nombre_completo)}</code>\n\n"
        f"Edad: {escape_html(str(edad))}\n"
        f"Sexo: {escape_html(str(sexo))}\n"
        f"Fecha de nacimiento: {escape_html(str(fecha_nac))}\n"
        f"Estado de nacimiento: {escape_html(str(estado_nac))}\n"
        f"C.U.R.P:\n<code>{escape_html(str(curp))}</code>\n"
        f"R.F.C:\n<code>{escape_html(str(rfc_full))}</code>\n"
        f"Homoclave: {escape_html(str(homoclave))}\n\n"
        f"{reload}<b>HADES CHK</b> {reload}"
        "\n</blockquote>"
    )


def _user_error(text: str) -> str:
    """Errores al usuario: lenguaje humano, nunca tecnicismos."""
    return f"<blockquote>\n{text}\n</blockquote>"


def _confirm_text(balance: int, cooldown_txt: str = "") -> str:
    """Paso 1: pide confirmación del cobro mostrando costo y saldo."""
    extra = f"\n{cooldown_txt}" if cooldown_txt else ""
    return (
        "<blockquote>\n"
        "🪪 <b>Registro RFC/CURP</b>\n\n"
        f"Este registro cuesta <b>{RFC_CREDIT_COST}</b> créditos.\n"
        f"Tu saldo actual: <b>{balance}</b> créditos.\n"
        f"Saldo después: <b>{balance - RFC_CREDIT_COST}</b> créditos.{extra}\n\n"
        "¿Confirmas el cobro?"
        "\n</blockquote>"
    )


def _deliver_record(message: Message) -> "object":
    """Entrega el registro y marca el cooldown. Devuelve el mensaje enviado."""
    record = get_random_record()
    if not record:
        logger.error(
            "Comando /rfc sin datos: personal_federalizado_corregido.csv vacío o ausente "
            f"(total_registros={total_registros()})"
        )
        return None
    return record


async def _send_record(message: Message, record: Dict[str, str]) -> None:
    try:
        await message.answer(_rfc_text(record), parse_mode=ParseMode.HTML)
    except TelegramBadRequest as exc:
        if "ENTITY_TEXT_INVALID" in str(exc):
            await message.answer(
                strip_custom_emojis(_rfc_text(record)),
                parse_mode=ParseMode.HTML,
            )
        else:
            raise


@router.message(Command("rfc"))
async def cmd_rfc(message: Message):
    """Paso 1: super admins reciben directo; el resto confirma el cobro."""
    user = message.from_user
    await register_user(user.id, user.username, user.full_name)
    is_exempt = is_super_admin(user.id)

    # Super admins: entrega directa, sin cobro ni confirmación.
    if is_exempt:
        record = _deliver_record(message)
        if record is None:
            await message.answer(
                _user_error(
                    "❌ <b>No hay datos disponibles en este momento.</b>\n\n"
                    "Inténtalo más tarde, ya estamos revisándolo."
                ),
                parse_mode=ParseMode.HTML,
            )
            return
        await _send_record(message, record)
        return

    # Cooldown: se revisa antes de pedir confirmación para no ilusionar.
    now = time.monotonic()
    last = _last_rfc_use.get(user.id, 0.0)
    if now - last < RFC_COOLDOWN_S:
        espera = int(RFC_COOLDOWN_S - (now - last))
        mins, segs = divmod(espera, 60)
        espera_txt = f"{mins} min {segs} seg" if mins else f"{segs} seg"
        await message.answer(
            _user_error(
                "⏳ <b>Espera un momento antes de pedir otro registro.</b>\n\n"
                f"Inténtalo de nuevo en {espera_txt}."
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    balance = await get_balance(user.id)
    if balance < RFC_CREDIT_COST:
        await message.answer(
            _user_error(
                "❌ <b>No tienes créditos suficientes.</b>\n\n"
                f"Este comando cuesta {RFC_CREDIT_COST} créditos "
                f"y tu saldo es {balance}.\n"
                "Contacta a un administrador para recargar."
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    _pending_rfc[user.id] = time.monotonic()
    await message.answer(
        _confirm_text(balance),
        parse_mode=ParseMode.HTML,
        reply_markup=_confirm_kb(),
    )


@router.callback_query(F.data == "rfc_confirm")
async def cb_rfc_confirm(callback: CallbackQuery):
    """Paso 2: cobra y entrega. Solo quien pidió con /rfc puede confirmar."""
    user_id = callback.from_user.id
    requested_at = _pending_rfc.pop(user_id, None)
    if requested_at is None:
        await callback.answer(
            "Sin solicitud pendiente. Usa /rfc para empezar.", show_alert=True
        )
        return
    if time.monotonic() - requested_at > RFC_PENDING_TTL_S:
        await callback.answer(
            "La solicitud expiró. Usa /rfc de nuevo.", show_alert=True
        )
        try:
            await callback.message.edit_text(
                _user_error("⌛ <b>La solicitud expiró.</b>\n\nUsa /rfc de nuevo."),
                parse_mode=ParseMode.HTML,
            )
        except TelegramBadRequest:
            pass
        return
    await callback.answer()

    # Solo los super admins confirman sin cobro (entrega directa).
    # Los admins normales también pagan: van al flujo de cobro de abajo.
    if is_super_admin(user_id):
        record = get_random_record()
        if record is None:
            await callback.message.edit_text(
                _user_error(
                    "❌ <b>No hay datos disponibles en este momento.</b>\n\n"
                    "Inténtalo más tarde, ya estamos revisándolo."
                ),
                parse_mode=ParseMode.HTML,
            )
            return
        await _send_record(callback.message, record)
        return

    # Revalidar cooldown al confirmar (pudo pasar otro /rfc en medio).
    now = time.monotonic()
    last = _last_rfc_use.get(user_id, 0.0)
    if now - last < RFC_COOLDOWN_S:
        espera = int(RFC_COOLDOWN_S - (now - last))
        mins, segs = divmod(espera, 60)
        espera_txt = f"{mins} min {segs} seg" if mins else f"{segs} seg"
        await callback.message.edit_text(
            _user_error(
                "⏳ <b>Espera un momento antes de pedir otro registro.</b>\n\n"
                f"Inténtalo de nuevo en {espera_txt}."
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    success, new_balance = await deduct_credits(
        user_id, RFC_CREDIT_COST, "Uso /rfc (registro RFC/CURP)"
    )
    if not success:
        await callback.message.edit_text(
            _user_error(
                "❌ <b>No tienes créditos suficientes.</b>\n\n"
                f"Este comando cuesta {RFC_CREDIT_COST} créditos "
                f"y tu saldo es {new_balance}.\n"
                "Contacta a un administrador para recargar."
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    record = get_random_record()
    if record is None:
        logger.error(
            "Comando /rfc sin datos: personal_federalizado_corregido.csv vacío o ausente "
            f"(total_registros={total_registros()})"
        )
        # Fallo técnico tras el cobro: reembolso automático (Fase 3).
        await refund_credits(user_id, RFC_CREDIT_COST, "Reembolso /rfc sin datos")
        await callback.message.edit_text(
            _user_error(
                "❌ <b>No hay datos disponibles en este momento.</b>\n\n"
                "Inténtalo más tarde, ya estamos revisándolo."
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    _last_rfc_use[user_id] = time.monotonic()
    try:
        await callback.message.delete()
    except TelegramBadRequest:
        pass
    await _send_record(callback.message, record)


@router.callback_query(F.data == "rfc_cancel")
async def cb_rfc_cancel(callback: CallbackQuery):
    """Cancela la solicitud: no se cobra nada."""
    _pending_rfc.pop(callback.from_user.id, None)
    await callback.answer("Solicitud cancelada.")
    try:
        await callback.message.edit_text(
            _user_error("❌ <b>Solicitud cancelada.</b>\n\nNo se cobró nada."),
            parse_mode=ParseMode.HTML,
        )
    except TelegramBadRequest:
        pass


def register_rfc_handlers(dp):
    dp.include_router(router)
