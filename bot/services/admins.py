"""Gestión de administradores (Persistencia SQLite)."""
import aiosqlite
from typing import Set
from bot.config import SUPER_ADMIN_IDS
from bot.services.database import get_connection

async def get_admin_ids() -> Set[int]:
    """Devuelve los IDs de administradores normales."""
    async with get_connection() as db:
        async with db.execute("SELECT user_id FROM admins") as cursor:
            rows = await cursor.fetchall()
            return {row[0] for row in rows}

async def get_all_admin_ids() -> Set[int]:
    """Devuelve todos los IDs con acceso admin (normales + super)."""
    admins = await get_admin_ids()
    return admins | SUPER_ADMIN_IDS

async def add_admin(user_id: int) -> bool:
    """Agrega un admin normal. Devuelve True si se agregó, False si ya existía."""
    if user_id in SUPER_ADMIN_IDS:
        return False
    
    async with get_connection() as db:
        # Verificar si ya existe
        async with db.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,)) as cursor:
            if await cursor.fetchone():
                return False
        
        await db.execute("INSERT INTO admins (user_id) VALUES (?)", (user_id,))
        await db.commit()
        return True

async def remove_admin(user_id: int) -> bool:
    """Elimina un admin normal. No permite eliminar super admins."""
    if user_id in SUPER_ADMIN_IDS:
        return False
    
    async with get_connection() as db:
        async with db.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,)) as cursor:
            if not await cursor.fetchone():
                return False
        
        await db.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
        await db.commit()
        return True

async def is_admin(user_id: int) -> bool:
    return user_id in await get_all_admin_ids()

def is_super_admin(user_id: int) -> bool:
    """True si el usuario es super admin (acceso ilimitado).

    Función síncrona porque solo consulta un set en memoria.
    """
    return user_id in SUPER_ADMIN_IDS
