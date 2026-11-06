"""bot_cookie_old.py (reparado).

Origen: fragmento truncado en evidencias/cookies/bot_cookie_old.py (125 lineas,
sin importaciones ni funciones auxiliares, con lineas `---` que rompian la
sintaxis).
Reparacion basada en evidencias/ANALISIS_BOT_COOKIE_OLD.md:
- Se eliminan los separadores `---` que causaban SyntaxError.
- Se agregan importaciones, logger, configuracion por variables de entorno
  (sin credenciales hardcodeadas), pool HTTP, funciones de SMSPool y solver
  de captcha grid (referencia: bot_cookies.py).
- Se conserva la identidad del fragmento original: flujo create_amazon_account
  para Termux/ARM (headless, --no-sandbox, viewport movil 360x740, locale
  en-US, URL de registro amazon.com con assoc_handle usflex).
- Se corrige `wait_until='commit'` a `'domcontentloaded'` para que el DOM este
  listo antes de buscar #ap_customer_name.
"""

import asyncio
import base64
import logging
import os
import random
import re
import uuid
from urllib.parse import urlencode

import httpx
from playwright.async_api import async_playwright

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dotenv opcional
    def load_dotenv(*args, **kwargs):
        return False


def _load_project_env():
    """Carga el .env de la raiz del proyecto (GenCookie/GenCookie/.env).

    El script vive en evidencias/cookies/, asi que se buscan .env en los
    padres hasta 4 niveles arriba. Sin esto, os.getenv no ve las claves que
    se agregaron al .env y la prueba falla con 'API Key no configurada'.
    """
    here = os.path.abspath(__file__)
    for _ in range(5):
        here = os.path.dirname(here)
        candidate = os.path.join(here, ".env")
        if os.path.isfile(candidate):
            load_dotenv(candidate, override=False)
            return candidate
    load_dotenv(override=False)
    return ""


_PROJECT_ENV = _load_project_env()

# ==================== CONFIGURACION (por entorno, sin secretos quemados) ====================

SMSPOOL_API_KEY = os.getenv("SMSPOOL_API_KEY", "")
SMSPOOL_BASE_URL = os.getenv("SMSPOOL_BASE_URL", "https://api.smspool.net")
SMSPOOL_SERVICE_ID = os.getenv("SMSPOOL_SERVICE_ID", "39")
SMSPOOL_COUNTRY_CODE = os.getenv("SMSPOOL_COUNTRY_CODE", "US")

ANTICAPTCHA_API_KEY = os.getenv("ANTICAPTCHA_API_KEY", "")

# HeroSMS (respaldo si SMSPool falla o se queda sin saldo).
# Clave en .env como HEROSMS_API_KEY. API compatible SMS-Activate:
# GET https://hero-sms.com/stubs/handler_api.php?action=<acc>&api_key=<k>
HEROSMS_API_KEY = os.getenv("HEROSMS_API_KEY", "")
HEROSMS_BASE_URL = os.getenv(
    "HEROSMS_BASE_URL", "https://hero-sms.com/stubs/handler_api.php"
)
HEROSMS_SERVICE = os.getenv("HEROSMS_SERVICE", "am")  # am = Amazon
HEROSMS_COUNTRY_ID = os.getenv("HEROSMS_COUNTRY_ID", "187")  # 187 = US

PROXY_HOST = os.getenv("PROXY_HOST", "")
PROXY_PORT = os.getenv("PROXY_PORT", "")
PROXY_USER = os.getenv("PROXY_USER", "")
PROXY_PASS = os.getenv("PROXY_PASS", "")

# IPRoyal (nombres del .env del proyecto). Si PROXY_HOST no esta definido se
# construye el proxy desde aqui. Ver .env: IPROYAL_ROTATORY_URL (string
# completa) e IPROYAL_STICKY_MX_URL.
IPROYAL_ROTATORY_URL = os.getenv("IPROYAL_ROTATORY_URL", "")
IPROYAL_STICKY_MX_URL = os.getenv("IPROYAL_STICKY_MX_URL", "")
IPROYAL_HOST = os.getenv("IPROYAL_HOST", "")
IPROYAL_PORT = os.getenv("IPROYAL_PORT", "")
IPROYAL_USERNAME = os.getenv("IPROYAL_USERNAME", "") or os.getenv("IPROYAL_USER", "")
IPROYAL_PASSWORD = os.getenv("IPROYAL_PASSWORD_BASE", "") or os.getenv("IPROYAL_PASS", "")


