import os
from pathlib import Path

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, FSInputFile, ErrorEvent
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest

from bot.keyboards import main_menu, back_button, gates_menu, gates_category_menu, gate_detail_menu
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

# Custom emoji para las 3 categorías (Auth / Charged / CCN Charged)
_GATE_CATEGORY_EMOJI_ID = "4970142833605345805"


def _gate_emoji(fallback: str) -> str:
    """Devuelve el custom emoji del panel Gates."""
    return f'<tg-emoji emoji-id="{_GATE_PANEL_EMOJI_ID}">{fallback}</tg-emoji>'


def _cat_emoji(fallback: str) -> str:
    """Devuelve el custom emoji de las categorías Auth / Charged / CCN Charged."""
    return f'<tg-emoji emoji-id="{_GATE_CATEGORY_EMOJI_ID}">{fallback}</tg-emoji>'


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
    """Opción GATES: 3 botones por categoría (Auth / Charged / CCN Charged)."""
    g = _gate_emoji
    c = _cat_emoji
    text = (
        "<blockquote>\n"
        f"{g('⚡')} <b>GATES DISPONIBLES</b>\n"
        "Selecciona una categoría para ver sus gates.\n\n"
        f"{c('🔐')} Auth — Ideal para ccs\n"
        f"{c('💳')} Charged — Cobro con cvv\n"
        f"{c('💎')} CCN Charged — Cobro sin revisar cvv\n\n"
        "Masivos: cuestan 5 créditos, gratis e ilimitado para miembros Olimpo.\n"
        f"{g('🛠️')} /gen — Generador de tarjetas\n"
        f"{g('💳')} /extra — Extrapolador de series\n"
        "</blockquote>"
    )
    await _edit_or_send(callback, text, gates_menu())
    await callback.answer()


def _gates_category_text(category: str) -> str:
    g = _gate_emoji
    c = _cat_emoji
    if category == "auth":
        return (
            "<blockquote>\n"
            f"{c('🔐')} <b>AUTH</b>\n\n"
            f"{g('👻')} Ghost (PayPal auth $5 MXN)\n"
            "       /gh\n"
            "</blockquote>"
        )
    if category == "charged":
        return (
            "<blockquote>\n"
            f"{c('💳')} <b>CHARGED</b>\n\n"
            f"{g('🐍')} Medusa PayFlow Pro $11.39 USD\n"
            "       /md /mdmass\n"
            "</blockquote>"
        )
    return (
        "<blockquote>\n"
        f"{c('💎')} <b>CCN CHARGED</b>\n\n"
        f"{g('💎')} Philotes (1 MXN)\n"
        "       /ph /phmass\n"
        f"{g('⚡')} Eurinias (20 MXN)\n"
        "       /eu /eumass\n"
        f"{g('🔥')} Wojtek (50 MXN)\n"
        "       /wo /womass\n"
        f"{g('🔱')} Euridice (100 MXN)\n"
        "       /di /dimass\n"
        "</blockquote>"
    )


@router.callback_query(F.data.in_({"menu_gates_auth", "menu_gates_charged", "menu_gates_ccn"}))
async def cb_gates_category(callback: CallbackQuery):
    """Muestra la lista de gates dentro de la categoría elegida."""
    category = callback.data.removeprefix("menu_gates_")
    await _edit_or_send(
        callback,
        _gates_category_text(category),
        gates_category_menu(category),
    )
    await callback.answer()


# ─────────────────────────────────────────────────────────────
# Panel de cada gate: descripción + formato de uso.
# Namespace "menu_gate_*" (propio del menú). No confundir con el
# handler "gate_*" que ya existe en bot/handlers/gates.py.
# ─────────────────────────────────────────────────────────────

