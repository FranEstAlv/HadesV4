"""Handlers de los gates /di, /ph, /eu, /wo y sus variantes masivas."""
from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime
from typing import Dict, List, Optional

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext

from bot.gates.checker import gate_check, gate_check_multiple
from bot.gates.amazon import (
    amazon_check,
    amazon_check_multiple,
    amazon_check_cookie,
    amazon_refresh_cookie,
    validate_amazon_cookie,
)
from bot.handlers.hiperion import register_hiperion_handlers
from bot.services.memberships import get_membership, is_membership_active
import logging

from bot.services.admins import is_admin
from bot.services.credits import deduct_credits
from bot.services.amazon_cookies import save_amazon_cookie, delete_amazon_cookie
from bot.services.card_generator import CardGenerator, validate_gen_input
from bot.utils.bin_lookup import get_card_info
from bot.utils.formatting import (
    escape_html,
    dead_emoji,
    mass_processing_text,
    processing_text,
)

router = Router()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuración de gates
# ---------------------------------------------------------------------------
GATE_COMMANDS = {
    "di": "Euridice",
    "ph": "Philotes",
    "eu": "Eurinias",
    "wo": "Wojtek",
}

GATE_EMOJIS = {
    "Euridice": "🔱",
    "Philotes": "💎",
    "Eurinias": "⚡",
    "Wojtek": "🔥",
    "Amazon": "🛒",
}

# Planes permitidos por gate (igual que Hades legacy)
_ALL_PLANS = {"olimpo", "mensual", "quincenal", "semanal", "personalizado"}
GATE_PLANS = {
    "di": {"olimpo", "mensual", "quincenal"},
    "dimass": {"olimpo", "mensual", "quincenal"},
    "ph": {"olimpo", "mensual"},
    "phmass": {"olimpo", "mensual"},
    "eu": {"olimpo", "mensual"},
    "eumass": {"olimpo", "mensual"},
    "wo": {"olimpo", "mensual"},
    "womass": {"olimpo", "mensual"},
    "amz": {"olimpo", "mensual", "quincenal"},
    "amzmass": {"olimpo", "mensual", "quincenal"},
    "gen": _ALL_PLANS,
    "extra": _ALL_PLANS,
}

SINGLE_COOLDOWN = 7
MASS_COOLDOWN = 20
MASS_LIMIT = 10
MASS_CREDIT_COST = 5

_GEN_CUSTOM_EMOJI_ID = "5454074580010295588"
_GATE_CUSTOM_EMOJI_ID = "5454074580010295588"
_LIVE_CUSTOM_EMOJI_ID = "5021905410089550576"
# Lupa de validación y Dead unificados para todos los gates.
_SEARCH_CUSTOM_EMOJI_ID = "5017088445353296841"
_DEAD_CUSTOM_EMOJI_ID = "5019523782004441717"


def _dead_status() -> str:
    """Status Dead con el emoji personalizado pedido."""
    return f"{dead_emoji()} Dead"


def _gen_emoji(fallback: str) -> str:
    """Custom emoji para todas las respuestas de /gen."""
    return f'<tg-emoji emoji-id="{_GEN_CUSTOM_EMOJI_ID}">{fallback}</tg-emoji>'


def _gate_emoji(fallback: str) -> str:
    """Custom emoji para todas las respuestas de gates."""
    return f'<tg-emoji emoji-id="{_GATE_CUSTOM_EMOJI_ID}">{fallback}</tg-emoji>'

last_single_time: Dict[str, Dict[int, datetime]] = {cmd: {} for cmd in GATE_COMMANDS}
last_mass_time: Dict[str, Dict[int, datetime]] = {cmd: {} for cmd in GATE_COMMANDS}
# Gate Amazon MX (/amz, /amzmass): mismos cooldowns, registros propios.
last_single_time["amz"] = {}
last_mass_time["amz"] = {}

# Lote masivo de Amazon en curso por usuario (para poder detenerlo con
# /amzstop y para no dejar tareas huérfanas).
_mass_tasks: Dict[int, "asyncio.Task"] = {}


# ---------------------------------------------------------------------------
# Permisos y utilidades
# ---------------------------------------------------------------------------
async def _can_use_gate(user_id: int, command: str) -> bool:
    """Admins siempre pueden; los demás necesitan membresía activa y plan permitido."""
    if await is_admin(user_id):
        return True
    membership = await get_membership(user_id)
    if not is_membership_active(membership):
        return False
    plan = membership.get("plan", "").lower()
    return plan in GATE_PLANS.get(command, set())


async def _is_olimpo_or_admin(user_id: int) -> bool:
    """True si el usuario es admin o tiene plan olimpo."""
    if await is_admin(user_id):
        return True
    membership = await get_membership(user_id)
    if not is_membership_active(membership):
        return False
    return membership.get("plan", "").lower() == "olimpo"


