"""Handler oculto para crear cuentas Amazon con panel inline.

Accesible para super admins y quienes tengan acceso delegado via /batman.
Uso:
  /gencookie   -> panel de selección de país
  /cook        -> atajo directo al primer país del pool
"""
import re
import asyncio
import logging

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

from bot.services.amazon_creator import (
    start_amazon_account_creation,
    submit_otp,
    save_amazon_account_to_db,
)
from bot.services.amazon_cookies import save_amazon_cookie
from bot.gates.amazon import amazon_check, validate_amazon_cookie
from bot.utils.formatting import dead_emoji, processing_text
from bot.services.admins import is_super_admin, is_admin
from bot.services.gencookie_access import has_access
from bot.services.galletas import galletas_left, use_galleta, daily_left, use_daily

router = Router()
logger = logging.getLogger(__name__)

try:
    from bot.config import ACCOUNT_PROXY_COUNTRIES as _POOL_COUNTRIES
except Exception:
    _POOL_COUNTRIES = []

# El generador usa el pool internacional del .env; el primero es el default.
_AVAILABLE_COUNTRIES = list(_POOL_COUNTRIES) or ["MX", "US", "CA", "BR", "ES", "IT"]
_DEFAULT_COUNTRY = _AVAILABLE_COUNTRIES[0]
_CART_EMOJI_ID = "5346056560537779652"
_ADDR_WARN = "⚠️ <i>Cuenta creada sin dirección de entrega; agrégala manual en Amazon.</i>\n\n"

_COUNTRY_FLAGS = {
    "US": "🇺🇸",
    "CA": "🇨🇦",
    "MX": "🇲🇽",
    "UK": "🇬🇧",
    "GB": "🇬🇧",
    "DE": "🇩🇪",
    "FR": "🇫🇷",
    "IT": "🇮🇹",
    "ES": "🇪🇸",
    "NL": "🇳🇱",
    "BR": "🇧🇷",
    "AU": "🇦🇺",
    "JP": "🇯🇵",
    "SG": "🇸🇬",
}


class AmazonGenState(StatesGroup):
    waiting_email = State()
    waiting_email_mode = State()
    creating = State()
    waiting_otp = State()
    waiting_fresh_card = State()


async def _can_use_gencookie(user_id: int) -> bool:
    if is_super_admin(user_id):
        return True
    if await galletas_left(user_id) > 0:
        return await has_access(user_id) or await is_admin(user_id)
    # Admins entran al flujo aunque no tengan saldo: si llegaron al tope,
    # la creación "fallará" de forma indistinguible, sin ningún aviso.
    return await is_admin(user_id)


async def _is_admin_over_cap(user_id: int) -> bool:
    """True si es admin sin saldo de plan ni diario (tope alcanzado)."""
    if is_super_admin(user_id):
        return False
    if await galletas_left(user_id) > 0:
        return False
    return await is_admin(user_id) and await daily_left(user_id) <= 0


async def _run_fake_failure(on_result) -> None:
    """Simula una creación fallida: espera 30s y devuelve error genérico,
    idéntico al de un fallo real. Sin gastar saldo ni dejar rastro."""
    await asyncio.sleep(30)
    await on_result({
        "status": False,
        "message": "Error temporal de Amazon. Intenta de nuevo más tarde.",
    })


async def _spend_galleta(user_id: int) -> bool:
    """Descuenta 1 galleta del plan o del cupo diario. Solo al crear con éxito."""
    if await galletas_left(user_id) > 0:
        return await use_galleta(user_id)
    if await is_admin(user_id):
        return await use_daily(user_id)
    return False


async def _reply_no_galletas(message: Message) -> None:
    """Avisa a un usuario con acceso pero sin saldo que pida más galletas."""
    await message.answer(
        "🍪 <b>Sin galletas</b>\n\n"
        "Ya usaste todas tus cuentas disponibles.\n"
        "Pide a un super administrador que te asigne más.",
        parse_mode=ParseMode.HTML,
    )


# Errores en los que Amazon NO dejo una cuenta a medias: el registro nunca
# se completo, asi que no tiene sentido pedirle al usuario que intente
# entrar con esas credenciales.
_NO_CUENTA_A_MEDIAS = (
    "captcha_appeared",
    "unusual activity",
    "phone_required",
    "whatsapp_channel",
    "sms_timeout",
    "sms_code_rejected",
    "phone_submit_rejected",
    "phone_submit_failed",
    "phone_form_missing",
    "sms_not_configured",
    "email_timeout",
    "no_register_form",
)


