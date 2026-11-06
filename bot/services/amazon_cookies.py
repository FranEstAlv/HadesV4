"""Gestión de cookies de Amazon por usuario."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from bot.services.database import get_connection


async def save_amazon_cookie(user_id: int, cookie: str, proxy: Optional[str] = None) -> None:
    """Guarda o actualiza la cookie de Amazon de un usuario."""
    now = datetime.now(timezone.utc).isoformat()
    async with get_connection() as db:
        await db.execute(
            """
            INSERT INTO amazon_cookies (user_id, cookie, proxy, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                cookie = excluded.cookie,
                proxy = excluded.proxy,
                updated_at = excluded.updated_at
            """,
            (user_id, cookie, proxy, now),
        )
        await db.commit()


async def get_amazon_cookie(user_id: int) -> Optional[Tuple[str, Optional[str]]]:
    """Devuelve la cookie de Amazon y el proxy guardados para un usuario, o None."""
    async with get_connection() as db:
        async with db.execute(
            "SELECT cookie, proxy FROM amazon_cookies WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return (row[0], row[1]) if row else None


async def delete_amazon_cookie(user_id: int) -> bool:
    """Elimina la cookie de Amazon de un usuario. Devuelve True si existía."""
    async with get_connection() as db:
        cursor = await db.execute(
            "DELETE FROM amazon_cookies WHERE user_id = ?",
            (user_id,),
        )
        await db.commit()
        return cursor.rowcount > 0