def _gate_detail_text(key: str) -> str:
    g = _gate_emoji
    details = {
        "ghost": (
            "<blockquote>\n"
            f"{g('👻')} <b>GHOST — Auth</b>\n"
            "PayPal auth de $5 MXN. Verifica si la tarjeta pasa la autenticación.\n\n"
            "Uso:\n"
            "<code>/gh cc|mes|año|cvv</code>\n\n"
            "Ejemplo:\n"
            "<code>/gh 4532640527811643|12|2028|123</code>\n\n"
            "Solo una tarjeta por vez, sin modo masivo.\n"
            "</blockquote>"
        ),
        "medusa": (
            "<blockquote>\n"
            f"{g('🐍')} <b>MEDUSA — Charged</b>\n"
            "PayFlow Pro CCN Charged de $11.39 USD. Hace un cargo real para confirmar la tarjeta.\n\n"
            "Uso:\n"
            "<code>/md cc|mes|año|cvv</code>\n\n"
            "Ejemplo:\n"
            "<code>/md 4347697133726676|04|2031|431</code>\n\n"
            "Masivo (máximo 10):\n"
            "<code>/mdmass</code> respondiendo a un mensaje con tarjetas.\n"
            "</blockquote>"
        ),
        "philotes": (
            "<blockquote>\n"
            f"{g('💎')} <b>PHILOTES — CCN Charged</b>\n"
            "Cargo de $1 MXN para validar la tarjeta.\n\n"
            "Uso:\n"
            "<code>/ph cc|mes|año|cvv</code>\n\n"
            "Ejemplo:\n"
            "<code>/ph 4532640527811643|12|2025|123</code>\n\n"
            "Masivo (máximo 10):\n"
            "<code>/phmass</code> respondiendo a un mensaje con tarjetas.\n"
            "</blockquote>"
        ),
        "eurinias": (
            "<blockquote>\n"
            f"{g('⚡')} <b>EURINIAS — CCN Charged</b>\n"
            "Cargo de $20 MXN para validar la tarjeta.\n\n"
            "Uso:\n"
            "<code>/eu cc|mes|año|cvv</code>\n\n"
            "Ejemplo:\n"
            "<code>/eu 4532640527811643|12|2025|123</code>\n\n"
            "Masivo (máximo 10):\n"
            "<code>/eumass</code> respondiendo a un mensaje con tarjetas.\n"
            "</blockquote>"
        ),
        "wojtek": (
            "<blockquote>\n"
            f"{g('🔥')} <b>WOJTEK — CCN Charged</b>\n"
            "Cargo de $50 MXN para validar la tarjeta.\n\n"
            "Uso:\n"
            "<code>/wo cc|mes|año|cvv</code>\n\n"
            "Ejemplo:\n"
            "<code>/wo 4532640527811643|12|2025|123</code>\n\n"
            "Masivo (máximo 10):\n"
            "<code>/womass</code> respondiendo a un mensaje con tarjetas.\n"
            "</blockquote>"
        ),
        "euridice": (
            "<blockquote>\n"
            f"{g('🔱')} <b>EURIDICE — CCN Charged</b>\n"
            "Cargo de $100 MXN para validar la tarjeta.\n\n"
            "Uso:\n"
            "<code>/di cc|mes|año|cvv</code>\n\n"
            "Ejemplo:\n"
            "<code>/di 4532640527811643|12|2025|123</code>\n\n"
            "Masivo (máximo 10):\n"
            "<code>/dimass</code> respondiendo a un mensaje con tarjetas.\n"
            "</blockquote>"
        ),
    }
    return details.get(key, "")


@router.callback_query(F.data.startswith("menu_gate_"))
async def cb_gate_detail(callback: CallbackQuery):
    """Muestra el panel de descripción del gate seleccionado."""
    key = callback.data.removeprefix("menu_gate_")
    text = _gate_detail_text(key)
    if not text:
        await callback.answer()
        return
    await _edit_or_send(callback, text, gate_detail_menu())
    await callback.answer()


# ─────────────────────────────────────────────────────────────
# Accesos directos: /gates y /perfil (sin pasar por /start).
# /gen ya existe en bot/handlers/gates.py, no se duplica aquí.
# Se registran antes de unknown_command para que no los capture.
# ─────────────────────────────────────────────────────────────

@router.message(Command("gates"))
async def cmd_gates(message: Message):
    """Abre directamente la sección de Gates."""
    user = message.from_user
    await register_user(user.id, user.username, user.full_name)
    g = _gate_emoji
    c = _cat_emoji
    text = (
        "<blockquote>\n"
        f"{g('⚡')} <b>GATES DISPONIBLES</b>\n"
        "Selecciona una categoría para ver sus gates.\n\n"
        f"{c('🔐')} Auth — Ideal para ccs\n"
        f"{c('💳')} Charged — Cobro con cvv\n"
        f"{c('💎')} CCN Charged — Cobro sin revisar cvv\n\n"
        "Masivos: cuestan 5 créditos, gratis e ilimitado para miembros Olimpo.\n"
        f"{g('🛠️')} /gen — Generador de tarjetas\n"
        f"{g('💳')} /extra — Extrapolador de series\n"
        "</blockquote>"
    )
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=gates_menu())


@router.message(Command("perfil"))
async def cmd_perfil(message: Message):
    """Abre directamente el perfil del usuario."""
    user = message.from_user
    await register_user(user.id, user.username, user.full_name)
    text = await _format_profile_text(user)
    try:
        if _WELCOME_IMAGE.exists():
            await message.answer_photo(
                photo=FSInputFile(_WELCOME_IMAGE),
                caption=text,
                parse_mode=ParseMode.HTML,
                reply_markup=back_button(),
            )
        else:
            await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=back_button())
    except TelegramBadRequest as exc:
        if "ENTITY_TEXT_INVALID" in str(exc):
            plain_text = strip_custom_emojis(text)
            if _WELCOME_IMAGE.exists():
                await message.answer_photo(
                    photo=FSInputFile(_WELCOME_IMAGE),
                    caption=plain_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=back_button(),
                )
            else:
                await message.answer(plain_text, parse_mode=ParseMode.HTML, reply_markup=back_button())
        else:
            raise


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
