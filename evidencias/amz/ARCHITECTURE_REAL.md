# GenCookie — mapa de arquitectura real

Este documento separa lo que el repositorio original realmente alcanza desde su Dockerfile de lo que parece legado, duplicado o huérfano. Esta edición de laboratorio no habilita generación real ni conexiones de red desde sus rutas `/lab/*`.

## 1. Entrada oficial del contenedor

El Dockerfile original ejecuta `python amazon_cookie_gen.py`. `main.py` contiene otra implementación de `AmazonAccountCreator`, pero el Dockerfile original no copia ni ejecuta ese archivo. Por tanto, para el despliegue Docker original, `amazon_cookie_gen.py` es la fuente de verdad operativa.

## 2. Ruta HTTP real

`POST /generate` hace, en orden: autorización opcional por `API_KEY`; parseo del JSON; verificación opcional de créditos contra `API_BASE_URL`; consulta del interruptor global del servicio; llamada a `generate_cookie_api`; y descuento de 4 créditos tras éxito si había token de usuario.

## 3. Selección de motor

La condición real es:

```text
si CAPSOLVER_API_KEY + HERO_SMS_API_KEY + PROXY_STRING:
    AmazonAccountCreator
else si force_playwright == true:
    create_amazon_account / Playwright
else:
    error
```

Consecuencia importante: `force_playwright=true` **no fuerza** Playwright cuando las tres dependencias del motor principal están presentes, porque la condición principal se evalúa primero.

## 4. Motor principal

`AmazonAccountCreator` usa perfil falso, HeroSMS, sesión HTTP/curl_cffi, manejo AWS WAF, CapSolver, OTP, exportación de cookies y `AccountBuilder` para la dirección. La API después pasa las cookies por `CookieConverter`.

## 5. Motor legacy/fallback

`create_amazon_account` usa Chromium headless mediante Playwright, proxy, HeroSMS/5sim, varios solvers (2Captcha/AntiCaptcha/CapSolver), automatización de formularios, manejo de actividad inusual, dirección y extracción de sesión.

## 6. Plano de control externo

`API_BASE_URL` se usa para créditos (`/user/credits`, `/user/use-credits`) y para habilitar/deshabilitar el servicio (`/service-status-for-generator`, `/admin/bot/toggle-service`). Esto está separado de AstralCHK/CORS: CORS permite a `https://astralchk.com` llamar a la API desde navegador, pero no prueba que el generador envíe resultados espontáneamente a ese dominio.

## 7. Código aparentemente legado/no referenciado

El análisis estático del archivo original no encontró referencias directas a varios helpers, entre ellos `process`, `verify_with_retry`, `is_phone_registered`, `add_good_session`, `get_best_session`, varios wrappers sync de SMS y algunos helpers de Playwright. Se marcan como *candidatos*, no como certeza absoluta de dead code.

## 8. Lectura general

La evidencia revisada encaja con un backend de automatización de cuentas/sesiones con dos generaciones de implementación. Hasta ahora no se ha identificado una etapa de robo de archivos locales, persistencia, lectura de credenciales del host o exfiltración espontánea durante el arranque.
