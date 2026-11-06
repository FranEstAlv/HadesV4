"""Handler del gate Medusa PayFlow Pro CCN Charged ($11.39 USD ~ $215 MXN) — Vedanta ShopSite.

Comandos:
    /md      <cc>|<mes>|<año>|<cvv>   -> verifica 1 tarjeta
    /mdmass  (responde a un mensaje)  -> verifica un lote (max 10)

from bot.utils.cleanup import schedule_delete
El veredicto real lo da el backend ShopSite (PayPal server-side): el bloque
"Payment Processing Error ... Detailed error message:" del billing.cgi final.
Ver bloque Vedanta de la skill atenea-gateway.

Todos los errores al usuario usan un solo mensaje sanitizado:
"Error. Gate reportado". El detalle real queda en el log del servidor.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from datetime import datetime
from typing import Dict, List

from aiogram import Router
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest

from bot.gates.medusa import (
    USER_ERROR_MSG,
    medusa_gate_check,
    medusa_gate_check_multiple,
)
from bot.services.memberships import get_membership, is_membership_active
from bot.services.admins import is_admin
from bot.services.credits import deduct_credits
from bot.config import MASS_CREDIT_COST
from bot.utils.cleanup import schedule_delete
from bot.utils.bin_lookup import get_card_info
from bot.utils.formatting import (
    dead_emoji,
    escape_html,
    mass_processing_text,
    processing_text,
)

router = Router()
logger = logging.getLogger(__name__)

MEDUSA_GATE_NAME = "Medusa PayFlow Pro Charged"
MEDUSA_PLANS = {"olimpo", "mensual"}

MEDUSA_SINGLE_COOLDOWN = 20
MEDUSA_MASS_COOLDOWN = 30
MEDUSA_MASS_LIMIT = 10
MEDUSA_MASS_CREDIT_COST = MASS_CREDIT_COST  # 5, gratis para admins y Olimpo

# Un solo lote Medusa a la vez por usuario: un segundo /mdmass se encimaría
# sobre el anterior (cada tarjeta tarda decenas de segundos por el captcha)
# y duplica la carga sobre la misma IP/proxy. Igual que /dimass y /amss.
_mass_tasks: Dict[int, "asyncio.Task"] = {}

_GATE_CUSTOM_EMOJI_ID = "5454074580010295588"
_LIVE_CUSTOM_EMOJI_ID = "5021905410089550576"

last_md_single: Dict[int, datetime] = {}
last_md_mass: Dict[int, datetime] = {}


def _dead_status() -> str:
    """Status Dead con el emoji personalizado."""
    return f"{dead_emoji()} Dead"


def _gate_emoji(fallback: str) -> str:
    return f'<tg-emoji emoji-id="{_GATE_CUSTOM_EMOJI_ID}">{fallback}</tg-emoji>'


async def _can_use_md(user_id: int) -> bool:
    if await is_admin(user_id):
        return True
    membership = await get_membership(user_id)
    if not is_membership_active(membership):
        return False
    return membership.get("plan", "").lower() in MEDUSA_PLANS


async def _is_olimpo_or_admin(user_id: int) -> bool:
    if await is_admin(user_id):
        return True
    membership = await get_membership(user_id)
    if not is_membership_active(membership):
        return False
    return membership.get("plan", "").lower() == "olimpo"


def _denied_message(command: str) -> str:
    planes = ", ".join(sorted(MEDUSA_PLANS))
    return (
        "❌ <b>Acceso denegado</b>\n\n"
        f"El comando /<b>{escape_html(command)}</b> requiere uno de estos planes:\n"
        f"<blockquote>{planes}</blockquote>\n\n"
        "Contacta a un administrador para adquirir acceso."
    )


def _get_bin_info(cc: str) -> Dict[str, str]:
    info = get_card_info(cc[:6]) if cc else None
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


def _format_md_response(
    card_data: str,
    result: Dict[str, str],
    command: str,
) -> str:
    status_raw = result.get("status", "⚠️ Error")

    card_parts = card_data.split("|")
    bin_info = _get_bin_info(card_parts[0]) if len(card_parts) == 4 else _get_bin_info("")

    e = _gate_emoji
    sep = "━━━━━━━━━━━━━━━━━━"
    card_code = f"<code>{escape_html(card_data)}</code>"

    if status_raw == "✅ Live":
        status_icon = f'<tg-emoji emoji-id="{_LIVE_CUSTOM_EMOJI_ID}">✅</tg-emoji> Live'
        lines = [
            f"{e('💎')} Status: {status_icon}",
            f"{e('💎')} Response: {escape_html(result.get('message', ''))}",
            f"{e('💎')} Resultado: APROBADA",
            f"{e('💎')} Error: NINGUNO",
        ]
    elif status_raw == "❌ Dead":
        status_icon = _dead_status()
        lines = [
            f"{e('💎')} Status: {status_icon}",
            f"{e('💎')} Response: {escape_html(result.get('message', ''))}",
            f"{e('💎')} Resultado: DECLINADA",
            f"{e('💎')} Error: NINGUNO",
        ]
    else:
        lines = [
            f"{e('💎')} Status: ⚠️ Error",
            f"{e('💎')} Response: {escape_html(USER_ERROR_MSG)}",
            f"{e('💎')} Resultado: Error",
            f"{e('💎')} Error: {escape_html(USER_ERROR_MSG)}",
        ]

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
        f"{e('💎')} {MEDUSA_GATE_NAME}\n"
        f"{e('💎')} Comando: /{command}\n"
        f"{sep}\n"
        f"{e('💎')} {card_code}\n"
        + "\n".join(lines) + "\n"
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


def _extract_cards_from_text(text: str | None) -> List[str]:
    """Extrae tarjetas cc|mes|año|cvv de un bloque de texto."""
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


async def _run_gate(card_data: str, user_id: int) -> Dict[str, str]:
    """Ejecuta el gate con un reintento ante error transitorio."""
    started = time.monotonic()
    result = await medusa_gate_check(card_data, user_id)
    if result.get("status") == "⚠️ Error":
        logger.warning("Medusa reintento por error en unitario")
        result = await medusa_gate_check(card_data, user_id)
    # Sanitizar: al usuario solo llega el mensaje unico de error.
    if result.get("status") == "⚠️ Error":
        result = {**result, "message": USER_ERROR_MSG}
    result["elapsed"] = time.monotonic() - started

    cc = card_data.split("|")[0]
    masked = f"{cc[:6]}******{cc[-4:]}" if len(cc) > 10 else "****"
    log = logger.warning if result["status"] == "⚠️ Error" else logger.info
    log("Medusa %s -> %s en %.2fs", masked, result["status"], result["elapsed"])
    return result


@router.message(Command("md"))
async def cmd_md_single(message: Message):
    """Verifica una tarjeta contra Medusa (Vedanta ShopSite)."""
    user_id = message.from_user.id
    if not await _can_use_md(user_id):
        await message.answer(_denied_message("md"), parse_mode=ParseMode.HTML)
        return

    now = datetime.now()
    last = last_md_single.get(user_id)
    if last and (now - last).total_seconds() < MEDUSA_SINGLE_COOLDOWN:
        remaining = MEDUSA_SINGLE_COOLDOWN - (now - last).total_seconds()
        await message.answer(
            f"⏳ Debes esperar <b>{remaining:.1f}</b> segundos antes de volver a usar /md.",
            parse_mode=ParseMode.HTML,
        )
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "❌ Formato incorrecto.\n\n"
            "/md &lt;cc&gt;|&lt;mes&gt;|&lt;año&gt;|&lt;cvv&gt;\n\n"
            "Ejemplo:\n"
            "/md 4347697133726676|04|2031|431",
            parse_mode=ParseMode.HTML,
        )
        return

    card_data = args[1].strip()
    if len(card_data.split("|")) != 4:
        await message.answer(
            "❌ Formato de tarjeta incorrecto. Usa: &lt;cc&gt;|&lt;mes&gt;|&lt;año&gt;|&lt;cvv&gt;",
            parse_mode=ParseMode.HTML,
        )
        return

    processing = await message.answer(
        processing_text(MEDUSA_GATE_NAME), parse_mode=ParseMode.HTML
    )

    try:
        result = await _run_gate(card_data, user_id)
    except Exception:
        logger.exception("Error en gate Medusa")
        result = {
            "status": "⚠️ Error",
            "classification": "ERROR",
            "message": USER_ERROR_MSG,
            "code": "",
            "elapsed": 0.0,
        }
    finally:
        last_md_single[user_id] = datetime.now()

    text = _format_md_response(card_data, result, "md")
    try:
        result_msg = await processing.edit_text(
            text, reply_markup=_result_markup(), parse_mode=ParseMode.HTML
        )
    except TelegramBadRequest:
        result_msg = await message.answer(
            text, reply_markup=_result_markup(), parse_mode=ParseMode.HTML
        )

    if result.get("status") != "✅ Live":
        schedule_delete(result_msg)


@router.message(Command("mdmass"))
async def cmd_md_mass(message: Message):
    """Verifica un lote de tarjetas contra Medusa (máximo 10)."""
    user_id = message.from_user.id
    if not await _can_use_md(user_id):
        await message.answer(_denied_message("mdmass"), parse_mode=ParseMode.HTML)
        return

    now = datetime.now()
    last = last_md_mass.get(user_id)
    if last and (now - last).total_seconds() < MEDUSA_MASS_COOLDOWN:
        remaining = MEDUSA_MASS_COOLDOWN - (now - last).total_seconds()
        await message.answer(
            f"⏳ Debes esperar <b>{remaining:.1f}</b> segundos antes de volver a usar /mdmass.",
            parse_mode=ParseMode.HTML,
        )
        return

    # Un solo lote Medusa a la vez por usuario: un segundo /mdmass se
    # encimaría sobre el anterior (cada tarjeta tarda decenas de segundos
    # por el captcha) y duplica la carga sobre la misma IP/proxy.
    # Igual que /dimass y /amss.
    running = _mass_tasks.get(user_id)
    if running is not None and not running.done():
        await message.answer(
            "⏳ Ya tienes un lote en curso. Espera a que termine antes de lanzar otro.",
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
            "Responde al mensaje con las tarjetas con /mdmass o escríbelas a continuación.\n\n"
            "Formato aceptado: 15 o 16 dígitos|mes|año|cvv\n"
            "Ejemplo VISA: 4347697133726676|04|2031|431",
            parse_mode=ParseMode.HTML,
        )
        return

    lines = lines[:MEDUSA_MASS_LIMIT]
    total = len(lines)

    # Costo de mass: 5 créditos, excepto para admins y miembros olimpo
    if not await _is_olimpo_or_admin(user_id):
        success, new_balance = await deduct_credits(
            user_id, MEDUSA_MASS_CREDIT_COST, "Uso /mdmass"
        )
        if not success:
            await message.answer(
                f"❌ Los comandos masivos cuestan <b>{MEDUSA_MASS_CREDIT_COST}</b> créditos.\n"
                f"Tu saldo actual es <b>{new_balance}</b>.\n\n"
                "Adquiere créditos o contrata el plan Olimpo para usarlos gratis e ilimitados.",
                parse_mode=ParseMode.HTML,
            )
            return

    processing = await message.answer(
        mass_processing_text(MEDUSA_GATE_NAME, 0, total), parse_mode=ParseMode.HTML
    )

    # Cooldown: se registra al TERMINAR el lote, no al iniciarlo. Igual que
    # /dimass y /amss: antes se guardaba aquí y un usuario podía lanzar otro
    # /mdmass a los 30s cuando el anterior seguía corriendo (cada tarjeta
    # tarda decenas de segundos por el captcha) y los dos lotes se encimaban.
    last_md_mass[user_id] = datetime.now()

    lock = asyncio.Lock()
    processed_count = 0
    current_processing = processing
    started = time.monotonic()

    async def _progress(done: int) -> None:
        """Avanza el mensaje de progreso igual que /phmass.

        Se manda un mensaje nuevo después del último resultado y se borra
        el anterior, para que el avance no se quede fijo al inicio del lote.
        """
        nonlocal current_processing
        try:
            new_msg = await message.answer(
                mass_processing_text(MEDUSA_GATE_NAME, done, total),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            new_msg = current_processing
        try:
            await current_processing.delete()
        except Exception:
            pass
        current_processing = new_msg

    async def result_callback(
        index: int, result: Dict[str, str], card_data: str, uid: int
    ) -> None:
        nonlocal processed_count, current_processing
        async with lock:
            if result.get("status") == "⚠️ Error":
                result = {**result, "message": USER_ERROR_MSG}
            text = _format_md_response(card_data, result, "mdmass")
            try:
                sent = await message.answer(
                    text, reply_markup=_result_markup(), parse_mode=ParseMode.HTML
                )
            except TelegramBadRequest:
                sent = await message.answer(
                    escape_html(text), reply_markup=_result_markup()
                )
            if result.get("status") != "✅ Live":
                schedule_delete(sent)

            processed_count += 1
            if processed_count < total:
                await _progress(processed_count)
            else:
                try:
                    await current_processing.delete()
                except Exception:
                    pass
                # Lote terminado: aquí empieza a contar el cooldown.
                last_md_mass[uid] = datetime.now()

    async def _run_batch() -> None:
        try:
            await medusa_gate_check_multiple(
                lines, callback=result_callback, user_id=user_id
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Medusa mass: error inesperado en el lote")
            try:
                await message.answer(
                    f"⚠️ {escape_html(USER_ERROR_MSG)}",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass

    task = asyncio.create_task(_run_batch())
    _mass_tasks[user_id] = task
    task.add_done_callback(lambda _t: _mass_tasks.pop(user_id, None))


def register_md_handlers(dp):
    dp.include_router(router)