def _denied_message(command: str) -> str:
    allowed = GATE_PLANS.get(command, set())
    if not allowed:
        return "❌ No tienes permiso para usar este gate."
    planes = ", ".join(sorted(allowed))
    return (
        "❌ <b>Acceso denegado</b>\n\n"
        f"El comando /<b>{escape_html(command)}</b> requiere uno de estos planes:\n"
        f"<blockquote>{planes}</blockquote>\n\n"
        "Contacta a un administrador para adquirir acceso."
    )


def _parse_command(message: Message) -> str:
    """Extrae el comando base sin @botname."""
    return message.text.split()[0].split("@")[0].lstrip("/")


def _get_bin_info(cc: str) -> Dict[str, str]:
    info = get_card_info(cc[:6])
    if not info:
        return {
            "Marca": "Desconocido",
            "Tipo de tarjeta": "Desconocido",
            "Nivel de tarjeta": "Desconocido",
            "Banco": "Desconocido",
            "Teléfono": "Desconocido",
            "País": "Desconocido",
        }
    return info


def _format_gate_response(
    card_data: str,
    result: Dict[str, str],
    gate_name: str,
    command: str,
) -> str:
    card_parts = card_data.split("|")
    if len(card_parts) == 4:
        cc = card_parts[0]
        bin_info = _get_bin_info(cc)
    else:
        bin_info = _get_bin_info("")

    status_raw = result.get("status", "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error")
    classification = result.get("classification", "")
    if status_raw == "✅ Live":
        status_icon = f'<tg-emoji emoji-id="{_LIVE_CUSTOM_EMOJI_ID}">✅</tg-emoji> Live'
        resultado = "APROBADA"
        response_text = result.get("message", "")
    elif status_raw == "❌ Dead":
        status_icon = _dead_status()
        resultado = "DECLINADA"
        response_text = result.get("message", "")
    elif status_raw in ("❌ Expirada", "🚫 Suspendida", "🔒 Verificacion", "🤖 CAPTCHA"):
        status_icon = status_raw
        resultado = classification.upper() if classification else "ERROR_COOKIES"
        response_text = result.get("message", "")
    elif status_raw == "🔌 Apagado":
        status_icon = status_raw
        resultado = "APAGADO"
        response_text = result.get("message", "")
    else:
        # Error de sesión (muro de Amazon, red, etc.). El mensaje sí se
        # muestra: es lo que le dice al usuario qué hacer (CVF, suspendida,
        # sin cuentas, etc.) y antes se perdía detrás de un "error" genérico.
        status_icon = status_raw
        resultado = classification.upper() if classification else "ERROR"
        response_text = result.get("message", "") or "error"

    e = _gate_emoji
    sep = "━━━━━━━━━━━━━━━━━━"
    card_code = f"<code>{escape_html(card_data)}</code>"

    # Origen de la sesión de Amazon: dice si el veredicto salió con la cookie
    # guardada o con una sesión abierta en caliente (login HTTP o navegador).
    session_source = result.get("session_source", "")
    source_line = ""
    if session_source == "navegador":
        source_line = f"{e('🌐')} Sesion: navegador real (muro max_auth_age)\n"
    elif session_source == "http":
        source_line = f"{e('🔑')} Sesion: login HTTP en caliente\n"
    elif session_source == "guardada":
        source_line = f"{e('🗄️')} Sesion: cookie guardada\n"

    info_block = (
        f"{e('💎')} Marca: {escape_html(bin_info['Marca'])}\n"
        f"{e('💎')} Tipo de tarjeta: {escape_html(bin_info['Tipo de tarjeta'])}\n"
        f"{e('💎')} Nivel de tarjeta: {escape_html(bin_info['Nivel de tarjeta'])}\n"
        f"{e('💎')} Banco: {escape_html(bin_info['Banco'])}\n"
        f"{e('💎')} Teléfono: {escape_html(bin_info['Teléfono'])}\n"
        f"{e('💎')} País: {escape_html(bin_info['País'])}"
    )

    return (
        f"{sep}\n"
        f"{e('🏛️')} <b>HADES</b> {e('🏛️')}\n"
        f"{sep}\n"
        f"{e('💎')} {gate_name} Check\n"
        f"{e('💎')} Comando: /{command}\n"
        f"{sep}\n"
        f"{e('💎')} {card_code}\n"
        f"{e('💎')} Status: {status_icon}\n"
        f"{e('💎')} Response: {escape_html(response_text)}\n"
        f"{e('💎')} Resultado: {resultado}\n"
        f"{e('💎')} Error: NINGUNO\n"
        f"{source_line}"
        f"{sep}\n"
        f"<blockquote>\n{info_block}\n</blockquote>\n"
        f"{sep}\n"
        f"{e('🏛️')} <b>HADES</b> {e('🏛️')}\n"
        f"{sep}"
    )


def _result_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="OLIMPO BINS Referencias",
                    url="https://t.me/olimpobins/1276",
                    style="primary",
                )
            ]
        ]
    )


async def _delete_after(message: Message, delay: int = 30) -> None:
    try:
        await asyncio.sleep(delay)
        await message.delete()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Comandos individuales
