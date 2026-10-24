"""Persistencia de usuarios (SQLite)."""
import aiosqlite
from datetime import datetime, timezone
from typing import Dict, List, Optional
from bot.services.database import get_connection

async def register_user(user_id: int, username: Optional[str], full_name: str) -> None:
    """Registra o actualiza un usuario que inició el bot."""
    now = datetime.now(timezone.utc).isoformat()
    async with get_connection() as db:
        # Verificar si ya existe para mantener first_seen
        async with db.execute("SELECT first_seen FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            first_seen = row[0] if row else now
        
        await db.execute('''
            INSERT INTO users (user_id, username, full_name, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                full_name = excluded.full_name,
                last_seen = excluded.last_seen
        ''', (user_id, username, full_name, first_seen, now))
        await db.commit()

async def get_all_users() -> List[dict]:
    """Devuelve la lista de todos los usuarios registrados."""
    async with get_connection() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users") as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

async def get_user_count() -> int:
    async with get_connection() as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cursor:
            row = await cursor.fetchone()
            return row[0]

async def get_user(user_id: int) -> Optional[dict]:
    async with get_connection() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def user_exists(user_id: int) -> bool:
    async with get_connection() as db:
        async with db.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)) as cursor:
            return await cursor.fetchone() is not None


async def ensure_user(user_id: int, username: Optional[str] = None, full_name: str = "") -> None:
    """Asegura que el usuario exista en la tabla users; lo crea si falta.

    Útil para operaciones que escriben en credits/memberships y necesitan
    que la foreign key esté satisfecha.
    """
    now = datetime.now(timezone.utc).isoformat()
    async with get_connection() as db:
        await db.execute('''
            INSERT INTO users (user_id, username, full_name, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                last_seen = excluded.last_seen,
                username = COALESCE(excluded.username, username),
                full_name = COALESCE(NULLIF(excluded.full_name, ''), full_name)
        ''', (user_id, username, full_name, now, now))
        await db.commit()
