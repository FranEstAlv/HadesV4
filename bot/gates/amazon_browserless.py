"""Proveedor de sesión de Amazon MX mediante navegador real (Browserless/BQL).

POR QUÉ EXISTE ESTE MÓDULO
-------------------------
El gate `/amz` hace el flujo de tarjeta en 5 pasos HTTP (Prime → register →
identify → addcard → CVV). El paso 1 (el "entry" de Prime) **no** se puede
conseguir con una cookie guardada: Amazon responde con un redirect a

    https://www.amazon.com.mx/ap/signin?openid.pape.max_auth_age=0&...

`max_auth_age=0` significa "la autenticación debe ser de esta misma sesión".
Medido el 2026-10-21 contra las 4 variantes de URL del entry (`/hp/wlp/...`,
`/gp/prime/...`, con `offerToken`, con `primeSignupFulfillmentType`) **todas**
terminan en ese redirect: sin `ppw-widgetState` ni `customerID`. Da igual que
la cookie sea de hace 10 minutos o de hace 3 meses.

La única forma de cruzar el muro es autenticarse de verdad y llegar al entry
**por dentro de la misma sesión**. Eso es exactamente lo que sabe hacer un
navegador real, y es lo que hacen los runners de
`evidencias/browserless-snapshot/` (login real + exportación de cookies +
confirmación de `ppw-widgetState` en el DOM: verificado el 2026-10-10 en
`output/step_prime_fresh_20261010_061604/` → `hasWidget=true`, `hasCustomer=true`,
`finalUrl=.../hp/wlp/pipeline/membersignup?campaignId=SlashPrime`).

Este módulo es el runner de Browserless convertido en pieza del bot:
- mismo endpoint (`{BROWSERLESS_URL}/stealth/bql`),
- mismos parámetros (`proxy=residential`, `proxyCountry`, `proxySticky`,
  `proxyLocaleMatch`, `humanlike`, `blockAds`, `blockConsentModals`, `timeout`),
- mismo payload (`{"query": ..., "variables": {...}}`),
- misma rotación de tokens ante 429.

Lo que aporta sobre los runners de CLI:
- devuelve un objeto con la sesión ya parseada (cookie, widgetState,
  customerID) en vez de archivos en disco,
- cachea la sesión en memoria para que `/amzmass` no pague un login por tarjeta,
- clasifica el muro (suspendida / OTP / WAF / login fallido) para que el gate
  pueda dar un mensaje honesto en vez de "error genérico".
"""
from __future__ import annotations

import html as html_lib
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

# Raíz del proyecto (bot/gates/amazon_browserless.py -> bot/gates -> bot -> raíz)
BASE_DIR = Path(__file__).resolve().parent.parent.parent
QUERIES_DIR = Path(__file__).resolve().parent / "queries"
EVIDENCIAS_ENV = BASE_DIR / "evidencias" / "browserless-snapshot" / ".env"
ACCOUNTS_DIR = BASE_DIR / "data" / "amazon_accounts"

DEFAULT_BROWSERLESS_URL = "https://production-sfo.browserless.io"
DEFAULT_PROXY_COUNTRY = "mx"
DEFAULT_TIMEOUT_MS = 120000

# Entry de Prime que Amazon.mx usa para "registrar método de pago con Prime".
PRIME_ENTRY_URL = (
    "https://www.amazon.com.mx/hp/wlp/pipeline/membersignup?campaignId=SlashPrime"
)

# Segundos que una sesión de navegador aguanta en caché antes de renovarse.
SESSION_TTL_S = 600

# Muros conocidos (texto visible / URL), para clasificar sin adivinar.
_WALL_SUSPENDED_URL = "account-status.amazon.com.mx"
_WALL_SUSPENDED_TEXT = (
    "su cuenta de amazon está suspendida",
    "your account has been suspended",
    "revoke-account-suspension",
)
_WALL_OTP_TEXT = (
    "enter the characters you see below",
    "ingresa los caracteres",
    "escribe los caracteres",
    "código de verificación",
    "codigo de verificacion",
    "verifica tu identidad",
    "te enviamos un código",
)
# CVF = Amazon pide verificar el número de celular con un SMS.
_WALL_CVF_TEXT = (
    "confirma tu identidad",
    "verifica el número de celular",
    "verifica el numero de celular",
    "código de seguridad que te enviamos",
    "codigo de seguridad que te enviamos",
    "too many otp",
    "demasiadas otp",
)
_WALL_WAF_TEXT = (
    "api-services-support@amazon.com",
    "to discuss automated access to amazon data",
)


