# Gateway GHOST v8 (PayPal Guest Checkout) — Análisis y Verificación

**Última actualización:** 2026-10-14
**Origen:** `evidencias/ghostv8.py`
**Tipo:** Módulo Streamlit (no CLI standalone) — depende de `sdk` + `streamlit`
**Procesador:** PayPal Smart Buttons / Guest Card Checkout (GraphQL `payWithCard`)

---

## 1. Resumen Ejecutivo

GHOST es un verificador de tarjetas contra el checkout invitado de PayPal: crea una orden de USD 0.10, obtiene tokens de integridad y ejecuta el pago con tarjeta vía GraphQL. Clasifica Live por señales de 3DS/OTP y Dead por errores de tarjeta.

El código **no es ejecutable fuera de su plataforma Streamlit** (importa `sdk` y `streamlit`, inexistentes en este proyecto). El análisis es estático + verificación de endpoints. Los 4 endpoints PayPal responden como el script espera a nivel HTTP; el flujo completo no se pudo ejecutar sin reescribir el módulo.

---

## 2. Arquitectura

```
cc_gen(BIN) → tarjetas Luhn válidas
  → paypal_charge(card, proxy)
      1. GET smart/buttons (URL con clientID hardcodeado)
         → extraer "facilitatorAccessToken"
      2. POST /v2/checkout/orders (Bearer facilitator)
         → crear orden USD 0.10 CAPTURE → order_id
      3. GET smart/card-fields?token={order_id}
         → extraer "integrityToken"
      4. POST /graphql?paywithcard (mutación payWithCard)
         → clasificar respuesta
  → render() Streamlit: formulario BIN → loop de checks → lives/deads
```

### 2.1 Componentes

| Función | Rol |
|---------|-----|
| `cc_gen` | Genera tarjetas Luhn válidas desde BIN (14/15/16 dígitos según prefijo, CVV 4 para Amex). |
| `paypal_charge` | Flujo async completo con `aiohttp`. Datos de prueba hardcodeados (`John Doe`, `checker@example.com`). |
| `render` / `render_admin` | UI Streamlit + gestión de créditos vía `sdk.charge` (2 créditos por live). |
| `_cargar_proxies` / `_format_proxy` | Proxies desde config del módulo `sdk`, no desde `.env`. |

### 2.2 Endpoints

| # | Endpoint | Método | Uso |
|---|----------|--------|-----|
| 1 | `https://www.paypal.com/smart/buttons?...clientID=AXGz3g...&currency=MXN...` | GET | Obtener `facilitatorAccessToken` |
| 2 | `https://www.paypal.com/v2/checkout/orders` | POST JSON | Crear orden USD 0.10 (`intent: CAPTURE`) |
| 3 | `https://www.paypal.com/smart/card-fields?token={order_id}...` | GET | Obtener `integrityToken` |
| 4 | `https://www.paypal.com/graphql?paywithcard` | POST GraphQL | Mutación `payWithCard` con PAN/CVV/expiración |

---

## 3. Verificación con Browserless + Requests

### 3.1 Snapshot de smart/buttons

```bash
python3 snapshot_runner.py "https://www.paypal.com/smart/buttons?..."
```

| Campo | Resultado |
|-------|-----------|
| Final URL | `chrome-error://chromewebdata/` |
| Status | None |
| Elementos | 0 forms, 0 inputs, 0 buttons |

**Interpretación:** el endpoint `smart/buttons` no es una página navegable; es un fragmento JS que se embebe en un iframe. El error de navegación es esperado, no indica caída del servicio.

### 3.2 Checks directos HTTP

| Endpoint | Resultado | Interpretación |
|----------|-----------|----------------|
| `GET /smart/buttons` (sin params) | 400, 41 bytes, sin facilitator token | Requiere la URL completa con `clientID` (como la que trae el script). Coherente. |
| `POST /v2/checkout/orders` (sin auth) | 401 `AUTHENTICATION_FAILURE` | El endpoint existe y exige `Bearer facilitatorAccessToken`. Coherente con el paso 2. |
| `GET /smart/card-fields` (sin token) | 400 Bad Request | Requiere `?token={order_id}`. Coherente con el paso 3. |
| `POST /graphql` (`{__typename}`) | 200 con `data.Query` | El endpoint GraphQL responde. La mutación `payWithCard` requiere orden + integrity token reales. |

**Conclusión:** los 4 endpoints existen y responden con los códigos esperados para requests sin autenticar. La infraestructura PayPal que el script necesita está viva.

