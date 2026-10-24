"""Login de Amazon MX por HTTP (sin navegador) para el gate `/amz`.

POR QUÉ
-------
El entry de Prime exige `openid.pape.max_auth_age=0`: la autenticación tiene
que ser de esa misma sesión. Medido el 2026-10-21, con una cookie guardada
Amazon redirige SIEMPRE a `/ap/signin` y nunca devuelve `ppw-widgetState`
(probado con 4 variantes de URL del entry y 3 cookies reales). Por eso el
gate necesita **abrir sesión de verdad**, y para eso hay dos caminos:

1. Este módulo: login HTTP con `curl_cffi` (rápido, ~10 s, escala a lotes).
2. `bot/gates/amazon_browserless.py`: login en un Chromium real (lento,
   ~90 s, pero pasa los retos de JavaScript).

Los dos dejan la sesión lista para el paso 1 del gate (el GET del entry de
Prime), que es el que devuelve `ppw-widgetState` + `customerID`.

Muros que este módulo detecta y clasifica:
  - cvf             -> Amazon pide verificar el celular (SMS). Se puede
                       completar con `submit_otp_code()`.
  - otp             -> pide un código por correo u otro canal.
  - suspended       -> cuenta suspendida.
  - bad_credentials -> correo o contraseña incorrectos.
  - waf             -> muro de AWS WAF.
  - signin          -> la sesión no quedó establecida.
"""
from __future__ import annotations

import html as html_lib
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote, urljoin

logger = logging.getLogger(__name__)

# País del gate. `handle` es el assoc_handle de OpenID que Amazon usa en el
# formulario de inicio de sesión (ver bot/services/amazon_gen/accountCreator.py).
COUNTRIES: Dict[str, Dict[str, str]] = {
    "MX": {"domain": "amazon.com.mx", "handle": "amzn_wlpmember_mx", "lang": "es-MX,es;q=0.9,en;q=0.8"},
    "US": {"domain": "amazon.com", "handle": "amzn_wlpmember_us", "lang": "en-US,en;q=0.9,es;q=0.8"},
}

PRIME_ENTRY_URL = (
    "https://www.amazon.com.mx/hp/wlp/pipeline/membersignup?campaignId=SlashPrime"
)

_UA_DESKTOP = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)

_MARKERS_SUSPENDED = (
    "su cuenta de amazon está suspendida",
    "your account has been suspended",
    "revoke-account-suspension",
    "account-status.amazon",
)
_MARKERS_BAD_CREDENTIALS = (
    "contraseña incorrecta",
    "clave incorrecta",
    "incorrect password",
    "no existe una cuenta con este correo",
    "your password is incorrect",
)
_MARKERS_WAF = (
    "api-services-support@amazon.com",
    "to discuss automated access to amazon data",
)
_MARKERS_OTP = (
    "código de verificación",
    "codigo de verificacion",
    "te enviamos un código",
    "enter the characters you see below",
    "ingresa los caracteres",
)


@dataclass
class LoginResult:
    """Resultado de intentar abrir sesión con Amazon."""

    status: str = "unknown"      # ok | cvf | otp | suspended | bad_credentials | waf | signin | error
    detail: str = ""
    cookie: str = ""
    session: object = None       # sesión curl_cffi viva (para seguir el flujo)
    phone_suffix: str = ""       # últimos dígitos del celular (CVF)
    otp_form: Optional[Dict[str, str]] = None   # action + campos para el código
    country: str = "MX"
    email: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"


@dataclass
class PrimeSession:
    """Sesión de Prime lista para procesar tarjetas."""

    status: str = "unknown"
    detail: str = ""
    cookie: str = ""
    widget_state: str = ""
    customer_id: str = ""
    source: str = "http"
    login: LoginResult = field(default_factory=LoginResult)

    @property
    def usable(self) -> bool:
        return self.status == "ok" and bool(self.widget_state) and bool(self.customer_id)


# ---------------------------------------------------------------------------
# Utilidades de parseo
# ---------------------------------------------------------------------------
def _body_text(html: str) -> str:
    if not html:
        return ""
    cleaned = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    cleaned = re.sub(r"(?s)<[^>]+>", " ", cleaned)
    return " ".join(html_lib.unescape(cleaned).split())


def _clean_html(text: str) -> str:
    for _ in range(3):
        decoded = html_lib.unescape(text)
        if decoded == text:
            break
        text = decoded
    return text


