#!/usr/bin/env python3

import requests
import time
import json
import random
import string
import os
import re
import logging
from datetime import datetime
import urllib3
import threading
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('amazon_generator.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

HERO_API_KEY = "e06384113bb0b59fc242A5A016589453"
BOT_TOKEN = "8042383084:AAGfhsyQUDqD1iq9eQWkEXosDfzWzNlIJrY"
CHAT_ID = "6249219406"
LAST_UPDATE_ID = 0
BASE_URL_NATIVE = "https://hero-sms.com/api/v1"
BASE_URL_LEGACY = "https://hero-sms.com/stubs/handler_api.php"

COUNTRY_IDS = {"US": 1, "UK": 2, "RU": 3, "DE": 4, "FR": 5, "ES": 6, "IT": 7, "CA": 8, "AU": 9, "BR": 10}

def send_to_telegram(text, reply_markup=None, parse_mode='HTML'):
    logger.debug(f"Enviando mensaje a Telegram: {text[:50]}...")
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": parse_mode}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        response = requests.post(url, data=payload, timeout=10)
        logger.debug(f"Respuesta Telegram: {response.status_code}")
        return response.json()
    except Exception as e:
        logger.error(f"Error enviando a Telegram: {e}")
        return None

def send_file_to_telegram(filename, caption=""):
    logger.debug(f"Enviando archivo: {filename}")
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
    try:
        with open(filename, 'rb') as f:
            files = {'document': f}
            data = {'chat_id': CHAT_ID, 'caption': caption}
            response = requests.post(url, files=files, data=data, timeout=30)
            logger.debug(f"Respuesta archivo: {response.status_code}")
            return response.status_code == 200
    except Exception as e:
        logger.error(f"Error enviando archivo: {e}")
        return False

def edit_telegram_message(message_id, text, reply_markup=None):
    logger.debug(f"Editando mensaje {message_id}")
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
    payload = {"chat_id": CHAT_ID, "message_id": message_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        response = requests.post(url, data=payload, timeout=10)
        logger.debug(f"Respuesta edit: {response.status_code}")
        return response.json()
    except Exception as e:
        logger.error(f"Error editando mensaje: {e}")
        return None

def get_updates(offset=None):
    logger.debug(f"Obteniendo updates, offset: {offset}")
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    params = {"timeout": 30}
    if offset:
        params["offset"] = offset
    try:
        response = requests.get(url, params=params, timeout=35)
        if response.status_code == 200:
            updates = response.json().get("result", [])
            logger.debug(f"Updates recibidos: {len(updates)}")
            return updates
    except Exception as e:
        logger.error(f"Error en getUpdates: {e}")
    return []

def get_main_keyboard():
    return {
        "inline_keyboard": [
            [{"text": "🆕 Generar Cookie", "callback_data": "generate_cookie"}],
            [{"text": "📊 Estado", "callback_data": "status"}, {"text": "📋 Ver Cookies", "callback_data": "list_cookies"}],
            [{"text": "📖 Ayuda", "callback_data": "help"}]
        ]
    }

def get_generating_keyboard():
    return {"inline_keyboard": [[{"text": "⏳ Generando...", "callback_data": "generating"}]]}

def get_result_keyboard():
    return {
        "inline_keyboard": [
            [{"text": "🔄 Generar Nueva Cookie", "callback_data": "generate_cookie"}],
            [{"text": "🏠 Menú Principal", "callback_data": "menu"}]
        ]
    }

def hero_sms_check_balance():
    logger.debug("Verificando saldo HeroSMS")
    params = {
        "action": "getBalance",
        "api_key": HERO_API_KEY
    }
    try:
        response = requests.get(BASE_URL_LEGACY, params=params, timeout=10)
        logger.debug(f"Respuesta balance: {response.status_code}")
        if response.status_code == 200 and "ACCESS_BALANCE" in response.text:
            balance = float(response.text.split(":")[1])
            logger.debug(f"Saldo actual: ${balance}")
            return balance
    except Exception as e:
        logger.error(f"Error verificando balance: {e}")
    return None

def hero_sms_get_number_native(country="1", service="39"):
    logger.debug(f"Intentando API nativa - País: {country}, Servicio: {service}")
    
    headers = {
        "Authorization": f"ApiKey {HERO_API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json"
    }
    
    payload = {
        "service": service,
        "country": COUNTRY_IDS.get(country, 1),
        "amount": 1,
        "verificationType": "SMS"
    }
    
    logger.debug(f"Payload nativo: {payload}")
    
    try:
        response = requests.post(
            f"{BASE_URL_NATIVE}/activations",
            json=payload,
            headers=headers,
            timeout=20
        )
        
        logger.debug(f"Status nativo: {response.status_code}")
        logger.debug(f"Response nativo: {response.text[:200]}")
        
        if response.status_code == 200:
            data = response.json()
            phone = data.get("number")
            if phone:
                phone = str(phone).replace("+", "").strip()
                logger.info(f"Número obtenido vía nativa: +{phone}")
                return {
                    "phone": phone,
                    "activation_id": data.get("activationId"),
                    "order_id": data.get("activationId")
                }
        elif response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 60))
            logger.warning(f"Rate limit nativo, esperando {retry_after}s")
            time.sleep(retry_after)
            return hero_sms_get_number_native(country, service)
        elif response.status_code == 401:
            logger.error("API Key inválida - Verificar HERO_API_KEY")
        else:
            logger.warning(f"Error nativo: {response.status_code} - {response.text}")
            
    except Exception as e:
        logger.error(f"Error en API nativa: {e}")
    
    return None

