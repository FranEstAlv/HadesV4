"""Generador de cuentas Amazon: registro completo sin navegador.

Orden de las etapas:
  1. Se pide un correo temporal (o IMAP) al servicio TempMail.
  2. Se resuelve el reto WAF de Amazon (AWS WAF) usando la proxy configurada.
  3. Se registra el correo en Amazon y se espera el codigo de verificacion.
  4. Si Amazon pide un reto Arc/Arkose se resuelve con el solver configurado
     (CapSolver, 2Captcha o Anti-Captcha).
  5. Si Amazon pide un numero de celular se compra uno virtual (HeroSMS), se
     espera el SMS con el codigo y se responde.
  6. Se agrega la direccion de facturacion y se exportan las cookies.

El avance del flujo se reporta por el callback ``Creator.on_status``: asi el
CLI puede mostrarlo en pantalla sin depender de prints. Los detalles van al
log (modulo ``logging``), no a la consola.
"""
import logging
import os ,re ,random ,secrets ,time ,types
from urllib .parse import urljoin ,unquote ,quote ,quote_plus
from concurrent .futures import ThreadPoolExecutor ,wait
from bs4 import BeautifulSoup
from faker import Faker

from credsBypass import TempMail ,encryptPassw
from curl_session import ImpersonateSession
from cvfSolver import solveCvf
from metaDataGen import FwcimClient
from smsProvider import HeroSms ,SmsError
from suspension import detect_suspension ,suspension_reason
from wafSolver import AwsWaf

logger = logging .getLogger (__name__)



