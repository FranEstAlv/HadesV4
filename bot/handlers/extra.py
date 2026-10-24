"""Handler /extra — Extrapolador de BINs (port de Hades V1).

Todo el flujo vive en un solo mensaje que se va editando en cada paso,
igual que los demás paneles. Cada extra del resultado va en monospace
(<code>) para copiarlo con un toque.
"""
from __future__ import annotations

import logging
import re

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.exceptions import TelegramBadRequest

from bot.services import extrapolator as xt
from bot.services.admins import is_admin
from bot.services.memberships import get_membership, is_membership_active
from bot.utils.formatting import escape_html

router = Router()
logger = logging.getLogger(__name__)

_EXTRA_CUSTOM_EMOJI_ID = "5454074580010295588"
_SEP = "━━━━━━━━━━━━━━━━━━"
_MAX_MSG = 3800


class ExtraState(StatesGroup):
    waiting_bin = State()
    waiting_cantidad = State()
    waiting_lives = State()


def _ex(fallback: str) -> str:
    return f'<tg-emoji emoji-id="{_EXTRA_CUSTOM_EMOJI_ID}">{fallback}</tg-emoji>'


async def _can_use_extra(user_id: int) -> bool:
    if await is_admin(user_id):
        return True
    membership = await get_membership(user_id)
    return is_membership_active(membership)


def _denied_text() -> str:
    return (
        "❌ <b>Acceso denegado</b>\n\n"
        "El comando /<b>extra</b> requiere una membresía activa.\n\n"
        "Contacta a un administrador para adquirir acceso."
    )


def _menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔢 Extrapolar BIN", callback_data="extra_mode:bin", style="primary")],
            [InlineKeyboardButton(text="🎯 Extrapolar desde lives", callback_data="extra_mode:lives", style="primary")],
            [InlineKeyboardButton(text="❌ Cancelar", callback_data="extra_cancel", style="danger")],
        ]
    )


def _mode_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔍 Cerca primero (recomendado)", callback_data="extra_modo:inteligente", style="success")],
            [InlineKeyboardButton(text="🌐 Rango amplio", callback_data="extra_modo:variado", style="primary")],
            [InlineKeyboardButton(text="❌ Cancelar", callback_data="extra_cancel", style="danger")],
        ]
    )


def _binlen_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="8️⃣ 8 dígitos (recomendado)", callback_data="extra_binlen:8", style="success")],
            [InlineKeyboardButton(text="6️⃣ 6 dígitos", callback_data="extra_binlen:6", style="primary")],
            [InlineKeyboardButton(text="❌ Cancelar", callback_data="extra_cancel", style="danger")],
        ]
    )


def _cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Cancelar", callback_data="extra_cancel", style="danger")],
        ]
    )


def _done_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Nuevo", callback_data="extra_restart", style="success")],
            [InlineKeyboardButton(text="❌ Cerrar", callback_data="extra_close", style="danger")],
        ]
    )


def _menu_text() -> str:
    return (
        f"{_ex('💳')} <b>HadesExtra</b>\n"
        f"{_SEP}\n"
        "Luhn Engine │ BIN │ Extrapolador\n\n"
        "<b>Flujo recomendado:</b>\n"
        "❶ Tienes lives → extrapola desde lives\n"
        "❷ Tienes BIN → extrapola el BIN\n\n"
        "Selecciona una opción:"
    )


async def _edit_panel(message: Message, text: str, reply_markup=None) -> None:
    try:
        await message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return
        raise


async def _show_text(message: Message, text: str, reply_markup=None) -> None:
    """Edita el panel; si el texto excede el límite, reparte en varios mensajes."""
    if len(text) <= 4096:
        try:
            await _edit_panel(message, text, reply_markup)
            return
        except TelegramBadRequest as exc:
            if "too long" not in str(exc).lower():
                raise
    lines = text.split("\n")
    chunks: list[str] = []
    current = ""
    for line in lines:
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > _MAX_MSG:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    await _edit_panel(message, chunks[0], None)
    for chunk in chunks[1:]:
        await message.answer(chunk, parse_mode=ParseMode.HTML)
    if reply_markup is not None:
        await message.answer("Selecciona:", reply_markup=reply_markup)


