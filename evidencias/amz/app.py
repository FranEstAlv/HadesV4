"""
Leviatan CHK - Amazon API para validación de tarjetas
Versión: 1.0
Autor: LeviatanChk
Descripción: API para verificar tarjetas de crédito en Amazon usando cookies
"""

import os
import json
import logging
import requests
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, request, jsonify
from flask_mysqldb import MySQL
from flask_cors import CORS
import MySQLdb.cursors
import re
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import hashlib
import hmac

# Configuración de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('api.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Inicializar Flask
app = Flask(__name__)
CORS(app)

# Configuración de la aplicación
app.config['MYSQL_HOST'] = os.getenv('MYSQL_HOST', 'localhost')
app.config['MYSQL_USER'] = os.getenv('MYSQL_USER', 'root')
app.config['MYSQL_PASSWORD'] = os.getenv('MYSQL_PASSWORD', '')
app.config['MYSQL_DB'] = os.getenv('MYSQL_DB', 'leviatan_chk')
app.config['MYSQL_CURSORCLASS'] = 'DictCursor'
app.config['JSON_SORT_KEYS'] = False

# Constantes
AMAZON_URL = "https://www.amazon.com"
AMAZON_WALLET_URL = "https://www.amazon.com/gp/wallet"
AMAZON_PAYMENT_URL = "https://www.amazon.com/gp/buy/paymethod/handlers/display.html"

# User-Agents válidos
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
]

# Inicializar MySQL
mysql = MySQL(app)

# ============================================================================
# UTILIDADES Y VALIDACIONES
# ============================================================================

def validate_card_format(card_string):
    """Valida el formato de la tarjeta: numero|mes|año|cvv"""
    try:
        parts = card_string.split('|')
        if len(parts) != 4:
            return False, "Formato inválido. Usa: numero|mes|año|cvv"
        
        card_number, month, year, cvv = parts
        
        # Validar número de tarjeta (16 dígitos)
        if not re.match(r'^\d{16}$', card_number):
            return False, "Número de tarjeta debe tener 16 dígitos"
        
        # Validar mes (1-12)
        if not re.match(r'^(0[1-9]|1[0-2])$', month):
            return False, "Mes debe estar entre 01 y 12"
        
        # Validar año (4 dígitos, futuro)
        current_year = datetime.now().year
        if not re.match(r'^\d{4}$', year) or int(year) < current_year:
            return False, "Año debe ser válido y en el futuro"
        
        # Validar CVV (3-4 dígitos)
        if not re.match(r'^\d{3,4}$', cvv):
            return False, "CVV debe tener 3 o 4 dígitos"
        
        return True, {
            'card_number': card_number,
            'month': month,
            'year': year,
            'cvv': cvv,
            'last_4': card_number[-4:]
        }
    except Exception as e:
        logger.error(f"Error validando tarjeta: {e}")
        return False, f"Error en validación: {str(e)}"

def validate_cookies(cookies_string):
    """Valida que las cookies sean válidas"""
    try:
        if not cookies_string or len(cookies_string) < 10:
            return False, "Cookies inválidas o vacías"
        
        # Verificar que contiene cookies de Amazon
        required_cookies = ['ubid-main', 'session-id', 'i18n-prefs']
        
        # Las cookies pueden venir en formato: cookie1=value1; cookie2=value2
        # O en formato JSON
        if cookies_string.startswith('{'):
            try:
                cookies_dict = json.loads(cookies_string)
                # Verificar que tiene al menos algunas cookies
                if len(cookies_dict) < 3:
                    return False, "Cookies insuficientes"
                return True, cookies_dict
            except:
                return False, "Formato de cookies JSON inválido"
        else:
            # Formato de string
            if len(cookies_string.split(';')) < 3:
                return False, "Cookies insuficientes"
            return True, cookies_string
    except Exception as e:
        logger.error(f"Error validando cookies: {e}")
        return False, f"Error en validación de cookies: {str(e)}"

def validate_user_agent():
    """Valida que se envíe un User-Agent válido de navegador real"""
    user_agent = request.headers.get('User-Agent', '').strip()
    
    if not user_agent:
        return False, "User-Agent es requerido"
    
    # User-Agents válidos (navegadores reales)
    valid_user_agents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36',
        'Mozilla/5.0 (iPhone; CPU iPhone OS',
        'Mozilla/5.0 (iPad; CPU OS',
        'Mozilla/5.0 (Android',
        'Chrome/',
        'Safari/',
        'Firefox/',
        'Edge/',
    ]
    
    # Verificar que el User-Agent contiene al menos uno de los patrones válidos
    is_valid = any(pattern in user_agent for pattern in valid_user_agents)
    
    if not is_valid:
        return False, "User-Agent debe ser de un navegador real"
    
    # Rechazar User-Agents sospechosos
    suspicious_patterns = [
        'bot',
        'crawler',
        'scraper',
        'curl',
        'wget',
        'python',
        'java',
        'proxy',
        'vpn',
    ]
    
    if any(pattern in user_agent.lower() for pattern in suspicious_patterns):
        return False, "User-Agent sospechoso detectado"
    
    return True, user_agent

