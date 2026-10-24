from aiogram import Router, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
import asyncio
import logging
from typing import Dict, List, Optional
import time

from bot.services.memberships import get_membership, is_membership_active
from bot.services.admins import is_admin, is_super_admin
from bot.services.credits import get_balance, deduct_credits
from bot.utils.formatting import (
    dead_emoji,
    escape_html,
    mass_processing_text,
    processing_text,
)
from bot.utils.bin_lookup import get_card_info

# Importar la función de verificación
from bot.gates.hiperion_checker import hiperion_check, hiperion_check_multiple

router = Router()
logger = logging.getLogger(__name__)

# Configuración del gate
HIPERION_SINGLE_COOLDOWN = 5
HIPERION_MASS_COOLDOWN = 15
HIPERION_MASS_CREDIT_COST = 0  # Gratis para todos (solo cobra en mass para no-olimpo)
HIPERION_GATE_NAME = "Hiperion"

_GATE_CUSTOM_EMOJI_ID = "5454074580010295588"
_LIVE_CUSTOM_EMOJI_ID = "5021905410089550576"
# Lupa de validación 5017088445353296841 y Dead 5019523782004441717.
_SEARCH_CUSTOM_EMOJI_ID = "5017088445353296841"
_DEAD_CUSTOM_EMOJI_ID = "5019523782004441717"


def _dead_status() -> str:
    """Status Dead con el emoji personalizado pedido."""
    return f"{dead_emoji()} Dead"

last_hiperion_time: Dict[int, float] = {}


def _gate_emoji(fallback: str) -> str:
    """Mismo estilo de emoji que el resto de gates."""
    return f'<tg-emoji emoji-id="{_GATE_CUSTOM_EMOJI_ID}">{fallback}</tg-emoji>'


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


async def _is_olimpo_or_admin(user_id: int) -> bool:
    """Verifica si el usuario es admin o tiene plan olimpo"""
    if is_super_admin(user_id) or await is_admin(user_id):
        return True
    
    # Verificar membresía activa de tipo olimpo
    try:
        membership = await get_membership(user_id)
        if membership and is_membership_active(membership) and membership.get("plan") == "olimpo":
            return True
    except Exception:
        pass
    return False


@router.message(Command("hi"))
async def cmd_hiperion(message: Message):
    """Comando individual para verificación con Hiperion Payflow"""
    user_id = message.from_user.id
    
    # Verificar membresía (todos pueden usar este gate)
    # No se requiere membresía específica para uso individual
    
    # Verificar cooldown
    now = time.time()
    last = last_hiperion_time.get(user_id, 0)
    if (now - last) < HIPERION_SINGLE_COOLDOWN:
        remaining = HIPERION_SINGLE_COOLDOWN - (now - last)
        await message.answer(
            f"⏳ Debes esperar <b>{remaining:.1f}</b> segundos antes de volver a usar /hi.",
            parse_mode=ParseMode.HTML,
        )
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer(
            "❌ Formato incorrecto.\n\n"
            "/hi &lt;cc&gt;|&lt;mes&gt;|&lt;año&gt;|&lt;cvv&gt;\n\n"
            "Ejemplo:\n"
            "/hi 5401040078503174|09|30|967"
        )
        return

    card_data = args[1].strip()
    if len(card_data.split("|")) != 4:
        await message.answer(
            "❌ Formato de tarjeta incorrecto. Usa: &lt;cc&gt;|&lt;mes&gt;|&lt;año&gt;|&lt;cvv&gt;"
        )
        return

    processing = await message.answer(
        processing_text(HIPERION_GATE_NAME), parse_mode=ParseMode.HTML
    )

    try:
        # Procesar tarjeta
        result = await hiperion_check(card_data, user_id)
        
        # Formatear respuesta
        response_text = _format_hiperion_response(card_data, result, "hi")

        try:
            result_msg = await processing.edit_text(response_text, reply_markup=_result_markup(), parse_mode=ParseMode.HTML)
        except TelegramBadRequest:
            result_msg = await message.answer(response_text, reply_markup=_result_markup(), parse_mode=ParseMode.HTML)

        if result.get("status") != "✅ Live":
            asyncio.create_task(_delete_after(result_msg))

        # Actualizar tiempo de último uso
        last_hiperion_time[user_id] = time.time()
        
    except Exception as e:
        logger.error(f"Error en gate Hiperion: {e}")
        await processing.edit_text("❌ Error al procesar la tarjeta.")
        
        # Registrar error en logs del canal si es necesario
        # await send_log_to_channel(f"Error en Hiperion gate para user {user_id}: {e}")

