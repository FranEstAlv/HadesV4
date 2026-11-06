"""Handler de RFC y CURP para Hades.

Integración con el CSV de datos federalizados.
Sin rastro de streamlit ni dependencias externas.
"""

import asyncio
import csv
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest

from bot.services.memberships import get_membership, is_membership_active
from bot.services.admins import is_admin
from bot.services.credits import deduct_credits
from bot.utils.formatting import escape_html

router = Router()
logger = logging.getLogger(__name__)

# Configuración del módulo
MODULE_ID = "rfc"
MODULE_NAME = "RFC & CURP"
MODULE_COST = 25
CSV_PATH = Path(__file__).parent.parent / "evidencias" / "personal_federalizado_datos_completos.csv"

# Emojis personalizados
_GATE_CUSTOM_EMOJI_ID = "5454074580010295588"
_CREDIT_CUSTOM_EMOJI_ID = "5019808886228517474"

# Cooldowns
LAST_RFC_USE: Dict[int, datetime] = {}


def _gate_emoji(fallback: str) -> str:
    """Devuelve el custom emoji del panel Gates."""
    return f'<tg-emoji emoji-id="{_GATE_CUSTOM_EMOJI_ID}">{fallback}</tg-emoji>'


def _credit_emoji(fallback: str) -> str:
    """Devuelve el custom emoji de créditos."""
    return f'<tg-emoji emoji-id="{_CREDIT_CUSTOM_EMOJI_ID}">{fallback}</tg-emoji>'


def _format_rfc_message(record: Dict) -> str:
    """Formatea el mensaje de entrega con blockquote y monospace para CURP y RFC."""
    
    nombre_completo = record.get('Nombre_Completo', 'No disponible')
    nombre = record.get('Nombre', '')
    paterno = record.get('Apellido_Paterno', '')
    materno = record.get('Apellido_Materno', '')
    
    # Si no hay nombre completo, construirlo
    if not nombre_completo or nombre_completo == 'No disponible':
        if nombre or paterno or materno:
            nombre_completo = f"{nombre} {paterno} {materno}".strip()
        else:
            nombre_completo = 'No disponible'
    
    fecha_nac = record.get('Fecha_Nacimiento', 'No disponible')
    if fecha_nac and '/' in fecha_nac:
        try:
            dt = datetime.strptime(fecha_nac, '%d/%m/%Y')
            fecha_nac = dt.strftime('%d-%m-%Y')
        except Exception:
            pass
    
    edad = record.get('Edad_Actual', 'No disponible')
    sexo = record.get('Sexo', 'No disponible').upper()
    estado_nac = record.get('Estado_Nacimiento', 'No disponible')
    curp = record.get('CURP', 'No disponible')
    rfc = record.get('RFC', 'No disponible')
    homoclave = ''
    
    # Extraer homoclave si existe (últimos 3 caracteres del RFC si hay 13 dígitos)
    if rfc and rfc != 'No disponible' and len(rfc) == 13:
        homoclave = rfc[-3:]
        rfc = rfc[:-3]
    
    return f"""<blockquote><b>HADES CHK</b>

👤Nombre: {escape_html(nombre_completo)}

🎂Edad: {escape_html(str(edad))}
🔥Sexo: {escape_html(str(sexo))}
👶Fecha de nacimiento: {escape_html(str(fecha_nac))}
🌎Estado de nacimiento: {escape_html(str(estado_nac))}
💳C.U.R.P: <code>{escape_html(str(curp))}</code>
💳R.F.C: <code>{escape_html(str(rfc))}</code>
💳Homoclave: {escape_html(str(homoclave))}

HADES CHK</blockquote>"""