def extract_widget_state(html: str) -> str:
    cleaned = _clean_html(html or "")
    match = re.search(r'name="ppw-widgetState" value="(.*?)"', cleaned)
    if match:
        return match.group(1)
    match = re.search(r'value="([^"]*)"[^>]*name="ppw-widgetState"', cleaned)
    return match.group(1) if match else ""


def extract_customer_id(html: str) -> str:
    cleaned = _clean_html(html or "")
    match = re.search(r'"customerID"\s*:\s*"([^"]+)"', cleaned)
    if match:
        return match.group(1)
    match = re.search(r'customerId\s*:\s*["\']([^"\']+)["\']', cleaned, re.IGNORECASE)
    return match.group(1) if match else ""


def _parse_form(html: str, form_id: str = "ap_login_form") -> Optional[Dict[str, str]]:
    """Saca action + campos ocultos del formulario de login."""
    match = re.search(
        r'(?is)<form[^>]*id="%s"[^>]*action="([^"]*)"[^>]*>(.*?)</form>' % re.escape(form_id),
        html,
    )
    if not match:
        return None

    action = html_lib.unescape(match.group(1))
    fields: Dict[str, str] = {}
    for inp in re.finditer(r"(?is)<input([^>]*)>", match.group(2)):
        attrs = inp.group(1)
        name = re.search(r'name="([^"]*)"', attrs)
        if not name:
            continue
        value = re.search(r'value="([^"]*)"', attrs)
        typ = re.search(r'type="([^"]*)"', attrs)
        if typ and typ.group(1) in ("submit", "button", "checkbox"):
            continue
        fields[name.group(1)] = html_lib.unescape(value.group(1)) if value else ""
    return {"action": action, "fields": fields}


def _parse_cvf_form(html: str) -> Optional[Dict[str, str]]:
    """Saca el formulario de verificación por código (CVF).

    En Amazon MX el formulario se llama `verification-code-form`, tiene
    `action="verify"` (relativo a /ap/cvf/) y un input de texto `code` más
    el `verifyToken` que hay que reenviar tal cual.
    """
    for match in re.finditer(r'(?is)<form([^>]*)>(.*?)</form>', html or ""):
        attrs = match.group(1)
        body = match.group(2)
        action = re.search(r'action="([^"]*)"', attrs)
        has_code = re.search(r'name="code"', body) is not None
        form_id = re.search(r'id="([^"]*)"', attrs)
        if not has_code and not (form_id and "verification" in form_id.group(1)):
            continue
        fields: Dict[str, str] = {}
        for inp in re.finditer(r"(?is)<input([^>]*)>", body):
            attrs_i = inp.group(1)
            name = re.search(r'name="([^"]*)"', attrs_i)
            if not name:
                continue
            value = re.search(r'value="([^"]*)"', attrs_i)
            typ = re.search(r'type="([^"]*)"', attrs_i)
            if typ and typ.group(1) in ("submit", "button", "checkbox"):
                continue
            fields[name.group(1)] = html_lib.unescape(value.group(1)) if value else ""
        return {
            "action": html_lib.unescape(action.group(1)) if action else "verify",
            "fields": fields,
        }
    return None


def _cookies_to_string(session) -> str:
    """Arma el header `cookie` con las cookies de la sesión.

    OJO: en curl_cffi `session.cookies` es un dict-like (iterarlo da los
    NOMBRES, no objetos Cookie). Los objetos Cookie están en `.jar`, igual
    que en requests. Por eso se usa `.jar` cuando existe.
    """
    jar = getattr(session.cookies, "jar", None)
    source = jar if jar is not None else session.cookies
    pairs = []
    try:
        for c in source:
            name = getattr(c, "name", None)
            value = getattr(c, "value", None)
            if name and value:
                pairs.append(f"{name}={value}")
    except Exception:
        return ""
    return "; ".join(pairs)


def _build_signin_url(entry_url: str, country: str = "MX") -> str:
    """Arma la URL OpenID de inicio de sesión que usa el entry de Prime."""
    info = COUNTRIES.get(country.upper(), COUNTRIES["MX"])
    return (
        f"https://www.{info['domain']}/ap/signin?openid.pape.max_auth_age=0"
        f"&openid.return_to={quote(entry_url, safe='')}"
        "&openid.identity=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select"
        f"&openid.assoc_handle={info['handle']}"
        "&openid.mode=checkid_setup&language=es_MX"
        "&openid.claimed_id=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select"
        "&openid.ns=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0"
    )


