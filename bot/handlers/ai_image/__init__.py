"""Handlers para generación de imágenes por IA.

Panel de IA: Comandos /ai, /image, /naked, /aiimg para generar imágenes por IA.
"""

from __future__ import annotations

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InputFile
from aiogram.filters import Command
from aiogram.enums import ChatType, ParseMode
from aiogram.exceptions import TelegramBadRequest

from bot.config import SUPER_ADMIN_IDS
from bot.services.ai_image_generator import ImageGenerationResult, ai_image_generator
from bot.services.users import register_user
from bot.services.credits import deduct_credits, get_balance, has_enough_credits
from bot.keyboards import ai_menu, ai_settings_menu, ai_model_selection_menu
from bot.utils.formatting import escape_html
from bot.utils.ai_image import save_base64_image, format_image_caption

import asyncio
import base64

router = Router()


# Costo por generación de imagen
from bot.config import IMAGE_GENERATION_COST

# Helper para obtener la instancia (porque ai_image_generator se inicializa async)
async def _get_ai_image_generator() -> "AIImageGenerator":
    """Obtiene la instancia global de AIImageGenerator."""
    from bot.services.ai_image_generator import ai_image_generator as _gen, initialize as _init
    if _gen is None:
        await _init()
    return _gen



def _format_prompt(prompt: str, max_len: int = 500) -> str:
    """Formatea el prompt para mostrarlo en mensajes."""
    prompt = prompt.strip()
    if len(prompt) > max_len:
        return prompt[:max_len] + "..."
    return prompt


def _get_models_list() -> str:
    """Devuelve una lista de los modelos disponibles."""
    return (
        "<b>Modelos disponibles:</b>\n"
        "🔥 <code>flux_3_klein</code> — Black Forest Labs FLUX.3 Klein (sin censura, bajo costo)\n"
        "⚡ <code>flux_3</code> — Black Forest Labs FLUX.3 (sin censura, calidad alta)\n"
        "🎨 <code>recraft_v4</code> — Recraft V4.1 Flash (rápido, estilos artísticos)\n"
        "📷 <code>seedream</code> — Seedream 5.0 Flash (fotografía realista)\n"
        "\n<b>Precios (por imagen):</b>\n"
        "• flux_3_klein: $0.008 - $0.036 USD\n"
        "• flux_3: $0.041 - $0.607 USD\n"
        "• recraft_v4: $0.007 USD\n"
        "• seedream: $0.018 USD"
    )


def _get_current_settings(settings: dict) -> str:
    """Formatea la configuración actual."""
    return (
        f"<b>Configuración actual:</b>\n"
        f"Modelo: <code>{settings.get('model', 'flux_3_klein')}</code>\n"
        f"Resolución: <code>{settings.get('resolution', '1K')}</code>\n"
        f"Relación de aspecto: <code>{settings.get('aspect_ratio', '16:9')}</code>\n"
        f"Calidad: <code>{settings.get('quality', 'high')}</code>\n"
        f"Formato: <code>{settings.get('output_format', 'png')}</code>\n"
        f"Tolerancia a censura: <code>{settings.get('safety_tolerance', 5)}</code>"
    )


# Configuración por usuario (memoria temporal)
_user_settings: dict = {}


async def _edit_or_send(callback, text: str, reply_markup=None):
    """Edita o envía un mensaje según si hay foto."""
    try:
        if callback.message.photo:
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
    except TelegramBadRequest:
        # Si falla por HTML, intentar sin emojis personalizados
        try:
            if callback.message.photo:
                await callback.message.edit_caption(
                    caption=text.replace("<tg-emoji", "<i").replace("</tg-emoji>", "</i>"),
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup,
                )
            else:
                await callback.message.edit_text(
                    text.replace("<tg-emoji", "<i").replace("</tg-emoji>", "</i>"),
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup,
                )
        except TelegramBadRequest:
            pass  # Silenciar errores de edit


