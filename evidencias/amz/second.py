import asyncio
import random
import time
import uuid
import logging
import httpx
import re
import base64
import json
from urllib.parse import urlencode
from playwright.async_api import async_playwright
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode
from collections import deque
from functools import lru_cache

# ==================== CONFIGURACIÓN ====================
TELEGRAM_BOT_TOKEN = "TU_BOT_TOKEN_AQUI"

# Anti-Captcha API
ANTICAPTCHA_API_KEY = "TU_API_KEY_ANTICAPTCHA"

# SMSPool API
SMSPOOL_API_KEY = "TU_API_KEY_SMSPOOL"
SMSPOOL_BASE_URL = "https://api.smspool.net"
SMSPOOL_SERVICE_NAME = "Amazon"  # Nombre correcto del servicio
SMSPOOL_COUNTRY_CODE = "US"  # USA

# 🌐 Configuración de Proxy (Decodo - anteriormente Smartproxy)
DECODO_PROXY_USER = "TU_USUARIO_DECODO"
DECODO_PROXY_PASS = "TU_CONTRASEÑA_DECODO"
DECODO_PROXY_ENDPOINT = "gate.decodo.com"
DECODO_PROXY_PORT = "7000"

# ⚡ OPTIMIZACIÓN: Límites de concurrencia
MAX_CONCURRENT_JOBS = 3  # Máximo de generaciones simultáneas
JOB_TIMEOUT = 300  # Timeout máximo por trabajo (5 min)

# Semáforo global para limitar concurrencia
job_semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)

# Cola de trabajos y estadísticas
job_queue = deque(maxlen=100)
active_jobs = {}
stats = {'total': 0, 'success': 0, 'failed': 0}

# Cliente HTTP global con connection pooling
http_client = None

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
]

# Configuración de logging optimizada
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== HTTP CLIENT POOL ====================

async def get_http_client():
    """Obtiene cliente HTTP global con connection pooling"""
    global http_client
    if http_client is None:
        http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=10.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            http2=True
        )
    return http_client

async def close_http_client():
    """Cierra el cliente HTTP global"""
    global http_client
    if http_client:
        await http_client.aclose()
        http_client = None

# ==================== SMS POOL FUNCTIONS (CORREGIDO) ====================

async def order_smspool_number():
    """Ordena número de teléfono virtual usando la API correcta de SMSPool"""
    data = {
        'key': SMSPOOL_API_KEY,
        'country': SMSPOOL_COUNTRY_CODE,
        'service': SMSPOOL_SERVICE_NAME,
        'max_price': '0.8',
        'pricing_option': '0'  # 0 = más barato disponible
    }
    
    url = f"{SMSPOOL_BASE_URL}/purchase/sms"
    
    try:
        client = await get_http_client()
        # Usar POST con form-data como indica la documentación oficial
        response = await client.post(url, data=data)
        
        if response.status_code == 200:
            data = response.json()
            logger.info(f"SMSPool order response: {data}")
            
            if data.get('success') == 1:
                order_id = data.get('order_id')
                # La API devuelve 'phonenumber' según la documentación
                phone_number = str(data.get('phonenumber', data.get('number', '')))
                country_code = str(data.get('cc', '1'))
                
                # Limpiar número - extraer solo dígitos
                phone_digits = re.sub(r'\D', '', phone_number)
                if len(phone_digits) > 10:
                    phone_digits = phone_digits[-10:]
                
                full_number = f"+{country_code}{phone_digits}"
                logger.info(f"Número obtenido: {full_number}")
                return order_id, full_number
            else:
                error_type = data.get('type', 'UNKNOWN')
                logger.error(f"Error SMSPool: {error_type}")
                if error_type == 'BALANCE_ERROR':
                    logger.error("Sin saldo en SMSPool")
                elif error_type == 'OUT_OF_STOCK':
                    logger.error("Sin números disponibles para Amazon US")
                
        return None, None
    except Exception as e:
        logger.error(f"Error ordenando número: {e}")
        return None, None

