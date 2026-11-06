"""Panel Herramientas: Generador (/gen), Extrapolador (/extra) y consulta BIN (/bin).

Namespace propio "menu_tool_*" para no empalmar con el panel de Gates
("menu_gate_*") ni con el handler "gate_*" de bot/handlers/gates.py.
"""
from __future__ import annotations

from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest

from bot.config import RFC_CREDIT_COST
from bot.keyboards import tools_menu, tool_detail_menu
from bot.services.users import register_user
from bot.utils.bin_lookup import get_card_info
from bot.utils.formatting import escape_html, strip_custom_emojis

router = Router()

_TOOL_CUSTOM_EMOJI_ID = "5454074580010295588"


def _tool_emoji(fallback: str) -> str:
    return f'<tg-emoji emoji-id="{_TOOL_CUSTOM_EMOJI_ID}">{fallback}</tg-emoji>'


def _tools_text() -> str:
    t = _tool_emoji
    return (
        "<blockquote>\n"
        f"{t('🧰')} <b>HERRAMIENTAS</b>\n"
        "Genera, extrapola y consulta tarjetas sin salir del menú.\n\n"
        f"{t('🛠️')} Generador — Crea tarjetas válidas por Luhn\n"
        "       /gen\n"
        f"{t('💳')} Extrapolador — Saca series desde un BIN o tus lives\n"
        "       /extra\n"
        f"{t('🔍')} Consultar BIN — Marca, banco y país de un BIN\n"
        "       /bin\n"
        f"{t('🪪')} Registro RFC — RFC y CURP de personal federalizado\n"
        "       /rfc\n"
        f"{t('📱')} Telcel — Megas gratis, factura y recarga con tarjeta\n"
        "       /telcel\n"
        "</blockquote>"
    )


def _tool_detail_text(key: str) -> str:
    t = _tool_emoji
    details = {
        "gen": (
            "<blockquote>\n"
            f"{t('🛠️')} <b>GENERADOR — /gen</b>\n"
            "Crea 10 tarjetas válidas por Luhn desde un BIN.\n\n"
            "Uso:\n"
            "<code>/gen BIN|mes|año|cvv</code>\n\n"
            "Ejemplo:\n"
            "<code>/gen 457249651136xxxx|06|2029|xxx</code>\n\n"
            "Acepta <code>rnd</code> en mes, año y cvv para aleatorios.\n"
            "El botón 🔄 Regenerar saca 10 nuevas con el mismo BIN.\n"
            "</blockquote>"
        ),
        "extra": (
            "<blockquote>\n"
            f"{t('💳')} <b>EXTRAPOLADOR — /extra</b>\n"
            "Saca series nuevas desde un BIN o desde tus tarjetas activas.\n\n"
            "Uso:\n"
            "<code>/extra</code> y sigue el panel\n\n"
            "Modos:\n"
            "🔢 Extrapolar BIN — escribe 6 a 10 dígitos o un template completo\n"
            "🎯 Extrapolar desde lives — pega mínimo 2 activas y escribe FIN\n"
            "</blockquote>"
        ),
        "bin": (
            "<blockquote>\n"
            f"{t('🔍')} <b>CONSULTAR BIN — /bin</b>\n"
            "Muestra marca, tipo, nivel, banco, teléfono y país de un BIN.\n\n"
            "Uso:\n"
            "<code>/bin 457249</code>\n\n"
            "Ejemplo:\n"
            "<code>/bin 457249</code>\n\n"
            "Acepta 6 u 8 dígitos. Si mandas la tarjeta completa,\n"
            "se usan sus primeros dígitos."
            "</blockquote>"
        ),
        "rfc": (
            "<blockquote>\n"
            f"{t('🪪')} <b>REGISTRO RFC — /rfc</b>\n"
            "Entrega un registro de RFC y CURP de personal federalizado.\n\n"
            "Uso:\n"
            "<code>/rfc</code> → confirma el cobro → recibes el registro\n\n"
            f"Cuesta {RFC_CREDIT_COST} créditos por registro.\n"
            "Cobra siempre salvo a super admins (admins también pagan).\n"
            "</blockquote>"
        ),
    }
    return details.get(key, "")