def _parse_proxy_url(url):
    """Convierte 'http(s)://user:pass@host:port' a dict proxy de Playwright."""
    from urllib.parse import urlparse

    url = (url or "").strip().strip("'\"")
    if not url:
        return None
    # El .env puede traer varias URLs separadas por coma o salto de linea:
    # se usa la primera valida.
    first = re.split(r"[\s,]+", url)[0]
    try:
        parts = urlparse(first if "://" in first else f"http://{first}")
    except Exception:
        return None
    if not parts.hostname or not parts.port:
        return None
    cfg = {"server": f"http://{parts.hostname}:{parts.port}"}
    if parts.username:
        cfg["username"] = parts.username
    if parts.password:
        cfg["password"] = parts.password
    return cfg


def get_proxy_config():
    """Resuelve el proxy FORZOSO desde el entorno.

    Orden: PROXY_HOST/... -> IPROYAL_ROTATORY_URL -> IPROYAL_STICKY_MX_URL
    -> IPROYAL_HOST/PORT/USER/PASS. Sin proxy disponible retorna None y el
    flujo debe abortar ANTES de comprar numero o lanzar el navegador.
    """
    if PROXY_HOST:
        cfg = {"server": f"http://{PROXY_HOST}:{PROXY_PORT}"}
        if PROXY_USER:
            cfg["username"] = PROXY_USER
        if PROXY_PASS:
            cfg["password"] = PROXY_PASS
        return cfg
    for raw in (IPROYAL_ROTATORY_URL, IPROYAL_STICKY_MX_URL):
        cfg = _parse_proxy_url(raw)
        if cfg:
            return cfg
    if IPROYAL_HOST and IPROYAL_PORT:
        cfg = {"server": f"http://{IPROYAL_HOST}:{IPROYAL_PORT}"}
        if IPROYAL_USERNAME:
            cfg["username"] = IPROYAL_USERNAME
        if IPROYAL_PASSWORD:
            cfg["password"] = IPROYAL_PASSWORD
        return cfg
    return None

