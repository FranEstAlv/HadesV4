"""SMS virtual — números temporales vía HeroSMS (hero-sms.com).

Lógica extraída de APP_OLIMPO/alfa1 (samaritan/services/smspool.py) y
adaptada a este proyecto: aiogram + aiosqlite + bot.services.database.

Flujo:
1. /sms → menú (Pedir SMS / Mi saldo / Historial / ¿Cómo funciona?).
2. Servicio → país (solo países con precio configurado aparecen).
3. Confirmación doble (como alfa1): costo en créditos, saldo y después.
4. Cobro ANTES de comprar (bot.services.credits.deduct_credits).
5. Polling cada 15s con asyncio; si el código no llega en el tiempo
   límite (+ gracia), reembolso automático (add_credits).
6. Cancelación manual en los primeros 10 segundos con reembolso.

Conversión USD → créditos (igual que alfa1, tabla sms_config clave
usd_to_mxn, default 18.5):
    ≤ $5 MXN → 10 cr | ≤ $10 → 20 cr | ≤ $15 → 30 cr | ≤ $20 → 40 cr
    > $20 MXN → 0 cr (país bloqueado, no aparece en el panel).

La clave se lee de HEROSMS_API_KEY. Nunca se escribe en logs.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import time as _time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

from bot.config import DATA_DIR
from bot.services.credits import add_credits, deduct_credits, get_balance
from bot.services.database import get_connection
from bot.services.users import ensure_user

logger = logging.getLogger(__name__)

HEROSMS_BASE = "https://hero-sms.com/stubs/handler_api.php"

# Límites de espera (segundos). Igual que alfa1.
_ORDER_DURATION = 20 * 60  # límite local de espera; cancelar antes de reembolsar
_POLL_INTERVAL = 15        # segundos entre checks
_CANCEL_WINDOW = 10        # segundos para cancelar tras la compra
_MAX_ORDER_AGE_MIN = 25    # minutos para considerar un pedido muerto
_GRACE_DELAYS = [15, 30, 45, 60]  # espera extra antes de declarar fallo


def _api_key() -> str:
    return os.environ.get("HEROSMS_API_KEY", "")


# ---------------------------------------------------------------------------
# Estado en memoria
# ---------------------------------------------------------------------------

# Pedidos activos con polling en curso:
# order_id → {chat_id, message_id, user_id, username, phone,
#              service_name, country_name, credits, cancel_deadline,
#              started_at, expires_at, task}
_ACTIVE: Dict[str, Dict[str, Any]] = {}

# Flujo de compra por usuario (evita depender de FSM):
# user_id → {service_id, service_name, country_id, country_name,
#            usd, credits, countries_map}
_FLOW: Dict[int, Dict[str, Any]] = {}

# Cache de servicios (se refresca cada 6h)
_services_cache: List[Dict[str, Any]] = []
_services_cached_at: Optional[datetime] = None

# Cache de países por servicio (10 min): evita que cada toque al mismo
# servicio dispare ~200 requests a HeroSMS y "congele" el botón.
_countries_cache: Dict[str, Tuple[datetime, List[Dict[str, Any]]]] = {}
_COUNTRIES_TTL_S = 10 * 60


# ---------------------------------------------------------------------------
# Esquema SQLite
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sms_orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_id           INTEGER NOT NULL,
    username        TEXT,
    order_id        TEXT NOT NULL UNIQUE,
    service_id      TEXT,
    service_name    TEXT,
    country_id      INTEGER,
    country_name    TEXT,
    phone_number    TEXT,
    credits_charged INTEGER NOT NULL DEFAULT 0,
    usd_cost        TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    sms_arrived     INTEGER NOT NULL DEFAULT 0,
    requested_at    TEXT NOT NULL,
    completed_at    TEXT,
    error_msg       TEXT
);

CREATE INDEX IF NOT EXISTS idx_sms_orders_tg_id    ON sms_orders (tg_id, requested_at DESC);
CREATE INDEX IF NOT EXISTS idx_sms_orders_status   ON sms_orders (status, requested_at DESC);
CREATE INDEX IF NOT EXISTS idx_sms_orders_order_id ON sms_orders (order_id);

CREATE TABLE IF NOT EXISTS sms_config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT OR IGNORE INTO sms_config (key, value) VALUES ('usd_to_mxn', '18.5');
INSERT OR IGNORE INTO sms_config (key, value) VALUES ('enabled', '1');

CREATE TABLE IF NOT EXISTS sms_tiers (
    piso_mxn  INTEGER PRIMARY KEY,
    creditos  INTEGER NOT NULL
);
"""


