# GenCookie — Documento técnico de la creación de cuentas Amazon

> **Fecha de corte:** 2026-10-20 (hora de Ciudad de México; el VPS está en
> Europe/Berlin, UTC+2, 8 h adelantado).
> **Alcance:** todo lo que se cambió y todo lo que se observó del lado de Amazon
> durante la sesión que se perdió (08:00–12:02 del VPS) y su continuación.
> **Complementa** a `evidencias/retro-sesion-perdida-2026-10-20.md` (cronología e
> hallazgos); este documento es la **referencia técnica de implementación**.

---

## 0. Cómo leer este documento

| Si quieres… | Ve a… |
|---|---|
| Saber qué hace cada archivo y qué bloque va dónde | **§4 — Estructura del código** |
| Entender qué le pide Amazon exactamente y en qué orden | **§5 — El protocolo observado** |
| Saber qué servicio resuelve qué reto | **§6 — Solvers** |
| Ver todos los cambios de hoy con su porqué | **§7 — Registro de cambios** |
| Los textos que ve el usuario | **§8 — Estados y mensajes** |
| Variables de entorno | **§9 — Configuración** |
| Operar y diagnosticar | **§10 — Operación** |
| Lo que falta | **§11 — Pendientes y riesgos** |

---

## 1. Estado del sistema (verificado 2026-10-20)

| Componente | Estado | Nota |
|---|---|---|
| `gencookie-bot.service` | activo | aiogram 3.31, polling, `/gencookie` y `/cook` |
| `emailapi-local.service` | activo | SMTP 1025, API 3000, dominios propios |
| Creador de cuentas Amazon | **operativo** | 5 cuentas creadas hoy; último flujo: **41 s** |
| Solver PoW (local) | operativo | 5/5 retos resueltos, 1–5 s |
| Solver AWS WAF (CapSolver) | operativo | 3/3 retos resueltos, 6–7 s |
| Solver Arkose (2Captcha) | parcial | funciona con la cadena *proxyless → con proxy*; con una sola variante daba 0/7 |
| SMS (HeroSMS) | operativo con lista negra | el pool recicla números: se descartan los que fallaron |
| Anti-Captcha | **inactivo** | key presente pero **saldo $0** |

**Cuentas creadas hoy (todas MX, `data/amazon_accounts/`):**
`MX_20261020_155843` · `MX_20261020_163148` · `MX_20261020_175232` ·
`MX_20261020_180600` (+ una por CLI de diagnóstico).

---

## 2. Mapa del sistema

```
Telegram  ──►  bot/handlers/amazon_creator.py      (panel inline, estado FSM)
                    │
                    ▼
              bot/services/amazon_creator.py        (orquestación async: callbacks,
                    │                                timeouts, mensajes, persistencia)
                    ▼
   ┌────────────────┴─────────────────┐
   ▼                                  ▼
bot/services/amazon_gen/          EmailApi (FastAPI, puerto 3000)
 accountCreator.py  ← motor        ├─ GET  /odisea/generate      → correo temporal
  ├─ cvfSolver.py   ← retos        ├─ GET  /odisea/messages/{token}
  ├─ smsProvider.py ← SMS          └─ SMTP :1025 (aiosmtpd, dominios propios)
  ├─ credsBypass.py ← correo
  ├─ metaDataGen.py ← huella FWCIM
  ├─ wafSolver.py   ← WAF clásico
  └─ curl_session.py← sesión curl_cffi

Servicios externos:  HeroSMS (SMS) · CapSolver (AWS WAF) · 2Captcha (Arkose)
                     Anti-Captcha (Arkose, sin saldo) · IPRoyal (proxy por país)
```

**Flujo de datos de una creación** (`country=MX`, correo temporal):

```
1. handler  →  create_amazon_account()  →  TempMail.getAddr()        [correo propio]
2.          →  Creator.init()             →  proxy IPRoyal + huella FWCIM + sesión curl
3.          →  Creator.flow()
     3.1 visitSignin()        GET  /ap/signin
     3.2 claimEmail()         POST /ap/register (identificador = correo)
     3.3 submitRegWithCaptcha()   ──► si hay reto CVF → cvfSolver.solveCvf()
     3.4 espera OTP de correo (TempMail.poll)  ──► código de EmailApi
     3.5 submitOtp()          POST /ap/cvf/verify (code)
     3.6 collectPhone()       número virtual + paso de teléfono
     3.7 submitOtp()          POST /ap/cvf/verify (código SMS)
4.          →  AccountBuilder.addAddress()  → exportCookies()
5.          →  _save_cookie_file()  +  save_amazon_account_to_db()
```

---

## 3. Lo que Amazon cambió (observado)

Antes (hasta el 19-oct 15:41 CDMX) el flujo era:

```
registro → CVF leve → OTP por correo → cuenta
```

Ahora Amazon mete un **CVF/ACIC** con retos y, después, un **paso de teléfono**:

```
registro → [CVF: PoW | AWS WAF grid | Arkose] → OTP por correo
         → "Agregar un número de teléfono móvil" (Paso 1 de 2)
         → código por WhatsApp **o** SMS (Paso 2 de 2)
         → cuenta creada (+ dirección de entrega)
```