async def check_smspool_sms(order_id):
    """Verifica SMS recibido usando la API correcta"""
    data = {
        'key': SMSPOOL_API_KEY,
        'orderid': order_id
    }
    url = f"{SMSPOOL_BASE_URL}/sms/check"
    
    try:
        client = await get_http_client()
        response = await client.post(url, data=data)
        
        if response.status_code == 200:
            data = response.json()
            status = data.get('status')
            
            # Status codes según documentación oficial:
            # 1 = pending, 3 = completed, 6 = refunded
            if status == 3 or status == '3':
                # SMS recibido
                sms_code = data.get('sms', '')
                full_sms = data.get('full_sms', '')
                
                if sms_code:
                    logger.info(f"SMS recibido: {sms_code}")
                    # Extraer código de 6-10 dígitos
                    code_match = re.search(r'\b\d{6,10}\b', str(sms_code))
                    if code_match:
                        return code_match.group()
                    return str(sms_code).strip()
                    
            elif status == 2 or status == '2':
                logger.warning("Orden SMS expirada")
                return "EXPIRED"
                
            elif status == 5 or status == '5':
                logger.warning("Orden SMS cancelada")
                return "CANCELLED"
                
            elif status == 1 or status == '1':
                # Pendiente
                time_left = data.get('time_left', 0)
                logger.info(f"SMS pendiente, tiempo restante: {time_left}s")
                return None
                
        return None
    except Exception as e:
        logger.error(f"Exception checking SMS: {e}")
        return None

async def cancel_smspool_order(order_id):
    """Cancela orden SMS usando la API correcta"""
    data = {
        'key': SMSPOOL_API_KEY,
        'orderid': order_id
    }
    url = f"{SMSPOOL_BASE_URL}/sms/cancel"
    
    try:
        client = await get_http_client()
        response = await client.post(url, data=data)
        if response.status_code == 200:
            result = response.json()
            return result.get('success') == 1
        return False
    except Exception as e:
        logger.error(f"Error cancelando orden: {e}")
        return False

# ==================== TRADUCCIÓN DE OBJETOS CAPTCHA ====================

def translate_captcha_object(text):
    """Traduce el texto del captcha a inglés para Anti-Captcha"""
    object_translations = {
        # Español -> Inglés
        'sillas': 'chair', 'silla': 'chair',
        'autobuses': 'bus', 'autobús': 'bus', 'autobus': 'bus',
        'motocicletas': 'motorcycle', 'motocicleta': 'motorcycle', 'motos': 'motorcycle', 'moto': 'motorcycle',
        'bicicletas': 'bicycle', 'bicicleta': 'bicycle', 'bici': 'bicycle',
        'semáforos': 'traffic light', 'semaforos': 'traffic light', 'semáforo': 'traffic light', 'semaforo': 'traffic light',
        'coches': 'car', 'coche': 'car', 'autos': 'car', 'auto': 'car', 'carros': 'car', 'carro': 'car',
        'camiones': 'truck', 'camión': 'truck', 'camion': 'truck',
        'aviones': 'airplane', 'avión': 'airplane', 'avion': 'airplane',
        'barcos': 'boat', 'barco': 'boat',
        'trenes': 'train', 'tren': 'train',
        'bolsas': 'bag', 'bolsa': 'bag',
        'baldes': 'bucket', 'balde': 'bucket', 'cubetas': 'bucket', 'cubeta': 'bucket',
        'hidrantes': 'fire hydrant', 'hidrante': 'fire hydrant',
        'escaleras': 'stairs', 'escalera': 'stairs',
        'palmeras': 'palm tree', 'palmera': 'palm tree',
        'puentes': 'bridge', 'puente': 'bridge',
        'cruces peatonales': 'crosswalk', 'cruce peatonal': 'crosswalk',
        'montañas': 'mountain', 'montaña': 'mountain',
        'chimeneas': 'chimney', 'chimenea': 'chimney',
        'taxis': 'taxi', 'taxi': 'taxi',
        # Inglés -> Inglés
        'chairs': 'chair', 'chair': 'chair',
        'buses': 'bus', 'bus': 'bus',
        'motorcycles': 'motorcycle', 'motorcycle': 'motorcycle',
        'bicycles': 'bicycle', 'bicycle': 'bicycle', 'bikes': 'bicycle', 'bike': 'bicycle',
        'traffic lights': 'traffic light', 'traffic light': 'traffic light',
        'cars': 'car', 'car': 'car', 'vehicles': 'car', 'vehicle': 'car',
        'trucks': 'truck', 'truck': 'truck',
        'airplanes': 'airplane', 'airplane': 'airplane', 'planes': 'airplane', 'plane': 'airplane',
        'boats': 'boat', 'boat': 'boat', 'ships': 'boat', 'ship': 'boat',
        'trains': 'train', 'train': 'train',
        'bags': 'bag', 'bag': 'bag',
        'buckets': 'bucket', 'bucket': 'bucket',
        'fire hydrants': 'fire hydrant', 'fire hydrant': 'fire hydrant',
        'stairs': 'stairs', 'stair': 'stairs',
        'palm trees': 'palm tree', 'palm tree': 'palm tree',
        'bridges': 'bridge', 'bridge': 'bridge',
        'crosswalks': 'crosswalk', 'crosswalk': 'crosswalk',
        'mountains': 'mountain', 'mountain': 'mountain',
        'chimneys': 'chimney', 'chimney': 'chimney',
        'taxis': 'taxi', 'taxi': 'taxi', 'cabs': 'taxi', 'cab': 'taxi',
        'traffic signs': 'traffic sign', 'traffic sign': 'traffic sign',
        'bus stops': 'bus stop', 'bus stop': 'bus stop',
        'parking meters': 'parking meter', 'parking meter': 'parking meter',
    }
    
    text_lower = text.lower().strip()
    
    # Buscar coincidencia exacta
    if text_lower in object_translations:
        return object_translations[text_lower]
    
    # Buscar si contiene alguna clave
    for key, value in object_translations.items():
        if key in text_lower:
            return value
    
    # Limpiar el texto si no se encuentra traducción
    words_to_remove = [
        'select all', 'click on', 'all squares', 'squares with', 'containing',
        'selecciona', 'todas las', 'imágenes', 'imagenes', 'con', 'que contengan',
        'haz clic', 'click', 'images', 'with', 'the', 'a', 'an'
    ]
    clean_text = text_lower
    for word in words_to_remove:
        clean_text = clean_text.replace(word, '')
    
    return clean_text.strip() or 'object'

