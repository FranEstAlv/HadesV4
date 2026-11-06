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
TELEGRAM_BOT_TOKEN = "8926930255:AAGJsMcXbynbobgqMQsFg6bH4zTgwOdF8xE"

CAPSOLVER_API_KEY = "CAP-371E9C49C0FE2613B88D60F7870ADCC5BABEB11CBD7CAA95CEDBEC353636CB87"
ANTICAPTCHA_API_KEY = "cecd0963cb6667a1d9ec1131829b06ad"
SMSPOOL_API_KEY="DlQH1Pvt2NM0TP7u46Wh8DvbhN0VYOmN"
SMSPOOL_BASE_URL = "https://api.smspool.net"
SMSPOOL_SERVICE_ID = "39"
SMSPOOL_COUNTRY_CODE = "US"  # USA

# 🌐 Configuración de Proxy (Oxylabs - IP rotativa)
PROXY_HOST = ""
PROXY_PORT = ""
PROXY_USER = ""
PROXY_PASS = ""

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

# ==================== SMS POOL FUNCTIONS ====================

async def order_smspool_number():
    """Ordena número de teléfono virtual (async) - Optimizado con cliente global"""
    params = {
        'key': SMSPOOL_API_KEY,
        'country': SMSPOOL_COUNTRY_CODE,
        'service': SMSPOOL_SERVICE_ID,
        'pool': '',
        'max_price': '0.8'
    }
    
    url = f"{SMSPOOL_BASE_URL}/purchase/sms?{urlencode(params)}"
    
    try:
        client = await get_http_client()
        response = await client.post(url)
        
        if response.status_code == 200:
            data = response.json()
            logger.info(f"SMSPool order: {data}")
            
            if data.get('success') == 1:
                order_id = data.get('order_id')
                phone_number = str(data.get('number', ''))
                country_code = str(data.get('cc', '1'))
                
                # Limpiar número
                phone_number = phone_number.lstrip('0').lstrip('+')
                if len(phone_number) > 10:
                    phone_number = phone_number[-10:]
                
                full_number = f"+{country_code}{phone_number}"
                logger.info(f"Número: {full_number}")
                return order_id, full_number
        return None, None
    except Exception as e:
        logger.error(f"Error ordenando número: {e}")
        return None, None

async def check_smspool_sms(order_id):
    """Verifica SMS recibido (async) - Optimizado"""
    params = {
        'key': SMSPOOL_API_KEY,
        'orderid': order_id
    }
    url = f"{SMSPOOL_BASE_URL}/sms/check?{urlencode(params)}"
    
    try:
        client = await get_http_client()
        response = await client.post(url)
        if response.status_code == 200:
            data = response.json()
            
            # Obtener status directamente (sin depender de 'success')
            # Status: 1 = pending, 2 = cancelled, 3 = completed
            status = data.get('status')
            
            if status == 3 or status == '3':
                # SMS recibido!
                sms_code = data.get('sms', '')
                if sms_code:
                    logger.info(f"SMS recibido RAW: {sms_code}")
                    # Extraer código de 6 a 10 dígitos del SMS
                    code_match = re.search(r'\b\d{6,10}\b', str(sms_code))
                    if code_match:
                        extracted_code = code_match.group()
                        logger.info(f"Código extraído: {extracted_code}")
                        return extracted_code
                    # Si no hay match, devolver el SMS completo
                    return str(sms_code).strip()
                    
            elif status == 2 or status == '2':
                logger.warning("Orden SMS cancelada")
                return "CANCELLED"
                
            elif status == 1 or status == '1':
                # Pendiente - revisar time_left
                time_left = data.get('time_left', 0)
                logger.info(f"SMS pendiente, tiempo restante: {time_left}s")
                return None
                
            else:
                # Estado desconocido, revisar si hay SMS en la respuesta
                sms_text = data.get('sms', '')
                if sms_text and len(str(sms_text)) > 0:
                    logger.info(f"SMS encontrado en respuesta: {sms_text}")
                    code_match = re.search(r'\b\d{6,10}\b', str(sms_text))
                    if code_match:
                        return code_match.group()
        else:
            logger.error(f"HTTP error checking SMS: {response.status_code}")
        return None
    except Exception as e:
        logger.error(f"Exception checking SMS: {e}")
        return None