def hero_sms_get_number_legacy(country="1", service="39"):
    logger.debug(f"Intentando API heredada - País: {country}, Servicio: {service}")
    
    params = {
        "action": "getNumberV2",
        "api_key": HERO_API_KEY,
        "service": service,
        "country": COUNTRY_IDS.get(country, 1)
    }
    
    logger.debug(f"Params heredados: {params}")
    
    try:
        response = requests.get(BASE_URL_LEGACY, params=params, timeout=20)
        logger.debug(f"Status heredado: {response.status_code}")
        logger.debug(f"Response heredado: {response.text[:200]}")
        
        if response.status_code == 200:
            try:
                data = response.json()
                logger.debug(f"Datos heredados JSON: {data}")
                
                if "error" in data:
                    logger.warning(f"Error heredado: {data}")
                    return None
                
                phone = data.get("number")
                if phone:
                    phone = str(phone).replace("+", "").strip()
                    logger.info(f"Número obtenido vía heredada: +{phone}")
                    return {
                        "phone": phone,
                        "activation_id": data.get("activationId"),
                        "order_id": data.get("activationId")
                    }
            except json.JSONDecodeError:
                logger.debug(f"Respuesta no JSON: {response.text}")
                
                if "NO_BALANCE" in response.text:
                    logger.error("Saldo insuficiente")
                elif "NO_NUMBERS" in response.text:
                    logger.warning("No hay números disponibles")
                    
    except Exception as e:
        logger.error(f"Error en API heredada: {e}")
    
    return None

