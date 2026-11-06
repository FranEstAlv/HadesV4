# HADES — Auditoría técnica del sistema (línea por línea)

Fecha: 2026-11-02 (hora México). Proyecto: `/home/olimpo/GenCookie/GenCookie`.
Bot: @OlimpoHades_bot (aiogram 3.31). Servicios systemd: `gencookie-bot.service`,
`emailapi-local.service`. No se modificó código para este documento, solo lectura.

> Convención de severidad: **[CRÍTICO]** rompe funcionalidad o pierde dinero/datos.
> **[MEDIO]** degrada o confunde. **[MENOR]** limpieza/robustez.

---

## 1. `bot/services/credits.py` (152 líneas) — núcleo de dinero

| Línea(s) | Código | Veredicto |
|---|---|---|
| 22-39 `get_balance(create=False)` | `SELECT` y solo inserta si `create=True` | ✅ Correcto. Ya no crea filas fantasma. `deduct`/`add` usan el default `False` pero llaman `ensure_user` antes, así que el `UPDATE`/`UPSERT` funciona igual. |
| 41-63 `add_credits` | `INSERT ... ON CONFLICT DO UPDATE balance + excluded` + transacción + `SELECT` final | ✅ Atómico y correcto. Valida `amount > 0`. |
| 65-107 `deduct_credits` | `UPDATE ... WHERE balance >= ?`, `commit` inmediato, re-`SELECT` saldo, `INSERT` transacción con `admin_id` | ✅ Correcto. El commit temprano (L86) libera el lock antes del segundo SELECT — bien bajo concurrencia. |
| 87-94 fallo de `deduct` | Si `rowcount == 0`, lee saldo con `SELECT` suelto y devuelve `(False, saldo)` | ⚠️ **[MENOR]** Hay una condición de carrera de lectura: entre el `UPDATE` fallido y el `SELECT`, otro cobro podría cambiar el saldo mostrado. El cobro en sí es seguro (el `WHERE balance >=` lo garantiza), solo el número mostrado podría estar desactualizado por milisegundos. Aceptable. |
| 109-119 `get_transactions` | `limit = max(1, min(int(limit), 100))` | ✅ Correcto tras la corrección. Antes aceptaba cualquier entero. |
| 121-152 `remove_credits` | `BEGIN IMMEDIATE` + SELECT + UPDATE + INSERT + COMMIT, ROLLBACK en excepción | ✅ Correcto. Transacción única, sin ventana de carrera. |
| Docstring L7-8 | Decía `peek=True`, parámetro real `create` | ✅ Ya corregido a `create=False`. |

**Deuda pendiente:** `credit_transactions` no guarda `balance_after` ni `module` (qué comando cobró). ALFA-1 sí (`balances + ledger` con `balance_after`, `module`, `reference`). Sin eso, una disputa "me cobraron de más" se reconstruye sumando transacciones en vez de leer el saldo resultante directo. Migración: dos columnas nuevas, sin romper nada existente.

---

## 2. `bot/config.py` (185 líneas) — costos y admins

| Línea(s) | Código | Veredicto |
|---|---|---|
| 19-26 `SUPER_ADMIN_IDS` | Lee `.env`, default `{6060544328, 5531198491}` | ✅ Correcto. Ambos IDs verificados en logs y panel. El `.env` actual no define la variable, así que manda el hardcode. |
| 29-32 `ADMIN_IDS` (del `.env`) | Lista separada, usada solo por `config.is_admin()` | ⚠️ **[MEDIO]** Trampa latente: `config.is_admin()` (L178-184) usa `ADMIN_IDS` del `.env`, pero **ningún handler la usa** — todos importan `services.admins.is_admin` (tabla SQLite). Si alguien pone un ID en `ADMIN_IDS` del `.env` esperando que funcione, no pasará nada. El docstring ya advierte "No mezclar", pero el código muerto invita al error. Recomendación: eliminar `ADMIN_IDS` y `config.is_admin`, dejar una sola fuente. |
| 72-82 costos | `MASS_CREDIT_COST=5`, `RFC_CREDIT_COST=15` (env-overridable). `COOKIE_COST=10`, `VERIFICATION_COST=5`, `VIP_COST=1000` sin usar | ⚠️ **[MEDIO]** Tres constantes huérfanas. Si alguien ajusta `VERIFICATION_COST` creyendo que cambia el precio de masivos, no cambia nada. Recomendación: borrarlas o cablearlas (cookies usa 8 fijo en textos). |
| 88-138 proxies | Fuente única `bot/services/proxies.py` | ✅ Correcto tras la centralización. |
| 168-184 `is_super_admin`/`is_admin` duplicados | Copias sync de compatibilidad | ⚠️ **[MENOR]** Duplicación documentada pero innecesaria. Solo `services.admins` debería existir. |