def _classify(url: str, html: str) -> Tuple[str, str]:
    """Clasifica la pantalla en la que quedó Amazon."""
    url_low = (url or "").lower()
    text_low = _body_text(html or "").lower()

    if any(m in url_low for m in _MARKERS_SUSPENDED) or any(m in text_low for m in _MARKERS_SUSPENDED):
        return "suspended", "Amazon indica que la cuenta está suspendida."
    if "/ap/cvf" in url_low or "confirma tu identidad" in text_low or "verifica el número de celular" in text_low:
        return "cvf", "Amazon pide verificar el número de celular (CVF) para entrar."
    if any(m in url_low for m in _MARKERS_WAF) or any(m in text_low for m in _MARKERS_WAF):
        return "waf", "Amazon mostró el muro de WAF automatizado."
    if any(m in text_low for m in _MARKERS_BAD_CREDENTIALS):
        return "bad_credentials", "Amazon rechazó el correo o la contraseña."
    if any(m in text_low for m in _MARKERS_OTP):
        return "otp", "Amazon pidió un código de verificación."
    if "/ap/signin" in url_low or "ap/signin" in text_low:
        return "signin", "Amazon volvió a pedir iniciar sesión."
    return "unknown", ""


def _extract_phone_suffix(text: str) -> str:
    """Saca los últimos dígitos del celular que muestra la pantalla de CVF."""
    match = re.search(r"termina en\s+([\d\s\-]{2,})", text or "", re.IGNORECASE)
    if match:
        digits = re.sub(r"\D", "", match.group(1))
        return digits[-4:] if digits else ""
    return ""


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------
def signin(
    email: str,
    password: str,
    proxies: Optional[dict] = None,
    country: str = "MX",
    entry_url: str = PRIME_ENTRY_URL,
    session=None,
    timeout: int = 30,
) -> LoginResult:
    """Inicia sesión en Amazon por HTTP.

    Devuelve un `LoginResult`. Si `status == "ok"` la sesión quedó abierta
    y el `cookie` sirve para el paso 1 del gate.
    """
    from bot.gates.amazon import _new_session  # import diferido: evita ciclo

    info = COUNTRIES.get(country.upper(), COUNTRIES["MX"])
    result = LoginResult(country=country.upper(), email=email)

    if session is None:
        session = _new_session()
        if proxies:
            session.proxies = dict(proxies)
    result.session = session

    headers = {
        "User-Agent": _UA_DESKTOP,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
            "image/webp,image/apng,*/*;q=0.8"
        ),
        "Accept-Language": info["lang"],
        "Upgrade-Insecure-Requests": "1",
    }

    signin_url = _build_signin_url(entry_url, country)
    try:
        page = session.get(signin_url, headers=headers, timeout=timeout)
    except Exception as exc:
        result.status = "error"
        result.detail = f"Error de red al abrir la pantalla de login: {exc}"
        return result

    if page.status_code >= 500:
        result.status = "error"
        result.detail = f"Amazon respondió {page.status_code} al pedir el login."
        return result

    form = _parse_form(page.text)
    if not form:
        status, detail = _classify(page.url, page.text)
        result.status = status if status != "unknown" else "error"
        result.detail = detail or "No se encontró el formulario de login de Amazon."
        result.cookie = _cookies_to_string(session)
        return result

    fields = dict(form["fields"])
    fields["email"] = email
    fields["password"] = password
    fields.setdefault("appAction", "SIGNIN_CLAIM_COLLECT")
    fields.setdefault("subPageType", "FullPageUnifiedClaimCollect")
    fields.setdefault("claimCollectionWorkflow", "unified")
    fields.setdefault("metadata1", "true")
    fields.setdefault("isServerSideRouting", "true")
    fields.setdefault("emailOnlyClaim", "false")

    action = urljoin(f"https://www.{info['domain']}", form["action"])
    try:
        posted = session.post(
            action,
            data=fields,
            headers={
                **headers,
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": f"https://www.{info['domain']}",
                "Referer": signin_url,
            },
            timeout=timeout,
        )
    except Exception as exc:
        result.status = "error"
        result.detail = f"Error de red al enviar el login: {exc}"
        return result

    result.cookie = _cookies_to_string(session)
    body = _body_text(posted.text)
    url_low = (posted.url or "").lower()

    # El login funcionó si la página ya no pide iniciar sesión.
    if "ap/signin" not in url_low and "/ap/cvf" not in url_low and "contraseña incorrecta" not in body.lower():
        result.status = "ok"
        result.detail = "Sesión de Amazon abierta por HTTP."
        return result

    status, detail = _classify(posted.url, posted.text)
    result.status = status
    result.detail = detail

    if status == "cvf":
        result.phone_suffix = _extract_phone_suffix(body)
        cvf_form = _parse_cvf_form(posted.text)
        if cvf_form:
            result.otp_form = cvf_form

    return result