def hero_sms_get_number_web(country="US", service="amazon"):
    logger.debug(f"Intentando web scraping - País: {country}, Servicio: {service}")
    
    session = requests.Session()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Referer": "https://herosms.com/"
    }
    
    try:
        response = session.get("https://herosms.com/", headers=headers, timeout=30)
        logger.debug(f"Status web scraping: {response.status_code}")
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            csrf_token = None
            for input_tag in soup.find_all('input'):
                if input_tag.get('name') in ['_token', 'csrf_token']:
                    csrf_token = input_tag.get('value')
                    logger.debug(f"CSRF token encontrado: {csrf_token[:10]}...")
                    break
            
            public_api_url = "https://herosms.com/api/getNumber"
            params = {
                "api_key": HERO_API_KEY,
                "country": country,
                "service": service
            }
            
            response = session.get(public_api_url, params=params, headers=headers, timeout=20)
            logger.debug(f"Status API pública: {response.status_code}")
            logger.debug(f"Response API pública: {response.text[:200]}")
            
            if response.status_code == 200:
                try:
                    data = response.json()
                    if data.get("status") == "ok" or data.get("success"):
                        phone = data.get("number") or data.get("phone")
                        if phone:
                            phone = str(phone).replace("+", "").strip()
                            logger.info(f"Número obtenido vía web: +{phone}")
                            return {"phone": phone, "order_id": data.get("id")}
                except:
                    phone_match = re.search(r'\b\d{10,12}\b', response.text)
                    if phone_match:
                        phone = phone_match.group()
                        logger.info(f"Número extraído vía web: +{phone}")
                        return {"phone": phone}
            
            country_url = f"https://herosms.com/api/getNumber/{country}"
            response = session.get(country_url, params={"api_key": HERO_API_KEY}, headers=headers, timeout=20)
            logger.debug(f"Status country API: {response.status_code}")
            
            if response.status_code == 200:
                try:
                    data = response.json()
                    phone = data.get("number") or data.get("phone")
                    if phone:
                        phone = str(phone).replace("+", "").strip()
                        logger.info(f"Número obtenido vía country: +{phone}")
                        return {"phone": phone}
                except:
                    pass
                    
    except Exception as e:
        logger.error(f"Error en web scraping: {e}")
    
    return None

def hero_sms_get_number_5sim():
    logger.debug("Intentando 5sim como respaldo")
    try:
        url = "https://5sim.net/v1/user/buy/activation/usa/any/amazon"
        response = requests.get(url, timeout=15)
        logger.debug(f"Status 5sim: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            phone = data.get("phone")
            if phone:
                logger.info(f"Número obtenido vía 5sim: +{phone}")
                return {"phone": phone, "order_id": data.get("id")}
    except Exception as e:
        logger.error(f"Error en 5sim: {e}")
    return None

def hero_sms_get_number(country="US", service="amazon"):
    logger.info(f"Iniciando obtención de número - País: {country}, Servicio: {service}")
    
    balance = hero_sms_check_balance()
    if balance is not None:
        logger.debug(f"Saldo disponible: ${balance}")
        if balance < 1.0:
            logger.warning(f"Saldo insuficiente: ${balance}")
            return None
    
    result = hero_sms_get_number_native(country, service)
    if result:
        return result
    
    time.sleep(1)
    logger.debug("Cambiando a método heredado")
    result = hero_sms_get_number_legacy(country, service)
    if result:
        return result
    
    time.sleep(1)
    logger.debug("Cambiando a web scraping")
    result = hero_sms_get_number_web(country, service)
    if result:
        return result
    
    time.sleep(1)
    logger.debug("Cambiando a 5sim")
    result = hero_sms_get_number_5sim()
    if result:
        return result
    
    logger.error("Todos los métodos fallaron para obtener número")
    return None

def generate_user_data():
    logger.debug("Generando datos de usuario")
    first_names = ["John", "Jane", "Michael", "Sarah", "David", "Emma", "James", "Maria", "Robert", "Lisa", "William", "Patricia"]
    last_names = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez", "Martinez"]
    domains = ["gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "protonmail.com", "mail.com"]
    
    random_string = ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))
    email = f"{random_string}@{random.choice(domains)}"
    password = ''.join(random.choices(string.ascii_letters + string.digits + "!@#$%^&*", k=14))
    
    user_data = {
        "first_name": random.choice(first_names),
        "last_name": random.choice(last_names),
        "email": email,
        "password": password
    }
    logger.debug(f"Datos generados: {user_data['email']}")
    return user_data

