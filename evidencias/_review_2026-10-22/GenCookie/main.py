"""CLI de GenCookie: crea cuentas Amazon y guarda las cookies.

Uso rapido:
    python main.py --country US --count 1

El menu interactivo (sin argumentos) pregunta pais, cantidad de cuentas y si
se usa un correo propio. Todo el detalle del flujo queda en
logs/gencookie.log; en pantalla solo se ve el avance y el resultado final.
"""
import os ,sys ,re ,json ,time ,random ,threading ,itertools ,shutil ,argparse ,logging
from pathlib import Path
from datetime import datetime

try :
    from dotenv import load_dotenv
    load_dotenv (Path (__file__ ).resolve ().parent /'.env')
except Exception :
    pass

from accountCreator import Creator ,countries
from credsBypass import TempMail

os .environ .setdefault ('TEMPMAIL_API','http://127.0.0.1:3000')


available =['US','CA','MX','IT','ES','BR']

# Proxies por variable de entorno (mismos nombres que el .env del bot):
#   PROXY_STICKY_MX            pool sticky para México
#   PROXY_ROTATORY             pool rotatorio
#   PROXY_STICKY_USA_3_MINUTES pool sticky de USA (se suma al rotatorio)
#   PROXY_<PAIS>               proxy directa para un país (ej. PROXY_ES)
#   PROXY_TEMPLATE             plantilla con {region} y {country}
def _parseProxies (*names ):
    """Lee una lista de proxies separada por comas de una variable del .env."""
    out =[]
    for name in names :
        for item in os .getenv (name ,'').split (','):
            item =item .strip ()
            if item :out .append (item )
    return out

MX_PROXIES =_parseProxies ('PROXY_STICKY_MX')
ROT_PROXIES =_parseProxies ('PROXY_ROTATORY','PROXY_STICKY_USA_3_MINUTES')
_REGION ={'US':'us','CA':'us','MX':'us','BR':'us','IT':'eu','ES':'eu'}

def _template (region ):
    """Arma la proxy con la plantilla PROXY_TEMPLATE."""
    tpl =os .getenv ('PROXY_TEMPLATE','').strip ()
    return tpl .replace ('{region}',region ).replace ('{country}',region )if tpl else None

def _mkproxy (country ):
    """Elige la proxy del pais: directa, del pool o de la plantilla."""
    direct =os .getenv (f'PROXY_{country }','').strip ()
    if direct :return direct
    pool =MX_PROXIES if country =='MX'else ROT_PROXIES
    if not pool :pool =ROT_PROXIES or MX_PROXIES
    if pool :return random .choice (pool )
    return _template (_REGION .get (country ,'us'))

proxies ={c :_mkproxy (c )for c in available }
if not any (proxies .values ()):
    print ('Aviso: no hay proxies configuradas. Usa PROXY_STICKY_MX / PROXY_ROTATORY '
    'o PROXY_TEMPLATE en el archivo .env.')

def _setupLogging (verbose =False ):
    """Deja el log en logs/gencookie.log (y en pantalla con --verbose)."""
    logdir =Path (__file__ ).resolve ().parent /'logs'
    try :logdir .mkdir (parents =True ,exist_ok =True )
    except Exception :pass
    handlers =[logging .FileHandler (logdir /'gencookie.log',encoding ='utf-8')]
    if verbose :handlers .append (logging .StreamHandler (sys .stderr ))
    logging .basicConfig (level =logging .INFO ,
    format ='%(asctime)s %(levelname)s %(name)s: %(message)s',handlers =handlers )



def fg (n ):
    """Codigo de color ANSI de 256 colores."""
    return f'\033[38;5;{n }m'
R ='\033[0m';B ='\033[1m';DIM ='\033[2m';IT ='\033[3m'
WHT =fg (255 );MUT =fg (245 );DK =fg (239 );DKER =fg (236 )
CY =fg (81 );GRN =fg (114 );RED =fg (203 );AMB =fg (215 );PNK =fg (205 )
ACC =fg (75 )
TTY =sys .stdout
_awaiting_input =threading .Event ()

def vlen (s ):
    """Largo visible de un texto con colores ANSI."""
    return len (re .sub (r'\033\[[0-9;]*m','',s ))