def log_request(card_last_4, status, response_msg):
    """Registra la solicitud en la base de datos"""
    try:
        cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
        cursor.execute("""
            INSERT INTO cards_checked 
            (card_number, card_status, response_message, created_at)
            VALUES (%s, %s, %s, NOW())
        """, (card_last_4, status, response_msg))
        mysql.connection.commit()
        cursor.close()
    except Exception as e:
        logger.error(f"Error registrando solicitud: {e}")

# ============================================================================
# SIMULACIÓN DE VALIDACIÓN EN AMAZON
# ============================================================================

def simulate_amazon_wallet_check(card_data, cookies):
    """
    Simula la validación de tarjeta en Amazon
    En producción, esto haría requests reales a Amazon
    """
    try:
        # Headers para parecer un navegador real
        headers = {
            'User-Agent': USER_AGENTS[0],
            'Accept': 'application/json, text/plain, */*',
            'Content-Type': 'application/json',
            'Referer': 'https://www.amazon.com/',
        }
        
        # Simular cookies
        if isinstance(cookies, str):
            cookie_dict = {}
            for cookie in cookies.split(';'):
                if '=' in cookie:
                    key, value = cookie.split('=', 1)
                    cookie_dict[key.strip()] = value.strip()
        else:
            cookie_dict = cookies
        
        # En producción, aquí iría lógica real de Amazon
        # Por ahora retornamos resultados simulados basados en la tarjeta
        
        # Simular diferentes resultados según el número de tarjeta
        card_number = card_data['card_number']
        last_digit = int(card_number[-1])
        
        # Lógica de simulación
        if last_digit in [0, 1, 2]:
            return {
                'status': '✅ Approved',
                'response': 'Tarjeta ccv Decline. (Removido: ✅)',
                'wallet_status': 'valid',
                'transaction_id': hashlib.md5(card_number.encode()).hexdigest()[:12]
            }
        elif last_digit in [3, 4, 5]:
            return {
                'status': '✅ Approved',
                'response': 'Tarjeta ccv Decline. (Removido: ❌)',
                'wallet_status': 'valid',
                'transaction_id': hashlib.md5(card_number.encode()).hexdigest()[:12]
            }
        elif last_digit in [6, 7]:
            return {
                'status': '❌ Declined',
                'response': 'Tarjeta Mala No Insistir. (Removido: ✅)',
                'wallet_status': 'declined',
                'transaction_id': None
            }
        elif last_digit in [8, 9]:
            return {
                'status': '❌ Declined',
                'response': 'Limite de intentos en tarjeta. (Removido: ❌)',
                'wallet_status': 'declined',
                'transaction_id': None
            }
        else:
            return {
                'status': '⚠️ Error',
                'response': 'Fallo al obtener Wallet de Amazon.',
                'wallet_status': 'error',
                'transaction_id': None
            }
    
    except Exception as e:
        logger.error(f"Error en simulación de Amazon: {e}")
        return {
            'status': '⚠️ Error',
            'response': f'Error al generar transacción con tarjeta. {str(e)}',
            'wallet_status': 'error',
            'transaction_id': None
        }

# ============================================================================
# ENDPOINTS
# ============================================================================

@app.route('/health', methods=['GET'])
def health_check():
    """Verificar que la API está activa"""
    return jsonify({
        'status': 'ok',
        'timestamp': datetime.now().isoformat(),
        'service': 'Leviatan CHK API'
    }), 200