def _has_photo(message) -> bool:
    return message.photo is not None and len(message.photo) > 0


async def _edit_or_send(callback, text: str, reply_markup=None):
    """Igual que en start.py: edita caption si hay foto, si no edita texto."""
    try:
        if _has_photo(callback.message):
            await callback.message.edit_caption(
                caption=text,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
        else:
            await callback.message.edit_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
    except TelegramBadRequest as exc:
        if "ENTITY_TEXT_INVALID" in str(exc):
            plain_text = strip_custom_emojis(text)
            if _has_photo(callback.message):
                await callback.message.edit_caption(
                    caption=plain_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup,
                )
            else:
                await callback.message.edit_text(
                    plain_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup,
                )
        else:
            raise


@router.callback_query(F.data == "menu_tools")
async def cb_tools(callback):
    """Abre el panel de Herramientas."""
    await _edit_or_send(callback, _tools_text(), tools_menu())
    await callback.answer()


@router.callback_query(F.data.startswith("menu_tool_"))
async def cb_tool_detail(callback):
    """Muestra el detalle de cada herramienta."""
    key = callback.data.removeprefix("menu_tool_")
    text = _tool_detail_text(key)
    if not text:
        await callback.answer()
        return
    await _edit_or_send(callback, text, tool_detail_menu())
    await callback.answer()


@router.message(Command("tools"))
async def cmd_tools(message: Message):
    """Atajo directo al panel de Herramientas sin pasar por /start."""
    user = message.from_user
    await register_user(user.id, user.username, user.full_name)
    await message.answer(_tools_text(), parse_mode=ParseMode.HTML, reply_markup=tools_menu())


@router.message(Command("bin"))
async def cmd_bin(message: Message):
    """Consulta la info de un BIN (6 u 8 dígitos) en la base local."""
    user = message.from_user
    await register_user(user.id, user.username, user.full_name)

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "❌ Formato incorrecto.\n\n"
            "<code>/bin 457249</code>\n\n"
            "Ejemplo:\n"
            "<code>/bin 457249</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    raw = "".join(ch for ch in args[1].strip() if ch.isdigit())
    if len(raw) < 6:
        await message.answer(
            "❌ El BIN debe tener al menos 6 dígitos.\n\n"
            "Ejemplo:\n"
            "<code>/bin 457249</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    info = get_card_info(raw[:8])
    t = _tool_emoji
    if not info:
        await message.answer(
            "<blockquote>\n"
            f"{t('🔍')} <b>BIN {escape_html(raw[:8])}</b>\n\n"
            "No encontré ese BIN en la base local.\n"
            "Prueba con los primeros 6 dígitos de la tarjeta."
            "\n</blockquote>",
            parse_mode=ParseMode.HTML,
        )
        return

    await message.answer(
        "<blockquote>\n"
        f"{t('🔍')} <b>BIN <code>{escape_html(raw[:8])}</code></b>\n\n"
        f"{t('💎')} Marca: {escape_html(info.get('Marca', 'Desconocido'))}\n"
        f"{t('💎')} Tipo de tarjeta: {escape_html(info.get('Tipo de tarjeta', 'Desconocido'))}\n"
        f"{t('💎')} Nivel de tarjeta: {escape_html(info.get('Nivel de tarjeta', 'Desconocido'))}\n"
        f"{t('💎')} Banco: {escape_html(info.get('Banco', 'Desconocido'))}\n"
        f"{t('💎')} Teléfono: {escape_html(info.get('Teléfono', 'Desconocido'))}\n"
        f"{t('💎')} País: {escape_html(info.get('País', 'Desconocido'))}"
        "\n</blockquote>",
        parse_mode=ParseMode.HTML,
    )


def register_tools_handlers(dp):
    dp.include_router(router)