def submit_otp_code(
    login_result: LoginResult,
    code: str,
    proxies: Optional[dict] = None,
    timeout: int = 30,
) -> LoginResult:
    """Completa el CVF/OTP con el código recibido por SMS.

    Solo funciona si `login_result.otp_form` está poblado (el gate lo llena
    cuando Amazon pide verificar el celular).
    """
    if not login_result.otp_form or not login_result.session:
        return LoginResult(
            status="error",
            detail="No hay formulario de código disponible para esta sesión.",
        )

    info = COUNTRIES.get(login_result.country, COUNTRIES["MX"])
    fields = dict(login_result.otp_form["fields"])
    fields["code"] = code
    fields.setdefault("action", "code")
    fields.setdefault("metadata1", "true")
    fields.setdefault("verificationPageContactType", "sms")
    action = urljoin(f"https://www.{info['domain']}", login_result.otp_form["action"])

    headers = {
        "User-Agent": _UA_DESKTOP,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": info["lang"],
        "Content-Type": "application/x-www-form-urlencoded",
    }

    try:
        posted = login_result.session.post(
            action, data=fields, headers=headers, timeout=timeout
        )
    except Exception as exc:
        return LoginResult(status="error", detail=f"Error de red al enviar el código: {exc}")

    body = _body_text(posted.text)
    if "código incorrecto" in body.lower() or "codigo incorrecto" in body.lower():
        return LoginResult(
            status="error",
            detail="Amazon rechazó el código de verificación.",
            session=login_result.session,
            cookie=_cookies_to_string(login_result.session),
        )

    return LoginResult(
        status="ok",
        detail="CVF completado con el código recibido.",
        session=login_result.session,
        cookie=_cookies_to_string(login_result.session),
        country=login_result.country,
        email=login_result.email,
    )


def open_prime_session(
    email: str,
    password: str,
    proxies: Optional[dict] = None,
    country: str = "MX",
    entry_url: str = PRIME_ENTRY_URL,
    otp_code: Optional[str] = None,
    timeout: int = 30,
) -> PrimeSession:
    """Abre sesión y llega al entry de Prime: devuelve widgetState + customerID."""
    info = COUNTRIES.get(country.upper(), COUNTRIES["MX"])
    headers = {
        "User-Agent": _UA_DESKTOP,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
            "image/webp,image/apng,*/*;q=0.8"
        ),
        "Accept-Language": info["lang"],
    }

    login = signin(
        email,
        password,
        proxies=proxies,
        country=country,
        entry_url=entry_url,
        timeout=timeout,
    )

    if login.status == "cvf" and otp_code:
        login = submit_otp_code(login, otp_code, proxies=proxies, timeout=timeout)

    session_out = PrimeSession(
        status=login.status,
        detail=login.detail,
        cookie=login.cookie,
        source="http",
        login=login,
    )

    if not login.ok or login.session is None:
        return session_out

    # Paso 1 del gate: el entry de Prime. Con la sesión recién abierta ya
    # NO debe redirigir a /ap/signin (muro max_auth_age=0).
    try:
        page = login.session.get(entry_url, headers=headers, timeout=timeout)
    except Exception as exc:
        session_out.status = "error"
        session_out.detail = f"Error de red al abrir el entry de Prime: {exc}"
        return session_out

    session_out.cookie = _cookies_to_string(login.session)
    session_out.widget_state = extract_widget_state(page.text)
    session_out.customer_id = extract_customer_id(page.text)

    if session_out.widget_state and session_out.customer_id:
        session_out.status = "ok"
        session_out.detail = "Sesión de Prime lista (widgetState + customerID)."
        return session_out

    status, detail = _classify(page.url, page.text)
    session_out.status = status if status != "unknown" else "error"
    session_out.detail = detail or (
        "El entry de Prime no trajo ppw-widgetState/customerID."
    )
    return session_out
