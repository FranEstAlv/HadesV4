import asyncio
import random
import time
import uuid
import logging
import httpx
import re
import base64
import json
import os
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
ANTICAPTCHA_API_KEY = "30d75186e63a96b635959c5e854e1fab" #cecd0963cb6667a1d9ec1131829b06ad"
SMSPOOL_API_KEY = "DlQH1Pvt2NM0TP7u46Wh8DvbhN0VYOmN"
SMSPOOL_BASE_URL = "https://api.smspool.net"
SMSPOOL_SERVICE_ID = "39"
SMSPOOL_COUNTRY_CODE = "US"  # USA

# ⚡ OPTIMIZACIÓN: Límites de concurrencia
MAX_CONCURRENT_JOBS = 2  # Reducido a 2 en Termux para evitar OOM (Out of Memory) en el teléfono
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
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36'
]

# Configuración de logging optimizada
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== HTTP CLIENT POOL ====================

def cargar_proxies():
    if not os.path.exists(PROXIES_FILE):
        return []
    with open(PROXIES_FILE) as f:
        return [l.strip() for l in f if l.strip() and not l.startswith("#")]


# 🌐 Configuración de Proxy (Oxylabs / Tu Proveedor - IP rotativa)
PROXIES_FILE = "proxies.txt"
proxies_list = cargar_proxies()
proxy_actual = random.choice(proxies_list)
partes = proxy_actual.split(":")

PROXY_HOST = "140.99.194.175" #partes[2]
PROXY_PORT = "7552" #partes[3]
PROXY_USER = "iqgbcwxy" #partes[0]
PROXY_PASS = "v2krgc0qmmv5" #partes[1]

print("proxie:")
print(PROXY_HOST,":",PROXY_PORT,":",PROXY_USER,";",PROXY_PASS)

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
    if not SMSPOOL_API_KEY:
        logger.error("SMSPool API Key no configurada")
        return None, None
        
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
            
            if data.get('success') == 1 or 'number' in data:
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
            status = data.get('status')
            
            if status == 3 or status == '3':
                sms_code = data.get('sms', '')
                if sms_code:
                    logger.info(f"SMS recibido RAW: {sms_code}")
                    code_match = re.search(r'\b\d{6,10}\b', str(sms_code))
                    if code_match:
                        extracted_code = code_match.group()
                        logger.info(f"Código extraído: {extracted_code}")
                        return extracted_code
                    return str(sms_code).strip()
                    
            elif status == 2 or status == '2':
                logger.warning("Orden SMS cancelada")
                return "CANCELLED"
                
            elif status == 1 or status == '1':
                time_left = data.get('time_left', 0)
                logger.info(f"SMS pendiente, tiempo restante: {time_left}s")
                return None
                
            else:
                sms_text = data.get('sms', '')
                if sms_text and len(str(sms_text)) > 0:
                    logger.info(f"SMS encontrado en respuesta: {sms_text}")
                    code_match = re.search(r'\b\d{6,10}\b', str(sms_text))
                    if code_match:
                        return code_match.group()
        return None
    except Exception as e:
        logger.error(f"Exception checking SMS: {e}")
        return None

