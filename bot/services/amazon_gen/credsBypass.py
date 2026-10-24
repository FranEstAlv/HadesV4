import logging
import os ,re ,random ,threading ,time
import base64 ,struct ,requests
from requests .exceptions import HTTPError
from cryptography .hazmat .backends import default_backend

logger = logging.getLogger(__name__)
from cryptography .hazmat .primitives import hashes
from cryptography .hazmat .primitives .asymmetric import padding as rsaPad
from cryptography .hazmat .primitives .asymmetric .rsa import RSAPublicNumbers
from cryptography .hazmat .primitives .ciphers .aead import AESGCM



def b64int (v ):
    return int .from_bytes (base64 .urlsafe_b64decode (v +'='*(-len (v )%4 )),'big')

def rsaPub (n ,e ='AQAB'):
    return RSAPublicNumbers (b64int (e ),b64int (n )).public_key (default_backend ())

def tlv (data ):
    return struct .pack ('>H',len (data ))+data

def encryptPassw (passw ,jwkN ,keyId ,token ,provider ='si:md5'):
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



def _api_url ():
    return os .getenv ('TEMPMAIL_API','http://127.0.0.1:3000').rstrip ('/')

class TempMail :
    def __init__ (self ,**_ ):
        self .address =None
        self .token =None
        self .wcode =None
        self .seen =set ()
        self .since =0.0
        self .ev =threading .Event ()
        self .stopper =threading .Event ()
        self .thread =None

    def getAddr (self ):
        apiUrl =_api_url ()
        r =requests .get (f'{apiUrl }/odisea/generate',timeout =10 )
        r .raise_for_status ()
        d =r .json ()
        logger.info (f'[mail] {d }')
        self .address =d ['email']
        self .token =d ['token']
        return {'activationId':self .address ,'number':self .address ,
        'normalizedNumber':self .address ,'cost':0.0 ,
        'domain':self .address .split ('@')[-1 ]}

    def watch (self ,addr ,since =None ):
        self .stop ()
        self .waddr =addr .lower ()
        self .wcode =None
        self .since =since or time .time ()
        self .ev .clear ();self .stopper .clear ()
        self .thread =threading .Thread (target =self .loop ,daemon =True )
        self .thread .start ()

    def stop (self ):
        self .stopper .set ();self .ev .set ()
        if self .thread and self .thread .is_alive ():
            self .thread .join (timeout =2 )
        self .thread =None

    def waitCode (self ,activationId ,timeout =None ,since =None ):
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
        if not self .token :return None
        apiUrl =_api_url ()
        try :
            r =requests .get (f'{apiUrl }/odisea/messages/{self .token }',timeout =8 )
            r .raise_for_status ()
            data =r .json ()
            msgs =data .get ('messages',[])if isinstance (data ,dict )else data
        except HTTPError as e :
            if e .response is not None and e .response .status_code ==404 :
                logger .warning (f'[mail] token invalido (404), deteniendo polling')
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
        subj =msg .get ('subject','')or ''
        body =msg .get ('body','')or msg .get ('text','')or msg .get ('html','')or ''
        for txt in (subj ,body ):
            c =TempMail .extract (txt )
            if c :return c
        return None

    @staticmethod
    def extract (txt ):
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
