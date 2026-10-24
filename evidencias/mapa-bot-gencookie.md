# Mapa del bot GenCookie/Hades

Fecha: 2026-10-22 (CDMX). Fuente: lectura directa del árbol del proyecto.
Propósito: mapa explícito de directorios, archivos y cómo se conectan.

## 0. Vista de pájaro

```
GenCookie/
├── .env / .env.example          # secretos y pools de proxy
├── bot/                         # el bot de Telegram (aiogram 3)
│   ├── main.py                  # arranque, dispatcher, polling
│   ├── config.py                # lee .env, reexporta proxies/planes
│   ├── keyboards.py             # menús inline (364 líneas)
│   ├── assets/                  # welcome.png
│   ├── handlers/                # comandos de Telegram (routers)
│   ├── gates/                   # motores de verificación de tarjetas
│   ├── services/                # persistencia, proxies, Amazon, créditos
│   └── utils/                   # BIN lookup + formatos de mensaje
├── data/                        # estado en disco (sqlite, json, cuentas)
├── EmailApi/                    # API local de correo temporal (TempMail)
├── Krispy/                      # scripts standalone de Krispy (referencia)
├── Black/                       # copia vieja black-chk (no se usa)
├── evidencias/                  # material de trazabilidad (no borrar)
├── cambios/                     # bitácora de sesiones de trabajo
├── justo.py                     # script base Adomicil/Krispy (reversible)
├── iproyal.py                   # imprime los proxies armados del .env
├── credit_card_generator.py     # generador de tarjetas (comando /gen)
├── tools/                       # utilidades sueltas
└── backups/                     # respaldos .tar.gz
```

Servicio systemd activo: `gencookie-bot.service` →
`/home/olimpo/GenCookie/GenCookie/venv/bin/python -m bot.main`
(directorio de trabajo `/home/olimpo/GenCookie/GenCookie`).

## 1. Arranque — `bot/main.py` (120 líneas)

```python
# bot/main.py (resumen)
from bot.handlers.start import register_start_handlers
from bot.handlers.admin import register_admin_handlers
from bot.handlers.gates import register_gate_handlers
from bot.handlers.ghost import register_ghost_handlers
from bot.handlers.rd import register_rd_handlers
from bot.handlers.amazon_creator import register_amazon_creator_handlers
from bot.handlers.extra import register_extra_handlers

def create_dispatcher():
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.middleware(CancelStaleStateMiddleware())
    register_admin_handlers(dp)
    register_gate_handlers(dp)
    register_ghost_handlers(dp)
    register_rd_handlers(dp)
    register_amazon_creator_handlers(dp)
    register_extra_handlers(dp)
    register_start_handlers(dp)
    return dp
```

Hace: crea `Bot` con `TELEGRAM_BOT_TOKEN`, `init_db()` (SQLite),
`dp.start_polling(bot)`. Al apagar, cierra la sesión y el Chromium de
`amazon_playwright`. Notas:

- `/kh` desactivado (import comentado).
- `/rd` usa `register_rd_handlers` (motor `radamanthysV2`).
- `CancelStaleStateMiddleware`: si llega un comando en medio de un flujo
  FSM (menos AmazonGen), cancela el flujo anterior.

## 2. Config — `bot/config.py` (169 líneas)

Lee `.env` y expone:

```python
TELEGRAM_BOT_TOKEN
SUPER_ADMIN_IDS        # default {6060544328, 5531198491} si no hay env
ADMIN_IDS
IPROYAL_USERNAME / PASSWORD_BASE / EXTRA_ARGS / PORT / PROTOCOL
IPROYAL_USER / PASS / HOST   # alias legacy "host:puerto"
GATE_STICKY_COUNTRY
GATE_PROXIES_MX / GATE_PROXIES_ROT / GATE_PROXIES  # compatibilidad
def gate_proxy(pool="mx")      # -> bot.services.proxies.gate_proxy
ACCOUNT_PROXY_COUNTRIES        # pool filtrado a países soportados por Amazon
def make_account_proxy(country)  # UK -> gb
ACCOUNT_PROXIES                # dict país -> proxy
AMAZON_IMPERSONATE             # default "chrome150"
```

## 3. Handlers — `bot/handlers/` (cada uno es un `Router` aiogram)

### 3.1 `start.py` (308 líneas) — `/start` y paneles