Tres consequences prácticas que senite:

1. **El correo no es el eslabón débil**: el OTP de correo llegó en el 100 % de los
   intentos de hoy. Los cuellos reales son **Arkose** y la **entrega del SMS**.
2. **El identificador sigue siendo el correo**: probamos el alta solo con
   teléfono (3 intentos) → `unexpected_response`, `email_associated` y
   `captcha_appeared`. El teléfono es **verificación**, no identificador.
3. **Amazon elige el canal del código**: a veces SMS, a veces WhatsApp. Un
   número virtual no recibe WhatsApp → hay que forzar el cambio de canal.

---

## 4. Estructura del código: dónde va cada bloque

Rangos de línea verificados sobre el código actual (2026-10-20).

### 4.1 `bot/config.py` (137 líneas) — configuración global

| Bloque | Qué es |
|---|---|
| L11–28 `_parse_proxies()` | Convierte el pool del `.env` (`host:port@user:pass`) a URLs |
| L37 `TELEGRAM_BOT_TOKEN` | Token del bot |
| L41–54 `SUPER_ADMIN_IDS` / `ADMIN_IDS` | Jerarquía de admins (desde `.env`, con defaults) |
| L89–92 `AES_KEY`, `C_SCRYPT`… | Claves del WAF Solver (⚠️ default en código, ver §11) |
| L106–110 `GATE_PROXIES*` | Pool del `.env` — **solo gates**, no la creación de cuentas |
| **L112–127 `ACCOUNT_PROXIES`** | **Cadena IPRoyal por país para crear cuentas.** `_mkproxy()` (L119) arma `http://user:pass_country-xx_session-<hex>@geo.iproyal.com:12321`; la sesión se **rota por intento** en `accountCreator` |

### 4.2 `bot/services/amazon_gen/accountCreator.py` (910 líneas) — motor del flujo

**Bloques de soporte (L66–303)**

| Rango | Bloque | Función |
|---|---|---|
| L66–86 | `needsWaf`, `fakeProfile` | País que requiere WAF previo; identidad ficticia |
| L88–112 | `buildSess` | Arma la sesión `curl_cffi` con UA, zona horaria, pantalla y proxy |
| L115–136 | `getField`, `postForm`, `genMeta` | Lectura de campos del form; POST con cabeceras de navegación; **metadatos FWCIM** (`metadata1`) |
| L138–163 | `extractScripts` | URLs y hashes de los scripts de la página (para `genMeta`) |
| L165–195 | `normalizeProxy`, `alignProxy`, `rotateProxySession`, `pickProxyFromConfig` | **Proxy**: normaliza, fija el país (minúsculas), **rota la sesión** y elige la cadena IPRoyal del país |
| L197–227 | `isAuth`, `isOtp`, `isCaptcha`, `cvfChallengeType`, `isUnusual`, `isRegForm` | **Detectores del estado de la página** (cookie `at-main`/`at-acb` = autenticado) |
| L229–244 | `submitReg` | POST del formulario de registro |
| L250–284 | `hasWaf`, `solveWaf`, `prefetchWaf`, `applyWaf` | WAF clásico (token `aws-waf-token`) previo al registro |
| L286–303 | `visitSignin`, `claimEmail` | GET a `/ap/signin` y "reclamo" del identificador |

**Bloque CVF / SMS (L305–667) — todo lo nuevo de hoy vive aquí**

| Rango | Bloque | Función |
|---|---|---|
| L305–311 | `emitStatus()` | **Nuevo**: manda el avance del flujo (lo consume el panel) |
| L313–351 | `submitRegWithCaptcha()` | Registra; si la respuesta trae reto CVF → llama `solveCvf()`; avisa "verificación de seguridad" |
| L353–362 | `continueMobileConflict` | Conflicto "teléfono ya registrado" |
| L364–383 | `detectChannel()` | Canal de la página de código: `email` / `sms` / `whatsapp` |
| L385–435 | `switchToSms()` | **Reutilizado**: ya sabía cambiar de canal (form, `newContactType`, enlaces). Hoy es el motor del cambio WhatsApp→SMS |
| L437–468 | `selectEmailJP` | Variante para JP |
| L470–488 | `requestSmsChannel()` | **Nuevo**: envuelve `switchToSms()` y valida que el canal quedó en SMS |
| L490–493 | `contactType()` | **Nuevo**: lee `verificationPageContactType` |
| L495–520 | `requestResend()` | **Nuevo**: "Reenviar código" (`cvf_action=resend`) |
| L522–608 | `collectPhone()` | **Nuevo (núcleo)**: comprar número → enviarlo → forzar SMS → esperar código → responderlo → cerrar/cancelar pedido |
| L610–667 | `submitOtp()` | Responde un código (correo o SMS). **Corregido**: el formulario trae varios `cvf_action` (`code`/`resend`/`collect`) y antes ganaba el último |

**Bloque de cuenta (L671–730)**

| Rango | Bloque | Función |
|---|---|---|
| L671–726 | `AccountBuilder.addAddress()` | Agrega dirección de entrega (best-effort; si falla, la cuenta se entrega igual con `warning=address_failed`) |
| L728–730 | `exportCookies()` | Cookies de la sesión, menos `ak_bmsc`/`bm_sv`/`csm-hit` |