async def cancel_smspool_order(order_id):
    """Cancela orden SMS (async)"""
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
    
    object_translations = {
        'sillas': 'chair', 'silla': 'chair', 'autobuses': 'bus', 'autobús': 'bus', 'autobus': 'bus',
        'motocicletas': 'motorcycle', 'motocicleta': 'motorcycle', 'motos': 'motorcycle', 'moto': 'motorcycle',
        'bicicletas': 'bicycle', 'bicicleta': 'bicycle', 'bici': 'bicycle', 'semáforos': 'traffic light',
        'semaforos': 'traffic light', 'semáforo': 'traffic light', 'semaforo': 'traffic light', 'coches': 'car',
        'coche': 'car', 'autos': 'car', 'auto': 'car', 'carros': 'car', 'carro': 'car', 'camiones': 'truck',
        'camión': 'truck', 'camion': 'truck', 'aviones': 'airplane', 'avión': 'airplane', 'avion': 'airplane',
        'barcos': 'boat', 'barco': 'boat', 'trenes': 'train', 'tren': 'train', 'bolsas': 'bag', 'bolsa': 'bag',
        'baldes': 'bucket', 'balde': 'bucket', 'cubetas': 'bucket', 'cubeta': 'bucket', 'hidrantes': 'fire hydrant',
        'hidrante': 'fire hydrant', 'escaleras': 'stairs', 'escalera': 'stairs', 'palmeras': 'palm tree',
        'palmera': 'palm tree', 'puentes': 'bridge', 'puente': 'bridge', 'cruces peatonales': 'crosswalk',
        'cruce peatonal': 'crosswalk', 'paso de peatones': 'crosswalk', 'pasos de cebra': 'crosswalk',
        'montañas': 'mountain', 'montaña': 'mountain', 'chimeneas': 'chimney', 'chimenea': 'chimney',
        'taxis': 'taxi', 'taxi': 'taxi', 'señales de tráfico': 'traffic sign', 'bus stops': 'bus stop',
        'storefronts': 'storefront', 'storefront': 'storefront'
    }
    
    client = await get_http_client()
    
    for attempt in range(max_retries):
        try:
            await update(f"🛡️ Captcha intento {attempt+1}/{max_retries}...")
            
            error_messages = [
                'text="Se ha superado el límite de tiempo"', 'text="Error temporal"',
                'text="Vuelva a intentarlo"', 'text="Time limit exceeded"', 'text="Try again"'
            ]
            
            error_found = False
            for error_selector in error_messages:
                error_msg = await page.query_selector(error_selector)
                if error_msg:
                    error_found = True
                    break
            
            if error_found:
                await update("⚠️ Error en captcha, reintentando...")
                await asyncio.sleep(2)
                
                retry_selectors = ['button:has-text("Retry")', '#amzn-btn-refresh', '.amzn-captcha-modal button']
                for selector in retry_selectors:
                    try:
                        retry_btn = await page.query_selector(selector)
                        if retry_btn and await retry_btn.is_visible():
                            await retry_btn.click()
                            await asyncio.sleep(2)
                            break
                    except:
                        continue
                continue
            
            canvas = await page.query_selector('#captcha-container canvas, #root canvas, .amzn-captcha-modal canvas')
            if not canvas:
                return True
            
            question_text = await page.evaluate('''() => {
                const selectors = ['.amzn-captcha-modal em', '.amzn-captcha-modal strong', '#captcha-container em', '.a-text-bold', '.cvf-widget-form-captcha em'];
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (el && el.textContent) return el.textContent.trim();
                }
                return '';
            }''')
            
            target_object = question_text.lower().strip()
            english_object = object_translations.get(target_object, target_object)
            
            canvas_box = await canvas.bounding_box()
            if not canvas_box:
                continue
            
            screenshot_bytes = await page.screenshot(clip={
                'x': max(0, canvas_box['x'] - 5),
                'y': max(0, canvas_box['y'] - 5),
                'width': canvas_box['width'] + 10,
                'height': canvas_box['height'] + 10
            })
            
            image_base64 = base64.b64encode(screenshot_bytes).decode('utf-8')
            
            create_payload = {
                "clientKey": ANTICAPTCHA_API_KEY,
                "task": {
                    "type": "ImageToCoordinatesTask",
                    "body": image_base64,
                    "comment": f"Click on objects: {english_object}",
                    "mode": "points"
                }
            }
            
            response = await client.post('https://api.anti-captcha.com/createTask', json=create_payload)
            result = response.json()
            
            if result.get('errorId') != 0:
                continue
            
            task_id = result.get('taskId')
            
            for poll in range(20):
                await asyncio.sleep(3)
                check_response = await client.post('https://api.anti-captcha.com/getTaskResult', json={"clientKey": ANTICAPTCHA_API_KEY, "taskId": task_id})
                check_result = check_response.json()
                
                if check_result.get('status') == 'ready':
                    coordinates = check_result.get('solution', {}).get('coordinates', [])
                    
                    for coord in coordinates:
                        rel_x = (coord.get('x') if isinstance(coord, dict) else coord) - 5
                        rel_y = (coord.get('y') if isinstance(coord, dict) else coord) - 5
                        
                        click_x = canvas_box['x'] + max(0, min(rel_x, canvas_box['width']))
                        click_y = canvas_box['y'] + max(0, min(rel_y, canvas_box['height']))
                        
                        await page.mouse.click(click_x, click_y)
                        await asyncio.sleep(0.5)
                    
                    confirm_selectors = ['#amzn-btn-verify-internal', 'button:has-text("Verify")', '#cvf-submit-captcha', 'button[type="submit"]']
                    for sel in confirm_selectors:
                        try:
                            confirm_btn = await page.query_selector(sel)
                            if confirm_btn and await confirm_btn.is_visible():
                                await confirm_btn.click()
                                await asyncio.sleep(3)
                                break
                        except:
                            continue
                    return True
        except Exception as e:
            logger.error(f"Error captcha: {e}")
    return False