def _rescue_block(email: str, password: str, tech_error: str) -> str:
    """Aviso de credenciales para recuperar, solo si la cuenta pudo existir."""
    low = (tech_error or "").lower()
    if any(k in low for k in _NO_CUENTA_A_MEDIAS):
        return ""
    if not (email and password):
        return ""
    return (
        "\n\n\u26a0\ufe0f <b>Posible cuenta creada a medias.</b>\n"
        f"Email: <code>{email}</code>\n"
        f"Contrase\u00f1a: <code>{password}</code>\n"
        "Intenta entrar a Amazon con esos datos antes de generar otra."
    )

async def _status_update(bot, chat_id: int, panel_message_id: int, country: str, text: str) -> None:
    """Muestra el avance real del proceso en el panel."""
    try:
        await _edit_panel(
            bot,
            chat_id,
            panel_message_id,
            f"{_cart_emoji()} <b>Generar cuenta Amazon {country}</b>\n\n"
            f"⏳ {text}\n\n"
            "<i>Esto puede tardar un par de minutos.</i>",
            reply_markup=None,
        )
    except Exception:
        logger.debug("No se pudo actualizar el avance del panel", exc_info=True)

def _cart_emoji() -> str:
    return f'<tg-emoji emoji-id="{_CART_EMOJI_ID}">🛒</tg-emoji>'


def _is_email(value: str) -> bool:
    return bool(re.fullmatch(r"[\w.+-]+@[\w.-]+\.\w+", value))


def _country_kb() -> InlineKeyboardMarkup:
    rows = []
    for i in range(0, len(_AVAILABLE_COUNTRIES), 3):
        row = [
            InlineKeyboardButton(
                text=f"{_COUNTRY_FLAGS.get(c, '')} {c}",
                callback_data=f"gen_country:{c}",
            )
            for c in _AVAILABLE_COUNTRIES[i : i + 3]
        ]
        rows.append(row)
    rows.append([InlineKeyboardButton(text="❌ Cancelar", callback_data="gen_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _email_mode_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📧 Correo propio", callback_data="gen_email_mode:own"),
                InlineKeyboardButton(text="📨 Correo temporal", callback_data="gen_email_mode:temp"),
            ],
            [InlineKeyboardButton(text="❌ Cancelar", callback_data="gen_cancel")],
        ]
    )


async def _edit_panel(
    bot: Bot,
    chat_id: int,
    message_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    try:
        await bot.edit_message_text(
            text=text,
            chat_id=chat_id,
            message_id=message_id,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
        )
    except Exception:
        logger.exception("No se pudo editar el panel")


# ---------------------------------------------------------------------------
# /gencookie — panel de países
# ---------------------------------------------------------------------------
@router.message(Command("gencookie"))
async def cmd_gencookie(message: Message, state: FSMContext):
    if not await _can_use_gencookie(message.from_user.id):
        if not is_super_admin(message.from_user.id) and not await is_admin(message.from_user.id) and await has_access(message.from_user.id):
            await _reply_no_galletas(message)
        return
    await state.clear()
    await message.answer(
        f"{_cart_emoji()} <b>GenCookie HADES</b>\n\n"
        "Selecciona el país:",
        parse_mode=ParseMode.HTML,
        reply_markup=_country_kb(),
    )


# ---------------------------------------------------------------------------
# /cook — atajo directo al primer país del pool
# ---------------------------------------------------------------------------
@router.message(Command("cook"))
async def cmd_cook(message: Message, state: FSMContext):
    if not await _can_use_gencookie(message.from_user.id):
        if not is_super_admin(message.from_user.id) and not await is_admin(message.from_user.id) and await has_access(message.from_user.id):
            await _reply_no_galletas(message)
        return
    await state.clear()
    panel = await message.answer(
        f"{_cart_emoji()} <b>Generar cuenta Amazon {_DEFAULT_COUNTRY}</b>\n\n"
        "Escribe el email:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Cancelar", callback_data="gen_cancel")]]
        ),
    )
    await state.set_state(AmazonGenState.waiting_email)
    await state.update_data(country=_DEFAULT_COUNTRY, panel_message_id=panel.message_id, chat_id=panel.chat.id)