# ==================== ANTI-CAPTCHA (CORREGIDO) ====================

async def solve_grid_captcha_anticaptcha(page, max_retries=5, status_callback=None):
    """Resuelve el captcha de grid usando Anti-Captcha con la API correcta"""
    
    async def update(msg):
        if status_callback:
            try:
                await status_callback(msg)
            except:
                pass
    
    client = await get_http_client()
    
    for attempt in range(max_retries):
        try:
            await update(f"🛡️ Captcha intento {attempt+1}/{max_retries}...")
            
            # Verificar si hay mensaje de error
            error_found = False
            error_selectors = [
                'text="Se ha superado el límite de tiempo"',
                'text="Error temporal"',
                'text="Vuelva a intentarlo"',
                'text="Time limit exceeded"',
                'text="Temporary error"',
                'text="Try again"',
            ]
            
            for selector in error_selectors:
                error_msg = await page.query_selector(selector)
                if error_msg:
                    error_found = True
                    break
            
            if error_found:
                await update("⚠️ Error en captcha, reintentando...")
                await asyncio.sleep(1)
                
                # Buscar botón de reintentar
                retry_selectors = [
                    'button:has-text("intentar")',
                    'button:has-text("Retry")',
                    'button:has-text("reintentar")',
                    'a:has-text("intentar")',
                    '#amzn-btn-refresh',
                ]
                
                clicked = False
                for selector in retry_selectors:
                    try:
                        retry_btn = await page.query_selector(selector)
                        if retry_btn and await retry_btn.is_visible():
                            await retry_btn.click()
                            clicked = True
                            await asyncio.sleep(2)
                            break
                    except:
                        continue
                
                if not clicked:
                    await page.reload()
                    await asyncio.sleep(3)
                continue
            
            # Buscar el canvas del captcha
            canvas = await page.query_selector('#captcha-container canvas, #root canvas, .amzn-captcha-modal canvas')
            
            if not canvas:
                await update("✅ No hay captcha visible")
                return True
            
            # Obtener texto del captcha
            question_text = await page.evaluate('''() => {
                const selectors = [
                    '.amzn-captcha-modal em',
                    '.amzn-captcha-modal b',
                    '.amzn-captcha-modal strong',
                    '#captcha-container em',
                    '#captcha-container b',
                    '#captcha-container strong',
                    '.a-text-bold',
                    '[class*="captcha"] em',
                    '[class*="captcha"] b',
                    '[class*="captcha"] strong',
                ];
                
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (el && el.textContent && el.textContent.trim().length > 0) {
                        return el.textContent.trim();
                    }
                }
                return '';
            }''')
            
            target_object = translate_captcha_object(question_text)
            logger.info(f"Objeto a buscar: '{target_object}'")
            await update(f"🔍 Buscando: {target_object}...")
            
            canvas_box = await canvas.bounding_box()
            if not canvas_box:
                continue
            
            # Capturar screenshot
            margin = 5
            screenshot_bytes = await page.screenshot(clip={
                'x': max(0, canvas_box['x'] - margin),
                'y': max(0, canvas_box['y'] - margin),
                'width': canvas_box['width'] + margin * 2,
                'height': canvas_box['height'] + margin * 2
            })
            
            image_base64 = base64.b64encode(screenshot_bytes).decode('utf-8')
            
            await update(f"📤 Enviando a Anti-Captcha...")
            
            # Crear tarea con la API correcta
            create_payload = {
                "clientKey": ANTICAPTCHA_API_KEY,
                "task": {
                    "type": "ImageToCoordinatesTask",
                    "body": image_base64,
                    "comment": f"Click on ALL images containing {target_object}. This is a 4x4 grid captcha from Amazon.",
                    "websiteURL": "https://www.amazon.com"
                }
            }
            
            response = await client.post(
                'https://api.anti-captcha.com/createTask',
                json=create_payload
            )
            result = response.json()
            
            if result.get('errorId') != 0:
                error_desc = result.get('errorDescription', 'desconocido')
                await update(f"⚠️ Error Anti-Captcha: {error_desc}")
                
                # Manejar errores específicos
                if result.get('errorCode') == 'ERROR_NO_SLOT_AVAILABLE':
                    await update("🔄 Sin workers disponibles, esperando...")
                    await asyncio.sleep(5)
                elif result.get('errorCode') == 'ERROR_ZERO_BALANCE':
                    await update("❌ Sin saldo en Anti-Captcha")
                    return False
                continue
            
            task_id = result.get('taskId')
            await update(f"⏳ Procesando captcha (Task: {task_id})...")
            
            # Sondeo más paciente - hasta 90 segundos
            for poll in range(45):
                await asyncio.sleep(2)
                
                check_payload = {
                    "clientKey": ANTICAPTCHA_API_KEY,
                    "taskId": task_id
                }
                
                check_response = await client.post(
                    'https://api.anti-captcha.com/getTaskResult',
                    json=check_payload
                )
                check_result = check_response.json()
                
                if check_result.get('errorId') != 0:
                    await update(f"⚠️ Error: {check_result.get('errorDescription')}")
                    break
                
                status = check_result.get('status')
                
                if status == 'ready':
                    solution = check_result.get('solution', {})
                    coordinates = solution.get('coordinates', [])
                    
                    if coordinates:
                        await update(f"✅ Recibido: {len(coordinates)} puntos, aplicando...")
                        logger.info(f"Coordenadas recibidas: {coordinates}")
                        
                        # Aplicar clicks en las coordenadas
                        for i, coord in enumerate(coordinates):
                            if isinstance(coord, list):
                                rel_x = coord[0] - margin
                                rel_y = coord[1] - margin
                            elif isinstance(coord, dict):
                                rel_x = coord.get('x', 0) - margin
                                rel_y = coord.get('y', 0) - margin
                            else:
                                continue
                            
                            # Asegurar que las coordenadas están dentro del canvas
                            rel_x = max(0, min(rel_x, canvas_box['width']))
                            rel_y = max(0, min(rel_y, canvas_box['height']))
                            
                            click_x = canvas_box['x'] + rel_x
                            click_y = canvas_box['y'] + rel_y
                            
                            # Click con movimiento previo
                            await page.mouse.move(click_x, click_y)
                            await asyncio.sleep(random.uniform(0.1, 0.2))
                            await page.mouse.click(click_x, click_y)
                            await asyncio.sleep(random.uniform(0.4, 0.7))
                        
                        await asyncio.sleep(1)
                        
                        # Buscar botón de confirmar
                        confirm_selectors = [
                            '#amzn-btn-verify-internal',
                            'button.btn-primary',
                            'button:has-text("Verify")',
                            'button:has-text("Submit")',
                            'button:has-text("Confirmar")',
                            'button:has-text("Continue")',
                            'input[type="submit"]',
                            '.amzn-captcha-modal button[type="submit"]',
                            'button.a-button-primary',
                        ]
                        
                        confirm_btn = None
                        for sel in confirm_selectors:
                            try:
                                confirm_btn = await page.query_selector(sel)
                                if confirm_btn and await confirm_btn.is_visible():
                                    logger.info(f"Botón confirmar: {sel}")
                                    break
                                confirm_btn = None
                            except:
                                continue
                        
                        if confirm_btn:
                            await confirm_btn.click()
                            await asyncio.sleep(3)
                            
                            still_visible = await page.query_selector('#captcha-container canvas, #root canvas')
                            if still_visible:
                                await update("🔄 Otro captcha detectado...")
                                break
                            else:
                                await update("✅ Captcha resuelto!")
                                return True
                        else:
                            await asyncio.sleep(2)
                            still_visible = await page.query_selector('#captcha-container canvas, #root canvas')
                            if not still_visible:
                                await update("✅ Captcha resuelto (auto)!")
                                return True
                        break
                    else:
                        await update("⚠️ Sin coordenadas en respuesta")
                        break
                        
                elif status == 'processing':
                    if poll % 5 == 0:
                        await update(f"⏳ Procesando... ({poll*2}s)")
                    continue
                else:
                    break
                        
        except Exception as e:
            logger.error(f"Error en intento {attempt+1}: {e}")
            await update(f"❌ Error: {str(e)[:50]}")
            continue
    
    await update("⚠️ Captcha no resuelto, continuando...")
    return False

