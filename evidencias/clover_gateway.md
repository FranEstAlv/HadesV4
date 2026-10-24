# Gateway Clover (`clover_ter.py`) — Análisis y Verificación

**Última actualización:** 2026-10-14
**Origen:** `evidencias/clover_ter.py`
**Tipo declarado:** Cargo real USD 1.00 vía Clover Hosted iFrame

---

## 1. Resumen Ejecutivo

El script **NO puede funcionar contra su URL configurada**. La página `https://isnvd.org/donate` migró su procesador de pagos de Clover a **Zeffy** (`zeffy-form-link` + `embed-form-script.min.js`). Todos los supuestos del script (endpoints Clover, tokenización RSA, `donatecharge`) quedaron obsoletos para ese merchant.

El código en sí está bien estructurado (async, RSA-OAEP real, captcha dedicado), pero apunta a una integración que **ya no existe** en el sitio objetivo.

---

## 2. Arquitectura del Script

```
GET {checkout_url} (/donate)
  → extraer sitekey/spec/settings del HTML
  → resolver reCAPTCHA vía CryptoTG (servicio externo)
  → descargar publicKey de checkout.clover.com/assets/keys.json
  → cifrar PAN con RSA-OAEP-SHA256
  → GET token.clover.com/v1/tokens con PAN cifrado (NO POST)
  → obtener clover_token
  → POST {checkout_url}/donatecharge con datos Faker + clover_token
  → clasificar HTML: thank you/success → APPROVED, error/declined → DECLINED
```

### 2.1 Componentes

| Clase | Rol |
|-------|-----|
| `ProxyManager` | Lee `proxys.txt` (`host:port:user:pass`), mezcla y rota. **Exige** proxies: aborta si el archivo está vacío. |
| `CryptoTG` | Cliente async del servicio `api.cryptotg.com` para resolver reCAPTCHA. Requiere `CRYPTOTG_API_KEY` (vacía en `.env`). |
| `CloverCrypto` | Descarga `keys.json` de Clover y cifra el PAN con RSA-OAEP (MGF1-SHA256). |
| `CloverHTTPx` | Orquesta todo el flujo con `httpx` async + proxy por tarjeta. |

### 2.2 Endpoints que el Script Asume

| Endpoint | Método | Estado real |
|----------|--------|-------------|
| `https://isnvd.org/donate` | GET | 200, pero ahora es página Zeffy (sin Clover) |
| `https://isnvd.org/donate/donatecharge` | POST | 200 pero retorna HTML genérico, no un charge |
| `https://token.clover.com/v1/tokens` | GET con query | 405 a GET vacío (espera params; endpoint vivo) |
| `https://checkout.clover.com/assets/keys.json` | GET | 200, llaves públicas reales |
| `https://api.cryptotg.com/createTask` | POST | Requiere API key (no configurada) |

---

## 3. Verificación con Browserless + Requests

### 3.1 Snapshot del checkout

```bash
python3 snapshot_runner.py "https://isnvd.org/donate"
```

| Campo | Resultado |
|-------|-----------|
| Status | 200 |
| Título | ISNVD |
| Forms | 0 |
| Inputs | 0 |
| Botones | 1 ("Donate") |

**Sin formulario ni inputs en la página.** El botón "Donate" abre un modal externo.

### 3.2 Análisis del HTML

- SDK de Clover presente: `<script src="https://checkout.clover.com/sdk.js">` (residuo, sin uso).
- Procesador real: **Zeffy**.
  ```html
  <button class="donate-button"
    zeffy-form-link="https://www.zeffy.com/embed/donation-form/support-isnvd?modal=true">Donate</button>
  ```
- Texto visible: *"Payments are securely processed through Zeffy."*
- Sin `sitekey`, sin `recaptcha`, sin `donatecharge`, sin iframe de Clover.

### 3.3 Endpoints Clover (infraestructura)

- `token.clover.com/v1/tokens` responde `405 GET not allowed` a GET vacío → el servicio existe, espera parámetros (coherente con el uso del script).
- `checkout.clover.com/assets/keys.json` responde 200 con llaves RSA reales → la parte criptográfica del script es válida contra la infra de Clover.
- `donatecharge` responde 200 con HTML genérico, no procesa ningún cargo.

---

## 4. Fallas Críticas del Script

### 4.1 Objetivo migrado a Zeffy (bloqueante)

El flujo completo depende de que `/donate` contenga el form Clover con `sitekey`/`parameter_spec`/`recaptcha_settings`. Hoy esa página no contiene ninguno. `_extract_captcha_params` usa valores hardcodeados de fallback (`6Le-wvkSXXXXXXX`, etc.), así que el script **intentaría resolver un captcha falso** y tokenizar contra un merchant que ya no usa Clover.

### 4.2 CryptoTG sin API key (bloqueante)

`CONFIG["cryptotg_key"]` viene de `CRYPTOTG_API_KEY`, que está vacía en el entorno. Sin ese servicio no hay token reCAPTCHA y el flujo muere en el paso 2.

### 4.3 `proxys.txt` obligatorio (operativo)

`ProxyManager` hace `raise RuntimeError("ABORT: Sin proxies válidos")` si el archivo no existe o está vacío. El nombre por defecto (`proxys.txt`, con falta ortográfica) ni siquiera coincide con convenciones del proyecto (`GATE_PROXIES` vía `.env`).

### 4.4 Clasificador de respuesta frágil

El cargo se clasifica por substrings (`thank you`, `error`, `declined`) sobre HTML arbitrario. Funciona para Zeffy/DonorPerfect simples, pero un cambio de copy rompe la clasificación Live/Dead.

### 4.5 Año a 4 dígitos

`normalize_card` convierte `aa` → `20aa` y el payload envía `exp_year` en 4 dígitos. La API de tokens Clover espera `exp_year` en 2 dígitos en la mayoría de integraciones Hosted iFrame; esto puede causar rechazos `invalid_expiry` aunque la tarjeta sea válida.

---

## 5. Qué Sí Es Reutilizable

- **CloverCrypto (RSA-OAEP):** correcto y verificado contra `keys.json` real. Sirve para cualquier otro merchant que sí use Clover Hosted iFrame.
- **Estructura async con semáforo:** el patrón `process_batch` con `asyncio.Semaphore` es el modelo a seguir para gates concurrentes.
- **Separación token vs charge:** tokenizar primero y cargar después es el flujo correcto de Clover; el script lo implementa bien.

---

## 6. Recomendación

**No integrar este gate contra `isnvd.org`.** Opciones:

1. **Buscar otro merchant Clover Hosted iFrame activo** y apuntar `CLOVER_CHECKOUT_URL` ahí, manteniendo el código cripto.
2. **Reescribir como gate Zeffy** si se quiere mantener el objetivo ISNVD (otro procesador, otro flujo, otro documento).
3. **Si se reutiliza el código:** mover proxies a `GATE_PROXIES` del `.env` del proyecto, mover `CRYPTOTG_API_KEY` a `.env`, corregir `exp_year` a 2 dígitos, y eliminar los fallbacks hardcodeados de sitekey.

---

**Verificado con:** Browserless `snapshot_runner.py` + `requests` directos.
**Evidencia snapshot:** `evidencias/browserless-snapshot/output/snapshot_20261014_141818/`
