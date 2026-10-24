"""Gestión de membresías (SQLite)."""
import aiosqlite
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict
from bot.config import PLAN_LABELS
from bot.services.database import get_connection
from bot.services.users import ensure_user

# Días por plan cuando no se especifican días personalizados
PLAN_DAYS = {
    "semanal": 7,
    "quincenal": 15,
    "mensual": 30,
    "olimpo": 365,
    "personalizado": None,
}


async def assign_membership(user_id: int, plan: str, custom_days: Optional[int] = None) -> dict:
    await ensure_user(user_id)
    label = PLAN_LABELS.get(plan, "Personalizado")

    if custom_days is not None and custom_days <= 0:
        raise ValueError("Los días personalizados deben ser mayores que 0")
    days = custom_days if custom_days else PLAN_DAYS.get(plan)
    if not days:
        raise ValueError(f"Plan no válido: {plan}")
    end_dt = datetime.now(timezone.utc) + timedelta(days=days)
    end_date = end_dt.isoformat()

    async with get_connection() as db:
        await db.execute('''
            INSERT INTO memberships (user_id, plan, end_date)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                plan = excluded.plan,
                end_date = excluded.end_date
        ''', (user_id, plan, end_date))
        await db.commit()
        return {"user_id": user_id, "plan": label, "end_date": end_date}

async def get_membership(user_id: int) -> Optional[dict]:
    async with get_connection() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM memberships WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                res = dict(row)
                res["label"] = PLAN_LABELS.get(res["plan"], "Personalizado")
                return res
            return None

async def remove_membership(user_id: int) -> bool:
    async with get_connection() as db:
        async with db.execute("SELECT 1 FROM memberships WHERE user_id = ?", (user_id,)) as cursor:
            if not await cursor.fetchone():
                return False
        
        await db.execute("DELETE FROM memberships WHERE user_id = ?", (user_id,))
        await db.commit()
        return True

def format_end_date(membership: dict) -> str:
    """Mantiene la compatibilidad con la función anterior."""
    end_date = membership.get("end_date")
    if not end_date:
        return "N/A"
    try:
        return datetime.fromisoformat(end_date).strftime("%Y-%m-%d")
    except Exception:
        return str(end_date)


def is_membership_active(membership: Optional[dict]) -> bool:
    """Devuelve True si la membresía existe y su fecha de fin no ha vencido."""
    if not membership:
        return False
    end_date = membership.get("end_date")
    if not end_date:
        return False
    try:
        end = datetime.fromisoformat(end_date)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) <= end
    except Exception:
        # Si no podemos parsear la fecha, asumimos vencida para evitar acceso no intencional
        return False