**Bloque orquestador (L734–910)**

| Rango | Bloque | Función |
|---|---|---|
| L738–756 | `Creator.__init__` | `on_status` (avances) y `deadline` (tope global) |
| L758–779 | `Creator.run()` | Ejecuta `flow()`, arma el resultado; captura excepción → `retry()` |
| L781–793 | `Creator.init()` | Identificador, perfil de navegador, huella, sesión, proxy |
| L805–863 | `Creator.flow()` | **El flujo completo**: signin → claim → registro+CVF → OTP correo → OTP SMS |
| L865–905 | `Creator.retry()` | Reintentos: rota proxy/identidad; **fail-fast** para `phone_required` y `sms_not_configured`; respeta `deadline` |
| L907–910 | `Creator.close()` | Cierra el pool de hilos |

### 4.3 `bot/services/amazon_gen/cvfSolver.py` (800 líneas) — resolutor de retos

| Rango | Bloque | Función |
|---|---|---|
| L33–99 | `_setup_config`, `_options`, `_headers`, `_resp_header` | Envoltura del protocolo **aaut**: arma el `options` JSON y lee la cabecera `amz-aamation-resp` |
| L102–121 | `_drop_amazon_com_cookies`, `_restore_cookies` | El XHR a `www.amazon.com` va sin cookies (cross-origin) |
| L124–252 | `_leading_zero_bits`, `_find_nonce`, `_probe`, `_calibrate`, `_solve_pow` | **Solver PoW local**: mide la tasa de hash, calibra bits/cadena y resuelve el reto |
| L255–260 | `_arkose_config` | Saca `publicKey` y `blob` del iframe de Arkose |
| L266–317 | `_capsolver_create`, `_capsolver_wait` | Cliente CapSolver (createTask / getTaskResult) |
| L320–374 | `_awswaf_config`, `_awswaf_problem_url` | **Nuevo**: extrae `awsKey` (id BASE64_JSON), tipo de problema y script; **reconstruye la URL `/problem`** del AWS WAF |
| L377–385 | `_capsolver_proxy` | Proxy en formato CapSolver (`http:host:port:user:pass`) |
| L388–419 | `_solve_with_capsolver` | **Nuevo**: reto AWS WAF con `awsProblemUrl` + `awsKey` + nuestra proxy |
| L422–484 | `_proxy_parts`, `_solve_with_2captcha` | Proxy parseado; Arkose en **dos variantes**: proxyless y con nuestra proxy |
| L487–517 | `_run_2captcha_task` | Ronda de createTask/getTaskResult de 2Captcha |
| L525–551 | `_anticaptcha_has_credit` | **Nuevo**: saldo de Anti-Captcha cacheado 10 min |
| L554–624 | `_solve_with_anticaptcha` | **Nuevo**: respaldo Arkose con `FunCaptchaTask` |
| L627–643 | `_solve_challenge` | **Enrutador**: PoW → local · WAF/AWS → CapSolver · resto → 2Captcha → Anti-Captcha |
| L646–670 | `_build_response`, `_form_fields` | Envoltura `{"challengeType":…, "data":…}` y campos del form |
| L673–800 | `solveCvf()` | **Orquesta el reto**: GET/POST `/aaut/verify/cvf`, resolver, POST del token, y el POST final a `/ap/cvf/verify` con `cvf_aamation_response_token` |

### 4.4 `bot/services/amazon_gen/smsProvider.py` (259 líneas) — SMS (HeroSMS)

| Rango | Bloque | Función |
|---|---|---|
| L40–52 | `SERVICE_AMAZON`, `COUNTRY_IDS`, `CALLING_CODES` | Servicio `am`; id de país en HeroSMS (MX=54, US=187…); prefijo telefónico |
| L69–70 | `SmsError` | Errores del proveedor |
| L84–108 | `HeroSms._get()` | GET a `handler_api.php`; **detecta el sobre de error** `{"title":"NO_NUMBERS"}` que llega con HTTP 200 |
| L111–136 | `_load_bad`, `_save_bad`, `mark_bad()` | **Nuevo**: lista negra persistente (`data/sms_bad_numbers.json`) |
| L138–145 | `balance()` | Saldo |
| L147–180 | `buy()` | **Corregido**: compra y **descarta los números ya conocidos como fallidos** (hasta 4 intentos) |
| L182–191 | `status()` | `STATUS_WAIT_CODE` / `STATUS_OK:<código>` / `STATUS_CANCEL` |
| L193–225 | `finish()`, `cancel()`, `cancel_later()` | Cierre, cancelación y **cancelación diferida** (HeroSMS no cancela antes de 120 s) |
| L227–237 | `wait_code()` | Polling del código |
| L241–259 | `_normalize_number`, `local_number()` | Dígitos; y **quitar el prefijo** (Amazon pide el número local de 10 dígitos) |

### 4.5 `bot/services/amazon_gen/credsBypass.py` (186 líneas) — correo temporal