def _short_template(t: str) -> str:
    """Quita solo el CVV placeholder del final.

    `554265300012|11|2027|xxx` → `554265300012|11|2027`
    `457231xxxxxxx|xx|20xx|xxx` → `457231xxxxxxx|xx|20xx`
    Un CVV real escrito por el usuario se conserva.
    """
    parts = t.split("|")
    if len(parts) == 1:
        return t
    if re.fullmatch(r"[xX]{3,4}", parts[-1].strip()):
        parts = parts[:-1]
    return "|".join(parts) if len(parts) > 1 else parts[0]


def _result_text(template_base: str, modo: str, bin_len: int, templates: list) -> str:
    modo_display = "cerca primero" if modo == "inteligente" else "rango amplio"
    lines = [
        f"{_ex('💳')} <b>HadesExtra</b>",
        _SEP,
        f"BIN base: <code>{escape_html(_short_template(template_base))}</code>",
        f"Modo: {modo_display} │ Fijos: {bin_len} dígitos",
        f"Extrapolados: <b>{len(templates)}</b>",
        _SEP,
    ]
    lines.extend(f"<code>{escape_html(_short_template(t))}</code>" for t in templates)
    lines.append(_SEP)
    return "\n".join(lines)


@router.message(Command("extra"))
async def cmd_extra(message: Message, state: FSMContext):
    if not await _can_use_extra(message.from_user.id):
        await message.answer(_denied_text(), parse_mode=ParseMode.HTML)
        return
    await state.clear()
    panel = await message.answer(_menu_text(), parse_mode=ParseMode.HTML, reply_markup=_menu_kb())
    await state.update_data(panel_message_id=panel.message_id, panel_chat_id=panel.chat.id)


