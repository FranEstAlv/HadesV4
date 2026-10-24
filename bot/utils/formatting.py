"""Utilidades de formato para mensajes del bot."""
import re
from typing import Optional, Union

from aiogram.types import Message
from aiogram.utils.text_decorations import html_decoration

# IDs de emojis personalizados (custom emoji) para usar en textos y botones.
# Requieren que la cuenta dueña del bot tenga Telegram Premium.
CUSTOM_EMOJIS = {
    "perfil": "5170618377835643873",
    "gates": "5017088445353296841",
    "precios_dinero": "5019808886228517474",
    "precios_tridente": "5021712394259268143",
    "contacto": "5017077798129369907",
    "dead": "5019523782004441717",
}


def custom_emoji(key: str, fallback: str) -> str:
    """Devuelve un custom emoji en HTML con fallback para no-Premium."""
    emoji_id = CUSTOM_EMOJIS.get(key)
    if not emoji_id:
        return fallback
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'


def search_emoji() -> str:
    """Lupa animada para mensajes de verificación en curso."""
    return custom_emoji("gates", "🔍")


def dead_emoji() -> str:
    """Emoji de Dead para los veredictos."""
    return custom_emoji("dead", "❌")


# Frames para dar sensación de animación en los captions (se elige por índice).
SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


def progress_bar(done: int, total: int, width: int = 12) -> str:
    """Barra de progreso con bloques llenos/vacíos."""
    if total <= 0:
        return "░" * width
    filled = min(width, int(width * done / total))
    return "▓" * filled + "░" * (width - filled)


def processing_text(gate_name: str, frame: int = 0) -> str:
    """Caption dinámico de verificación en curso (unitario).

    Lupa personalizada 5017088445353296841 + spinner animado + mini barra
    indeterminada para que no se vea estático mientras llega el resultado.
    """
    spinner = SPINNER_FRAMES[frame % len(SPINNER_FRAMES)]
    # Barra indeterminada: un bloque que se mueve según el frame.
    width = 12
    pos = frame % width
    bar = "".join("▓" if i == pos else "░" for i in range(width))
    return (
        f"{search_emoji()} <b>Verificando con {escape_html(gate_name)}...</b>\n"
        f"{spinner} <i>Rastreando tu tarjeta, espera un momento...</i>\n"
        f"<code>{bar}</code>"
    )


def mass_processing_text(gate_name: str, done: int, total: int) -> str:
    """Caption dinámico de verificación en curso (masivo, con barra real)."""
    spinner_emoji = '<tg-emoji emoji-id="4999015678238262018">⚡</tg-emoji>'
    pct = int(100 * done / total) if total > 0 else 0
    # Mensajes dinámicos del 1 al 9 según progreso
    mensajes = [
        "Empezamos",
        "Si jaló nmms",
        "Huele a obo",
        "Padalustro",
        "A la mitad",
        "Una más",
        "Nmms ya we",
        "Sexo en el Oxxo",
        "Ya casi",
    ]
    msg_idx = min(done, len(mensajes) - 1) if done > 0 else 0
    msg_text = mensajes[msg_idx]
    # Barra de 10 partes: avanza 1 bloque por actualización; fija (10x▰) a partir del 50%
    if pct >= 50:
        bar_str = "▰" * 10
    else:
        filled = min(done, 10)
        bar_str = "▰" * filled + "░" * (10 - filled)
    return (
        f'<tg-emoji emoji-id="5017088445353296841">🔍</tg-emoji> <b>Verificando Tarjetas con {escape_html(gate_name)} Mi Amor...</b>\n\n'
        f"{spinner_emoji} {bar_str} <code>{pct}%</code>\n"
        f'<tg-emoji emoji-id="5371058888017715839">⚡</tg-emoji> <i>{msg_text} · {done}/{total}</i>\n'
        f'<tg-emoji emoji-id="5017409485568738052">🔹</tg-emoji> Aguanta men, sigo revisando... '
    )


def strip_custom_emojis(text: str) -> str:
    """Elimina tags <tg-emoji> y deja solo el fallback."""
    return re.sub(r'<tg-emoji[^>]*>(.*?)</tg-emoji>', r'\1', text)


def escape_html(text: Optional[str]) -> str:
    """Escapa caracteres HTML básicos para evitar parse errors accidentales."""
    if not text:
        return ""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def message_to_html(message: Message) -> str:
    """Devuelve el texto o caption de un mensaje como HTML, incluyendo entidades."""
    if message.text:
        return html_decoration.unparse(message.text, message.entities)
    if message.caption:
        return html_decoration.unparse(message.caption, message.caption_entities)
    return ""