| Rango | Bloque | Función |
|---|---|---|
| L24–40 | `encryptPassw` | Cifra la contraseña como Amazon (RSA + AES-GCM + KMS frame) |
| L47–68 | `TempMail.getAddr()` | Pide dirección a `http://127.0.0.1:3000/odisea/generate` |
| L70–102 | `watch()`, `stop()`, `waitCode()` | Vigila el correo y espera el código |
| L110–124 | `loop()` | Hilo que sondea el correo |
| L126–161 | `poll()` | **Corregido hoy**: filtra por `received_at` (ignora correos de intentos anteriores) y usa `received_at\|asunto\|remitente` como id (antes `asunto+remitente`, que colisionaba y re-leía el código viejo) |
| L164–186 | `parseCode`, `extract` | Extrae el código de 6 dígitos del asunto/cuerpo |

### 4.6 `bot/services/amazon_gen/` — resto del paquete

| Archivo | Rango / bloque | Función |
|---|---|---|
| `metaDataGen.py` (754) | `FwcimClient` L237–754 | **Huella de navegador**: canvas, métricas, interacción humana, `performance.timing`, cifrado XXTEA de `metadata1` |
| `wafSolver.py` (245) | `AwsWaf` L13–245 | WAF **clásico** de Amazon (`challenge.js`): scrypt/sha256/bandwidth + señales |
| `curl_session.py` (25) | `ImpersonateSession` | `curl_cffi` con `impersonate` por request y `HTTPPROXYTUNNEL` (bug de IPRoyal) |
| `telegram_mail.py` (96) | `TelegramMail` | Modo manual: el usuario mete el código a mano |

### 4.7 `bot/services/amazon_creator.py` (345 líneas) — orquestación async

| Rango | Bloque | Función |
|---|---|---|
| L29–55 | `_save_cookie_file()` | Guarda `data/amazon_accounts/{CC}_{ts}.json` (chmod 600) |
| L58–97 | `_save_attempt_file()` | Respaldo de credenciales cuando el intento falla (`data/amazon_attempts/`) |
| L100–119 | `_FRIENDLY_ERRORS`, `_humanize_error()` | **Tabla de traducción** de error técnico → texto humano (18 estados) |
| L132–136 | `_run_status()` | Puente del avance (hilo → loop) |
| L139–272 | `create_amazon_account()` | **Orquesta**: modo `temp` o `own`, `Creator` con `deadline` (300 s) y `on_status`, `wait_for` de 600 s, persistencia, traducción del error |
| L275–299 | `start_amazon_account_creation()` | Lanza la creación como `asyncio.Task` |
| L302–310 | `submit_otp()` | Permite mandar el OTP a mano (modo manual) |
| L313–333 | `save_amazon_account_to_db()` | Persistencia en SQLite |

### 4.8 `bot/handlers/amazon_creator.py` (687 líneas) — panel inline

| Rango | Bloque | Función |
|---|---|---|
| L49–54 | `AmazonGenState` | Estados FSM (`waiting_email`, `waiting_otp`, `creating`, `waiting_fresh_card`) |
| L57–83 | `_can_use_gencookie`, `_is_admin_over_cap`, `_run_fake_failure` | Reglas de acceso; **diseño deliberado**: un admin sin saldo recibe un fallo genérico (no se delata el tope) |
| L86–102 | `_spend_galleta`, `_reply_no_galletas` | Consumo de galletas/diario |
| **L124–136** | `_rescue_block()` | **Nuevo**: el aviso "Posible cuenta creada a medias" **solo** si la cuenta pudo existir |
| **L138–151** | `_status_update()` | **Nuevo**: pinta el avance real en el panel |
| L161–204 | `_country_kb`, `_email_mode_kb`, `_edit_panel` | Teclados y edición del panel |
| L211–274 | `cmd_gencookie`, `cmd_cook`, `cb_cancel`, `cb_country` | Entrada del usuario |
| L278–423 | `cb_email_mode` | Selector correo propio / temporal + **arranque con `on_status_callback`** |
| L427–510 | `cb_verify_fresh`, `process_fresh_card` | Verificar tarjeta con sesión fresca |
| L517–624 | `process_email` | Modo manual: pide el correo y **arranca con `on_status_callback`** |
| L631–665 | `process_otp` | Recibe el OTP manual |

### 4.9 `EmailApi/main.py` (326 líneas) — correo propio

| Rango | Bloque | Función |
|---|---|---|
| L66–123 | `init_db`, `db_insert`, `db_messages`, `db_cleanup` | SQLite: `messages(recipient, sender, subject, body, received_at)` y `accounts(token, email, expires_at)` |
| L141–171 | `_next_domain`, `db_insert_account`… | **Rotación de dominios** por peso (`EMAILAPI_DOMAIN_WEIGHTS`) |
| L182–216 | `SMTPHandler.handle_DATA` | Recibe el correo por SMTP (aiosmtpd) y guarda |
| L219–256 | `make_router` | `GET /odisea/generate` y `GET /odisea/messages/{token}` |
| L280–311 | `rate_limit_middleware` | Límite de peticiones por IP |

### 4.10 Persistencia en `data/`