- Registra al usuario (`services/users.register_user`).
- Muestra `main_menu` (`keyboards.py`): Gates, Generador, Amazon, perfil.
- Panel Gates lista `/di /ph /eu /wo`, `/amz`, `/gh`, `/rd`, `/extra`.
- Sin proxy ni gates: solo navegación y membresía.

### 3.2 `admin.py` (2369 líneas) — `/batman` (oculto)

- Comando oculto `/batman`; silencio absoluto si no eres admin.
- Subpaneles: usuarios, membresías, créditos, admins (solo super admin
  puede asignar/retirar), galletas Amazon, acceso gencookie.
- FSM con estados `AdminStates` (asignar plan, sumar/restar créditos...).
- Niveles: `super admin` vs `admin`, sin mostrar la diferencia en paneles.

### 3.3 `gates.py` (1143 líneas) — `/di /ph /eu /wo`, `/amz`, `/gen`

```python
GATE_COMMANDS = {"di": ..., "ph": ..., "eu": ..., "wo": ...}
# unitarios:  Command(commands=["di","ph","eu","wo"])
# masivos:    Command(commands=["dimass","phmass","eumass","womass"])
```

- Unitario: envía `processing_text(gate)`, corre el check, edita el mensaje
  con `_format_gate_response(...)`, borra no-Live con `_delete_after`.
- Masivo: envía `mass_processing_text(gate,0,total)`; por cada tarjeta manda
  el resultado con `message.answer`, luego **envía un mensaje nuevo de
  progreso y borra el anterior** (así el avance queda debajo del último
  resultado); al final borra el progreso.
- Costo masivo: 5 créditos (salvo admin/olimpo).
- Incluye `/amz`, `/amzmass`, `/amzstop`, `/gen`, `/save`,
  `/checkcookies`, `/checkcookie` (cookies Amazon).

### 3.4 `ghost.py` (226 líneas) — `/gh`, `/ghmass` (PayPal Guest)

- Motor `bot/gates/paypal_guest.py`; semáforo global de 3 checks.
- Mismo patrón unitario/masivo y formato Hades.

### 3.5 `hiperion.py` (378 líneas) — `/hi`, `/himass`

- Motor `bot/gates/hiperion_checker.py` (Payflow).
- Patrón unitario/masivo con reintento y créditos.

### 3.6 `kh.py` (229 líneas) — `/kh` (desactivado en `main.py`)

- Motor `bot/gates/nmi_one_dollar.py`; masivo deshabilitado.

### 3.7 `rd.py` (487 líneas) — `/rd`, `/rdmass` (Radamanthys)

```python
async def _run_gate(card_data, user_id):
    raw = await gate_krispy_check(card_data, user_id,
                                  shared_proxies=_pick_proxy("mx"))
    if raw.get("status") == "⚠️ Error":   # un reintento con proxy nuevo
        raw = await gate_krispy_check(card_data, user_id,
                                      shared_proxies=_pick_proxy("mx"))
```

- Motor `bot/gates/radamanthysV2.py` (siempre por proxy MX).
- Progreso masivo igual que `gates.py` (mensaje nuevo tras cada tarjeta).
- Cooldowns: 20 s unitario, 30 s masivo; masivo máx 10, cuesta 5 créditos.

### 3.8 `amazon_creator.py` (732 líneas) — `/gencookie`, `/cook`, `/pin`

- Panel inline de país → crea cuenta Amazon con
  `services/amazon_gen/accountCreator.py` usando pool internacional.
- Guarda JSON en `data/amazon_accounts/` y cookies en
  `services/amazon_cookies.py` / `data/amazon_cookies`.
- Acceso: super admins + delegados (`gencookie_access.py`).

### 3.9 `extra.py` (473 líneas) — `/extra` (extrapolador de BINs)

- Motor `services/extrapolator.py`; flujo en un solo mensaje editable.
- Resultado en `<code>` para copiar con un toque.

## 4. Gates — `bot/gates/` (motores de verificación)

### 4.1 `checker.py` (551 líneas) — `/di /ph /eu /wo`

- `GATE_PROXY_POOL`: cada gate usa su pool (`mx` o `rot`).
- `_pick_proxy(pool)` → `{"http": url, "https": url}`.
- Flujo: cuenta generada al vuelo → token → cargo → parseo Live/Dead.
- `queries/`: GraphQL de Amazon (`amazon_wallet_session`,
  `amz_login_diag`) usados por diagnóstico de sesión.