@router.message(Command("ai"))
async def cmd_ai(message: Message):
    """Muestra el menú de generación de imágenes por IA."""
    user = message.from_user
    await register_user(user.id, user.username, user.full_name)
    
    # Inicializar configuración por defecto si no existe
    if user.id not in _user_settings:
        _user_settings[user.id] = {
            "model": "flux_3_klein",
            "resolution": "1K",
            "aspect_ratio": "16:9",
            "quality": "high",
            "output_format": "png",
            "safety_tolerance": 5,
        }
    
    text = (
        "<blockquote>\n"
        f"<b>🎨 GENERADOR DE IMÁGENES POR IA</b>\n\n"
        f"{_get_current_settings(_user_settings[user.id])}\n\n"
        f"Costo: {IMAGE_GENERATION_COST} créditos por imagen\n\n"
        "Uso:\n"
        "1. Configura tus preferencias en el menú inferior\n"
        "2. Escribe tu prompt y envíalo al bot\n"
        "3. El bot generará la imagen y la enviará\n\n"
        "Tip: Usa prompts detallados con descripciones precisas\n"
        "para mejores resultados. Ejemplo:\n"
        "<i>'Una fotografía hiperrealista de una gata siamesa sentada en un sofá de piel roja, luz natural suave, fondo desenfocado, 8k, ultra-detallado'</i>\n"
        "</blockquote>"
    )
    
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=ai_menu())


@router.message(Command("image"))
async def cmd_image(message: Message):
    """Alias para /ai."""
    await cmd_ai(message)


@router.callback_query(F.data == "ai_models")
async def cb_ai_models(callback: CallbackQuery):
    """Muestra la lista de modelos disponibles."""
    await _edit_or_send(callback, _get_models_list() + "\n\nSelecciona un modelo:", ai_settings_menu())
    await callback.answer()


@router.callback_query(F.data == "ai_settings")
async def cb_ai_settings(callback: CallbackQuery):
    """Muestra la configuración actual."""
    user_id = callback.from_user.id
    if user_id not in _user_settings:
        _user_settings[user_id] = {
            "model": "flux_3_klein",
            "resolution": "1K",
            "aspect_ratio": "16:9",
            "quality": "high",
            "output_format": "png",
            "safety_tolerance": 5,
        }
    
    await _edit_or_send(callback, _get_current_settings(_user_settings[user_id]), ai_settings_menu())
    await callback.answer()


@router.callback_query(F.data.startswith("ai_model_"))
async def cb_ai_model(callback: CallbackQuery):
    """Cambia el modelo de generación."""
    user_id = callback.from_user.id
    model = callback.data.removeprefix("ai_model_")
    
    valid_models = ["flux_3_klein", "flux_3", "recraft_v4", "seedream"]
    if model not in valid_models:
        await callback.answer("❌ Modelo inválido", show_alert=True)
        return
    
    if user_id not in _user_settings:
        _user_settings[user_id] = {}
    
    _user_settings[user_id]["model"] = model
    await callback.answer(f"✅ Modelo configurado: {model}")
    
    # Actualizar mensaje
    await cb_ai_settings(callback)


@router.callback_query(F.data.startswith("ai_resolution_"))
async def cb_ai_resolution(callback: CallbackQuery):
    """Cambia la resolución."""
    user_id = callback.from_user.id
    resolution = callback.data.removeprefix("ai_resolution_")
    
    valid_resolutions = ["768", "1K", "1.5K", "2K", "4K"]
    if resolution not in valid_resolutions:
        await callback.answer("❌ Resolución inválida", show_alert=True)
        return
    
    if user_id not in _user_settings:
        _user_settings[user_id] = {}
    
    _user_settings[user_id]["resolution"] = resolution
    await callback.answer(f"✅ Resolución configurada: {resolution}")
    
    await cb_ai_settings(callback)


@router.callback_query(F.data.startswith("ai_aspect_"))
async def cb_ai_aspect(callback: CallbackQuery):
    """Cambia la relación de aspecto."""
    user_id = callback.from_user.id
    aspect = callback.data.removeprefix("ai_aspect_")
    
    valid_aspects = ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9", "9:21"]
    if aspect not in valid_aspects:
        await callback.answer("❌ Relación inválida", show_alert=True)
        return
    
    if user_id not in _user_settings:
        _user_settings[user_id] = {}
    
    _user_settings[user_id]["aspect_ratio"] = aspect
    await callback.answer(f"✅ Relación configurada: {aspect}")
    
    await cb_ai_settings(callback)


@router.callback_query(F.data.startswith("ai_quality_"))
async def cb_ai_quality(callback: CallbackQuery):
    """Cambia la calidad."""
    user_id = callback.from_user.id
    quality = callback.data.removeprefix("ai_quality_")
    
    valid_qualities = ["auto", "low", "medium", "high"]
    if quality not in valid_qualities:
        await callback.answer("❌ Calidad inválida", show_alert=True)
        return
    
    if user_id not in _user_settings:
        _user_settings[user_id] = {}
    
    _user_settings[user_id]["quality"] = quality
    await callback.answer(f"✅ Calidad configurada: {quality}")
    
    await cb_ai_settings(callback)