def _load_csv_data() -> List[Dict]:
    """Carga y devuelve los datos del CSV."""
    datos = []
    try:
        with open(CSV_PATH, 'r', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                datos.append(row)
    except FileNotFoundError:
        logger.error(f"CSV no encontrado: {CSV_PATH}")
    except Exception as e:
        logger.error(f"Error al cargar CSV: {e}")
    return datos


def _get_random_record() -> Optional[Dict]:
    """Devuelve un registro aleatorio del CSV."""
    datos = _load_csv_data()
    if not datos:
        return None
    import random
    return random.choice(datos)


async def _can_use_rfc(user_id: int) -> bool:
    """Verifica si el usuario puede usar el comando /rfc."""
    if await is_admin(user_id):
        return True
    membership = await get_membership(user_id)
    if not is_membership_active(membership):
        return False
    return True


async def _check_cooldown(user_id: int) -> Tuple[bool, str]:
    """Verifica si el usuario tiene cooldown activo. Devuelve (puede_usar, mensaje_error)."""
    cooldown_minutes = 5
    if user_id in LAST_RFC_USE:
        elapsed = (datetime.now() - LAST_RFC_USE[user_id]).total_seconds() / 60
        if elapsed < cooldown_minutes:
            remaining = int(cooldown_minutes - elapsed)
            return False, f"⏱️ Estás en cooldown. Espera {remaining} minuto(s) antes de usar /rfc de nuevo."
    return True, ""


async def _update_cooldown(user_id: int):
    """Actualiza el timestamp del último uso por usuario."""
    LAST_RFC_USE[user_id] = datetime.now()


@router.message(Command("rfc"))
async def cmd_rfc(message: Message):
    """Comando /rfc: entrega un registro aleatorio de RFC y CURP."""
    user = message.from_user
    
    # Verificar cooldown
    puede_usar, error_msg = await _check_cooldown(user.id)
    if not puede_usar:
        await message.answer(error_msg, parse_mode=ParseMode.HTML)
        return
    
    # Verificar membresía/permisos
    if not await _can_use_rfc(user.id):
        await message.answer(
            f"<blockquote>❌ <b>ACCESO RESTRINGIDO</b>\n\n"
            f"Necesitas una membresía activa para usar este comando.</blockquote>",
            parse_mode=ParseMode.HTML
        )
        return
    
    # Verificar créditos
    user_credits = await deduct_credits(user.id, MODULE_COST, f"{MODULE_ID}_rfc_consulta")
    if not user_credits[0]:
        remaining = await deduct_credits(user.id, 0, "")[1] if hasattr(deduct_credits(user.id, 0, ""), '__getitem__') else 0
        from bot.services.credits import get_balance
        remaining = await get_balance(user.id)
        await message.answer(
            f"<blockquote>❌ <b>CRÉDITOS INSUFICIENTES</b>\n\n"
            f"Esta consulta cuesta {MODULE_COST} créditos.\n"
            f"Tienes: {remaining} créditos.</blockquote>",
            parse_mode=ParseMode.HTML
        )
        return
    
    # Obtener registro aleatorio
    record = _get_random_record()
    if not record:
        await message.answer(
            "<blockquote>❌ <b>ERROR TEMPORAL</b>\n\n"
            "No se pueden cargar los datos en este momento. Inténtalo más tarde.</blockquote>",
            parse_mode=ParseMode.HTML
        )
        logger.error("No se pudieron cargar datos del CSV para /rfc")
        return
    
    # Formatear y enviar mensaje
    try:
        message_text = _format_rfc_message(record)
        await message.answer(message_text, parse_mode=ParseMode.HTML)
        
        # Actualizar cooldown
        await _update_cooldown(user.id)
        
        logger.info(f"Usuario {user.id} obtuvo RFC/CURP. Costo: {MODULE_COST} créditos")
        
    except TelegramBadRequest as e:
        logger.error(f"Error al enviar mensaje de RFC: {e}")
        await message.answer(
            "<blockquote>❌ <b>ERROR AL ENVIAR</b>\n\n"
            "Hubo un problema al procesar el mensaje. Revisa los logs.</blockquote>",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Error inesperado en /rfc: {e}", exc_info=True)
        await message.answer(
            "<blockquote>❌ <b>ERROR INESPERADO</b>\n\n"
            "Ocurrió un error al procesar tu solicitud. Inténtalo más tarde.</blockquote>",
            parse_mode=ParseMode.HTML
        )


def register_rfc_handlers(dp):
    """Registra el handler de /rfc en el dispatcher."""
    dp.include_router(router)
