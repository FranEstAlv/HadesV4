"""Handler /sms — SMS virtual (números temporales vía HeroSMS).

UI aiogram sobre bot.services.sms:
- /sms → menú (Pedir SMS / Mi saldo / Historial / ¿Cómo funciona?).
- Servicio → país → doble confirmación → cobro → número + polling.
- Cancelación manual en 10s, reembolso automático si no llega el código.

Acceso: membresía activa o admin. Solo super admins exentos de cobro;
admins normales pagan igual que el resto (misma regla que /rfc).
"""
from __future__ import annotations

import logging
import math
from typing import Dict, List

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

from bot.services import sms as svc
from bot.services.admins import is_admin, is_super_admin
from bot.services.credits import deduct_credits, get_balance
from bot.services.logger import log_error
from bot.services.memberships import get_membership, is_membership_active
from bot.services.users import register_user
from bot.utils.formatting import escape_html

router = Router()
logger = logging.getLogger(__name__)

_PAGE_SVC = 10
_PAGE_HIS = 5

_FLAG: Dict[str, str] = {
    "US": "🇺🇸", "MX": "🇲🇽", "GB": "🇬🇧", "RU": "🇷🇺", "FR": "🇫🇷",
    "DE": "🇩🇪", "ES": "🇪🇸", "BR": "🇧🇷", "IN": "🇮🇳", "CA": "🇨🇦",
    "AU": "🇦🇺", "JP": "🇯🇵", "KZ": "🇰🇿", "PK": "🇵🇰", "TH": "🇹🇭",
    "IL": "🇮🇱", "IE": "🇮🇪", "CY": "🇨🇾", "CZ": "🇨🇿", "KG": "🇰🇬",
}


def _flag(short: str) -> str:
    return _FLAG.get((short or "").upper(), "🌐")


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data, style="primary")


_CLOSE = InlineKeyboardButton(text="❌ Cerrar", callback_data="sms:close", style="danger")
_HOME = InlineKeyboardButton(text="🔙 Inicio", callback_data="sms:home", style="primary")


# ---------------------------------------------------------------------------
# Textos
# ---------------------------------------------------------------------------

def _main_text(balance: int) -> str:
    return (
        "<blockquote>\n"
        "📱 <b>SMS VIRTUAL</b>\n"
        "━━━━━━━━━━━━━━\n"
        "Solicita un número temporal para recibir un código.\n\n"
        f"💳 Tu saldo: <b>{balance} créditos</b>"
        "\n</blockquote>"
    )


def _main_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("📱 Pedir SMS", "sms:services:0")],
            [
                _btn("💳 Mi saldo", "sms:balance"),
                _btn("📋 Historial", "sms:history:0"),
            ],
            [_btn("ℹ️ ¿Cómo funciona?", "sms:info")],
            [_CLOSE],
        ]
    )


def _services_text(page: int, total: int) -> str:
    pages = max(1, math.ceil(total / _PAGE_SVC))
    return (
        "<blockquote>\n"
        "📱 <b>Elige el servicio</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"Página {page + 1} de {pages}. Toca el que necesites:"
        "\n</blockquote>"
    )