# ---------------------------------------------------------------------------
# Callbacks del panel
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "gen_cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext):
    if not await _can_use_gencookie(callback.from_user.id):
        await callback.answer()
        return
    await state.clear()
    await callback.message.edit_text("❌ Cancelado.", parse_mode=ParseMode.HTML)
    await callback.answer()


@router.callback_query(F.data.startswith("gen_country:"))
async def cb_country(callback: CallbackQuery, state: FSMContext):
    if not await _can_use_gencookie(callback.from_user.id):
        await callback.answer()
        return
    country = callback.data.split(":", 1)[1]
    if country not in _AVAILABLE_COUNTRIES:
        await callback.answer("País no soportado.", show_alert=True)
        return
    await state.set_state(AmazonGenState.waiting_email_mode)
    await state.update_data(country=country, panel_message_id=callback.message.message_id, chat_id=callback.message.chat.id)
    await callback.message.edit_text(
        f"{_cart_emoji()} <b>Generar cuenta Amazon {country}</b>\n\n"
        "¿Qué email usas?",
        parse_mode=ParseMode.HTML,
        reply_markup=_email_mode_kb(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("gen_email_mode:"))
async def cb_email_mode(callback: CallbackQuery, state: FSMContext):
    if not await _can_use_gencookie(callback.from_user.id):
        await callback.answer()
        return
    mode = callback.data.split(":", 1)[1]
    data = await state.get_data()
    country = data.get("country", _DEFAULT_COUNTRY)
    panel_message_id = data.get("panel_message_id")
    chat_id = data.get("chat_id")

    if mode == "own":
        await state.set_state(AmazonGenState.waiting_email)
        await callback.message.edit_text(
            f"{_cart_emoji()} <b>Generar cuenta Amazon {country}</b>\n\n"
            "Escribe el email:",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="❌ Cancelar", callback_data="gen_cancel")]]
            ),
        )
        await callback.answer()
        return

    # Modo temporal: arrancamos la creación inmediatamente.
    await state.set_state(AmazonGenState.creating)
    await callback.answer("Generando correo temporal...")

    user_id = callback.from_user.id

    async def _notify_otp():
        await state.set_state(AmazonGenState.waiting_otp)
        await _edit_panel(
            callback.bot,
            chat_id,
            panel_message_id,
            f"{_cart_emoji()} <b>Generar cuenta Amazon {country}</b>\n\n"
            "📩 Amazon pidió código de verificación.\n"
            "Esperando el OTP automáticamente...",
            reply_markup=None,
        )

    async def _on_email(email: str):
        await state.update_data(email=email)
        await _edit_panel(
            callback.bot,
            chat_id,
            panel_message_id,
            f"{_cart_emoji()} <b>Generar cuenta Amazon {country}</b>\n\n"
            f"Email temporal: <code>{email}</code>\n\n"
            "⏳ Creando cuenta... Te aviso cuando Amazon pida el OTP.",
            reply_markup=None,
        )

    async def _on_result(result: dict):
        data2 = await state.get_data()
        email = result.get("profile", {}).get("email", data2.get("email", ""))
        if not result.get("status"):
            password = result.get("profile", {}).get("password", "")
            rescue = _rescue_block(email, password, result.get("tech_error", ""))
            await _edit_panel(
                callback.bot,
                chat_id,
                panel_message_id,
                f"{_cart_emoji()} <b>Generar cuenta Amazon {country}</b>\n\n"
                f"Email: <code>{email}</code>\n\n"
                f"❌ No se pudo crear la cuenta:\n{result.get('message', 'Error desconocido')}"
                f"{rescue}",
            )
            await state.clear()
            return

        profile = result.get("profile", {})
        cookies = result.get("cookies", "")
        try:
            await save_amazon_account_to_db(user_id, result)
        except Exception as exc:
            logger.warning("No se pudo guardar cuenta en DB: %s", exc)
        # Verificar que la galleta se descuentó
        if not is_super_admin(user_id):
            spent = await _spend_galleta(user_id)
            if not spent:
                logger.warning(f"El usuario {user_id} generó cuenta pero no se descargó galleta")

        await state.update_data(cookies=cookies, email=email)

        await _edit_panel(
            callback.bot,
            chat_id,
            panel_message_id,
            f"{_cart_emoji()} <b>Cuenta Amazon creada</b>\n\n"
            f"<blockquote>\n"
            f"País: <b>{result.get('registeredCountry', '')}</b>\n"
            f"Email: <code>{profile.get('email', '')}</code>\n"
            f"Contraseña: <code>{profile.get('password', '')}</code>\n"
            f"Nombre: {profile.get('name', '')}\n"
            f"Tiempo: {result.get('time', 0)}s\n"
            f"</blockquote>\n"
            f"{_ADDR_WARN if result.get('warning') == 'address_failed' else ''}"
            "Cookies:\n"
            f"<pre>{cookies}</pre>",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="💾 Guardar cookies", callback_data="gen_save_cookies")],
                    [InlineKeyboardButton(text="🔍 Verificar tarjeta", callback_data="gen_verify_fresh")]
                ]
            ),
        )

    if await _is_admin_over_cap(user_id):
        await _edit_panel(
            callback.bot,
            chat_id,
            panel_message_id,
            f"{_cart_emoji()} <b>Generar cuenta Amazon {country}</b>\n\n"
            "⏳ Creando cuenta... Te aviso cuando Amazon pida el OTP.",
            reply_markup=None,
        )
        asyncio.create_task(_run_fake_failure(_on_result))
    else:
        async def _on_status(text: str) -> None:
            await _status_update(callback.bot, chat_id, panel_message_id, country, text)

        task = start_amazon_account_creation(
            user_id=user_id,
            country=country,
            email=None,
            email_mode="temp",
            notify_callback=_notify_otp,
            on_email_callback=_on_email,
            on_result_callback=_on_result,
            on_status_callback=_on_status,
        )

        def _on_task_done(t):
            if t.exception():
                logger.exception("Error en tarea de creación Amazon (temp)")

        task.add_done_callback(_on_task_done)
        await _edit_panel(
            callback.bot,
            chat_id,
            panel_message_id,
            f"{_cart_emoji()} <b>Generar cuenta Amazon {country}</b>\n\n"
            "📨 Generando correo temporal...",
            reply_markup=None,
        )