async def cancel_smspool_order(order_id):
    """Cancela orden SMS (async) - Optimizado"""
    params = {
        'key': SMSPOOL_API_KEY,
        'orderid': order_id
    }
    url = f"{SMSPOOL_BASE_URL}/sms/cancel?{urlencode(params)}"
    
    try:
        client = await get_http_client()
        response = await client.post(url)
        return response.status_code == 200 and response.json().get('success') == 1
    except:
        return False

# ==================== ANTI-CAPTCHA GRID SOLVER ====================

async def solve_grid_captcha_anticaptcha(page, max_retries=5, status_callback=None):
    """Resuelve el captcha de grid usando Anti-Captcha (async)"""
    
    async def update(msg):
        if status_callback:
            try:
                await status_callback(msg)
            except:
                pass
    
    # Traducciones español -> inglés Y términos en inglés (para Amazon US)
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
        'cruces peatonales': 'crosswalk', 'cruce peatonal': 'crosswalk', 'paso de peatones': 'crosswalk',
        'pasos de cebra': 'crosswalk', 'paso de cebra': 'crosswalk',
        'montañas': 'mountain', 'montaña': 'mountain',
        'chimeneas': 'chimney', 'chimenea': 'chimney',
        'taxis': 'taxi', 'taxi': 'taxi',
        'autobús': 'bus', 'buses': 'bus',
        'señales de tráfico': 'traffic sign', 'señal de tráfico': 'traffic sign',
        'señales de transito': 'traffic sign', 'señal de transito': 'traffic sign',
        'paradas de autobús': 'bus stop', 'parada de autobús': 'bus stop',
        'parquímetros': 'parking meter', 'parquímetro': 'parking meter',
        
        # Inglés -> Inglés (para Amazon US que ya viene en inglés)
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
        'fire hydrants': 'fire hydrant', 'fire hydrant': 'fire hydrant', 'hydrants': 'fire hydrant', 'hydrant': 'fire hydrant',
        'stairs': 'stairs', 'stair': 'stairs', 'staircases': 'stairs', 'staircase': 'stairs',
        'palm trees': 'palm tree', 'palm tree': 'palm tree',
        'bridges': 'bridge', 'bridge': 'bridge',
        'crosswalks': 'crosswalk', 'crosswalk': 'crosswalk', 'pedestrian crossings': 'crosswalk',
        'mountains': 'mountain', 'mountain': 'mountain',
        'chimneys': 'chimney', 'chimney': 'chimney',
        'taxis': 'taxi', 'taxi': 'taxi', 'cabs': 'taxi', 'cab': 'taxi',
        'traffic signs': 'traffic sign', 'traffic sign': 'traffic sign', 'street signs': 'traffic sign',
        'bus stops': 'bus stop', 'bus stop': 'bus stop',
        'parking meters': 'parking meter', 'parking meter': 'parking meter',
        'storefronts': 'storefront', 'storefront': 'storefront',
        'mailboxes': 'mailbox', 'mailbox': 'mailbox',
        'tractors': 'tractor', 'tractor': 'tractor',
        'stop signs': 'stop sign', 'stop sign': 'stop sign',
        'street numbers': 'street number', 'street number': 'street number',
        'fire escapes': 'fire escape', 'fire escape': 'fire escape',
        'utility poles': 'utility pole', 'utility pole': 'utility pole', 'poles': 'pole', 'pole': 'pole',
    }
    
    # Usar cliente HTTP global para mejor rendimiento
    client = await get_http_client()
    
    for attempt in range(max_retries):
        try:
            await update(f"🛡️ Captcha intento {attempt+1}/{max_retries}...")
            
            # Verificar si hay mensaje de error/timeout y hacer clic en reintentar
            error_messages = [
                'text="Se ha superado el límite de tiempo"',
                'text="Error temporal"',
                'text="Vuelva a intentarlo"',
                'text="Time limit exceeded"',
                'text="Temporary error"',
                'text="Try again"',
            ]
            
            error_found = False
            for error_selector in error_messages:
                error_msg = await page.query_selector(error_selector)
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
                    'a:has-text("Vuelva")',
                    'button:has-text("Vuelva")',
                    '#amzn-btn-refresh',
                    '.amzn-captcha-modal button',
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
                    # Si no hay botón, recargar la página
                    await page.reload()
                    await asyncio.sleep(3)
                continue
            
            # Buscar el canvas del captcha
            canvas = await page.query_selector('#captcha-container canvas, #root canvas, .amzn-captcha-modal canvas')
            
            if not canvas:
                # No hay captcha visible, éxito
                await update("✅ No hay captcha visible")
                return True
            
            question_text = await page.evaluate('''() => {
                // Múltiples selectores para encontrar el texto del captcha
                const selectors = [
                    '.amzn-captcha-modal em',
                    '.amzn-captcha-modal b',
                    '.amzn-captcha-modal strong',
                    '.amzn-captcha-modal div[style*="margin-bottom"]',
                    '#captcha-container em',
                    '#captcha-container b',
                    '#captcha-container strong',
                    '.a-text-bold',
                    '[class*="captcha"] em',
                    '[class*="captcha"] b',
                    '[class*="captcha"] strong',
                    // Amazon US específico
                    '.cvf-widget-form-captcha em',
                    '.cvf-widget-form-captcha b',
                    'label em',
                    'label b'
                ];
                
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (el && el.textContent && el.textContent.trim().length > 0) {
                        return el.textContent.trim();
                    }
                }
                
                // Buscar en todo el texto visible que contenga patrones comunes
                const allText = document.body.innerText;
                const patterns = [
                    /click on (?:all )?(?:the )?(.+?)(?:\.|$)/i,
                    /select (?:all )?(?:the )?(.+?)(?:\.|$)/i,
                    /containing (?:a )?(.+?)(?:\.|$)/i
                ];
                
                for (const pattern of patterns) {
                    const match = allText.match(pattern);
                    if (match) return match[1].trim();
                }
                
                return '';
            }''')
            
            target_object = question_text.lower().strip()
            logger.info(f"Texto captcha detectado: '{target_object}'")
            
            # Buscar el objeto en el diccionario de traducciones
            english_object = None
            
            # Primero intentar coincidencia exacta con las claves del diccionario
            for key, eng in object_translations.items():
                if key == target_object:
                    english_object = eng
                    break
            
            # Si no hay coincidencia exacta, buscar si la clave está contenida en el texto
            if not english_object:
                for key, eng in object_translations.items():
                    if key in target_object:
                        english_object = eng
                        break
            
            # Si aún no se encontró, usar el texto original (probablemente ya está en inglés)
            if not english_object:
                # Limpiar el texto: quitar palabras comunes en inglés/español
                clean_text = target_object
                words_to_remove = [
                    'select all', 'click on', 'all squares', 'squares with', 'containing',
                    'selecciona', 'todas las', 'imágenes', 'imagenes', 'con', 'que contengan',
                    'haz clic', 'click', 'images', 'with', 'the', 'a', 'an'
                ]
                for word in words_to_remove:
                    clean_text = clean_text.replace(word, '')
                english_object = clean_text.strip()
            
            logger.info(f"Objeto a buscar: '{english_object}'")
            await update(f"🔍 Buscando: {english_object}...")
            
            canvas_box = await canvas.bounding_box()
            if not canvas_box:
                continue
            
            # Capturar screenshot del captcha completo (incluyendo margen extra)
            margin = 5
            screenshot_bytes = await page.screenshot(clip={
                'x': max(0, canvas_box['x'] - margin),
                'y': max(0, canvas_box['y'] - margin),
                'width': canvas_box['width'] + margin * 2,
                'height': canvas_box['height'] + margin * 2
            })
            
            image_base64 = base64.b64encode(screenshot_bytes).decode('utf-8')
            
            # Detectar dimensiones de la cuadrícula (normalmente 4x4 o 3x3)
            grid_cols = 4
            grid_rows = 4
            cell_width = canvas_box['width'] / grid_cols
            cell_height = canvas_box['height'] / grid_rows
            
            logger.info(f"Grid detectada: {grid_cols}x{grid_rows}, celda: {cell_width:.0f}x{cell_height:.0f}")
            
            await update(f"📤 Enviando a Anti-Captcha... ({english_object})")
            
            # Usar ImageToCoordinatesTask con instrucciones muy claras
            create_payload = {
                "clientKey": ANTICAPTCHA_API_KEY,
                "task": {
                    "type": "ImageToCoordinatesTask",
                    "body": image_base64,
                    "comment": f"Click on the CENTER of each image that contains a {english_object}. This is a 4x4 grid captcha.",
                    "mode": "points"
                }
            }
            
            response = await client.post(
                'https://api.anti-captcha.com/createTask',
                json=create_payload
            )
            result = response.json()
            
            if result.get('errorId') != 0:
                await update(f"⚠️ Error Anti-Captcha: {result.get('errorDescription', 'desconocido')}")
                continue
            
            task_id = result.get('taskId')
            await update(f"⏳ Procesando captcha (Task: {task_id})...")
            
            # Timeout más corto: 20 intentos x 2 seg = 40 segundos máximo
            for poll in range(20):
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
                
                status = check_result.get('status')
                
                if status == 'ready':
                    solution = check_result.get('solution', {})
                    coordinates = solution.get('coordinates', [])
                    
                    if coordinates:
                        await update(f"✅ Recibido: {len(coordinates)} puntos, aplicando...")
                        logger.info(f"Coordenadas recibidas: {coordinates}")
                        
                        # Ajustar por el margen añadido al screenshot
                        margin = 5
                        
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
                            
                            logger.info(f"Click {i+1}: canvas({rel_x:.0f},{rel_y:.0f}) -> página({click_x:.0f},{click_y:.0f})")
                            
                            # Click con movimiento previo para simular humano
                            await page.mouse.move(click_x, click_y)
                            await asyncio.sleep(random.uniform(0.1, 0.2))
                            await page.mouse.click(click_x, click_y)
                            await asyncio.sleep(random.uniform(0.4, 0.7))
                        
                        await asyncio.sleep(1)
                        
                        # Buscar botón de confirmar con múltiples selectores
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
                            '#cvf-submit-captcha'
                        ]
                        
                        confirm_btn = None
                        for sel in confirm_selectors:
                            try:
                                confirm_btn = await page.query_selector(sel)
                                if confirm_btn and await confirm_btn.is_visible():
                                    logger.info(f"Botón confirmar encontrado: {sel}")
                                    break
                                confirm_btn = None
                            except:
                                continue
                        
                        if confirm_btn:
                            await confirm_btn.click()
                            await asyncio.sleep(3)
                            
                            still_visible = await page.query_selector('#captcha-container canvas, #root canvas, .amzn-captcha-modal canvas')
                            if still_visible:
                                await update("🔄 Otro captcha detectado...")
                                break  # Salir del poll loop para reintentar
                            else:
                                await update("✅ Captcha resuelto!")
                                return True
                        else:
                            # Si no hay botón, quizás el captcha se auto-confirma
                            logger.info("No se encontró botón confirmar, esperando...")
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
                    await update(f"⚠️ Estado: {status}")
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

