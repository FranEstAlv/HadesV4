"""Handler /correo — correo temporal de 24 horas (EmailApi local).

Sin costo ni membresía: abierto a todo usuario registrado.
Una cuenta activa por usuario; crear otra reemplaza la anterior.
Bandeja de hasta 20 mensajes con leer y eliminar (y adjuntos descargables).
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest

from bot.services import correo as svc
from bot.services.users import register_user
from bot.utils.formatting import email_body_to_text, escape_html

router = Router()
logger = logging.getLogger(__name__)


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data, style="primary")


_CLOSE = InlineKeyboardButton(text="❌ Cerrar", callback_data="correo:close", style="danger")
_HOME = InlineKeyboardButton(text="🔙 Inicio", callback_data="correo:home", style="primary")


def _main_markup(has_account: bool) -> InlineKeyboardMarkup:
    rows = []
    if has_account:
        rows.append([_btn("📥 Revisar bandeja", "correo:inbox")])
        rows.append([_btn("🔄 Nuevo correo (reemplaza)", "correo:new")])
    else:
        rows.append([_btn("📬 Crear correo", "correo:new")])
    rows.append([_CLOSE])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _main_text(acc: dict | None, left_h: float) -> str:
    if acc:
        return (
            "<blockquote>\n"
            "📧 <b>Correo temporal</b>\n"
            "━━━━━━━━━━━━━━\n"
            f"✉️ Correo activo:\n<code>{escape_html(acc['email'])}</code>\n"
            f"⏳ Expira en: <b>{left_h:.1f} h</b> (24 h desde su creación)\n"
            "━━━━━━━━━━━━━━\n"
            "Selecciona una opción:\n"
            "</blockquote>"
        )
    return (
        "<blockquote>\n"
        "📧 <b>Correo temporal</b>\n"
        "━━━━━━━━━━━━━━\n"
        "⚠️ No tienes un correo activo.\n"
        "━━━━━━━━━━━━━━\n"
        "Toca <b>Crear correo</b> y recibe uno válido por 24 horas.\n"
        "Sin costo.\n"
        "</blockquote>"
    )


def _human_size(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n} B"


def _inbox_text(email: str, messages: list, left_h: float) -> str:
    lines = [
        "<blockquote>",
        "📥 <b>Bandeja de entrada</b>",
        "━━━━━━━━━━━━━━",
        f"✉️ <code>{escape_html(email)}</code>",
        f"⏳ Expira en: <b>{left_h:.1f} h</b>",
        "━━━━━━━━━━━━━━",
    ]
    if not messages:
        lines.append("📭 Sin mensajes aún.")
    else:
        for m in messages[:10]:
            subject = (m.get("subject") or "(sin asunto)")[:40]
            sender = (m.get("sender") or "?")[:30]
            ts = (m.get("received_at") or "")[:16].replace("T", " ")
            att = m.get("n_att") or 0
            mark = " 📎" if att else ""
            lines.append(f"📩 <b>{escape_html(subject)}</b>{mark}\n    De: {escape_html(sender)}\n    📅 {ts}")
    lines.append("</blockquote>")
    return "\n".join(lines)


def _inbox_markup(messages: list) -> InlineKeyboardMarkup:
    rows = []
    for m in messages[:10]:
        subject = (m.get("subject") or "(sin asunto)")[:30]
        att = m.get("n_att") or 0
        if att:
            subject = f"📎 {subject}"
        rows.append([_btn(f"📩 {subject}", f"correo:read:{m.get('received_at', '')}")])
    rows.append([_btn("🔄 Actualizar", "correo:inbox"), _HOME])
    rows.append([_CLOSE])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _message_text(msg: dict, n_att: int = 0) -> str:
    subject = msg.get("subject") or "(sin asunto)"
    sender = msg.get("sender") or "?"
    ts = (msg.get("received_at") or "")[:19].replace("T", " ")
    body = email_body_to_text(msg.get("body") or "").strip() or "(sin cuerpo de texto)"
    if len(body) > 3700:
        body = body[:3700] + "\n… (mensaje truncado)"
    extra = f"📎 <b>Adjuntos:</b> {n_att}\n" if n_att else ""
    return (
        "<blockquote>\n"
        f"📨 <b>{escape_html(subject)}</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"👤 <b>De:</b> {escape_html(str(sender))}\n"
        f"📅 <b>Fecha:</b> {ts}\n"
        f"{extra}"
        "━━━━━━━━━━━━━━\n"
        f"{escape_html(body)}\n"
        "</blockquote>"
    )


async def _edit(callback: CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return
        raise


@router.callback_query(F.data.startswith("correo:att:"))
async def cb_correo_att(callback: CallbackQuery):
    parts = callback.data.split(":", 2)[2].rsplit(":", 1)
    if len(parts) != 2 or not parts[1].isdigit():
        await callback.answer()
        return
    received_at, att_id = parts[0], int(parts[1])
    await callback.answer("⏳ Descargando archivo...")
    try:
        att = await svc.fetch_attachment(callback.from_user.id, received_at, att_id)
    except svc.CorreoError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if not att:
        await callback.answer("El archivo ya no está disponible.", show_alert=True)
        return
    if att["size"] > svc.MAX_ATTACHMENT_BYTES:
        await callback.answer(
            "Archivo demasiado grande para enviar por Telegram "
            f"({_human_size(att['size'])}).",
            show_alert=True,
        )
        return
    suffix = os.path.splitext(att["filename"])[1][:16]
    fd, tmp_path = tempfile.mkstemp(suffix=suffix or ".bin", prefix="gencookie_att_")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(att["data"])
        doc = FSInputFile(tmp_path, filename=att["filename"])
        await asyncio.wait_for(
            callback.message.answer_document(document=doc),
            timeout=180,
        )
        await callback.answer("✅ Archivo descargado.")
    except asyncio.TimeoutError:
        await callback.answer("La descarga tardó demasiado. Intenta de nuevo.", show_alert=True)
    except Exception:
        logger.warning("correo: fallo al enviar adjunto %s", att["filename"], exc_info=True)
        await callback.answer("No se pudo enviar el archivo. Intenta de nuevo.", show_alert=True)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


@router.message(Command("correo"))
async def cmd_correo(message: Message):
    user = message.from_user
    await register_user(user.id, user.username, user.full_name)
    acc = await svc.get_account(user.id)
    left = await svc.remaining_h(user.id) if acc else 0.0
    await message.answer(
        _main_text(acc, left),
        parse_mode=ParseMode.HTML,
        reply_markup=_main_markup(bool(acc)),
    )


@router.callback_query(F.data == "correo:home")
async def cb_correo_home(callback: CallbackQuery):
    acc = await svc.get_account(callback.from_user.id)
    left = await svc.remaining_h(callback.from_user.id) if acc else 0.0
    await _edit(callback, _main_text(acc, left), _main_markup(bool(acc)))
    await callback.answer()


@router.callback_query(F.data == "correo:close")
async def cb_correo_close(callback: CallbackQuery):
    try:
        await callback.message.delete()
    except TelegramBadRequest:
        pass
    await callback.answer()


@router.callback_query(F.data == "correo:new")
async def cb_correo_new(callback: CallbackQuery):
    acc = await svc.get_account(callback.from_user.id)
    if acc:
        await callback.message.edit_text(
            "<blockquote>\n⚠️ <b>Ya tienes un correo activo:</b>\n"
            f"<code>{escape_html(acc['email'])}</code>\n\n"
            "Si creas uno nuevo <b>el anterior se reemplaza</b>.\n"
            "¿Deseas continuar?\n</blockquote>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        _btn("✅ Sí, crear nuevo", "correo:new_confirm"),
                        _btn("❌ Cancelar", "correo:home"),
                    ]
                ]
            ),
        )
        await callback.answer()
        return
    await _do_create(callback)


@router.callback_query(F.data == "correo:new_confirm")
async def cb_correo_new_confirm(callback: CallbackQuery):
    await _do_create(callback)


async def _do_create(callback: CallbackQuery) -> None:
    await callback.answer("⏳ Creando correo...")
    try:
        acc = await svc.create_account(callback.from_user.id)
    except svc.CorreoError as exc:
        await callback.message.edit_text(
            f"<blockquote>\n⚠️ <b>Error al crear correo:</b>\n{escape_html(str(exc))}\n</blockquote>",
            parse_mode=ParseMode.HTML,
            reply_markup=_main_markup(False),
        )
        return
    await callback.message.edit_text(
        "<blockquote>\n✅ <b>Correo temporal creado</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"✉️ <code>{escape_html(acc['email'])}</code>\n"
        "⏳ Válido por <b>24 horas</b>.\n"
        "━━━━━━━━━━━━━━\n"
        "Ya puedes recibir correos en esta dirección.\n"
        "</blockquote>",
        parse_mode=ParseMode.HTML,
        reply_markup=_main_markup(True),
    )


@router.callback_query(F.data == "correo:inbox")
async def cb_correo_inbox(callback: CallbackQuery):
    await callback.answer("⏳ Consultando bandeja...")
    acc = await svc.get_account(callback.from_user.id)
    if not acc:
        await callback.message.edit_text(
            "<blockquote>\n❌ No tienes un correo activo. Crea uno primero.\n</blockquote>",
            parse_mode=ParseMode.HTML,
            reply_markup=_main_markup(False),
        )
        return
    try:
        messages = await svc.fetch_inbox(callback.from_user.id)
    except svc.CorreoError as exc:
        logger.warning("correo inbox: %s", exc)
        await callback.message.edit_text(
            f"<blockquote>\n⚠️ <b>Error al consultar la bandeja:</b>\n{escape_html(str(exc))}\n</blockquote>",
            parse_mode=ParseMode.HTML,
            reply_markup=_main_markup(True),
        )
        return
    left = await svc.remaining_h(callback.from_user.id)
    await _edit(callback, _inbox_text(acc["email"], messages, left), _inbox_markup(messages))


@router.callback_query(F.data.startswith("correo:read:"))
async def cb_correo_read(callback: CallbackQuery):
    received_at = callback.data.split(":", 2)[2] if ":" in callback.data else ""
    acc = await svc.get_account(callback.from_user.id)
    if not acc:
        await callback.answer("Sin cuenta activa.", show_alert=True)
        return
    try:
        messages = await svc.fetch_inbox(callback.from_user.id)
    except svc.CorreoError as exc:
        await callback.answer(f"Error: {exc}", show_alert=True)
        return
    msg = next((m for m in messages if m.get("received_at") == received_at), None)
    if not msg:
        await callback.answer("Mensaje no encontrado.", show_alert=True)
        return
    atts = await svc.list_attachments(callback.from_user.id, received_at)
    rows = []
    for a in atts:
        name = a.get("filename") or "archivo"
        size = _human_size(a.get("size") or 0)
        mark = " (no disponible)" if a.get("too_big") else f" · {size}"
        rows.append([_btn(f"⬇️ {name[:40]}{mark}", f"correo:att:{received_at}:{a['id']}")])
    rows.append([_btn("🗑 Eliminar", f"correo:del:{received_at}"), _btn("🔙 Bandeja", "correo:inbox")])
    await callback.message.edit_text(
        _message_text(msg, len(atts)),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("correo:del:"))
async def cb_correo_del(callback: CallbackQuery):
    received_at = callback.data.split(":", 2)[2] if ":" in callback.data else ""
    ok = await svc.delete_message(callback.from_user.id, received_at)
    await callback.answer("🗑 Eliminado." if ok else "No se pudo eliminar.")
    # Volver a la bandeja
    acc = await svc.get_account(callback.from_user.id)
    if acc:
        try:
            messages = await svc.fetch_inbox(callback.from_user.id)
        except svc.CorreoError:
            messages = []
        left = await svc.remaining_h(callback.from_user.id)
        await _edit(callback, _inbox_text(acc["email"], messages, left), _inbox_markup(messages))


def register_correo_handlers(dp) -> None:
    dp.include_router(router)