def _services_markup(services: List[Dict], page: int) -> InlineKeyboardMarkup:
    start = page * _PAGE_SVC
    chunk = services[start: start + _PAGE_SVC]
    rows = []
    for i in range(0, len(chunk), 2):
        pair = chunk[i: i + 2]
        rows.append([_btn(s.get("name", "?"), f"sms:svc:{s['ID']}") for s in pair])
    pages = math.ceil(len(services) / _PAGE_SVC)
    nav = []
    if page > 0:
        nav.append(_btn("◀️", f"sms:services:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="sms:noop"))
    if start + _PAGE_SVC < len(services):
        nav.append(_btn("▶️", f"sms:services:{page + 1}"))
    rows.append(nav)
    rows.append([_HOME, _CLOSE])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _countries_text(service_name: str) -> str:
    return (
        "<blockquote>\n"
        f"🌍 <b>{escape_html(service_name)}</b> — elige el país\n"
        "━━━━━━━━━━━━━━"
        "\n</blockquote>"
    )


def _countries_markup(
    service_id: str, countries: List[Dict], credits_map: Dict[int, int]
) -> InlineKeyboardMarkup:
    rows = []
    for c in countries[:20]:
        credits = credits_map.get(c["country_id"], 0)
        if credits == 0:
            continue
        flag = _flag(c.get("short_name", ""))
        name = c.get("name", "?")
        success = c.get("success_rate", "?")
        success_str = f"{success}%" if success != "?" else "?"
        label = f"{flag} {name} · {success_str} · {credits} cr"
        rows.append([_btn(label, f"sms:cty:{service_id}:{c['country_id']}")])
    if not rows:
        rows.append([_btn("⛔ Sin países disponibles", "sms:noop")])
    rows.append([_btn("🔙 Servicios", "sms:services:0"), _CLOSE])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _confirm1_text(service_name: str, country_name: str, credits: int, balance: int) -> str:
    after = balance - credits
    ok = "✅" if after >= 0 else "❌"
    return (
        "<blockquote>\n"
        "📋 <b>Confirmar pedido</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"📱 Servicio: <b>{escape_html(service_name)}</b>\n"
        f"🌍 País: <b>{escape_html(country_name)}</b>\n"
        f"💳 Costo: <b>{credits} créditos</b>\n\n"
        f"Tu saldo: {balance} → {ok} {after} créditos"
        "\n</blockquote>"
    )


def _confirm1_markup(service_id: str, country_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn("✅ Confirmar", f"sms:confirm2:{service_id}:{country_id}"),
                _btn("❌ Cancelar", "sms:services:0"),
            ],
            [_HOME],
        ]
    )


def _confirm2_text(service_name: str, country_name: str, credits: int) -> str:
    return (
        "<blockquote>\n"
        "⚠️ <b>¿Estás seguro?</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"Vas a pedir un número de <b>{escape_html(service_name)}</b> en "
        f"<b>{escape_html(country_name)}</b>.\n"
        f"Se descontarán <b>{credits} créditos</b>.\n\n"
        "<i>Si el código no llega, te los devolvemos automáticamente.</i>"
        "\n</blockquote>"
    )


def _confirm2_markup(service_id: str, country_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn("✅ Sí, pedir", f"sms:buy:{service_id}:{country_id}"),
                _btn("↩️ Volver", f"sms:cty:{service_id}:{country_id}"),
            ],
        ]
    )


def active_text(phone: str, service: str, country: str, time_left: int, cancel_ok: bool) -> str:
    mins, secs = divmod(time_left, 60)
    cancel_note = (
        "\n\n⚡ <i>Tienes 10 seg para cancelar si te equivocaste.</i>"
        if cancel_ok
        else ""
    )
    return (
        "<blockquote>\n"
        "📲 <b>Número asignado</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"📞 <code>{escape_html(phone)}</code>\n"
        f"🌐 {escape_html(country)} — {escape_html(service)}\n"
        f"⏱️ Tiempo restante: <b>{mins}:{secs:02d}</b>\n\n"
        "Usa este número donde lo necesites.\n"
        "Cuando llegue el código te lo mando aquí."
        f"{cancel_note}"
        "\n</blockquote>"
    )


def active_markup(order_id: str, cancel_ok: bool) -> InlineKeyboardMarkup:
    rows = []
    if cancel_ok:
        rows.append([_btn("🚫 Cancelar pedido", f"sms:cancel:{order_id}")])
    else:
        rows.append([_btn("🔄 Verificar ahora", f"sms:refresh:{order_id}")])
    rows.append([_CLOSE])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _history_text(orders: List[Dict], page: int, total: int) -> str:
    if not orders:
        return (
            "<blockquote>\n📋 <b>Historial de pedidos</b>\n━━━━━━━━━━━━━━\n"
            "No tienes pedidos aún.\n</blockquote>"
        )
    icons = {"success": "✅", "failed": "❌", "cancelled": "🚫", "pending": "⏳"}
    lines = ["<blockquote>", "📋 <b>Historial de pedidos</b>", "━━━━━━━━━━━━━━"]
    for o in orders:
        icon = icons.get(o.get("status", ""), "❓")
        svc_name = o.get("service_name", "?")
        cty = o.get("country_name", "?")
        ts = (o.get("requested_at") or "")[:16].replace("T", " ")
        ticket = o.get("order_id", "")
        credits = o.get("credits_charged", 0)
        lines.append(
            f"{icon} <b>{escape_html(str(svc_name))}</b> · {escape_html(str(cty))}\n"
            f"   💳 {credits} cr  🎫 <code>{escape_html(str(ticket))}</code>  📅 {ts}"
        )
    pages = max(1, math.ceil(total / _PAGE_HIS))
    lines.append(f"\n<i>Página {page + 1} de {pages}</i>")
    lines.append("</blockquote>")
    return "\n".join(lines)


