#!/usr/bin/env python3
"""
Generador de Cookies Amazon - Usando Web Scraping de HeroSMS
"""

import requests
import time
import json
import random
import string
import os
import re
from datetime import datetime
import urllib3
import threading
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- CONFIGURACIÓN ---
HERO_API_KEY = "e06384113bb0b59fc242A5A016589453"
BOT_TOKEN = "8042383084:AAGfhsyQUDqD1iq9eQWkEXosDfzWzNlIJrY"
CHAT_ID = "6249219406"
LAST_UPDATE_ID = 0

# --- FUNCIONES DE TELEGRAM (MANTENER IGUAL) ---
def send_to_telegram(text, reply_markup=None, parse_mode='HTML'):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": parse_mode}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        response = requests.post(url, data=payload, timeout=10)
        return response.json()
    except:
        return None

def send_file_to_telegram(filename, caption=""):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
    try:
        with open(filename, 'rb') as f:
            files = {'document': f}
            data = {'chat_id': CHAT_ID, 'caption': caption}
            response = requests.post(url, files=files, data=data, timeout=30)
            return response.status_code == 200
    except:
        return False

def edit_telegram_message(message_id, text, reply_markup=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
    payload = {"chat_id": CHAT_ID, "message_id": message_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        response = requests.post(url, data=payload, timeout=10)
        return response.json()
    except:
        return None

def get_updates(offset=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    params = {"timeout": 30}
    if offset:
        params["offset"] = offset
    try:
        response = requests.get(url, params=params, timeout=35)
        if response.status_code == 200:
            return response.json().get("result", [])
    except:
        pass
    return []

# --- BOTONES ---
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

# --- NUEVA FUNCIÓN: USAR WEB SCRAPING DE HERO SMS ---
def hero_sms_get_number_web():
    """
    Obtiene número usando la interfaz web de HeroSMS
    Esto funciona incluso si la API está caída
    """
    print("🌐 Usando interfaz web de HeroSMS...")
    
    session = requests.Session()
    
    # Headers realistas
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Referer": "https://herosms.com/"
    }
    
    try:
        # 1. Ir a la página principal
        print("🔄 Cargando HeroSMS...")
        response = session.get("https://herosms.com/", headers=headers, timeout=30)
        
        if response.status_code == 200:
            print("✅ Página cargada")
            
            # 2. Buscar el formulario de obtener número
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Buscar inputs ocultos con tokens
            csrf_token = None
            for input_tag in soup.find_all('input'):
                if input_tag.get('name') == '_token' or input_tag.get('name') == 'csrf_token':
                    csrf_token = input_tag.get('value')
                    break
            
            # 3. Intentar obtener número directamente
            # Primero probamos con la API pública
            public_api_url = "https://herosms.com/api/getNumber"
            params = {
                "api_key": HERO_API_KEY,
                "country": "US",
                "service": "amazon"
            }
            
            response = session.get(public_api_url, params=params, headers=headers, timeout=20)
            
            if response.status_code == 200:
                try:
                    data = response.json()
                    if data.get("status") == "ok" or data.get("success"):
                        phone = data.get("number") or data.get("phone")
                        if phone:
                            phone = str(phone).replace("+", "").strip()
                            print(f"✅ Número obtenido: +{phone}")
                            return {"phone": phone, "order_id": data.get("id")}
                except:
                    pass
            
            # 4. Si falla, intentar con el endpoint de países
            country_url = f"https://herosms.com/api/getNumber/US"
            response = session.get(country_url, params={"api_key": HERO_API_KEY}, headers=headers, timeout=20)
            
            if response.status_code == 200:
                try:
                    data = response.json()
                    phone = data.get("number") or data.get("phone")
                    if phone:
                        phone = str(phone).replace("+", "").strip()
                        print(f"✅ Número obtenido: +{phone}")
                        return {"phone": phone}
                except:
                    pass
            
            # 5. Último intento: buscar en el HTML
            phone_match = re.search(r'\b\d{10,12}\b', response.text)
            if phone_match:
                phone = phone_match.group()
                print(f"✅ Número extraído: +{phone}")
                return {"phone": phone}
            
    except Exception as e:
        print(f"⚠️ Error en web scraping: {e}")
    
    return None

# --- FUNCIÓN MEJORADA PARA OBTENER NÚMERO ---
def hero_sms_get_number(country="US", service="amazon"):
    """
    Obtiene número usando múltiples métodos
    """
    
    # MÉTODO 1: Web Scraping
    print("🔄 Método 1: Web Scraping...")
    result = hero_sms_get_number_web()
    if result:
        return result
    
    # MÉTODO 2: Intentar con diferentes formatos de URL
    print("🔄 Método 2: Diferentes formatos de URL...")
    urls = [
        f"https://herosms.com/api/getNumber?api_key={HERO_API_KEY}&country={country}&service={service}",
        f"https://herosms.com/api/v1/getNumber?api_key={HERO_API_KEY}&country={country}&service={service}",
        f"https://herosms.com/api/order?api_key={HERO_API_KEY}&country={country}&service={service}",
        f"https://herosms.com/api/request?api_key={HERO_API_KEY}&country={country}&service={service}",
        f"https://herosms.com/api/getPhone?api_key={HERO_API_KEY}&country={country}&service={service}",
    ]
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json"
    }
    
    for url in urls:
        try:
            print(f"🔄 Probando: {url[:60]}...")
            response = requests.get(url, headers=headers, timeout=15, verify=False)
            
            if response.status_code == 200:
                try:
                    data = response.json()
                    if data.get("status") == "ok" or data.get("success"):
                        phone = data.get("number") or data.get("phone")
                        if phone:
                            phone = str(phone).replace("+", "").strip()
                            print(f"✅ Número obtenido: +{phone}")
                            return {"phone": phone, "order_id": data.get("id")}
                except:
                    # Intentar extraer número del texto
                    phone_match = re.search(r'\b\d{10,12}\b', response.text)
                    if phone_match:
                        phone = phone_match.group()
                        print(f"✅ Número extraído: +{phone}")
                        return {"phone": phone}
                        
        except Exception as e:
            print(f"⚠️ Error: {str(e)[:50]}")
            continue
        
        time.sleep(1)
    
    # MÉTODO 3: Usar 5sim como respaldo
    print("🔄 Método 3: Usando 5sim.net...")
    try:
        url = "https://5sim.net/v1/user/buy/activation/usa/any/amazon"
        response = requests.get(url, timeout=15)
        if response.status_code == 200:
            data = response.json()
            phone = data.get("phone")
            if phone:
                print(f"✅ Número de 5sim: +{phone}")
                return {"phone": phone, "order_id": data.get("id")}
    except:
        pass
    
    return None

# --- GENERAR COOKIE ---
def generate_user_data():
    first_names = ["John", "Jane", "Michael", "Sarah", "David", "Emma", "James", "Maria"]
    last_names = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis"]
    domains = ["gmail.com", "yahoo.com", "outlook.com", "hotmail.com"]
    
    random_string = ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))
    email = f"{random_string}@{random.choice(domains)}"
    password = ''.join(random.choices(string.ascii_letters + string.digits + "!@#$%^&*", k=14))
    
    return {
        "first_name": random.choice(first_names),
        "last_name": random.choice(last_names),
        "email": email,
        "password": password
    }