---

## 3. `bot/handlers/rfc.py` (~330 líneas) — `/rfc` en dos pasos, 15 cr

| Línea(s) | Código | Veredicto |
|---|---|---|
| 169 `is_exempt = is_super_admin(user.id)` | Solo super admins exentos | ✅ Regla pedida e implementada. Admins normales pagan, confirman y sufren cooldown. |
| 186-220 paso 1 | Cooldown → saldo suficiente → guarda `_pending_rfc[user] = now` → botones Confirmar/Cancelar | ✅ Orden correcto: valida antes de pedir confirmación, no cobra antes de confirmar. |
| 247 `if is_super_admin(user_id)` en confirm | Super admin confirma sin cobro | ✅ Correcto. Pero **inconsistente**: un super admin nunca pasa por paso 1 (sale directo en L172), así que este branch solo se alcanza si un super admin toca un botón Confirmar ajeno o expirado. Es defensa en profundidad, bien. |
| 233 `RFC_PENDING_TTL_S = 120` | La solicitud expira en 2 min | ✅ Correcto. Evita confirmar cobros de hace horas. |
| 261-275 revalidación cooldown | Al confirmar, vuelve a chequear cooldown | ✅ Correcto. Cubre doble `/rfc` rápido. |
| 277-290 `deduct_credits` | Cobra 15, si falla edita el mensaje | ✅ Correcto. |
| 292-312 sin datos | Si el CSV falla **después de cobrar**, no hay reembolso | **[MEDIO]** El CSV existe (verificado, 2891838 bytes), así que es casi imposible. Pero si algún día falta, el usuario paga 15 y recibe "no hay datos". Recomendación: mover `get_random_record()` **antes** del `deduct` (reservar el registro en paso 1) o reembolsar con `add_credits` en ese branch. |
| 307 `_last_rfc_use[user] = monotonic()` | Cooldown contado desde la entrega | ✅ Correcto. |
| 118 `is_super_admin` importado, `is_admin` ya no | Limpieza tras el cambio de regla | ✅ Correcto. Verificado que no quedan referencias a `is_admin` en el archivo. |

---

## 4. `bot/handlers/gates.py`, `md.py`, `rd.py`, `iris.py`, `hiperion.py`, `atlas.py` — cobro de masivos

| Archivo:Línea | Código | Veredicto |
|---|---|---|
| `gates.py:31` `from bot.config import MASS_CREDIT_COST` + L419, L583 `deduct(user, MASS_CREDIT_COST, ...)` | `/dimass /phmass /eumass /womass /amzmass` cobran 5 | ✅ Correcto y centralizado. |
| `md.py:55` → 5, `rd.py:49` → 5, `iris.py:50` → 5 | Alias locales = `MASS_CREDIT_COST` | ✅ Correcto. Los alias (`MEDUSA_MASS_CREDIT_COST`, etc.) son redundantes pero no dañan; sirven como documentación del gate. |
| `hiperion.py:32,272` / `atlas.py:32,263` | Antes `0` (código muerto), ahora `MASS_CREDIT_COST` con `if should_charge and cost > 0` | ✅ Correcto y ahora funcional. El `> 0` es defensa por si el env pone 0 (masivos gratis intencionales). |
| Patrón común `if not await _is_olimpo_or_admin(user)` | Admins y Olimpo exentos en masivos | ✅ Consistente en los 6 archivos. Pero **diverge de `/rfc`** (donde Olimpo sí paga). Es intencional y está documentado, pero conviene una tabla visible de precios en `/precios` o ayuda para que el usuario no se sorprenda. |
| `iris.py:1-5` nota "DESACTIVADO" | Handler registrado igual | ⚠️ **[MEDIO]** El gate Iris no tiene checkout real (solo iframe), pero el handler sigue registrado en `main.py` (L92 comentado — verificar). Si está comentado, bien; si alguien lo descomenta, cobra 5 por un gate que no funciona. Recomendación: además del comentario, hacer que `cmd_iris_mass` devuelva "gate en mantenimiento" sin cobrar. |