async def ensure_schema() -> None:
    """Crea las tablas del módulo si no existen + tramos default."""
    async with get_connection() as db:
        await db.executescript(_SCHEMA)
        await db.commit()
        # Sembrar tramos default si la tabla manual está vacía
        try:
            async with db.execute("SELECT COUNT(*) FROM sms_tiers") as cursor:
                row = await cursor.fetchone()
                if row and row[0] == 0:
                    await db.executemany(
                        "INSERT OR IGNORE INTO sms_tiers (piso_mxn, creditos) VALUES (?, ?)",
                        sorted(DEFAULT_CREDIT_TIERS.items()),
                    )
                    await db.commit()
        except Exception as exc:
            logger.warning("sms: no se pudieron sembrar tramos: %s", exc)
    logger.info("sms: esquema verificado")


async def get_recent_orders(limit: int = 10) -> List[Dict[str, Any]]:
    """Últimos pedidos (para el panel /batman)."""
    limit = max(1, min(int(limit), 50))
    async with get_connection() as db:
        import aiosqlite

        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM sms_orders ORDER BY requested_at DESC LIMIT ?",
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def set_enabled(enabled: bool) -> bool:
    """Activa/desactiva el módulo. Devuelve el estado final."""
    await _set_config("enabled", "1" if enabled else "0")
    return await is_enabled()


async def set_usd_to_mxn(rate: float) -> float:
    """Cambia el tipo de cambio. Devuelve el valor guardado."""
    val = float(rate)
    if val <= 0 or val > 1000:
        raise ValueError("Tipo de cambio inválido (0–1000)")
    await _set_config("usd_to_mxn", str(val))
    return val


async def get_usd_to_mxn() -> str:
    return await _get_config("usd_to_mxn", "18.5")