def pad (txt ,w ,fill =' '):
    """Centra un texto en un ancho dado."""
    vl =vlen (txt );lp =max (0 ,(w -vl )//2 );rp =max (0 ,w -vl -lp )
    return fill *lp +txt +fill *rp

def clr ():
    """Limpia la pantalla."""
    sys .stdout .write ('\033[2J\033[H');sys .stdout .flush ()



class ManualMail :
    """Buzon manual: el usuario escribe el codigo en la terminal."""
    def __init__ (self ,addr ):
        """Prepara el objeto con el estado que se muestra en pantalla."""
        self .address =addr ;self .token ='manual';self .wcode =None
    def getAddr (self ):
        """Devuelve la direccion escrita a mano por el usuario."""
        return {'activationId':self .address ,'number':self .address ,'normalizedNumber':self .address ,'cost':0.0 ,'domain':self .address .split ('@')[-1 ]}
    def waitCode (self ,aid ,timeout =None ,since =None ):
        """Espera a que el usuario escriba el codigo en la terminal."""
        _awaiting_input .set ()
        try :
            TTY .write (f'\r\033[2K\n  {CY }OTP ›{R } ')
            TTY .flush ()
            line =sys .__stdin__ .readline ()
            return line .strip ()if line else ''
        except (KeyboardInterrupt ,EOFError ):return ''
        finally :_awaiting_input .clear ()
    def watch (self ,*a ,**kw ):
        """No hace nada: el codigo se escribe a mano."""
        pass
    def stop (self ):
        """Detiene el spinner y deja el mensaje final."""
        pass
    def markOk (self ,*a ):
        """No hace nada: el codigo se escribe a mano."""
        pass
    def ban (self ,*a ):
        """No hace nada: el codigo se escribe a mano."""
        pass
    def cancel (self ,*a ):
        """No hace nada: el codigo se escribe a mano."""
        pass
    def finish (self ,*a ):
        """No hace nada: el codigo se escribe a mano."""
        pass
    def balance (self ):
        """No aplica: el buzon manual no tiene saldo."""
        return None


# Errores del generador traducidos a lenguaje claro. El codigo tecnico se
# conserva aparte para diagnostico.
_ERRORS =(
 ('account_suspended','Amazon creó la cuenta pero la suspendió de inmediato. Ese registro no es usable.'),
 ('unexpected_response','Amazon respondió algo inesperado. Es posible que la cuenta sí se haya creado: intenta entrar con ese correo.'),
 ('address_failed','La cuenta se creó pero no se pudo agregar la dirección de entrega.'),
 ('email_timeout','No llegó el código de verificación a tiempo. El correo temporal pudo vencer.'),
 ('OTP not received','No llegó el código de verificación a tiempo. El correo temporal pudo vencer.'),
 ('otp_failed','Amazon rechazó el código de verificación. Esa dirección ya pudo quedar registrada.'),
 ('email_associated','Ese correo ya está registrado en Amazon. Usa otra dirección.'),
 ('number_associated','Ese correo o teléfono ya está asociado a una cuenta Amazon. Usa otro.'),
 ('unusual activity','Amazon marcó actividad inusual y bloqueó el intento. Espera unos minutos y prueba de nuevo.'),
 ('captcha_appeared','Amazon pidió una verificación extra (captcha) y no se pudo continuar. Intenta de nuevo más tarde.'),
 ('sms_not_configured','El servicio de números por SMS no está configurado (falta HEROSMS_API_KEY en el .env).'),
 ('phone_required','Amazon pide un número de celular para terminar el registro. Configura el servicio de SMS.'),
 ('phone_submit_rejected','Amazon no aceptó el número de celular. Intenta de nuevo más tarde.'),
 ('phone_submit_failed','No se pudo enviar el número de celular a Amazon. Intenta de nuevo más tarde.'),
 ('phone_form_missing','Amazon cambió la pantalla de verificación y no se pudo completar el registro.'),
 ('sms_timeout','No llegó el código por SMS a tiempo. El número quedó sin usar.'),
 ('sms_code_rejected','Amazon no aceptó el código que llegó por SMS. Intenta de nuevo.'),
 ('whatsapp_channel','Amazon pidió el código por WhatsApp, que con un número virtual no es posible.'),
 ('timeout_total','Amazon tardó demasiado en responder y se canceló el intento.'),
 ('Cancelled by user','Cancelado por el usuario.'),
 )

def _humanize (message ):
    """Traduce el codigo de error del generador a un mensaje claro."""
    low =(message or '').lower ()
    for key ,friendly in _ERRORS :
        if key .lower ()in low :return friendly
    return message or 'Error desconocido'


class Status :
    """Spinner del CLI: dibuja el paso actual del flujo."""
    frames =['⣾','⣽','⣻','⢿','⡿','⣟','⣯','⣷']

    def __init__ (self ,state ):
        """Prepara el objeto con el estado que se muestra en pantalla."""
        self .state =state
        self .ev =threading .Event ()
        self .th =None

    def start (self ):
        """Arranca el hilo que dibuja el spinner."""
        self .ev .clear ()
        self .th =threading .Thread (target =self ._loop ,daemon =True )
        self .th .start ()

    def _loop (self ):
        """Dibuja el spinner con el estado actual."""
        for f in itertools .cycle (self .frames ):
            if self .ev .is_set ():break
            if _awaiting_input .is_set ():
                time .sleep (0.1 );continue
            s =self .state
            msg =s .get ('step','Initializing')
            sub =''
            if s .get ('otp'):sub =f'OTP: {s ["otp"]}'
            elif s .get ('email'):sub =s ['email']
            line =f'  {ACC }{f }{R }  {WHT }{msg }{R }'
            if sub :line +=f'  {DK }·{R }  {MUT }{sub }{R }'
            TTY .write (f'\r\033[2K{line }')
            TTY .flush ()
            time .sleep (0.07 )

    def stop (self ,final ='' ,icon =''):
        """Detiene el spinner y deja el mensaje final."""
        self .ev .set ()
        if self .th :self .th .join (timeout =0.5 )
        TTY .write ('\r\033[2K')
        if final :
            TTY .write (f'  {icon }  {final }\n')
        TTY .flush ()



def save (result ,folder ='cookies' ):
    """Guarda el resultado en cookies/ (o en attempts/ si el intento fallo)."""
    base =Path (__file__ ).parent /folder
    base .mkdir (exist_ok =True )
    ts =datetime .now ().strftime ('%Y%m%d_%H%M%S')
    p =result .get ('profile',{})
    cc =result .get ('registeredCountry','XX')
    ok =bool (result .get ('status'))
    suffix ='' if ok else '_failed'
    fname =f'{cc }_{ts }{suffix }.json'
    data ={
    'created':datetime .now ().isoformat (),
    'country':cc ,
    'domain':result .get ('domain',''),
    'email':p .get ('email',''),
    'password':p .get ('password',''),
    'name':p .get ('name',''),
    'cookies':result .get ('cookies',''),
    'time':result .get ('time',0 ),
    'warning':result .get ('warning',''),
    'tech_message':result .get ('message',''),
    }
    (base /fname ).write_text (json .dumps (data ,indent =2 ,ensure_ascii =False ),encoding ='utf-8')
    return base /fname



def create (country ,mail =None ,timeout =600 ):
    """Crea una cuenta y devuelve el resultado con el tiempo que tardo."""
    state ={'step':'Starting...','email':'','otp':''}
    status =Status (state )

    t0 =time .time ()
    box =[None ]
    err =[None ]

    creator =Creator (country =country ,proxy =proxies .get (country ),mail =mail )
    creator .deadline =t0 +timeout
    creator .maxRetries =2

    def _status (text ):
        """Actualiza el paso que muestra el spinner."""
        state ['step']=text
        try :
            ph =creator .phone
            if ph and ph .get ('number'):state ['email']=ph ['number']
            code =getattr (creator .mail ,'wcode',None)
            if code :state ['otp']=code
        except Exception :
            pass

    creator .on_status =_status

    def run ():
        """Ejecuta el generador en un hilo aparte."""
        try :
            box [0 ]=creator .run ()
        except Exception as e :
            err [0 ]=e
        finally :
            try :creator .close ()
            except Exception :pass

    status .start ()
    th =threading .Thread (target =run ,daemon =True )
    th .start ()
    th .join ()

    elapsed =round (time .time ()-t0 ,1 )

    if err [0 ]:
        status .stop (f'{RED }Error: {err [0 ]}{R }',f'{RED }✗{R }')
        return {'status':False ,'message':str (err [0 ]),'time':elapsed }

    r =box [0 ]
    r ['time']=elapsed

    if r .get ('status'):
        status .stop (f'{GRN }Account created{R }  {DK }·{R }  {MUT }{elapsed }s{R }',f'{GRN }✓{R }')
    else :
        status .stop (f'{RED }{_humanize (r .get ("message","Failed"))}{R }',f'{RED }✗{R }')

    return r



def showResult (r ):
    """Muestra el resultado en la consola."""
    tw =shutil .get_terminal_size ((80 ,24 )).columns
    w =min (tw -6 ,52 )
    sep =DK +'─'*w +R
    print ()
    if r .get ('status'):
        p =r .get ('profile',{})
        print (f'  {sep }')
        print (f'  {GRN }{B }✓  Account Created{R }')
        print (f'  {sep }')
        for lbl ,val in [('Email',p .get ('email','')),('Password',p .get ('password','')),('Country',r .get ('registeredCountry','')),('Domain',r .get ('domain','')),('Time',f'{r .get ("time",0 )}s')]:
            print (f'  {MUT }{lbl :<10}{R }  {WHT }{val }{R }')
        if r .get ('warning')=='address_failed':
            print (f'  {AMB }!  Sin direccion de entrega (las credenciales si sirven){R }')
        fp =save (r )
        print (f'  {sep }')
        print (f'  {DIM }saved → {fp .name }{R }')
    else :
        print (f'  {sep }')
        print (f'  {RED }{B }✗  Failed{R }  {MUT }{_humanize (r .get ("message",""))}{R }')
        if r .get ('message'):
            print (f'  {DKER }detalle: {r .get ("message")}{R }')
        p =r .get ('profile')or {}
        if p .get ('email'):
            fp =save (r ,folder ='attempts')
            print (f'  {MUT }credenciales guardadas → {fp .name }{R }')
        print (f'  {sep }')
    print ()



def banner ():
    """Dibuja el banner de inicio."""
    clr ()
    tw =shutil .get_terminal_size ((80 ,24 )).columns
    top =[63 ,69 ,75 ,81 ,87 ,123 ,159 ,195 ,159 ,123 ,87 ,81 ,75 ,69 ]
    mid =[93 ,99 ,105 ,111 ,117 ,153 ,189 ,195 ,189 ,153 ,117 ,111 ,105 ,99 ]
    bot =[60 ,66 ,72 ,78 ,84 ,120 ,156 ,192 ,156 ,120 ,84 ,78 ,72 ,66 ]
    art =[
    '  ▄████ ▓█████  ███▄    █     ▄████▄   ▒█████   ▒█████   ██ ▄█▀ ██▓▓█████',
    ' ██▒ ▀█▒▓█   ▀  ██ ▀█   █    ▒██▀ ▀█  ▒██▒  ██▒▒██▒  ██▒ ██▄█▒ ▓██▒▓█   ▀',
    '▒██░▄▄▄░▒███   ▓██  ▀█ ██▒   ▒▓█    ▄ ▒██░  ██▒▒██░  ██▒▓███▄░ ▒██▒▒███',
    '░▓█  ██▓▒▓█  ▄ ▓██▒  ▐▌██▒   ▒▓▓▄ ▄██▒▒██   ██░▒██   ██░▓██ █▄ ░██░▒▓█  ▄',
    '░▒▓███▀▒░▒████▒▒██░   ▓██░   ▒ ▓███▀ ░░ ████▓▒░░ ████▓▒░▒██▒ █▄░██░░▒████▒',
    ' ░▒   ▒ ░░ ▒░ ░░ ▒░   ▒ ▒    ░ ░▒ ▒  ░░ ▒░▒░▒░ ░ ▒░▒░▒░ ▒ ▒▒ ▓▒░▓  ░░ ▒░ ░',
    '  ░   ░  ░ ░  ░░ ░░   ░ ▒░     ░  ▒     ░ ▒ ▒░   ░ ▒ ▒░ ░ ░▒ ▒░ ▒ ░ ░ ░  ░',
    '░ ░   ░    ░      ░   ░ ░    ░        ░ ░ ░ ▒  ░ ░ ░ ▒  ░ ░░ ░  ▒ ░   ░',
    '      ░    ░  ░         ░    ░ ░          ░ ░      ░ ░  ░  ░    ░     ░  ░',
    '                             ░',
    ]
    pals =[top ]*3 +[mid ]*4 +[bot ]*3
    maxw =max (len (r )for r in art )
    lp =max (0 ,(tw -maxw )//2 )
    print ()
    for i ,row in enumerate (art ):
        p =pals [i %len (pals )]
        colored =''
        ci =0
        for ch in row :
            if ch !=' ':
                colored +=fg (p [ci %len (p )])+ch +R
                ci +=1
            else :
                colored +=' '
        print (' '*lp +colored )
    print ()
    credit =f'{DK }GenCookie corregido y actualizado por {R }{fg (205 )}@MrMxyzptlk04{R }'
    clp =max (0 ,(tw -16 )//2 )
    print (' '*clp +credit )
    print ()



def ask (prompt ,default =''):
    """Pide un dato al usuario y devuelve el valor por defecto."""
    try :v =input (f'  {DK }›{R }  {prompt } ').strip ()
    except (KeyboardInterrupt ,EOFError ):return ''
    return v or default

def pickCountry ():
    """Menú para elegir el pais de la cuenta."""
    tw =shutil .get_terminal_size ((80 ,24 )).columns
    for i ,c in enumerate (available ,1 ):
        dom =countries [c ]['domain']
        print (f'  {DK }[{MUT }{i }{DK }]{R }  {WHT }{c }{R }  {DK }{dom }{R }')
    print (f'  {DK }[{MUT }0{DK }]{R }  {MUT }Exit{R }')
    print ()
    raw =ask (f'{WHT }Country{R }','0')
    if raw =='0':return None
    if raw .isdigit ():
        idx =int (raw )-1
        if 0 <=idx <len (available ):return available [idx ]
    elif raw .upper ()in available :
        return raw .upper ()
    return None

def menu (imap =None ,country =None ,count =None ,timeout =600 ):
    """Menu principal: pais, cantidad de cuentas y buzon de correo."""
    banner ()
    interactive =(country is None or count is None)
    if country is None :country =pickCountry ()
    if not country :
        print (f'\n  {MUT }Bye.{R }\n');return

    if count is None :
        try :n =int (ask (f'{WHT }How many?{R }  {DK }[1]{R }','1'))
        except :n =1
    else :n =count

    mf =None
    if imap :
        # Una sola instancia rota las variantes con puntos entre cuentas.
        shared =TempMail (**imap )
        mf =(lambda :shared )
    else :
        probe =TempMail ()
        if probe .imap_email :
            # Credenciales IMAP tomadas del .env: no preguntar por correo.
            mf =(lambda :probe )
        elif interactive :
            man =ask (f'{WHT }Manual email?{R }  {DK }[y/N]{R }','n').lower ()
            if man in ('y','yes','s','si'):
                addr =ask (f'{WHT }Email{R }','')
                if addr :mf =(lambda a =addr :ManualMail (a ))

    print ()
    results =[]
    for i in range (n ):
        if n >1 :
            print (f'  {ACC }[{i +1 }/{n }]{R }')
        mail =mf ()if mf else None
        r =create (country ,mail =mail ,timeout =timeout )
        results .append (r )
        showResult (r )
        if n >1 and i <n -1 :time .sleep (1 )

    if n >1 :
        ok =sum (1 for r in results if r .get ('status'))
        print (f'  {GRN }✓ {ok }{R }  {RED }✗ {n -ok }{R }  of {n }')
        print ()

    print (f'  {MUT }Done.{R }\n')

if __name__ =='__main__':
    ap =argparse .ArgumentParser (description ='GenCookie: creador de cuentas Amazon.')
    ap .add_argument ('--country','-c',choices =available ,help ='Pais de la cuenta.')
    ap .add_argument ('--count','-n',type =int ,default =None ,help ='Cuantas cuentas crear.')
    ap .add_argument ('--imap-email',help ='Correo real (Gmail o dominio propio) para recibir el OTP.')
    ap .add_argument ('--imap-password',help ='Contrasena o contrasena de aplicacion del correo.')
    ap .add_argument ('--imap-host',help ='Servidor IMAP (por defecto se detecta por el dominio).')
    ap .add_argument ('--imap-port',type =int ,help ='Puerto IMAP (993 para SSL, 143 para STARTTLS).')
    ap .add_argument ('--imap-alias',help ='Alias o etiqueta del correo (ej. +amazon).')
    ap .add_argument ('--mark-seen',action ='store_true',help ='Marcar como leidos los correos consultados.')
    ap .add_argument ('--timeout',type =int ,default =600 ,help ='Segundos maximos por intento.')
    ap .add_argument ('--verbose','-v',action ='store_true',help ='Mostrar el log detallado en pantalla.')
    args =ap .parse_args ()
    _setupLogging (args .verbose )
    if not os .getenv ('HEROSMS_API_KEY','').strip ():
        print (f'  {AMB }!  Sin HEROSMS_API_KEY: si Amazon pide un numero de celular, el '
        f'intento se detiene.{R }')
    if not any (os .getenv (k ,'').strip ()for k in ('TWO_CAPTCHA_API_KEY','CAPSOLVER_API_KEY','ANTICAPTCHA_API_KEY')):
        print (f'  {AMB }!  Sin solver de captcha configurado (TWO_CAPTCHA_API_KEY / '
        f'CAPSOLVER_API_KEY / ANTICAPTCHA_API_KEY).{R }')
    imap =None
    if args .imap_email or args .imap_password :
        if not (args .imap_email and args .imap_password ):
            ap .error ('--imap-email y --imap-password deben usarse juntos.')
        imap ={'imap_email':args .imap_email ,'imap_password':args .imap_password ,
        'imap_host':args .imap_host ,'imap_port':args .imap_port ,
        'imap_alias':args .imap_alias ,'mark_seen':args .mark_seen }
    menu (imap =imap ,country =args .country ,count =args .count ,timeout =args .timeout )
