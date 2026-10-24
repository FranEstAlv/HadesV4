"""Handler del gate Radamantis ($1 MXN) — Krispy Kreme MX.

Comandos:
    /rd      <cc>|<mes>|<año>|<cvv>   -> verifica 1 tarjeta
    /rdmass  (responde a un mensaje)  -> verifica un lote (max 10)

El veredicto real lo da el gate radamanthysV2 (Krispy Kreme MX): signup con
correo, token de Stripe y Payment/Card/Add. El token de Stripe solo pasa el
filtro de formato. Ver bloque KrispyKreme de la skill atenea-gateway.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from datetime import datetime
from typing import Dict, List, Optional

from aiogram import Router
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest

from bot.gates.radamanthysV2 import gate_krispy_check
from bot.gates.checker import _pick_proxy
from bot.services.memberships import get_membership, is_membership_active
from bot.services.admins import is_admin
from bot.services.credits import deduct_credits
from bot.utils.bin_lookup import get_card_info
from bot.utils.formatting import (
    dead_emoji,
    escape_html,
    mass_processing_text,
    processing_text,
)

router = Router()
logger = logging.getLogger(__name__)

RADAMANTIS_GATE_NAME = "Radamanthys CCN AUTH"
RADAMANTIS_PLANS = {"olimpo", "mensual"}

RADAMANTIS_SINGLE_COOLDOWN = 20
RADAMANTIS_MASS_COOLDOWN = 30
RADAMANTIS_MASS_LIMIT = 10
RADAMANTIS_MASS_CREDIT_COST = 5

_GATE_CUSTOM_EMOJI_ID = "5454074580010295588"
_LIVE_CUSTOM_EMOJI_ID = "5021905410089550576"

last_rd_single: Dict[int, datetime] = {}
last_rd_mass: Dict[int, datetime] = {}


def _dead_status() -> str:
    """Status Dead con el emoji personalizado."""
    return f"{dead_emoji()} Dead"


def _gate_emoji(fallback: str) -> str:
    return f'<tg-emoji emoji-id="{_GATE_CUSTOM_EMOJI_ID}">{fallback}</tg-emoji>'


async def _can_use_rd(user_id: int) -> bool:
    if await is_admin(user_id):
        return True
    membership = await get_membership(user_id)
    if not is_membership_active(membership):
        return False
    return membership.get("plan", "").lower() in RADAMANTIS_PLANS


async def _is_olimpo_or_admin(user_id: int) -> bool:
    if await is_admin(user_id):
        return True
    membership = await get_membership(user_id)
    if not is_membership_active(membership):
        return False
    return membership.get("plan", "").lower() == "olimpo"


def _denied_message(command: str) -> str:
    planes = ", ".join(sorted(RADAMANTIS_PLANS))
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


def _format_rd_response(
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
            f"{e('💎')} Response: Approved $1 MNX",
            f"{e('💎')} Resultado: APROBADA",
            f"{e('💎')} Error: NINGUNO",
        ]
    elif status_raw == "❌ Dead":
        status_icon = _dead_status()
        lines = [
            f"{e('💎')} Status: {status_icon}",
            f"{e('💎')} Response: Dead",
            f"{e('💎')} Resultado: DECLINADA",
            f"{e('💎')} Error: NINGUNO",
        ]
    else:
        lines = [
            f"{e('💎')} Status: ⚠️ Error",
            f"{e('💎')} Response: Avisar ADM",
            "🏷️ Codigo: Error",
            f"{e('💎')} Resultado: Error",
            f"{e('💎')} Error: Error",
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
        f"{e('💎')} {RADAMANTIS_GATE_NAME}\n"
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


async def _delete_after(message: Message, delay: int = 30) -> None:
    try:
        await asyncio.sleep(delay)
        await message.delete()
    except Exception:
        pass


def _extract_cards_from_text(text: Optional[str]) -> List[str]:
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


def _normalize_gate_result(result: Dict[str, str], elapsed: float) -> Dict[str, str]:
    """Adapta la respuesta de radamanthysV2 al formato que ya usa Hades."""
    status = result.get("status", "⚠️ Error")
    if status == "✅ Live":
        classification = "APROBADA"
    elif status == "❌ Dead":
        classification = "DECLINADA"
    else:
        classification = "ERROR"
    return {
        "status": status,
        "message": result.get("message", ""),
        "classification": classification,
        "code": "",
        "elapsed": elapsed,
    }


async def _run_gate(card_data: str, user_id: int) -> Dict[str, str]:
    """Ejecuta el gate con proxy MX y un reintento ante error transitorio."""
    started = time.monotonic()
    raw = await gate_krispy_check(
        card_data, user_id, shared_proxies=_pick_proxy("mx")
    )
    if raw.get("status") == "⚠️ Error":
        logger.warning(
            "Radamanthys reintento por error: %s", raw.get("message", "")
        )
        raw = await gate_krispy_check(
            card_data, user_id, shared_proxies=_pick_proxy("mx")
        )
    result = _normalize_gate_result(raw, time.monotonic() - started)

    cc = card_data.split("|")[0]
    masked = f"{cc[:6]}******{cc[-4:]}" if len(cc) > 10 else "****"
    log = logger.warning if result["status"] == "⚠️ Error" else logger.info
    log("Radamanthys %s -> %s en %.2fs | %s", masked, result["status"],
        result["elapsed"], (result.get("message") or "")[:100])
    return result


@router.message(Command("rd"))
async def cmd_rd_single(message: Message):
    """Verifica una tarjeta contra Radamantis (Krispy Kreme MX)."""
    user_id = message.from_user.id
    if not await _can_use_rd(user_id):
        await message.answer(_denied_message("rd"), parse_mode=ParseMode.HTML)
        return

    now = datetime.now()
    last = last_rd_single.get(user_id)
    if last and (now - last).total_seconds() < RADAMANTIS_SINGLE_COOLDOWN:
        remaining = RADAMANTIS_SINGLE_COOLDOWN - (now - last).total_seconds()
        await message.answer(
            f"⏳ Debes esperar <b>{remaining:.1f}</b> segundos antes de volver a usar /rd.",
            parse_mode=ParseMode.HTML,
        )
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "❌ Formato incorrecto.\n\n"
            "/rd &lt;cc&gt;|&lt;mes&gt;|&lt;año&gt;|&lt;cvv&gt;\n\n"
            "Ejemplo:\n"
            "/rd 4532640527811643|12|2028|123",
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
        processing_text(RADAMANTIS_GATE_NAME), parse_mode=ParseMode.HTML
    )

    try:
        result = await _run_gate(card_data, user_id)
    except Exception:
        logger.exception("Error en gate Radamantis")
        result = {
            "status": "⚠️ Error",
            "classification": "ERROR TOOL",
            "message": "Error interno del gate, intenta de nuevo",
            "code": "",
            "elapsed": 0.0,
        }
    finally:
        last_rd_single[user_id] = datetime.now()

    text = _format_rd_response(card_data, result, "rd")
    try:
        result_msg = await processing.edit_text(
            text, reply_markup=_result_markup(), parse_mode=ParseMode.HTML
        )
    except TelegramBadRequest:
        result_msg = await message.answer(
            text, reply_markup=_result_markup(), parse_mode=ParseMode.HTML
        )

    if result.get("status") != "✅ Live":
        asyncio.create_task(_delete_after(result_msg))


@router.message(Command("rdmass"))
async def cmd_rd_mass(message: Message):
    """Verifica un lote de tarjetas contra Radamantis (máximo 10)."""
    user_id = message.from_user.id
    if not await _can_use_rd(user_id):
        await message.answer(_denied_message("rdmass"), parse_mode=ParseMode.HTML)
        return

    now = datetime.now()
    last = last_rd_mass.get(user_id)
    if last and (now - last).total_seconds() < RADAMANTIS_MASS_COOLDOWN:
        remaining = RADAMANTIS_MASS_COOLDOWN - (now - last).total_seconds()
        await message.answer(
            f"⏳ Debes esperar <b>{remaining:.1f}</b> segundos antes de volver a usar /rdmass.",
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
            "Responde al mensaje con las tarjetas con /rdmass o escríbelas a continuación.\n\n"
            "Formato aceptado: 15 o 16 dígitos|mes|año|cvv\n"
            "Ejemplo AMEX: 377713996217025|10|2026|0000\n"
            "Ejemplo VISA: 4532640527811643|12|2028|123",
            parse_mode=ParseMode.HTML,
        )
        return

    lines = lines[:RADAMANTIS_MASS_LIMIT]
    total = len(lines)

    # Costo de mass: 5 créditos, excepto para admins y miembros olimpo
    if not await _is_olimpo_or_admin(user_id):
        success, new_balance = await deduct_credits(
            user_id, RADAMANTIS_MASS_CREDIT_COST, "Uso /rdmass"
        )
        if not success:
            await message.answer(
                f"❌ Los comandos masivos cuestan <b>{RADAMANTIS_MASS_CREDIT_COST}</b> créditos.\n"
                f"Tu saldo actual es <b>{new_balance}</b>.\n\n"
                "Adquiere créditos o contrata el plan Olimpo para usarlos gratis e ilimitados.",
                parse_mode=ParseMode.HTML,
            )
            return

    processing = await message.answer(
        mass_processing_text(RADAMANTIS_GATE_NAME, 0, total), parse_mode=ParseMode.HTML
    )

    last_rd_mass[user_id] = datetime.now()

    lock = asyncio.Lock()
    processed_count = 0
    current_processing = processing
    started = time.monotonic()

    async def _progress(done: int) -> None:
        nonlocal current_processing
        elapsed = int(time.monotonic() - started)
        per_card = elapsed / done if done else 0
        eta = int(per_card * (total - done))
        text = mass_processing_text(RADAMANTIS_GATE_NAME, done, total)
        extra = f"\n⏱ <i>{elapsed}s transcurridos · ~{eta}s restantes</i>"
        # Igual que /ph, /eu, /wo y /di: se manda un mensaje nuevo después
        # del último resultado y se borra el anterior, para que el avance
        # no se quede fijo al inicio del lote.
        try:
            new_msg = await message.answer(
                text + extra, parse_mode=ParseMode.HTML
            )
        except Exception:
            try:
                await current_processing.edit_text(
                    text + extra, parse_mode=ParseMode.HTML
                )
            except Exception:
                pass
            return
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
            text = _format_rd_response(card_data, result, "rdmass")
            try:
                sent = await message.answer(
                    text, reply_markup=_result_markup(), parse_mode=ParseMode.HTML
                )
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

    try:
        for index, card in enumerate(lines):
            started_card = time.monotonic()
            try:
                result = await _run_gate(card, user_id)
            except Exception:
                logger.exception("Radamantis mass: error en una tarjeta")
                result = _normalize_gate_result(
                    {"status": "⚠️ Error",
                     "message": "Error interno del gate, intenta de nuevo"},
                    time.monotonic() - started_card,
                )
            await result_callback(index, result, card, user_id)
            if index < len(lines) - 1:
                await asyncio.sleep(2.5 + random.random() * 1.5)
    except Exception:
        logger.exception("Radamantis mass: error inesperado en el lote")
        try:
            await message.answer(
                "⚠️ No se pudo completar el lote. Avisa a un administrador.",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass


def register_rd_handlers(dp):
    dp.include_router(router)
