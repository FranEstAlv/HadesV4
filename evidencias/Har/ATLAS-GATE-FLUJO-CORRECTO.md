# Gate Atlas /tl - Flujo Correcto (Revisado 2026-10-23)

## Resumen Ejecutivo

Réplica **literal** del flujo medido en `www.easternsurplus.net2.har`: **Eastern Surplus + PayPal Payflow Link**, con el mismo producto del HAR (`9M-804`, `PartDetails/4648`, condición `28`). La orden real del HAR quedó en **$5.00 + $0.30 de impuesto = $5.30**; el precio actual en el sitio puede variar. El script de respaldo vive en `tools/atlas_replica.py` y **no** está integrado a Hades.

## Flujo Correcto (Revisado con HAR www.easternsurplus.net2.har)

Secuencia real de páginas en el HAR:

```
GET  /Account/Login
POST /Login                          (btnLogin)
POST /PartsList/BrowseAllParts       (búsqueda)
GET  /PartDetails/4648/9M-804-...
POST /PartDetails/4648/9M-804-...     (ddlCondition=28)
POST /PartDetails/4648/9M-804-...     (btnAddToCart)
GET  /Shop/ShoppingCart
POST /Shop/ShoppingCart               (btnCheckOut)
POST /Shop/Checkout                   (btnReviewOrder)
POST /Shop/ReviewOrder                (rblDeliveryMethod$1)
POST /Shop/ReviewOrder                (chkTermsOfUse)
POST /Shop/ReviewOrder                (btnCompleteOrder + reCAPTCHA)
GET  /SecurePages/PayFlowProDialog.aspx?SecureToken=...&secureTokenId=...
GET  https://payflowlink.paypal.com/?SECURETOKEN=...&SECURETOKENID=...
POST https://payflowlink.paypal.com/processCCInfo.do
GET  /SecurePages/PayPalPayFlowReturn?...RESULT=0&RESPMSG=Approved...
```

### Paso 1: Login (solo si no hay sesión viva)
```
POST https://www.easternsurplus.net/Login
```

**Payload:**
- `ctl00$ctl00$ScriptMan` = `<panel>|btnLogin`
- `RadAJAXControlID` = `ctl00_ctl00_globalContent_mainContent_rdAjaxPanel`
- `__ASYNCPOST` = `true`
- `__EVENTTARGET` = `ctl00$ctl00$globalContent$mainContent$btnLogin`
- `ctl00$ctl00$globalContent$mainContent$txtEmailAddress` = `<email>`
- `ctl00$ctl00$globalContent$mainContent$txtPassword` = `<password>`

**Condiciones:**
- Las credenciales se leen de `ATLAS_EMAIL` / `ATLAS_PASSWORD` en el entorno (los valores reales están en el HAR, no se codifican en el script).
- El reCAPTCHA (`6Lf03wwrAAAAAG4fCR5lNXKUf-aIkr-hPf7_d6C2`) solo aparece en ciertos intentos de login fallido.

### Paso 2: Ir al carrito (si no hay)
```
GET https://www.easternsurplus.net/Shop/ShoppingCart.aspx
```

**Verificar:** Si el carrito ya tiene items, vaciarlos (POST `btnRemove` por cada item).

### Paso 3: Agregar producto del HAR (9M-804, condición 28)
```
POST https://www.easternsurplus.net/PartDetails/4648/9M-804-Spare-Tire-Retainer-Threaded-Rod-Tee-Handle-M939A1-M939A2
```

**3a. Selección de condición (async):**
- `__EVENTTARGET` = `ctl00$ctl00$globalContent$mainContent$ddlCondition`
- `ctl00$ctl00$globalContent$mainContent$ddlCondition` = `28` (Used)

**3b. Agregar al carrito (async):**
- `__EVENTTARGET` = `ctl00$ctl00$globalContent$mainContent$btnAddToCart`
- `ctl00$ctl00$globalContent$mainContent$ddlCondition` = `28`

### Paso 4: Checkout desde el carrito (btnCheckOut)
```
POST https://www.easternsurplus.net/Shop/ShoppingCart
```

**Payload (async):**
- `ctl00$ctl00$ScriptMan` = `<panel>|btnCheckOut`
- `__EVENTTARGET` = `ctl00$ctl00$globalContent$mainContent$btnCheckOut`
- `ctl00$ctl00$globalContent$mainContent$clShoppingCart$ctrl0$txtProductQty` = `1`
- `ctl00$ctl00$globalContent$mainContent$ddlCountry` = `UNITED STATES`
- `ctl00$ctl00$globalContent$mainContent$txtZipCode` = `19053` (zip de facturación)

