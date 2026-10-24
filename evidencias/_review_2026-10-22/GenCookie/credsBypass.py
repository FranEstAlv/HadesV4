"""Buzon de correo para el generador: temporal (local) o IMAP propio.

Habla con el servicio TempMail (EmailApi) por HTTP:
  - POST /odisea/mailbox           registra un buzon IMAP existente
  - GET  /odisea/generate           crea un buzon temporal
  - GET  /odisea/messages/<token>   lista los mensajes y su codigo

Ademas trae el cifrado de la contrasena del registro con las llaves publicas
de CSE de Amazon (encryptPassw).
"""
import logging
import os ,re ,random ,threading ,time
import base64 ,struct ,requests
from requests .exceptions import HTTPError
from cryptography .hazmat .backends import default_backend
from cryptography .hazmat .primitives import hashes
from cryptography .hazmat .primitives .asymmetric import padding as rsaPad
from cryptography .hazmat .primitives .asymmetric .rsa import RSAPublicNumbers
from cryptography .hazmat .primitives .ciphers .aead import AESGCM

logger = logging .getLogger (__name__)



def b64int (v ):
    """Convierte un valor base64url en entero."""
    return int .from_bytes (base64 .urlsafe_b64decode (v +'='*(-len (v )%4 )),'big')

def rsaPub (n ,e ='AQAB'):
    """Arma la llave publica RSA a partir del modulo y el exponente."""
    return RSAPublicNumbers (b64int (e ),b64int (n )).public_key (default_backend ())

def tlv (data ):
    """Empaqueta un dato en formato TLV (Amazon lo usa en el frame cifrado)."""
    return struct .pack ('>H',len (data ))+data

def encryptPassw (passw ,jwkN ,keyId ,token ,provider ='si:md5'):
    """Cifra la contrasena con el formato KMS que usa Amazon (AWSKMSEncryptionClient)."""
    pub =rsaPub (jwkN )
    mid =os .urandom (16 )
    dkey =AESGCM .generate_key (bit_length =128 )
    ctx =struct .pack ('>H',1 )+tlv (b'appActionToken')+tlv (token .encode ())
    wkey =pub .encrypt (dkey ,rsaPad .OAEP (mgf =rsaPad .MGF1 (hashes .SHA256 ()),algorithm =hashes .SHA256 (),label =None ))
    kps =struct .pack ('>H',1 )+tlv (provider .encode ())+tlv (keyId .encode ())+tlv (wkey )
    hdr =bytes ([1 ,128 ])+struct .pack ('>H',20 )+mid +struct .pack ('>H',len (ctx ))+ctx +kps +bytes ([2 ])+b'\x00\x00\x00\x00'+bytes ([12 ])+struct .pack ('>I',16 )
    gcm =AESGCM (dkey )
    htag =gcm .encrypt (b'\x00'*12 ,b'',hdr )
    seq =1
    iv =seq .to_bytes (12 ,'big')
    pt =passw .encode ()
    aad =mid +b'AWSKMSEncryptionClient Final Frame'+struct .pack ('>I',seq )+struct .pack ('>Q',len (pt ))
    ct =gcm .encrypt (iv ,pt ,aad )
    frame =b'\xff\xff\xff\xff'+struct .pack ('>I',seq )+iv +struct .pack ('>I',len (pt ))+ct
    return base64 .b64encode (hdr +b'\x00'*12 +htag +frame ).decode ('ascii')



_GMAIL_DOMAINS =('gmail.com','googlemail.com')


def _api_url ():
    """URL del servicio TempMail (EmailApi)."""
    return os .getenv ('TEMPMAIL_API','http://127.0.0.1:3000').rstrip ('/')


def _imap_config ():
    """Credenciales IMAP desde el entorno (.env)."""
    port =os .getenv ('IMAP_PORT','').strip ()
    return {
    'imap_email':os .getenv ('IMAP_EMAIL','').strip (),
    'imap_password':os .getenv ('IMAP_PASSWORD','').strip (),
    'imap_host':os .getenv ('IMAP_HOST','').strip ()or None ,
    'imap_port':int (port )if port .isdigit ()else None ,
    'imap_alias':os .getenv ('IMAP_ALIAS','').strip ()or None ,
    'mark_seen':os .getenv ('IMAP_MARK_SEEN','0').strip ().lower ()in ('1','true','yes','si'),
    }


def _con_puntos (local ,rng ):
    """Inserta puntos aleatorios en la parte local (sin puntos consecutivos)."""
    gaps =len (local )-1
    if gaps <1 :return local
    mask =[rng .random ()<0.45 for _ in range (gaps )]
    if not any (mask ):mask [rng .randrange (gaps )]=True
    return ''.join (ch +('.'if i <gaps and mask [i ]else '')for i ,ch in enumerate (local ))


