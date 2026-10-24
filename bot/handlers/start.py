import os
from pathlib import Path

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, FSInputFile, ErrorEvent
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest

from bot.keyboards import main_menu, back_button, gates_menu
from bot.services.users import register_user
from bot.services.memberships import get_membership, format_end_date, is_membership_active
from bot.services.logger import log_new_user, log_unknown_command, log_error
from bot.utils.formatting import custom_emoji as ce, strip_custom_emojis
from bot.services.credits import get_balance

router = Router()

# Ruta de la imagen de bienvenida
_WELCOME_IMAGE = Path(__file__).parent.parent / "assets" / "welcome.png"

# Custom emoji para el panel de Gates
_GATE_PANEL_EMOJI_ID = "5454074580010295588"


def _gate_emoji(fallback: str) -> str:
    """Devuelve el custom emoji del panel Gates."""
    return f'<tg-emoji emoji-id="{_GATE_PANEL_EMOJI_ID}">{fallback}</tg-emoji>'


def welcome_text(first_name: str) -> str:
    """Texto de bienvenida minimalista."""
    return "<b>BIENVENIDO MORTAL</b>"


def _has_photo(message) -> bool:
    return message.photo is not None and len(message.photo) > 0


async def _edit_or_send(callback: CallbackQuery, text: str, reply_markup=None):
    """Edita caption si el mensaje es foto, o edita texto si es mensaje de texto.
    Si los custom emojis fallan, reintenta sin ellos.
    """
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


@router.message(Command("start"))
async def cmd_start(message: Message, bot):
    """Handler del comando /start: registra usuario y envía imagen + caption + menú inline."""
    user = message.from_user
    await register_user(user.id, user.username, user.full_name)

    # Alerta silenciosa al canal de logs
    await log_new_user(bot, user.id, user.username, user.full_name)

    caption = welcome_text(message.from_user.first_name or "Usuario")

    if _WELCOME_IMAGE.exists():
        await message.answer_photo(
            photo=FSInputFile(_WELCOME_IMAGE),
            caption=caption,
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu(is_admin=False),
        )
    else:
        await message.answer(
            caption,
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu(is_admin=False),
        )


@router.callback_query(F.data == "menu_back")
async def cb_back(callback: CallbackQuery):
    """Volver al menú principal."""
    caption = welcome_text(callback.from_user.first_name or "Usuario")

    if _has_photo(callback.message):
        await callback.message.edit_caption(
            caption=caption,
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu(is_admin=False),
        )
    else:
        await callback.message.edit_text(
            caption,
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu(is_admin=False),
        )
    await callback.answer()


@router.callback_query(F.data == "menu_close")
async def cb_close(callback: CallbackQuery):
    """Cerrar menú: elimina el mensaje completamente."""
    try:
        await callback.message.delete()
    except Exception:
        # Si no se puede borrar, editamos a un mensaje vacío como fallback
        await _edit_or_send(
            callback,
            "👋 <b>Menú cerrado.</b>\n\nUsa /start para volver a abrirlo.",
            reply_markup=None,
        )
    await callback.answer("Menú cerrado", show_alert=False)


# ─────────────────────────────────────────────────────────────
# Nuevas opciones del menú: PERFIL, GATES, PRECIOS, CONTACTO
# Por ahora muestran placeholders hasta que el usuario defina el contenido.
# ─────────────────────────────────────────────────────────────

async def _format_profile_text(user) -> str:
    """Formatea el texto del perfil con blockquote y negritas."""
    membership = await get_membership(user.id)
    credits = await get_balance(user.id)

    if is_membership_active(membership):
        plan = membership["label"]
        vencimiento = format_end_date(membership)
        olimpo = "Sí" if membership["plan"] == "olimpo" else "No"
    else:
        plan = "Sin plan asignado"
        vencimiento = "N/A"
        olimpo = "No"

    return (
        f"{ce('perfil', '👤')} <b>TU INFORMACIÓN</b>\n\n"
        "<blockquote>\n"
        f"<b>USUARIO=</b> {user.full_name or user.username or 'Usuario'}\n"
        f"<b>ID=</b> <code><i>{user.id}</i></code>\n"
        f"<b>Plan=</b> {plan}\n"
        f"<b>Vencimiento=</b> {vencimiento}\n"
        f"<b>OLIMPO=</b> {olimpo}\n"
        f"<b>CRÉDITOS=</b> {credits}\n"
        "</blockquote>"
    )


