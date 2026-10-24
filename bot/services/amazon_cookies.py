"""Gestión de cookies de Amazon por usuario."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from bot.services.database import get_connection


async def save_amazon_cookie(user_id: int, cookie: str) -> None:
    """Guarda o actualiza la cookie de Amazon de un usuario."""
    now = datetime.now(timezone.utc).isoformat()
    async with get_connection() as db:
        await db.execute(
            """
            INSERT INTO amazon_cookies (user_id, cookie, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                cookie = excluded.cookie,
                updated_at = excluded.updated_at
            """,
            (user_id, cookie, now),
        )
        await db.commit()


async def get_amazon_cookie(user_id: int) -> Optional[str]:
    """Devuelve la cookie de Amazon guardada para un usuario, o None."""
    async with get_connection() as db:
        async with db.execute(
            "SELECT cookie FROM amazon_cookies WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


async def delete_amazon_cookie(user_id: int) -> bool:
    """Elimina la cookie de Amazon de un usuario. Devuelve True si existía."""
    async with get_connection() as db:
        cursor = await db.execute(
            "DELETE FROM amazon_cookies WHERE user_id = ?",
            (user_id,),
        )
        await db.commit()
        return cursor.rowcount > 0