USER_AGENTS = [
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

AMAZON_REGISTER_URL = (
    "https://www.amazon.com/ap/register"
    "?openid.pape.max_auth_age=0"
    "&openid.return_to=https%3A%2F%2Fwww.amazon.com%2F"
    "&openid.identity=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select"
    "&openid.assoc_handle=usflex"
    "&openid.mode=checkid_setup"
    "&openid.claimed_id=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select"
    "&openid.ns=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0"
)

OTP_SELECTORS = [
    "#auth-pv-enter-code",
    'input[name="code"]',
    "#cvf_input_code",
    'input[type="tel"]',
]

# ==================== LOGGING ====================

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ==================== HTTP CLIENT POOL ====================

http_client = None


async def get_http_client():
    """Cliente HTTP global con connection pooling."""
    global http_client
    if http_client is None:
        http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=10.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return http_client


async def close_http_client():
    """Cierra el cliente HTTP global."""
    global http_client
    if http_client:
        await http_client.aclose()
        http_client = None


# ==================== SMSPOOL ====================

async def order_smspool_number():
    """Ordena un numero virtual en SMSPool. Retorna (order_id, full_number)."""
    if not SMSPOOL_API_KEY:
        logger.error("SMSPool API Key no configurada (SMSPOOL_API_KEY)")
        return None, None

    params = {
        "key": SMSPOOL_API_KEY,
        "country": SMSPOOL_COUNTRY_CODE,
        "service": SMSPOOL_SERVICE_ID,
        "pool": "",
        "max_price": "0.8",
    }
    url = f"{SMSPOOL_BASE_URL}/purchase/sms?{urlencode(params)}"

    try:
        client = await get_http_client()
        response = await client.post(url)
        if response.status_code == 200:
            data = response.json()
            logger.info(f"SMSPool order: {data}")
            if data.get("success") == 1 or "number" in data:
                order_id = data.get("order_id")
                phone_number = str(data.get("number", ""))
                country_code = str(data.get("cc", "1"))
                phone_number = phone_number.lstrip("0").lstrip("+")
                if len(phone_number) > 10:
                    phone_number = phone_number[-10:]
                full_number = f"+{country_code}{phone_number}"
                logger.info(f"Numero: {full_number}")
                return order_id, full_number
        return None, None
    except Exception as e:
        logger.error(f"Error ordenando numero: {e}")
        return None, None


async def check_smspool_sms(order_id):
    """Revisa si ya llego el SMS. Retorna codigo, 'CANCELLED' o None."""
    params = {"key": SMSPOOL_API_KEY, "orderid": order_id}
    url = f"{SMSPOOL_BASE_URL}/sms/check?{urlencode(params)}"

    try:
        client = await get_http_client()
        response = await client.post(url)
        if response.status_code == 200:
            data = response.json()
            status = data.get("status")

            if status == 3 or status == "3":
                sms_code = data.get("sms", "")
                if sms_code:
                    logger.info(f"SMS recibido RAW: {sms_code}")
                    code_match = re.search(r"\b\d{6,10}\b", str(sms_code))
                    if code_match:
                        extracted = code_match.group()
                        logger.info(f"Codigo extraido: {extracted}")
                        return extracted
                    return str(sms_code).strip()

            elif status == 2 or status == "2":
                logger.warning("Orden SMS cancelada")
                return "CANCELLED"

            elif status == 1 or status == "1":
                logger.info(f"SMS pendiente, tiempo restante: {data.get('time_left', 0)}s")
                return None

            else:
                sms_text = data.get("sms", "")
                if sms_text and len(str(sms_text)) > 0:
                    logger.info(f"SMS encontrado en respuesta: {sms_text}")
                    code_match = re.search(r"\b\d{6,10}\b", str(sms_text))
                    if code_match:
                        return code_match.group()
        return None
    except Exception as e:
        logger.error(f"Exception checking SMS: {e}")
        return None


async def cancel_smspool_order(order_id):
    """Cancela una orden de SMSPool."""
    if not order_id:
        return False
    params = {"key": SMSPOOL_API_KEY, "orderid": order_id}
    url = f"{SMSPOOL_BASE_URL}/sms/cancel?{urlencode(params)}"
    try:
        client = await get_http_client()
        response = await client.post(url)
        return response.status_code == 200 and response.json().get("success") == 1
    except Exception:
        return False


# ==================== HEROSMS (respaldo) ====================

async def order_herosms_number():
    """Compra un numero en HeroSMS. Retorna (activation_id, numero) o (None, None)."""
    if not HEROSMS_API_KEY:
        logger.warning("HEROSMS_API_KEY no configurada; sin respaldo HeroSMS")
        return None, None
    try:
        client = await get_http_client()
        resp = await client.get(
            HEROSMS_BASE_URL,
            params={
                "action": "getNumber",
                "api_key": HEROSMS_API_KEY,
                "service": HEROSMS_SERVICE,
                "country": HEROSMS_COUNTRY_ID,
            },
            timeout=25,
        )
        text = (resp.text or "").strip()
        logger.info(f"HeroSMS getNumber: {text[:80]}")
        if text.startswith("ACCESS_NUMBER:"):
            _, activation_id, number = text.split(":", 2)
            digits = re.sub(r"\D", "", number or "")
            return activation_id.strip(), digits
        logger.warning(f"HeroSMS no entrego numero: {text[:120]}")
        return None, None
    except Exception as e:
        logger.error(f"Error comprando numero HeroSMS: {e}")
        return None, None


async def check_herosms_sms(activation_id):
    """Revisa el codigo en HeroSMS. Retorna codigo, 'CANCELLED' o None."""
    try:
        client = await get_http_client()
        resp = await client.get(
            HEROSMS_BASE_URL,
            params={"action": "getStatus", "api_key": HEROSMS_API_KEY, "id": activation_id},
            timeout=25,
        )
        text = (resp.text or "").strip()
        if text.startswith("STATUS_OK:"):
            code = text.split(":", 1)[1].strip()
            match = re.search(r"\b\d{4,10}\b", code)
            return match.group() if match else code
        if text.startswith("STATUS_WAIT_RETRY:"):
            code = text.split(":", 1)[1].strip()
            match = re.search(r"\b\d{4,10}\b", code)
            return match.group() if match else (code or None)
        if text == "STATUS_CANCEL":
            return "CANCELLED"
        return None
    except Exception as e:
        logger.error(f"Error revisando SMS HeroSMS: {e}")
        return None


async def cancel_herosms_order(activation_id):
    """Cancela el pedido en HeroSMS (status=8)."""
    if not activation_id:
        return False
    try:
        client = await get_http_client()
        resp = await client.get(
            HEROSMS_BASE_URL,
            params={
                "action": "setStatus",
                "api_key": HEROSMS_API_KEY,
                "id": activation_id,
                "status": 8,
            },
            timeout=25,
        )
        return (resp.text or "").strip() == "ACCESS_CANCEL"
    except Exception:
        return False


async def order_phone_number():
    """Ordena numero: SMSPool primero, HeroSMS como respaldo.

    Retorna (proveedor, order_id, phone). proveedor en {'smspool','herosms'}.
    """
    order_id, phone = await order_smspool_number()
    if phone:
        return "smspool", order_id, phone
    logger.warning("SMSPool fallo o sin saldo; intentando HeroSMS...")
    activation_id, number = await order_herosms_number()
    if number:
        return "herosms", activation_id, number
    return None, None, None


async def check_phone_sms(provider, order_id):
    """Revisa el SMS segun el proveedor usado."""
    if provider == "herosms":
        return await check_herosms_sms(order_id)
    return await check_smspool_sms(order_id)


async def cancel_phone_order(provider, order_id):
    """Cancela el pedido segun el proveedor usado."""
    if provider == "herosms":
        return await cancel_herosms_order(order_id)
    return await cancel_smspool_order(order_id)


# ==================== CAPTCHA GRID ====================

async def solve_grid_captcha_anticaptcha(page, max_retries=5, status_callback=None):
    """Resuelve el captcha de grid con Anti-Captcha. Retorna True/False."""

    async def update(msg):
        if status_callback:
            try:
                await status_callback(msg)
            except Exception:
                pass

    object_translations = {
        "sillas": "chair", "silla": "chair", "autobuses": "bus", "autobús": "bus",
        "autobus": "bus", "motocicletas": "motorcycle", "motocicleta": "motorcycle",
        "motos": "motorcycle", "moto": "motorcycle", "bicicletas": "bicycle",
        "bicicleta": "bicycle", "bici": "bicycle", "semáforos": "traffic light",
        "semaforos": "traffic light", "semáforo": "traffic light",
        "semaforo": "traffic light", "coches": "car", "coche": "car",
        "autos": "car", "auto": "car", "carros": "car", "carro": "car",
        "camiones": "truck", "camión": "truck", "camion": "truck",
        "aviones": "airplane", "avión": "airplane", "avion": "airplane",
        "barcos": "boat", "barco": "boat", "trenes": "train", "tren": "train",
        "taxis": "taxi", "taxi": "taxi",
    }

    if not ANTICAPTCHA_API_KEY:
        logger.error("ANTICAPTCHA_API_KEY no configurada")
        return False

    client = await get_http_client()

    for attempt in range(max_retries):
        try:
            await update(f"Captcha intento {attempt + 1}/{max_retries}...")
            canvas = await page.query_selector(
                "#captcha-container canvas, #root canvas, .amzn-captcha-modal canvas"
            )
            if not canvas:
                return True

            question_text = await page.evaluate("""() => {
                const selectors = ['.amzn-captcha-modal em', '.amzn-captcha-modal strong',
                    '#captcha-container em', '.a-text-bold', '.cvf-widget-form-captcha em'];
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (el && el.textContent) return el.textContent.trim();
                }
                return '';
            }""")

            target_object = (question_text or "").lower().strip()
            english_object = object_translations.get(target_object, target_object)

            canvas_box = await canvas.bounding_box()
            if not canvas_box:
                continue

            screenshot_bytes = await page.screenshot(clip={
                "x": max(0, canvas_box["x"] - 5),
                "y": max(0, canvas_box["y"] - 5),
                "width": canvas_box["width"] + 10,
                "height": canvas_box["height"] + 10,
            })
            image_base64 = base64.b64encode(screenshot_bytes).decode("utf-8")

            create_payload = {
                "clientKey": ANTICAPTCHA_API_KEY,
                "task": {
                    "type": "ImageToCoordinatesTask",
                    "body": image_base64,
                    "comment": f"Click on objects: {english_object}",
                    "mode": "points",
                },
            }
            response = await client.post(
                "https://api.anti-captcha.com/createTask", json=create_payload
            )
            result = response.json()
            if result.get("errorId") != 0:
                continue
            task_id = result.get("taskId")

            for _ in range(20):
                await asyncio.sleep(3)
                check_response = await client.post(
                    "https://api.anti-captcha.com/getTaskResult",
                    json={"clientKey": ANTICAPTCHA_API_KEY, "taskId": task_id},
                )
                check_result = check_response.json()
                if check_result.get("status") == "ready":
                    coordinates = check_result.get("solution", {}).get("coordinates", [])
                    for coord in coordinates:
                        rel_x = (coord.get("x") if isinstance(coord, dict) else coord) - 5
                        rel_y = (coord.get("y") if isinstance(coord, dict) else coord) - 5
                        click_x = canvas_box["x"] + max(0, min(rel_x, canvas_box["width"]))
                        click_y = canvas_box["y"] + max(0, min(rel_y, canvas_box["height"]))
                        await page.mouse.click(click_x, click_y)
                        await asyncio.sleep(0.5)
                    for sel in [
                        "#amzn-btn-verify-internal",
                        'button:has-text("Verify")',
                        "#cvf-submit-captcha",
                        'button[type="submit"]',
                    ]:
                        try:
                            confirm_btn = await page.query_selector(sel)
                            if confirm_btn and await confirm_btn.is_visible():
                                await confirm_btn.click()
                                await asyncio.sleep(3)
                                break
                        except Exception:
                            continue
                    return True
        except Exception as e:
            logger.error(f"Error captcha: {e}")
    return False


# ==================== CREACION DE CUENTA ====================

async def create_amazon_account(status_callback=None):
    """Crea una cuenta de Amazon con numero virtual de SMSPool.

    Retorna dict con phone/password/fullname/cookies, o None si falla.
    No realiza ningun cargo ni integra nada al bot: es el flujo original del
    fragmento, ahora con dependencias completas y sintaxis valida.
    """

    async def update_status(msg):
        if status_callback:
            await status_callback(msg)

    await update_status("Ordenando numero de telefono...")
    provider, order_id, phone = await order_phone_number()
    if not phone:
        await update_status("Error: sin saldo o sin numeros en SMSPool y HeroSMS")
        return None

    password = f"Pass{random.randint(1000, 9999)}{uuid.uuid4().hex[:8]}"
    fullname = f"John {''.join(random.choices('abcdefg', k=5)).capitalize()}"

    proxy_config = get_proxy_config()
    if not proxy_config:
        logger.error(
            "Sin proxy disponible: PROXY_HOST ni IPRoyal configurados. "
            "Se aborta: Amazon exige proxy residencial."
        )
        await update_status("Sin proxy configurado: flujo abortado")
        return None
    logger.info(f"Proxy forzoso: {proxy_config.get('server')}")

    browser = None
    try:
        async with async_playwright() as p:
            # Configuracion para compatibilidad ARM/Termux (sin sandbox ni GPU).
            browser_args = [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-gpu",
            ]
            browser = await p.chromium.launch(headless=True, args=browser_args)

            context = await browser.new_context(
                user_agent=random.choice(USER_AGENTS),
                viewport={"width": 360, "height": 740},
                locale="en-US",
                proxy=proxy_config,
            )
            page = await context.new_page()

            try:
                await update_status("Navegando a Amazon Register...")
                await page.goto(
                    AMAZON_REGISTER_URL,
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                logger.info("Page goto OK")
                await asyncio.sleep(3)

                # EVIDENCIA 2026-11-02 (capturas humo_register.png y
                # error_bloqueo_old.png + query
                # evidencias/browserless-snapshot/queries/login_identifier.graphql):
                # ap/register redirige a ax/claim con flujo identifier-first:
                # titulo "Sign in", sin #ap_customer_name / #ap_email /
                # #ap_password. El paso 1 es escribir el telefono en el campo
                # de identificador y pulsar Continue; el nombre/password se
                # piden DESPUES (si Amazon ofrece crear cuenta para ese
                # numero). Selectores verificados: #ap_email_login y #continue.
                identifier_selectors = [
                    "#ap_email_login",
                    'input[name="email"]',
                    "#ap_email",
                ]
                identifier = None
                for sel in identifier_selectors:
                    try:
                        if await page.query_selector(sel):
                            identifier = sel
                            break
                    except Exception:
                        continue

                if identifier:
                    phone_digits = re.sub(r"\D", "", phone)[-10:]
                    await page.fill(identifier, phone_digits)
                    logger.info(f"Identificador OK ({identifier})")
                    cont = await page.query_selector("#continue, input[type=\"submit\"]")
                    if cont:
                        await cont.click()
                    logger.info("#continue identificador OK")
                    await asyncio.sleep(5)

                    # EVIDENCIA 2026-11-03 (error_bloqueo_old.png de la corrida):
                    # tras el identificador Amazon NO muestra directo
                    # #ap_customer_name. Con numero NUEVO muestra pantalla
                    # intermedia "Looks like you're new..." con boton amarillo
                    # "Proceed to create an account". Con numero RECICLADO
                    # (cuenta ya existente) muestra directo "Amazon password"
                    # (#ap_password): no es registro, se cancela sin esperar.
                    # Sin clic en Proceed el flujo muere aqui. Se detecta por
                    # texto (el id varia) y se pulsa antes de buscar
                    # #ap_customer_name. NO se pulsa #continue generico: eso
                    # re-dispara el identificador y esconde la rama real.
                    try:
                        # Fast-path cuenta existente: si ya hay #ap_password
                        # visible, abortar sin buscar customer_name (ahorra
                        # ~24 s de waits).
                        try:
                            _pw = await page.query_selector("#ap_password")
                            _pw_vis = await _pw.is_visible() if _pw else False
                        except Exception:
                            _pw, _pw_vis = None, False
                        if _pw and _pw_vis:
                            logger.warning("La cuenta ya existe (pide password): no es registro nuevo")
                            await page.screenshot(path="error_bloqueo_old.png")
                            await update_status("El numero ya tiene cuenta: se cancela el pedido")
                            await cancel_phone_order(provider, order_id)
                            return None
                        proceed_text = False
                        try:
                            proceed_text = await page.evaluate("""() => {
                                const b = (document.body.innerText || '').toLowerCase();
                                return b.includes('proceed to create an account')
                                    || b.includes(\"looks like you're new to amazon\");
                            }""")
                        except Exception:
                            proceed_text = False
                        proceed_clicked = False
                        if proceed_text:
                            for sel in [
                                'button:has-text("Proceed to create an account")',
                                'a:has-text("Proceed to create an account")',
                                'span:has-text("Proceed to create an account")',
                                'input:has-text("Proceed to create an account")',
                            ]:
                                try:
                                    btn = await page.query_selector(sel)
                                    if btn:
                                        try:
                                            visible = await btn.is_visible()
                                        except Exception:
                                            visible = True
                                        if visible:
                                            await btn.click()
                                            proceed_clicked = True
                                            logger.info(f"Proceed OK ({sel})")
                                            break
                                except Exception:
                                    continue
                        if not proceed_clicked and proceed_text:
                            # Fallback: buscar por innerText exacto via JS.
                            clicked = await page.evaluate("""() => {
                                const els = Array.from(
                                    document.querySelectorAll('button, input[type="submit"], a')
                                );
                                for (const el of els) {
                                    const t = (el.innerText || el.value || '').toLowerCase();
                                    if (t.includes('proceed to create an account')) {
                                        el.click();
                                        return true;
                                    }
                                }
                                return false;
                            }""")
                            if clicked:
                                proceed_clicked = True
                                logger.info("Proceed OK (fallback JS por texto)")
                        if proceed_clicked:
                            await asyncio.sleep(5)
                        elif proceed_text:
                            logger.warning("Texto Proceed presente pero boton no clicable")
                        else:
                            logger.warning("Sin pantalla Proceed (ni password): rama desconocida")
                    except Exception as e:
                        logger.warning(f"Error buscando Proceed: {e}")
                else:
                    await page.screenshot(path="error_bloqueo_old.png")
                    await update_status("No se encontro campo de identificador. Captura en error_bloqueo_old.png")
                    await cancel_phone_order(provider, order_id)
                    return None

                # Paso 2: si Amazon ofrece crear cuenta para este numero,
                # aparece #ap_customer_name (+ password). Si en cambio pide
                # password directo, la cuenta YA EXISTE: no es registro.
                customer_input = None
                for sel in [
                    "#ap_customer_name",
                    'input[name="customerName"]',
                    "#ap_customer_name_mobile",
                ]:
                    try:
                        if await page.wait_for_selector(sel, timeout=8000):
                            customer_input = sel
                            break
                    except Exception:
                        continue

                if customer_input:
                    await page.fill(customer_input, fullname)
                    logger.info(f"{customer_input} OK")
                    if await page.query_selector("#ap_password"):
                        await page.fill("#ap_password", password)
                        logger.info("#ap_password OK")
                    submit_btn = await page.query_selector("#continue, input[type=\"submit\"]")
                    if submit_btn:
                        await submit_btn.click()
                    logger.info("#continue registro OK")
                else:
                    title = ""
                    try:
                        title = (await page.title() or "").lower()
                    except Exception:
                        pass
                    html_snippet = ""
                    try:
                        html_snippet = (await page.content())[:4000].lower()
                    except Exception:
                        pass
                    if "ap_password" in html_snippet or "passwor" in title:
                        logger.warning("La cuenta ya existe (pide password): no es registro nuevo")
                        await update_status("El numero ya tiene cuenta: se cancela el pedido")
                        await cancel_phone_order(provider, order_id)
                        return None
                    logger.warning("Sin formulario de registro tras el identificador")
                    await page.screenshot(path="error_bloqueo_old.png")
                    await update_status("Sin formulario de registro. Captura en error_bloqueo_old.png")
                    await cancel_phone_order(provider, order_id)
                    return None

                await asyncio.sleep(4)

                # EVIDENCIA 2026-11-03: captura del punto OTP antes de esperar
                # el SMS. Hipotesis: Amazon ofrece enviar el codigo por
                # WhatsApp como opcion 1 y por SMS como opcion 2, asi que el
                # SMS nunca llega aunque el numero sea valido. Esta captura
                # (otp_channel_old.png) + el log de canales permite ver que
                # opcion quedo seleccionada.
                try:
                    await page.screenshot(path="otp_channel_old.png")
                    logger.info("Captura OTP guardada en otp_channel_old.png")
                except Exception as e:
                    logger.warning(f"No se pudo guardar captura OTP: {e}")
                try:
                    channel_info = await page.evaluate("""() => {
                        const out = {title: document.title, radios: [], texts: []};
                        const radios = Array.from(
                            document.querySelectorAll('input[type="radio"]')
                        );
                        for (const r of radios) {
                            const label = r.closest('label')
                                || document.querySelector(`label[for="${r.id}"]`);
                            out.radios.push({
                                id: r.id || '', name: r.name || '',
                                value: r.value || '', checked: !!r.checked,
                                label: (label ? label.innerText : '').trim().slice(0, 120),
                            });
                        }
                        const body = (document.body.innerText || '').toLowerCase();
                        for (const kw of ['whatsapp', 'sms', 'text message',
                                          'otp', 'verification code',
                                          'send code', 'resend']) {
                            if (body.includes(kw)) out.texts.push(kw);
                        }
                        return JSON.stringify(out).slice(0, 2000);
                    }""")
                    logger.info(f"Canal OTP: {channel_info}")
                except Exception as e:
                    logger.warning(f"No se pudo leer canales OTP: {e}")

                if await page.query_selector("#captcha-container canvas"):
                    await update_status("Resolviendo captcha...")
                    solved = await solve_grid_captcha_anticaptcha(
                        page, status_callback=update_status
                    )
                    if not solved:
                        logger.warning("Captcha no resuelto, se continua al OTP de todos modos")

                await update_status("Esperando SMS OTP...")
                verification_code = None
                for _ in range(12):
                    code = await check_phone_sms(provider, order_id)
                    if code == "CANCELLED":
                        await update_status("Orden SMS cancelada por el proveedor")
                        return None
                    if code:
                        verification_code = code
                        break
                    await asyncio.sleep(10)

                if not verification_code:
                    await update_status("No llego el SMS en el tiempo esperado")
                    await cancel_phone_order(provider, order_id)
                    return None

                await update_status(f"Codigo recibido: {verification_code}. Ingresando...")
                filled = False
                for sel in OTP_SELECTORS:
                    try:
                        if await page.query_selector(sel):
                            await page.fill(sel, verification_code)
                            filled = True
                            break
                    except Exception:
                        continue

                if not filled:
                    logger.warning("No se encontro input de OTP con los selectores conocidos")
                    await cancel_phone_order(provider, order_id)
                    return None

                await page.keyboard.press("Enter")
                await asyncio.sleep(5)

                cookies = await context.cookies()
                cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
                if not cookie_str:
                    logger.error("Sin cookies tras el OTP: registro incompleto")
                    return None

                logger.info("Cookies obtenidas OK")
                return {
                    "provider": provider,
                    "phone": phone,
                    "password": password,
                    "fullname": fullname,
                    "cookies": cookie_str,
                }

            except Exception as e:
                logger.error(f"Error critico en flujo: {e}")
                return None
            finally:
                await browser.close()
                browser = None
    except Exception as e:
        logger.error(f"Error critico en flujo: {e}")
        return None
    finally:
        if browser:
            try:
                await browser.close()
            except Exception:
                pass
    return None


if __name__ == "__main__":
    async def _probe():
        import sys as _sys
        print(f".env: {_PROJECT_ENV or '(no encontrado)'}")
        print(f"SMSPOOL_API_KEY: {'configurada' if SMSPOOL_API_KEY else 'VACIA'}")
        print(f"HEROSMS_API_KEY: {'configurada' if HEROSMS_API_KEY else 'VACIA'}")
        print(f"ANTICAPTCHA_API_KEY: {'configurada' if ANTICAPTCHA_API_KEY else 'VACIA'}")
        print(f"PROXY_HOST: {PROXY_HOST or '(no definido)'}")
        print(f"Proxy forzoso: {'SI' if get_proxy_config() else 'NO (el flujo se aborta)'})")
        # Reintentos con otro numero si el pool entrega reciclados
        # (cuenta ya existente). Cada intento compra numero: max intentos
        # por argv (default 1). Balance SMSPool ~0.74 -> max ~5 intentos.
        max_tries = 1
        if len(_sys.argv) > 1:
            try:
                max_tries = max(1, min(8, int(_sys.argv[1])))
            except Exception:
                max_tries = 1
        print(f"Intentos max: {max_tries}")
        result = None
        for i in range(1, max_tries + 1):
            print(f"--- Intento {i}/{max_tries} ---")
            result = await create_amazon_account(status_callback=None)
            if result:
                break
            print(f"Intento {i}: sin cuenta (numero reciclado u otro fallo).")
        await close_http_client()
        if result:
            print("RESULTADO: OK")
            print(f"  proveedor: {result.get('provider')}")
            print(f"  phone: {result.get('phone')}")
            print(f"  fullname: {result.get('fullname')}")
            print(f"  cookies: {(result.get('cookies') or '')[:120]}...")
        else:
            print("RESULTADO: FALLO (ver log)")

    asyncio.run(_probe())