# ==================== CREACIÓN DE CUENTA ====================

async def create_amazon_account(status_callback=None):
    """Crea cuenta Amazon US y retorna los datos"""
    
    async def update_status(msg):
        if status_callback:
            await status_callback(msg)
    
    await update_status("📱 Ordenando número de teléfono...")
    order_id, phone = await order_smspool_number()
    
    if not phone:
        await update_status("❌ Error: Sin saldo en SMS Pool")
        return None
    
    await update_status(f"✅ Número obtenido: {phone}")
    
    password = f"Pass{random.randint(1000, 9999)}{uuid.uuid4().hex[:8]}"
    first_name = ''.join(random.choices('abcdefghijklmnopqrstuvwxyz', k=5)).capitalize()
    last_name = ''.join(random.choices('abcdefghijklmnopqrstuvwxyz', k=5)).capitalize()
    fullname = f"{first_name} {last_name}"
    
    # Generar session ID único para IP diferente en cada ejecución
    session_id = uuid.uuid4().hex[:8]
    
    async with async_playwright() as p:
        # Rotar entre navegadores para evitar detección
        browsers = ['chromium', 'firefox', 'webkit']
        browser_choice = random.choice(browsers)
        logger.info(f"Usando navegador: {browser_choice}, session: {session_id}")
        
        browser_args = [
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-blink-features=AutomationControlled',
            '--start-maximized',
            '--disable-web-security',
            '--disable-features=IsolateOrigins,site-per-process',
        ]
        
        # Seleccionar navegador
        if browser_choice == 'firefox':
            browser = await p.firefox.launch(
                headless=False,
                args=[]  # Firefox no soporta los mismos args
            )
        elif browser_choice == 'webkit':
            browser = await p.webkit.launch(
                headless=False,
                args=[]
            )
        else:
            browser = await p.chromium.launch(
                headless=False,
                args=browser_args
            )
        
        # Configurar proxy con session ID para IP diferente
        # El session ID fuerza una nueva IP en proxies rotativos
        proxy_user_with_session = f"{PROXY_USER}-session-{session_id}"
        
        proxy_config = {
            'server': f'http://{PROXY_HOST}:{PROXY_PORT}',
            'username': proxy_user_with_session,
            'password': PROXY_PASS
        }
        
        # User agents variados por navegador
        user_agents_by_browser = {
            'chromium': [
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
            ],
            'firefox': [
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0',
            ],
            'webkit': [
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Safari/605.1.15',
            ]
        }
        
        selected_ua = random.choice(user_agents_by_browser.get(browser_choice, USER_AGENTS))
        
        # Contexto limpio sin cookies ni caché previo
        context = await browser.new_context(
            user_agent=selected_ua,
            viewport={'width': random.randint(1280, 1920), 'height': random.randint(720, 1080)},
            locale='en-US',
            timezone_id='America/New_York',
            proxy=proxy_config,
            ignore_https_errors=True,
            # Sin estado previo - contexto completamente limpio
            storage_state=None
        )
        
        # Limpiar cualquier cookie residual
        await context.clear_cookies()
        
        # Scripts anti-detección mejorados
        await context.add_init_script("""
            // Ocultar webdriver
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            
            // Simular Chrome
            window.chrome = {runtime: {}, loadTimes: function(){}, csi: function(){}};
            
            // Ocultar plugins vacíos
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
            
            // Ocultar languages
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
            
            // Permisos
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                Promise.resolve({ state: Notification.permission }) :
                originalQuery(parameters)
            );
            
            // WebGL vendor
            const getParameter = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(parameter) {
                if (parameter === 37445) return 'Intel Inc.';
                if (parameter === 37446) return 'Intel Iris OpenGL Engine';
                return getParameter.apply(this, arguments);
            };
        """)
        
        page = await context.new_page()
        
        try:
            await update_status(f"🌐 Navegando ({browser_choice})...")
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
            
            # Limpiar número de teléfono - extraer solo los 10 dígitos
            phone_digits = re.sub(r'\D', '', phone)  # Solo dígitos
            if phone_digits.startswith('1') and len(phone_digits) == 11:
                phone_digits = phone_digits[1:]  # Quitar el 1 de US
            if len(phone_digits) > 10:
                phone_digits = phone_digits[-10:]  # Últimos 10 dígitos
            
            logger.info(f"Número original: {phone}, limpio: {phone_digits}")
            
            # Código de país +1 (USA)
            try:
                # Buscar el dropdown de código de país
                country_dropdown = await page.query_selector('#auth-country-picker, select[name="countryCode"], .country-picker select, #ap_phone_country_code')
                if country_dropdown:
                    await country_dropdown.select_option(value='US')
                    await asyncio.sleep(0.3)
                    logger.info("Código de país cambiado a +1 (US)")
                else:
                    # Intentar hacer clic en el selector de país y elegir US
                    country_btn = await page.query_selector('.country-display, .country-picker-trigger, [data-country-code]')
                    if country_btn:
                        await country_btn.click()
                        await asyncio.sleep(0.5)
                        # Buscar opción de US
                        us_option = await page.query_selector('[data-country-code="US"], [value="US"], li:has-text("+1")')
                        if us_option:
                            await us_option.click()
                            await asyncio.sleep(0.3)
                            logger.info("Código de país seleccionado: +1 (US)")
            except Exception as e:
                logger.warning(f"No se pudo cambiar código de país: {e}")
            
            await page.fill('#ap_email', phone_digits)
            await asyncio.sleep(0.5)
            
            await page.fill('#ap_password', password)
            await asyncio.sleep(0.5)
            
            try:
                await page.fill('#ap_password_check', password, timeout=5000)
            except:
                pass
            
            # Verificar y resolver captcha ANTES de enviar formulario
            await asyncio.sleep(1)
            captcha_grid = await page.query_selector('#captcha-container canvas, #root canvas, .amzn-captcha-modal canvas, #auth-captcha-image')
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
                except Exception as e:
                    continue
            
            if not submit_clicked:
                # Forzar click con JavaScript
                await page.evaluate('document.querySelector("#continue")?.click()')
                await asyncio.sleep(1)
            
            await asyncio.sleep(2)
            
            try:
                await page.wait_for_load_state('domcontentloaded', timeout=30000)
            except:
                pass
            
            await asyncio.sleep(3)
            
            # Resolver captcha
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
                        await update_status("⚠️ Orden SMS cancelada por SMSPool")
                        break
                    verification_code = code
                    await update_status(f"✅ Código SMS detectado: {verification_code}")
                    break
                    
                await asyncio.sleep(8)  # Reducido a 8 segundos para respuesta más rápida
            
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
                    'input[name="cvf_otp_code"]',
                    '#auth-mfa-otpcode',
                    'input[autocomplete="one-time-code"]',
                    '.cvf-widget-input-code input',
                ]
                
                code_entered = False
                for selector in code_selectors:
                    try:
                        element = await page.wait_for_selector(selector, timeout=3000)
                        if element:
                            await element.click()
                            await asyncio.sleep(0.2)
                            # Escribir código carácter por carácter
                            for char in verification_code:
                                await page.keyboard.type(char)
                                await asyncio.sleep(random.uniform(0.05, 0.12))
                            await update_status(f"📝 Código ingresado en: {selector}")
                            code_entered = True
                            break
                    except:
                        continue
                
                if not code_entered:
                    # Intentar con fill directo
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
                    '#a-autoid-0',
                    'input[type="submit"]',
                    'button[type="submit"]',
                    '.a-button-primary button',
                    'button.a-button-text',
                    '#auth-signin-button',
                    'span.a-button-inner button',
                    'input.a-button-input',
                    '#continue',
                    '#auth-mfa-verify-button',
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
                    # Intentar presionar Enter como alternativa
                    await page.keyboard.press('Enter')
                    await update_status("📤 Enviado con Enter")
                
                try:
                    await page.wait_for_load_state('domcontentloaded', timeout=15000)
                except:
                    pass
                await asyncio.sleep(2)
                
                # Verificar otro captcha
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
    """Comando /gcookie - Genera cookies de Amazon (Optimizado con semáforo)"""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    # Verificar si el usuario ya tiene un trabajo en progreso
    if user_id in active_jobs:
        await update.message.reply_text(
            "⏳ *Ya tienes una generación en progreso*\n\n"
            "Espera a que termine antes de iniciar otra.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Verificar capacidad
    if job_semaphore.locked() and len(active_jobs) >= MAX_CONCURRENT_JOBS:
        queue_position = len(active_jobs) - MAX_CONCURRENT_JOBS + 1
        await update.message.reply_text(
            f"🔄 *Cola de espera*\n\n"
            f"Hay {len(active_jobs)} trabajos en progreso.\n"
            f"Tu posición en cola: ~{queue_position}\n\n"
            f"Intenta de nuevo en 1-2 minutos.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Registrar trabajo activo
    active_jobs[user_id] = time.time()
    stats['total'] += 1
    
    # Mensaje inicial con animación
    status_message = await update.message.reply_text(
        "🍪 *Generando Cookies US*\n\n"
        "⏳ *Procesando...*\n"
        "🍪 ⏳ 🍪",
        parse_mode=ParseMode.MARKDOWN
    )
    
    # Frames de animación de cookies
    cookie_frames = [
        "🍪 ⏳ 🍪",
        "🍪🍪 ⏳ 🍪🍪",
        "🍪🍪🍪 ⏳ 🍪🍪🍪",
        "🍪🍪🍪🍪 ⏳ 🍪🍪🍪🍪",
        "🍪🍪🍪 ⏳ 🍪🍪🍪",
        "🍪🍪 ⏳ 🍪🍪",
    ]
    frame_index = [0]
    last_update = [0]  # Rate limiting para ediciones
    
    async def update_status(msg):
        """Callback para mostrar animación de cookies (con rate limiting)"""
        now = time.time()
        # Solo actualizar cada 1.5 segundos para evitar flood
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
        # Usar semáforo para limitar concurrencia
        async with job_semaphore:
            # Timeout para el trabajo completo
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
        # Remover trabajo activo
        active_jobs.pop(user_id, None)
    
    if result:
        # Formatear respuesta con datos de la cuenta
        phone = result['phone']
        password = result['password']
        fullname = result['fullname']
        cookies = result['cookies']
        
        # Mensaje final con formato monoespaciado citado
        success_message = f"""
✅ *¡Cuenta Amazon US creada exitosamente!*

👤 *Nombre:* `{fullname}`
📱 *Teléfono:*
```
{phone}
```
🔑 *Contraseña:*
```
{password}
```
🍪 *Cookies:*
```
{cookies}
```
⏰ Generado: {time.strftime('%Y-%m-%d %H:%M:%S')}
"""
        await status_message.edit_text(success_message, parse_mode=ParseMode.MARKDOWN)
        stats['success'] += 1
        
        # Guardar en archivo local (async-safe usando thread)
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
            # Fallback si no hay aiofiles
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
            "• Amazon bloqueó la solicitud\n\n"
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
    """
    await update.message.reply_text(stats_text, parse_mode=ParseMode.MARKDOWN)

# ==================== MAIN ====================

async def cleanup(application):
    """Limpieza al cerrar el bot"""
    await close_http_client()
    logger.info("Cliente HTTP cerrado")

def main():
    """Función principal del bot - Optimizado"""
    print("""
    ╔══════════════════════════════════════════════════╗
    ║   BOT TELEGRAM - AMAZON US COOKIE GENERATOR      ║
    ║   ⚡ Optimizado para alta carga                  ║
    ╚══════════════════════════════════════════════════╝
    """)
    
    if TELEGRAM_BOT_TOKEN == "TU_BOT_TOKEN_AQUI":
        print("⚠️ ERROR: Configura TELEGRAM_BOT_TOKEN con tu token de @BotFather")
        print("   1. Habla con @BotFather en Telegram")
        print("   2. Usa /newbot para crear un bot")
        print("   3. Copia el token y pégalo en TELEGRAM_BOT_TOKEN")
        return
    
    # Crear aplicación del bot con configuración optimizada
    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .concurrent_updates(True)  # Procesar updates en paralelo
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
    print(f"📱 Comandos: /gcookie, /stats, /help")
    print("🛑 Presiona Ctrl+C para detener")
    
    # Iniciar el bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
