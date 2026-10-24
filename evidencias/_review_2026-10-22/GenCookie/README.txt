GenCookie
=========

Creador de cuentas Amazon por línea de comandos.

GenCookie corregido y actualizado por @MrMxyzptlk04

Empieza por PRUEBA-RAPIDA.txt: ahí está, paso a paso, cómo levantar el
EmailApi y probar el generador.

Requisitos
----------
- Python 3.10 o superior
- Las dependencias de requirements.txt
- Un servicio TempMail (EmailApi) corriendo en TEMPMAIL_API
  (por defecto http://127.0.0.1:3000)

Instalación
-----------
    pip install -r requirements.txt
    cp .env.example .env
    # edita .env con tus proxies y tu correo

Los proxies, el correo IMAP y los dominios se configuran en el archivo
.env (se carga automáticamente al arrancar).

Uso
---
    python main.py

El menú interactivo permite elegir país, cantidad de cuentas y el correo
a usar. También se puede ejecutar sin menú:

    python main.py --country US --count 1
    python main.py --country MX --count 3 --imap-alias amazon
    python main.py --country US --count 1 --timeout 900 --verbose

Opciones:
    --timeout   segundos máximos por intento (por defecto 600)
    --verbose   muestra el log detallado en pantalla (además del archivo)

Las cuentas que se crean quedan en cookies/. Cuando el intento falla pero
Amazon sí llegó a registrar el correo, las credenciales se guardan en
attempts/ para recuperarlas a mano.

Log
---
Todo el detalle del flujo queda en logs/gencookie.log (el archivo se crea
solo). Con --verbose también se ve en pantalla.

Verificación de seguridad (captcha)
----------------------------------
Durante el registro Amazon puede pedir un reto Arc/Arkose (funCaptcha) o
AWS WAF. El script los resuelve solo si hay un solver configurado:

    TWO_CAPTCHA_API_KEY=    Arkose (FunCaptchaTask)
    CAPSOLVER_API_KEY=      AWS WAF (AntiAwsWafTask) y Arkose (FunCaptchaTask)
    ANTICAPTCHA_API_KEY=    Arkose como respaldo de 2Captcha

Se prueban en ese orden y el primero que entregue token gana. Sin ninguna
de estas claves, el intento se detiene en el captcha.

Número de celular por SMS
-------------------------
Amazon pide un número de celular para terminar el registro. El script
compra el número, lo envía en el formulario de verificación, espera el SMS
con el código y lo responde:

    HEROSMS_API_KEY=

Si no hay clave, el intento se detiene en ese paso. Los números que no
reciben el SMS se guardan en data/sms_bad_numbers.json y no se vuelven a
comprar (HeroSMS los recicla).

Correo por IMAP
---------------
Sin argumentos, el CLI toma el correo del .env (IMAP_EMAIL, IMAP_PASSWORD).
También se puede pasar por línea de comandos:

    python main.py --country US \
        --imap-email tu@gmail.com \
        --imap-password "xxxx xxxx xxxx xxxx" \
        --imap-alias amazon

- Gmail: usa una contraseña de aplicación de 16 caracteres. El correo se
  enmascara insertando puntos en la parte local y cada cuenta nueva (o
  reintento) rota a una variante distinta.
- Dominio propio: se detecta imap.<dominio>:993; se puede forzar con
  --imap-host y --imap-port.
- --imap-alias usa el formato usuario+alias@dominio y filtra solo los
  correos dirigidos a esa dirección.
- --mark-seen marca los correos consultados como leídos.

Proxy
-----
Sin proxy, las peticiones a Amazon salen directo y casi siempre fallan.
Configura los mismos nombres de variables que usa el bot:

    PROXY_STICKY_MX=            # pool sticky para México
    PROXY_ROTATORY=             # pool rotatorio
    PROXY_STICKY_USA_3_MINUTES= # pool sticky de USA
    PROXY_TEMPLATE=             # plantilla con {region} y {country}

Los pools aceptan varias proxies separadas por comas. También se puede
fijar una proxy por país, por ejemplo PROXY_ES. Si no hay ninguna
configurada, el CLI lo avisa al arrancar. Con PROXY_REQUIRED=1 se aborta
en vez de salir directo.

Dominios para el correo
-----------------------
- Los dominios de name.com funcionan mejor con esta API.
- Hay dominios económicos en Namecheap, name.com, Contabo, Spaceship y
  Hostinger.
- No compres dominios con descuento o promoción: normalmente vienen
  quemados o en listas negras y los correos no llegan bien.

Los dominios suelen soportar entre 40 y 80 cookies; conviene rotarlos
entre intervalos. La API ya elige uno al azar de tu lista.