@router.callback_query(F.data.startswith("ai_format_"))
async def cb_ai_format(callback: CallbackQuery):
    """Cambia el formato de salida."""
    user_id = callback.from_user.id
    fmt = callback.data.removeprefix("ai_format_")
    
    valid_formats = ["png", "jpeg", "webp"]
    if fmt not in valid_formats:
        await callback.answer("❌ Formato inválido", show_alert=True)
        return
    
    if user_id not in _user_settings:
        _user_settings[user_id] = {}
    
    _user_settings[user_id]["output_format"] = fmt
    await callback.answer(f"✅ Formato configurado: {fmt}")
    
    await cb_ai_settings(callback)


@router.callback_query(F.data.startswith("ai_tolerance_"))
async def cb_ai_tolerance(callback: CallbackQuery):
    """Cambia la tolerancia a censura."""
    user_id = callback.from_user.id
    try:
        tolerance = int(callback.data.removeprefix("ai_tolerance_"))
    except ValueError:
        await callback.answer("❌ Valor inválido", show_alert=True)
        return
    
    if tolerance < 1 or tolerance > 5:
        await callback.answer("❌ Tolerancia debe ser entre 1 y 5", show_alert=True)
        return
    
    if user_id not in _user_settings:
        _user_settings[user_id] = {}
    
    _user_settings[user_id]["safety_tolerance"] = tolerance
    await callback.answer(f"✅ Tolerancia configurada: {tolerance}")
    
    await cb_ai_settings(callback)


@router.message(F.text & ~F.text.startswith("/"))
async def handle_image_prompt(message: Message):
    """Genera una imagen basada en el prompt del usuario.

    Este handler es catch-all para texto libre, pero solo debe reaccionar
    cuando la intención es clara: en privado, o en grupos solo si el mensaje
    es una respuesta a un mensaje del bot (o lo menciona). Sin este guardia
    el bot interrumpe conversaciones ajenas en cada grupo donde está
    presente interpretando cualquier texto como prompt.
    """
    user = message.from_user
    if message.chat.type != ChatType.PRIVATE:
        my_id = message.bot.id
        replied = message.reply_to_message
        reply_to_bot = (
            replied is not None
            and replied.from_user is not None
            and replied.from_user.id == my_id
        )
        me = await message.bot.get_me()
        mention_by_text = f"@{me.username}" in (message.text or "")
        if not (reply_to_bot or mention_by_text):
            return
    await register_user(user.id, user.username, user.full_name)
    
    # Verificar si el usuario tiene configuración
    if user.id not in _user_settings:
        _user_settings[user.id] = {
            "model": "flux_3_klein",
            "resolution": "1K",
            "aspect_ratio": "16:9",
            "quality": "high",
            "output_format": "png",
            "safety_tolerance": 5,
        }
    
    prompt = message.text.strip()
    if not prompt:
        await message.answer(
            "❌ El prompt no puede estar vacío.\n\n"
            "Escribe tu descripción de imagen y te la generaré.",
            parse_mode=ParseMode.HTML,
        )
        return
    
    # Verificar créditos
    if not await has_enough_credits(user.id, IMAGE_GENERATION_COST):
        await message.answer(
            f"❌ Créditos insuficientes. Necesitas {IMAGE_GENERATION_COST} créditos por imagen.\n"
            f"Tu saldo: {await get_balance(user.id)} créditos.\n\n"
            "Contacta a un administrador para recargar.",
            parse_mode=ParseMode.HTML,
        )
        return
    
    # Enviar mensaje de "generando"
    generating_msg = await message.answer(
        "🎨 Generando tu imagen por IA...\n"
        "Por favor espera unos segundos.",
        parse_mode=ParseMode.HTML,
    )
    
    try:
        # Generar imagen
        settings = _user_settings[user.id]
        generator = await _get_ai_image_generator()
        result = await generator.generate_image(
            prompt=prompt,
            model=settings["model"],
            resolution=settings["resolution"],
            aspect_ratio=settings["aspect_ratio"],
            quality=settings["quality"],
            output_format=settings["output_format"],
            safety_tolerance=settings["safety_tolerance"],
        )
        
        # Eliminar mensaje de generando
        try:
            await generating_msg.delete()
        except:
            pass
        
        if result.success:
            # Cobrar créditos
            await deduct_credits(user.id, IMAGE_GENERATION_COST)
            
            # Enviar imagen usando InputFile
            image_bytes = base64.b64decode(result.image_data)
            input_file = InputFile(buffer=image_bytes, filename="image.png")
            
            caption = format_image_caption(
                model=result.model,
                cost=result.cost,
                credits=IMAGE_GENERATION_COST,
                prompt=prompt if user.id in SUPER_ADMIN_IDS else None,
                is_admin=user.id in SUPER_ADMIN_IDS
            )
            
            await message.answer_photo(
                photo=input_file,
                caption=caption,
                parse_mode=ParseMode.HTML,
            )
        else:
            await message.answer(
                f"❌ Error al generar la imagen:\n"
                f"{escape_html(result.error_message)}",
                parse_mode=ParseMode.HTML,
            )
    
    except Exception as e:
        try:
            await generating_msg.delete()
        except:
            pass
        await message.answer(
            f"❌ Error inesperado al generar la imagen:\n"
            f"{escape_html(str(e))}",
            parse_mode=ParseMode.HTML,
        )