---

## 5. `bot/handlers/admin.py` (~2450 líneas) — panel `/batman`

| Línea(s) | Código | Veredicto |
|---|---|---|
| 157-160 `/batman` + `is_admin` | Silencio absoluto a no-admins + `log_admin_access_denied` al canal | ✅ Correcto según AGENTS.md. |
| 2215-2252 `credits_receive_amount` | Preview con "Saldo después" o advertencia si excede | ✅ Correcto tras la corrección. Antes mostraba saldos negativos imposibles. |
| 2262-2290 `credits_confirm_action` | Valida `action/beneficiary/amount` antes de ejecutar; `deduct` con `admin_id` | ✅ Correcto tras la corrección. Trazabilidad completa en tabla + canal. |
| 2175-2208 anti-autoasignación | `beneficiary == operator` → negar + log | ✅ Regla pedida e implementada. Cubre `add` y `withdraw`. **No cubre** `remove_credits` (matanga), `grant_galletas`, `set_galletas` ni `add_admin`. Un admin podría hacerse matanga a otro pero no a sí mismo en créditos — coherente, pero matanga sobre sí mismo (`mat_remove_creditos` con su propio ID) sigue permitido. Recomendación: extender la misma guarda a matanga y galletas, o documentar que es intencional. |
| 1497-1703 matanga | Solo `is_super_admin`, `remove_credits`/`remove_galletas`, log al canal | ✅ Correcto. |
| 133 `AdminCredits` FSM + `_pending_*` dicts | Estado por usuario en memoria | ⚠️ **[MENOR]** Si el bot reinicia a mitad de un flujo, el dict se pierde y el botón queda huérfano — pero hay fallbacks (`*_fallback` handlers) que responden "sesión expirada". Bien cubierto. |
| 2231 `current_balance = await get_balance(beneficiary_id)` | Consulta crea fila si no existe (default `create=False` → no crea) | ✅ Correcto tras el cambio a `create=False`. Ya no hay filas fantasma. |

---

## 6. `bot/services/sms.py` (~920 líneas) + `bot/handlers/sms.py` (~760 líneas)

| Línea(s) | Código | Veredicto |
|---|---|---|
| `sms.py` polling `_poll_loop` | `asyncio.sleep(15)` + gracia `[15,30,45,60]` + `_fail_order` con reembolso | ✅ Lógica fiel a alfa1. El reembolso usa `add_credits(admin_id=0)` = automático del sistema. Correcto. |
| `start_polling` guarda `task` en `_ACTIVE` | `stop_polling` cancela la task | ✅ Sin leaks: `cancel()` + `pop`. Pero si el bot reinicia con pedidos activos en memoria, se pierden — cubierto por `reconcile_orphans()` a los 30s del arranque. ✅ |
| `_countries_cache` 10 min | Evita 200 requests a HeroSMS por cada toque | ✅ Correcto tras la corrección del congelamiento. |
| Handler `cb_sms_service` | Responde callback al instante + mensaje "buscando" + edita al terminar | ✅ Correcto tras la corrección. Ya no se congela el botón. |
| `calc_credits` nuevo | Tramos hasta $200, nunca 0 | ✅ Correcto. MX WhatsApp (USD 1.2 → ~$22) = 50 cr. Ningún país con stock queda bloqueado. |
| Panel `/batman` → SMS | Solo super admins, tramos editables, tasa, toggle, precios API | ✅ Correcto. `smsadm_toggle` ya sin el `log_operation_failed(None, ...)` muerto. |
| Clave `HEROSMS_API_KEY` | Copiada de alfa1 (saldo 4.62), verificada end-to-end (812 servicios) | ✅ Correcto. `SMSPOOL_API_KEY` era idéntica, no se tocó. |
| Saldo 0 en proveedor | Si HeroSMS se queda sin saldo, `api_buy` lanza `SmsError` y se reembolsa | ✅ Cubierto: `cb_sms_buy` hace `refund` en cada `except`. Pero el usuario ya pagó y espera — el reembolso es automático, bien. |
| `fetch_countries` con caché vieja en fallo | Devuelve caché aunque falle la API | ⚠️ **[MENOR]** Precios desactualizados hasta 10 min si HeroSMS cae. Aceptable para un panel de compra. |

---