# ---------------------------------------------------------------------------
@router.message(Command(commands=["di", "ph", "eu", "wo"]))
async def cmd_gate_single(message: Message):
    command = _parse_command(message)
    gate_name = GATE_COMMANDS[command]
    user_id = message.from_user.id

    if not await _can_use_gate(user_id, command):
        await message.answer(_denied_message(command), parse_mode=ParseMode.HTML)
        return

    now = datetime.now()
    last = last_single_time[command].get(user_id)
    if last and (now - last).total_seconds() < SINGLE_COOLDOWN:
        remaining = SINGLE_COOLDOWN - (now - last).total_seconds()
        await message.answer(
            f"⏳ Debes esperar <b>{remaining:.1f}</b> segundos antes de volver a usar /{command}.",
            parse_mode=ParseMode.HTML,
        )
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            f"❌ Formato incorrecto.\n\n"
            f"/{command} &lt;cc&gt;|&lt;mes&gt;|&lt;año&gt;|&lt;cvv&gt;\n\n"
            f"Ejemplo:\n"
            f"/{command} 4532640527811643|12|2025|123"
        )
        return

    card_data = args[1].strip()
    if len(card_data.split("|")) != 4:
        await message.answer(
            "❌ Formato de tarjeta incorrecto. Usa: &lt;cc&gt;|&lt;mes&gt;|&lt;año&gt;|&lt;cvv&gt;"
        )
        return

    processing = await message.answer(
        processing_text(gate_name), parse_mode=ParseMode.HTML
    )

    result = await gate_check(gate_name, card_data, user_id)

    text = _format_gate_response(card_data, result, gate_name, command)
    try:
        result_msg = await processing.edit_text(text, reply_markup=_result_markup(), parse_mode=ParseMode.HTML)
    except TelegramBadRequest:
        result_msg = await message.answer(text, reply_markup=_result_markup(), parse_mode=ParseMode.HTML)

    if result.get("status") != "✅ Live":
        asyncio.create_task(_delete_after(result_msg))

    last_single_time[command][user_id] = datetime.now()


# ---------------------------------------------------------------------------
# Comandos masivos
# ---------------------------------------------------------------------------
def _extract_cards_from_text(text: Optional[str]) -> List[str]:
    """Extrae tarjetas en formato cc|mes|año|cvv de un bloque de texto."""
    if not text:
        return []

    text_clean = re.sub(r"<[^>]+>", "", text)
    text_clean = re.sub(r"[🔥🏛️🔹•\-]", "", text_clean)
    found: List[str] = []

    for line in text_clean.split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) != 4:
            continue
        cc, mes, ano, cvv = parts
        if not mes.isdigit() or not ano.isdigit():
            continue
        if cvv not in ("rnd", "xxx", "xxxx"):
            if not cvv.isdigit() or len(cvv) < 3 or len(cvv) > 4:
                continue
        if len(cc) not in (15, 16):
            continue
        if "x" in cc.lower():
            continue
        found.append(f"{cc}|{mes}|{ano}|{cvv}")

    return found


@router.message(Command(commands=["dimass", "phmass", "eumass", "womass"]))
async def cmd_gate_mass(message: Message):
    command = _parse_command(message)
    base_command = command.replace("mass", "")
    gate_name = GATE_COMMANDS[base_command]
    user_id = message.from_user.id

    if not await _can_use_gate(user_id, command):
        await message.answer(_denied_message(command), parse_mode=ParseMode.HTML)
        return

    now = datetime.now()
    last = last_mass_time[base_command].get(user_id)
    if last and (now - last).total_seconds() < MASS_COOLDOWN:
        remaining = MASS_COOLDOWN - (now - last).total_seconds()
        await message.answer(
            f"⏳ Debes esperar <b>{remaining:.1f}</b> segundos antes de volver a usar /{command}.",
            parse_mode=ParseMode.HTML,
        )
        return

    lines: List[str] = []

    # 1. Tarjetas respondiendo a otro mensaje
    if message.reply_to_message and message.reply_to_message.text:
        lines = _extract_cards_from_text(message.reply_to_message.text)

    # 2. Fallback: líneas del mensaje actual (después del comando)
    if not lines:
        text_parts = message.text.split("\n")
        if len(text_parts) > 1:
            lines = _extract_cards_from_text("\n".join(text_parts[1:]))

    if not lines:
        await message.answer(
            "❌ No se encontraron tarjetas para verificar.\n\n"
            f"Responde al mensaje con las tarjetas con /{command} o escríbelas a continuación.\n\n"
            "Formato aceptado: 15 o 16 dígitos|mes|año|cvv\n"
            "Ejemplo AMEX: 377713996217025|10|2026|0000\n"
            "Ejemplo VISA: 4532640527811643|12|2025|123"
        )
        return

    lines = lines[:MASS_LIMIT]
    total = len(lines)

    # Costo de mass: 5 créditos, excepto para admins y miembros olimpo
    if not await _is_olimpo_or_admin(user_id):
        success, new_balance = await deduct_credits(user_id, MASS_CREDIT_COST, f"Uso /{command}")
        if not success:
            await message.answer(
                f"❌ Los comandos masivos cuestan <b>{MASS_CREDIT_COST}</b> créditos.\n"
                f"Tu saldo actual es <b>{new_balance}</b>.\n\n"
                "Adquiere créditos o contrata el plan Olimpo para usarlos gratis e ilimitados.",
                parse_mode=ParseMode.HTML,
            )
            return

    processing = await message.answer(
        mass_processing_text(gate_name, 0, total), parse_mode=ParseMode.HTML
    )

    last_mass_time[base_command][user_id] = datetime.now()

    lock = asyncio.Lock()
    processed_count = 0
    current_processing = processing

    async def result_callback(index: int, result: Dict[str, str], card_data: str, uid: int) -> None:
        nonlocal processed_count, current_processing
        async with lock:
            text = _format_gate_response(card_data, result, gate_name, command)
            try:
                sent = await message.answer(text, reply_markup=_result_markup(), parse_mode=ParseMode.HTML)
            except TelegramBadRequest:
                sent = await message.answer(
                    escape_html(text), reply_markup=_result_markup()
                )
            if result.get("status") != "✅ Live":
                asyncio.create_task(_delete_after(sent))

            processed_count += 1
            if processed_count < total:
                try:
                    new_msg = await message.answer(
                        mass_processing_text(gate_name, processed_count, total),
                        parse_mode=ParseMode.HTML,
                    )
                except Exception:
                    new_msg = current_processing
                try:
                    await current_processing.delete()
                except Exception:
                    pass
                current_processing = new_msg
            else:
                try:
                    await current_processing.delete()
                except Exception:
                    pass

    asyncio.create_task(gate_check_multiple(gate_name, lines, user_id, result_callback))