class BrowserlessError(RuntimeError):
    """Error de comunicación con Browserless (no de negocio)."""


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
def _read_env_file(path: Path) -> Dict[str, str]:
    """Lee un .env sin tocar os.environ (el .env del bot manda si existe)."""
    values: Dict[str, str] = {}
    if not path.is_file():
        return values
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                values[key] = value
    except OSError as exc:
        logger.warning("No se pudo leer %s: %s", path, exc)
    return values


def _env(name: str, default: str = "") -> str:
    """Busca la variable en el entorno y, si no está, en el .env de evidencias."""
    import os

    value = os.getenv(name, "").strip()
    if value:
        return value
    return _read_env_file(EVIDENCIAS_ENV).get(name, default).strip()


# ---------------------------------------------------------------------------
# Cliente BrowserQL
# ---------------------------------------------------------------------------
class BrowserlessClient:
    """Cliente mínimo del endpoint /stealth/bql de Browserless.

    Reproduce la mecánica de `evidencias/browserless-snapshot/snapshot_runner.py`
    y `step_runner.py` (incluida la rotación de claves ante HTTP 429).
    """

    def __init__(
        self,
        token: Optional[str] = None,
        base_url: Optional[str] = None,
        proxy_country: Optional[str] = None,
        timeout_ms: Optional[int] = None,
    ) -> None:
        self.token = token if token is not None else _env("BROWSERLESS_TOKEN")
        self.base_url = (
            base_url
            if base_url is not None
            else (_env("BROWSERLESS_URL") or DEFAULT_BROWSERLESS_URL)
        ).rstrip("/")
        self.proxy_country = (
            proxy_country
            if proxy_country is not None
            else (_env("BROWSERLESS_PROXY_COUNTRY") or DEFAULT_PROXY_COUNTRY)
        )
        try:
            self.timeout_ms = int(
                timeout_ms if timeout_ms is not None else _env("BROWSERLESS_TIMEOUT_MS") or DEFAULT_TIMEOUT_MS
            )
        except (TypeError, ValueError):
            self.timeout_ms = DEFAULT_TIMEOUT_MS

    # -- estado ----------------------------------------------------------
    @property
    def available(self) -> bool:
        return bool(self.token)

    @property
    def tokens(self) -> List[str]:
        return [t.strip() for t in (self.token or "").split(",") if t.strip()]

    def endpoint(self) -> str:
        return f"{self.base_url}/stealth/bql"

    def params(self, token: str) -> Dict[str, str]:
        return {
            # Proxy residencial nativo de Browserless
            "proxy": "residential",
            # País del proxy (Amazon MX necesita IP mexicana)
            "proxyCountry": self.proxy_country,
            # Mantener la identidad de proxy durante la sesión
            "proxySticky": "true",
            # Alinear locale/timezone con el proxy
            "proxyLocaleMatch": "true",
            # Interacción humana (evita el score de bot)
            "humanlike": "true",
            # Menos ruido de publicidad/rastreadores
            "blockAds": "true",
            # Quitar overlays de cookies/consentimiento
            "blockConsentModals": "true",
            # Límite de la sesión
            "timeout": str(self.timeout_ms),
            "token": token,
        }

    # -- ejecución -------------------------------------------------------
    def run_bql(
        self,
        query: str,
        variables: Dict[str, object],
        timeout_s: int = 150,
    ) -> Dict:
        """Ejecuta una query BrowserQL y devuelve el JSON completo de la API."""
        if not self.available:
            raise BrowserlessError("No hay BROWSERLESS_TOKEN configurado.")

        payload = {"query": query, "variables": variables}
        last_error: Optional[requests.Response] = None

        for token in self.tokens:
            try:
                response = requests.post(
                    self.endpoint(),
                    params=self.params(token),
                    headers={"Content-Type": "application/json"},
                    json=payload,
                    timeout=timeout_s,
                )
            except requests.RequestException as exc:
                raise BrowserlessError(f"Error de red hacia Browserless: {exc}") from exc

            if response.status_code == 429:
                # Límite de concurrencia: se prueba la siguiente clave.
                last_error = response
                continue

            if response.status_code >= 400:
                raise BrowserlessError(
                    f"Browserless respondió {response.status_code}: {response.text[:400]}"
                )

            try:
                return response.json()
            except ValueError as exc:
                raise BrowserlessError("Browserless devolvió una respuesta que no es JSON.") from exc

        if last_error is not None:
            raise BrowserlessError(
                "Límite de concurrencia de Browserless alcanzado (429) con todas las "
                f"claves disponibles. Última respuesta: {last_error.text[:200]}"
            )
        raise BrowserlessError("No se pudo completar la solicitud a Browserless.")

    def run_query_file(self, filename: str, variables: Dict[str, object]) -> Dict:
        query_path = QUERIES_DIR / filename
        if not query_path.is_file():
            raise BrowserlessError(f"No existe la query: {query_path}")
        query = query_path.read_text(encoding="utf-8")
        return self.run_bql(query, variables)


