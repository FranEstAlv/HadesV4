"""Correo temporal — /correo sobre la EmailApi local (EmailApi/main.py).

NO usa la API de alfa1 (mail.tm). Los correos salen de la EmailApi propia:
GET /odisea/generate → {email, token, expires_*} y
GET /odisea/messages/{token} → {email, expires_in_seconds, messages}.

Reglas:
- Comando /correo: sin costo, sin membresía (abierto a todo usuario
  registrado). Solo super admins exentos de nada porque no hay cobro.
- Duración: 24 horas por cuenta (CORREO_TTL_H = 24). La EmailApi expira
  en 30 min; el bot re-emite el token si caducó dentro de las 24h y la
  bandeja se consulta directo a la BD de la EmailApi sin filtro de expira.
- Una cuenta activa por usuario: crear otra reemplaza la anterior.
"""
from __future__ import annotations

import logging
import os
import random
import sqlite3
import string
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import aiohttp

from bot.services.database import get_connection

logger = logging.getLogger(__name__)

# Duración de la cuenta para el usuario (horas). La EmailApi expira en
# minutos; el bot extiende la vida útil re-emitiendo tokens.
CORREO_TTL_H = 24

EMAILAPI_BASE = (
    f"http://{os.getenv('EMAILAPI_API_HOST', '127.0.0.1')}"
    f":{os.getenv('EMAILAPI_API_PORT', '3000')}/odisea"
)
EMAILAPI_DB = os.getenv("EMAILAPI_DB_PATH", "EmailApi/emails.db")


class CorreoError(Exception):
    """Error del servicio de correo temporal."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS correo_accounts (
    user_id    INTEGER PRIMARY KEY,
    email      TEXT NOT NULL,
    token      TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_correo_expires ON correo_accounts(expires_at);
"""


async def ensure_schema() -> None:
    async with get_connection() as db:
        await db.executescript(_SCHEMA)
        await db.commit()
    logger.info("correo: esquema verificado")


def _bot_root() -> str:
    from pathlib import Path

    return str(Path(__file__).parent.parent.parent)


def _emailapi_db_path() -> str:
    p = EMAILAPI_DB
    if not os.path.isabs(p):
        p = os.path.join(_bot_root(), p)
    return p


# ---------------------------------------------------------------------------
# EmailApi HTTP
# ---------------------------------------------------------------------------

async def _api_generate() -> Dict[str, Any]:
    """Pide una cuenta nueva a la EmailApi. Lanza CorreoError si falla."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{EMAILAPI_BASE}/generate",
                timeout=aiohttp.ClientTimeout(total=15),
            ) as r:
                if r.status >= 400:
                    raise CorreoError(f"La EmailApi respondió {r.status}")
                data = await r.json()
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        raise CorreoError("No se pudo conectar con el servicio de correo") from exc
    if not data.get("email") or not data.get("token"):
        raise CorreoError("La EmailApi devolvió una cuenta inválida")
    return data


async def _api_messages(token: str) -> Dict[str, Any]:
    """Lee la bandeja por token. Devuelve el JSON crudo de la EmailApi."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{EMAILAPI_BASE}/messages/{token}",
                timeout=aiohttp.ClientTimeout(total=15),
            ) as r:
                if r.status in (404, 410):
                    raise CorreoError("expired")
                if r.status >= 400:
                    raise CorreoError(f"La EmailApi respondió {r.status}")
                return await r.json()
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        raise CorreoError("No se pudo conectar con el servicio de correo") from exc


import asyncio


def _emailapi_conn() -> sqlite3.Connection:
    c = sqlite3.connect(_emailapi_db_path())
    c.row_factory = sqlite3.Row
    return c


def _emailapi_reseed(email: str, minutes: int = 30) -> str:
    """Re-emite un token fresco para un email (extiende la vida útil).

    La EmailApi expira cuentas en 30 min. Para dar 24h al usuario, el bot
    inserta un token nuevo apuntando al mismo email: los mensajes viejos
    (por recipient) siguen llegando a la bandeja.
    """
    import uuid

    token = str(uuid.uuid4())
    expires = datetime.now() + timedelta(minutes=minutes)
    c = _emailapi_conn()
    try:
        c.execute(
            "INSERT OR REPLACE INTO accounts (token, email, expires_at) "
            "VALUES (?, ?, ?)",
            (email.lower(), email.lower(), expires.isoformat()),
        )
        c.commit()
    finally:
        c.close()
    return token