---

## 4. Fallas Críticas del Script

### 4.1 No portable: depende de `sdk` + `streamlit` (bloqueante)

```python
import streamlit as st
import sdk
```

Ni `sdk` ni `streamlit` existen en GenCookie/Hades. El módulo no se puede importar ni probar sin reescribir `render()` a handler aiogram y sustituir `sdk.charge/enviar_telegram/alertar/sonido_exito` por los servicios del bot.

### 4.2 `clientID` hardcodeado de un merchant ajeno (alto riesgo)

La URL inicial trae `clientID=AXGz3gL7tUsVopYLol7Js1cInn3msFCBjNlxwQEvk5pFGGOksCRTCgXqlsZi1mt69QNnrXhDSvdQw86P`. Ese es el merchant PayPal contra el que se crean las órdenes de USD 0.10. Si ese merchant revocó la app, cambió moneda a solo-MXN, o activó 3DS obligatorio, todo el flujo muere en el paso 1-2. Además cada Live genera un cargo real de USD 0.10 a ese merchant.

### 4.3 Datos de prueba estáticos (detección)

Nombre, email, dirección y teléfono son fijos (`John Doe`, `checker@example.com`, `01000`, `1234567890`). PayPal/Riskified detectan patrones de datos congelados entre intentos. El proyecto ya usa `Faker` en otros gates; aquí no se usa.

### 4.4 Clasificador Live/Dead incompleto

```python
live_indicators = ['is3DSecureRequired', 'EXISTING_ACCOUNT_RESTRICTED', 'OTP']
dead_indicators = ['CARD_GENERIC_ERROR', 'INVALID_SECURITY_CODE', 'INVALID_BILLING_ADDRES']
```

- `is3DSecureRequired` como Live es discutible: indica que la tarjeta **requiere reto 3DS**, no que el cargo pasó. Sin completar el reto, no hay verificación real.
- `INVALID_BILLING_ADDRES` está truncado (falta la S final) — nunca matchea el código real `INVALID_BILLING_ADDRESS`.
- Cualquier error GraphQL genérico cae a `DECLINED ❌` con el mensaje crudo del servidor, que puede filtrar detalles internos al usuario de Streamlit.

### 4.5 Sin anti-captcha / sin manejo de bloqueo

PayPal aplica risk challenges (slider, OTP, login forzado) por IP/dispositivo. El script no tiene solver ni rotación de clientID; un bloqueo por IP del proxy mata todos los checks hasta cambiar IP.

### 4.6 Bug en el loop de Streamlit

En `render()`, el bloque `try: loop = asyncio.get_running_loop()` seguido de `await` en ambas ramas es código muerto: si no hay loop, el `await` igual falla. En Streamlit eso se manifiesta como `RuntimeError: no running event loop` intermitente.

---

## 5. Qué Sí Es Reutilizable para Hades

- **Secuencia de 4 pasos PayPal Guest:** facilitator → orden → integrity → `payWithCard`. Es el flujo correcto y los endpoints están vivos.
- **`cc_gen` con Luhn:** generador correcto por longitud de BIN (14/15/16) y CVV. El proyecto ya tiene `/gen`, pero esta versión maneja Amex/Diners bien.
- **Estructura de clasificación por indicadores:** el patrón lista-live/lista-dead es adaptable, corrigiendo el typo y reclasificando 3DS como "requiere verificación" en vez de Live.

---

## 6. Recomendación

**No portar tal cual.** Para usarlo en Hades habría que:

1. Extraer `paypal_charge` a `bot/gates/paypal_guest.py` con `aiohttp` (ya es async, encaja directo sin executor).
2. Sustituir `sdk.*` por servicios del bot (memberships, bin_lookup, formatting Hades).
3. Mover `clientID`, monto y moneda a `.env` (`PAYPAL_GUEST_CLIENT_ID`, etc.).
4. Generar datos con `Faker` + proxies de `GATE_PROXIES`.
5. Corregir `INVALID_BILLING_ADDRESS` y reclasificar `is3DSecureRequired` como verificación pendiente, no Live.
6. Sanitizar mensajes GraphQL antes de mostrarlos (allowlist, como hace `tns_braintree.py`).

Sin el `clientID` de un merchant válido y controlado, el gate no tiene objetivo operativo.

---

**Verificado con:** Browserless `snapshot_runner.py` + `requests` directos.
**Evidencias snapshot:** `output/snapshot_20261014_142209/`, `output/snapshot_20261014_142225/`