def _history_markup(page: int, total: int) -> InlineKeyboardMarkup:
    pages = max(1, math.ceil(total / _PAGE_HIS))
    nav = []
    if page > 0:
        nav.append(_btn("◀️ Ant", f"sms:history:{page - 1}"))
    if page + 1 < pages:
        nav.append(_btn("▶️ Sig", f"sms:history:{page + 1}"))
    rows = []
    if nav:
        rows.append(nav)
    rows.append([_HOME, _CLOSE])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _info_text() -> str:
    return (
        "<blockquote>\n"
        "ℹ️ <b>¿Cómo funciona?</b>\n"
        "━━━━━━━━━━━━━━\n"
        "1️⃣ Eliges el servicio (WhatsApp, Telegram, etc.)\n"
        "2️⃣ Eliges el país del número\n"
        "3️⃣ El bot te cobra créditos y te da un número temporal\n"
        "4️⃣ Usas ese número donde te pide el código\n"
        "5️⃣ En cuanto llegue el código, te lo mando aquí\n\n"
        "⏰ El número dura un tiempo limitado.\n"
        "💳 Si el código no llega, tus créditos se devuelven solos.\n"
        "🔒 Todo es privado — nadie más lo ve."
        "\n</blockquote>"
    )


def _balance_text(balance: int) -> str:
    return (
        "<blockquote>\n"
        "💳 <b>Tu saldo de créditos</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"Balance actual: <b>{balance} créditos</b>"
        "\n</blockquote>"
    )


def _denied_text() -> str:
    return (
        "<blockquote>\n"
        "❌ <b>Acceso denegado</b>\n\n"
        "El comando /<b>sms</b> requiere una membresía activa.\n\n"
        "Contacta a un administrador para adquirir acceso."
        "\n</blockquote>"
    )


# ---------------------------------------------------------------------------
# Acceso
# ---------------------------------------------------------------------------

async def _can_use_sms(user_id: int) -> bool:
    if await is_admin(user_id):
        return True
    membership = await get_membership(user_id)
    return is_membership_active(membership)


def _is_exempt(user_id: int) -> bool:
    """Solo super admins exentos de cobro (misma regla que /rfc)."""
    return is_super_admin(user_id)


async def _edit(callback: CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return
        raise


# ---------------------------------------------------------------------------
# Comando /sms
# ---------------------------------------------------------------------------

@router.message(Command("sms"))
async def cmd_sms(message: Message):
    user = message.from_user
    await register_user(user.id, user.username, user.full_name)
    if not await svc.is_enabled():
        await message.answer(
            "<blockquote>\n🔴 El servicio de SMS está temporalmente no disponible.\n</blockquote>",
            parse_mode=ParseMode.HTML,
        )
        return
    if not await _can_use_sms(user.id):
        await message.answer(_denied_text(), parse_mode=ParseMode.HTML)
        return
    balance = await get_balance(user.id)
    await message.answer(
        _main_text(balance), reply_markup=_main_markup(), parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data == "sms:close")
async def cb_sms_close(callback: CallbackQuery):
    try:
        await callback.message.delete()
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data == "sms:noop")
async def cb_sms_noop(callback: CallbackQuery):
    await callback.answer()


@router.callback_query(F.data == "sms:home")
async def cb_sms_home(callback: CallbackQuery):
    if not await _can_use_sms(callback.from_user.id):
        await callback.answer("❌ Membresía requerida.", show_alert=True)
        return
    balance = await get_balance(callback.from_user.id)
    await _edit(callback, _main_text(balance), _main_markup())
    await callback.answer()


@router.callback_query(F.data == "sms:balance")
async def cb_sms_balance(callback: CallbackQuery):
    if not await _can_use_sms(callback.from_user.id):
        await callback.answer("❌ Membresía requerida.", show_alert=True)
        return
    balance = await get_balance(callback.from_user.id)
    await _edit(
        callback,
        _balance_text(balance),
        InlineKeyboardMarkup(inline_keyboard=[[ _HOME, _CLOSE ]]),
    )
    await callback.answer()


@router.callback_query(F.data == "sms:history:0")
async def cb_sms_history_root(callback: CallbackQuery):
    await cb_sms_history(callback, 0)


