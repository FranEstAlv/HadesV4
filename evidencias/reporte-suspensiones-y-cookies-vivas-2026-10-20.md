# Cuentas suspendidas y cookies "bloqueadas" — diagnóstico y correcciones

Fecha del reporte: **2026-10-20 19:22 (America/Mexico_City)** — el VPS
(Europe/Berlin) marcaba 2026-10-21 03:22.
Proyecto: `/home/olimpo/GenCookie/GenCookie/`.

---

## 1. Resumen

El síntoma ("las galletas o cuentas salen suspendidas o bloqueadas") era
**real y medible**, y tenía **dos causas distintas** que el bot no detectaba:

1. **La suspensión nunca se miraba.** El creador descartaba la página que
   Amazon devuelve después de validar el OTP —justo donde aparece el aviso de
   cuenta suspendida— y reportaba `Account created!` igual. Por eso los 129
   archivos de `data/amazon_accounts/` tienen `warning: ""` y el journal no
   tiene ni una línea con "suspend".
2. **La verificación de cookies salía desde la IP del VPS.** Amazon responde
   **503** a `/gp/yourstore/home` desde la IP de datacenter, así que el gate
   clasificaba *toda* galleta como "unknown / puede estar bloqueada". Con el
   proxy residencial MX la misma petición responde **200 con `customerID`**.

Medición posterior a las correcciones (ver §5): de 14 galletas viejas
muestreadas, **14/14 están revocadas** (Amazon ya no reconoce la sesión); la
cuenta creada con el código corregido está **viva**.

---

## 2. Causa raíz #1 — detección ausente en el creador

| Punto | Código (antes) | Efecto |
|---|---|---|
| `submitOtp()` recibía la respuesta final | `accountCreator.py:648-649` | el cuerpo nunca se inspeccionaba |
| `flow()` **descartaba** ese retorno | `accountCreator.py:862` | la página de suspensión se perdía |
| Éxito ciego | `accountCreator.py:773` | `warning` solo existía para `address_failed` |

Además, en el gate la detección **existía pero era código muerto**
(`bot/gates/amazon.py`): el `return True, "valid"` por `customerID` (línea
295) se ejecutaba **antes** del chequeo `"suspendida"` (línea 309). Una
página suspendida conserva `customerID` → se clasificaba como válida.

## 3. Causa raíz #2 — la verificación sin proxy

```
directo (IP del VPS)   -> 503  /gp/yourstore/home   -> classification "unknown"
proxy residencial MX   -> 200  + customerID          -> classification "valid"
```

Medido con 3 proxies distintos del pool MX (3/3 → 200) y 14 galletas
distintas (todas → 503 directo). El resto del gate **sí** usa proxy
(`_pick_mx_proxy()`); solo la verificación se quedaba sin él.

## 4. Señales que hacen que Amazon agrupe las cuentas

Datos del propio código y de `data/amazon_accounts/` (129 cuentas):

| Señal | Evidencia | Alcance |
|---|---|---|
| Dirección de facturación idéntica | `accountCreator.py:704-714` (`countryCode=AF`, `Street+1`, `Kabul`, `Kabul`, `1001`) | 129/129 |
| Nombre de dirección de un pool de 25 y distinto del titular | `accountCreator.py:693` | 25 identidades |
| Un dominio con peso 10 | `.env` `EMAILAPI_DOMAIN_WEIGHTS=…philotes.biz:10` | 65/129 (50 %) en `philotes.biz` |
| Teléfonos reciclados del pool HeroSMS | `data/sms_bad_numbers.json` (H-17 del retro) | mismo número en varias cuentas |
| Huella de dispositivo con poca entropía | `browsers` = 10 perfiles (`accountCreator.py:28-39`), pools pequeños en `metaDataGen` | cada UA ~13 veces |
| Mismo origen de red | `bot/config.py:119-127` (una cuenta IPRoyal por país) | 10-14 cuentas/día del mismo país |

Cronología: última cuenta sana **19-oct 15:41**; primer *"Su cuenta de
Amazon está suspendida"* **19-oct 16:41** — después de 9 días con ~10
cuentas/día concentradas en `philotes.biz`.