# ---------------------------------------------------------------------------
# Sesión de Amazon obtained en navegador
# ---------------------------------------------------------------------------
@dataclass
class AmazonBrowserSession:
    """Sesión de Prime obtenida en un Chromium real."""

    status: str = "unknown"          # ok | suspended | otp | waf | signin | error
    detail: str = ""
    cookie: str = ""
    cookies: Dict[str, str] = field(default_factory=dict)
    widget_state: str = ""
    customer_id: str = ""
    final_url: str = ""
    title: str = ""
    html: str = ""
    account: str = ""
    created_at: float = field(default_factory=time.time)

    @property
    def usable(self) -> bool:
        """True si la sesión sirve para procesar tarjetas."""
        return self.status == "ok" and bool(self.widget_state) and bool(self.customer_id)


def _clean_html(text: str) -> str:
    """Amazon entrega el markup del widget con doble escape; hay que desescapar."""
    for _ in range(3):
        decoded = html_lib.unescape(text)
        if decoded == text:
            break
        text = decoded
    return text


def extract_widget_state(html: str) -> str:
    """Saca el ppw-widgetState del HTML del entry de Prime."""
    cleaned = _clean_html(html or "")
    match = re.search(r'name="ppw-widgetState" value="(.*?)"', cleaned)
    if match:
        return match.group(1)
    # Variante con el atributo en otro orden
    match = re.search(r'value="([^"]*)"[^>]*name="ppw-widgetState"', cleaned)
    return match.group(1) if match else ""


def extract_customer_id(html: str) -> str:
    """Saca el customerID del HTML de Prime."""
    cleaned = _clean_html(html or "")
    match = re.search(r'"customerID"\s*:\s*"([^"]+)"', cleaned)
    if match:
        return match.group(1)
    match = re.search(r'customerId\s*:\s*["\']([^"\']+)["\']', cleaned, re.IGNORECASE)
    return match.group(1) if match else ""


def _cookies_to_string(cookies: List[Dict[str, str]]) -> str:
    """Arma el header `cookie` a partir de la lista de cookies del navegador."""
    pairs = []
    for cookie in cookies or []:
        name = (cookie.get("name") or "").strip()
        value = cookie.get("value")
        if not name or value is None:
            continue
        pairs.append(f"{name}={value}")
    return "; ".join(pairs)