def generate_cookie(message_id=None):
    logger.info("Iniciando generación de cookie")
    start_time = time.time()
    
    if message_id:
        logger.debug(f"Editando mensaje {message_id}")
        edit_telegram_message(
            message_id,
            "🔄 Iniciando generación...\n📱 Obteniendo número...",
            reply_markup=get_generating_keyboard()
        )
    
    sms_data = hero_sms_get_number(country="US", service="amazon")
    
    if not sms_data:
        logger.error("No se pudo obtener número")
        error_msg = "❌ No se pudo obtener número\n\n"
        error_msg += "💡 Soluciones:\n"
        error_msg += "1. HeroSMS puede estar caído\n"
        error_msg += "2. Verifica tu API Key\n"
        error_msg += "3. Saldo insuficiente\n"
        error_msg += "4. Intenta más tarde"
        
        if message_id:
            edit_telegram_message(message_id, error_msg, reply_markup=get_main_keyboard())
        else:
            send_to_telegram(error_msg, reply_markup=get_main_keyboard())
        return
    
    phone = sms_data.get("phone")
    order_id = sms_data.get("order_id")
    activation_id = sms_data.get("activation_id")
    
    logger.info(f"Teléfono obtenido: +{phone}")
    logger.debug(f"Order ID: {order_id}, Activation ID: {activation_id}")
    
    user_data = generate_user_data()
    logger.info(f"Email generado: {user_data['email']}")
    
    result = {
        "success": True,
        "phone": phone,
        "email": user_data['email'],
        "password": user_data['password'],
        "first_name": user_data['first_name'],
        "last_name": user_data['last_name'],
        "order_id": order_id,
        "activation_id": activation_id,
        "created_at": datetime.now().isoformat()
    }
    
    filename = f"amazon_cookie_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    try:
        with open(filename, 'w') as f:
            json.dump(result, f, indent=2)
        logger.info(f"Archivo guardado: {filename}")
    except Exception as e:
        logger.error(f"Error guardando archivo: {e}")
    
    elapsed_time = time.time() - start_time
    logger.info(f"Cookie generada en {elapsed_time:.2f} segundos")
    
    message = f"""
🎯 <b>¡COOKIE AMAZON GENERADA!</b>

📋 <b>Credenciales:</b>
├─ 📧 Email: <code>{user_data['email']}</code>
├─ 🔑 Password: <code>{user_data['password']}</code>
├─ 📱 Teléfono: <code>+{phone}</code>
├─ 👤 Nombre: {user_data['first_name']} {user_data['last_name']}
├─ 🕒 Generado: {datetime.now().strftime('%H:%M:%S')}
└─ ⏱️ Tiempo: {elapsed_time:.1f}s

📁 <b>Archivo:</b> {filename}
    """
    
    if message_id:
        edit_telegram_message(message_id, message, reply_markup=get_result_keyboard())
    else:
        send_to_telegram(message, reply_markup=get_result_keyboard())
    
    file_sent = send_file_to_telegram(filename, f"Cookie Amazon - {user_data['email']}")
    logger.debug(f"Archivo enviado: {file_sent}")
    
    try:
        os.remove(filename)
        logger.debug(f"Archivo eliminado: {filename}")
    except:
        pass
    
    logger.info("Proceso completado exitosamente")