async def _get_config(key: str, default: str = "") -> str:
    async with get_connection() as db:
        async with db.execute(
            "SELECT value FROM sms_config WHERE key = ?", (key,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else default


async def _set_config(key: str, value: str) -> None:
    async with get_connection() as db:
        await db.execute(
            "INSERT INTO sms_config (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        await db.commit()


async def is_enabled() -> bool:
    return (await _get_config("enabled", "1")) == "1"


# ---------------------------------------------------------------------------
# Persistencia de pedidos
# ---------------------------------------------------------------------------

async def _save_order(
    tg_id: int,
    username: str,
    order_id: str,
    service_id: str,
    service_name: str,
    country_id: int,
    country_name: str,
    phone_number: str,
    credits_charged: int,
    usd_cost: str,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    async with get_connection() as db:
        await db.execute(
            """
            INSERT INTO sms_orders
                (tg_id, username, order_id, service_id, service_name,
                 country_id, country_name, phone_number, credits_charged,
                 usd_cost, status, sms_arrived, requested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?)
            """,
            (
                tg_id, username, order_id, service_id, service_name,
                country_id, country_name, phone_number,
                credits_charged, usd_cost, now,
            ),
        )
        await db.commit()


async def _update_order_status(
    order_id: str, status: str, sms_arrived: int = 0, error_msg: str = ""
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    async with get_connection() as db:
        await db.execute(
            """
            UPDATE sms_orders
            SET status=?, sms_arrived=?, completed_at=?, error_msg=?
            WHERE order_id=?
            """,
            (status, sms_arrived, now, error_msg, order_id),
        )
        await db.commit()


async def get_user_history(
    tg_id: int, limit: int = 5, offset: int = 0
) -> Tuple[List[Dict[str, Any]], int]:
    limit = max(1, min(int(limit), 50))
    offset = max(0, int(offset))
    async with get_connection() as db:
        async with db.execute(
            "SELECT COUNT(*) FROM sms_orders WHERE tg_id = ?", (tg_id,)
        ) as cursor:
            row = await cursor.fetchone()
            total = row[0] if row else 0
        import aiosqlite

        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM sms_orders WHERE tg_id = ? "
            "ORDER BY requested_at DESC LIMIT ? OFFSET ?",
            (tg_id, limit, offset),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows], total


async def get_stats() -> Dict[str, int]:
    async with get_connection() as db:
        async def _cnt(where: str) -> int:
            async with db.execute(
                f"SELECT COUNT(*) FROM sms_orders{where}"
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0

        return {
            "total": await _cnt(""),
            "success": await _cnt(" WHERE status='success'"),
            "failed": await _cnt(" WHERE status='failed'"),
            "cancelled": await _cnt(" WHERE status='cancelled'"),
            "pending": await _cnt(" WHERE status='pending'"),
        }


# ---------------------------------------------------------------------------
# Cliente HeroSMS (API heredada estilo SMS-Activate)
# ---------------------------------------------------------------------------

class SmsError(Exception):
    """Error del proveedor de SMS (clave, saldo, stock, formato o tiempo)."""


async def _hero_request(action: str, **params: Any) -> Any:
    """API heredada oficial de HeroSMS. Nunca propaga la clave en errores."""
    import json

    key = _api_key()
    if not key:
        raise SmsError("El proveedor de SMS no está configurado (HEROSMS_API_KEY)")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                HEROSMS_BASE,
                params={**params, "action": action, "api_key": key},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as response:
                if response.status >= 400:
                    raise SmsError(f"El proveedor de SMS respondió {response.status}")
                raw = (await response.text()).strip()
    except (aiohttp.ClientError, asyncio.TimeoutError):
        raise SmsError("No se pudo conectar con el proveedor de SMS") from None
    try:
        data = json.loads(raw)
    except ValueError:
        data = raw
    if isinstance(data, dict) and (data.get("title") or data.get("status") == "error"):
        raise SmsError("El proveedor de SMS rechazó la solicitud")
    if isinstance(data, str) and not data.startswith(("ACCESS_", "STATUS_")):
        raise SmsError("El proveedor de SMS rechazó la solicitud")
    return data


def _hero_order_id(order_id: str) -> str:
    if not order_id.startswith("hero-") or not order_id[5:].isdigit():
        raise SmsError("El pedido no pertenece a HeroSMS")
    return order_id[5:]


async def api_balance() -> str:
    """Devuelve el saldo del proveedor como texto, o 'error'."""
    try:
        data = await _hero_request("getBalance")
        if not isinstance(data, str) or not data.startswith("ACCESS_BALANCE:"):
            raise SmsError("Saldo inválido")
        return data.split(":", 1)[1]
    except SmsError as exc:
        logger.warning("sms: saldo no disponible: %s", exc)
        return "error"


async def api_stock(service: str, country: int) -> int:
    """Cuántos números hay disponibles para servicio+país."""
    data = await _hero_request("getPrices", service=service, country=country)
    offer = data.get(str(country), {}).get(str(service), {})
    return int(offer.get("count", 0))


async def api_buy(service: str, country: int, max_price: str) -> Dict[str, Any]:
    """Compra un número. Devuelve {order_id, number, expires_in}."""
    data = await _hero_request(
        "getNumber", service=service, country=country, maxPrice=max_price
    )
    if not isinstance(data, str) or not data.startswith("ACCESS_NUMBER:"):
        raise SmsError("Compra inválida")
    _, activation_id, number = data.split(":", 2)
    if not activation_id.isdigit() or not number.isdigit():
        raise SmsError("Número inválido")
    return {
        "order_id": f"hero-{activation_id}",
        "number": number,
        "expires_in": _ORDER_DURATION,
    }


async def api_check(order_id: str) -> Dict[str, Any]:
    """Consulta el estado. Devuelve {status, sms, time_left}.

    status 3 = código recibido (sms trae el código).
    status 1 = esperando. status 6 = cancelado.
    """
    activation_id = _hero_order_id(order_id)
    data = await _hero_request("getStatus", id=activation_id)
    if not isinstance(data, str):
        raise SmsError("Estado inválido")
    if data.startswith("STATUS_OK:"):
        return {"status": 3, "sms": data.split(":", 1)[1], "time_left": 0}
    if data == "STATUS_CANCEL":
        return {"status": 6, "sms": "", "time_left": 0}
    if data in ("STATUS_WAIT_CODE", "STATUS_WAIT_RESEND") or data.startswith(
        "STATUS_WAIT_RETRY:"
    ):
        info = _ACTIVE.get(order_id, {})
        remaining = max(
            0, int(info.get("expires_at", _time.time()) - _time.time())
        )
        return {"status": 1, "sms": "", "time_left": remaining}
    raise SmsError("Estado desconocido")


async def api_set_status(order_id: str, action: str) -> None:
    """ action: 'cancel' | 'resend' | 'finish'. """
    status = {"cancel": 8, "resend": 3, "finish": 6}[action]
    activation_id = _hero_order_id(order_id)
    data = await _hero_request("setStatus", id=activation_id, status=status)
    expected = {8: "ACCESS_CANCEL", 3: "ACCESS_RETRY_GET", 6: "ACCESS_ACTIVATION"}[
        status
    ]
    if data != expected:
        raise SmsError("El proveedor no confirmó el cambio de estado")


async def api_cancel(order_id: str) -> None:
    await api_set_status(order_id, "cancel")


async def api_finish(order_id: str) -> None:
    await api_set_status(order_id, "finish")


# ---------------------------------------------------------------------------
# Servicios y países
# ---------------------------------------------------------------------------

async def fetch_services() -> List[Dict[str, Any]]:
    """Lista de servicios [{ID, name}]. Cache en memoria de 6h."""
    global _services_cache, _services_cached_at
    now = datetime.now(timezone.utc)
    if _services_cached_at and (now - _services_cached_at).total_seconds() < 6 * 3600:
        return _services_cache
    try:
        response = await _hero_request("getServicesList", lang="en")
        data = [
            {"ID": str(item["code"]), "name": item["name"]}
            for item in response.get("services", [])
        ]
        if data:
            _services_cache = data
            _services_cached_at = now
    except Exception as exc:
        logger.warning("sms: no se pudo actualizar lista de servicios: %s", exc)
    return _services_cache


async def fetch_countries(service_id: str) -> List[Dict[str, Any]]:
    """Países con stock y precio para un servicio. MX va primero.

    Con caché de 10 min por servicio: la primera consulta tarda (requests
    a HeroSMS), las siguientes son instantáneas.
    """
    now = datetime.now(timezone.utc)
    cached = _countries_cache.get(service_id)
    if cached and (now - cached[0]).total_seconds() < _COUNTRIES_TTL_S:
        return cached[1]
    try:
        prices, countries = await asyncio.gather(
            _hero_request("getPrices", service=service_id),
            _hero_request("getCountries"),
        )
    except Exception as exc:
        logger.warning("sms: países para servicio %s: %s", service_id, exc)
        # Si falla y hay caché vieja, devolverla antes que vacío
        if cached:
            return cached[1]
        return []
    country_items = countries.values() if isinstance(countries, dict) else countries
    country_map = {int(c["id"]): c for c in country_items}
    suggested = []
    for country_id, services in prices.items():
        offer = services.get(str(service_id), {})
        if int(offer.get("count", 0)) <= 0 or float(offer.get("cost", 0)) <= 0:
            continue
        cid = int(country_id)
        country = country_map.get(cid, {})
        name = country.get("eng") or country.get("name") or str(cid)
        suggested.append(
            {
                "country_id": cid,
                "name": name,
                "short_name": (
                    "MX" if name.lower() in ("mexico", "méxico") else ""
                ),
                "price": str(offer["cost"]),
                "success_rate": "?",
            }
        )
    suggested.sort(key=lambda c: (c["short_name"] != "MX", float(c["price"])))
    _countries_cache[service_id] = (datetime.now(timezone.utc), suggested)
    return suggested


# ---------------------------------------------------------------------------
# Créditos: conversión USD → créditos + tramos manuales por rango
# ---------------------------------------------------------------------------

# Tramos por defecto (MXN → créditos). Cubren hasta $200 MXN para que
# ningún país con stock quede bloqueado por defecto. El panel /batman
# (Rangos de precios) permite cambiar cada tramo sin tocar código.
# Clave: piso del tramo en MXN. Se usa el tramo más alto cuyo piso
# no supere el precio. Ej: $22.2 → tramo 20 → 40 cr.
DEFAULT_CREDIT_TIERS: Dict[int, int] = {
    0: 10,    # $0–$5
    6: 20,   # $6–$10
    11: 30,  # $11–$15
    16: 40,  # $16–$20
    21: 50,  # $21–$30
    31: 60,  # $31–$50
    51: 80,  # $51–$80
    81: 100,  # $81–$120
    121: 150,  # $121–$200
}


async def get_credit_tiers() -> Dict[int, int]:
    """Tramos vigentes: manuales de sms_tiers si existen, si no los default."""
    async with get_connection() as db:
        try:
            async with db.execute(
                "SELECT piso_mxn, creditos FROM sms_tiers ORDER BY piso_mxn"
            ) as cursor:
                rows = await cursor.fetchall()
                if rows:
                    return {int(r[0]): int(r[1]) for r in rows}
        except Exception:
            pass
    return dict(DEFAULT_CREDIT_TIERS)


async def set_credit_tier(piso_mxn: int, creditos: int) -> Dict[int, int]:
    """Fija o actualiza un tramo. piso>=0, créditos 1–10000."""
    piso = int(piso_mxn)
    cr = int(creditos)
    if piso < 0 or cr < 1 or cr > 10000:
        raise ValueError("Tramo inválido (piso ≥ 0, créditos 1–10000)")
    async with get_connection() as db:
        await db.execute(
            "INSERT INTO sms_tiers (piso_mxn, creditos) VALUES (?, ?) "
            "ON CONFLICT(piso_mxn) DO UPDATE SET creditos=excluded.creditos",
            (piso, cr),
        )
        await db.commit()
    return await get_credit_tiers()


async def delete_credit_tier(piso_mxn: int) -> Dict[int, int]:
    """Elimina un tramo manual (vuelve al default para ese piso)."""
    async with get_connection() as db:
        try:
            await db.execute(
                "DELETE FROM sms_tiers WHERE piso_mxn = ?", (int(piso_mxn),)
            )
            await db.commit()
        except Exception:
            pass
    return await get_credit_tiers()

def usd_to_mxn_default() -> float:
    return float(os.getenv("SMS_USD_TO_MXN", "18.5"))


async def calc_credits(usd_price: str) -> int:
    """Convierte precio USD a créditos según los tramos vigentes.

    Usa los tramos manuales del panel si existen, si no los default.
    Siempre devuelve ≥10: ningún país con stock queda bloqueado.
    """
    try:
        usd = float(usd_price)
        rate = float((await _get_config("usd_to_mxn", "18.5")) or "18.5")
        mxn = usd * rate
        tiers = await get_credit_tiers()
        best = 10
        for piso in sorted(tiers):
            if mxn >= piso:
                best = tiers[piso]
        return best
    except Exception:
        pass
    return 10


def bracket_key(usd_price: float) -> str:
    lo = math.floor(usd_price * 10) / 10
    return f"{lo:.2f}"


async def refund_credits(
    admin_id: int, user_id: int, credits: int, reason: str
) -> int:
    """Devuelve créditos. admin_id=0 = reembolso automático del sistema."""
    if credits <= 0:
        return await get_balance(user_id)
    try:
        new_balance = await add_credits(user_id, credits, reason, admin_id or 0)
        return new_balance
    except Exception as exc:
        logger.error("sms: error al reembolsar créditos user %s: %s", user_id, exc)
        return await get_balance(user_id)


# ---------------------------------------------------------------------------
# Helpers de estado (para el handler)
# ---------------------------------------------------------------------------

def flow_get(user_id: int) -> Dict[str, Any]:
    return _FLOW.get(user_id, {})


def flow_set(user_id: int, **kwargs: Any) -> None:
    _FLOW.setdefault(user_id, {}).update(kwargs)


def flow_pop(user_id: int) -> Dict[str, Any]:
    return _FLOW.pop(user_id, {})


def active_get(order_id: str) -> Dict[str, Any]:
    return _ACTIVE.get(order_id, {})


def active_orders() -> Dict[str, Dict[str, Any]]:
    return _ACTIVE


# ---------------------------------------------------------------------------
# Polling (asyncio, sin job_queue — este proyecto usa aiogram puro)
# ---------------------------------------------------------------------------

async def _deliver_code(bot: Any, order_id: str, code: str) -> None:
    """Código recibido: finaliza en HeroSMS, marca éxito y lo entrega."""
    try:
        await api_finish(order_id)
    except Exception as exc:
        logger.warning("sms: no se pudo finalizar %s: %s", order_id, exc)
    info = _ACTIVE.pop(order_id, {})
    await _update_order_status(order_id, "success", sms_arrived=1)

    text = (
        "✅ <b>¡Llegó tu código!</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"📞 Número: <code>{info.get('phone', '')}</code>\n"
        f"🔑 Código: <code>{code}</code>\n"
        f"🌐 {info.get('country_name', '')} — {info.get('service_name', '')}\n"
        f"🎫 Ticket: <code>{order_id}</code>"
    )
    try:
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔙 Inicio", callback_data="sms:home"
                    ),
                    InlineKeyboardButton(
                        text="❌ Cerrar", callback_data="sms:close"
                    ),
                ]
            ]
        )
        await bot.edit_message_text(
            text,
            chat_id=info["chat_id"],
            message_id=info["message_id"],
            reply_markup=markup,
        )
    except Exception as exc:
        logger.error(
            "sms: no se pudo entregar código a user %s: %s",
            info.get("user_id"),
            exc,
        )


async def _fail_order(bot: Any, order_id: str, reason: str) -> None:
    """Sin código a tiempo: última verificación, cancelar y reembolsar."""
    try:
        result = await api_check(order_id)
        code_val = str(result.get("sms") or "")
        if result.get("status") == 3 or (
            code_val and code_val not in ("0", "", "None")
        ):
            if code_val and code_val not in ("0", "", "None"):
                await _deliver_code(bot, order_id, code_val)
                return
    except Exception:
        pass

    try:
        result = await api_check(order_id)
        if result.get("status") != 6:
            await api_cancel(order_id)
    except Exception as exc:
        logger.warning("sms: cancelación pendiente %s: %s", order_id, exc)
        # Reprogramar el fallo para no dejar el pedido colgado
        asyncio.create_task(_poll_loop(bot, order_id, extra_delay=60))
        return

    info = _ACTIVE.pop(order_id, {})
    await _update_order_status(order_id, "failed", error_msg=reason)

    user_id = info.get("user_id")
    username = info.get("username", "")
    credits = info.get("credits", 0)
    service = info.get("service_name", "")
    await refund_credits(
        0, user_id, credits, f"Reembolso automático — SMS no recibido ({order_id})"
    )

    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    text = (
        "⏰ <b>Pedido expirado</b>\n"
        "━━━━━━━━━━━━━━\n"
        "No llegó ningún código a tiempo.\n"
        f"💳 Tus <b>{credits} créditos</b> fueron devueltos.\n"
        f"🎫 Ticket: <code>{order_id}</code>"
    )
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔁 Intentar de nuevo", callback_data="sms:services:0"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 Inicio", callback_data="sms:home"
                ),
                InlineKeyboardButton(
                    text="❌ Cerrar", callback_data="sms:close"
                ),
            ],
        ]
    )
    try:
        await bot.edit_message_text(
            text,
            chat_id=info.get("chat_id"),
            message_id=info.get("message_id"),
            reply_markup=markup,
        )
    except Exception as exc:
        logger.error(
            "sms: no se pudo notificar fallo user %s: %s", user_id, exc
        )