| Ruta | Contenido |
|---|---|
| `data/amazon_accounts/{CC}_{ts}.json` | Cuentas creadas (correo, contraseña, cookies, `warning`) — chmod 600 |
| `data/amazon_attempts/{CC}_{ts}_u{user}.json` | Respaldo de intentos fallidos (para recuperación manual) |
| `data/sms_bad_numbers.json` | **Nuevo**: números de SMS que no recibieron el código |
| `data/bot_database.db` | SQLite del bot (gallegas, membresías, cuentas) |
| `data/users.json`, `data/admins.json` | Persistencia temporal (migración a Postgres pendiente) |

---

## 5. El protocolo observado, paso a paso

### 5.1 Registro y reclamo (`claimEmail`)
```
GET  https://www.amazon.com.mx/ap/signin?openid.…        → cookies, scripts, csrf
POST https://www.amazon.com.mx/ap/register                 → claimCollectionLayoutType=unifiedAuthClaimCollection
```
El identificador viaja en el campo `email` (aunque sea un correo temporal).

### 5.2 CVF / ACIC — el reto (`cvfSolver.solveCvf`)
```
GET  https://www.amazon.com/aaut/verify/cvf?options=<json>
      → cabecera  amz-aamation-resp: {"sessionToken":…, "clientSideContext":…}
POST https://www.amazon.com/aaut/verify/cvf?options=<json>
      → HTML del reto (PoW | grid AWS WAF | iframe Arkose)
…    resolver el reto
POST https://www.amazon.com/aaut/verify/cvf/<sessionToken>?context=…&response=…
      → amz-aamation-resp: {"actionType":"PASS"|"ARKOSE_LEVEL_n"|…,"sessionToken":…}
POST https://www.amazon.com.mx/ap/cvf/verify   (cvf_aamation_response_token=<token>)
```
El `response` es `{"challengeType": <tipo>, "data": "<payload>"}` y el `data` es
`JSON.stringify(valor)` — **exactamente** lo que produce el cliente real
(`ACIC.addResponse` en `acic.js`).

`options` incluye: `clientData` (el `data-context`), `challengeType`, `locale`,
`externalId`, `enableHeaderFooter/ModalView/BypassMechanism` en `false`,
`eventTrigger`, `aaExternalToken`, `forceJsFlush`, `aamationToken`, `pageId`.

**Tipos de reto vistos y su solver:**

| `data-challenge-type` | Producto | Solver | Medido |
|---|---|---|---|
| `PROOF_OF_WORK_LEVEL_1..4` (+`_CHAIN_…`) | proof-of-work | local (`_solve_pow`) | ✅ 5/5, 1–5 s |
| `WAF_ADVERSARIAL_SYNTHETIC_GRID_V2_LEVEL_1` | **AWS WAF captcha** (grid `ait.captcha.awswaf.com`) | **CapSolver** `AntiAwsWafTask` | ✅ 3/3, 6–7 s |
| `ARKOSE_LEVEL_2/3/4` | Arkose / FunCaptcha (iframe `arkoselabs.com`) | 2Captcha `FunCaptchaTask` | ⚠️ UNSOLVABLE con una variante; funciona con proxyless→proxy |

### 5.3 El grid de AWS WAF (variante "ait")
La página del reto trae:
```html
<script src="https://ait.<hash>.us-east-1.captcha.awswaf.com/ait/ait/ait/captcha.js"></script>
… "problem":"gridcaptcha-v2-5-0.1-0", "visualSolutionsRequired":1,
   "id":"eyJ1bmlxdWVWYWxpZGF0aW9uSWQiOiJmNDc5MGYwNC1iMWY0LTRiYTQtYTMxMy1iNDU4YjUyN2I3NWMiLCJ0eXBlIjoiQkFTRTY0X0pTT04ifQ"
```
El `id` es el **awsKey** (BASE64_JSON con `uniqueValidationId`). El `captcha.js`
arma esta URL (**hay que reconstruirla a mano** para CapSolver):

```
<prefix>/problem?kind=visual&domain=amazon.com.mx&locale=es-MX
          &problem=gridcaptcha-v2-5-0.1-0&num_solutions_required=1&id=<awsKey>
```
Con esa `awsProblemUrl` + `awsKey` + nuestra proxy, CapSolver resuelve y
devuelve el token en **`solution.cookie`** (JWT del `aws-waf-token`).

### 5.4 OTP de correo
El panel de CVF trae `autoReadStatus=manual`, `verificationPageContactType=email`
y un `<input name="code">`. El código llega por SMTP a nuestros dominios y se
lee del `EmailApi` (`GET /odisea/messages/{token}`).

### 5.5 Paso de teléfono (Paso 1 de 2)
Formulario real (capturado):
```html
<form method="post" action="verify">
  anti-csrftoken-a2z | openid.assoc_handle | openid.mode | openid.ns | verifyToken
  <input type="tel" name="cvf_phone_num">        ← número LOCAL de 10 dígitos
  <select name="cvf_phone_cc">                   ← ISO-2 del país (MX, US, CA, IT, ES, BR)
  <input name="cvf_action" type="submit" value="collect">
</form>
```
Puntos que costaron tiempo y ya están resueltos:
- El país va **separado** (`cvf_phone_cc=MX`); mandar `52…` (12 dígitos) hace que
  Amazon rechace el número.
