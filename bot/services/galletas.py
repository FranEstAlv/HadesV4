"""Cupo de galletas: cuentas Amazon que un usuario puede generar con /gencookie.

Cada asignación suma el paquete al saldo existente (30 Olimpo / 20 Rancias)
y otorga acceso delegado a /gencookie. Cada cuenta generada con éxito
descuenta 1 galleta. Los super admins no consumen cupo.
"""
from datetime import datetime, timezone
from typing import Optional

import aiosqlite

import random as _random

from bot.services.database import get_connection
from bot.services.users import ensure_user
# Galletas que otorga cada paquete
TIPO_AMOUNT = {
    "olimpo": 30,
    "rancias": 20,
}

TIPO_LABELS = {
    "olimpo": "Olimpo",
    "rancias": "Rancias",
}


async def grant_galletas(user_id: int, tipo: str, granted_by: int) -> dict:
    """Suma un paquete de galletas al cupo existente del usuario.

    Si ya tenía saldo, el paquete se acumula (no se reemplaza).
    Devuelve el cupo actualizado más la cantidad sumada en "added".
    """
    if tipo not in TIPO_AMOUNT:
        raise ValueError(f"Tipo de galletas inválido: {tipo}")
    await ensure_user(user_id)
    amount = TIPO_AMOUNT[tipo]
    async with get_connection() as db:
        await db.execute(
            """
            INSERT INTO user_galletas (user_id, tipo, total, usadas, granted_by, granted_at)
            VALUES (?, ?, ?, 0, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                tipo = excluded.tipo,
                total = user_galletas.total + excluded.total,
                granted_by = excluded.granted_by,
                granted_at = excluded.granted_at
            """,
            (user_id, tipo, amount, granted_by, datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()
    data = await get_galletas(user_id)
    data["added"] = amount
    return data


async def get_galletas(user_id: int) -> Optional[dict]:
    """Devuelve el cupo de galletas del usuario, con saldo restante."""
    async with get_connection() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM user_galletas WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            res = dict(row)
            res["label"] = TIPO_LABELS.get(res["tipo"], res["tipo"])
            res["left"] = max(0, res["total"] - res["usadas"])
            return res


async def galletas_left(user_id: int) -> int:
    """Saldo de galletas disponibles (0 si no tiene paquete)."""
    data = await get_galletas(user_id)
    return data["left"] if data else 0


async def use_galleta(user_id: int) -> bool:
    """Descuenta 1 galleta. Devuelve True si había saldo."""
    async with get_connection() as db:
        cursor = await db.execute(
            "UPDATE user_galletas SET usadas = usadas + 1 "
            "WHERE user_id = ? AND usadas < total",
            (user_id,),
        )
        await db.commit()
        return cursor.rowcount > 0


async def remove_galletas(user_id: int) -> bool:
    """Quita todo el cupo de galletas (matanga). Devuelve True si tenía."""
    async with get_connection() as db:
        cursor = await db.execute(
            "DELETE FROM user_galletas WHERE user_id = ?", (user_id,)
        )
        await db.commit()
        return cursor.rowcount > 0


async def refill_galletas(user_id: int) -> Optional[dict]:
    """Restaura el cupo al total del paquete (usadas = 0). None si no tiene."""
    async with get_connection() as db:
        cursor = await db.execute(
            "UPDATE user_galletas SET usadas = 0 WHERE user_id = ?", (user_id,)
        )
        await db.commit()
        if cursor.rowcount == 0:
            return None
    return await get_galletas(user_id)


async def set_galletas(user_id: int, total: int, granted_by: int) -> dict:
    """Fija manualmente el cupo en un valor (usadas = 0)."""
    if total < 1 or total > 1000:
        raise ValueError("El cupo debe estar entre 1 y 1000.")
    await ensure_user(user_id)
    async with get_connection() as db:
        await db.execute(
            """
            INSERT INTO user_galletas (user_id, tipo, total, usadas, granted_by, granted_at)
            VALUES (?, 'olimpo', ?, 0, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                total = excluded.total,
                usadas = 0,
                granted_by = excluded.granted_by,
                granted_at = excluded.granted_at
            """,
            (user_id, total, granted_by, datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()
    data = await get_galletas(user_id)
    data["added"] = total
    return data


# ─────────────────────────────────────────────────────────────
# Cupo diario para admins sin plan (silencioso, 15-20 al día)
# ─────────────────────────────────────────────────────────────
DAILY_MIN = 15
DAILY_MAX = 20


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


async def get_daily(user_id: int) -> dict:
    """Cupo diario del admin. Cada día se sortea un valor entre 15 y 20."""
    await ensure_user(user_id)
    today = _today()
    async with get_connection() as db:
        db.row_factory = aiosqlite.Row
        # Intentar obtener existente
        async with db.execute(
            "SELECT * FROM admin_daily_galletas WHERE user_id = ? AND day = ?", (user_id, today)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                res = dict(row)
                res["left"] = max(0, res["allowance"] - res["used"])
                return res
        
        # No existe para hoy: generar nuevo cupo atómicamente
        allowance = _random.randint(DAILY_MIN, DAILY_MAX)
        result = await db.execute(
            """
            INSERT INTO admin_daily_galletas (user_id, day, allowance, used)
            VALUES (?, ?, ?, 0)
            ON CONFLICT(user_id) DO UPDATE SET
                day = excluded.day,
                allowance = excluded.allowance,
                used = 0
            RETURNING user_id, day, allowance, used
            """,
            (user_id, today, allowance),
        )
        row = await result.fetchone()
        await db.commit()
        res = dict(row) if row else {"user_id": user_id, "day": today, "allowance": allowance, "used": 0}
    res["left"] = max(0, res["allowance"] - res["used"])
    return res


async def daily_left(user_id: int) -> int:
    """Saldo diario disponible (0 si no es día corriente o sin saldo)."""
    data = await get_daily(user_id)
    return data["left"]


async def use_daily(user_id: int) -> bool:
    """Descuenta 1 del cupo diario. Devuelve True si había saldo."""
    today = _today()
    async with get_connection() as db:
        cursor = await db.execute(
            "UPDATE admin_daily_galletas SET used = used + 1 "
            "WHERE user_id = ? AND day = ? AND used < allowance",
            (user_id, today),
        )
        await db.commit()
        return cursor.rowcount > 0