class TempMail :
    """Buzon de correo: temporal (local) o IMAP propio.

    watch() lanza un hilo que consulta los mensajes y guarda el codigo en
    wcode; waitCode() es la forma sincrona de esperarlo.
    """
    def __init__ (self ,imap_email =None ,imap_password =None ,imap_host =None ,
    imap_port =None ,imap_alias =None ,mark_seen =None ,**_ ):
        self .address =None
        self .token =None
        self .wcode =None
        self .seen =set ()
        self .since =0.0
        self .ev =threading .Event ()
        self .stopper =threading .Event ()
        self .thread =None
        cfg =_imap_config ()
        self .imap_email =imap_email or cfg ['imap_email']or None
        self .imap_password =imap_password or cfg ['imap_password']or None
        self .imap_host =imap_host or cfg ['imap_host']
        self .imap_port =imap_port if imap_port is not None else cfg ['imap_port']
        self .imap_alias =imap_alias or cfg ['imap_alias']
        self .mark_seen =bool (mark_seen )if mark_seen is not None else cfg ['mark_seen']
        dominio =(self .imap_email or '').partition ('@')[2 ].lower ()
        # Gmail ignora los puntos: se usan para enmascarar y rotar el correo.
        self .dot_mask =bool (self .imap_email )and dominio in _GMAIL_DOMAINS
        self ._rng =random .Random ()
        self ._dots_usados =set ()
        # Un buzon IMAP suele tardar mas en recibir el correo que el TempMail local.
        self .waitTimeout =90 if self .imap_email else 0

    def useMailbox (self ,email ,password ,imap_host =None ,imap_port =None ,
    alias =None ,mark_seen =False ):
        """Usa un buzon IMAP concreto en vez del buzon temporal."""
        self .imap_email =email
        self .imap_password =password
        self .imap_host =imap_host
        self .imap_port =imap_port
        self .imap_alias =alias
        self .mark_seen =mark_seen
        dominio =email .partition ('@')[2 ].lower ()
        self .dot_mask =dominio in _GMAIL_DOMAINS
        self ._dots_usados =set ()
        self .waitTimeout =90
        return self

    def _masked_email (self ):
        """Siguiente variante con puntos del correo (solo Gmail)."""
        if not self .imap_email or not self .dot_mask :return self .imap_email
        local ,_ ,dom =self .imap_email .partition ('@')
        base ,plus ,tag =local .partition ('+')
        if len (base )<2 :return self .imap_email
        candidato =None
        for _ in range (200 ):
            cand =_con_puntos (base ,self ._rng )
            if cand not in self ._dots_usados :
                candidato =cand ;break
        if candidato is None :
            n =len (self ._dots_usados )%(len (base )-1 )+1
            candidato =f'{base [:n ]}.{base [n :]}'
        self ._dots_usados .add (candidato )
        return f'{candidato }{plus }{tag }@{dom }'

    def getAddr (self ):
        """Pide una direccion de correo y devuelve sus datos (activationId, number, domain)."""
        apiUrl =_api_url ()
        if self .imap_email :
            direccion =self ._masked_email ()
            payload ={'email':direccion ,'password':self .imap_password ,
            'mark_seen':bool (self .mark_seen )}
            if self .imap_host :payload ['imap_host']=self .imap_host
            if self .imap_port :payload ['imap_port']=int (self .imap_port )
            if self .imap_alias :payload ['alias']=self .imap_alias
            r =requests .post (f'{apiUrl }/odisea/mailbox',json =payload ,timeout =30 )
        else :
            r =requests .get (f'{apiUrl }/odisea/generate',timeout =10 )
        r .raise_for_status ()
        d =r .json ()
        logger .info (f'[mail] {d }')
        self .address =d ['email']
        self .token =d ['token']
        return {'activationId':self .address ,'number':self .address ,
        'normalizedNumber':self .address ,'cost':0.0 ,
        'domain':self .address .split ('@')[-1 ]}

    def watch (self ,addr ,since =None ):
        """Empieza a consultar el buzon en segundo plano desde la marca de tiempo dada."""
        self .stop ()
        self .waddr =addr .lower ()
        self .wcode =None
        self .since =since or time .time ()
        self .seen .clear ()
        self .ev .clear ();self .stopper .clear ()
        self .thread =threading .Thread (target =self .loop ,daemon =True )
        self .thread .start ()

    def stop (self ):
        """Detiene el hilo que consulta el buzon."""
        self .stopper .set ();self .ev .set ()
        if self .thread and self .thread .is_alive ():
            self .thread .join (timeout =2 )
        self .thread =None

    def waitCode (self ,activationId ,timeout =None ,since =None ):
        """Espera el codigo de verificacion hasta el tiempo limite."""
        fast =os .getenv ('FAST_MODE','1')=='1'
        base =float (os .getenv ('TEMPMAIL_POLL_INTERVAL','0.5'if fast else '1.0'))
        interval =base
        maxInterval =max (base ,5.0 )
        deadline =time .time ()+(timeout or 120 )
        while time .time ()<deadline :
            if self .wcode :return self .wcode
            code =self .poll ()
            if code :return code
            interval =min (interval +1.0 ,maxInterval )
            rem =deadline -time .time ()
            if rem <=0 :break
            time .sleep (min (interval ,rem ))
        if not self .wcode :
            try :self .stop ()
            except Exception :pass
        return self .wcode or self .poll ()

    def markOk (self ,address =None ):pass
    def ban (self ,address =None ):pass
    def cancel (self ,activationId ):self .stop ()
    def finish (self ,activationId ):self .stop ()
    def balance (self ):return None

    def loop (self ):
        """Hilo que consulta el buzon y guarda el codigo en wcode."""
        fast =os .getenv ('FAST_MODE','1')=='1'
        base =float (os .getenv ('TEMPMAIL_POLL_INTERVAL','0.5'if fast else '1.0'))
        interval =base
        maxInterval =max (base ,5.0 )
        while not self .stopper .is_set ():
            try :
                code =self .poll ()
                if code :
                    self .wcode =code
                    self .ev .set ()
                    return
                interval =min (interval +1.0 ,maxInterval )
            except Exception :pass
            if self .stopper .wait (interval ):return

    def poll (self ):
        """Consulta los mensajes y devuelve el codigo si hay uno nuevo."""
        if not self .token :return None
        apiUrl =_api_url ()
        try :
            r =requests .get (f'{apiUrl }/odisea/messages/{self .token }',timeout =8 )
            r .raise_for_status ()
            data =r .json ()
            msgs =data .get ('messages',[])if isinstance (data ,dict )else data
        except HTTPError as e :
            if e .response is not None and e .response .status_code in (404 ,410 ):
                logger .warning ('[mail] token invalido o expirado, deteniendo polling')
                self .stop ()
            else :
                logger .info (f'[mail] poll err: {e }')
            return None
        except Exception as e :
            logger .info (f'[mail] poll err: {e }');return None
        for msg in reversed (msgs ):
            ts =msg .get ('received_at')or ''
            if ts and self .since :
                # Solo cuenta el codigo recibido durante este intento: si se
                # reintenta con el mismo correo, el codigo viejo ya no sirve.
                try :
                    from datetime import datetime as _dt
                    when =_dt .fromisoformat (ts .replace ('Z','+00:00')).timestamp ()
                except Exception :
                    when =0
                if when and when <self .since -15 :continue
            mid =msg .get ('id')or (f'{ts }|{msg .get ("subject","") }|{msg .get ("from","") }')
            if mid in self .seen :continue
            code =TempMail .parseCode (msg )
            if code :
                self .seen .add (mid )
                logger .info (f'[mail] code: {code }')
                return code
        return None

    @staticmethod
    def parseCode (msg ):
        """Saca el codigo del mensaje (usa el campo otp o busca en asunto y cuerpo)."""
        otp =msg .get ('otp')
        if otp :return str (otp )
        subj =msg .get ('subject','')or ''
        body =msg .get ('body','')or msg .get ('text','')or msg .get ('html','')or ''
        for txt in (subj ,body ):
            c =TempMail .extract (txt )
            if c :return c
        return None

    @staticmethod
    def extract (txt ):
        """Busca un codigo de 6 digitos en un texto."""
        if not txt :return None
        pats =[
        r'(?:password monouso|one[- ]time password|OTP\)?)[^\d]{0,80}(\d{6})',
        r'(?:codice|code|c.digo|verification code)[^\d]{0,40}(\d{6})',
        r'[：:]\s*(\d{6})\b',
        r'(?:確認コード|認証コード|ワンタイムパスワード|コード)[^\d]{0,20}(\d{6})',
        r'(\d{6})[^\d]{0,40}(?:codice|code|c.digo|verifica)',
        ]
        for p in pats :
            m =re .search (p ,txt ,re .I )
            if m :return m .group (1 )
        m =re .search (r'\b(\d{6})\b',txt )
        return m .group (1 )if m else None