# ==================== COMPORTAMIENTO HUMANO ====================

async def simulate_human_behavior(page):
    """Simula comportamiento humano"""
    try:
        for _ in range(random.randint(2, 4)):
            x = random.randint(100, 800)
            y = random.randint(100, 600)
            await page.mouse.move(x, y)
            await asyncio.sleep(random.uniform(0.1, 0.3))
        
        for _ in range(random.randint(1, 3)):
            await page.evaluate(f"window.scrollBy(0, {random.randint(100, 300)})")
            await asyncio.sleep(random.uniform(0.3, 0.8))
    except:
        pass

# ==================== CREACIÓN DE CUENTA (CON PROXY DECODO) ====================

async def create_amazon_account(status_callback=None):
    """Crea cuenta Amazon US usando proxy Decodo"""
    
    async def update_status(msg):
        if status_callback:
            await status_callback(msg)
    
    await update_status("📱 Ordenando número de teléfono...")
    order_id, phone = await order_smspool_number()
    
    if not phone:
        await update_status("❌ Error: Sin saldo en SMS Pool o sin números disponibles")
        return None
    
    await update_status(f"✅ Número obtenido: {phone}")
    
    password = f"Pass{random.randint(1000, 9999)}{uuid.uuid4().hex[:8]}"
    first_name = ''.join(random.choices('abcdefghijklmnopqrstuvwxyz', k=5)).capitalize()
    last_name = ''.join(random.choices('abcdefghijklmnopqrstuvwxyz', k=5)).capitalize()
    fullname = f"{first_name} {last_name}"
    
    # Generar session ID único para IP sticky
    session_id = uuid.uuid4().hex[:8]
    
    async with async_playwright() as p:
        # Usar Chromium principalmente para mejor compatibilidad
        browser_args = [
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-blink-features=AutomationControlled',
            '--start-maximized',
            '--disable-web-security',
            '--disable-features=IsolateOrigins,site-per-process',
        ]
        
        browser = await p.chromium.launch(
            headless=False,
            args=browser_args
        )
        
        # Configurar proxy Decodo correctamente
        # Formato: user-{usuario}-session-{id}:{password}@gate.decodo.com:7000
        proxy_username = f"user-{DECODO_PROXY_USER}-session-{session_id}"
        
        proxy_config = {
            'server': f'http://{DECODO_PROXY_ENDPOINT}:{DECODO_PROXY_PORT}',
            'username': proxy_username,
            'password': DECODO_PROXY_PASS
        }
        
        selected_ua = random.choice(USER_AGENTS)
        
        # Contexto limpio
        context = await browser.new_context(
            user_agent=selected_ua,
            viewport={'width': random.randint(1280, 1920), 'height': random.randint(720, 1080)},
            locale='en-US',
            timezone_id='America/New_York',
            proxy=proxy_config,
            ignore_https_errors=True,
        )
        
        await context.clear_cookies()
        
        # Scripts anti-detección
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = {runtime: {}, loadTimes: function(){}, csi: function(){}};
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
            
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                Promise.resolve({ state: Notification.permission }) :
                originalQuery(parameters)
            );
            
            const getParameter = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(parameter) {
                if (parameter === 37445) return 'Intel Inc.';
                if (parameter === 37446) return 'Intel Iris OpenGL Engine';
                return getParameter.apply(this, arguments);
            };
        """)
        
        page = await context.new_page()
        
        try:
            await update_status("🌐 Navegando a Amazon...")
            await page.goto(
                'https://www.amazon.com/ap/register?openid.pape.max_auth_age=0&openid.return_to=https%3A%2F%2Fwww.amazon.com%2F&openid.identity=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select&openid.assoc_handle=usflex&openid.mode=checkid_setup&openid.claimed_id=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select&openid.ns=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0',
                wait_until='domcontentloaded',
                timeout=30000
            )
            
            await asyncio.sleep(3)
            await simulate_human_behavior(page)
            
            await update_status("📝 Llenando formulario...")
            await page.wait_for_selector('#ap_customer_name', timeout=15000)
            await page.fill('#ap_customer_name', fullname)
            await asyncio.sleep(0.5)
            
            # Limpiar número de teléfono
            phone_digits = re.sub(r'\D', '', phone)
            if phone_digits.startswith('1') and len(phone_digits) == 11:
                phone_digits = phone_digits[1:]
            if len(phone_digits) > 10:
                phone_digits = phone_digits[-10:]
            
            logger.info(f"Número limpio: {phone_digits}")
            
            await page.fill('#ap_email', phone_digits)
            await asyncio.sleep(0.5)
            
            await page.fill('#ap_password', password)
            await asyncio.sleep(0.5)
            
            try:
                await page.fill('#ap_password_check', password, timeout=5000)
            except:
                pass
            
            # Verificar captcha antes de enviar
            await asyncio.sleep(1)
            captcha_grid = await page.query_selector('#captcha-container canvas, #root canvas, .amzn-captcha-modal canvas')
            if captcha_grid:
                await update_status("🛡️ Captcha detectado, resolviendo...")
                solved = await solve_grid_captcha_anticaptcha(page, status_callback=update_status)
                if not solved:
                    await update_status("⚠️ Captcha difícil, reintentando...")
            
            await update_status("📤 Enviando formulario...")
            
            # Intentar click con múltiples selectores
            submit_clicked = False
            submit_selectors = [
                '#continue',
                'input[type="submit"]',
                '#auth-continue',
                '.a-button-input',
                'button[type="submit"]',
            ]
            
            for selector in submit_selectors:
                try:
                    btn = await page.query_selector(selector)
                    if btn and await btn.is_visible():
                        await btn.click(timeout=10000)
                        submit_clicked = True
                        logger.info(f"Click exitoso en: {selector}")
                        break
                except:
                    continue
            
            if not submit_clicked:
                await page.evaluate('document.querySelector("#continue")?.click()')
                await asyncio.sleep(1)
            
            await asyncio.sleep(3)
            
            # Verificar captcha después del envío
            captcha_grid = await page.query_selector('#captcha-container canvas, #root canvas, .amzn-captcha-modal canvas')
            if captcha_grid:
                solved = await solve_grid_captcha_anticaptcha(page, status_callback=update_status)
                if not solved:
                    await update_status("⚠️ Captcha difícil, continuando...")
            
            await update_status("📩 Esperando SMS (máx 2.5 min)...")
            verification_code = None
            
            for attempt in range(15):
                await update_status(f"🔄 Verificando SMS... ({attempt+1}/15)")
                code = await check_smspool_sms(order_id)
                
                if code:
                    if code == "CANCELLED":
                        await update_status("⚠️ Orden SMS cancelada")
                        break
                    elif code == "EXPIRED":
                        await update_status("⚠️ Orden SMS expirada")
                        break
                    verification_code = code
                    await update_status(f"✅ Código SMS detectado: {verification_code}")
                    break
                
                await asyncio.sleep(8)
            
            if verification_code:
                await update_status(f"✅ Ingresando código: {verification_code}")
                await asyncio.sleep(1)
                
                code_selectors = [
                    '#auth-pv-enter-code',
                    'input[name="code"]',
                    'input[type="tel"]',
                    '#cvf_input_code',
                    '#cvf-input-code',
                    'input[name="otp"]',
                    '#auth-mfa-otpcode',
                    'input[autocomplete="one-time-code"]',
                ]
                
                code_entered = False
                for selector in code_selectors:
                    try:
                        element = await page.wait_for_selector(selector, timeout=3000)
                        if element:
                            await element.click()
                            await asyncio.sleep(0.2)
                            for char in verification_code:
                                await page.keyboard.type(char)
                                await asyncio.sleep(random.uniform(0.05, 0.12))
                            await update_status(f"📝 Código ingresado en: {selector}")
                            code_entered = True
                            break
                    except:
                        continue
                
                if not code_entered:
                    for selector in code_selectors:
                        try:
                            await page.fill(selector, verification_code, timeout=3000)
                            await update_status(f"📝 Código ingresado (fill): {selector}")
                            code_entered = True
                            break
                        except:
                            continue
                
                await asyncio.sleep(1)
                
                submit_selectors = [
                    '#auth-verify-button',
                    '#cvf-submit-otp-button',
                    'input[type="submit"]',
                    'button[type="submit"]',
                    '.a-button-primary button',
                    'button.a-button-text',
                    '#continue',
                ]
                
                button_clicked = False
                for selector in submit_selectors:
                    try:
                        btn = await page.wait_for_selector(selector, timeout=2000)
                        if btn and await btn.is_visible():
                            await asyncio.sleep(0.3)
                            await btn.click()
                            await update_status(f"📤 Botón presionado: {selector}")
                            button_clicked = True
                            break
                    except:
                        continue
                
                if not button_clicked:
                    await page.keyboard.press('Enter')
                    await update_status("📤 Enviado con Enter")
                
                try:
                    await page.wait_for_load_state('domcontentloaded', timeout=15000)
                except:
                    pass
                
                await asyncio.sleep(3)
                
                # Verificar captcha final
                captcha_grid2 = await page.query_selector('#captcha-container canvas, #root canvas, .amzn-captcha-modal canvas')
                if captcha_grid2:
                    await solve_grid_captcha_anticaptcha(page, status_callback=update_status)
                
                current_url = page.url
                
                if 'your-account' in current_url or '/gp/' in current_url or 'amazon.com' in current_url:
                    await update_status("🎉 ¡Cuenta creada exitosamente!")
                    
                    cookies = await context.cookies()
                    cookie_str = '; '.join([f"{c['name']}={c['value']}" for c in cookies])
                    
                    result = {
                        'phone': phone,
                        'password': password,
                        'fullname': fullname,
                        'cookies': cookie_str,
                        'order_id': order_id
                    }
                    
                    await browser.close()
                    return result
                else:
                    await update_status(f"⚠️ URL inesperada: {current_url[:50]}...")
            else:
                await update_status("❌ No se recibió código SMS")
                await cancel_smspool_order(order_id)
        
        except Exception as e:
            await update_status(f"❌ Error: {str(e)[:100]}")
            logger.error(f"Error creando cuenta: {e}", exc_info=True)
        
        finally:
            await browser.close()
    
    return None

# ==================== COMANDOS DEL BOT ====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /start"""
    welcome_text = """
🍪 *Bot Generador de Cookies Amazon US*

📋 *Comandos disponibles:*
• `/gcookie` - Genera una nueva cuenta Amazon US con cookies

⚠️ *Nota:* El proceso puede tardar 2-3 minutos.
    """
    await update.message.reply_text(welcome_text, parse_mode=ParseMode.MARKDOWN)