def process_callback(callback_data, message_id):
    logger.info(f"Procesando callback: {callback_data} de mensaje {message_id}")
    
    if callback_data == "generate_cookie":
        logger.debug("Iniciando thread de generación")
        thread = threading.Thread(target=generate_cookie, args=(message_id,))
        thread.daemon = True
        thread.start()
        logger.debug("Thread iniciado")
        
    elif callback_data == "status":
        logger.debug("Callback status")
        balance = hero_sms_check_balance()
        status_text = f"📊 <b>Estado del Sistema</b>\n\n"
        status_text += f"💰 Saldo: ${balance if balance is not None else 'N/A'}\n"
        status_text += f"🤖 Bot: Activo\n"
        status_text += f"🔑 API Key: {'Configurada' if HERO_API_KEY else 'No configurada'}"
        send_to_telegram(status_text, reply_markup=get_main_keyboard())
        
    elif callback_data == "list_cookies":
        logger.debug("Callback list_cookies")
        cookie_files = [f for f in os.listdir() if f.startswith("amazon_cookie_")]
        text = f"📋 Cookies generadas: {len(cookie_files)}\n\n"
        for f in cookie_files[-10:]:
            text += f"• {f}\n"
        if not cookie_files:
            text += "No hay cookies guardadas"
        send_to_telegram(text, reply_markup=get_main_keyboard())
        
    elif callback_data == "help":
        logger.debug("Callback help")
        help_text = "📖 <b>Ayuda</b>\n\n"
        help_text += "1. Presiona 'Generar Cookie'\n"
        help_text += "2. Espera 1-2 minutos\n"
        help_text += "3. Recibe tus credenciales\n\n"
        help_text += "🔍 <b>Detalles:</b>\n"
        help_text += "• Usa HeroSMS para números\n"
        help_text += "• Genera emails aleatorios\n"
        help_text += "• Guarda cookie en JSON\n"
        help_text += "• Envía archivo por Telegram"
        send_to_telegram(help_text, reply_markup=get_main_keyboard())
        
    elif callback_data == "menu":
        logger.debug("Callback menu")
        send_to_telegram("🏠 Menú Principal", reply_markup=get_main_keyboard())
        
    elif callback_data == "generating":
        logger.debug("Callback generating - ignorar")
        pass
        
    else:
        logger.warning(f"Callback desconocido: {callback_data}")

def main():
    global LAST_UPDATE_ID
    
    logger.info("="*60)
    logger.info("INICIANDO GENERADOR AMAZON + HERO SMS v4.0")
    logger.info("="*60)
    logger.info(f"Bot Token: {BOT_TOKEN[:10]}...")
    logger.info(f"Chat ID: {CHAT_ID}")
    logger.info(f"API Key: {HERO_API_KEY[:10]}...")
    
    try:
        send_to_telegram(
            "🎯 <b>¡GENERADOR DE COOKIES AMAZON!</b>\n\n"
            "Presiona 'Generar Cookie' para crear una cuenta\n"
            "y recibir tus credenciales.\n\n"
            "📊 Estado: <b>ACTIVO</b>",
            reply_markup=get_main_keyboard()
        )
        logger.info("Mensaje de bienvenida enviado")
    except Exception as e:
        logger.error(f"Error enviando mensaje de bienvenida: {e}")
    
    logger.info("Bot iniciado, esperando comandos...")
    
    while True:
        try:
            offset = LAST_UPDATE_ID + 1 if LAST_UPDATE_ID else None
            updates = get_updates(offset)
            
            for update in updates:
                update_id = update.get("update_id")
                if update_id and update_id > LAST_UPDATE_ID:
                    LAST_UPDATE_ID = update_id
                    logger.debug(f"Update ID actualizado: {LAST_UPDATE_ID}")
                
                callback_query = update.get("callback_query")
                if callback_query:
                    data = callback_query.get("data")
                    message = callback_query.get("message", {})
                    message_id = message.get("message_id")
                    
                    if data and message_id:
                        logger.debug(f"Callback recibido: {data} de {message_id}")
                        process_callback(data, message_id)
                
                message = update.get("message")
                if message:
                    text = message.get("text")
                    if text == "/start":
                        logger.info("Comando /start recibido")
                        send_to_telegram(
                            "🎯 ¡Bienvenido al Generador de Cookies Amazon!\n\n"
                            "Usa el menú para comenzar.",
                            reply_markup=get_main_keyboard()
                        )
            
            time.sleep(1)
            
        except KeyboardInterrupt:
            logger.info("Bot detenido por usuario")
            break
        except Exception as e:
            logger.error(f"Error en bucle principal: {e}", exc_info=True)
            time.sleep(5)

if __name__ == "__main__":
    main()