- El número va **local** (10 dígitos): `smsProvider.local_number()` quita el prefijo.

### 5.6 El canal del código: WhatsApp o SMS
Pantalla "Paso 2 de 2" (variante WhatsApp):
```
Confirma tu identidad — Verificar con WhatsApp +52… — Ingresa el código enviado por WhatsApp
… ¿No tienes WhatsApp? [Enviar código por SMS]
   <form action="verify"> … requestedContactType=sms, action=resend, cvf_action=resend …
```
Variante SMS (la que funciona): `verificationPageContactType=sms`,
`resendContactType=sms`, "Enviamos un código a tu número de teléfono".

Amazon **elige** el canal; hay que forzar el cambio con `switchToSms()` (ya
existía en el código y se reutilizó). Si el canal sigue en WhatsApp, se cancela
el número y se reintenta (`whatsapp_channel`).

### 5.7 Código por SMS
```
POST /ap/cvf/verify  (cvf_action=code, code=<código>, verifyToken, …)
```
⚠️ El formulario trae **varios `cvf_action`** (`code`, `resend`, `collect`): si
gana el último, Amazon reinicia la verificación. Hay que enviar `code`.

### 5.8 Cierre
`POST /a/addresses/add` (dirección de entrega, best-effort) y exportación de
cookies (sin `ak_bmsc`, `bm_sv`, `csm-hit`).

---

## 6. Solvers: qué cubre cada uno

| Servicio | Task type | Cubre | Estado |
|---|---|---|---|
| **local** | — | Proof-of-work | ✅ operativo |
| **CapSolver** | `AntiAwsWafTask` | **AWS WAF grid** | ✅ 3/3 (necesita `awsProblemUrl`) |
| **2Captcha** | `FunCaptchaTaskProxyless` / `FunCaptchaTask` | Arkose | ⚠️ con las **dos** variantes; si no, UNSOLVABLE |
| **2Captcha** | `AmazonTaskProxyless` | AWS WAF **clásico** | ❌ `ERROR_BAD_PARAMETERS` con el reto "ait" |
| **Anti-Captcha** | `FunCaptchaTask` | Arkose | ⛔ **saldo $0** (key en `.env`) |
| **Capzy** | `FunCaptchaTask` (drop-in) | Arkose | — requiere key |
| **captcha.bar** | `FunCaptchaTaskProxyless` | Arkose | — requiere key |

**Enrutador real** (`cvfSolver._solve_challenge`, L627–643):
```
PROOF_OF_WORK*      → _solve_pow (local)
contiene WAF o AWS  → _solve_with_capsolver  → (si falla) _solve_with_2captcha
resto (Arkose)      → _solve_with_2captcha (proxyless → con proxy) → _solve_with_anticaptcha
```

---

## 7. Registro de cambios (2026-10-20)

| # | Archivo / bloque | Antes | Después | Por qué |
|---|---|---|---|---|
| 1 | `accountCreator.py` `pickProxyFromConfig` (L188) | pool del `.env` | cadena IPRoyal por país | El pool mezcla IPs y países |
| 2 | `accountCreator.py` `alignProxy` (L175) | `_country-MX` | `_country-mx` | Formato exacto de IPRoyal (verificado en vivo) |
| 3 | `config.py` L112–127 | — | `ACCOUNT_PROXIES` + `_session-<hex>` | Sesión estable por intento |
| 4 | `cvfSolver.py` (nuevo) | — | protocolo aaut + PoW local + CapSolver + 2Captcha | Amazon metió el CVF |
| 5 | `accountCreator.py` `submitRegWithCaptcha` | `raise captcha_appeared` | `solveCvf()` | El CVF es franqueable |
| 6 | `accountCreator.py` `submitOtp` | ganaba el último `cvf_action` | gana el `code` | Amazon reiniciaba la verificación |
| 7 | `accountCreator.py` `submitOtp` | `phone_required` fail-fast | → paso de teléfono real | Amazon exige celular |
| 8 | `accountCreator.py` `collectPhone` (nuevo) | — | número local + `cvf_phone_cc` | Amazon rechazaba el número |
| 9 | `accountCreator.py` `requestSmsChannel` (nuevo) | — | reutiliza `switchToSms` | Amazon manda por WhatsApp |
| 10 | `accountCreator.py` `requestResend` (nuevo) | — | "Reenviar código" | El SMS a veces no llega |
| 11 | `smsProvider.py` (nuevo) | — | cliente HeroSMS | Compra/espera/cancela números |
| 12 | `smsProvider.py` `buy` + `mark_bad` | — | lista negra de números | HeroSMS **recicla** los cancelados |
| 13 | `cvfSolver.py` `_awswaf_problem_url` (nuevo) | — | URL `/problem` del AWS WAF | Sin esto CapSolver dice `ERROR_INVALID_TASK_DATA` |
| 14 | `cvfSolver.py` `_solve_with_capsolver` (nuevo) | — | grid AWS WAF | 2Captcha no lo cubre |
| 15 | `cvfSolver.py` `_solve_with_2captcha` | una variante | proxyless **y** con proxy | Arkose se resolvía con la 2ª |
| 16 | `cvfSolver.py` `_solve_with_anticaptcha` (nuevo) | — | respaldo Arkose | Preparado; falta saldo |
| 17 | `credsBypass.py` `poll` | id = asunto+remitente, sin filtro de fecha | id con `received_at` + filtro por intento | Se re-leía el OTP viejo → "Código no válido" |
| 18 | `credsBypass.py` | CRLF | LF | Los editors fallaban el match |
| 19 | `amazon_creator.py` (servicio) `create_amazon_account` | — | `on_status_callback`, `deadline` 300 s | El panel se quedaba mudo 5 min |
| 20 | `amazon_creator.py` (servicio) | — | `tech_error` | Saber el error técnico sin romper el texto humano |
| 21 | `amazon_creator.py` (servicio) `_FRIENDLY_ERRORS` | 9 estados | 18 estados | Ningún error técnico llega crudo al usuario |
| 22 | `handlers/amazon_creator.py` `_rescue_block` (nuevo) | siempre "cuenta a medias" | solo si pudo existir | Con captcha la cuenta **no** existe |
| 23 | `handlers/amazon_creator.py` `_status_update` (nuevo) | — | avance en vivo | El usuario veía "creando cuenta…" 5 min |
| 24 | `handlers/amazon_creator.py` | — | `on_status_callback` en los 2 modos | Avances en ambos flujos |
| 25 | `.env` / `.env.example` | — | `HEROSMS_API_KEY`, `TWO_CAPTCHA_API_KEY` | Dependencias nuevas documentadas |