def _code_valid(code_val: str) -> bool:
    return bool(code_val and code_val not in ("0", "", "None"))


def code_valid(code_val: str) -> bool:
    """Valida un código recibido (público para el handler)."""
    return _code_valid(code_val)


def cancel_window() -> int:
    """Segundos para cancelar tras la compra (público para el handler)."""
    return _CANCEL_WINDOW


async def ensure_user_order(
    tg_id: int,
    username: str,
    order_id: str,
    service_id: str,
    service_name: str,
    country_id: int,
    country_name: str,
    phone_number: str,
    credits_charged: int,
    usd_cost: str,
) -> None:
    """Guarda el pedido garantizando que el usuario exista (FK users)."""
    await ensure_user(tg_id)
    await _save_order(
        tg_id=tg_id, username=username, order_id=order_id,
        service_id=service_id, service_name=service_name,
        country_id=country_id, country_name=country_name,
        phone_number=phone_number, credits_charged=credits_charged,
        usd_cost=usd_cost,
    )


async def mark_cancelled(order_id: str) -> None:
    """Marca un pedido como cancelado (público para el handler)."""
    await _update_order_status(order_id, "cancelled")


async def deliver_code(bot: Any, order_id: str, code: str) -> None:
    """Entrega un código recibido (público para el botón Verificar ahora)."""
    await _deliver_code(bot, order_id, code)