async def gcookie_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /gcookie - Genera cookies de Amazon"""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    if user_id in active_jobs:
        await update.message.reply_text(
            "⏳ *Ya tienes una generación en progreso*\n\n"
            "Espera a que termine antes de iniciar otra.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    if job_semaphore.locked() and len(active_jobs) >= MAX_CONCURRENT_JOBS:
        await update.message.reply_text(
            f"🔄 *Cola de espera*\n\n"
            f"Hay {len(active_jobs)} trabajos en progreso.\n"
            f"Intenta de nuevo en 1-2 minutos.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    active_jobs[user_id] = time.time()
    stats['total'] += 1
    
    status_message = await update.message.reply_text(
        "🍪 *Generando Cookies US*\n\n"
        "⏳ *Procesando...*\n"
        "🍪 ⏳ 🍪",
        parse_mode=ParseMode.MARKDOWN
    )
    
    cookie_frames = [
        "🍪 ⏳ 🍪",
        "🍪🍪 ⏳ 🍪🍪",
        "🍪🍪🍪 ⏳ 🍪🍪🍪",
        "🍪🍪🍪🍪 ⏳ 🍪🍪🍪🍪",
        "🍪🍪🍪 ⏳ 🍪🍪🍪",
        "🍪🍪 ⏳ 🍪🍪",
    ]
    frame_index = [0]
    last_update = [0]
    
    async def update_status(msg):
        """Callback para mostrar animación"""
        now = time.time()
        if now - last_update[0] < 1.5:
            return
        last_update[0] = now
        
        try:
            frame = cookie_frames[frame_index[0] % len(cookie_frames)]
            frame_index[0] += 1
            
            await status_message.edit_text(
                f"🍪 *Generando Cookies US*\n\n"
                f"{frame}\n\n"
                f"⏳ *Procesando...*",
                parse_mode=ParseMode.MARKDOWN
            )
        except:
            pass
    
    try:
        async with job_semaphore:
            result = await asyncio.wait_for(
                create_amazon_account(status_callback=update_status),
                timeout=JOB_TIMEOUT
            )
    except asyncio.TimeoutError:
        result = None
        logger.warning(f"Timeout en trabajo para user {user_id}")
    except Exception as e:
        result = None
        logger.error(f"Error en trabajo: {e}")
    finally:
        active_jobs.pop(user_id, None)
    
    if result:
        phone = result['phone']
        password = result['password']
        fullname = result['fullname']
        cookies = result['cookies']
        
        success_message = f"""