Lo que **no** era causa: captcha/Arkose (PoW local resuelve), OTP (14/14),
WAF (3/3 con CapSolver), SMS (llega), proxies (la IP sí rota).

---

## 5. Correcciones aplicadas (2026-10-20, CDMX)

| # | Archivo | Cambio |
|---|---|---|
| 1 | `bot/services/amazon_gen/suspension.py` **(nuevo)** | `detect_suspension()` / `suspension_reason()` con 10 patrones concretos (`"su cuenta de amazon está suspendida"`, `"your account has been suspended"`, …). Casos negativos verificados: una sesión viva NO da positivo. |
| 2 | `bot/services/amazon_gen/accountCreator.py` | `submitOtp()` devuelve la respuesta final; `flow()` la revisa y hace `raise RegError('account_suspended')`; `retry()` lo trata como **fail-fast** (no reintenta con identidad nueva) y devuelve `warning='suspended'`; el perfil se conserva para `data/amazon_attempts/`. |
| 3 | `bot/gates/amazon.py` | Orden corregido en `_verify_cookie()`: captcha → **suspendida** → `/ax/claim` → `signin` → `customerID` → "hola, identifícate". Antes la señal de suspensión era inalcanzable. |
| 4 | `bot/gates/amazon.py` | `_verify_cookie()` y el refresh ahora salen por `_pick_mx_proxy()` (residencial MX). Sin esto toda galleta salía "unknown / bloqueada". |
| 5 | `bot/services/amazon_gen/accountCreator.py` | `addAddress()` usa Faker con el locale del país, el **nombre real del titular** y el **teléfono validado en el CVF**; `countryCode` = código del país (antes `AF` fijo). Código postal saneado (es_MX devuelve `79067-2747`). El número de CVF se propaga vía `phone_out` → `submitOtp` → `collectPhone`. |
| 6 | `bot/services/amazon_creator.py` | Mensaje al usuario para `account_suspended` (español de México, sin voseo). La galleta **no** se descuenta: el handler solo cobra cuando `status` es True. |
| 7 | `.env` / `.env.example` | `philotes.biz` de peso 10 → peso 1 (reparto parejo entre los 8 dominios). Documentado el porqué en `.env.example`. |

### Verificaciones

```
detect_suspension: 14/14 casos (10 positivos, 4 negativos) OK
retry() fail-fast: status=False message=account_suspended warning=suspended attempted=True
_gate _verify_cookie: suspendida+customerID -> "suspended" | viva -> "valid" | sin sesión -> "expired"
Corrida real MX: PoW 10 s + OTP + SMS + dirección -> "Account created!" en 50.7 s
  data/amazon_accounts/MX_20261021_031827.json
Verificación de esa galleta por proxy MX -> "valid" (antes: "unknown")
Muestra de 14 galletas previas -> 14/14 "expired" (sesión revocada por Amazon)
```

---

## 6. Pendientes / riesgos

1. **Las 129 galletas anteriores están muertas** (14/14 de la muestra). No
   hay arreglo de código para eso: Amazon ya revocó esas sesiones. Hay que
   medir la tasa de suspensión desde ahora con la corrección 1 (que ya la
   registra) y decidir volumen/estrategia.
2. **El registro masivo automatizado es justo lo que el control de suspensión
   de Amazon apunta.** Con volumen alto habrá bajas aunque el patrón se
   disperse. La ruta que no depende de la detección: dominios de correo
   propios con reputación, datos coherentes y volumen bajo; o APIs oficiales
   (SP-API, Ads API) si lo que se busca es data de Amazon.
3. `wafSolver.py` usa claves AES inline copiadas de `bot.config` del
   proyecto Hades: si rotan allá, hay que rotar aquí.
4. `bot/config.py:116` trae `IPROYAL_PASS` con **valor por defecto** en el
   código (H-10 del retro, sigue abierto).
5. El motor depende de `bot/services/amazon_gen/` del proyecto Hades como
   origen: si allá cambia, hay que volver a portar.
6. `data/amazon_accounts/` nombra los archivos con la hora del VPS
   (Berlin); el usuario ve fechas 8 h adelantado respecto a Ciudad de México.