def _classify_wall(final_url: str, body_text: str) -> Tuple[str, str]:
    """Clasifica el muro que impidió obtener la sesión de Prime."""
    url_low = (final_url or "").lower()
    text_low = (body_text or "").lower()

    if _WALL_SUSPENDED_URL in url_low or any(t in text_low for t in _WALL_SUSPENDED_TEXT):
        return (
            "suspended",
            "Amazon redirigió a la pantalla de suspensión de cuenta. "
            "La cuenta usada para el sesión está suspendida.",
        )
    if any(t in text_low for t in _WALL_WAF_TEXT):
        return (
            "waf",
            "Amazon mostró el muro de WAF automatizado (api-services-support).",
        )
    if any(t in text_low for t in _WALL_CVF_TEXT) or "/ap/cvf" in url_low:
        return (
            "cvf",
            "Amazon pide verificar el número de celular (CVF) para iniciar sesión.",
        )
    if any(t in text_low for t in _WALL_OTP_TEXT):
        return (
            "otp",
            "Amazon pidió un código de verificación (OTP/CAPTCHA) después del login.",
        )
    if "/ap/signin" in url_low:
        return (
            "signin",
            "Amazon volvió a pedir iniciar sesión: la sesión no quedó establecida.",
        )
    return ("unknown", "")


def _body_text(html: str) -> str:
    """Texto plano del HTML, sin scripts (para clasificar muros)."""
    if not html:
        return ""
    cleaned = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    cleaned = re.sub(r"(?s)<[^>]+>", " ", cleaned)
    return html_lib.unescape(cleaned)


def parse_session_payload(
    result: Dict,
    account: str = "",
) -> AmazonBrowserSession:
    """Convierte la respuesta BQL en una sesión parseada."""
    data = result.get("data") or {}
    errors = result.get("errors") or []

    session = AmazonBrowserSession(account=account)

    if errors:
        first = errors[0]
        message = first.get("message", "") if isinstance(first, dict) else str(first)
        session.status = "error"
        session.detail = f"Error de BrowserQL: {message}"
        return session

    def _value(key: str) -> str:
        node = data.get(key)
        if isinstance(node, dict):
            return str(node.get("value", ""))
        return ""

    session.final_url = _value("finalUrl")
    session.title = (data.get("pageTitle") or {}).get("text", "") if isinstance(data.get("pageTitle"), dict) else ""
    body_text = _value("bodyText")

    raw_cookies = data.get("sessionCookies")
    cookies_list: List[Dict[str, str]] = []
    if isinstance(raw_cookies, dict):
        cookies_list = raw_cookies.get("cookies") or []
    elif isinstance(raw_cookies, list):
        cookies_list = raw_cookies
    session.cookies = {
        (c.get("name") or ""): (c.get("value") or "")
        for c in cookies_list
        if c.get("name")
    }
    session.cookie = _cookies_to_string(cookies_list)

    # El widgetState y el customerID llegan ya extraídos por `evaluate`
    # (el HTML completo de Prime pesa ~1 MB y no cabe en la sesión de 120 s).
    session.widget_state = _value("widgetState")
    session.customer_id = _value("customerId")
    if not session.widget_state and session.html:
        session.widget_state = extract_widget_state(session.html)
    if not session.customer_id and session.html:
        session.customer_id = extract_customer_id(session.html)

    if session.widget_state and session.customer_id:
        session.status = "ok"
        session.detail = "Sesión de Prime lista (widgetState + customerID)."
        return session

    status, detail = _classify_wall(session.final_url, body_text)
    session.status = status
    session.detail = detail or (
        "El navegador no encontró ppw-widgetState/customerID en la página de Prime."
    )
    return session


# ---------------------------------------------------------------------------
# Cuentas disponibles
# ---------------------------------------------------------------------------
def list_local_accounts(limit: int = 10, country: str = "MX") -> List[Dict[str, str]]:
    """Cuentas creadas por /gencookie guardadas en data/amazon_accounts/.

    Se ordenan por fecha de creación (no por nombre de archivo) y se prefieren
    las del país del gate. Se descartan las que tienen `warning` (suspendida,
    dirección fallida, etc.).
    """
    if not ACCOUNTS_DIR.is_dir():
        return []

    entries: List[Tuple[str, Dict[str, str]]] = []
    for path in ACCOUNTS_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue

        email = str(data.get("email") or "").strip()
        password = str(data.get("password") or "").strip()
        if not email or not password:
            continue
        # Una cuenta con `warning` no sirve (suspendida, dirección fallida, etc.).
        if str(data.get("warning") or "").strip():
            continue

        account = {
            "email": email,
            "password": password,
            "country": str(data.get("country") or ""),
            "created": str(data.get("created") or ""),
            "source": str(path),
        }
        entries.append((str(data.get("created") or ""), account))

    entries.sort(key=lambda item: item[0], reverse=True)

    ordered: List[Dict[str, str]] = []
    if country:
        ordered.extend(acc for _, acc in entries if acc["country"].upper() == country.upper())
    ordered.extend(acc for _, acc in entries if acc["country"].upper() != country.upper())
    return ordered[:limit]