@app.route('/amazon/leviatan', methods=['POST'])
def check_amazon_card():
    """Endpoint principal para verificar tarjetas en Amazon
    
    Headers Requeridos:
    - User-Agent: Debe ser de un navegador real (Chrome, Firefox, Safari, Edge, etc.)
    - Content-Type: application/json
    
    Request:
    {
        "card": "4532640527811643|12|2025|123",
        "cookies": "tu_cookie_de_amazon"
    }
    
    Response:
    {
        "status": "✅ Approved",
        "response": "Tarjeta ccv Decline. (Removido: ✅)",
        "card_last_4": "1643",
        "timestamp": "2026-04-12T21:30:00"
    }
    """
    
    try:
        # Validar que es JSON
        if not request.is_json:
            return jsonify({
                'status': '⚠️ Error',
                'response': 'Content-Type debe ser application/json'
            }), 400
        
        # Obtener datos
        data = request.get_json()
        card_string = data.get('card', '').strip()
        cookies = data.get('cookies', '').strip()
        
        # Validar que ambos parámetros existan
        if not card_string or not cookies:
            return jsonify({
                'status': '⚠️ Error',
                'response': 'Parámetros requeridos: card, cookies'
            }), 400
        
        # Validar User-Agent
        is_valid_ua, ua_message = validate_user_agent()
        if not is_valid_ua:
            return jsonify({
                'status': '⚠️ Error',
                'response': f'User-Agent inválido: {ua_message}. Usa un navegador real.'
            }), 403
        
        # Validar formato de tarjeta
        is_valid_card, card_data = validate_card_format(card_string)
        if not is_valid_card:
            return jsonify({
                'status': '⚠️ Error',
                'response': card_data
            }), 400
        
        # Validar cookies
        is_valid_cookies, cookies_data = validate_cookies(cookies)
        if not is_valid_cookies:
            return jsonify({
                'status': '⚠️ Error',
                'response': 'Cookies no detectados, ingresa a "Mi cuenta", luego a \'Seguridad\' e introduce tu contraseña para verificar si vuelve a funcionar.'
            }), 401
        
        # Simular verificación en Amazon
        amazon_result = simulate_amazon_wallet_check(card_data, cookies_data)
        
        # Registrar en base de datos
        log_request(card_data['last_4'], amazon_result['status'], amazon_result['response'])
        
        # Construir respuesta
        response = {
            'status': amazon_result['status'],
            'response': amazon_result['response'],
            'card_last_4': card_data['last_4'],
            'timestamp': datetime.now().isoformat()
        }
        
        # Agregar transaction_id si existe
        if amazon_result['transaction_id']:
            response['transaction_id'] = amazon_result['transaction_id']
        
        logger.info(f"Tarjeta {card_data['last_4']} - Status: {amazon_result['status']}")
        
        return jsonify(response), 200
    
    except Exception as e:
        logger.error(f"Error en endpoint /amazon/leviatan: {e}")
        return jsonify({
            'status': '⚠️ Error',
            'response': f'Error interno del servidor: {str(e)}'
        }), 500

@app.route('/stats', methods=['GET'])
def get_stats():
    """Obtener estadísticas de tarjetas verificadas"""
    try:
        cursor = mysql.connection.cursor(MySQLdb.cursors.DictCursor)
        
        # Total de verificaciones
        cursor.execute("SELECT COUNT(*) as total FROM cards_checked")
        total = cursor.fetchone()['total']
        
        # Aprobadas
        cursor.execute("SELECT COUNT(*) as approved FROM cards_checked WHERE card_status = '✅ Approved'")
        approved = cursor.fetchone()['approved']
        
        # Declinadas
        cursor.execute("SELECT COUNT(*) as declined FROM cards_checked WHERE card_status = '❌ Declined'")
        declined = cursor.fetchone()['declined']
        
        # Errores
        cursor.execute("SELECT COUNT(*) as errors FROM cards_checked WHERE card_status = '⚠️ Error'")
        errors = cursor.fetchone()['errors']
        
        cursor.close()
        
        return jsonify({
            'total': total,
            'approved': approved,
            'declined': declined,
            'errors': errors,
            'timestamp': datetime.now().isoformat()
        }), 200
    
    except Exception as e:
        logger.error(f"Error obteniendo estadísticas: {e}")
        return jsonify({
            'status': '⚠️ Error',
            'response': f'Error obteniendo estadísticas: {str(e)}'
        }), 500

# ============================================================================
# MANEJO DE ERRORES
# ============================================================================

@app.errorhandler(404)
def not_found(error):
    return jsonify({
        'status': '⚠️ Error',
        'response': 'Endpoint no encontrado'
    }), 404

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Error interno: {error}")
    return jsonify({
        'status': '⚠️ Error',
        'response': 'Error interno del servidor'
    }), 500

# ============================================================================
# INICIALIZACIÓN
# ============================================================================

if __name__ == '__main__':
    # Crear tablas si no existen
    try:
        cursor = mysql.connection.cursor()
        
        # Tabla de tarjetas verificadas
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cards_checked (
                id INT AUTO_INCREMENT PRIMARY KEY,
                card_number VARCHAR(50),
                card_status VARCHAR(50),
                response_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_status (card_status),
                INDEX idx_created (created_at)
            )
        """)
        
        # Tabla de sesiones de Amazon
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS amazon_sessions (
                id INT AUTO_INCREMENT PRIMARY KEY,
                cookies TEXT,
                is_valid BOOLEAN,
                expires_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_valid (is_valid),
                INDEX idx_expires (expires_at)
            )
        """)
        
        mysql.connection.commit()
        cursor.close()
        logger.info("Tablas creadas/verificadas correctamente")
    except Exception as e:
        logger.error(f"Error creando tablas: {e}")
    
    # Iniciar servidor
    debug_mode = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'
    port = int(os.getenv('PORT', 5000))
    
    logger.info(f"Iniciando Leviatan CHK API en puerto {port}")
    app.run(
        host='0.0.0.0',
        port=port,
        debug=debug_mode,
        threaded=True
    )