**Nota:** el HAR **no** usa `btnUpdate`; va directo de `btnAddToCart` a `btnCheckOut`. El `ScriptMan_TSM` del HAR es dinámico (Telerik) y no se replicó de forma estática.

### Paso 5: Checkout → ReviewOrder
```
POST https://www.easternsurplus.net/Shop/Checkout
```

**Payload (async):**
- `ctl00$ctl00$ScriptMan` = `<panel>|btnReviewOrder`
- `__EVENTTARGET` = `ctl00$ctl00$globalContent$mainContent$btnReviewOrder`
- `ctl00$ctl00$globalContent$mainContent$ddlShippingCountry` = `230` (US)
- `ctl00$ctl00$globalContent$mainContent$ddlShippingState` = `10` (CA)
- `ctl00$ctl00$globalContent$mainContent$txtPhone` = `<phone>`
- `ctl00$ctl00$globalContent$mainContent$txtShippingAdd1` = `<address>`
- `ctl00$ctl00$globalContent$mainContent$txtShippingCity` = `<city>`
- `ctl00$ctl00$globalContent$mainContent$txtShippingName` = `<nombre>`
- `ctl00$ctl00$globalContent$mainContent$txtShippingZip` = `<zip>`

### Paso 6: ReviewOrder - Delivery Method (Pickup)
```
POST https://www.easternsurplus.net/Shop/ReviewOrder
```

**Payload (async):**
- `ctl00$ctl00$ScriptMan` = `<panel>|rblDeliveryMethod$1`
- `__EVENTTARGET` = `ctl00$ctl00$globalContent$mainContent$rblDeliveryMethod$1`
- `ctl00$ctl00$globalContent$mainContent$rblDeliveryMethod` = `2` (Pickup)

### Paso 7: ReviewOrder - Terms of Use
```
POST https://www.easternsurplus.net/Shop/ReviewOrder
```

**Payload (async):**
- `ctl00$ctl00$ScriptMan` = `<panel>|chkTermsOfUse`
- `__EVENTTARGET` = `ctl00$ctl00$globalContent$mainContent$chkTermsOfUse`
- `ctl00$ctl00$globalContent$mainContent$chkTermsOfUse` = `on`
- `ctl00$ctl00$globalContent$mainContent$rblDeliveryMethod` = `2`

### Paso 8: ReviewOrder - Complete Order (con reCAPTCHA)
```
POST https://www.easternsurplus.net/Shop/ReviewOrder
```

**Payload (async):**
- `ctl00$ctl00$ScriptMan` = `<panel>|btnCompleteOrder`
- `__EVENTTARGET` = `ctl00$ctl00$globalContent$mainContent$btnCompleteOrder`
- `ctl00$ctl00$globalContent$mainContent$chkTermsOfUse` = `on`
- `ctl00$ctl00$globalContent$mainContent$rblDeliveryMethod` = `2`
- `g-recaptcha-response` = `<token>` (¡OBLIGATORIO!)

**Nota crítica:** El POST a `btnCompleteOrder` **requiere reCAPTCHA**. Sin el token, el servidor no procesa la orden.

### Paso 9: PayFlow Pro Dialog (redirect de vuelta)
```
GET https://www.easternsurplus.net/SecurePages/PayFlowProDialog.aspx?SecureToken=<token>&secureTokenId=<id>
```

### Paso 10: Payflow Link
```
GET https://payflowlink.paypal.com/?SECURETOKEN=<token>&SECURETOKENID=<id>
```

### Paso 11: Procesar tarjeta en Payflow
```
POST https://payflowlink.paypal.com/processCCInfo.do
```

**Payload (exacto del HAR):**
```python
data = [
    ("subaction", ""),
    ("currentpage", "mobileCCEntryPage"),
    ("sameasbilling", "false"),
    ("sid", "<correlation-id-from-payflow>"),
    ("src_identifier", "PFL_mobilePayments"),
    ("CARDNUM", "<tarjeta>"),
    ("EXPDATE", "11/2028"),  # formato MM/YYYY
    ("CSC", "<cvv>"),
    ("billingAddress1", "<address>"),
    ("billingAddress2", ""),
    ("ZIP", "<zip>"),
    ("CSRF_TOKEN", "<csrf-token-from-payflow-page>"),
    ("pay.x", "Pay Now"),
]
```