def _emailapi_inbox_direct(email: str) -> List[Dict[str, Any]]:
    """Bandeja directo a la BD de la EmailApi, sin filtro de expira.

    La EmailApi filtra por received_at > 30 min. El bot da 24h, así que
    lee sin ese filtro (la limpieza de 24h la hace purge_correo).
    """
    c = _emailapi_conn()
    try:
        rows = c.execute(
            "SELECT m.sender, m.subject, m.body, m.received_at, "
            "(SELECT COUNT(*) FROM attachments a WHERE a.message_id = m.id) AS n_att "
            "FROM messages m WHERE m.recipient = ? "
            "ORDER BY m.received_at DESC LIMIT 20",
            (email.lower(),),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


def _emailapi_delete_message(email: str, received_at: str) -> bool:
    c = _emailapi_conn()
    try:
        c.execute(
            "DELETE FROM attachments WHERE message_id IN "
            "(SELECT id FROM messages WHERE recipient = ? AND received_at = ?)",
            (email.lower(), received_at),
        )
        cur = c.execute(
            "DELETE FROM messages WHERE recipient = ? AND received_at = ?",
            (email.lower(), received_at),
        )
        c.commit()
        return cur.rowcount > 0
    finally:
        c.close()


# Límite práctico para subir por Bot API (Telegram acepta 50 MB multipart).
MAX_ATTACHMENT_BYTES = 45 * 1024 * 1024


def _emailapi_list_attachments(email: str, received_at: str) -> List[Dict[str, Any]]:
    """Metadatos de los adjuntos de un mensaje (sin el contenido)."""
    c = _emailapi_conn()
    try:
        rows = c.execute(
            "SELECT a.id, a.filename, a.content_type, LENGTH(a.data) AS size "
            "FROM attachments a JOIN messages m ON a.message_id = m.id "
            "WHERE m.recipient = ? AND m.received_at = ? "
            "ORDER BY a.id",
            (email.lower(), received_at),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


def _emailapi_attachment_data(
    email: str, received_at: str, att_id: int
) -> Optional[Dict[str, Any]]:
    """Datos binarios de un adjunto (verificando que sea de ese mensaje)."""
    c = _emailapi_conn()
    try:
        row = c.execute(
            "SELECT a.filename, a.content_type, a.data "
            "FROM attachments a JOIN messages m ON a.message_id = m.id "
            "WHERE m.recipient = ? AND m.received_at = ? AND a.id = ?",
            (email.lower(), received_at, att_id),
        ).fetchone()
        return dict(row) if row else None
    finally:
        c.close()


# ---------------------------------------------------------------------------
# Cuentas del bot (24h)
# ---------------------------------------------------------------------------

async def _save_account(user_id: int, email: str, token: str) -> Dict[str, str]:
    now = datetime.now(timezone.utc)
    expires = (now + timedelta(hours=CORREO_TTL_H)).isoformat()
    async with get_connection() as db:
        await db.execute(
            "INSERT INTO correo_accounts (user_id, email, token, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "email=excluded.email, token=excluded.token, "
            "created_at=excluded.created_at, expires_at=excluded.expires_at",
            (user_id, email.lower(), token, now.isoformat(), expires),
        )
        await db.commit()
    return {"email": email.lower(), "expires_at": expires}


async def get_account(user_id: int) -> Optional[Dict[str, Any]]:
    """Cuenta activa del usuario o None (expirada o inexistente)."""
    async with get_connection() as db:
        import aiosqlite

        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM correo_accounts WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            acc = dict(row)
    try:
        exp = datetime.fromisoformat(acc["expires_at"])
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp <= datetime.now(timezone.utc):
            await delete_account(user_id)
            return None
    except Exception:
        return None
    return acc


async def delete_account(user_id: int) -> None:
    async with get_connection() as db:
        await db.execute("DELETE FROM correo_accounts WHERE user_id = ?", (user_id,))
        await db.commit()


async def remaining_h(user_id: int) -> float:
    acc = await get_account(user_id)
    if not acc:
        return 0.0
    try:
        exp = datetime.fromisoformat(acc["expires_at"])
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return max(0.0, (exp - datetime.now(timezone.utc)).total_seconds() / 3600)
    except Exception:
        return 0.0


async def create_account(user_id: int) -> Dict[str, str]:
    """Crea una cuenta de 24h (reemplaza la anterior si existe)."""
    data = await _api_generate()
    return await _save_account(user_id, data["email"], data["token"])


async def _ensure_fresh_token(acc: Dict[str, Any]) -> str:
    """Si el token de la EmailApi caducó (30 min), re-emite uno y lo guarda."""
    token = acc["token"]
    try:
        await _api_messages(token)
        return token
    except CorreoError as exc:
        if str(exc) != "expired":
            raise
    # Token de la EmailApi vencido pero la cuenta del bot sigue viva:
    # re-emitir y guardar el nuevo token.
    new_token = await asyncio.to_thread(_emailapi_reseed, acc["email"])
    async with get_connection() as db:
        await db.execute(
            "UPDATE correo_accounts SET token = ? WHERE user_id = ?",
            (new_token, acc.get("user_id")),
        )
        await db.commit()
    return new_token


async def fetch_inbox(user_id: int) -> List[Dict[str, Any]]:
    """Bandeja de la cuenta activa (hasta 20 mensajes, directos a la BD)."""
    acc = await get_account(user_id)
    if not acc:
        raise CorreoError("Sin cuenta activa")
    # Asegurar token fresco para no romper nada que dependa de la API;
    # la lectura real va directo a la BD (24h, sin filtro de 30 min).
    try:
        fresh = await _ensure_fresh_token(acc)
        if fresh != acc["token"]:
            acc["token"] = fresh
    except CorreoError:
        pass
    return await asyncio.to_thread(_emailapi_inbox_direct, acc["email"])


async def delete_message(user_id: int, received_at: str) -> bool:
    acc = await get_account(user_id)
    if not acc:
        return False
    return await asyncio.to_thread(
        _emailapi_delete_message, acc["email"], received_at
    )


async def list_attachments(user_id: int, received_at: str) -> List[Dict[str, Any]]:
    """Adjuntos (metadatos) del mensaje indicado de la cuenta del usuario."""
    acc = await get_account(user_id)
    if not acc:
        return []
    atts = await asyncio.to_thread(
        _emailapi_list_attachments, acc["email"], received_at
    )
    # Telegram sube archivos hasta 50 MB por Bot API; se marcan aparte
    # los que excedan el límite práctico para avisar al tocarlos.
    for a in atts:
        a["too_big"] = (a.get("size") or 0) > MAX_ATTACHMENT_BYTES
    return atts


async def fetch_attachment(
    user_id: int, received_at: str, att_id: int
) -> Optional[Dict[str, Any]]:
    """Archivo adjunto (bytes) de la cuenta del usuario, o None si no existe.

    Devuelve dict con filename, content_type, size y data (bytes).
    """
    acc = await get_account(user_id)
    if not acc:
        return None
    att = await asyncio.to_thread(
        _emailapi_attachment_data, acc["email"], received_at, att_id
    )
    if not att:
        return None
    data = att.get("data") or b""
    if not isinstance(data, bytes):
        data = bytes(data)
    return {
        "filename": att.get("filename") or "archivo",
        "content_type": att.get("content_type") or "application/octet-stream",
        "size": len(data),
        "data": data,
    }


async def random_domain() -> str:
    """Dominio aleatorio de los configurados (para mostrar variedad)."""
    domains = [
        d.strip()
        for d in os.getenv("EMAILAPI_DOMAINS", "olimpo.cyou").split(",")
        if d.strip()
    ]
    return random.choice(domains) if domains else "olimpo.cyou"


async def purge_expired() -> int:
    """Borra cuentas del bot vencidas (>24h). Devuelve cuántas."""
    now = datetime.now(timezone.utc).isoformat()
    async with get_connection() as db:
        cur = await db.execute(
            "DELETE FROM correo_accounts WHERE expires_at <= ?", (now,)
        )
        await db.commit()
        return cur.rowcount or 0
