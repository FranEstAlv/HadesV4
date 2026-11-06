"""Gestión de créditos (SQLite).

Fuente única de costos: bot.config.MASS_CREDIT_COST / RFC_CREDIT_COST.
Los handlers nunca hardcodean precios: importan la constante.

Reglas:
- get_balance: solo lectura por defecto (create=False): no crea filas
  fantasma al consultar usuarios sin créditos.
- deduct_credits: UPDATE atómico con guarda balance >= amount, registra
  la transacción con admin_id cuando el retiro lo hace un admin.
- remove_credits (matanga): transacción única BEGIN IMMEDIATE para que
  SELECT + UPDATE + INSERT sean atómicos.
- Todas las transacciones guardan timestamp UTC ISO y admin_id cuando
  aplica, para trazabilidad completa en credit_transactions.
"""
import aiosqlite
from datetime import datetime, timezone
from typing import List, Tuple, Optional
from bot.services.database import get_connection
from bot.services.users import ensure_user

async def get_balance(user_id: int, create: bool = False) -> int:
    """Devuelve el saldo. Solo crea la fila si create=True.

    Por defecto no crea nada: consultar el saldo de un usuario nuevo
    devuelve 0 sin ensuciar la tabla credits.
    """
    async with get_connection() as db:
        async with db.execute("SELECT balance FROM credits WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return row[0]
            if not create:
                return 0
            # Solo handlers que van a operar (deduct/add) crean la fila.
            await ensure_user(user_id)
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

async def deduct_credits(
    user_id: int, amount: int, reason: str, admin_id: Optional[int] = None
) -> Tuple[bool, int]:
    """Descuenta créditos de forma atómica.

    admin_id: quién ejecuta el retiro (panel de admin). En cobros por uso
    de comandos se deja None porque no hay admin involucrado.
    """
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
        await db.commit()
        if result.rowcount == 0:
            # No se actualizó ninguna fila: o no tiene saldo o no existe
            async with db.execute(
                "SELECT balance FROM credits WHERE user_id = ?", (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
                current_balance = row[0] if row else 0
            return False, current_balance
        
        async with db.execute("SELECT balance FROM credits WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            new_balance = row[0] if row else 0
        
        now = datetime.now(timezone.utc).isoformat()
        await db.execute('''
            INSERT INTO credit_transactions (user_id, amount, reason, timestamp, admin_id)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, -amount, reason, now, admin_id))
        await db.commit()
        
        return True, new_balance


async def refund_credits(user_id: int, amount: int, reason: str) -> int:
    """Reembolso centralizado con auditoría (Fase 3).

    Es un `add_credits` con admin_id=0 (sistema) para que la razón
    quede trazada en credit_transactions. Retorna el nuevo saldo.
    Si amount <= 0 no hace nada y devuelve el saldo actual.
    """
    import logging as _logging

    if amount <= 0:
        return await get_balance(user_id)
    try:
        return await add_credits(user_id, amount, reason, 0)
    except Exception as exc:
        _logging.getLogger(__name__).error(
            "refund_credits: no se pudo reembolsar %s a %s: %s",
            amount, user_id, exc,
        )
        return await get_balance(user_id)


async def get_transactions(user_id: int, limit: int = 10) -> List[dict]:
    limit = max(1, min(int(limit), 100))
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
    """Quita todos los créditos (matanga). Devuelve (tenía_saldo, saldo_previo).

    Transacción única BEGIN IMMEDIATE: SELECT + UPDATE + INSERT son
    atómicos, sin ventana de carrera entre la lectura y el borrado.
    """
    await ensure_user(user_id)
    now = datetime.now(timezone.utc).isoformat()
    async with get_connection() as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            async with db.execute(
                "SELECT balance FROM credits WHERE user_id = ?", (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
                previous = row[0] if row else 0
            if previous <= 0:
                await db.execute("ROLLBACK")
                return False, 0
            await db.execute("UPDATE credits SET balance = 0 WHERE user_id = ?", (user_id,))
            await db.execute('''
                INSERT INTO credit_transactions (user_id, amount, reason, timestamp, admin_id)
                VALUES (?, ?, ?, ?, ?)
            ''', (user_id, -previous, "Matanga (remoción total)", now, admin_id))
            await db.execute("COMMIT")
            return True, previous
        except Exception:
            try:
                await db.execute("ROLLBACK")
            except Exception:
                pass
            raise


async def has_enough_credits(user_id: int, required: int) -> bool:
    """Verifica si el usuario tiene suficientes créditos.
    
    Si required es 0, solo devuelve True si el usuario existe.
    """
    balance = await get_balance(user_id, create=False)
    return balance >= required


async def deduct_credits(user_id: int, amount: int) -> bool:
    """Deduce créditos de un usuario (para pagos automáticos).
    
    Returns True si la transacción fue exitosa, False si no tiene saldo.
    """
    if amount <= 0:
        return False
    
    balance = await get_balance(user_id, create=False)
    if balance < amount:
        return False
    
    now = datetime.now(timezone.utc).isoformat()
    async with get_connection() as db:
        try:
            await db.execute("BEGIN IMMEDIATE")
            await db.execute("UPDATE credits SET balance = balance - ? WHERE user_id = ?", (amount, user_id))
            await db.execute('''
                INSERT INTO credit_transactions (user_id, amount, reason, timestamp)
                VALUES (?, ?, ?, ?)
            ''', (user_id, -amount, "Generación de imagen por IA", now))
            await db.execute("COMMIT")
            return True
        except Exception:
            try:
                await db.execute("ROLLBACK")
            except Exception:
                pass
            return False