browsers =[
{'impersonate':'chrome150','ua':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36','maj':'150','full':'150.0.0.0','chUa':'"Chromium";v="150", "Google Chrome";v="150", "Not-A.Brand";v="99"'},
{'impersonate':'chrome146','ua':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36','maj':'146','full':'146.0.0.0','chUa':'"Chromium";v="146", "Google Chrome";v="146", "Not-A.Brand";v="99"'},
{'impersonate':'chrome142','ua':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36','maj':'142','full':'142.0.0.0','chUa':'"Chromium";v="142", "Google Chrome";v="142", "Not-A.Brand";v="99"'},
{'impersonate':'chrome136','ua':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36','maj':'136','full':'136.0.0.0','chUa':'"Chromium";v="136", "Google Chrome";v="136", "Not-A.Brand";v="99"'},
{'impersonate':'chrome131','ua':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36','maj':'131','full':'131.0.0.0','chUa':'"Chromium";v="131", "Google Chrome";v="131", "Not-A.Brand";v="99"'},
{'impersonate':'chrome150','ua':'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36','maj':'150','full':'150.0.0.0','chUa':'"Chromium";v="150", "Google Chrome";v="150", "Not-A.Brand";v="99"'},
{'impersonate':'safari180','ua':'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15','maj':'18','full':'18.0','chUa':''},
{'impersonate':'safari184','ua':'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.4 Safari/605.1.15','maj':'18','full':'18.4','chUa':''},
{'impersonate':'firefox135','ua':'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:135.0) Gecko/20100101 Firefox/135.0','maj':'135','full':'135.0.0.0','chUa':''},
{'impersonate':'firefox144','ua':'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:144.0) Gecko/20100101 Firefox/144.0','maj':'144','full':'144.0.0.0','chUa':''},
]

countries ={
'US':{'domain':'amazon.com','handle':'usflex','code':'US','locale':'en_US','lang':'en-US,en;q=0.9','tz':-6 ,'faker':'en_US','mails':('gmail.com','outlook.com','hotmail.com')},
'CA':{'domain':'amazon.ca','handle':'caflex','code':'CA','locale':'en_CA','lang':'en-CA,en;q=0.9,fr-CA;q=0.8','tz':-5 ,'faker':'en_CA','mails':('gmail.com','outlook.com','hotmail.com')},
'MX':{'domain':'amazon.com.mx','handle':'mxflex','code':'MX','locale':'es_MX','lang':'es-MX,es;q=0.9,en;q=0.8','tz':-6 ,'faker':'es_MX','mails':('gmail.com','outlook.com','hotmail.com')},
'BR':{'domain':'amazon.com.br','handle':'brflex','code':'BR','locale':'pt_BR','lang':'pt-BR,pt;q=0.9,en;q=0.8','tz':-3 ,'faker':'pt_BR','mails':('gmail.com','outlook.com','hotmail.com')},
'UK':{'domain':'amazon.co.uk','handle':'gbflex','code':'GB','locale':'en_GB','lang':'en-GB,en;q=0.9','tz':0 ,'faker':'en_GB','mails':('gmail.com','outlook.com','hotmail.com')},
'DE':{'domain':'amazon.de','handle':'deflex','code':'DE','locale':'de_DE','lang':'de-DE,de;q=0.9,en;q=0.8','tz':1 ,'faker':'de_DE','mails':('gmail.com','outlook.com','hotmail.com')},
'FR':{'domain':'amazon.fr','handle':'frflex','code':'FR','locale':'fr_FR','lang':'fr-FR,fr;q=0.9,en;q=0.8','tz':1 ,'faker':'fr_FR','mails':('gmail.com','outlook.com','hotmail.com')},
'IT':{'domain':'amazon.it','handle':'itflex','code':'IT','locale':'it_IT','lang':'it-IT,it;q=0.9,en;q=0.8','tz':1 ,'faker':'it_IT','mails':('gmail.com','outlook.com','hotmail.com')},
'ES':{'domain':'amazon.es','handle':'esflex','code':'ES','locale':'es_ES','lang':'es-ES,es;q=0.9,en;q=0.8','tz':1 ,'faker':'es_ES','mails':('gmail.com','outlook.com','hotmail.com')},
'NL':{'domain':'amazon.nl','handle':'nlflex','code':'NL','locale':'nl_NL','lang':'nl-NL,nl;q=0.9,en;q=0.8','tz':1 ,'faker':'nl_NL','mails':('gmail.com','outlook.com','hotmail.com')},
'SG':{'domain':'amazon.sg','handle':'sgflex','code':'SG','locale':'en_SG','lang':'en-SG,en;q=0.9','tz':8 ,'faker':'en_US','mails':('gmail.com','outlook.com','hotmail.com')},
'AU':{'domain':'amazon.com.au','handle':'auflex','code':'AU','locale':'en_AU','lang':'en-AU,en;q=0.9','tz':10 ,'faker':'en_AU','mails':('gmail.com','outlook.com','hotmail.com')},
'JP':{'domain':'amazon.co.jp','handle':'jpflex','code':'JP','locale':'ja_JP','lang':'ja-JP,ja;q=0.9,en;q=0.8','tz':9 ,'faker':'en_US','mails':('gmail.com','outlook.com','hotmail.com')},
}

cse ={
'NA':{'keyId':'973900addb061fbe5bb4ea871e9d8161','jwkN':'rwLCVK_8hcUgil9KQiN7RbtmcJV5Pt12CwbhZ1h9fvdbVRILCanjv2RNSW9l-Mq0fnRq6DLTLzX3J3TuVCZQ1wjfa-Ef1BDeXnVNaY4q0Vvl2e1e9UF-uwyK5mDyiftlPt5JcsRuFXU1dMSb5TwDiFV1UlGOc-db33zi1MlmrL5L7iyfqBQmlEoa5el5pFbmeK2wSOKBZtJja-dbVzde0jrpGlVhHDZOAlH7g8aTftqwHLVP27T9Pr0UJtaj9LIX-sg_K9-Pl7H2W9BJDTJLJi_EAAqBHTrRueejO3XbEuSGrsrphCk0ZlYqoLkobey-kubWTba5kzsWL-huF--tzQ'},
'EU':{'keyId':'8c3749c7577cfbe8de80ec2d8e03f35c','jwkN':'7MBo_ZCPa0E3BnEXiLK0zhb9fZwQVrkkCPEBuf9HVq_-uEnER6cZBu7BABkLvvvFQnPYaxvjyQ3vAhJsdQUkHVb6spDOJsDLq3xQnzPr5T41PxXgMVPlrEWtjT2eB3ENU5gf-gtwCAm6JZMzzMr4k41aehnfikRtEdAKewUZm0KbrS0gcWCKnBxrkAWiOHUEZaL0IWH45sU_ul6y9Ej2w8xl1Nm8KOvt3FV_uF8OXj2icLLHMlUTlYDbC3xSLNahTXoh0Dao5ihk6kf_Wxv_d2h_ftx0MWuPVmVMASYCY9YGErKQ182exEaWta0I_Eva-omXyYxoKxneN9LdZYw-NQ'},
'FE':{'keyId':'e7696039e9f2aed1c5c0d34eb95a3cd4','jwkN':'oez6pTbcxFW1_fdZyYWlQonAop33Yv9BK4b_f21ttSmSe7TmjPN2mqXsUpFoTpwcVJ3akIu3cExnkjV_juIoj0u7V8CvrkCZjRVWYSwuBdGmNKx3p8fsmHBkqvMXjcSrhFZAWI7_GFLo66DATzpJu5TVWbzkGS95nNL-YCsr8OUxy08o7Wp7oiujLGGdI7RtcYXXe1SchC5cMj3g8nfhDGuFui6hgEoDzg_fgypshbKsybaesCLyxNFqQbiH24_T8nSuOYVGui23Td8sKFJoTqEq14JYp-GDAn88IMUErUS4NdxL4FJPPEXV6SMsH5P0MDOrGu8TYpsgV0YRTT0ZBw'},
}
cseRegion ={'US':'NA','CA':'NA','MX':'NA','BR':'NA','GB':'EU','DE':'EU','FR':'EU','IT':'EU','ES':'EU','NL':'EU','SG':'FE','AU':'FE','JP':'FE'}

supported =list (countries .keys ())

def needsWaf (c ):
    """True si el pais usa el reto WAF de Amazon antes del registro."""
    return c .upper ()in {'US','CA','MX','JP'}

def getCse (code ):
    """Llaves publicas de cifrado de contrasena (CSE) del pais."""
    return cse [cseRegion .get ((code or '').upper (),'NA')]




def fakeProfile (country ='US'):
    """Nombre, contrasena y correo de la identidad falsa del registro."""
    cfg =countries .get (country .upper (),countries ['US'])
    try :fk =Faker (cfg ['faker'])
    except Exception :fk =Faker ('en_US')
    fn =fk .first_name ();ln =fk .last_name ()
    af =''.join (c for c in fn if c .isascii ()and c .isalnum ())or 'user'
    al =''.join (c for c in ln if c .isascii ()and c .isalnum ())or 'mail'
    return types .SimpleNamespace (
    fname =fn ,lname =ln ,
    name =f'{fn } {ln }',
    password =f"{random .choice (('Passwd','Densws','Assws'))}{random .randint (1000 ,9999 )}",
    mail =f"{af }{random .choice ('._-')}{al }{random .randint (0 ,999 )}@{random .choice (cfg ['mails'])}",
    )

def buildSess (baseUrl ,domain ,proxy ,country ='US',profile =None ,resetFp =True ):
    """Abre la sesion con la huella del navegador (UA, headers, proxy)."""
    cfg =countries .get (country .upper (),countries ['US'])
    if resetFp :
        FwcimClient .reset_session ()
    FwcimClient .set_session_timezone (cfg ['tz'])
    if profile is None :profile =random .choice (browsers )
    sess =ImpersonateSession (impersonate =profile ['impersonate'])
    ua =profile ['ua']
    maj =profile ['maj']
    full =profile ['full']
    chUa =profile ['chUa']
    chUaFull =(chUa .replace (f'";v="{maj }"',f'";v="{full }"')
    .replace ('";v="24"','";v="24.0.0.0"')
    .replace ('";v="99"','";v="99.0.0.0"'))
    fp =FwcimClient ._session_profile
    if not fp :
        FwcimClient (location =baseUrl ,userAgent =ua ,timezone =cfg ['tz'])
        fp =FwcimClient ._session_profile
    screen =fp ['screen'];vw =screen ['width']
    dpr =fp ['dpr'];mem =str (min (8 ,fp ['deviceMemory']))
    if proxy :
        pn =normalizeProxy (proxy )
        if not pn :
            raise RuntimeError ('proxy_invalido')
        sess .proxies ={'http':pn ,'https':pn }
    elif os .getenv ('PROXY_REQUIRED','0').strip ().lower ()in ('1','true','yes','si'):
        # Evita que una configuracion incompleta termine saliendo directo.
        raise RuntimeError ('proxy_required: configura PROXY_STICKY_MX / PROXY_ROTATORY en .env')
    sess .headers .update ({'User-Agent':ua ,'Accept':'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7','Accept-Language':cfg ['lang'],'Accept-Encoding':'gzip, deflate, br, zstd','Upgrade-Insecure-Requests':'1','Sec-Fetch-Dest':'document','Sec-Fetch-Mode':'navigate','Sec-Fetch-Site':'none','Sec-Fetch-User':'?1','sec-ch-ua':chUa ,'sec-ch-ua-mobile':'?0','sec-ch-ua-platform':'"Windows"','sec-ch-ua-full-version-list':chUaFull ,'sec-ch-ua-platform-version':'"10.0"','device-memory':mem ,'sec-ch-device-memory':mem ,'dpr':str (dpr ),'sec-ch-dpr':str (dpr ),'viewport-width':str (vw ),'sec-ch-viewport-width':str (vw ),'ect':'4g','rtt':'50','downlink':'10'})
    return (sess ,ua )


def getField (html ,name ):
    """Valor de un input o select del HTML."""
    el =html .find ('input',{'name':name })
    if el :return el .get ('value','')
    sel =html .find ('select',{'name':name })
    if sel :
        opt =sel .find ('option',selected =True )
        return opt .get ('value','')if opt else None
    return None

def postForm (sess ,url ,data ,ref ,origin ,params =None ,timeout =None ):
    """POST de formulario con los headers que espera Amazon."""
    return sess .post (url ,data =data ,params =params ,headers ={'Content-Type':'application/x-www-form-urlencoded','Origin':origin ,'Referer':str (ref ),'Sec-Fetch-Dest':'document','Sec-Fetch-Mode':'navigate','Sec-Fetch-Site':'same-origin','Sec-Fetch-User':'?1','Upgrade-Insecure-Requests':'1'},timeout =timeout or 120 )

def genMeta (loc ,ref ,urls ,hashes ,ua ,email ='',name ='',passw ='',captcha =False ,otp =''):
    """Genera el metadata1 (huella FWCIM) que Amazon pide en cada envio."""
    res =FwcimClient (
    location =loc ,userAgent =ua ,referrer =ref ,
    dynamicUrls =urls ,inlineHashes =hashes ,
    emailValue =email ,customerName =name ,passwordValue =passw ,
    otpValue =otp ,postCaptcha =captcha ,
    ).generateMetadata ()
    if not res .get ('status'):
        raise RuntimeError (f'metadata1 failed: {res .get ("description")}')
    return res ['metadata1']

def extractScripts (html ):
    """URLs de scripts y hashes de scripts en linea de la pagina."""
    urls =[]
    for tag in re .findall (r'<script[\s\S]*?>[\s\S]*?<\/script>',html ,re .IGNORECASE ):
        for pat in [
        re .compile (r"""load\.js\([\'\"](https?://[^\'\"\)]+)[\'\"]\b"""),
        re .compile (r"""ue\.uels\([\'\"](https?://[^\'\"]+\.js)[\'\"]\b"""),
        re .compile (r"""src=[\"\'](https://static\.siege-amazon\.com/[^\"\']+\.js\?v=\d+)[\"\']\b"""),
        re .compile (r"""src=[\"\'](https://[^\"\']+AUIClients/[^\"\']+)[\"\']\b"""),
        re .compile (r"""src=[\"\'](https://m\.media-amazon\.com/[^\"\']+\.js[^\"\']*)[\"\']\b"""),
        re .compile (r"""src=[\"\'](https://images-na\.ssl-images-amazon\.com/[^\"\']+\.js[^\"\']*)[\"\']\b"""),
        ]:
            urls .extend (m .group (1 )for m in pat .finditer (tag ))
    seen ,deduped =set (),[]
    for u in urls :
        if u not in seen :seen .add (u );deduped .append (u )
    deduped .reverse ()
    hashes =[]
    for tag in BeautifulSoup (html ,'html.parser').find_all ('script'):
        if not tag .get ('src')and tag .string :
            txt =tag .string .strip ()
            if txt :
                h =0
                for ch in txt :h =(31 *h +ord (ch ))&0xFFFFFFFF
                if h >=0x80000000 :h -=0x100000000
                hashes .append (h )
    return (deduped ,hashes )

def normalizeProxy (proxy ):
    """Normaliza la proxy al formato http://usuario:pass@host:puerto."""
    if not proxy :return None
    proxy =proxy .strip ()
    if '://'in proxy :return proxy
    if '@'in proxy :return f'http://{proxy }'
    parts =proxy .split (':')
    if len (parts )>=4 and parts [1 ].isdigit ():
        return f'http://{parts [2 ]}:{":".join (parts [3 :])}@{parts [0 ]}:{parts [1 ]}'
    return f'http://{proxy }'

def alignProxy (proxy ,country ):
    """Fija el marcador de pais de la proxy."""
    if not proxy :return None
    # Solo se alinea el marcador de pais (en minuscula, como lo espera el
    # pool de proxies). Los pools por region ya vienen con la region correcta.
    return re .sub (r'([-_])country-[A-Za-z]{2}',lambda m :f'{m .group (1 )}country-{country .lower ()}',proxy ,count =1 ,flags =re .I )

def rotateProxySession (proxy ):
    """Genera una nueva sesion de proxy para cambiar IP dentro del mismo pais."""
    if not proxy :return proxy
    session_id =secrets .token_hex (8 )
    proxy =re .sub (r'_session-[A-Za-z0-9_-]+',f'_session-{session_id }',proxy )
    return re .sub (r'-session-[A-Za-z0-9_-]+',f'-session-{session_id }',proxy )

def isAuth (sess ):
    """True si la sesion ya quedo autenticada (cookies at-main / at-acb)."""
    for c in sess .cookies .jar :
        if c .value and (c .name =='at-main'or c .name .startswith ('at-acb')):return True
    return False

def isOtp (txt ):
    """True si la pagina pide el codigo de verificacion."""
    if isCaptcha (txt ):return False
    low =txt .lower ()
    if 'se ha producido un error'in low :return False
    kw =('verify your identity','verification code','we sent a code','verify mobile','two-step verification','enter the code','confirma tu identidad','código de verificación','enviamos un código','te enviamos un código','verifica tu número','identität bestätigen','bestätigungscode','vérifier votre identité','code de vérification','nous avons envoyé un code','verifica la tua identità','codice di verifica','abbiamo inviato un codice','verificar sua identidade','código de verificação','enviamos um código','本人確認','確認コード','コードを送信しました')
    return any (k in low for k in kw )and bool (re .search (r'name\s*=\s*["\']code["\']',txt ,re .I ))

def isCaptcha (txt ):
    """True si la pagina pide una verificacion de seguridad (Arc/Arkose)."""
    low =txt .lower ()
    kw =('aamation','authentication required','autenticación requerida','authentifizierung erforderlich','authentification requise','autenticazione richiesta','autenticação necessária','認証が必要')
    return 'data-external-id'in low and any (k in low for k in kw )

def isUnusual (txt ):
    """True si Amazon marco actividad inusual."""
    low =txt .lower ()
    kw =('unusual activity',"aren't able to create",'we cannot create','actividad inusual','no podemos crear','activité inhabituelle','impossible de créer','attività insolita','non è possibile creare','atividade incomum','não conseguimos criar','通常と異なるアクティビティ','アカウントを作成できません')
    return any (k in low for k in kw )

def cvfChallengeType (txt ):
    """Tipo de reto de la pagina de verificacion (PROOF_OF_WORK, ARKOSE_*)."""
    m =re .search (r'"data-challenge-type"\s*:\s*"([A-Z0-9_]+)"',txt )
    return m .group (1 )if m else None

def emitStatus (status ,text ):
    """Informa el avance del flujo (el CLI lo muestra en pantalla)."""
    if not status :return
    try :
        status (text )
    except Exception :
        pass

def isRegForm (txt ):
    """True si la pagina es el formulario de registro."""
    if 'appActionToken'not in txt :return False
    for form in BeautifulSoup (txt ,'html.parser').find_all ('form'):
        if form .find ('input',{'name':'appActionToken'}):return True
    return False

def submitReg (sess ,page ,ref ,phoneShort ,user ,ua ,baseUrl ,dynUrls ,dynHashes ,code ,label ,captcha =False ,lean =False ,regParams =None ):
    """Envia el formulario de registro con el correo y la contrasena cifrada."""
    html =BeautifulSoup (page .text ,'html.parser')
    pu ,ph =extractScripts (page .text )
    meta =genMeta (str (page .url ),'',pu or dynUrls ,ph or dynHashes ,ua ,email =phoneShort ,name =user .name ,passw =user .password ,captcha =captcha )
    token =getField (html ,'appActionToken')
    cseP =getCse (code )
    enc =encryptPassw (user .password ,cseP ['jwkN'],cseP ['keyId'],token )
    payload ={'appActionToken':token ,'appAction':'REGISTER','openid.return_to':getField (html ,'openid.return_to'),'prevRID':getField (html ,'prevRID'),'workflowState':getField (html ,'workflowState'),'anti-csrftoken-a2z':getField (html ,'anti-csrftoken-a2z'),'claimCollectionLayoutType':getField (html ,'claimCollectionLayoutType')or 'unifiedAuthClaimCollection','unifiedAuthTreatment':getField (html ,'unifiedAuthTreatment')or '','email':phoneShort ,'customerName':user .name ,'encryptedPwd':enc ,'encryptedPwdCheck':enc ,'metadata1':meta }
    if not lean :
        payload ['countryCode']=getField (html ,'countryCode')or code
        payload ['changeClaimUrl']=getField (html ,'changeClaimUrl')or ''
        payload ['encryptedPasswordExpected']=''
    ft =html .find ('form',action =True )
    act =ft .get ('action','')if ft else ''
    url =act if act .startswith ('http')else f'{baseUrl }/ap/register'
    return postForm (sess ,url ,payload ,page .url ,baseUrl ,params =regParams )



class RegError (Exception ):pass

def hasWaf (sess ,domain =None ):
    """True si la sesion ya tiene el token WAF."""
    for c in sess .cookies .jar :
        if c .name =='aws-waf-token'and c .value :
            if domain is None or c .domain .lstrip ('.')==domain .lstrip ('.'):return True
    return False

def solveWaf (sess ,baseUrl ,domain ,proxy ,ua ,force =False ,country ='US'):
    """Resuelve el reto WAF y deja el token en la sesion."""
    if not force and not needsWaf (country ):return True
    if not force and hasWaf (sess ,domain ):return True
    try :
        res =AwsWaf (websiteURL =f'{baseUrl }/',proxy =normalizeProxy (proxy ),userAgent =ua ).solve ()
        if res ['status']:
            sess .cookies .set ('aws-waf-token',res ['token'],domain =f'.{domain }',path ='/')
            return True
        return False
    except Exception :return False

def prefetchWaf (baseUrl ,domain ,proxy ,ua ,country ='US'):
    """Resuelve el reto WAF en paralelo (se adelanta al registro)."""
    if not needsWaf (country ):return {'status':False ,'description':'skipped','token':None }
    try :return AwsWaf (websiteURL =f'{baseUrl }/',proxy =normalizeProxy (proxy ),userAgent =ua ).solve ()
    except Exception as e :return {'status':False ,'description':str (e ),'token':None }

def applyWaf (sess ,domain ,res ):
    """Pone en la sesion el token WAF ya resuelto."""
    if res .get ('status')and res .get ('token'):
        sess .cookies .set ('aws-waf-token',res ['token'],domain =f'.{domain }',path ='/')
        return True
    return False

def visitSignin (sess ,baseUrl ,handle ):
    """Abre /ap/signin y saca el arb (token) y el csrf."""
    params ={'openid.return_to':f'{baseUrl }/?ref_=nav_ya_signin','openid.identity':'http://specs.openid.net/auth/2.0/identifier_select','openid.assoc_handle':handle ,'openid.mode':'checkid_setup','openid.claimed_id':'http://specs.openid.net/auth/2.0/identifier_select','openid.ns':'http://specs.openid.net/auth/2.0'}
    r =sess .get (f'{baseUrl }/ap/signin',params =params ,headers ={'Referer':f'{baseUrl }/','Sec-Fetch-Site':'same-origin','Sec-Fetch-User':'?1'},timeout =60 )
    soup =BeautifulSoup (r .text ,'html.parser')
    arb =getField (soup ,'arb')or (lambda m :m .group (1 )if m else None )(re .search ('arb=([a-zA-Z0-9_-]+)',r .text ))
    csrf =getField (soup ,'anti-csrftoken-a2z')
    return (r ,arb ,csrf )

def claimEmail (sess ,signinResp ,arb ,csrf ,email ,ua ,baseUrl ,handle ,dynUrls ,dynHashes ):
    """Reclama la identidad del correo: deja la sesion lista para registrar."""
    meta =genMeta (str (signinResp .url ),'',dynUrls ,dynHashes ,ua ,email =email )
    params ={'openid.assoc_handle':handle ,'openid.mode':'checkid_setup','policy_handle':'Retail-Checkout','openid.return_to':f'{baseUrl }/?ref_=nav_ya_signin','openid.ns':'http://specs.openid.net/auth/2.0','arb':arb }
    payload ={'appAction':'SIGNIN_CLAIM_COLLECT','subPageType':'FullPageUnifiedClaimCollect','claimCollectionWorkflow':'unified','metadata1':meta ,'claimType':'','countryCode':'','isServerSideRouting':'true','openid.ns':'http://specs.openid.net/auth/2.0','openid.mode':'checkid_setup','openid.return_to':f'{baseUrl }/?ref_=nav_ya_signin','openid.assoc_handle':handle ,'openid.identity':'http://specs.openid.net/auth/2.0/identifier_select','openid.claimed_id':'http://specs.openid.net/auth/2.0/identifier_select','signalUnknownCredentialUnifiedAuthWeblabActive':'false','anti-csrftoken-a2z':csrf ,'ue_back':'1','email':email ,'password':''}
    r =postForm (sess ,f'{baseUrl }/ax/claim',payload ,str (signinResp .url ),baseUrl ,params =params )
    soup =BeautifulSoup (r .text ,'html.parser')
    arb2 =getField (soup ,'arb')or (lambda m :m .group (1 )if m else None )(re .search ('arb=([a-zA-Z0-9_-]+)',r .text ))
    csrf2 =getField (soup ,'anti-csrftoken-a2z')
    if not arb2 :
        if 'SIGNIN_PWD_COLLECT'in r .text or 'SIGNIN_OTP_COLLECT'in r .text :raise RegError ('number_associated')
        raise RegError ('no_arb')
    ft =soup .find ('form')
    raw =(ft .get ('action','')if ft else '').replace ('&amp;','&')
    m =re .search (r'openid\.return_to=([^&"\']+)',unquote (raw ))
    ret =unquote (m .group (1 ))if m else f'{baseUrl }/'
    rp ={'openid.mode':'checkid_setup','openid.ns':'http://specs.openid.net/auth/2.0','openid.identity':'http://specs.openid.net/auth/2.0/identifier_select','openid.claimed_id':'http://specs.openid.net/auth/2.0/identifier_select','openid.ns.pape':'http://specs.openid.net/extensions/pape/1.0','showRememberMe':'true','openid.pape.max_auth_age':'900','pageId':handle ,'prepopulatedLoginId':'','openid.assoc_handle':handle ,'openid.return_to':ret ,'policy_handle':'Retail-Checkout'}
    rp2 =postForm (sess ,f'{baseUrl }/ap/register',{'claimCollectionLayoutType':'unifiedAuthClaimCollection','unifiedAuthTreatment':'','email':email ,'arb':arb2 ,'csrf-token':csrf2 },str (r .url ),baseUrl ,params =rp )
    return (rp2 ,r .url ,rp )

def submitRegWithCaptcha (sess ,regPage ,claimUrl ,phoneShort ,user ,ua ,baseUrl ,dynUrls ,dynHashes ,code ,handle ,lean =False ,regParams =None ,status =None ):
    """Registra el correo y, si aparece un reto, lo resuelve y sigue."""
    html =BeautifulSoup (regPage .text ,'html.parser')
    if not getField (html ,'appActionToken'):raise RegError ('no_register_form')
    r =submitReg (sess ,regPage ,claimUrl ,phoneShort ,user ,ua ,baseUrl ,dynUrls ,dynHashes ,code ,'first',lean =lean ,regParams =regParams )
    if 'mobileclaimconflict'in str (r .url ):
        cont =continueMobileConflict (sess ,r ,baseUrl )
        if cont is None :raise RegError ('number_associated')
        r =cont
    if isAuth (sess ):return r
    if isUnusual (r .text ):raise RegError ('unusual activity')
    if isOtp (r .text ):return r
    challenge =cvfChallengeType (r .text )
    if challenge or isCaptcha (r .text ):
        logger .warning ('cvf challenge: %s url=%s status=%s len=%s',challenge or 'unknown',getattr (r ,'url','?'),getattr (r ,'status_code','?'),len (r .text or ''))
        emitStatus (status ,'Amazon esta pidiendo una verificacion de seguridad (captcha). Resolviendo...')
        if not challenge :
            raise RegError ('captcha_appeared')
        try :
            r =solveCvf (sess ,r ,challenge ,baseUrl )
            logger .info ('cvf solved: url=%s status=%s len=%s',getattr (r ,'url','?'),getattr (r ,'status_code','?'),len (r .text or ''))
        except Exception as e :
            logger .warning ('cvf solve failed: %s',e )
            raise RegError ('captcha_appeared')
        if isAuth (sess ):return r
        if isUnusual (r .text ):raise RegError ('unusual activity')
        if isOtp (r .text ):return r
        if isCaptcha (r .text ):raise RegError ('captcha_appeared')
    if '/ap/cvf/'in str (r .url ):return r
    if isRegForm (r .text )and any (kw in r .text .lower ()for kw in ("there's already an account","already an account with this","ya existe una cuenta","il existe déjà","es gibt bereits","esiste già")):
        raise RegError ('email_associated')
    if isCaptcha (r .text ):raise RegError ('captcha_appeared')
    if not isOtp (r .text ):
        try :
            logger .warning ('unexpected_response at %s status=%s len=%s snippet=%s',
            getattr (r ,'url','?'),getattr (r ,'status_code','?'),
            len (r .text ),r .text [:500 ].replace ('\n',' ')[:500 ])
        except Exception :
            pass
        raise RegError ('unexpected_response')

def continueMobileConflict (sess ,resp ,baseUrl ):
    """Confirma el aviso de conflicto del numero de telefono."""
    soup =BeautifulSoup (resp .text ,'html.parser')
    ft =soup .find ('form')
    if not ft :return None
    act =ft .get ('action','')
    if not act :return None
    if not act .startswith ('http'):act =urljoin (str (resp .url ),act )
    pl ={i .get ('name'):i .get ('value','')for i in ft .find_all ('input')if i .get ('name')}
    if pl .get ('appAction')!='MOBILE_PHONE_REGISTRATION_CONFLICT_WARNED_VERIFY':return None
    return postForm (sess ,act ,pl ,str (resp .url ),baseUrl )

def detectChannel (txt ):
    """Canal de envio del codigo: email, sms o whatsapp."""
    if bool (re .search (r'name=["\']hideSendOtpOverSms["\']\s+value=["\']true["\']',txt )):
        m =re .search (r'name=["\']requestedContactType["\']\s+value=["\']([^"\']+)["\']',txt )
        if m :
            v =m .group (1 ).strip ().lower ()
            if v =='whatsapp':return 'whatsapp'
            if v in ('sms','sms_otp'):return 'sms'
    m =re .search (r'name=["\']verificationPageContactType["\']\s+value=["\']([^"\']+)["\']',txt )
    if m :
        v =m .group (1 ).strip ().lower ()
        if v in ('sms','sms_otp'):return 'sms'
        if v =='whatsapp':return 'whatsapp'
        if v =='email':return 'email'
    low =txt .lower ()
    hasWa =any (k in low for k in ('whatsapp','wa.me','sent a code via whatsapp','código por whatsapp','enviamos un código por whatsapp','code via whatsapp'))
    hasSms =any (k in low for k in ('text message','sms','mensaje de texto','sms-code','code par sms'))
    if hasWa and not hasSms :return 'whatsapp'
    if hasSms and not hasWa :return 'sms'
    if hasWa and hasSms :return 'whatsapp'
    return 'unknown'

def switchToSms (sess ,resp ,baseUrl ):
    """Cambia el canal del codigo a SMS (WhatsApp no sirve con numero virtual)."""
    soup =BeautifulSoup (resp .text ,'html.parser')
    ch =detectChannel (resp .text )
    if ch in ('email','sms'):return resp
    if ch =='whatsapp':
        for ft in soup .find_all ('form'):
            inp =ft .find ('input',{'name':'requestedContactType'})
            if inp and inp .get ('value','').lower ()=='whatsapp':
                pl ={i .get ('name'):i .get ('value','')for i in ft .find_all ('input')if i .get ('name')}
                pl ['requestedContactType']='sms';pl .pop ('hideSendOtpOverSms',None )
                act =ft .get ('action','')
                if act and not act .startswith ('http'):act =urljoin (str (resp .url ),act )
                if not act :act =f'{baseUrl }/ap/cvf/verify'
                try :
                    r2 =postForm (sess ,act ,pl ,str (resp .url ),baseUrl )
                    s2 =BeautifulSoup (r2 .text ,'html.parser')
                    ch2 =detectChannel (r2 .text )
                    if (s2 .find ('input',{'name':'code'})or ch2 =='sms')and ch2 !='whatsapp':return r2
                except Exception :pass
                raise RegError ('sms_blocked')
    sms_patterns =[('newContactType','SMS_OTP'),('newContactType','sms'),('requestedContactType','sms'),('otpChannel','SMS'),('otpChannel','sms'),('deliveryMechanism','sms'),('codeDeliveryType','SMS'),('verificationMechanism','sms'),('channel','sms'),('contactType','sms')]
    for fname ,fval in sms_patterns :
        for ft in soup .find_all ('form'):
            inp =ft .find ('input',{'name':fname })
            if not inp :continue
            if (inp .get ('value')or '').strip ().lower ()==fval .lower ():
                pl ={i .get ('name'):i .get ('value','')for i in ft .find_all ('input')if i .get ('name')}
                act =ft .get ('action','')
                if act and not act .startswith ('http'):act =urljoin (str (resp .url ),act )
                if not act :act =f'{baseUrl }/ap/cvf/verify'
                r2 =postForm (sess ,act ,pl ,str (resp .url ),baseUrl )
                ch2 =detectChannel (r2 .text )
                s2 =BeautifulSoup (r2 .text ,'html.parser')
                if s2 .find ('input',{'name':'code'})and ch2 !='whatsapp':return r2
                if ch2 =='sms':return r2
    for a in soup .find_all ('a',href =True ):
        href =a ['href'];hl =href .lower ()
        if 'switchdefaultcvfotpchannel'in hl or 'switch_contact_type'in hl or 'deliverytype=sms'in hl or 'newcontacttype=sms'in hl :
            full =href if href .startswith ('http')else urljoin (str (resp .url ),href )
            try :
                r2 =sess .get (full ,headers ={'Referer':str (resp .url ),'Sec-Fetch-Dest':'document','Sec-Fetch-Mode':'navigate','Sec-Fetch-Site':'same-origin'},timeout =60 )
                ch2 =detectChannel (r2 .text )
                if ch2 =='sms'or BeautifulSoup (r2 .text ,'html.parser').find ('input',{'name':'code'}):return r2
            except Exception :pass
    if ch =='whatsapp':
        for url in [f'{baseUrl }/ap/cvf/verify?action=SWITCH_CONTACT_TYPE_IN_CVF&newContactType=SMS_OTP',f'{baseUrl }/ap/cvf/verify?action=switchDefaultCvfOtpChannel&deliveryType=sms']:
            try :
                r2 =sess .get (url ,headers ={'Referer':str (resp .url )},timeout =60 )
                if detectChannel (r2 .text )=='sms'or BeautifulSoup (r2 .text ,'html.parser').find ('input',{'name':'code'}):return r2
            except Exception :continue
    return resp

def selectEmailJP (sess ,resp ,baseUrl ):
    """En Japon el codigo puede llegar por correo: se fuerza ese canal."""
    soup =BeautifulSoup (resp .text ,'html.parser')
    if soup .find ('input',{'name':'code'}):return resp
    if detectChannel (resp .text )=='email':return resp
    for a in soup .find_all ('a',href =True ):
        href =a .get ('href','')
        if any (k in href .lower ()for k in ('email','contacttype=email','deliverytype=email')):
            full =href if href .startswith ('http')else urljoin (str (resp .url ),href )
            try :
                r2 =sess .get (full ,headers ={'Referer':str (resp .url ),'Sec-Fetch-Dest':'document','Sec-Fetch-Mode':'navigate','Sec-Fetch-Site':'same-origin'},timeout =60 )
                s2 =BeautifulSoup (r2 .text ,'html.parser')
                if s2 .find ('input',{'name':'code'})or detectChannel (r2 .text )=='email':return r2
            except Exception :pass
    for ft in soup .find_all ('form'):
        for inp in ft .find_all ('input'):
            if inp .get ('value','').lower ()in ('email','email_otp','email_address'):
                pl ={i .get ('name'):i .get ('value','')for i in ft .find_all ('input')if i .get ('name')}
                act =ft .get ('action','')
                if act and not act .startswith ('http'):act =urljoin (str (resp .url ),act )
                act =act or f'{baseUrl }/ap/cvf/verify'
                try :
                    r2 =postForm (sess ,act ,pl ,str (resp .url ),baseUrl )
                    s2 =BeautifulSoup (r2 .text ,'html.parser')
                    if s2 .find ('input',{'name':'code'})or detectChannel (r2 .text )=='email':return r2
                except Exception :pass
    for url in [f'{baseUrl }/ap/cvf/verify?action=SWITCH_CONTACT_TYPE_IN_CVF&newContactType=EMAIL',f'{baseUrl }/ap/cvf/verify?action=switchDefaultCvfOtpChannel&deliveryType=email',f'{baseUrl }/ap/cvf/request?contactType=EMAIL']:
        try :
            r2 =sess .get (url ,headers ={'Referer':str (resp .url ),'Sec-Fetch-Dest':'document','Sec-Fetch-Mode':'navigate','Sec-Fetch-Site':'same-origin'},timeout =60 )
            s2 =BeautifulSoup (r2 .text ,'html.parser')
            if s2 .find ('input',{'name':'code'})or detectChannel (r2 .text )=='email':return r2
        except Exception :continue
    return resp

def requestSmsChannel (sess ,resp ,baseUrl ):
    """Pide que Amazon envie el codigo por SMS en vez de WhatsApp."""
    try :
        r =switchToSms (sess ,resp ,baseUrl )
    except RegError :
        return None
    except Exception as e :
        logger .warning ('No se pudo cambiar a canal SMS: %s',e )
        return None
    if r is None :return None
    txt =getattr (r ,'text','')or ''
    if detectChannel (txt )=='sms':
        logger .info ('canal SMS activo len=%s',len (txt ))
        return r
    return None

def contactType (txt ):
    """Canal de verificacion de la pagina de codigo (email / sms / whatsapp)."""
    m =re .search (r'name=["\']verificationPageContactType["\']\s+value=["\']([^"\']+)["\']',txt ,re .I )
    return m .group (1).strip ().lower ()if m else ''

def requestResend (sess ,resp ,baseUrl ):
    """Le pide a Amazon que reenvie el codigo por el mismo canal."""
    soup =BeautifulSoup (resp .text ,'html.parser')
    ft =None
    for f in soup .find_all ('form'):
        if any (i .get ('value')=='resend'for i in f .find_all ('input',{'name':'cvf_action'})):
            ft =f ;break
    if ft is None :return None
    act =ft .get ('action','')or 'verify'
    if not act .startswith ('http'):act =urljoin (f'{baseUrl }/ap/cvf/',act .lstrip ('/'))
    pl ={}
    for inp in ft .find_all ('input'):
        n =inp .get ('name')
        if not n :continue
        if n =='cvf_action':
            if inp .get ('value')=='resend':pl [n ]='resend'
            continue
        pl [n ]=inp .get ('value','')
    pl ['cvf_action']='resend'
    try :
        r =postForm (sess ,act ,pl ,str (resp .url ),baseUrl ,timeout =60 )
    except Exception as e :
        logger .warning ('No se pudo pedir el reenvio del codigo: %s',e )
        return None
    logger .info ('resend solicitado url=%s len=%s',getattr (r ,'url','?'),len (getattr (r ,'text','')or ''))
    return r

def collectPhone (sess ,resp ,country ,baseUrl ,short ,user ,ua ,dynUrls ,dynHashes ,status =None ,phone_out =None ):
    """Completa el paso de CVF que exige un numero de telefono movil.

    Compra un numero virtual (HeroSMS), lo envia en el formulario de
    verificacion, espera el SMS con el codigo y responde ese codigo. Si
    Amazon no acepta el numero o no llega el SMS, se cancela el pedido para
    que el proveedor reembolse.
    """
    soup =BeautifulSoup (resp .text ,'html.parser')
    ft =soup .find ('form',action ='verify')or soup .find ('form')
    if ft is None :raise RegError ('phone_form_missing')
    act =ft .get ('action','')or 'verify'
    if not act .startswith ('http'):act =urljoin (f'{baseUrl }/ap/cvf/',act .lstrip ('/'))
    pl ={}
    for inp in ft .find_all ('input'):
        n =inp .get ('name')
        if not n :continue
        pl [n ]=inp .get ('value','')
    cc =(country or '').upper ()
    if ft .find ('select',{'name':'cvf_phone_cc'})is not None :
        pl ['cvf_phone_cc']=cc
    emitStatus (status ,'Amazon pide verificar un numero de celular. Comprando numero y esperando el SMS...')
    sms =HeroSms ()
    if not sms .available ():raise RegError ('sms_not_configured')
    aid ,number =sms .buy (country )
    local =sms .local_number (number ,country )
    logger .info ('SMS number ready for %s (order %s)',cc ,aid )
    if phone_out is not None :
        try :phone_out ['number'] =local
        except Exception :pass
    pl ['cvf_phone_num']=local
    pl ['cvf_action']='collect'
    try :
        r =postForm (sess ,act ,pl ,str (resp .url ),baseUrl ,timeout =60 )
    except Exception as e :
        sms .cancel (aid )
        raise RegError (f'phone_submit_failed: {e }')from e
    _t =getattr (r ,'text','')or ''
    try :
        open ('/tmp/cvf_after_phone.html','w',encoding ='utf-8').write (_t )
    except Exception :
        pass
    has_code =('name="code"'in _t )or ("name='code'"in _t )
    logger .info ('phone submit url=%s len=%s has_code_input=%s',
    getattr (r ,'url','?'),len (_t ),has_code )
    if has_code and 'whatsapp'in _t .lower () :
        r2 =requestSmsChannel (sess ,r ,baseUrl )
        if r2 is not None :
            r =r2
            _t =getattr (r ,'text','')or ''
            try :
                open ('/tmp/cvf_after_phone.html','w',encoding ='utf-8').write (_t )
            except Exception :
                pass
            logger .info ('sms channel page len=%s whatsapp=%s',len (_t ),'whatsapp'in _t .lower ())
    if 'cvf_phone_num'in _t :
        sms .cancel (aid )
        raise RegError ('phone_submit_rejected')
    if contactType (_t )=='whatsapp':
        # Amazon quiere mandar el codigo por WhatsApp: un numero virtual no
        # lo recibe, mejor cancelar y reintentar con otro intento.
        sms .cancel (aid )
        raise RegError ('whatsapp_channel')
    code =None
    deadline =time .time ()+165
    while time .time ()<deadline and not code :
        try :
            code =sms .wait_code (aid ,timeout =55 ,interval =8 )
        except SmsError :
            code =None
        if code :break
        emitStatus (status ,'Todavia no llega el SMS. Pidiendo que Amazon lo reenvie...')
        r2 =requestResend (sess ,r ,baseUrl )
        if r2 is not None :r =r2
    if not code :
        # Ese numero no sirvio (HeroSMS recicla los cancelados y ya no
        # reciben mensajes): se aparta para no volver a comprarlo.
        sms .mark_bad (local )
        sms .cancel (aid )
        raise RegError ('sms_timeout: No llego el codigo por SMS a tiempo')
    logger .info ('SMS code received for %s',country )
    emitStatus (status ,'Llego el codigo por SMS. Verificando con Amazon...')
    try :
        submitOtp (sess ,r ,code ,short ,user ,ua ,baseUrl ,dynUrls ,dynHashes ,allowPhone =False ,status =status )
    except Exception :
        sms .cancel (aid )
        raise
    if isAuth (sess ):sms .finish (aid )
    else :sms .cancel (aid )
    return r

def submitOtp (sess ,resp ,code ,phoneShort ,user ,ua ,baseUrl ,dynUrls ,dynHashes ,regDomain ='amazon.com',targetDomain =None ,country =None ,allowPhone =True ,status =None ,phone_out =None ):
    """Envia el codigo de verificacion y devuelve la respuesta final."""
    targetDomain =targetDomain or regDomain
    soup =BeautifulSoup (resp .text ,'html.parser')
    forms =[f for f in soup .find_all ('form')if f .find ('input',{'name':'code'})]
    ft =next ((f for f in forms if (f .find ('input',{'name':'verificationPageContactType'})or type ('',(),{'get':lambda s ,k ,d ='':''})()).get ('value','').lower ()=='email'),None )
    if ft is None and forms :ft =forms [0 ]
    if ft is None :ft =soup .find ('form')
    act =ft .get ('action','')if ft else ''
    if act and not act .startswith ('http'):act =urljoin (f'{baseUrl }/ap/cvf/',act .lstrip ('/'))
    if not act :act =f'{baseUrl }/ap/cvf/verify'
    ou ,oh =extractScripts (resp .text )
    pl ={}
    if ft :
        for inp in ft .find_all ('input'):
            n =inp .get ('name')
            if not n or n =='code':continue
            v =inp .get ('value','')
            if n =='cvf_action':
                # El formulario trae varios botones con el mismo nombre
                # (code / resend / collect). Al responder un codigo importa
                # el que dice "code": con otro valor Amazon reinicia el paso.
                if v =='code'or 'cvf_action'not in pl :pl [n ]=v
                continue
            if n =='resendContactType':continue
            pl [n ]=v
        for sel in ft .find_all ('select'):
            n =sel .get ('name')
            if not n :continue
            opt =sel .find ('option',selected =True )or sel .find ('option')
            pl [n ]=opt .get ('value','')if opt else ''
    else :
        pl ['action']='code'
    pl ['code']=code
    pl ['metadata1']=genMeta (str (resp .url ),str (resp .url ),ou ,oh ,ua ,email =phoneShort ,name =user .name ,passw =user .password ,otp =code )
    if 'action'not in pl :pl ['action']='code'
    try :
        logger .info ('otp submit code=%s act=%s fields=%s',code ,act ,list (pl .keys ()))
        r =postForm (sess ,act ,pl ,str (resp .url ),baseUrl ,timeout =60 )
        logger .info ('otp post url=%s len=%s auth=%s',getattr (r ,'url','?'),len (getattr (r ,'text','')or ''),isAuth (sess ))
    except Exception as e :
        if isAuth (sess ):return None
        raise RegError (f'otp_failed: {e }')from e
    if not isAuth (sess ):
        _t =getattr (r ,'text','')or ''
        try :
            open ('/tmp/cvf_last_step.html','w',encoding ='utf-8').write (_t )
        except Exception :
            pass
        if 'cvf_phone_num'in _t :
            # Ya estamos respondiendo el codigo del SMS y Amazon devuelve la
            # pantalla del telefono: el codigo no fue aceptado. Se reintenta
            # con otro numero en vez de terminar el intento.
            if not allowPhone :raise RegError ('sms_code_rejected')
            r =collectPhone (sess ,r ,country or 'US',baseUrl ,phoneShort ,user ,ua ,dynUrls ,dynHashes ,status ,phone_out =phone_out )
            if not isAuth (sess ):raise RegError ('sms_code_rejected')
            return r
        raise RegError ('otp_failed')
    # Se devuelve la respuesta final: es la pagina donde Amazon avisa que la
    # cuenta quedo suspendida y antes se descartaba (cuenta suspendida
    # entregada como "Account created!").
    return r



class AccountBuilder :
    """Agrega la direccion de facturacion y exporta las cookies."""
    addUa ='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36'

    def __init__ (self ,sess ,country ,name =None ,phone =None ):
        country =country .upper ()
        if country not in countries :raise ValueError (f"Country '{country }' not supported")
        self .country =country
        self .domain =countries [country ]['domain']
        self .sess =sess
        self .name =name or ''
        self .phone =phone or ''

    @classmethod
    def fromSess (cls ,sess ,country ,name =None ,phone =None ):
        """Crea el generador de direccion a partir de la sesion ya iniciada."""
        return cls (sess ,country ,name =name ,phone =phone )

    def between (self ,hay ,start ,end ,idx =1 ):
        if hay is None :return None
        try :return hay .split (start )[idx ].split (end )[0 ]
        except (IndexError ,ValueError ):return None


    def addAddress (self ):
        """Agrega la direccion de facturacion con datos del pais."""
        d =self .domain
        # La direccion de facturacion se armaba con literales fijos
        # ("Street 1, Kabul, Kabul, 1001", countryCode=AF) y con un nombre de
        # un pool de 25: todas las cuentas compartian la misma direccion - una
        # huella de registro en serie muy facil de agrupar. Ahora sale de
        # Faker con el locale del pais y usa el nombre real del titular y el
        # telefono que Amazon ya valido en el CVF.
        cfg =countries [self .country ]
        try :fk =Faker (cfg ['faker'])
        except Exception :fk =Faker ('en_US')
        nm =self .name or f'{fk .first_name ()} {fk .last_name ()}'
        try :line1 =fk .street_address ()
        except Exception :line1 ='Street 1'
        try :city =fk .city ()
        except Exception :city =cfg ['code']
        try :state =fk .state ()
        except Exception :state =cfg ['code']
        try :zipc =fk .postcode ()
        except Exception :zipc ='00000'
        # El widget de Amazon acepta codigo postal alfanumerico sin guiones;
        # Faker en es_MX devuelve "79067-2747".
        zipc =re .sub (r'[^A-Za-z0-9]','',str (zipc ))or '00000'
        if self .country =='MX' and zipc .isdigit ()and len (zipc )>5 :zipc =zipc [:5]
        ph =re .sub (r'\D','',self .phone or '')
        if not ph :
            try :ph =re .sub (r'\D','',fk .phone_number ())
            except Exception :ph =''
        if not ph :ph ='55'+str (random .randint (1000000000 ,9999999999 ))
        try :
            r1 =self .sess .get (f'https://www.{d }/a/addresses/add?ref_=ya_address_book_add_button',headers ={'User-Agent':self .addUa },timeout =20 ).text
        except Exception :return False
        csrf =self .between (r1 ,"type='hidden' name='csrfToken' value='","'")
        if not csrf :return False
        jwt =self .between (r1 ,'name="address-ui-widgets-previous-address-form-state-token" value="','"')or ''
        wCsrf =self .between (r1 ,'name="address-ui-widgets-csrfToken" value="','"')or ''
        fConf =self .between (r1 ,'name="address-ui-widgets-form-config" value="','"')or ''
        obfCus =self .between (r1 ,'name="address-ui-widgets-obfuscated-customerId" value="','"')or ''
        pl =(f'csrfToken={quote_plus (csrf )}&address-ui-widgets-countryCode={quote_plus (cfg ["code"] )}'
        f'&address-ui-widgets-enterAddressFullName={quote_plus (nm )}'
        f'&address-ui-widgets-enterAddressPhoneNumber={quote_plus (ph )}'
        f'&address-ui-widgets-enterAddressLine1={quote_plus (line1 )}'
        f'&address-ui-widgets-enterAddressCity={quote_plus (city )}'
        f'&address-ui-widgets-enterAddressStateOrRegion={quote_plus (state )}'
        f'&address-ui-widgets-enterAddressPostalCode={quote_plus (zipc )}'
        +(f'&address-ui-widgets-previous-address-form-state-token={quote_plus (jwt )}'if jwt else '')
        +(f'&address-ui-widgets-csrfToken={quote_plus (wCsrf )}'if wCsrf else '')
        +(f'&address-ui-widgets-form-config={quote_plus (fConf )}'if fConf else '')
        +(f'&address-ui-widgets-obfuscated-customerId={quote_plus (obfCus )}'if obfCus else ''))
        try :
            r2 =self .sess .post (f'https://www.{d }/a/addresses/add?ref=ya_address_book_add_post',
            headers ={'Content-Type':'application/x-www-form-urlencoded','User-Agent':self .addUa ,'Origin':f'https://www.{d }','Referer':f'https://www.{d }/a/addresses/add?ref=ya_address_book_add_button','Upgrade-Insecure-Requests':'1'},
            data =pl ,timeout =20 )
        except Exception :return False
        for pat in [r'ypc-aborder-address-id=([A-Za-z0-9-]+)',r'ya_address_book-id=([A-Za-z0-9-]+)',r'addressId=([A-Za-z0-9-]+)',r'addressID=([A-Za-z0-9-]+)']:
            for src in [str (r2 .url )]+[h .headers .get ('Location','')for h in getattr (r2 ,'history',[])]:
                m =re .search (pat ,src )
                if m and len (m .group (1 ))>=8 :self .addrId =m .group (1 );return True
            m =re .search (pat ,r2 .text )
            if m and len (m .group (1 ))>=8 :self .addrId =m .group (1 );return True
        return r2 .status_code ==200

    def exportCookies (self ):
        """Serializa las cookies de la sesion en un solo texto."""
        skip =frozenset ({'ak_bmsc','bm_sv','csm-hit'})
        return '; '.join (f'{c .name }={c .value }'for c in self .sess .cookies .jar if c .value and c .name not in skip )



class Creator :
    """Orquesta el flujo completo de creacion de una cuenta."""
    maxRetries =3
    burned =('email_associated','email_timeout','unexpected_response')

    def __init__ (self ,country ='US',proxy =None ,mail =None ):
        self .on_status =None
        self .deadline =None
        if country not in supported :raise ValueError (f"Country '{country }' not supported")
        cfg =countries [country ]
        self .country =country
        self .domain =cfg ['domain']
        self .base =f'https://www.{self .domain }'
        self .handle =cfg ['handle']
        self .proxy =rotateProxySession (alignProxy (proxy ,country ))if proxy else None
        self .user =fakeProfile (country )
        self .phone =None
        self .cvfPhone =None
        self .mail =mail if mail is not None else TempMail ()
        self .pool =ThreadPoolExecutor (max_workers =3 )
        self .sessFut =None
        self .wafFut =None
        self .fpFut =None
        self .profile =None

    def run (self ,retry =0 ):
        """Ejecuta el flujo completo y devuelve status, profile y cookies."""
        try :
            self .init (retry )
            sess =self .flow ()
            builder =AccountBuilder .fromSess (sess ,self .country ,name =f'{self .user .fname } {self .user .lname }' ,phone =self .cvfPhone or '' )
            logger .info ('Adding billing address...')
            try :
                addrOk =builder .addAddress ()
            except Exception :
                addrOk =False
            cookies =builder .exportCookies ()
            p ={'name':f'{self .user .fname } {self .user .lname }','email':self .phone ['number'],'password':self .user .password }
            if not addrOk :
                logger .warning ('Billing address failed — delivering partial success to preserve credentials')
                return {'status':True ,'profile':p ,'cookies':cookies ,'domain':self .domain ,'registeredCountry':self .country ,'warning':'address_failed'}
            logger .info (f'\nAccount created!\n  Email:    {p ["email"]}\n  Password: {p ["password"]}\n  Country:  {self .country }  |  Domain: {self .domain }\n')
            return {'status':True ,'profile':p ,'cookies':cookies ,'domain':self .domain ,'registeredCountry':self .country }
        except KeyboardInterrupt :
            self .phone =None
            return {'status':False ,'message':'Cancelled by user'}
        except Exception as e :
            return self .retry (e ,retry )

    def init (self ,retry ):
        """Prepara correo, huella y sesion antes de empezar el flujo."""
        if not self .phone :
            emitStatus (self .on_status ,'Obteniendo un correo para la cuenta...')
            self .phone =self .mail .getAddr ()
            logger .info (f'Identifier acquired: {self .phone ["number"]}')
        else :
            logger .info (f'Identifier: {self .phone ["number"]}')
        self .profile =random .choice (browsers )
        ua =self .profile ['ua']
        self .mail .watch (self .phone ['number'],since =time .time ())
        if needsWaf (self .country ):
            self .wafFut =self .pool .submit (prefetchWaf ,self .base ,self .domain ,self .proxy ,ua ,self .country )
        self .fpFut =self .pool .submit (self .prepFp ,ua )
        self .sessFut =self .pool .submit (self .buildSess )

    def prepFp (self ,ua ):
        """Genera la huella FWCIM de la sesion."""
        cfg =countries [self .country ]
        FwcimClient .reset_session ()
        FwcimClient .set_session_timezone (cfg ['tz'])
        FwcimClient (location =self .base ,userAgent =ua ,timezone =cfg ['tz'])

    def buildSess (self ):
        """Construye la sesion HTTP en paralelo a la huella."""
        if self .fpFut :self .fpFut .result (timeout =30 )
        return buildSess (self .base ,self .domain ,self .proxy ,self .country ,profile =self .profile ,resetFp =False )

    def flow (self ):
        """Etapas del registro: alta del correo, OTP, captcha y telefono."""
        phone =self .phone ['number']
        short =self .phone ['normalizedNumber']
        user =self .user
        base =self .base
        code =countries [self .country ]['code']

        if self .wafFut :
            wait ([self .sessFut ,self .wafFut ])
            sess ,ua =self .sessFut .result ()
            wafRes =self .wafFut .result ()
        else :
            sess ,ua =self .sessFut .result (timeout =30 )
            wafRes =None
        self .sessFut =self .wafFut =None

        if needsWaf (self .country ):
            if wafRes and applyWaf (sess ,self .domain ,wafRes ):pass
            else :solveWaf (sess ,base ,self .domain ,self .proxy ,ua ,country =self .country )

        signinR ,arb ,csrf =visitSignin (sess ,base ,self .handle )
        dynUrls ,dynHashes =extractScripts (signinR .text )

        regPage ,claimUrl ,regParams =claimEmail (sess ,signinR ,arb ,csrf ,phone ,ua ,base ,self .handle ,dynUrls ,dynHashes )

        if needsWaf (self .country )and not hasWaf (sess ,self .domain ):
            solveWaf (sess ,base ,self .domain ,self .proxy ,ua ,country =self .country )

        emitStatus (self .on_status ,'Registrando tu correo con Amazon...')
        otpR =submitRegWithCaptcha (sess ,regPage ,claimUrl ,short ,user ,ua ,base ,dynUrls ,dynHashes ,code ,self .handle ,lean =True ,regParams =regParams ,status =self .on_status )

        if isAuth (sess ):
            logger .info ('Account authenticated directly — no OTP required!')
            return sess

        if '@'not in phone :
            otpR =switchToSms (sess ,otpR ,base )

        if self .country =='JP':
            otpR =selectEmailJP (sess ,otpR ,base )

        code2 =self .mail .wcode
        if code2 :
            logger .info (f'Code received: {code2 }')
        else :
            tout =120 if self .country =='JP'else 60
            # Los buzones IMAP (Gmail/dominio propio) pueden tardar mas que
            # el TempMail local en recibir el correo.
            tout =max (tout ,getattr (self .mail ,'waitTimeout',0 )or 0 )
            emitStatus (self .on_status ,'Amazon envio un codigo a tu correo. Esperando que llegue...')
            logger .info (f'Waiting for OTP at {phone }... (timeout: {tout }s)')
            try :
                code2 =self .mail .waitCode (self .phone ['activationId'],timeout =tout )
                if not code2 :raise RuntimeError ('OTP not received')
                logger .info (f'Code received: {code2 }')
            except Exception as e :
                logger .info (f'[OTP error] {e }')
                raise RegError ('email_timeout')

        emitStatus (self .on_status ,'Amazon envio el codigo a tu correo. Validando...')
        phoneBox ={}
        finalR =submitOtp (sess ,otpR ,code2 ,short ,user ,ua ,base ,dynUrls ,dynHashes ,regDomain =self .domain ,targetDomain =self .domain ,country =self .country ,status =self .on_status ,phone_out =phoneBox )
        self .cvfPhone =phoneBox .get ('number') or None
        # La pagina que queda tras validar el OTP es donde Amazon avisa que la
        # cuenta quedo suspendida. Antes se descartaba y la cuenta se entregaba
        # como creada; ahora el intento se marca como fallido.
        if finalR is not None :
            _ftxt =getattr (finalR ,'text','')or ''
            if detect_suspension (_ftxt ):
                logger .warning ('Amazon devolvio la pagina de cuenta suspendida (%s)',suspension_reason (_ftxt ))
                raise RegError ('account_suspended')
        return sess

    def retry (self ,err ,n ):
        """Decide si el intento se reintenta y con que cambios."""
        msg =str (err )
        if self .deadline and time .time ()>self .deadline :
            logger .warning ('Se agoto el tiempo total de la creacion; no se reintenta mas')
            ph =self .phone or {}
            prof ={'name':getattr (self .user ,'name',''),'email':ph .get ('number',''),'password':getattr (self .user ,'password','')}
            return {'status':False ,'message':'timeout_total' ,'profile':prof ,'domain':self .domain ,'registeredCountry':self .country ,'attempted':True }
        if 'phone_required'in msg or 'sms_not_configured'in msg :
            ph =self .phone or {}
            prof ={'name':getattr (self .user ,'name',''),'email':ph .get ('number',''),'password':getattr (self .user ,'password','')}
            logger .warning ('Amazon requires a mobile phone — stopping retries')
            return {'status':False ,'message':msg ,'profile':prof ,'domain':self .domain ,'registeredCountry':self .country ,'attempted':True }
        if 'account_suspended'in msg :
            # Amazon creo la cuenta pero la suspendio de inmediato. No se
            # reintenta con identidad nueva: la cuenta existe y nace
            # suspendida, asi que se reporta el motivo y se guardan las
            # credenciales.
            ph =self .phone or {}
            prof ={'name':getattr (self .user ,'name',''),'email':ph .get ('number',''),'password':getattr (self .user ,'password','')}
            logger .warning ('La cuenta fue suspendida por Amazon — no se reintenta')
            return {'status':False ,'message':'account_suspended' ,'profile':prof ,'domain':self .domain ,'registeredCountry':self .country ,'attempted':True ,'warning':'suspended'}
        if any (t in msg for t in self .burned ):self .phone =None
        if n >=self .maxRetries :
            prof ={}
            try :
                ph =self .phone or {}
                prof ={'name':getattr (self .user ,'name',''),'email':ph .get ('number',''),'password':getattr (self .user ,'password','')}
            except Exception :
                pass
            return {'status':False ,'message':msg ,'profile':prof ,'domain':self .domain ,'registeredCountry':self .country ,'attempted':True }
        # Rotar la sesion de proxy en cada reintento para cambiar IP/huella.
        self .proxy =rotateProxySession (self .proxy )
        if 'unusual activity'in msg :
            self .user =fakeProfile (self .country )
            logger .info (f'Unusual activity — new identity + proxy... ({n +1 }/{self .maxRetries })')
        elif 'email_associated'in msg :
            logger .info (f'Email already associated — new address + proxy... ({n +1 }/{self .maxRetries })')
        elif 'email_timeout'in msg :
            logger .info (f'OTP timeout — new address + proxy... ({n +1 }/{self .maxRetries })')
        elif 'otp_failed'in msg :
            self .phone =None ;self .user =fakeProfile (self .country )
            logger .info (f'OTP rejected — new email + identity + proxy... ({n +1 }/{self .maxRetries })')
        elif 'unexpected_response'in msg :
            logger .info (f'Unexpected Amazon response — new address + proxy... ({n +1 }/{self .maxRetries })')
        elif 'captcha_appeared'in msg :
            logger .info (f'Captcha — retrying fresh + proxy... ({n +1 }/{self .maxRetries })')
        else :
            logger .info (f'Retrying... ({n +1 }/{self .maxRetries }) — {msg }')
        time .sleep (1 )
        return self .run (n +1 )

    def close (self ):
        if getattr (self ,'pool',None ):
            self .pool .shutdown (wait =True ,cancel_futures =True )
            self .pool =None