### 4.2 `amazon.py` (1528 líneas) — gate Amazon MX

```python
async def amazon_check(card_data) -> {...}
async def amazon_check_multiple(cards_data, callback) -> [...]
```

- 5 pasos HTTP: entry Prime → register → identify → addcard → CVV.
- Usa sesión de `amazon_playwright.py` (Chromium real local) o
  `amazon_browserless.py` (navegador remoto) + `amazon_login.py` (HTTP).
- Cookies vivas en `data/amazon_cookie.txt` + servicio `amazon_cookies.py`.

### 4.3 `amazon_playwright.py` (664 líneas)

- Chromium real local para la sesión que exige
  `openid.pape.max_auth_age=0`. Cierra el browser al apagar (`main.py`).

### 4.4 `amazon_browserless.py` (600 líneas) / `amazon_login.py` (508)

- Alternativa remota (Browserless/BQL) y login HTTP sin navegador.

### 4.5 `hiperion_checker.py` (650 líneas)

- Verificación vía Nigel B Design + PayPal Payflow.

### 4.6 `nmi_one_dollar.py` (580 líneas)

- NMI $1.00 vía DonorPerfect WebLink.

### 4.7 `paypal_guest.py` (467 líneas) — GHOST

```
GET smart/buttons (clientID) -> facilitatorAccessToken
POST /v2/checkout/orders      -> order_id (USD 0.10)
GET smart/card-fields         -> integrityToken
POST pago guest               -> veredicto
```

### 4.8 `radamantis.py` (1099 líneas) — motor viejo de `/rd`

- Versión anterior (AddCard Krispy adaptada de `Krispy/donas2.py`).
- Ya no se usa: `/rd` corre `radamanthysV2.py`.

### 4.9 `radamanthysV2.py` (523 líneas) — motor actual de `/rd`

```
GetConfig (cache 1h) -> SignupClient -> Login/Client ->
POST /v1/tokens (pua stripe-android) -> Payment/Card/Add -> Payment/Card/List
```

- Ver `evidencias/radamanthysV2.md` para la lógica campo por campo.
- Todo por sticky MX; login explícito tras signup; control con PAN
  inexistente `4532640527811642` (espera `incorrect_number`).

### 4.10 `tns_braintree.py` (366 líneas) + `tns_worker/bt_addpm.py`

- Braintree Vault vía subproceso aislado (`bt_addpm.py`), con credenciales
  IPRoyal legacy (`IPROYAL_USER/PASS/HOST`).

## 5. Services — `bot/services/`

| Archivo | Líneas | Qué hace |
|---|---|---|
| `proxies.py` | 191 | Fábrica central: `gate_proxy("mx"/"rot")`, `gate_proxies`, `account_proxy`, `pool_*`. Lee `IPROYAL_ROTATORY_URL` (tal cual) y `IPROYAL_STICKY_MX_URL` (sticky fijo 10m). |
| `database.py` | 179 | SQLite `data/bot_database.db`: users, admins, memberships, credits, credit_transactions, amazon_cookies, amazon_accounts, gencookie_access, user_galletas, admin_daily_galletas. |
| `users.py` | 69 | Registro y perfil básico. |
| `admins.py` | 56 | `is_admin`, `is_super_admin`; altas/bajas solo super. |
| `memberships.py` | 88 | Planes (`olimpo`, `mensual`), vigencia, `is_membership_active`. |
| `credits.py` | 106 | Saldo y movimientos; masivos cuestan 5. |
| `card_generator.py` | 175 | Generador Luhn para `/gen`. |
| `extrapolator.py` | 352 | Lógica de `/extra`. |
| `galletas.py` | 201 | "Galletas" (recompensas diarias de admin). |
| `gencookie_access.py` | 62 | Acceso delegado a `/gencookie`. |
| `amazon_cookies.py` | 46 | Guarda/lee cookies Amazon. |
| `amazon_creator.py` | 346 | Orquesta creación de cuentas (usa `amazon_gen/`). |
| `logger.py` | 272 | Logging estructurado a journal. |
| `amazon_gen/` | 3392 | Creador de cuentas: `accountCreator.py` (972), `cvfSolver.py` (800, captchas/desafíos), `wafSolver.py` (245), `metaDataGen.py` (754), `smsProvider.py` (259, HeroSMS), `credsBypass.py` (186), `curl_session.py` (25), `telegram_mail.py` (96), `suspension.py` (50). |