**Respuesta esperada:**
- `RESULT=0` → Aprobada
- `RESULT=2` → Declinada
- `RESPMSG=Approved` / `RESPMSG=Declined`
- `PNREF=<transaction-id>`

### Paso 12: Redirect de vuelta al comercio
Payflow redirige automáticamente a:
```
https://www.easternsurplus.net/securepages/PayPalPayFlowReturn?RESULT=<result>&RESPMSG=<message>&...
```

**Veredicto:**
- Si `RESULT=0` y `RESPMSG=Approved` → **LIVE**
- Si `RESULT=2` → **DEAD**

## Proceso de Diseño del Nuevo Gate

### 1. Costo
El HAR corresponde a una compra real de **$5.00 + $0.30 de impuesto = $5.30** (OrderConfirm 22748). Se replica el mismo producto y flujo; el precio vigente lo decide el sitio al agregar al carrito.

### 2. ReCAPTCHA
El flujo requiere reCAPTCHA v2 en el paso 8 (`btnCompleteOrder`). El sitekey es:
```
6Lf03wwrAAAAAG4fCR5lNXKUf-aIkr-hPf7_d6C2
```

**Solución:** Usar 2Captcha o CapSolver (`tools/solve_recaptcha.py`).

### 3. Payloads Completos

#### 3.1 Login
```python
# POST /Login
data = {
    "ctl00$ctl00$ScriptMan": "ctl00$ctl00$globalContent$mainContent$ctl00$ctl00$globalContent$mainContent$rdAjaxPanelPanel|ctl00$ctl00$globalContent$mainContent$btnLogin",
    "RadAJAXControlID": "ctl00_ctl00_globalContent_mainContent_rdAjaxPanel",
    "__ASYNCPOST": "true",
    "__EVENTTARGET": "ctl00$ctl00$globalContent$mainContent$btnLogin",
    "ctl00$ctl00$globalContent$mainContent$txtEmailAddress": email,
    "ctl00$ctl00$globalContent$mainContent$txtPassword": password,
}
```

#### 3.2 Add To Cart (producto del HAR)
```python
# POST /PartDetails/4648/9M-804...
data = {
    "ctl00$ctl00$ScriptMan": "ctl00$ctl00$globalContent$mainContent$ctl00$ctl00$globalContent$mainContent$rdAjaxPanelPanel|ctl00$ctl00$globalContent$mainContent$ddlCondition",
    "RadAJAXControlID": "ctl00_ctl00_globalContent_mainContent_rdAjaxPanel",
    "__ASYNCPOST": "true",
    "__EVENTTARGET": "ctl00$ctl00$globalContent$mainContent$ddlCondition",
    "ctl00$ctl00$globalContent$mainContent$ddlCondition": "28",  # Used
}

data2 = {
    "ctl00$ctl00$ScriptMan": "ctl00$ctl00$globalContent$mainContent$ctl00$ctl00$globalContent$mainContent$rdAjaxPanelPanel|ctl00$ctl00$globalContent$mainContent$btnAddToCart",
    "RadAJAXControlID": "ctl00_ctl00_globalContent_mainContent_rdAjaxPanel",
    "__ASYNCPOST": "true",
    "__EVENTTARGET": "ctl00$ctl00$globalContent$mainContent$btnAddToCart",
    "ctl00$ctl00$globalContent$mainContent$ddlCondition": "28",
}
```

#### 3.3 Checkout desde el carrito (btnCheckOut)
```python
# POST /Shop/ShoppingCart
data = {
    "ctl00$ctl00$ScriptMan": "ctl00$ctl00$globalContent$mainContent$ctl00$ctl00$globalContent$mainContent$rdAjaxPanelPanel|ctl00$ctl00$globalContent$mainContent$btnCheckOut",
    "RadAJAXControlID": "ctl00_ctl00_globalContent_mainContent_rdAjaxPanel",
    "__ASYNCPOST": "true",
    "__EVENTTARGET": "ctl00$ctl00$globalContent$mainContent$btnCheckOut",
    "ctl00$ctl00$globalContent$mainContent$clShoppingCart$ctrl0$txtProductQty": "1",
    "ctl00$ctl00$globalContent$mainContent$ddlCountry": "UNITED STATES",
    "ctl00$ctl00$globalContent$mainContent$txtZipCode": zip_code,
}
```