async def _poll_loop(bot: Any, order_id: str, extra_delay: int = 0) -> None:
    """Bucle de polling: cada 15s consulta HeroSMS hasta código o timeout."""
    if extra_delay:
        await asyncio.sleep(extra_delay)
    info = _ACTIVE.get(order_id)
    if not info:
        return
    grace_attempt = 0

    while True:
        await asyncio.sleep(_POLL_INTERVAL)
        if order_id not in _ACTIVE:
            return
        try:
            result = await api_check(order_id)
        except Exception as exc:
            logger.warning("sms: poll error %s: %s", order_id, exc)
            continue

        status = result.get("status")
        time_left = int(result.get("time_left") or 0)
        code_val = str(result.get("sms") or "")

        if status == 3 or _code_valid(code_val):
            if not _code_valid(code_val):
                # Status 3 pero código vacío: retry en 5s
                await asyncio.sleep(5)
                if order_id in _ACTIVE:
                    try:
                        retry = await api_check(order_id)
                        retry_code = str(retry.get("sms") or "")
                        if _code_valid(retry_code):
                            await _deliver_code(bot, order_id, retry_code)
                            return
                    except Exception:
                        pass
                continue
            await _deliver_code(bot, order_id, code_val)
            return

        if time_left <= 0 or status not in (1, 3):
            if grace_attempt < len(_GRACE_DELAYS):
                await asyncio.sleep(_GRACE_DELAYS[grace_attempt])
                grace_attempt += 1
                continue
            await _fail_order(bot, order_id, "Tiempo agotado sin recibir código")
            return

        # Actualizar cuenta regresiva en el mensaje
        try:
            from bot.handlers.sms import active_text, active_markup

            await bot.edit_message_text(
                active_text(
                    info["phone"],
                    info["service_name"],
                    info["country_name"],
                    time_left,
                    False,
                ),
                chat_id=info["chat_id"],
                message_id=info["message_id"],
                reply_markup=active_markup(order_id, False),
            )
        except Exception:
            pass


