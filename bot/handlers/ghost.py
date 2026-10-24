"""Handlers de GHOST PayPal Guest: /gh (unitario) y /ghmass (masivo, máx 10).

Backend en bot/gates/paypal_guest.py (semáforo global: 3 checks a la vez).
Respuesta con la plantilla Hades igual que el resto de gates.
"""
from __future__ import annotations

import asyncio
import time
from typing import Dict

from aiogram import Router
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest

from bot.gates.paypal_guest import ghost_check
from bot.services.memberships import get_membership, is_membership_active
from bot.services.admins import is_admin
from bot.utils.bin_lookup import get_card_info
from bot.utils.formatting import (
    dead_emoji,
    escape_html,
    processing_text,
)

router = Router()
import logging

logger = logging.getLogger(__name__)

GHOST_GATE_NAME = "Ghost"
GHOST_SINGLE_COOLDOWN = 15
GHOST_PLANS = {"olimpo", "mensual"}

_SEARCH_CUSTOM_EMOJI_ID = "5017088445353296841"
_DEAD_CUSTOM_EMOJI_ID = "5019523782004441717"


def _dead_status() -> str:
    """Status Dead con el emoji personalizado pedido."""
    return f"{dead_emoji()} Dead"


_GATE_CUSTOM_EMOJI_ID = "5454074580010295588"
_LIVE_CUSTOM_EMOJI_ID = "5021905410089550576"

last_ghost_single: Dict[int, float] = {}


def _gate_emoji(fallback: str) -> str:
    return f'<tg-emoji emoji-id="{_GATE_CUSTOM_EMOJI_ID}">{fallback}</tg-emoji>'


async def _can_use_ghost(user_id: int) -> bool:
    if await is_admin(user_id):
        return True
    membership = await get_membership(user_id)
    if not is_membership_active(membership):
        return False
    return membership.get("plan", "").lower() in GHOST_PLANS


def _denied_message(command: str) -> str:
    planes = ", ".join(sorted(GHOST_PLANS))
    return (
        "❌ <b>Acceso denegado</b>\n\n"
        f"El comando /<b>{escape_html(command)}</b> requiere uno de estos planes:\n"
        f"<blockquote>{planes}</blockquote>\n\n"
        "Contacta a un administrador para adquirir acceso."
    )


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


def _format_ghost_response(card_data: str, result: Dict[str, str], command: str) -> str:
    card_parts = card_data.split("|")
    bin_info = _get_bin_info(card_parts[0]) if len(card_parts) == 4 else _get_bin_info("")

    status_raw = result.get("status", "⚠️ Error")
    if status_raw == "✅ Live":
        status_icon = f'<tg-emoji emoji-id="{_LIVE_CUSTOM_EMOJI_ID}">✅</tg-emoji> Live'
        resultado = "APROBADA"
        response_text = result.get("message", "")
    elif status_raw == "❌ Dead":
        status_icon = _dead_status()
        resultado = "DECLINADA"
        response_text = result.get("message", "")
    else:
        status_icon = status_raw
        resultado = "ERROR"
        response_text = "error"

    e = _gate_emoji
    sep = "━━━━━━━━━━━━━━━━━━"
    card_code = f"<code>{escape_html(card_data)}</code>"

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
        f"{e('💎')} {GHOST_GATE_NAME} Check\n"
        f"{e('💎')} Comando: /{command}\n"
        f"{sep}\n"
        f"{e('💎')} {card_code}\n"
        f"{e('💎')} Status: {status_icon}\n"
        f"{e('💎')} Response: {escape_html(response_text)}\n"
        f"{e('💎')} Resultado: {resultado}\n"
        f"{e('💎')} Error: NINGUNO\n"
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


@router.message(Command("gh"))
async def cmd_ghost_single(message: Message):
    user_id = message.from_user.id
    if not await _can_use_ghost(user_id):
        await message.answer(_denied_message("gh"), parse_mode=ParseMode.HTML)
        return

    now = time.time()
    last = last_ghost_single.get(user_id, 0)
    if (now - last) < GHOST_SINGLE_COOLDOWN:
        remaining = GHOST_SINGLE_COOLDOWN - (now - last)
        await message.answer(
            f"⏳ Debes esperar <b>{remaining:.1f}</b> segundos antes de volver a usar /gh.",
            parse_mode=ParseMode.HTML,
        )
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "❌ Formato incorrecto.\n\n"
            "/gh &lt;cc&gt;|&lt;mes&gt;|&lt;año&gt;|&lt;cvv&gt;\n\n"
            "Ejemplo:\n"
            "/gh 4532640527811643|12|2028|123"
        )
        return

    card_data = args[1].strip()
    if len(card_data.split("|")) != 4:
        await message.answer("❌ Formato de tarjeta incorrecto. Usa: &lt;cc&gt;|&lt;mes&gt;|&lt;año&gt;|&lt;cvv&gt;")
        return

    processing = await message.answer(
        processing_text(GHOST_GATE_NAME), parse_mode=ParseMode.HTML
    )

    try:
        result = await ghost_check(card_data, user_id)
    except Exception:
        logger.exception("Error en gate Ghost")
        result = {"status": "⚠️ Error", "message": "error"}
    finally:
        last_ghost_single[user_id] = time.time()

    text = _format_ghost_response(card_data, result, "gh")
    try:
        result_msg = await processing.edit_text(text, reply_markup=_result_markup(), parse_mode=ParseMode.HTML)
    except TelegramBadRequest:
        result_msg = await message.answer(text, reply_markup=_result_markup(), parse_mode=ParseMode.HTML)

    if result.get("status") != "✅ Live":
        asyncio.create_task(_delete_after(result_msg))


@router.message(Command("ghmass"))
async def cmd_ghost_mass(message: Message):
    """Modo masivo deshabilitado: verificaciones solo una por una con /gh."""
    await message.answer(
        "❌ El modo masivo no está disponible para Ghost.\n"
        "Usa /gh una tarjeta por vez."
    )


def register_ghost_handlers(dp):
    dp.include_router(router)