@router.callback_query(F.data.startswith("sms:history:"))
async def cb_sms_history(callback: CallbackQuery, page: int = 0):
    if not await _can_use_sms(callback.from_user.id):
        await callback.answer("❌ Membresía requerida.", show_alert=True)
        return
    try:
        page = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        page = 0
    orders, total = await svc.get_user_history(
        callback.from_user.id, limit=_PAGE_HIS, offset=page * _PAGE_HIS
    )
    await _edit(callback, _history_text(orders, page, total), _history_markup(page, total))
    await callback.answer()


@router.callback_query(F.data == "sms:info")
async def cb_sms_info(callback: CallbackQuery):
    if not await _can_use_sms(callback.from_user.id):
        await callback.answer("❌ Membresía requerida.", show_alert=True)
        return
    await _edit(
        callback,
        _info_text(),
        InlineKeyboardMarkup(inline_keyboard=[[ _HOME, _CLOSE ]]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("sms:services:"))
async def cb_sms_services(callback: CallbackQuery):
    if not await _can_use_sms(callback.from_user.id):
        await callback.answer("❌ Membresía requerida.", show_alert=True)
        return
    try:
        page = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        page = 0
    # Respuesta inmediata: la carga tarda (fetch a HeroSMS) y si no se
    # responde el callback, el botón se queda girando ("congelado").
    await callback.answer("⏳ Cargando servicios...")
    services = await svc.fetch_services()
    if not services:
        await _edit(
            callback,
            "<blockquote>\n⚠️ <b>No se pudo cargar servicios.</b>\n\n"
            "Intenta más tarde.</blockquote>",
            InlineKeyboardMarkup(inline_keyboard=[[ _HOME, _CLOSE ]]),
        )
        return
    await _edit(callback, _services_text(page, len(services)), _services_markup(services, page))


@router.callback_query(F.data.startswith("sms:svc:"))
async def cb_sms_service(callback: CallbackQuery):
    if not await _can_use_sms(callback.from_user.id):
        await callback.answer("❌ Membresía requerida.", show_alert=True)
        return
    service_id = callback.data.split(":", 2)[2] if ":" in callback.data else ""
    # Respuesta inmediata: fetch_countries tarda 30–90s (un request por
    # país). Se muestra "buscando" y se edita al terminar.
    await callback.answer("⏳ Buscando países con stock...")
    try:
        await callback.message.edit_text(
            "<blockquote>\n⏳ <b>Buscando países con stock...</b>\n\n"
            "Esto puede tardar hasta un minuto.</blockquote>",
            parse_mode=ParseMode.HTML,
        )
    except TelegramBadRequest:
        pass
    services = await svc.fetch_services()
    svc_name = next((s["name"] for s in services if s["ID"] == service_id), str(service_id))
    countries = await svc.fetch_countries(service_id)
    if not countries:
        await _edit(
            callback,
            "<blockquote>\n⚠️ <b>Sin disponibilidad para este servicio.</b>\n\n"
            "Prueba con otro servicio.</blockquote>",
            _services_markup(services, 0),
        )
        return
    credits_map: Dict[int, int] = {}
    for c in countries:
        credits_map[c["country_id"]] = await svc.calc_credits(str(c.get("price", "0")))
    if not any(credits_map.values()):
        await callback.answer("⛔ Sin precio configurado para este servicio.", show_alert=True)
        return
    svc.flow_set(
        callback.from_user.id,
        service_id=service_id,
        service_name=svc_name,
        countries_map={c["country_id"]: c for c in countries},
    )
    await _edit(
        callback,
        _countries_text(svc_name),
        _countries_markup(service_id, countries, credits_map),
    )


@router.callback_query(F.data.startswith("sms:cty:"))
async def cb_sms_country(callback: CallbackQuery):
    if not await _can_use_sms(callback.from_user.id):
        await callback.answer("❌ Membresía requerida.", show_alert=True)
        return
    parts = callback.data.split(":")
    service_id = parts[2] if len(parts) >= 4 else ""
    try:
        country_id = int(parts[3]) if len(parts) >= 4 else 0
    except ValueError:
        country_id = 0
    user_id = callback.from_user.id
    flow = svc.flow_get(user_id)
    country = flow.get("countries_map", {}).get(country_id)
    svc_name = flow.get("service_name", str(service_id))
    if not country:
        countries = await svc.fetch_countries(service_id)
        country = next((c for c in countries if c["country_id"] == country_id), None)
        if not country:
            await callback.answer("⚠️ País no disponible.", show_alert=True)
            return
    country_name = country.get("name", str(country_id))
    usd = str(country.get("price", "0"))
    credits = await svc.calc_credits(usd)
    if credits == 0:
        await callback.answer("⛔ Sin precio configurado para este país.", show_alert=True)
        return
    svc.flow_set(
        user_id, country_id=country_id, country_name=country_name, usd=usd, credits=credits
    )
    balance = await get_balance(user_id)
    await _edit(
        callback,
        _confirm1_text(svc_name, country_name, credits, balance),
        _confirm1_markup(service_id, country_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("sms:confirm2:"))
async def cb_sms_confirm2(callback: CallbackQuery):
    if not await _can_use_sms(callback.from_user.id):
        await callback.answer("❌ Membresía requerida.", show_alert=True)
        return
    parts = callback.data.split(":")
    service_id = parts[2] if len(parts) >= 4 else ""
    try:
        country_id = int(parts[3]) if len(parts) >= 4 else 0
    except ValueError:
        country_id = 0
    flow = svc.flow_get(callback.from_user.id)
    svc_name = flow.get("service_name", str(service_id))
    country_name = flow.get("country_name", str(country_id))
    credits = int(flow.get("credits", 0))
    if not credits:
        await callback.answer("⚠️ Datos perdidos, empieza de nuevo.", show_alert=True)
        return
    await _edit(
        callback,
        _confirm2_text(svc_name, country_name, credits),
        _confirm2_markup(service_id, country_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("sms:buy:"))
async def cb_sms_buy(callback: CallbackQuery, bot=None):
    user_id = callback.from_user.id
    if not await _can_use_sms(user_id):
        await callback.answer("❌ Membresía requerida.", show_alert=True)
        return
    parts = callback.data.split(":")
    service_id = parts[2] if len(parts) >= 4 else ""
    try:
        country_id = int(parts[3]) if len(parts) >= 4 else 0
    except ValueError:
        country_id = 0
    flow = svc.flow_get(user_id)
    svc_name = flow.get("service_name", str(service_id))
    country_name = flow.get("country_name", str(country_id))
    credits = int(flow.get("credits", 0))
    usd = str(flow.get("usd", "0"))
    username = callback.from_user.username or str(user_id)
    if not credits:
        await callback.answer("⚠️ Datos perdidos, empieza de nuevo.", show_alert=True)
        return
    bot = bot or callback.bot

    # Super admins exentos de cobro (misma regla que /rfc).
    if not _is_exempt(user_id):
        balance = await get_balance(user_id)
        if balance < credits:
            await callback.answer(
                f"❌ Saldo insuficiente. Tienes {balance}, necesitas {credits} créditos.",
                show_alert=True,
            )
            return
        # Stock antes de cobrar
        try:
            if await svc.api_stock(service_id, country_id) == 0:
                await callback.answer(
                    "⛔ Sin números disponibles para este país/servicio. Elige otro.",
                    show_alert=True,
                )
                return
        except Exception:
            pass  # Si falla el check, se intenta la compra igual
        success, _ = await deduct_credits(
            user_id, credits, f"SMS virtual {svc_name} {country_name}"
        )
        if not success:
            await callback.answer("❌ No se pudo cobrar. Intenta de nuevo.", show_alert=True)
            return
    else:
        credits = 0

    try:
        purchase = await svc.api_buy(service_id, country_id, usd)
    except Exception as exc:
        logger.warning("sms: error de compra: %s", exc)
        if credits:
            await svc.refund_credits(0, user_id, credits, f"Error API compra SMS ({exc})")
        await callback.answer(
            "❌ Error al conectar con el proveedor. Créditos devueltos.", show_alert=True
        )
        return

    order_id = str(purchase.get("order_id", ""))
    phone = str(purchase.get("number", ""))
    time_left = int(purchase.get("expires_in", 1200))
    if not order_id or not phone:
        if credits:
            await svc.refund_credits(0, user_id, credits, "Sin order_id en respuesta SMS")
        await callback.answer("❌ Respuesta inválida. Créditos devueltos.", show_alert=True)
        return

    await svc.ensure_user_order(user_id, username, order_id, service_id, svc_name,
                                country_id, country_name, phone, credits, usd)
    info = {
        "chat_id": callback.message.chat.id,
        "message_id": callback.message.message_id,
        "user_id": user_id,
        "username": username,
        "phone": phone,
        "service_name": svc_name,
        "country_name": country_name,
        "credits": credits,
        "cancel_deadline": _now() + svc.cancel_window(),
        "started_at": _now(),
        "expires_at": _now() + time_left,
    }
    svc.start_polling(bot, order_id, info)
    svc.flow_pop(user_id)

    # Auto-quitar botón de cancelar a los 10s
    async def _remove_cancel() -> None:
        import asyncio as _aio

        await _aio.sleep(svc.cancel_window())
        nfo = svc.active_get(order_id)
        if not nfo:
            return
        try:
            await bot.edit_message_reply_markup(
                chat_id=nfo["chat_id"],
                message_id=nfo["message_id"],
                reply_markup=active_markup(order_id, False),
            )
        except Exception:
            pass

    import asyncio as _asyncio

    _asyncio.create_task(_remove_cancel())

    try:
        await callback.message.edit_text(
            active_text(phone, svc_name, country_name, time_left, True),
            reply_markup=active_markup(order_id, True),
            parse_mode=ParseMode.HTML,
        )
    except TelegramBadRequest:
        pass
    await callback.answer("✅ Número asignado.")


def _now() -> float:
    import time as _t

    return _t.time()


@router.callback_query(F.data.startswith("sms:cancel:"))
async def cb_sms_cancel_order(callback: CallbackQuery):
    order_id = callback.data.split(":", 2)[2] if ":" in callback.data else ""
    info = svc.active_get(order_id)
    if not info:
        await callback.answer("⚠️ Pedido ya no cancelable.", show_alert=True)
        return
    if info.get("user_id") != callback.from_user.id:
        await callback.answer("❌ No es tu pedido.", show_alert=True)
        return
    if _now() > info.get("cancel_deadline", 0):
        await callback.answer("⏱️ El tiempo para cancelar ya pasó.", show_alert=True)
        return
    try:
        await svc.api_cancel(order_id)
    except Exception as exc:
        logger.warning("sms: cancel API error %s: %s", order_id, exc)
        await callback.answer(
            "⚠️ El proveedor aún no permite cancelar este pedido.", show_alert=True
        )
        return
    svc.stop_polling(order_id)
    await svc.mark_cancelled(order_id)
    credits = info.get("credits", 0)
    username = info.get("username", str(callback.from_user.id))
    if credits:
        await svc.refund_credits(
            0, callback.from_user.id, credits,
            f"Reembolso cancelación manual ({order_id})",
        )
    await _edit(
        callback,
        (
            "<blockquote>\n🚫 <b>Pedido cancelado</b>\n━━━━━━━━━━━━━━\n"
            f"💳 Tus <b>{credits} créditos</b> fueron devueltos.\n"
            f"🎫 Ticket: <code>{escape_html(order_id)}</code>\n</blockquote>"
        ),
        InlineKeyboardMarkup(
            inline_keyboard=[
                [_btn("🔁 Intentar de nuevo", "sms:services:0")],
                [_HOME, _CLOSE],
            ]
        ),
    )
    await callback.answer("🚫 Pedido cancelado.")


@router.callback_query(F.data.startswith("sms:refresh:"))
async def cb_sms_refresh(callback: CallbackQuery):
    order_id = callback.data.split(":", 2)[2] if ":" in callback.data else ""
    info = svc.active_get(order_id)
    if not info:
        await callback.answer("ℹ️ Pedido ya no activo.", show_alert=True)
        return
    if info.get("user_id") != callback.from_user.id:
        await callback.answer("❌ No es tu pedido.", show_alert=True)
        return
    try:
        result = await svc.api_check(order_id)
        code_val = str(result.get("sms") or "")
        if svc.code_valid(code_val) or result.get("status") == 3:
            if svc.code_valid(code_val):
                await svc.deliver_code(callback.bot, order_id, code_val)
            else:
                await callback.answer(
                    "⏳ Código aún en camino, intenta en unos segundos.", show_alert=True
                )
        else:
            await callback.answer("⏳ Sin código aún. Espera un momento.", show_alert=True)
            try:
                await callback.message.edit_text(
                    active_text(
                        info["phone"], info["service_name"], info["country_name"],
                        int(result.get("time_left") or 0), False,
                    ),
                    reply_markup=active_markup(order_id, False),
                    parse_mode=ParseMode.HTML,
                )
            except TelegramBadRequest:
                pass
    except Exception as exc:
        await callback.answer(f"⚠️ Error al verificar: {str(exc)[:80]}", show_alert=True)


def register_sms_handlers(dp) -> None:
    dp.include_router(router)