## 6. Utils — `bot/utils/`

- `formatting.py` (101): `processing_text` (unitario, spinner),
  `mass_processing_text` (masivo con barra `▓/░`), `escape_html`,
  `strip_custom_emojis`, emojis personalizados del bot.
- `bin_lookup.py` (66): BIN → marca, tipo, nivel, banco, país
  (usa `binlist.net` con cache).

## 7. Data — `data/` (estado en disco)

```
data/
├── bot_database.db        # SQLite: users, admins, memberships, credits...
├── users.json / admins.json / memberships.json / credits.json
├── tarjetas.csv           # tarjetas de prueba/semilla
├── amazon_cookie.txt      # cookie viva de Amazon
├── amazon_accounts/       # JSON por cuenta creada (BR/CA/IT/MX/...)
├── amazon_attempts/ amazon_debug*/ hiperion_debug/  # trazas de gates
├── sms_bad_numbers.json   # números SMS que fallaron
├── txt/                   # textos/plantillas
└── gates/
    ├── krispy_accounts.txt  # cuentas de tienda (email|password)
    └── tns_accounts.txt
```

Las carpetas `amazon_debug*` guardan el paso 1 del entry Prime por timestamp.

## 8. Raíz: scripts sueltos

| Archivo | Uso |
|---|---|
| `justo.py` (458) | Flujo base Adomicil/Krispy reversible: `getconfig/signup/login/card/list/validate/update/raw`. Firma MD5 `TR#SC=...`, token Stripe con `stripe-android/20.26.0`, UA `okhttp/4.12.0`, todo directo sin proxy. |
| `iproyal.py` (110) | Imprime sticky/rotatorio/pools armados desde `.env`. Punto de referencia para validar proxies a mano. |
| `credit_card_generator.py` | Generador detrás de `/gen` y `services/card_generator.py`. |
| `kc.py`, `justo_sign.py`, `extra_functions.py` | Utilidades/experimentos de firma y funciones extra. |
| `test_*.py`, `verify_consolidation.py` | Pruebas y verificaciones sueltas. |
| `EmailApi/` | Servicio `emailapi-local.service`: TempMail local (SMTP :1025, API :3000, dominios `*.cyou` etc.), SQLite `emails.db`. |
| `Krispy/` | `kremee.py`, `donas2.py` (CLIs standalone alineados al `.env`); `mode_racoon.py` con `IndentationError` preexistente (no tocar). |
| `Black/` | Copia vieja `black-chk-src` (referencia, no corre). |
| `tools/`, `jadx_out/` | Herramientas y decompilado del APK. |
| `cambios/` | Bitácoras por sesión (`README-2026-10-22-radamanthys.md`, ...). |
| `backups/` | `.tar.gz` de código (sin `.env`). |
| `session-ses_*.md`, `signup_out.json`, `getconfig.json` | Trazas y config cacheada de Krispy. |

## 9. Flujo de un `/rdmass` (ejemplo punta a punta)

```
Usuario: /rdmass (reply con 10 tarjetas)
  -> handlers/rd.py: cobra 5 créditos, manda mass_processing_text(0/10)
  -> _pick_proxy("mx") -> services/proxies.gate_proxy("mx")
       -> IPROYAL_STICKY_MX_URL (string fija del .env)
  -> gates/radamanthysV2.gate_krispy_check por tarjeta:
       GetConfig -> Signup -> Login -> token Stripe -> Card/Add -> Card/List
  -> handlers/rd.py: manda cada resultado, reenvía el progreso
       (mensaje nuevo + borra el anterior), borra no-Live (delay)
  -> services/credits.deduct_credits ya cobrado al inicio
  -> services/logger + journal registran last4 enmascarado
```

## 10. Reglas operativas (de `AGENTS.md`)

1. Todo proceso como servicio systemd; tras cambiar código,
   `sudo systemctl restart gencookie-bot.service` (y `emailapi-local`).
2. Nunca `pkill`/`kill`; no levantar doble instancia (mismo token).
3. Comandos admin ocultos (`/batman`): silencio absoluto a no-autorizados.
4. Español de México, sin voseo.
5. `evidencias/` nunca se borra; `2Captcha` se valida por POST JSON.