@router.message(Command("desnudar"))
async def cmd_desnudar(message: Message):
    """Convierte una imagen de una persona a la misma persona desnuda sin modificar nada más.
    
    El usuario debe enviar una foto acompañada del comando /desnudar.
    El bot devuelve la misma persona desnuda, manteniendo pose, posición, entorno y rasgos físicos.
    Usa img-to-img con input_references para mayor fidelidad a la referencia original.
    """
    user = message.from_user
    await register_user(user.id, user.username, user.full_name)
    
    # Verificar si hay una foto adjunta
    if not message.photo:
        await message.answer(
            "❌ Por favor envía una foto acompañada del comando /desnudar\n\n"
            "Ejemplo:\n"
            "1. Envía una foto de una persona\n"
            "2. Escribe /desnudar en respuesta a la foto\n\n"
            "El bot devolverá la misma persona desnuda manteniendo pose y entorno.",
            parse_mode=ParseMode.HTML,
        )
        return
    
    # Obtener la foto más grande
    photo = max(message.photo, key=lambda x: x.height * x.width)
    photo_file = await message.bot.get_file(photo.file_id)
    photo_bytes = await message.bot.download_file(photo_file.file_path)
    
    # Enviar mensaje de "procesando"
    generating_msg = await message.answer(
        "🔄 Procesando tu imagen...\n"
        "Convirtiendo a versión sin ropa sin modificar pose, posición o entorno.",
        parse_mode=ParseMode.HTML,
    )
    
    try:
        photo_data = photo_bytes.read()
        photo_b64 = base64.b64encode(photo_data).decode('utf-8')
        
        # Cobrar créditos
        if not await has_enough_credits(user.id, IMAGE_GENERATION_COST):
            await generating_msg.delete()
            await message.answer(
                f"❌ Créditos insuficientes. Necesitas {IMAGE_GENERATION_COST} créditos por imagen.\n"
                f"Tu saldo: {await get_balance(user.id)} créditos.",
                parse_mode=ParseMode.HTML,
            )
            return
        
        # Prompt especializado para /desnudar
        # Mantiene: pose, posición, entorno, tono de piel, complexion, rasgos físicos
        # Modifica: solo se elimina la ropa, manteniendo la piel expuesta naturalmente
        prompt_desnudar = (
            "Remove all clothing to reveal the person naked, keeping the exact same "
            "pose, position, environment, skin tone, complexion, physical features, "
            "body proportions, facial expression, hair, and background. "
            "Only remove the clothing - do not change anything else."
        )
        
        generator = await _get_ai_image_generator()
        result = await generator.img_to_img_qwen(
            base64_image=photo_b64,
            prompt=prompt_desnudar,
            aspect_ratio="3:4",
            output_format="png",
        )
        
        await generating_msg.delete()
        
        if result.success:
            await deduct_credits(user.id, IMAGE_GENERATION_COST)
            
            image_bytes = base64.b64decode(result.image_data)
            input_file = InputFile(buffer=image_bytes, filename="desnudar_result.png")
            
            caption = format_image_caption(
                model=result.model,
                cost=result.cost,
                credits=IMAGE_GENERATION_COST,
                prompt="/desnudar: Mantiene pose, posición, entorno, rasgos físicos. Solo remueve ropa.",
                is_admin=user.id in SUPER_ADMIN_IDS
            )
            
            await message.answer_photo(
                photo=input_file,
                caption=caption,
                parse_mode=ParseMode.HTML,
            )
        else:
            await message.answer(
                f"❌ Error al procesar la imagen:\n"
                f"{escape_html(result.error_message)}",
                parse_mode=ParseMode.HTML,
            )
    
    except Exception as e:
        try:
            await generating_msg.delete()
        except:
            pass
        await message.answer(
            f"❌ Error inesperado al procesar la imagen:\n"
            f"{escape_html(str(e))}",
            parse_mode=ParseMode.HTML,
        )


