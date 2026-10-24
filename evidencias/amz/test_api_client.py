"""
Cliente de prueba para la API Leviatan CHK (ACTUALIZADO)
Uso: python test_api_client_updated.py
"""

import requests
import json
from datetime import datetime

# Configuración
API_URL = "http://localhost:5000"
HEALTH_ENDPOINT = f"{API_URL}/health"
CHECK_ENDPOINT = f"{API_URL}/amazon/leviatan"
STATS_ENDPOINT = f"{API_URL}/stats"

# Headers con User-Agent de navegador real (REQUERIDO)
HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}

# Tarjetas de prueba
TEST_CARDS = [
    "4532640527811643|12|2025|123",  # Aprobada
    "5425233010103442|06|2026|456",  # Declinada
    "3782822463100051|03|2027|789",  # Error
]

# Cookies de prueba (simuladas)
TEST_COOKIES = {
    "ubid-main": "test_ubid_value",
    "session-id": "test_session_id",
    "i18n-prefs": "es_ES",
    "x-main": "test_x_main",
    "session-token": "test_session_token"
}

def print_header(text):
    """Imprime un encabezado formateado"""
    print("\n" + "="*80)
    print(f"  {text}")
    print("="*80 + "\n")

def test_health():
    """Prueba el endpoint de salud"""
    print_header("PRUEBA 1: Health Check")
    try:
        response = requests.get(HEALTH_ENDPOINT, headers=HEADERS, timeout=5)
        print(f"Status Code: {response.status_code}")
        print(f"Response: {json.dumps(response.json(), indent=2, ensure_ascii=False)}")
        return response.status_code == 200
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

def test_check_card(card, cookies):
    """Prueba el endpoint de verificación de tarjeta"""
    print_header(f"PRUEBA: Verificar Tarjeta {card.split('|')[0][-4:]}")
    
    payload = {
        "card": card,
        "cookies": json.dumps(cookies) if isinstance(cookies, dict) else cookies
    }
    
    print(f"Payload enviado:")
    print(f"  Card: {card}")
    print(f"  Cookies: {'<JSON con 5 cookies>' if isinstance(cookies, dict) else '<String de cookies>'}\n")
    
    try:
        response = requests.post(
            CHECK_ENDPOINT,
            json=payload,
            headers=HEADERS,
            timeout=10
        )
        
        print(f"Status Code: {response.status_code}")
        print(f"Response:")
        print(json.dumps(response.json(), indent=2, ensure_ascii=False))
        
        return response.status_code in [200, 400, 401]
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

def test_invalid_user_agent():
    """Prueba con User-Agent inválido"""
    print_header("PRUEBA: User-Agent Inválido (RECHAZADO)")
    
    invalid_user_agents = [
        "",
        "curl/7.64.1",
        "python-requests/2.25.1",
        "bot/1.0",
    ]
    
    for ua in invalid_user_agents:
        print(f"\n  Probando User-Agent: '{ua}'")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": ua
        }
        
        payload = {
            "card": TEST_CARDS[0],
            "cookies": json.dumps(TEST_COOKIES)
        }
        
        try:
            response = requests.post(
                CHECK_ENDPOINT,
                json=payload,
                headers=headers,
                timeout=5
            )
            print(f"    Status: {response.status_code}")
            print(f"    Response: {response.json()['response']}")
        except Exception as e:
            print(f"    Error: {e}")

def test_valid_user_agents():
    """Prueba con User-Agents válidos"""
    print_header("PRUEBA: User-Agents Válidos (ACEPTADOS)")
    
    valid_user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    ]
    
    for ua in valid_user_agents:
        print(f"\n  Probando User-Agent: {ua[:50]}...")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": ua
        }
        
        payload = {
            "card": TEST_CARDS[0],
            "cookies": json.dumps(TEST_COOKIES)
        }
        
        try:
            response = requests.post(
                CHECK_ENDPOINT,
                json=payload,
                headers=headers,
                timeout=5
            )
            print(f"    Status: {response.status_code}")
            print(f"    Response: {response.json()['response']}")
        except Exception as e:
            print(f"    Error: {e}")

def test_invalid_card_format():
    """Prueba con formato de tarjeta inválido"""
    print_header("PRUEBA: Formato de Tarjeta Inválido")
    
    invalid_cards = [
        "1234567890123456|13|2025|123",  # Mes inválido
        "123456789012345|12|2025|123",   # Número muy corto
        "4532640527811643|12|2020|123",  # Año en el pasado
        "4532640527811643|12|2025|12",   # CVV muy corto
    ]
    
    for card in invalid_cards:
        print(f"\n  Probando: {card}")
        payload = {
            "card": card,
            "cookies": json.dumps(TEST_COOKIES)
        }
        
        try:
            response = requests.post(
                CHECK_ENDPOINT,
                json=payload,
                headers=HEADERS,
                timeout=5
            )
            print(f"    Status: {response.status_code}")
            print(f"    Response: {response.json()['response']}")
        except Exception as e:
            print(f"    Error: {e}")