---

## 8. Estados y mensajes

### 8.1 Avances que ve el usuario en el panel
| Momento | Texto |
|---|---|
| Inicio | "Registrando tu correo con Amazon…" |
| Aparece reto | "Amazon está pidiendo una verificación de seguridad (captcha). Resolviendo…" |
| Espera correo | "Amazon envió un código a tu correo. Esperando que llegue…" |
| Valida correo | "Amazon envió el código a tu correo. Validando…" |
| Paso de teléfono | "Amazon pide verificar un número de celular. Comprando número y esperando el SMS…" |
| Reenvío | "Todavía no llega el SMS. Pidiendo que Amazon lo reenvíe…" |
| Llega el código | "Llegó el código por SMS. Verificando con Amazon…" |

### 8.2 Errores técnicos → texto humano (`_FRIENDLY_ERRORS`)
| Técnico | Texto al usuario |
|---|---|
| `otp_failed` | Amazon rechazó el código de verificación… |
| `unexpected_response` | Amazon respondió algo inesperado… |
| `billing_failed` | La cuenta parece creada pero no se pudo agregar la dirección… |
| `email_associated` | Ese correo ya está registrado en Amazon… |
| `number_associated` | Ese correo/teléfono ya está asociado a una cuenta Amazon… |
| `unusual activity` | Amazon marcó actividad inusual… |
| `captcha_appeared` | Amazon pidió una verificación extra (captcha)… |
| `phone_required` | Amazon ahora pide un número de celular… |
| `phone_submit_rejected` / `phone_submit_failed` / `phone_form_missing` | Amazon no aceptó / no se pudo enviar / Amazon cambió la pantalla… |
| `sms_timeout` | No llegó el código por SMS a tiempo… |
| `sms_code_rejected` | Amazon no aceptó el código que llegó por SMS… |
| `whatsapp_channel` | Amazon pidió el código por WhatsApp… |
| `sms_not_configured` | El servicio de números por SMS no está configurado… |
| `timeout_total` | Amazon tardó demasiado en responder… |

### 8.3 Fall-fast vs. reintento
- **Fail-fast** (no se reintenta, no se quema correo): `phone_required`,
  `sms_not_configured`.
- **Reintentable** (correo + IP + identidad nuevos): `captcha_appeared`,
  `otp_failed`, `unexpected_response`, `sms_timeout`, `sms_code_rejected`,
  `whatsapp_channel`, `phone_submit_rejected`.
- **Tope global**: 300 s (`Creator.deadline`) → `timeout_total`.

---

## 9. Configuración

| Variable | Para qué | Estado |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot | ok |
| `SUPER_ADMIN_IDS`, `ADMIN_IDS` | Roles | ok |
| `IPROYAL_USER`, `IPROYAL_PASS`, `IPROYAL_HOST` | Proxy por país | ok (⚠️ default en código) |
| `EMAILAPI_DOMAINS`, `EMAILAPI_DOMAIN_WEIGHTS` | Dominios del correo | ok |
| `EMAILAPI_API_HOST/PORT`, `EMAILAPI_SMTP_*` | EmailApi | ok |
| `TEMPMAIL_API`, `TEMPMAIL_POLL_INTERVAL`, `FAST_MODE` | Cliente de correo | ok |
| `TWO_CAPTCHA_API_KEY` | Arkose | ok ($2.74) |
| `CAPSOLVER_API_KEY` | AWS WAF | ok ($5.69) |
| `ANTICAPTCHA_API_KEY` | Arkose (respaldo) | ⚠️ **saldo $0** |
| `ANTICAPTCHA_SOFT_ID` | Opcional (Anti-Captcha) | sin definir |
| `HEROSMS_API_KEY` | SMS | ok ($7.65) |