# ---------------------------------------------------------------------------
# Gate Amazon MX: /amz (unitario) y /amzmass (masivo)
# ---------------------------------------------------------------------------
AMAZON_GATE_NAME = "Amazon"


@router.message(Command("amz"))
async def cmd_amz_single(message: Message):
    """Verifica una tarjeta con el gate Amazon MX."""
    command = _parse_command(message)
    user_id = message.from_user.id

    if not await _can_use_gate(user_id, command):
        await message.answer(_denied_message(command), parse_mode=ParseMode.HTML)
        return

    now = datetime.now()
    last = last_single_time["amz"].get(user_id)
    if last and (now - last).total_seconds() < SINGLE_COOLDOWN:
        remaining = SINGLE_COOLDOWN - (now - last).total_seconds()
        await message.answer(
            f"⏳ Debes esperar <b>{remaining:.1f}</b> segundos antes de volver a usar /{command}.",
            parse_mode=ParseMode.HTML,
        )
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "❌ Formato incorrecto.\n\n"
            "/amz &lt;cc&gt;|&lt;mes&gt;|&lt;año&gt;|&lt;cvv&gt;\n\n"
            "Ejemplo:\n"
            "/amz 4532640527811643|12|2025|123"
        )
        return

    card_data = args[1].strip()
    if len(card_data.split("|")) != 4:
        await message.answer(
            "❌ Formato de tarjeta incorrecto. Usa: &lt;cc&gt;|&lt;mes&gt;|&lt;año&gt;|&lt;cvv&gt;"
        )
        return

    processing = await message.answer(
        processing_text(f"{AMAZON_GATE_NAME} MX"), parse_mode=ParseMode.HTML
    )

    result = await amazon_check(card_data, user_id)

    text = _format_gate_response(card_data, result, AMAZON_GATE_NAME, command)
    try:
        result_msg = await processing.edit_text(text, reply_markup=_result_markup(), parse_mode=ParseMode.HTML)
    except TelegramBadRequest:
        result_msg = await message.answer(text, reply_markup=_result_markup(), parse_mode=ParseMode.HTML)

    if result.get("status") != "✅ Live":
        asyncio.create_task(_delete_after(result_msg))

    last_single_time["amz"][user_id] = datetime.now()