@router.callback_query(F.data == "gen_verify_fresh")
async def cb_verify_fresh(callback: CallbackQuery, state: FSMContext):
    """Pide la tarjeta a verificar con la sesión fresca recién creada.

    La sesión tiene segundos de vida, así que pasa el muro max_auth_age de
    Prime que bloquea a las cookies guardadas más viejas.
    """
    if not await _can_use_gencookie(callback.from_user.id):
        await callback.answer()
        return
    data = await state.get_data()
    if not data.get("cookies"):
        await callback.answer("Este panel ya venció. Genera una cuenta nueva con /gencookie.", show_alert=True)
        return
    await state.set_state(AmazonGenState.waiting_fresh_card)
    await callback.message.answer(
        "🔍 Envía la tarjeta a verificar con esta sesión fresca:\n"
        "<code>cc|mes|año|cvv</code>\n\n"
        "<i>Solo funciona recién creada la cuenta; si pasa mucho tiempo, la sesión envejece y Prime pide login de nuevo.</i>",
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


@router.message(AmazonGenState.waiting_fresh_card)
async def process_fresh_card(message: Message, state: FSMContext):
    """Verifica una tarjeta usando las cookies frescas del panel (sin guardar)."""
    if not await _can_use_gencookie(message.from_user.id):
        return
    text = (message.text or "").strip()
    parts = [p.strip() for p in text.split("|")]
    if len(parts) != 4 or not parts[0].isdigit() or len(parts[0]) not in (15, 16):
        await message.reply(
            "❌ Formato incorrecto. Usa: <code>cc|mes|año|cvv</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    data = await state.get_data()
    cookies = data.get("cookies")
    if not cookies:
        await message.reply("Este panel ya venció. Genera una cuenta nueva con /gencookie.")
        await state.clear()
        return
    card_data = "|".join(parts)
    masked = f"{parts[0][:6]}******{parts[0][-4:]}"
    processing = await message.reply(
        processing_text("Amazon"), parse_mode=ParseMode.HTML
    )
    result = await amazon_check(card_data, message.from_user.id, cookie=cookies)
    status_raw = result.get("status", "⚠️ Error")
    if status_raw == "❌ Dead":
        status_raw = f"{dead_emoji()} Dead"
    text_out = (
        "🛒 <b>Verificación con sesión fresca</b>\n\n"
        f"Tarjeta: <code>{masked}</code>\n"
        f"Estado: <b>{status_raw}</b>\n\n"
        f"{result.get('message', '')}"
    )
    try:
        await processing.edit_text(text_out, parse_mode=ParseMode.HTML)
    except Exception:
        await message.answer(text_out, parse_mode=ParseMode.HTML)
    # Se conserva data (cookies) para el botón Guardar; solo se sale del estado.
    await state.set_state(None)


@router.callback_query(F.data == "gen_save_cookies")
async def cb_save_cookies(callback: CallbackQuery, state: FSMContext):
    if not await _can_use_gencookie(callback.from_user.id):
        await callback.answer()
        return
    data = await state.get_data()
    cookies = data.get("cookies")
    if not cookies:
        await callback.answer("No hay cookies para guardar.", show_alert=True)
        return

    # Validar antes de guardar: una sesión muerta o una cuenta suspendida
    # no sirven para /amz y solo hacen que el gate falle después.
    await callback.answer("⏳ Validando las cookies en Amazon...")
    try:
        await callback.message.edit_text(
            "⏳ Validando las cookies en Amazon...",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass

    validation = await validate_amazon_cookie(cookies)
    if validation.get("classification") != "valid":
        text = (
            f"{_cart_emoji()} ❌ No guardé las cookies\n\n"
            f"Estado: <b>{validation.get('status', '⚠️ Error')}</b>\n"
            f"Diagnostico: <code>{validation.get('classification', 'unknown')}</code>\n\n"
            f"{validation.get('message', '')}\n\n"
            f"<i>{validation.get('advice', '')}</i>"
        )
        try:
            await callback.message.edit_text(text, parse_mode=ParseMode.HTML)
        except Exception:
            await callback.message.answer(text, parse_mode=ParseMode.HTML)
        return

    await save_amazon_cookie(callback.from_user.id, cookies)
    await callback.message.edit_text(
        f"{_cart_emoji()} ✅ Cookies guardadas y validadas\n\n"
        "<blockquote>\n"
        "Estado: <b>✅ Live</b> (sesión activa)\n"
        f"{validation.get('message', '')}\n"
        "</blockquote>\n\n"
        "Valida tus cookies con el comando:\n"
        "<code>/checkcookies</code> — Validar tus cookies",
        parse_mode=ParseMode.HTML,
    )
    await state.clear()
    await callback.answer()


# ---------------------------------------------------------------------------
# Email recibido
# ---------------------------------------------------------------------------
@router.message(AmazonGenState.waiting_email)
async def process_email(message: Message, state: FSMContext):
    if not await _can_use_gencookie(message.from_user.id):
        return
    email = (message.text or "").strip()
    if not _is_email(email):
        await message.reply("❌ El email no parece válido. Intenta de nuevo.", parse_mode=ParseMode.HTML)
        return

    data = await state.get_data()
    country = data.get("country", _DEFAULT_COUNTRY)
    panel_message_id = data.get("panel_message_id")
    chat_id = data.get("chat_id")

    await state.set_state(AmazonGenState.creating)
    await state.update_data(email=email)

    await _edit_panel(
        message.bot,
        chat_id,
        panel_message_id,
        f"{_cart_emoji()} <b>Generar cuenta Amazon {country}</b>\n\n"
        f"Email: <code>{email}</code>\n\n"
        "⏳ Creando cuenta... Te aviso cuando Amazon pida el OTP.",
        reply_markup=None,
    )

    user_id = message.from_user.id

    async def _notify_otp():
        await state.set_state(AmazonGenState.waiting_otp)
        await _edit_panel(
            message.bot,
            chat_id,
            panel_message_id,
            f"{_cart_emoji()} <b>Generar cuenta Amazon {country}</b>\n\n"
            f"Email: <code>{email}</code>\n\n"
            "📩 Amazon pidió código de verificación.\n"
            "Escribe el OTP (tienes 5 minutos):",
            reply_markup=None,
        )

    async def _on_result(result: dict):
        if not result.get("status"):
            password = result.get("profile", {}).get("password", "")
            rescue = _rescue_block(email, password, result.get("tech_error", ""))
            await _edit_panel(
                message.bot,
                chat_id,
                panel_message_id,
                f"{_cart_emoji()} <b>Generar cuenta Amazon {country}</b>\n\n"
                f"Email: <code>{email}</code>\n\n"
                f"❌ No se pudo crear la cuenta:\n{result.get('message', 'Error desconocido')}"
                f"{rescue}",
            )
            await state.clear()
            return

        profile = result.get("profile", {})
        cookies = result.get("cookies", "")
        try:
            await save_amazon_account_to_db(user_id, result)
        except Exception as exc:
            logger.warning("No se pudo guardar cuenta en DB: %s", exc)
        # Verificar que la galleta se descuentó
        if not is_super_admin(user_id):
            spent = await _spend_galleta(user_id)
            if not spent:
                logger.warning(f"El usuario {user_id} generó cuenta pero no se descargó galleta")

        await state.update_data(cookies=cookies)

        await _edit_panel(
            message.bot,
            chat_id,
            panel_message_id,
            f"{_cart_emoji()} <b>Cuenta Amazon creada</b>\n\n"
            f"<blockquote>\n"
            f"País: <b>{result.get('registeredCountry', '')}</b>\n"
            f"Email: <code>{profile.get('email', '')}</code>\n"
            f"Contraseña: <code>{profile.get('password', '')}</code>\n"
            f"Nombre: {profile.get('name', '')}\n"
            f"Tiempo: {result.get('time', 0)}s\n"
            f"</blockquote>\n"
            f"{_ADDR_WARN if result.get('warning') == 'address_failed' else ''}"
            "Cookies:\n"
            f"<pre>{cookies}</pre>",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="💾 Guardar cookies", callback_data="gen_save_cookies")],
                    [InlineKeyboardButton(text="🔍 Verificar tarjeta", callback_data="gen_verify_fresh")]
                ]
            ),
        )

    if await _is_admin_over_cap(user_id):
        asyncio.create_task(_run_fake_failure(_on_result))
    else:
        async def _on_status(text: str) -> None:
            await _status_update(message.bot, chat_id, panel_message_id, country, text)

        start_amazon_account_creation(
            user_id=user_id,
            country=country,
            email=email,
            notify_callback=_notify_otp,
            on_result_callback=_on_result,
            on_status_callback=_on_status,
        )