---

## 10. Operación

```bash
# Estado
systemctl status gencookie-bot.service --no-pager -n 20
journalctl -u gencookie-bot.service -n 100 --no-pager
journalctl -u emailapi-local.service -n 50 --no-pager

# Tras cualquier cambio de código (obligatorio)
sudo systemctl restart gencookie-bot.service

# Prueba manual de una cuenta (fuera del servicio)
cd /home/olimpo/GenCookie/GenCookie
venv/bin/python - <<'PY'
import asyncio, logging
logging.basicConfig(level=logging.INFO)
from bot.services.amazon_creator import create_amazon_account
async def main():
    async def on_status(t): print("  [panel]", t)
    r = await create_amazon_account(user_id=6060544328, country='MX',
                                    email_mode='temp', on_status_callback=on_status)
    print(r.get('status'), r.get('message'), r.get('saved_to'))
asyncio.run(main())
PY

# Saldos
curl -s "https://hero-sms.com/stubs/handler_api.php?action=getBalance&api_key=$HEROSMS_API_KEY"
curl -s -X POST https://api.2captcha.com/getBalance   -H "Content-Type: application/json" -d "{\"clientKey\":\"$TWO_CAPTCHA_API_KEY\"}"
curl -s -X POST https://api.capsolver.com/getBalance -H "Content-Type: application/json" -d "{\"clientKey\":\"$CAPSOLVER_API_KEY\"}"
curl -s -X POST https://api.anti-captcha.com/getBalance -H "Content-Type: application/json" -d "{\"clientKey\":\"$ANTICAPTCHA_API_KEY\"}"

# Diagnóstico del reto CVF (capturas en /tmp)
ls -la /tmp/cvf_last_step.html /tmp/cvf_after_phone.html /tmp/cvf_r2_*.html

# Números de SMS que fallaron (lista negra)
cat data/sms_bad_numbers.json

# Historial de pedidos del proveedor (para ver reciclaje de números)
curl -s "https://hero-sms.com/stubs/handler_api.php?action=getHistory&api_key=$HEROSMS_API_KEY&size=100"
```

---

## 11. Pendientes y riesgos

| # | Pendiente | Impacto | Acción |
|---|---|---|---|
| 1 | **Recargar Anti-Captcha** (saldo $0) | Arkose depende de 2Captcha | Recarga ~$5 (bid $0.0006/solve); el código ya está listo |
| 2 | **Pool de SMS pequeño y reciclado** | 1 de cada ~3 números muere | Lista negra activa; conviene medir cuántos vírgenes quedan en el pool MX |
| 3 | **Cuentas suspendidas** (19-oct) | Amazon detecta el patrón | Medir tasa de suspensión por dominio de correo; rotar dominios |
| 4 | **Secretos con default en el código** (`bot/config.py`) | Fuga si el repo se filtra | Dejar el default vacío y fallar ruidosamente |
| 5 | **Sin control de versiones** | Otra sesión perdida = otra reconstrucción | `git init` + `.gitignore` (`.env`, `data/`, `session-ses_*.md`, `__pycache__`, `*.db`) |
| 6 | **Ruido en la raíz** | 21 `session-ses_*.md` (5.6 MB), `Black/` (1.3 MB) | Mover a `_archivo/` (**no** borrar `evidencias/`) |
| 7 | **Postgres compartido** | ⚠️ Prohibido tocarlo | Coordinar con quien lo administra (AGENTS.md §7) |

---

## 12. Anexos

**A. Capturas del diagnóstico** (en `/tmp`, se regeneran en cada corrida)
`cvf_last_step.html` (pantalla tras responder un código) ·
`cvf_after_phone.html` (pantalla del código) ·
`cvf_r2_*.html` (páginas de reto por tipo) · `ait_captcha.js` (script del AWS WAF) ·
`acic.js` (bundle de ACIC, 1.8 MB, con `addResponse`).

**B. Reciclaje de números (HeroSMS)**
```
1002133182 16:38  528110516805  sms=None   status=8  (cancelado)
1002286664 17:16  528110516805  sms=None   status=8  (mismo número, reciclado)
1002205022 16:56  529516501132  sms='069938 es tu contraseña temporal…'  (nuevo: llegó)
```

**C. Medición de retos del día**
| Reto | Veces | Resultado |
|---|---|---|
| PROOF_OF_WORK (1–4, +1 encadenado) | 5 | ✅ 5/5 (1–5 s) |
| WAF_ADVERSARIAL_SYNTHETIC_GRID_V2 | 3 | ✅ 3/3 con CapSolver (6–7 s) |
| ARKOSE_LEVEL_2/3/4 (+1 encadenado) | 7 | ✅ 1/7 — el único éxito llegó con la cadena proxyless→proxy (con una sola variante: 0/7) |

**D. Tiempos de un flujo completo (con todos los ajustes)**
| Etapa | Tiempo |
|---|---|
| Registro + PoW | ~15 s |
| OTP de correo | ~3 s |
| Compra del número + envío | ~2 s |
| SMS (número vírgen) | **9 s** |
| Verificación final + dirección | ~10 s |
| **Total** | **41 s** |