@router.message(Command("amzmass"))
async def cmd_amz_mass(message: Message):
    """Verifica un lote de tarjetas con el gate Amazon MX (máximo 10)."""
    command = _parse_command(message)
    user_id = message.from_user.id

    if not await _can_use_gate(user_id, command):
        await message.answer(_denied_message(command), parse_mode=ParseMode.HTML)
        return

    now = datetime.now()
    last = last_mass_time["amz"].get(user_id)
    if last and (now - last).total_seconds() < MASS_COOLDOWN:
        remaining = MASS_COOLDOWN - (now - last).total_seconds()
        await message.answer(
            f"⏳ Debes esperar <b>{remaining:.1f}</b> segundos antes de volver a usar /{command}.",
            parse_mode=ParseMode.HTML,
        )
        return

    lines: List[str] = []

    # 1. Tarjetas respondiendo a otro mensaje
    if message.reply_to_message and message.reply_to_message.text:
        lines = _extract_cards_from_text(message.reply_to_message.text)

    # 2. Fallback: líneas del mensaje actual (después del comando)
    if not lines:
        text_parts = message.text.split("\n")
        if len(text_parts) > 1:
            lines = _extract_cards_from_text("\n".join(text_parts[1:]))

    if not lines:
        await message.answer(
            "❌ No se encontraron tarjetas para verificar.\n\n"
            f"Responde al mensaje con las tarjetas con /{command} o escríbelas a continuación.\n\n"
            "Formato aceptado: 15 o 16 dígitos|mes|año|cvv\n"
            "Ejemplo AMEX: 377713996217025|10|2026|0000\n"
            "Ejemplo VISA: 4532640527811643|12|2025|123"
        )
        return

    lines = lines[:MASS_LIMIT]
    total = len(lines)

    # Costo de mass: 5 créditos, excepto para admins y miembros olimpo
    if not await _is_olimpo_or_admin(user_id):
        success, new_balance = await deduct_credits(user_id, MASS_CREDIT_COST, f"Uso /{command}")
        if not success:
            await message.answer(
                f"❌ Los comandos masivos cuestan <b>{MASS_CREDIT_COST}</b> créditos.\n"
                f"Tu saldo actual es <b>{new_balance}</b>.\n\n"
                "Adquiere créditos o contrata el plan Olimpo para usarlos gratis e ilimitados.",
                parse_mode=ParseMode.HTML,
            )
            return

    processing = await message.answer(
        mass_processing_text(f"{AMAZON_GATE_NAME} MX", 0, total),
        parse_mode=ParseMode.HTML,
    )

    last_mass_time["amz"][user_id] = datetime.now()

    lock = asyncio.Lock()
    processed_count = 0
    current_processing = processing
    started = time.monotonic()

    async def _progress(done: int) -> None:
        """Actualiza el mensaje de progreso (EDITA, no borra y crea otro).

        Borrar y volver a crear por cada tarjeta genera 20 mensajes por
        lote y es lo que hace que el usuario vea el proceso "atascado".
        """
        elapsed = int(time.monotonic() - started)
        per_card = elapsed / done if done else 0
        eta = int(per_card * (total - done))
        text = mass_processing_text(f"{AMAZON_GATE_NAME} MX", done, total)
        extra = f"\n⏱ <i>{elapsed}s transcurridos · ~{eta}s restantes</i>"
        if done < total:
            extra += "\n<i>Para detener: /amzstop</i>"
        try:
            await current_processing.edit_text(text + extra, parse_mode=ParseMode.HTML)
        except Exception:
            try:
                current_processing = await message.answer(
                    text + extra, parse_mode=ParseMode.HTML
                )
            except Exception:
                pass

    async def result_callback(index: int, result: Dict[str, str], card_data: str, uid: int) -> None:
        nonlocal processed_count, current_processing
        async with lock:
            text = _format_gate_response(card_data, result, AMAZON_GATE_NAME, command)
            try:
                sent = await message.answer(text, reply_markup=_result_markup(), parse_mode=ParseMode.HTML)
            except TelegramBadRequest:
                sent = await message.answer(
                    escape_html(text), reply_markup=_result_markup()
                )
            if result.get("status") != "✅ Live":
                asyncio.create_task(_delete_after(sent))

            processed_count += 1
            if processed_count < total:
                await _progress(processed_count)
            else:
                try:
                    await current_processing.delete()
                except Exception:
                    pass

    async def _run_batch() -> None:
        try:
            await amazon_check_multiple(lines, callback=result_callback, user_id=user_id)
        except asyncio.CancelledError:
            logger.info("Amazon mass: lote cancelado por el usuario (user_id=%s)", user_id)
            try:
                await message.answer(
                    "🛑 Lote de Amazon detenido.",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass
            raise
        except Exception:
            logger.exception("Amazon mass: error inesperado en el lote")
            try:
                await message.answer(
                    "⚠️ El lote de Amazon falló por un error interno.",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass

    task = asyncio.create_task(_run_batch())
    _mass_tasks[user_id] = task
    task.add_done_callback(lambda _t: _mass_tasks.pop(user_id, None))


@router.message(Command("amzstop"))
async def cmd_amz_stop(message: Message):
    """Detiene el lote de Amazon que esté corriendo el usuario."""
    user_id = message.from_user.id
    task = _mass_tasks.get(user_id)
    if task is None or task.done():
        await message.answer(
            "No tienes ningún lote de Amazon en curso.",
            parse_mode=ParseMode.HTML,
        )
        return
    task.cancel()
    await message.answer(
        "🛑 Deteniendo el lote de Amazon...",
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# Comando /gen (generador de tarjetas)
# ---------------------------------------------------------------------------
def _format_gen_response(
    template: str,
    month: str,
    year: str,
    cards: List[str],
    bin_info: Optional[dict],
) -> str:
    marca = bin_info.get("Marca", "Desconocido") if bin_info else "Desconocido"
    tipo = bin_info.get("Tipo de tarjeta", "Desconocido") if bin_info else "Desconocido"
    nivel = bin_info.get("Nivel de tarjeta", "Desconocido") if bin_info else "Desconocido"
    banco = bin_info.get("Banco", "Desconocido") if bin_info else "Desconocido"
    telefono = bin_info.get("Teléfono", "Desconocido") if bin_info else "Desconocido"
    pais = bin_info.get("País", "Desconocido") if bin_info else "Desconocido"

    e = _gen_emoji
    separator = "━━━━━━━━━━━━━━━━━━"

    card_lines = "\n".join(f"<code>{escape_html(card)}</code>" for card in cards)

    header = (
        f"{e('🏛️')} <b>Hades</b> {e('🏛️')}\n"
        f"{separator}\n"
        f"CC: <code>{escape_html(template)}|{month}|{year}</code>\n"
        f"{separator}\n"
        f"{card_lines}\n"
        f"{separator}"
    )

    info_block = (
        f"{e('🔥')} <b>Bin:</b> <code>{escape_html(template[:6])}</code>\n"
        f"{e('🔥')} <b>Marca:</b> {escape_html(marca)}\n"
        f"{e('🔥')} <b>Tipo de tarjeta:</b> {escape_html(tipo)}\n"
        f"{e('🔥')} <b>Nivel de tarjeta:</b> {escape_html(nivel)}\n"
        f"{e('🔥')} <b>Banco:</b> {escape_html(banco)}\n"
        f"{e('🔥')} <b>Teléfono:</b> {escape_html(telefono)}\n"
        f"{e('🔥')} <b>País:</b> {escape_html(pais)}"
    )

    footer = (
        f"{separator}\n"
        f"{e('🏛️')} <b>HADES</b> {e('🏛️')}\n"
        f"{separator}"
    )

    return f"{header}\n<blockquote>\n{info_block}\n</blockquote>\n{footer}"


@router.message(Command("gen"))
async def cmd_gen(message: Message):
    command = _parse_command(message)
    user_id = message.from_user.id

    if not await _can_use_gate(user_id, command):
        await message.answer(_denied_message(command), parse_mode=ParseMode.HTML)
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "❌ Formato incorrecto.\n\n"
            "/gen &lt;BIN&gt;|&lt;mes&gt;|&lt;año&gt;|&lt;cvv&gt;\n\n"
            "Ejemplo:\n"
            "/gen 457249651136xxxx|06|2029|xxx"
        )
        return

    raw = args[1].strip()
    parts = raw.split("|")
    if len(parts) < 3 or len(parts) > 4:
        await message.answer(
            "❌ Formato incorrecto.\n\n"
            "/gen &lt;BIN&gt;|&lt;mes&gt;|&lt;año&gt;|&lt;cvv&gt;\n\n"
            "Ejemplo:\n"
            "/gen 457249651136xxxx|06|2029|xxx"
        )
        return

    number, month, year = parts[:3]
    cvv = parts[3] if len(parts) == 4 else "rnd"

    if not number or not month or not year:
        await message.answer(
            "❌ Error: Verifica que todos los campos tengan valor.\n\n"
            "Ejemplo: /gen 457249651136xxxx|06|2029|xxx"
        )
        return

    if len(year) == 2:
        year = "20" + year

    if not validate_gen_input(month, year, cvv):
        await message.answer("❌ Entradas inválidas. Verifica el mes, año y CVV.")
        return

    try:
        cards = CardGenerator.generate_cc(number, month, year, cvv, quantity=10)
    except ValueError as exc:
        await message.answer(f"❌ Error: {exc}")
        return

    bin_info = get_card_info(number[:6])
    text = _format_gen_response(number, month, year, cards, bin_info)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Regenerar",
                    callback_data=f"regenerate|{number}|{month}|{year}|{cvv}",
                    style="success",
                )
            ],
            [
                InlineKeyboardButton(
                    text="OLIMPO BINS Referencias",
                    url="https://t.me/olimpobins",
                    style="primary",
                )
            ],
        ]
    )

    await message.answer(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


@router.callback_query(F.data.startswith("regenerate|"))
async def cb_regenerate(callback: CallbackQuery):
    """Regenera tarjetas a partir del botón Regenerar."""
    user_id = callback.from_user.id
    if not await _can_use_gate(user_id, "gen"):
        await callback.answer("No tienes acceso a /gen", show_alert=True)
        return

    parts = callback.data.split("|")
    if len(parts) != 5:
        await callback.answer("Datos de regeneración inválidos", show_alert=True)
        return

    _, number, month, year, cvv = parts
    try:
        cards = CardGenerator.generate_cc(number, month, year, cvv, quantity=10)
    except ValueError as exc:
        await callback.answer(f"Error: {exc}", show_alert=True)
        return

    bin_info = get_card_info(number[:6])
    text = _format_gen_response(number, month, year, cards, bin_info)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Regenerar",
                    callback_data=f"regenerate|{number}|{month}|{year}|{cvv}",
                    style="success",
                )
            ],
            [
                InlineKeyboardButton(
                    text="OLIMPO BINS Referencias",
                    url="https://t.me/olimpobins",
                    style="primary",
                )
            ],
        ]
    )

    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)

    await callback.answer()