# ==================== COMPORTAMIENTO HUMANO ====================

async def simulate_human_behavior(page):
    try:
        for _ in range(random.randint(2, 4)):
            await page.mouse.move(random.randint(100, 500), random.randint(100, 500))
            await asyncio.sleep(0.2)
    except:
        pass

# ==================== CREACIÓN DE CUENTA ====================

async def create_amazon_account(status_callback=None):
    async def update_status(msg):
        if status_callback:
            await status_callback(msg)
            
    await update_status("📱 Ordenando número de teléfono...")
    order_id, phone = await order_smspool_number()
    if not phone:
        await update_status("❌ Error: Falla de saldo en SMSPool")
        return None
        
    password = f"Pass{random.randint(1000, 9999)}{uuid.uuid4().hex[:8]}"
    fullname = f"John {''.join(random.choices('abcdefg', k=5)).capitalize()}"
    
    async with async_playwright() as p:
        # Argumentos de evasión optimizados (eliminamos anti-gpu en local)
        browser_args = [
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-blink-features=AutomationControlled',
        ]
        
        # Mantenemos headless=True pero reforzamos el contexto sigiloso
        browser = await p.chromium.launch(headless=True, args=browser_args)
        
        proxy_config = None
        if PROXY_HOST:
            proxy_config = {
                'server': f'http://{PROXY_HOST}:{PROXY_PORT}',
                'username': PROXY_USER,
                'password': PROXY_PASS
            }
            
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={'width': 1280, 'height': 800},
            locale='en-US',
            timezone_id='America/New_York',
            proxy=proxy_config
        )
        
        page = await context.new_page()
        
        try:
            await update_status("🌐 Navegando a Amazon Register...")
            await page.goto(
                'https://amazon.com',
                wait_until='commit',
                timeout=45000
            )

            print("page.goto")
            
            await asyncio.sleep(3) # Espera técnica anti-detección
            
            # [NUEVO] DETECTOR Y BYPASS DE CAPTCHA INICIAL / BLOQUEO
            if await page.query_selector('#captcha-container canvas, #root canvas, input[name="captcha_input"]'):
                await update_status("🛡️ Amazon solicitó verificación inicial. Resolviendo...")
                await solve_grid_captcha_anticaptcha(page, status_callback=update_status)
                await asyncio.sleep(3)

            print("captcha")

            # Verificar si caímos en pantalla de reintento/bloqueo de Amazon
            if "página no encontrada" in (await page.title()).lower() or await page.query_selector('text="Robot Check"'):
                await update_status("❌ IP de Proxy Quemada/Bloqueada por Amazon.")
                return None
            
            # Esperamos al input usando selectores alternativos por si cambió la interfaz mobile/desktop
            await update_status("📝 Buscando campos de texto...")
            input_found = False
            for selector in ['#ap_customer_name', 'input[name="customerName"]', '#ap_customer_name_mobile']:
                try:
                    if await page.wait_for_selector(selector, timeout=8000):
                        await page.fill(selector, fullname)
                        input_found = True
                        break
                except:
                    continue
            print("#ap_customer_name")

            if not input_found:
                # Si fallan los selectores, guardamos evidencia para que analices qué vio el bot
                await page.screenshot(path="error_bloqueo.png")
                await update_status("❌ No se encontró el formulario. Captura guardada como error_bloqueo.png")
                return None
            
            # Continuar llenando el resto de datos si el input fue hallado
            phone_digits = re.sub(r'\D', '', phone)[-10:]
            await page.fill('#ap_email', phone_digits)

            await page.fill('#ap_password', password)
            
            submit_btn = await page.query_selector('#continue, input[type="submit"]')
            if submit_btn:
                await submit_btn.click()

            print("submit")

            await asyncio.sleep(4)
            
            # Procesamiento de OTP SMS convencional...
            await update_status("📩 Esperando SMS OTP...")
            verification_code = None
            for attempt in range(12):
                code = await check_smspool_sms(order_id)
                if code:
                    verification_code = code
                    break
                await asyncio.sleep(10)

            print("SMS")

            if verification_code:
                await update_status(f"🔑 Código recibido: {verification_code}. Ingresando...")
                for sel in ['#auth-pv-enter-code', 'input[name="code"]', '#cvf_input_code', 'input[type="tel"]']:
                    try:
                        if await page.query_selector(sel):
                            await page.fill(sel, verification_code)
                            break
                    except:
                        continue
                        
                await page.keyboard.press('Enter')
                await asyncio.sleep(5)
                
                cookies = await context.cookies()
                cookie_str = '; '.join([f"{c['name']}={c['value']}" for c in cookies])
                
                print("Final")
                return {
                    'phone': phone, 'password': password, 'fullname': fullname, 'cookies': cookie_str
                }
                
        except Exception as e:
            logger.error(f"Error crítico en flujo: {e}")
        finally:
            await browser.close()
    return None