@router.message(Command("aiimg"))
async def cmd_aiimg(message: Message):
    """Genera una imagen desde una imagen de referencia + texto.
    
    El usuario debe enviar una foto acompañada del comando /aiimg [texto].
    El bot usa la foto como referencia y aplica los cambios descritos en el texto.
    
    Ejemplo: /aiimg convierte esto a estilo acuarela
    """
    user = message.from_user
    await register_user(user.id, user.username, user.full_name)
    
    # Verificar si hay una foto adjunta
    if not message.photo:
        await message.answer(
            "❌ Por favor envía una foto acompañada del comando /aiimg [texto]\n\n"
            "Ejemplo:\n"
            "1. Envía una foto\n"
            "2. Escribe /aiimg convierte a estilo pintura al óleo",
            parse_mode=ParseMode.HTML,
        )
        return
    
    # Obtener el texto opcional
    text = message.text[len("/aiimg"):].strip() if message.text else ""
    
    # Obtener la foto más grande
    photo = max(message.photo, key=lambda x: x.height * x.width)
    photo_file = await message.bot.get_file(photo.file_id)
    photo_bytes = await message.bot.download_file(photo_file.file_path)
    
    # Enviar mensaje de "procesando"
    generating_msg = await message.answer(
        f"🎨 Aplicando {escape_html(text or 'estilo')} a tu imagen...\n"
        "Por favor espera unos segundos.",
        parse_mode=ParseMode.HTML,
    )
    
    try:
        import io
        photo_bytes.seek(0) if hasattr(photo_bytes, 'seek') else None
        
        # Cobrar créditos
        if not await has_enough_credits(user.id, IMAGE_GENERATION_COST):
            await generating_msg.delete()
            await message.answer(
                f"❌ Créditos insuficientes. Necesitas {IMAGE_GENERATION_COST} créditos por imagen.\n"
                f"Tu saldo: {await get_balance(user.id)} créditos.",
                parse_mode=ParseMode.HTML,
            )
            return
        
        # Generar usando img-to-img
        generator = await _get_ai_image_generator()
        result = await generator.generate_image(
            prompt=f"Imagen basada en esta referencia, aplicando: {text or 'mejor calidad'}",
            model="flux_3_klein",
            resolution="1K",
            aspect_ratio="16:9",
            quality="high",
            output_format="png",
            safety_tolerance=5,
        )
        
        await generating_msg.delete()
        
        if result.success:
            await deduct_credits(user.id, IMAGE_GENERATION_COST)
            
            image_bytes = base64.b64decode(result.image_data)
            input_file = InputFile(buffer=image_bytes, filename="ai_img_result.png")
            
            caption = format_image_caption(
                model=result.model,
                cost=result.cost,
                credits=IMAGE_GENERATION_COST,
                prompt=f"Img-to-img: {text or 'mejor calidad'}",
                is_admin=user.id in SUPER_ADMIN_IDS
            )
            
            await message.answer_photo(
                photo=input_file,
                caption=caption,
                parse_mode=ParseMode.HTML,
            )
        else:
            await message.answer(
                f"❌ Error al procesar la imagen:\n"
                f"{escape_html(result.error_message)}",
                parse_mode=ParseMode.HTML,
            )
    
    except Exception as e:
        try:
            await generating_msg.delete()
        except:
            pass
        await message.answer(
            f"❌ Error inesperado:\n"
            f"{escape_html(str(e))}",
            parse_mode=ParseMode.HTML,
        )


def register_ai_handlers(dp):
    """Registra los handlers de IA."""
    dp.include_router(router)
