from curl_cffi.requests import Session
import requests, ssl, json, base64, urllib3
from requests.adapters import HTTPAdapter
from urllib3.util import create_urllib3_context
from cryptography.hazmat.primitives import hashes, padding
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from wafSolver.stormCasterCse import StormCasterCSE

urllib3.disable_warnings()


class LegacySSL(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        ctx = create_urllib3_context()
        ctx.set_ciphers('DEFAULT:@SECLEVEL=1')
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        kwargs['ssl_context'] = ctx
        return super().init_poolmanager(*args, **kwargs)


def cleanphone(phone):
    n = ''.join(c for c in phone if c.isdigit())[-10:]
    return n, f'+52{n}'


def decrypt_data(payload, password='ranDlathEMYraTEFulINmAgi'):
    salt, iv, ct = [base64.b64decode(x) for x in payload.split('|')]
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=250000).derive(password.encode())
    dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    unp = padding.PKCS7(128).unpadder()
    text = (unp.update(dec.update(ct) + dec.finalize()) + unp.finalize()).decode()
    return json.loads(text)


def gate(phone: str):
    num, msisdn = cleanphone(phone)
    curl = Session(impersonate='chrome')
    cse  = StormCasterCSE(verbose=False)

    #Bypass WAF
    cse.solve(curl, 'https://pay.telcel.com/')

    #Erbium -> credential
    r1 = curl.post('https://pay.telcel.com/api/erbium', headers={'accept': 'application/json, text/plain, */*','accept-language': 'es-ES,es;q=0.9','content-type': 'application/json','origin': 'https://pay.telcel.com','priority': 'u=1, i','referer': 'https://pay.telcel.com/consultaMegas/7?rsd=1','sec-ch-ua': '"Google Chrome";v="155", "Chromium";v="155", "Not(A:Brand";v="24"','sec-ch-ua-mobile': '?0','sec-ch-ua-platform': '"Linux"','sec-fetch-dest': 'empty','sec-fetch-mode': 'cors','sec-fetch-site': 'same-origin','user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36','uzlc': cse.get_uzlc({c.name: c.value for c in curl.cookies.jar}),}, json={'msisdn': msisdn})
    if r1.status_code != 200: return {'status': 'error', 'message': f'erbium {r1.status_code}', 'body': r1.text[:300]}
    credential = r1.json()['credential']

    #Actinium -> datos de la linea (cifrado)
    r2 = curl.get('https://pay.telcel.com/api/actinium', headers={'accept': 'application/json, text/plain, */*','accept-language': 'es-ES,es;q=0.9','cp-bi': 'com.claropay.web.portalpagos','priority': 'u=1, i','referer': 'https://pay.telcel.com/consultaMegas/7?rsd=1','sec-ch-ua': '"Google Chrome";v="155", "Chromium";v="155", "Not(A:Brand";v="24"','sec-ch-ua-mobile': '?0','sec-ch-ua-platform': '"Linux"','sec-fetch-dest': 'empty','sec-fetch-mode': 'cors','sec-fetch-site': 'same-origin','user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36','uzlc': cse.get_uzlc({c.name: c.value for c in curl.cookies.jar}),'x-portal-credential': credential,}, params={'a': msisdn})
    if r2.status_code != 200: return {'status': 'error', 'message': f'actinium {r2.status_code}', 'body': r2.text[:300]}
    line   = decrypt_data(r2.json()['data'])
    tinfo  = line.get('telcelInfo', {})
    iframe = tinfo.get('urlIframe')
    perfil = 'AMIGO' if tinfo.get('prepago', True) else 'POSTPAGO'

    #Frame consumos (Mi Telcel)
    mt = requests.Session()
    mt.mount('https://', LegacySSL())
    mt.verify = False
    if iframe: mt.get(iframe, timeout=15)

    r3 = mt.post('https://uat.mitelcel.com/mitelcel/mitelcel-api-web/api/prepago/internet/frame-consumos', headers={'Accept': 'application/json, text/javascript, */*; q=0.01','Accept-Language': 'es-ES,es;q=0.9','Connection': 'keep-alive','Content-Type': 'application/json','Origin': 'https://uat.mitelcel.com','Referer': iframe or 'https://uat.mitelcel.com/mitelcel/consumo-datos/claro?translate=false','Sec-Fetch-Dest': 'empty','Sec-Fetch-Mode': 'cors','Sec-Fetch-Site': 'same-origin','User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36','X-Requested-With': 'XMLHttpRequest','sec-ch-ua': '"Google Chrome";v="155", "Chromium";v="155", "Not(A:Brand";v="24"','sec-ch-ua-mobile': '?0','sec-ch-ua-platform': '"Linux"',}, json={'telefono': tinfo.get('msisdn', num), 'region': '4', 'perfil': perfil}, timeout=20)
    try:
        body = r3.json()
    except Exception:
        return {'status': 'error', 'message': f'frame-consumos {r3.status_code}', 'body': r3.text[:300]}
    if isinstance(body.get('data'), str) and '|' in body['data']:
        body = decrypt_data(body['data'])

    return {'status': 'ok', 'phone': num, 'perfil': perfil, 'consumos': body}


if __name__ == '__main__':
    run = gate(input('telefono: '))
    print(json.dumps(run, indent=4, ensure_ascii=False))