def generate_cookie(message_id=None):
    print("🔄 Generando cookie...")
    
    if message_id:
        edit_telegram_message(
            message_id,
            "🔄 Iniciando generación...\n📱 Obteniendo número...",
            reply_markup=get_generating_keyboard()
        )
    
    # Obtener número
    sms_data = hero_sms_get_number(country="US", service="amazon")
    
    if not sms_data:
        error_msg = "❌ No se pudo obtener número\n\n"
        error_msg += "💡 Soluciones:\n"
        error_msg += "1. HeroSMS puede estar caído\n"
        error_msg += "2. Verifica tu API Key\n"
        error_msg += "3. Intenta más tarde"
        
        if message_id:
            edit_telegram_message(message_id, error_msg, reply_markup=get_main_keyboard())
        else:
            send_to_telegram(error_msg, reply_markup=get_main_keyboard())
        return
    
    phone = sms_data.get("phone")
    order_id = sms_data.get("order_id")
    
    print(f"📱 Teléfono: +{phone}")
    
    # Generar datos
    user_data = generate_user_data()
    print(f"📧 Email: {user_data['email']}")
    
    # Guardar resultado
    result = {
        "success": True,
        "phone": phone,
        "email": user_data['email'],
        "password": user_data['password'],
        "first_name": user_data['first_name'],
        "last_name": user_data['last_name'],
        "order_id": order_id,
        "created_at": datetime.now().isoformat()
    }
    
    filename = f"amazon_cookie_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(filename, 'w') as f:
        json.dump(result, f, indent=2)
    
    # Enviar resultado
    message = f"""
🎯 <b>¡COOKIE AMAZON GENERADA!</b>

📋 <b>Credenciales:</b>
├─ 📧 Email: <code>{user_data['email']}</code>
├─ 🔑 Password: <code>{user_data['password']}</code>
├─ 📱 Teléfono: <code>+{phone}</code>
├─ 👤 Nombre: {user_data['first_name']} {user_data['last_name']}
└─ 🕒 Generado: {datetime.now().strftime('%H:%M:%S')}

📁 <b>Archivo:</b> {filename}
    """
    
    if message_id:
        edit_telegram_message(message_id, message, reply_markup=get_result_keyboard())
    else:
        send_to_telegram(message, reply_markup=get_result_keyboard())
    
    send_file_to_telegram(filename, f"Cookie Amazon - {user_data['email']}")
    print("✅ Proceso completado!")