@router.callback_query(F.data == "extra_cancel")
async def cb_extra_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await callback.message.edit_text(
            "❌ <b>Proceso cancelado.</b>\n\nUsa /extra cuando quieras empezar de nuevo.",
            parse_mode=ParseMode.HTML,
        )
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data == "extra_close")
async def cb_extra_close(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await callback.message.delete()
    except TelegramBadRequest:
        try:
            await callback.message.edit_text(
                "👋 <b>Panel cerrado.</b>\n\nUsa /extra para volver a empezar.",
                parse_mode=ParseMode.HTML,
            )
        except TelegramBadRequest:
            pass
    await callback.answer()


@router.callback_query(F.data == "extra_restart")
async def cb_extra_restart(callback: CallbackQuery, state: FSMContext):
    if not await _can_use_extra(callback.from_user.id):
        await callback.answer("No tienes acceso a /extra.", show_alert=True)
        return
    await state.clear()
    await state.update_data(
        panel_message_id=callback.message.message_id,
        panel_chat_id=callback.message.chat.id,
    )
    await _edit_panel(callback.message, _menu_text(), _menu_kb())
    await callback.answer()


@router.callback_query(F.data.startswith("extra_mode:"))
async def cb_extra_mode(callback: CallbackQuery, state: FSMContext):
    if not await _can_use_extra(callback.from_user.id):
        await callback.answer("No tienes acceso a /extra.", show_alert=True)
        return
    mode = callback.data.split(":", 1)[1]
    await state.update_data(
        panel_message_id=callback.message.message_id,
        panel_chat_id=callback.message.chat.id,
    )
    if mode == "bin":
        await state.set_state(ExtraState.waiting_bin)
        await _edit_panel(
            callback.message,
            f"{_ex('🔢')} <b>Extrapolar BIN</b>\n"
            f"{_SEP}\n"
            "Escribe tu BIN (6 a 10 dígitos) o un template completo:\n"
            "<code>NUMERO|MM|AAAA|CVV</code>\n\n"
            "Ejemplo:\n<code>457249651136xxxx|06|2029|xxx</code>",
            _cancel_kb(),
        )
    else:
        await state.set_state(ExtraState.waiting_lives)
        await state.update_data(entradas=[])
        await _edit_panel(
            callback.message,
            f"{_ex('🎯')} <b>Extrapolar desde lives</b>\n"
            f"{_SEP}\n"
            "Pega tus tarjetas activas (una por línea) y el sistema detecta "
            "el BIN en común para extrapolarlo.\n\n"
            "Mínimo 2. Cuando termines, escribe <b>FIN</b>.\n\n"
            "Recibidos: <b>0</b>",
            _cancel_kb(),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("extra_modo:"))
async def cb_extra_modo(callback: CallbackQuery, state: FSMContext):
    if not await _can_use_extra(callback.from_user.id):
        await callback.answer("No tienes acceso a /extra.", show_alert=True)
        return
    modo = callback.data.split(":", 1)[1]
    await state.update_data(modo=modo)
    await _edit_panel(
        callback.message,
        f"{_ex('🔢')} <b>Dígitos fijos</b>\n"
        f"{_SEP}\n"
        "¿Cuántos dígitos congelas como BIN fijo?\n\n"
        "<b>8 dígitos</b> — lo mejor para tarjetas actuales\n"
        "<b>6 dígitos</b> — solo si con 8 no obtienes variedad",
        _binlen_kb(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("extra_binlen:"))
async def cb_extra_binlen(callback: CallbackQuery, state: FSMContext):
    if not await _can_use_extra(callback.from_user.id):
        await callback.answer("No tienes acceso a /extra.", show_alert=True)
        return
    try:
        bin_len = int(callback.data.split(":", 1)[1])
    except ValueError:
        bin_len = 8
    await state.update_data(bin_len=bin_len)
    await state.set_state(ExtraState.waiting_cantidad)
    data = await state.get_data()
    template_base = data.get("template_base", "")
    modo = data.get("modo", "inteligente")
    modo_display = "cerca primero" if modo == "inteligente" else "rango amplio"
    await _edit_panel(
        callback.message,
        f"{_ex('🔢')} <b>Cantidad</b>\n"
        f"{_SEP}\n"
        f"BIN base: <code>{escape_html(_short_template(template_base))}</code>\n"
        f"Modo: {modo_display} │ Fijos: {bin_len}\n\n"
        "¿Cuántos extras generas? (1 a 99, default 10)",
        _cancel_kb(),
    )
    await callback.answer()


@router.message(ExtraState.waiting_bin)
async def process_extra_bin(message: Message, state: FSMContext):
    if not await _can_use_extra(message.from_user.id):
        return
    data = await state.get_data()
    panel_message_id = data.get("panel_message_id")
    chat_id = data.get("panel_chat_id")
    texto = (message.text or "").strip()
    if not xt.validar_bin_entrada(texto):
        await message.reply(
            "❌ Solo dígitos (6 a 10) o formato <code>NUMERO|MM|AAAA|CVV</code>. Intenta de nuevo.",
            parse_mode=ParseMode.HTML,
        )
        return
    try:
        template_base = xt.normalizar_template_entrada(texto)
    except ValueError as exc:
        await message.reply(f"❌ Error: {escape_html(str(exc))}", parse_mode=ParseMode.HTML)
        return
    await state.update_data(template_base=template_base)
    await state.set_state(None)
    try:
        panel = await message.bot.edit_message_text(
            text=f"{_ex('🔢')} <b>¿Cómo exploras?</b>\n"
            f"{_SEP}\n"
            f"BIN base: <code>{escape_html(template_base)}</code>\n\n"
            "<b>Cerca primero</b> — números vecinos, alta probabilidad\n"
            "<b>Rango amplio</b> — cuando cerca ya no da nada nuevo",
            chat_id=chat_id,
            message_id=panel_message_id,
            parse_mode=ParseMode.HTML,
            reply_markup=_mode_kb(),
        )
    except TelegramBadRequest:
        panel = await message.answer(
            f"{_ex('🔢')} <b>¿Cómo exploras?</b>\n"
            f"{_SEP}\n"
            f"BIN base: <code>{escape_html(template_base)}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=_mode_kb(),
        )
        await state.update_data(panel_message_id=panel.message_id, panel_chat_id=panel.chat.id)


@router.message(ExtraState.waiting_cantidad)
async def process_extra_cantidad(message: Message, state: FSMContext):
    if not await _can_use_extra(message.from_user.id):
        return
    texto = (message.text or "").strip()
    try:
        cantidad = int(texto) if texto else 10
        if not 1 <= cantidad <= 99:
            raise ValueError
    except ValueError:
        cantidad = 10
    data = await state.get_data()
    template_base = data.get("template_base", "")
    modo = data.get("modo", "inteligente")
    bin_len = int(data.get("bin_len", 8))
    panel_message_id = data.get("panel_message_id")
    chat_id = data.get("panel_chat_id")
    try:
        templates = xt.extrapolar_templates(template_base, cantidad, modo, bin_len)
    except ValueError as exc:
        await message.reply(f"❌ Error: {escape_html(str(exc))}", parse_mode=ParseMode.HTML)
        return
    text = _result_text(template_base, modo, bin_len, templates)
    await state.clear()
    try:
        panel = await message.bot.edit_message_text(
            text=text,
            chat_id=chat_id,
            message_id=panel_message_id,
            parse_mode=ParseMode.HTML,
            reply_markup=_done_kb(),
        )
    except TelegramBadRequest:
        await _show_text(message, text, _done_kb())


@router.message(ExtraState.waiting_lives)
async def process_extra_lives(message: Message, state: FSMContext):
    if not await _can_use_extra(message.from_user.id):
        return
    data = await state.get_data()
    entradas: list = data.get("entradas", [])
    panel_message_id = data.get("panel_message_id")
    chat_id = data.get("panel_chat_id")
    texto = (message.text or "").strip()

    async def _update_panel(text: str, reply_markup=None):
        try:
            await message.bot.edit_message_text(
                text=text,
                chat_id=chat_id,
                message_id=panel_message_id,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
        except TelegramBadRequest:
            pass

    if texto.upper() == "FIN":
        if len(entradas) < 2:
            await _update_panel(
                f"{_ex('🎯')} <b>Extrapolar desde lives</b>\n"
                f"{_SEP}\n"
                "Necesitas al menos 2 tarjetas antes de continuar.\n\n"
                f"Recibidos: <b>{len(entradas)}</b>\n\n"
                "Sigue pegando lives o escribe <b>FIN</b> al terminar.",
                _cancel_kb(),
            )
            return
        n = len(entradas)
        mes_auto, anio_auto = xt.detectar_fecha_comun(entradas)
        red_comun, cvv_auto = xt.red_comun_y_cvv(entradas)
        umbral = xt.umbral_por_lives(n)
        try:
            template, _reporte, bin_confiable = xt.analizar_patron(
                entradas, umbral, mes_auto, anio_auto, cvv_auto
            )
        except ValueError as exc:
            await message.reply(f"❌ Error: {escape_html(str(exc))}", parse_mode=ParseMode.HTML)
            return
        await state.update_data(template_base=template)
        await state.set_state(None)
        umbral_display = 100 if n <= 2 else int(umbral * 100)
        await _update_panel(
            f"{_ex('🎯')} <b>BIN detectado</b>\n"
            f"{_SEP}\n"
            f"Analizados: <b>{n}</b> (umbral {umbral_display}%)\n"
            f"Red: <b>{escape_html(red_comun)}</b>\n"
            f"BIN: <code>{escape_html(bin_confiable)}</code>\n"
            f"Completo: <code>{escape_html(_short_template(template))}</code>\n\n"
            "Selecciona cómo explorar:",
            _mode_kb(),
        )
        return

    nuevas = [ln.strip() for ln in texto.split("\n") if ln.strip()]
    entradas.extend(nuevas)
    await state.update_data(entradas=entradas)
    await _update_panel(
        f"{_ex('🎯')} <b>Extrapolar desde lives</b>\n"
        f"{_SEP}\n"
        "Pega tus tarjetas activas (una por línea).\n\n"
        f"Recibidos: <b>{len(entradas)}</b>\n\n"
        "Escribe <b>FIN</b> cuando termines.",
        _cancel_kb(),
    )


def register_extra_handlers(dp):
    dp.include_router(router)