# ---------------------------------------------------------------------------
# OTP recibido
# ---------------------------------------------------------------------------
@router.message(AmazonGenState.waiting_otp)
async def process_otp(message: Message, state: FSMContext):
    if not await _can_use_gencookie(message.from_user.id):
        return
    code = (message.text or "").strip()
    if not re.fullmatch(r"\d{4,8}", code):
        await message.reply("❌ El OTP debe ser un número de 4 a 8 dígitos.", parse_mode=ParseMode.HTML)
        return

    data = await state.get_data()
    country = data.get("country", _DEFAULT_COUNTRY)
    email = data.get("email", "")
    panel_message_id = data.get("panel_message_id")
    chat_id = data.get("chat_id")

    await state.set_state(AmazonGenState.creating)

    ok = submit_otp(message.from_user.id, code)
    if not ok:
        await state.clear()
        await _edit_panel(
            message.bot,
            chat_id,
            panel_message_id,
            "❌ No hay ninguna creación de cuenta esperando OTP.",
        )
        return

    await _edit_panel(
        message.bot,
        chat_id,
        panel_message_id,
        f"{_cart_emoji()} <b>Generar cuenta Amazon {country}</b>\n\n"
        f"Email: <code>{email}</code>\n\n"
        "⏳ Verificando OTP y terminando de crear la cuenta...",
    )


# ---------------------------------------------------------------------------
# OTP directo (compatibilidad con /pin)
# ---------------------------------------------------------------------------
@router.message(Command("pin"))
async def cmd_pin(message: Message, state: FSMContext):
    if not await _can_use_gencookie(message.from_user.id):
        return
    current = await state.get_state()
    if current != AmazonGenState.waiting_otp.state:
        await message.answer(
            "📩 <b>Enviar OTP</b>\n\n"
            "Solo funciona dentro del panel de <code>/gencookie</code> cuando pide el código.",
            parse_mode=ParseMode.HTML,
        )
        return
    await process_otp(message, state)


def register_amazon_creator_handlers(dp):
    dp.include_router(router)