def start_polling(bot: Any, order_id: str, info: Dict[str, Any]) -> None:
    """Registra el pedido y arranca su polling en background."""
    _ACTIVE[order_id] = info
    info["task"] = asyncio.create_task(_poll_loop(bot, order_id))


def stop_polling(order_id: str) -> Optional[Dict[str, Any]]:
    """Detiene el polling y devuelve la info del pedido (o None)."""
    info = _ACTIVE.pop(order_id, None)
    task = (info or {}).get("task")
    if task and not task.done():
        task.cancel()
    return info


async def reconcile_orphans(bot: Any = None) -> int:
    """Pedidos 'pending' de >25 min: último check, cancelar y reembolsar.

    Llamar ~30s después del arranque (igual que alfa1).
    Devuelve cuántos se reconciliaron.
    """
    cutoff = (
        datetime.now(timezone.utc) - timedelta(minutes=_MAX_ORDER_AGE_MIN)
    ).isoformat()
    async with get_connection() as db:
        import aiosqlite

        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT order_id, tg_id, username, credits_charged, service_name "
            "FROM sms_orders "
            "WHERE status='pending' AND requested_at < ?",
            (cutoff,),
        ) as cursor:
            rows = await cursor.fetchall()

    count = 0
    for row in rows:
        order_id = row["order_id"]
        if not order_id.startswith("hero-") or order_id in _ACTIVE:
            continue
        try:
            result = await api_check(order_id)
            status = result.get("status")
            code_val = str(result.get("sms") or "")
            if status == 3 or _code_valid(code_val):
                await _update_order_status(order_id, "success", sms_arrived=1)
                logger.info("sms: reconciliación exitosa %s", order_id)
                count += 1
                continue
            if status != 6:
                await api_cancel(order_id)
        except Exception as exc:
            logger.warning("sms: reconciliación pendiente %s: %s", order_id, exc)
            continue
        await _update_order_status(
            order_id, "failed", error_msg="Reconciliación: timeout en reinicio"
        )
        await refund_credits(
            0,
            row["tg_id"],
            row["credits_charged"],
            f"Reembolso reconciliación reinicio ({order_id})",
        )
        logger.info(
            "sms: reconciliación fallida %s — %d cr reembolsados",
            order_id,
            row["credits_charged"],
        )
        count += 1
    return count