@router.callback_query(F.data.startswith("gate_"))
async def cb_gate_button(callback: CallbackQuery):
    """Maneja los botones del menú Gates."""
    command = callback.data.replace("gate_", "")

    if command == "gen":
        text = (
            "🛠️ <b>/gen</b> — Generador de tarjetas\n\n"
            "<blockquote>\n"
            "Formato: <code>/gen BIN|mes|año|cvv</code>\n"
            "Máximo: 10 tarjetas\n"
            "</blockquote>\n\n"
            "Ejemplo:\n"
            "<code>/gen 457249651136xxxx|06|2029|xxx</code>"
        )
        await callback.message.answer(text, parse_mode=ParseMode.HTML)
        await callback.answer()
        return

    is_mass = command.endswith("mass")
    base = command.replace("mass", "")
    gate_name = GATE_COMMANDS.get(base, "")

    if is_mass:
        text = (
            f"{GATE_EMOJIS.get(gate_name, '🔥')} <b>/{command}</b> — {gate_name} mass\n\n"
            "Responde a un mensaje con las tarjetas o escríbelas a continuación.\n"
            "<blockquote>\n"
            "Formato: <code>cc|mes|año|cvv</code>\n"
            "Límite: 10 tarjetas\n"
            "Cooldown: 20 segundos\n"
            "</blockquote>\n\n"
            f"Ejemplo:\n<code>/{command}</code>\n"
            "<code>4532640527811643|12|2025|123</code>"
        )
    else:
        text = (
            f"{GATE_EMOJIS.get(gate_name, '🔥')} <b>/{command}</b> — {gate_name}\n\n"
            "<blockquote>\n"
            "Formato: <code>cc|mes|año|cvv</code>\n"
            "Cooldown: 7 segundos\n"
            "</blockquote>\n\n"
            f"Ejemplo:\n<code>/{command} 4532640527811643|12|2025|123</code>"
        )

    await callback.message.answer(text, parse_mode=ParseMode.HTML)
    await callback.answer()


