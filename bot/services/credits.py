"""Gestión de créditos (SQLite)."""
import aiosqlite
from datetime import datetime, timezone
from typing import List, Tuple, Optional
from bot.services.database import get_connection
from bot.services.users import ensure_user

async def get_balance(user_id: int) -> int:
    await ensure_user(user_id)
    async with get_connection() as db:
        async with db.execute("SELECT balance FROM credits WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return row[0]
            # Si no tiene registro, creamos uno con balance 0
            await db.execute("INSERT INTO credits (user_id, balance) VALUES (?, 0)", (user_id,))
            await db.commit()
            return 0

async def add_credits(user_id: int, amount: int, reason: str, admin_id: int) -> int:
    if amount <= 0:
        raise ValueError("El monto debe ser positivo")
    await ensure_user(user_id)
    now = datetime.now(timezone.utc).isoformat()
    async with get_connection() as db:
        # Actualizar balance
        await db.execute('''
            INSERT INTO credits (user_id, balance) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET balance = balance + excluded.balance
        ''', (user_id, amount))
        
        # Registrar transacción
        await db.execute('''
            INSERT INTO credit_transactions (user_id, amount, reason, timestamp, admin_id)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, amount, reason, now, admin_id))
        
        await db.commit()
        
        async with db.execute("SELECT balance FROM credits WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0]

async def deduct_credits(user_id: int, amount: int, reason: str) -> Tuple[bool, int]:
    if amount <= 0:
        raise ValueError("El monto debe ser positivo")
    await ensure_user(user_id)
    async with get_connection() as db:
        # Operación atómica: descuenta solo si hay saldo suficiente
        result = await db.execute(
            """
            UPDATE credits 
            SET balance = balance - ? 
            WHERE user_id = ? AND balance >= ?
            """,
            (amount, user_id, amount)
        )
        if result.rowcount == 0:
            # No se actualizó ninguna fila: o no tiene saldo o no existe
            current_balance = await get_balance(user_id)
            return False, current_balance
        
        await db.commit()
        
        async with db.execute("SELECT balance FROM credits WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            new_balance = row[0] if row else 0
        
        now = datetime.now(timezone.utc).isoformat()
        await db.execute('''
            INSERT INTO credit_transactions (user_id, amount, reason, timestamp)
            VALUES (?, ?, ?, ?)
        ''', (user_id, -amount, reason, now))
        await db.commit()
        
        return True, new_balance

async def get_transactions(user_id: int, limit: int = 10) -> List[dict]:
    async with get_connection() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('''
            SELECT * FROM credit_transactions 
            WHERE user_id = ? 
            ORDER BY timestamp DESC LIMIT ?
        ''', (user_id, limit)) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def remove_credits(user_id: int, admin_id: int) -> Tuple[bool, int]:
    """Quita todos los créditos (matanga). Devuelve (tenía_saldo, saldo_previo)."""
    await ensure_user(user_id)
    now = datetime.now(timezone.utc).isoformat()
    async with get_connection() as db:
        async with db.execute("SELECT balance FROM credits WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            previous = row[0] if row else 0
        if previous <= 0:
            return False, 0
        await db.execute("UPDATE credits SET balance = 0 WHERE user_id = ?", (user_id,))
        await db.execute('''
            INSERT INTO credit_transactions (user_id, amount, reason, timestamp, admin_id)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, -previous, "Matanga (remoción total)", now, admin_id))
        await db.commit()
        return True, previous
