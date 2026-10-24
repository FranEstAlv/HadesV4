"""Fábrica central de proxies residenciales IPRoyal.

Lee únicamente el bloque unificado de ``.env``:

    IPROYAL_USERNAME
    IPROYAL_PASSWORD_BASE
    IPROYAL_EXTRA_ARGS
    IPROYAL_HOST
    IPROYAL_PORT
    IPROYAL_PROTOCOL
    IPROYAL_STICKY_COUNTRIES / IPROYAL_STICKY_LIFETIME
    IPROYAL_ROTATING_COUNTRIES
    IPROYAL_POOL_COUNTRIES / IPROYAL_POOL_STICKY_LIFETIME

Las variables antiguas (``IPROYAL_USER``, ``IPROYAL_PASS``) solo se aceptan
como respaldo para no romper scripts viejos. Todo el código nuevo debe usar
las funciones de este módulo en vez de armar la cadena a mano.
"""
from __future__ import annotations

import os
import random
import secrets
import string
from typing import Iterable, List, Optional

from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------------------
# Lectura de configuración
# ---------------------------------------------------------------------------
def _first_env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip() != "":
            return value.strip()
    return default


def _csv_env(name: str, default: str) -> List[str]:
    raw = _first_env(name, default=default)
    raw = raw.replace("'", "").replace('"', "")
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


USERNAME = _first_env("IPROYAL_USERNAME", "IPROYAL_USER")
PASSWORD_BASE = _first_env("IPROYAL_PASSWORD_BASE", "IPROYAL_PASS")
EXTRA_ARGS = _first_env("IPROYAL_EXTRA_ARGS", default="")
PROTOCOL = _first_env("IPROYAL_PROTOCOL", default="http").lower()

# Los pool "rot" y el sticky del gate son strings completas que vienen del
# .env (IPROYAL_ROTATORY_URL / IPROYAL_STICKY_MX_URL); no se arman por partes.
ROTATORY_URL = _first_env("IPROYAL_ROTATORY_URL", default="")
STICKY_MX_URL = _first_env("IPROYAL_STICKY_MX_URL", default="")

# El .env nuevo separa host y puerto. La variable vieja podía traer
# "geo.iproyal.com:12321": se acepta, pero IPROYAL_PORT tiene prioridad.
_host_raw = _first_env("IPROYAL_HOST", default="geo.iproyal.com")
_host, _, _host_port = _host_raw.partition(":")
HOST = _host or "geo.iproyal.com"
PORT = _first_env("IPROYAL_PORT", default=_host_port or "11245")

STICKY_COUNTRIES = _csv_env("IPROYAL_STICKY_COUNTRIES", "mx,us")
ROTATING_COUNTRIES = _csv_env("IPROYAL_ROTATING_COUNTRIES", "mx,us")
POOL_COUNTRIES = _csv_env("IPROYAL_POOL_COUNTRIES", "mx,us,ca,br,es,it")

STICKY_LIFETIME = _first_env("IPROYAL_STICKY_LIFETIME", default="2m")
POOL_STICKY_LIFETIME = _first_env(
    "IPROYAL_POOL_STICKY_LIFETIME", default=STICKY_LIFETIME
)

# País sticky que usan los gates /di /ph /eu /wo /rd y sus masivos.
GATE_STICKY_COUNTRY = _first_env("IPROYAL_GATE_COUNTRY", default="mx").lower()

# El generador Amazon identifica Reino Unido como "UK"; IPRoyal como "gb".
_ACCOUNT_TO_IPROYAL = {"UK": "gb"}
_IPROYAL_TO_ACCOUNT = {"gb": "UK"}


def proxy_country(country: str) -> str:
    """Normaliza una clave del generador (UK) al código que espera IPRoyal (gb)."""
    code = (country or "").strip().upper()
    return _ACCOUNT_TO_IPROYAL.get(code, code.lower())


def account_country(code: str) -> str:
    """Normaliza un código IPRoyal (gb) a la clave del generador Amazon (UK)."""
    low = (code or "").strip().lower()
    return _IPROYAL_TO_ACCOUNT.get(low, low.upper())


def account_countries() -> List[str]:
    """Países del pool internacional, en el orden configurado en el .env."""
    return [account_country(code) for code in POOL_COUNTRIES]


# ---------------------------------------------------------------------------
# Construcción de la cadena
# ---------------------------------------------------------------------------
def make_proxy(
    *,
    country: Optional[str] = None,
    countries: Optional[Iterable[str]] = None,
    sticky: bool = False,
    lifetime: Optional[str] = None,
) -> Optional[str]:
    """Arma ``http://user:pass_sufijos@host:port``.

    Los sufijos de IPRoyal van en el password:
    ``_country-xx``, opcionalmente ``_session-xxxxxxxx_lifetime-Nm``.
    Devuelve ``None`` si faltan credenciales.
    """
    if not USERNAME or not PASSWORD_BASE:
        return None

    if countries:
        codes = [proxy_country(code) for code in countries if code]
        if not codes:
            return None
        location = "_country-" + ",".join(codes)
    elif country:
        location = f"_country-{proxy_country(country)}"
    else:
        raise ValueError("Debes indicar country o countries")

    password = f"{PASSWORD_BASE}{location}"
    if sticky:
        session = "".join(
            secrets.choice(string.ascii_letters + string.digits) for _ in range(8)
        )
        password += f"_session-{session}_lifetime-{lifetime or STICKY_LIFETIME}"
    password += EXTRA_ARGS

    return f"{PROTOCOL}://{USERNAME}:{password}@{HOST}:{PORT}"


def gate_proxy(pool: str = "mx") -> Optional[str]:
    """Proxy para un gate.

    pool='mx'  -> sticky MX con sesion fija de 10m (string completa del .env).
    pool='rot' -> rotatorio random (string completa del .env).
    """
    pool = (pool or "mx").lower()
    if pool == "rot":
        if ROTATORY_URL:
            return ROTATORY_URL
        choices = ROTATING_COUNTRIES or STICKY_COUNTRIES or ["mx"]
        return make_proxy(country=random.choice(choices), sticky=False)
    if STICKY_MX_URL:
        return STICKY_MX_URL
    choices = STICKY_COUNTRIES or ["mx"]
    country = GATE_STICKY_COUNTRY if GATE_STICKY_COUNTRY in choices else choices[0]
    return make_proxy(country=country, sticky=True, lifetime=STICKY_LIFETIME)


def gate_proxies(pool: str = "mx", count: int = 8) -> List[str]:
    """Varios proxies para pools que necesitan una lista (Playwright, etc.)."""
    result: List[str] = []
    for _ in range(max(1, count)):
        url = gate_proxy(pool)
        if url:
            result.append(url)
    return result


def account_proxy(country: str) -> Optional[str]:
    """Proxy sticky del pool internacional para crear una cuenta Amazon."""
    return make_proxy(
        country=country,
        sticky=True,
        lifetime=POOL_STICKY_LIFETIME,
    )


def pool_sticky_proxy() -> Optional[str]:
    """Un proxy sticky que rota entre todos los países del pool."""
    if not POOL_COUNTRIES:
        return None
    country = random.choice(POOL_COUNTRIES)
    return make_proxy(country=country, sticky=True, lifetime=POOL_STICKY_LIFETIME)


def pool_rotating_proxy() -> Optional[str]:
    """Un proxy rotatorio del pool internacional (sin sesión fija)."""
    if not POOL_COUNTRIES:
        return None
    country = random.choice(POOL_COUNTRIES)
    return make_proxy(country=country, sticky=False)