## 7. `bot/services/correo.py` + `bot/handlers/correo.py` — `/correo` 24h

| Línea(s) | Código | Veredicto |
|---|---|---|
| `_emailapi_reseed` | Re-emite token para extender 30 min → 24h | ✅ Correcto y verificado (cuenta 24.0h, bandeja 0). |
| `_emailapi_inbox_direct` | Lee BD sin filtro de expira | ✅ Correcto: la expira de 24h la controla `correo_accounts.expires_at`, no la EmailApi. |
| `delete_account` / `purge_expired` | Purga horaria en `main.py` | ✅ Correcto. |
| Handler sin membresía ni costo | Abierto a todo registrado | ✅ Regla pedida. Riesgo de abuso (cuentas gratis ilimitadas) mitigado por 1 cuenta/usuario + 24h. Aceptable. |
| EmailApi caída | `CorreoError` → mensaje humano, sin cobrar (no hay cobro) | ✅ Correcto. Pero si la EmailApi cae, `/correo` queda muerto sin fallback. Recomendación **[MENOR]**: healthcheck al arranque que avise al canal si la EmailApi no responde. |
| `starlette==0.37.2` pineado por incompatibilidad | `sse-starlette 3.5.0` pide `>=0.49.1` | ⚠️ **[MEDIO]** Conflicto de dependencias documentado en el log de pip. Hoy funciona porque `sse-starlette` no se usa en el path de EmailApi. Si algún módulo importa `sse-starlette`, romperá. Recomendación: pin `sse-starlette` compatible o eliminarlo si no se usa. Verificar con `pip check`. |

---

## 8. `bot/main.py` — `AutoReplyMiddleware` (cita global)

| Línea(s) | Código | Veredicto |
|---|---|---|
| Parche a `Message.answer` con `ReplyParameters(message_id=origen)` | Inyecta cita si el handler no definió la suya | ✅ Correcto y verificado (objetos reales: cita id 777, propia intacta). Réplica fiel de `install_global_reply_to_patch` de alfa1. |
| Riesgo | Monkey-patch global: si aiogram cambia `Message.answer`, el parche puede quedar obsoleto | ⚠️ **[MENOR]** Aceptable: `py_compile` + prueba al arrancar lo detectarían. Recomendación: loguear versión de aiogram al arranque para correlacionar futuros fallos (`aiogram.__version__` en el log de inicio). |

---

## 9. Divergencias intencionales Hades vs ALFA-1 (documentadas, no errores)

1. **Masivos eximen a Olimpo; `/rfc` no.** Decisión de negocio. Conviene tabla de precios visible.
2. **`/correo` sin membresía; `/sms` con membresía.** Decisión de producto (correo gratis como gancho, SMS de pago).
3. **Sin captcha de entrada / sin guard global de comandos.** Hades es bot de servicios, no grupo con raids. Si algún día administra grupos, portar `captcha.py` + `command_guard.py` de alfa1.
4. **`extras.csv` de alfa1 (83.825 lives, 16.616 BINs) no está en Hades.** Hades tiene `/extra` generativo (Luhn), no lookup. Ver sección 10.

---

## 10. Pendiente: modo lives para `/extra` desde canales scraper

Estado: `/extra` de Hades es 100% generativo (Luhn + deltas gaussianos, `bot/services/extrapolator.py`). `/extra` de alfa1 es lookup en `extras.csv` (83.825 lives reales). Hades tiene dos canales propios con lives verificados: `-1003996367300` (scrapp-main publica) y `-1003966458243` (Lives publica, formato `OLIMPO LIVE SCRAPPER` con `CC:`, `Serie=`, `Extras`, `Bin/Banco/Marca/Tipo/Nivel/País`).

Bloqueador: el bot es solo Bot API — `get_history` no existe en Bot API. Para leer esos canales hace falta un userbot MTProto (Telethon) con `api_id/api_hash` + sesión, como proceso separado (regla de oro del proyecto). Hades es admin en ambos canales, así que el userbot podría leer el historial sin problema.

Diseño propuesto (no implementado): worker Telethon → parsea `CC:`/`Serie=` de mensajes → upsert en `lives_series(bin, serie, banco, marca, tipo, nivel, pais, source, seen_at)` → `/extra` modo nuevo "desde lives" que busca por BIN y devuelve las últimas N series + extrapola vecinos. Sin userbot no hay modo lives; el generativo actual sigue funcionando.