# ---------------------------------------------------------------------------
# Caché de sesión
# ---------------------------------------------------------------------------
_cache_lock = threading.Lock()
_session_cache: Dict[str, Tuple[float, AmazonBrowserSession]] = {}


def _cache_get(key: str) -> Optional[AmazonBrowserSession]:
    with _cache_lock:
        entry = _session_cache.get(key)
        if not entry:
            return None
        expires_at, session = entry
        if expires_at < time.time():
            _session_cache.pop(key, None)
            return None
        return session


def _cache_put(key: str, session: AmazonBrowserSession) -> None:
    with _cache_lock:
        _session_cache[key] = (time.time() + SESSION_TTL_S, session)


def clear_cache() -> None:
    with _cache_lock:
        _session_cache.clear()


# ---------------------------------------------------------------------------
# API principal
# ---------------------------------------------------------------------------
def open_prime_session(
    login: str,
    password: str,
    client: Optional[BrowserlessClient] = None,
    entry_url: str = PRIME_ENTRY_URL,
) -> AmazonBrowserSession:
    """Hace login en el navegador real y devuelve la sesión de Prime.

    Sincrónico a propósito: el gate lo llama dentro de `run_in_executor`.
    """
    client = client or BrowserlessClient()
    if not client.available:
        return AmazonBrowserSession(
            status="error",
            detail="Browserless no está configurado (falta BROWSERLESS_TOKEN).",
            account=login,
        )

    variables = {"url": entry_url, "login": login, "password": password}
    try:
        result = client.run_query_file("amazon_wallet_session.graphql", variables)
    except BrowserlessError as exc:
        logger.warning("Browserless falló en la sesión de Prime: %s", exc)
        return AmazonBrowserSession(status="error", detail=str(exc), account=login)

    session = parse_session_payload(result, account=login)
    logger.info(
        "Browserless: sesión para %s -> status=%s widget=%s cid=%s cookies=%d",
        login,
        session.status,
        bool(session.widget_state),
        bool(session.customer_id),
        len(session.cookies),
    )
    return session


def acquire_session(
    account: Optional[Dict[str, str]] = None,
    client: Optional[BrowserlessClient] = None,
    force: bool = False,
) -> AmazonBrowserSession:
    """Devuelve una sesión de Prime, reutilizando la caché cuando se puede.

    `account` = {"email": ..., "password": ...}. Si no se pasa, se toma la
    cuenta local más reciente sin `warning`.
    """
    client = client or BrowserlessClient()
    if not client.available:
        return AmazonBrowserSession(
            status="error",
            detail="Browserless no está configurado (falta BROWSERLESS_TOKEN).",
        )

    if account is None:
        accounts = list_local_accounts(limit=1)
        if not accounts:
            return AmazonBrowserSession(
                status="error",
                detail=(
                    "No hay cuentas de Amazon disponibles para abrir sesión. "
                    "Genera una con /gencookie."
                ),
            )
        account = accounts[0]

    email = (account.get("email") or "").strip()
    password = (account.get("password") or "").strip()
    if not email or not password:
        return AmazonBrowserSession(
            status="error", detail="La cuenta no tiene correo ni contraseña."
        )

    cache_key = f"{client.proxy_country}:{email}"
    if not force:
        cached = _cache_get(cache_key)
        if cached is not None:
            logger.info("Browserless: reusando sesión en caché para %s", email)
            return cached

    session = open_prime_session(email, password, client=client)
    if session.usable:
        _cache_put(cache_key, session)
    return session