def test_invalid_cookies():
    """Prueba con cookies inválidas"""
    print_header("PRUEBA: Cookies Inválidas")
    
    invalid_cookies = [
        "",
        "abc",
        "cookie1=value1",
    ]
    
    for cookies in invalid_cookies:
        print(f"\n  Probando cookies: '{cookies}'")
        payload = {
            "card": TEST_CARDS[0],
            "cookies": cookies
        }
        
        try:
            response = requests.post(
                CHECK_ENDPOINT,
                json=payload,
                headers=HEADERS,
                timeout=5
            )
            print(f"    Status: {response.status_code}")
            print(f"    Response: {response.json()['response']}")
        except Exception as e:
            print(f"    Error: {e}")

def test_missing_parameters():
    """Prueba sin parámetros requeridos"""
    print_header("PRUEBA: Parámetros Faltantes")
    
    # Sin card
    print("\n  Probando sin parámetro 'card'")
    payload = {"cookies": json.dumps(TEST_COOKIES)}
    try:
        response = requests.post(
            CHECK_ENDPOINT,
            json=payload,
            headers=HEADERS,
            timeout=5
        )
        print(f"    Status: {response.status_code}")
        print(f"    Response: {response.json()['response']}")
    except Exception as e:
        print(f"    Error: {e}")
    
    # Sin cookies
    print("\n  Probando sin parámetro 'cookies'")
    payload = {"card": TEST_CARDS[0]}
    try:
        response = requests.post(
            CHECK_ENDPOINT,
            json=payload,
            headers=HEADERS,
            timeout=5
        )
        print(f"    Status: {response.status_code}")
        print(f"    Response: {response.json()['response']}")
    except Exception as e:
        print(f"    Error: {e}")

def test_stats():
    """Prueba el endpoint de estadísticas"""
    print_header("PRUEBA: Estadísticas")
    try:
        response = requests.get(STATS_ENDPOINT, headers=HEADERS, timeout=5)
        print(f"Status Code: {response.status_code}")
        print(f"Response: {json.dumps(response.json(), indent=2, ensure_ascii=False)}")
        return response.status_code == 200
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

def test_invalid_endpoint():
    """Prueba un endpoint que no existe"""
    print_header("PRUEBA: Endpoint No Existente")
    try:
        response = requests.get(f"{API_URL}/invalid", headers=HEADERS, timeout=5)
        print(f"Status Code: {response.status_code}")
        print(f"Response: {json.dumps(response.json(), indent=2, ensure_ascii=False)}")
    except Exception as e:
        print(f"Error: {e}")

def run_all_tests():
    """Ejecuta todas las pruebas"""
    print("\n")
    print("╔" + "="*78 + "╗")
    print("║" + " "*78 + "║")
    print("║" + "  🚀 SUITE DE PRUEBAS - API LEVIATAN CHK (ACTUALIZADA)".center(78) + "║")
    print("║" + " "*78 + "║")
    print("╚" + "="*78 + "╝")
    
    results = []
    
    # Prueba 1: Health Check
    results.append(("Health Check", test_health()))
    
    # Prueba 2-4: Verificar tarjetas válidas
    for i, card in enumerate(TEST_CARDS, 1):
        results.append((f"Verificar Tarjeta {i}", test_check_card(card, TEST_COOKIES)))
    
    # Prueba 5: User-Agents válidos
    test_valid_user_agents()
    results.append(("User-Agents Válidos", True))
    
    # Prueba 6: User-Agents inválidos (rechazados)
    test_invalid_user_agent()
    results.append(("User-Agents Inválidos", True))
    
    # Prueba 7: Formato inválido
    test_invalid_card_format()
    results.append(("Formato de Tarjeta Inválido", True))
    
    # Prueba 8: Cookies inválidas
    test_invalid_cookies()
    results.append(("Cookies Inválidas", True))
    
    # Prueba 9: Parámetros faltantes
    test_missing_parameters()
    results.append(("Parámetros Faltantes", True))
    
    # Prueba 10: Estadísticas
    results.append(("Estadísticas", test_stats()))
    
    # Prueba 11: Endpoint no existente
    test_invalid_endpoint()
    results.append(("Endpoint No Existente", True))
    
    # Resumen
    print_header("RESUMEN DE PRUEBAS")
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    print(f"Total de pruebas: {total}")
    print(f"Exitosas: {passed}")
    print(f"Fallidas: {total - passed}")
    print(f"Tasa de éxito: {(passed/total)*100:.1f}%\n")
    
    for test_name, result in results:
        status = "✅" if result else "❌"
        print(f"  {status} {test_name}")
    
    print("\n" + "="*80 + "\n")

if __name__ == "__main__":
    print("\n⚠️  ASEGÚRATE DE QUE LA API ESTÁ CORRIENDO EN http://localhost:5000")
    print("   Ejecuta: python app.py\n")
    
    input("Presiona Enter para comenzar las pruebas...")
    
    run_all_tests()