# --- PROCESAR CALLBACKS ---
def process_callback(callback_data, message_id):
    print(f"📥 Callback: {callback_data}")
    
    if callback_data == "generate_cookie":
        thread = threading.Thread(target=generate_cookie, args=(message_id,))
        thread.daemon = True
        thread.start()
    elif callback_data == "status":
        send_to_telegram("📊 Sistema activo y funcionando", reply_markup=get_main_keyboard())
    elif callback_data == "list_cookies":
        cookie_files = [f for f in os.listdir() if f.startswith("amazon_cookie_")]
        text = f"📋 Cookies: {len(cookie_files)}\n\n"
        for f in cookie_files[-5:]:
            text += f"• {f}\n"
        send_to_telegram(text, reply_markup=get_main_keyboard())
    elif callback_data == "help":
        help_text = "📖 Ayuda:\n\n1. Presiona 'Generar Cookie'\n2. Espera 1-2 minutos\n3. Recibe tus credenciales"
        send_to_telegram(help_text, reply_markup=get_main_keyboard())
    elif callback_data == "menu":
        send_to_telegram("🏠 Menú Principal", reply_markup=get_main_keyboard())

# --- MAIN ---
def main():
    global LAST_UPDATE_ID
    
    print("="*60)
    print("🚀 GENERADOR AMAZON + HERO SMS v3.0")
    print("="*60)
    print("🤖 Bot activo...")
    
    send_to_telegram(
        "🎯 <b>¡GENERADOR DE COOKIES AMAZON!</b>\n\n"
        "Presiona 'Generar Cookie' para crear una cuenta\n"
        "y recibir tus credenciales.",
        reply_markup=get_main_keyboard()
    )
    
    while True:
        try:
            updates = get_updates(LAST_UPDATE_ID + 1 if LAST_UPDATE_ID else None)
            
            for update in updates:
                update_id = update.get("update_id")
                if update_id:
                    LAST_UPDATE_ID = update_id
                
                callback_query = update.get("callback_query")
                if callback_query:
                    data = callback_query.get("data")
                    message = callback_query.get("message", {})
                    message_id = message.get("message_id")
                    if data and message_id:
                        process_callback(data, message_id)
            
            time.sleep(1)
            
        except KeyboardInterrupt:
            print("\n🛑 Bot detenido")
            break
        except Exception as e:
            print(f"⚠️ Error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