@router.callback_query(F.data == "menu_perfil")
async def cb_perfil(callback: CallbackQuery):
    """Opción PERFIL: muestra info del usuario sobre la imagen de Hades."""
    text = await _format_profile_text(callback.from_user)

    try:
        if _has_photo(callback.message):
            # Si ya estamos en una foto, solo editamos el caption
            await callback.message.edit_caption(
                caption=text,
                parse_mode=ParseMode.HTML,
                reply_markup=back_button(),
            )
        else:
            # Si no hay foto, enviamos la imagen de Hades con el caption
            if _WELCOME_IMAGE.exists():
                await callback.message.answer_photo(
                    photo=FSInputFile(_WELCOME_IMAGE),
                    caption=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=back_button(),
                )
            else:
                await callback.message.answer(
                    text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=back_button(),
                )
    except TelegramBadRequest as exc:
        if "ENTITY_TEXT_INVALID" in str(exc):
            plain_text = strip_custom_emojis(text)
            if _has_photo(callback.message):
                await callback.message.edit_caption(
                    caption=plain_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=back_button(),
                )
            else:
                if _WELCOME_IMAGE.exists():
                    await callback.message.answer_photo(
                        photo=FSInputFile(_WELCOME_IMAGE),
                        caption=plain_text,
                        parse_mode=ParseMode.HTML,
                        reply_markup=back_button(),
                    )
                else:
                    await callback.message.answer(
                        plain_text,
                        parse_mode=ParseMode.HTML,
                        reply_markup=back_button(),
                    )
        else:
            raise
    await callback.answer()


@router.callback_query(F.data == "menu_gates")
async def cb_gates(callback: CallbackQuery):
    """Opción GATES: lista de comandos disponibles."""
    g = _gate_emoji
    text = (
        "<blockquote>\n"
        f"{g('⚡')} <b>GATES DISPONIBLES</b>\n\n"
        f"{g('🔱')} /di — Euridice (100 MXN)\n"
        f"{g('💎')} /ph — Philotes (1 MXN)\n"
        f"{g('⚡')} /eu — Eurinias (20 MXN)\n"
        f"{g('🔥')} /wo — Wojtek (50 MXN)\n"
        f"{g('💎')} /rd — Radamanthys CCN AUTH $1 MXN\n"
        f"{g('🏛️')} /tl — Atlas Payflow CCN $7 USD\n"
        # /ir — Iris Stripe $6 USD (DESACTIVADO 2026-10-23: sin checkout real; no mapear)
        f"{g('👻')} /gh — Ghost - Paypal cvv auth $5 mxn (ccs)\n"
        f"{g('🛠️')} /gen — Generador de tarjetas\n"
        f"{g('💳')} /extra — Extrapolador de BINs\n\n"
        "Masivos: Costo 5 créditos, gratis e ilimitado para miembros olimpo\n"
        f"{g('🔱')} /dimass — Euridice mass\n"
        f"{g('💎')} /phmass — Philotes mass\n"
        f"{g('⚡')} /eumass — Eurinias mass\n"
        f"{g('🔥')} /womass — Wojtek mass\n"
        f"{g('💎')} /rdmass — Radamanthys CCN AUTH mass\n"
        f"{g('🏛️')} /tlmass — Atlas Payflow CCN $7 USD mass\n"
        # /irmass — Iris Stripe $6 USD mass (DESACTIVADO)
        "</blockquote>"
    )
    await _edit_or_send(callback, text, back_button())
    await callback.answer()


@router.callback_query(F.data == "menu_precios")
async def cb_precios(callback: CallbackQuery):
    """Opción PRECIOS: lista de planes y créditos en blockquote."""
    money = ce('precios_dinero', '💵')
    trident = ce('precios_tridente', '🔱')

    text = (
        "<blockquote>\n"
        f"{money} <b>PRECIOS HADES</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"{trident} Semanal\n"
        f"{money} Precio: $120 MXN\n\n"
        f"{trident} Quincenal\n"
        f"{money} Precio: $260 MXN\n\n"
        f"{trident} Plan Mensual\n"
        f"{money} Precio: $480 MXN\n\n"
        f"{trident} Creditos internos\n"
        f"{money} 1 MXN por crédito\n\n"
        f"{trident} Cookies 8 créditos\n"
        "</blockquote>"
    )
    await _edit_or_send(callback, text, back_button())
    await callback.answer()


@router.callback_query(F.data == "menu_contacto")
async def cb_contacto(callback: CallbackQuery):
    """Opción CONTACTO: grupo oficial y comando /staff."""
    text = (
        "<blockquote>\n"
        f"{ce('contacto', '📞')} <b>GRUPO OFICIAL</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "<a href=\"https://t.me/EternalTartaro\">ETERNAL TARTARO</a>\n\n"
        "DENTRO USA EL COMANDO /staff PARA VER LA LISTA DE ADMINISTRADORES CON QUIENES PUEDES ADQUIRIR EL SERVICIO\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "</blockquote>"
    )
    await _edit_or_send(callback, text, back_button())
    await callback.answer()


@router.message(F.text.startswith("/"))
async def unknown_command(message: Message, bot):
    """Captura comandos no reconocidos y alerta al canal de logs."""
    command = message.text.split()[0].split("@")[0]
    await log_unknown_command(bot, message.from_user.id, command, message.chat.type)


@router.errors()
async def global_error_handler(event: ErrorEvent, bot):
    """Captura errores no manejados y alerta al canal de logs."""
    exception = event.exception
    await log_error(bot, "Unhandled exception", f"{type(exception).__name__}: {exception}")


def register_start_handlers(dp):
    dp.include_router(router)