#### 3.4 Checkout
```python
# POST /Shop/Checkout
data = {
    "ctl00$ctl00$ScriptMan": "ctl00$ctl00$globalContent$mainContent$ctl00$ctl00$globalContent$mainContent$rdAjaxPanelPanel|ctl00$ctl00$globalContent$mainContent$btnReviewOrder",
    "RadAJAXControlID": "ctl00_ctl00_globalContent_mainContent_rdAjaxPanel",
    "__ASYNCPOST": "true",
    "__EVENTTARGET": "ctl00$ctl00$globalContent$mainContent$btnReviewOrder",
    "ctl00$ctl00$globalContent$mainContent$ddlShippingCountry": "230",
    "ctl00$ctl00$globalContent$mainContent$ddlShippingState": "10",
    "ctl00$ctl00$globalContent$mainContent$txtPhone": phone,
    "ctl00$ctl00$globalContent$mainContent$txtShippingAdd1": address,
    "ctl00$ctl00$globalContent$mainContent$txtShippingCity": city,
    "ctl00$ctl00$globalContent$mainContent$txtShippingName": name,
    "ctl00$ctl00$globalContent$mainContent$txtShippingZip": zip_code,
}
```

#### 3.5 ReviewOrder (delivery method)
```python
# POST /Shop/ReviewOrder
data = {
    "ctl00$ctl00$ScriptMan": "ctl00$ctl00$globalContent$mainContent$ctl00$ctl00$globalContent$mainContent$rdAjaxPanelPanel|ctl00$ctl00$globalContent$mainContent$rblDeliveryMethod$1",
    "RadAJAXControlID": "ctl00_ctl00_globalContent_mainContent_rdAjaxPanel",
    "__ASYNCPOST": "true",
    "__EVENTTARGET": "ctl00$ctl00$globalContent$mainContent$rblDeliveryMethod$1",
    "ctl00$ctl00$globalContent$mainContent$ddlShippingOptions": "0",
    "ctl00$ctl00$globalContent$mainContent$rblDeliveryMethod": "2",  # Pickup
}
```

#### 3.6 ReviewOrder (terms of use)
```python
# POST /Shop/ReviewOrder
data = {
    "ctl00$ctl00$ScriptMan": "ctl00$ctl00$globalContent$mainContent$ctl00$ctl00$globalContent$mainContent$rdAjaxPanelPanel|ctl00$ctl00$globalContent$mainContent$chkTermsOfUse",
    "RadAJAXControlID": "ctl00_ctl00_globalContent_mainContent_rdAjaxPanel",
    "__ASYNCPOST": "true",
    "__EVENTTARGET": "ctl00$ctl00$globalContent$mainContent$chkTermsOfUse",
    "ctl00$ctl00$globalContent$mainContent$chkTermsOfUse": "on",
    "ctl00$ctl00$globalContent$mainContent$rblDeliveryMethod": "2",
}
```

#### 3.7 ReviewOrder (complete order - CON RECAPTCHA)
```python
# POST /Shop/ReviewOrder
data = {
    "ctl00$ctl00$ScriptMan": "ctl00$ctl00$globalContent$mainContent$ctl00$ctl00$globalContent$mainContent$rdAjaxPanelPanel|ctl00$ctl00$globalContent$mainContent$btnCompleteOrder",
    "RadAJAXControlID": "ctl00_ctl00_globalContent_mainContent_rdAjaxPanel",
    "__ASYNCPOST": "true",
    "__EVENTTARGET": "ctl00$ctl00$globalContent$mainContent$btnCompleteOrder",
    "ctl00$ctl00$globalContent$mainContent$chkTermsOfUse": "on",
    "ctl00$ctl00$globalContent$mainContent$rblDeliveryMethod": "2",
    "g-recaptcha-response": recaptcha_token,  # ¡OBLIGATORIO!
}
```

#### 3.8 Payflow processCCInfo.do
```python
# POST https://payflowlink.paypal.com/processCCInfo.do
data = [
    ("subaction", ""),
    ("currentpage", "mobileCCEntryPage"),
    ("sameasbilling", "false"),
    ("sid", correlation_id),  # De payflow page
    ("src_identifier", "PFL_mobilePayments"),
    ("CARDNUM", card_number),
    ("EXPDATE", f"{month}/{year}"),  # Ej: "11/2028"
    ("CSC", cvv),
    ("billingAddress1", address),
    ("billingAddress2", ""),
    ("ZIP", zip_code),
    ("CSRF_TOKEN", csrf_token),  # De payflow page
    ("pay.x", "Pay Now"),
]
```