✅ *¡Cuenta Amazon US creada exitosamente!*

👤 *Nombre:* `{fullname}`
📱 *Teléfono:*'{phone}'
🔑 *Contraseña:*'{password}'
🍪 *Cookies:*'{cookies}'
⏰ Generado: {time.strftime('%Y-%m-%d %H:%M:%S')}
"""
        await status_message.edit_text(success_message, parse_mode=ParseMode.MARKDOWN)
        stats['success'] += 1
        
        try:
            import aiofiles
            async with aiofiles.open('generated_accounts.txt', 'a', encoding='utf-8') as f:
                await f.write(f"\n{'='*50}\n")
                await f.write(f"Teléfono: {phone}\n")
                await f.write(f"Password: {password}\n")
                await f.write(f"Nombre: {fullname}\n")
                await f.write(f"Cookies: {cookies}\n")
                await f.write(f"Fecha: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        except ImportError:
            with open('generated_accounts.txt', 'a', encoding='utf-8') as f:
                f.write(f"\n{'='*50}\n")
                f.write(f"Teléfono: {phone}\n")
                f.write(f"Password: {password}\n")
                f.write(f"Nombre: {fullname}\n")
                f.write(f"Cookies: {cookies}\n")
                f.write(f"Fecha: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        
    else:
        await status_message.edit_text(
            "❌ *Error al generar la cuenta*\n\nPosibles causas:\n"
            "• Sin saldo en SMS Pool\n"
            "• Captcha no resuelto\n"
            "• Amazon bloqueó la solicitud\n"
            "• Error de proxy\n\n"
            "Intenta de nuevo con `/gcookie`",
            parse_mode=ParseMode.MARKDOWN
        )
        stats['failed'] += 1

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /help"""
    help_text = """
📖 *Ayuda del Bot*

*Comandos:*
• `/start` - Mensaje de bienvenida
• `/gcookie` - Genera cuenta Amazon US con cookies
• `/help` - Muestra esta ayuda
• `/stats` - Ver estadísticas del bot

*¿Cómo funciona?*
1. Usa `/gcookie` para iniciar
2. El bot ordena un número virtual
3. Crea la cuenta en Amazon US
4. Resuelve el captcha automáticamente
5. Verifica el SMS
6. Te envía las credenciales y cookies

⏱ *Tiempo estimado:* 2-3 minutos
    """
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /stats - Muestra estadísticas del bot"""
    success_rate = (stats['success'] / stats['total'] * 100) if stats['total'] > 0 else 0
    
    stats_text = f"""