# ---------------------------------------------------------------------------
# Verificacion de cookie de Amazon
# ---------------------------------------------------------------------------
_EXPORT_COOLDOWN_S = 120
_export_last: Dict[int, datetime] = {}


def _cookie_export_keyboard() -> InlineKeyboardMarkup:
    """Teclado del mensaje de /checkcookies: botón Export en azul."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📤 Export",
                    callback_data="cookie_export",
                    style="primary",
                )
            ]
        ]
    )


@router.message(Command("checkcookies", "checkcookie"))
async def cmd_check_cookie(message: Message):
    """Verifica que la cookie de Amazon guardada sea valida."""
    user_id = message.from_user.id
    processing = await message.answer("⏳ Verificando cookie de Amazon...")
    result = await amazon_check_cookie(user_id)
    classification = result.get("classification", "unknown")
    recommendation = ""
    if classification == "valid":
        recommendation = "La cookie esta lista para usar."
    elif classification == "expired":
        recommendation = "Genera una nueva cookie con /gencookie."
    elif classification in ("verification_required", "captcha"):
        recommendation = "La cookie o la IP estan siendo cuestionadas. Prueba con /gencookie o cambia de proxy."
    elif classification == "suspended":
        recommendation = "La cuenta fue suspendida. Genera una nueva cookie con /gencookie."
    else:
        recommendation = "Revisa los logs en data/amazon_debug/verify_cookie_*.txt"

    text = (
        f"{_gate_emoji('🛒')} <b>Check Cookie Amazon</b>\n\n"
        f"Estado: <b>{result['status']}</b>\n"
        f"Diagnostico: <code>{classification}</code>\n\n"
        f"{result['message']}\n\n"
        f"<i>{recommendation}</i>"
    )
    try:
        await processing.edit_text(text, reply_markup=_cookie_export_keyboard(), parse_mode=ParseMode.HTML)
    except TelegramBadRequest:
        await message.answer(text, reply_markup=_cookie_export_keyboard(), parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "cookie_export")
async def cb_cookie_export(callback: CallbackQuery, state: FSMContext):
    """Botón Export: refresca en Amazon y muestra las cookies nuevas.

    No guarda nada: deja las cookies en el panel igual que en el flujo
    de /gencookie y el usuario decide si las guarda con el botón
    "Guardar cookies". Lo guardado antes sigue intacto.

    Flujo anti-suspensión: responde el callback de inmediato, aplica un
    cooldown de 2 minutos por usuario, hace UN solo refresco ligero
    (ver amazon_refresh_cookie) y nunca reintenta en bucle.
    """
    user_id = callback.from_user.id
    now = datetime.now()
    last = _export_last.get(user_id)
    if last and (now - last).total_seconds() < _EXPORT_COOLDOWN_S:
        wait = int(_EXPORT_COOLDOWN_S - (now - last).total_seconds())
        await callback.answer(
            f"Espera {wait}s antes de exportar de nuevo. Refrescar muy seguido puede marcar tu cuenta.",
            show_alert=True,
        )
        return
    _export_last[user_id] = now
    await callback.answer("⏳ Refrescando cookies en Amazon...")
    try:
        await callback.message.edit_text(
            "⏳ Refrescando cookies en Amazon...\n<i>Una sola consulta ligera, sin reintentos.</i>",
            parse_mode=ParseMode.HTML,
        )
    except TelegramBadRequest:
        pass

    result = await amazon_refresh_cookie(user_id)
    classification = result.get("classification", "unknown")
    if classification == "valid":
        cookies = result.get("cookie") or ""
        await state.update_data(cookies=cookies)
        if result.get("refreshed"):
            try:
                await delete_amazon_cookie(user_id)
                cleared_note = "Las guardadas se eliminaron de la base.\nGuarda estas para activarlas."
            except Exception:
                logger.exception("Export: no se pudieron eliminar las cookies guardadas")
                cleared_note = "No se pudieron eliminar las guardadas; al presionar Guardar se reemplazan."
        else:
            cleared_note = "Amazon no devolvió cambios, las guardadas siguen vigentes."
        text = (
            f"{_gate_emoji('🛒')} <b>Cookies exportadas</b>\n\n"
            "<blockquote>\n"
            "Estado: <b>✅ Live</b>\n"
            f"{cleared_note}\n"
            "</blockquote>\n\n"
            "Cookies:\n"
            f"<pre>{cookies}</pre>"
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="💾 Guardar cookies", callback_data="gen_save_cookies")]
            ]
        )
        try:
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        except TelegramBadRequest:
            await callback.message.answer(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        return

    if classification == "expired":
        head = "❌ <b>Tus cookies expiraron</b>\n\nGenera una cuenta nueva con /gencookie."
    elif classification == "suspended":
        head = "🚫 <b>Cuenta en revisión</b>\n\nNo se tocó lo guardado. Espera unas horas antes de intentar de nuevo."
    elif classification in ("verification_required", "captcha"):
        head = "🔒 <b>Amazon pidió verificación</b>\n\nNo se tocó lo guardado. Espera y evita refrescar seguido."
    elif classification == "missing":
        head = "⚠️ <b>Sin cookies</b>"
    else:
        head = "⚠️ <b>No se pudo refrescar</b>"

    text = (
        f"{_gate_emoji('🛒')} <b>Export Cookies Amazon</b>\n\n"
        f"{head}\n\n"
        f"{result.get('message', '')}"
    )
    try:
        await callback.message.edit_text(text, reply_markup=_cookie_export_keyboard(), parse_mode=ParseMode.HTML)
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=_cookie_export_keyboard(), parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# Guardado de cookie de Amazon
# ---------------------------------------------------------------------------
@router.message(Command("save"))
async def cmd_save_cookie(message: Message):
    """Guarda la cookie de Amazon del usuario, pero solo si está viva.

    Antes de guardar se VALIDA la cookie contra Amazon (por el proxy
    residencial del gate): una sesión muerta, una cuenta suspendida o una
    cuenta en revisión no sirven para /amz, así que se rechazan con el
    motivo en vez de guardarlas y dejar que el gate falle después.
    """
    user_id = message.from_user.id

    cookie: Optional[str] = None
    if message.reply_to_message and message.reply_to_message.text:
        cookie = message.reply_to_message.text.strip()
    else:
        args = message.text.split(maxsplit=1)
        if len(args) > 1:
            cookie = args[1].strip()

    if not cookie:
        await message.answer(
            "❌ No recibí ninguna cookie.\n\n"
            "Formatos:\n"
            "<code>/save cookie_de_amazon</code>\n"
            "o responde <code>/save</code> a un mensaje que contenga la cookie.",
            parse_mode=ParseMode.HTML,
        )
        return

    # Borrar los mensajes con la cookie ANTES de validar (no debe quedar
    # visible mientras se hace la consulta).
    try:
        await message.delete()
    except Exception:
        pass
    if message.reply_to_message:
        try:
            await message.reply_to_message.delete()
        except Exception:
            pass

    processing = await message.answer("⏳ Validando la cookie en Amazon...")
    validation = await validate_amazon_cookie(cookie)

    if validation.get("classification") != "valid":
        text = (
            f"{_gate_emoji('🛒')} <b>No guardé la cookie</b>\n\n"
            f"Estado: <b>{validation.get('status', '⚠️ Error')}</b>\n"
            f"Diagnostico: <code>{validation.get('classification', 'unknown')}</code>\n\n"
            f"{validation.get('message', '')}\n\n"
            f"<i>{validation.get('advice', '')}</i>"
        )
        try:
            await processing.edit_text(text, parse_mode=ParseMode.HTML)
        except TelegramBadRequest:
            await message.answer(text, parse_mode=ParseMode.HTML)
        return

    await save_amazon_cookie(user_id, cookie)

    confirm = await message.answer(
        "✅ Cookie de Amazon guardada y validada.\n\n"
        "<blockquote>\n"
        "Estado: <b>✅ Live</b> (sesión activa)\n"
        f"{validation.get('message', '')}\n"
        "</blockquote>\n\n"
        "Valida tus cookies con el comando:\n"
        "<code>/checkcookies</code> — Validar tus cookies",
        parse_mode=ParseMode.HTML,
    )
    asyncio.create_task(_delete_after(confirm, delay=10))


def register_gate_handlers(dp):
    register_hiperion_handlers(dp)
    dp.include_router(router)