### 4. Extraer campos críticos del HTML

#### 4.1 ViewState y EventValidation
```python
from bs4 import BeautifulSoup

soup = BeautifulSoup(html, 'html.parser')
viewstate = soup.find('input', {'id': '__VIEWSTATE'})['value']
eventvalidation = soup.find('input', {'id': '__EVENTVALIDATION'})['value']
viewstategenerator = soup.find('input', {'id': '__VIEWSTATEGENERATOR'})['value']
```

#### 4.2 CSRF Token de Payflow
```python
csrf_match = re.search(r'name="CSRF_TOKEN"[^>]*value="([^"]+)"', payflow_html)
csrf_token = csrf_match.group(1) if csrf_match else None
```

#### 4.3 Secure Token
```python
securetoken_match = re.search(r'SecureToken=([^\s&"\'>]+)', redirect_url)
securetokenid_match = re.search(r'secureTokenId=([^\s&"\'>]+)', redirect_url, re.IGNORECASE)
```

### 5. Mano de obra estimada

| Tarea | Tiempo estimado |
|---|---|
| Replicar flujo del HAR (producto 9M-804) | 2 horas |
| Integrar reCAPTCHA solver (2Captcha) | 4 horas |
| Implementar todos los POSTs | 4 horas |
| Test con tarjetas reales | 2 horas |
| Documentar y ajustar | 2 horas |
| **Total** | **14 horas** |

### 6. Notas adicionales

1. **El carrito se acumula:** Si hay items anteriores, vaciarlos con POST `btnRemove` antes de agregar el nuevo producto.

2. **El reCAPTCHA es obligatorio:** Sin `g-recaptcha-response` en el paso 8, el servidor no procesa la orden.

3. **El idioma del sitio es inglés:** Usar datos en inglés (street address, city, state).

4. **Payflow usa un correlation ID:** El `sid` en `processCCInfo.do` debe coincidir con el de la sesión de Payflow.

5. **El tiempo de espera es mayor:** Con reCAPTCHA y múltiples POSTs, cada verificación tarda ~15-20 segundos.

## Script de respaldo

## Script de respaldo

**Ubicación:** `/home/olimpo/GenCookie/GenCookie/tools/atlas_replica.py`

Este script replica **EXACTAMENTE** el flujo medido en el HAR `www.easternsurplus.net2.har`:
- Mismo producto: `9M-804` (`PartDetails/4648`, condición `28`)
- Mismos payloads (incluido `processCCInfo.do` con `EXPDATE` MM/YYYY, `sid`, `src_identifier`)
- Secuencia real: `btnAddToCart` → `btnCheckOut` → `btnReviewOrder` → `rblDeliveryMethod$1` → `chkTermsOfUse` → `btnCompleteOrder`
- Requiere reCAPTCHA resuelto en `btnCompleteOrder`
- **NO está integrado en Hades** (solo script de respaldo)

**Credenciales:** se leen de `ATLAS_EMAIL` y `ATLAS_PASSWORD` (no se codifican en el script).

**Uso:**
```bash
export ATLAS_EMAIL="..."
export ATLAS_PASSWORD="..."
python3 tools/atlas_replica.py --card "4242424242424242|12|2030|123" --recaptcha "<token>"
```

**Solver de reCAPTCHA:**
```bash
python3 tools/solve_recaptcha.py --api-key "<KEY>" \
  --site-key 6Lf03wwrAAAAAG4fCR5lNXKUf-aIkr-hPf7_d6C2 \
  --page-url https://www.easternsurplus.net/Shop/ReviewOrder
```

**Nota:** sin un token de reCAPTCHA válido, el POST a `btnCompleteOrder` no procesa la orden.

## Implementación recomendada

1. **Separar en módulos:**
   - `atlas_login.py` - Login con rotación de cuentas
   - `atlas_cart.py` - Gestión de carrito
   - `atlas_checkout.py` - Flujo de checkout
   - `atlas_payflow.py` - Procesamiento Payflow
   - `atlas_recaptcha.py` - Integración con solver

2. **Manejo de errores:**
   - Cuenta fallida → marcar como mala
   - ReCAPTCHA timeout → reintentar
   - Payflow error → loguear detalle

3. **Testing:**
   - Usar tarjetas de prueba Visa/Mastercard
   - Verificar veredictos LIVE/DEAD
   - Medir tiempo por verificación