def _format_hiperion_response(card_data: str, result: Dict, command: str = "hi") -> str:
    """Formatea la respuesta del gate Hiperion con la misma plantilla del resto."""
    card_parts = card_data.split("|")
    bin_info = _get_bin_info(card_parts[0]) if len(card_parts) == 4 else _get_bin_info("")

    status_raw = result.get("status", "⚠️ Error")
    if status_raw == "✅ Live":
        status_icon = f'<tg-emoji emoji-id="{_LIVE_CUSTOM_EMOJI_ID}">✅</tg-emoji> Live'
        resultado = "APROBADA"
        response_text = result.get("message", "")
    elif status_raw in ("❌ Dead", "💸 Fondos Insuficientes"):
        # Veredictos de tarjeta (Fondos pertenece a Dead): conservan su respuesta.
        if status_raw == "❌ Dead":
            status_icon = _dead_status()
        else:
            status_icon = f"{dead_emoji()} Fondos Insuficientes"
        resultado = "DECLINADA"
        response_text = result.get("message", "")
    else:
        # Solo los errores se sanitizan.
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
        f"{e('💎')} {HIPERION_GATE_NAME} Check\n"
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

@router.message(Command("himass"))
async def cmd_hiperion_mass(message: Message):
    """Comando masivo para verificación con Hiperion Payflow"""
    user_id = message.from_user.id
    
    # Verificar membresía (todos pueden usar este gate)
    # Pero solo los no-olimpo pagan créditos en modo masivo
    
    # Verificar cooldown
    now = time.time()
    last = last_hiperion_time.get(user_id, 0)
    if (now - last) < HIPERION_MASS_COOLDOWN:
        remaining = HIPERION_MASS_COOLDOWN - (now - last)
        await message.answer(
            f"⏳ Debes esperar <b>{remaining:.1f}</b> segundos antes de volver a usar /himass.",
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
            f"Responde al mensaje con las tarjetas con /himass o escríbelas a continuación.\n\n"
            "Formato aceptado: 15 o 16 dígitos|mes|año|cvv\n"
            "Ejemplo: 5401040078503174|09|30|967"
        )
        return

    # Limitar a 10 tarjetas máximo
    lines = lines[:10]
    total = len(lines)

    # Verificar si el usuario debe pagar créditos (no es olimpo ni admin)
    should_charge_credits = not await _is_olimpo_or_admin(user_id)
    credits_cost = 0  # Gratis para todos según especificación
    
    if should_charge_credits and credits_cost > 0:
        balance = await get_balance(user_id)
        if balance < credits_cost:
            await message.answer(
                f"❌ Las verificaciones masivas cuestan <b>{credits_cost}</b> créditos.\n"
                f"Tu saldo actual es <b>{balance}</b>.\n\n"
                "Adquiere créditos o contrata el plan Olimpo para usarlos gratis e ilimitados.",
                parse_mode=ParseMode.HTML,
            )
            return
        
        # Deduct credits
        success, new_balance = await deduct_credits(user_id, credits_cost, f"Uso /himass ({total} tarjetas)")
        if not success:
            await message.answer(
                f"❌ Error al deducir créditos.\n"
                f"Tu saldo actual es <b>{new_balance}</b>.",
                parse_mode=ParseMode.HTML,
            )
            return

    processing = await message.answer(
        mass_processing_text(HIPERION_GATE_NAME, 0, total),
        parse_mode=ParseMode.HTML,
    )

    last_hiperion_time[user_id] = time.time()

    lock = asyncio.Lock()
    processed_count = 0
    current_processing = processing

    async def result_callback(index: int, result: Dict, card_data: str, uid: int) -> None:
        nonlocal processed_count, current_processing
        async with lock:
            response_text = _format_hiperion_response(card_data, result, "himass")

            try:
                sent = await message.answer(response_text, reply_markup=_result_markup(), parse_mode=ParseMode.HTML)
            except TelegramBadRequest:
                sent = await message.answer(
                    escape_html(response_text), reply_markup=_result_markup()
                )

            if result.get("status") != "✅ Live":
                asyncio.create_task(_delete_after(sent))

            processed_count += 1
            if processed_count < total:
                try:
                    new_msg = await message.answer(
                        mass_processing_text(
                            HIPERION_GATE_NAME, processed_count, total
                        ),
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

    # Procesar tarjetas masivamente
    asyncio.create_task(hiperion_check_multiple(lines, result_callback, user_id))

def _extract_cards_from_text(text: Optional[str]) -> List[str]:
    """Extrae tarjetas en formato cc|mes|año|cvv de un bloque de texto."""
    if not text:
        return []

    # Remover etiquetas HTML y caracteres especiales comunes
    import re
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

def register_hiperion_handlers(dp):
    """Registra los handlers de Hiperion"""
    dp.include_router(router)