📊 *Estadísticas del Bot*

📈 *Totales:*
• Generaciones totales: `{stats['total']}`
• Exitosas: `{stats['success']}` ✅
• Fallidas: `{stats['failed']}` ❌
• Tasa de éxito: `{success_rate:.1f}%`

⚡ *Estado actual:*
• Trabajos activos: `{len(active_jobs)}/{MAX_CONCURRENT_JOBS}`
• Semáforo disponible: `{'✅ Sí' if not job_semaphore.locked() else '🔒 Ocupado'}`

🖥️ *Sistema:*
• Max concurrencia: `{MAX_CONCURRENT_JOBS}`
• Timeout por trabajo: `{JOB_TIMEOUT}s`
• Proxy: `Decodo (Smartproxy)`
    """
    await update.message.reply_text(stats_text, parse_mode=ParseMode.MARKDOWN)

# ==================== MAIN ====================

async def cleanup(application):
    """Limpieza al cerrar el bot"""
    await close_http_client()
    logger.info("Cliente HTTP cerrado")

def main():
    """Función principal del bot"""
    print("""
    ╔══════════════════════════════════════════════════╗
    ║   BOT TELEGRAM - AMAZON US COOKIE GENERATOR      ║
    ║   ⚡ Optimizado con Decodo Proxy                ║
    ╚══════════════════════════════════════════════════╝
    """)
    
    # Verificar configuración
    if TELEGRAM_BOT_TOKEN == "TU_BOT_TOKEN_AQUI":
        print("⚠️ ERROR: Configura TELEGRAM_BOT_TOKEN con tu token de @BotFather")
        return
    
    if ANTICAPTCHA_API_KEY == "TU_API_KEY_ANTICAPTCHA":
        print("⚠️ ERROR: Configura ANTICAPTCHA_API_KEY con tu API key de Anti-Captcha")
        return
    
    if SMSPOOL_API_KEY == "TU_API_KEY_SMSPOOL":
        print("⚠️ ERROR: Configura SMSPOOL_API_KEY con tu API key de SMSPool")
        return
    
    if DECODO_PROXY_USER == "TU_USUARIO_DECODO":
        print("⚠️ ERROR: Configura DECODO_PROXY_USER con tu usuario de Decodo")
        return
    
    # Crear aplicación del bot
    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .concurrent_updates(True)
        .build()
    )
    
    # Agregar handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("gcookie", gcookie_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("stats", stats_command))
    
    # Agregar cleanup al cerrar
    application.post_shutdown = cleanup
    
    print(f"✅ Bot iniciado correctamente")
    print(f"⚡ Max concurrencia: {MAX_CONCURRENT_JOBS} trabajos")
    print(f"🌐 Proxy: Decodo (gate.decodo.com:7000)")
    print(f"📱 Comandos: /gcookie, /stats, /help")
    print("🛑 Presiona Ctrl+C para detener")
    
    # Iniciar el bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()