# ==================== COMANDOS DEL BOT ====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🍪 *Bot de Cookies Amazon US activo.* Usa /gcookie para iniciar.", parse_mode=ParseMode.MARKDOWN)

async def gcookie_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in active_jobs:
        await update.message.reply_text("❌ Ya tienes un proceso corriendo.")
        return
        
    active_jobs[user_id] = time.time()
    stats['total'] += 1
    
    status_message = await update.message.reply_text("⏳ *Iniciando ciclo asíncrono...*", parse_mode=ParseMode.MARKDOWN)
    
    async def update_status_tg(msg_text):
        try:
            await status_message.edit_text(f"⚙️ *Progreso:* {msg_text}", parse_mode=ParseMode.MARKDOWN)
        except:
            pass

    try:
        async with job_semaphore:
            result = await asyncio.wait_for(
                create_amazon_account(status_callback=update_status_tg),
                timeout=JOB_TIMEOUT
            )
    except Exception as e:
        result = None
        logger.error(f"Job fallido: {e}")
    finally:
        active_jobs.pop(user_id, None)
        
    if result:
        success_text = f"✅ *Cuenta Creada!*\n\n👤 *User:* `{result['fullname']}`\n📱 *Tel:* `{result['phone']}`\n🔑 *Pass:* `{result['password']}`\n\n🍪 *Cookies:* \n```\n{result['cookies']}\n```"
        await status_message.edit_text(success_text, parse_mode=ParseMode.MARKDOWN)
        stats['success'] += 1
        
        # Guardado No-Bloqueante adaptado para Termux usando hilos paralelos de procesamiento
        def save_file():
            with open('generated_accounts.txt', 'a', encoding='utf-8') as f:
                f.write(f"Tel: {result['phone']} | Pass: {result['password']} | Cookie: {result['cookies']}\n")
        await asyncio.to_thread(save_file)
    else:
        await status_message.edit_text("❌ *Error en la generación.* Revisa logs, proxies o APIs.", parse_mode=ParseMode.MARKDOWN)
        stats['failed'] += 1

def main():
    if TELEGRAM_BOT_TOKEN == "":
        print("❌ Define el TOKEN correcto del bot antes de arrancar.")
        return
        
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).concurrent_updates(True).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("gcookie", gcookie_command))
    
    print("🚀 Bot asíncrono inicializado en modo Termux.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
