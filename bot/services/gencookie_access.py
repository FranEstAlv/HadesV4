"""Gestión de acceso al comando /gencookie.

Super admins tienen acceso implícito. Esta tabla controla acceso delegado a
administradores normales y usuarios.
"""
from datetime import datetime, timezone
from typing import Optional

import aiosqlite

from bot.services.database import get_connection


async def grant_access(user_id: int, granted_by: int, access_type: str = "user") -> bool:
    """Otorga acceso a /gencookie. Devuelve True si se otorgó, False si ya tenía."""
    async with get_connection() as db:
        async with db.execute(
            "SELECT 1 FROM gencookie_access WHERE user_id = ?", (user_id,)
        ) as cursor:
            if await cursor.fetchone():
                return False
        await db.execute(
            "INSERT INTO gencookie_access (user_id, access_type, granted_by, granted_at) VALUES (?, ?, ?, ?)",
            (user_id, access_type, granted_by, datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()
        return True


async def revoke_access(user_id: int) -> bool:
    """Revoca el acceso a /gencookie. Devuelve True si se eliminó."""
    async with get_connection() as db:
        cursor = await db.execute("DELETE FROM gencookie_access WHERE user_id = ?", (user_id,))
        await db.commit()
        return cursor.rowcount > 0


async def has_access(user_id: int) -> bool:
    """True si el usuario tiene acceso delegado a /gencookie."""
    async with get_connection() as db:
        async with db.execute(
            "SELECT 1 FROM gencookie_access WHERE user_id = ?", (user_id,)
        ) as cursor:
            return await cursor.fetchone() is not None


async def get_access_list(access_type: Optional[str] = None) -> list[dict]:
    """Devuelve la lista de usuarios con acceso delegado."""
    async with get_connection() as db:
        db.row_factory = aiosqlite.Row
        if access_type:
            async with db.execute(
                "SELECT * FROM gencookie_access WHERE access_type = ? ORDER BY granted_at DESC",
                (access_type,),
            ) as cursor:
                rows = await cursor.fetchall()
        else:
            async with db.execute(
                "SELECT * FROM gencookie_access ORDER BY granted_at DESC"
            ) as cursor:
                rows = await cursor.fetchall()
        return [dict(row) for row in rows]
