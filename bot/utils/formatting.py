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


_ANY_HTML_TAG_RE = re.compile(r"<[a-zA-Z][^>]*>")

_BLOCK_TAGS = (
    "p", "div", "li", "tr", "td", "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "pre", "section", "article", "header", "footer",
    "main", "hr",
)


def _list_prefix(li) -> str:
    """Viñeta para <ul>, número para <ol> según la posición del <li>."""
    parent = li.find_parent(["ol", "ul"])
    if parent and parent.name == "ol":
        for i, sibling in enumerate(parent.find_all("li", recursive=False), 1):
            if sibling is li:
                return f"{i}. "
    return "• "


def email_body_to_text(body: str) -> str:
    """Convierte el cuerpo de un correo HTML en texto plano legible.

    Los correos de marketing suelen venir enteros en HTML (con CSS
    incluido). Quita lo invisible (head, style, script, comentarios),
    mantiene saltos por bloque, listas, enlaces y colapsa espacios.
    Si el cuerpo ya es texto plano, lo regresa casi igual. Nunca devuelve
    HTML crudo: todo se escapa después en el llamante para parse_mode HTML.
    """
    if not body:
        return ""
    if not _ANY_HTML_TAG_RE.search(body):
        return body.strip()
    try:
        from bs4 import BeautifulSoup, Comment
    except ImportError:
        return re.sub(r"<[^>]+>", " ", body).strip()
    try:
        soup = BeautifulSoup(body, "html.parser")
        for tag in soup(["head", "style", "script", "title", "meta", "link", "noscript", "svg"]):
            tag.decompose()
        for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
            comment.extract()
        for br in soup.find_all("br"):
            br.replace_with("\n")
        for li in soup.find_all("li"):
            li.insert_before(_list_prefix(li) + " ")
        for a in soup.find_all("a"):
            href = a.get("href") or ""
            text = a.get_text(" ", strip=True)
            if href and href.lower().startswith(("http://", "https://", "mailto:")):
                target = href[7:] if href.lower().startswith("mailto:") else href
                if text and text != target:
                    a.replace_with(f"{text} ({target})")
                else:
                    a.replace_with(target)
        for tag in list(soup.find_all(True)):
            name = (tag.name or "").lower()
            if name in _BLOCK_TAGS:
                tag.insert_before("\n")
                tag.insert_after("\n")
        text = soup.get_text("\n")
    except Exception:
        return re.sub(r"<[^>]+>", " ", body).strip()
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def message_to_html(message: Message) -> str:
    """Devuelve el texto o caption de un mensaje como HTML, incluyendo entidades."""
    if message.text:
        return html_decoration.unparse(message.text, message.entities)
    if message.caption:
        return html_decoration.unparse(message.caption, message.caption_entities)
    return ""
