"""Utilidades para generación de imágenes por IA."""

import base64
import os
from datetime import datetime
from pathlib import Path

from bot.config import BASE_DIR


def save_base64_image(image_b64: str, filename: str = None) -> str:
    """Guarda una imagen en base64 a un archivo.
    
    Args:
        image_b64: Cadena en base64 de la imagen
        filename: Nombre del archivo (opcional, genera uno automáticamente)
    
    Returns:
        Ruta al archivo guardado
    """
    # Decodificar base64
    image_bytes = base64.b64decode(image_b64)
    
    # Generar nombre si no se proporciona
    if not filename:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"ai_image_{timestamp}.png"
    
    # Crear directorio si no existe
    output_dir = BASE_DIR / "data" / "ai_images"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Guardar archivo
    filepath = output_dir / filename
    with open(filepath, "wb") as f:
        f.write(image_bytes)
    
    return str(filepath)


def format_image_caption(model: str, cost: float, credits: int, prompt: str = None, is_admin: bool = False) -> str:
    """Formatea el caption de una imagen generada.
    
    Args:
        model: Nombre del modelo usado
        cost: Costo en USD
        credits: Créditos cobrados
        prompt: Prompt original (solo para admins)
        is_admin: Si el usuario es admin
    
    Returns:
        HTML string con el caption
    """
    model_name = model.split("/")[-1] if "/" in model else model
    
    caption = (
        f"<b>🎨 Imagen generada</b>\n"
        f"Modelo: <code>{model_name}</code>\n"
        f"Costo: ${cost:.3f} USD ({credits} créditos)"
    )
    
    if is_admin and prompt:
        caption += f"\nPrompt: <i>{prompt}</i>"
    
    return f"<blockquote>{caption}</blockquote>"
