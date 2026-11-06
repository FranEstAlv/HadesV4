---
name: telegram-bot-architect
description: >
  Ayuda a elegir y sostener la arquitectura de un bot de Telegram en Python
  entre python-telegram-bot (PTB), aiogram, pyTelegramBotAPI/telebot,
  Telethon, Pyrogram o TgCrypto — o a evaluar si conviene sumar/migrar a otra
  librería (ej. un bot Bot API que necesita funcionalidad de userbot vía
  MTProto). Usar SIEMPRE que el usuario planee un bot de Telegram nuevo en
  Python, compare librerías, pregunte "qué necesito para que mi bot también
  actúe como usuario/login con teléfono", "cómo evito que Telegram me
  bloquee por mandar muchos mensajes", "por qué mi bot deja de responder
  cuando entran muchos usuarios de golpe", "sqlite database is locked" en un
  cliente de Telegram, o decida si necesita un segundo bot/proceso separado
  — aunque no mencione ninguna librería por nombre. No es de producto/UX de
  bots (`telegram-bot-builder`) ni de TDLib puro (`pytdbot`) — esta decide
  el protocolo y la librería correctos, y referencia a esas dos cuando
  corresponde.
---

# Arquitecto de bots de Telegram en Python

Tu trabajo acá no es "escribir un bot" — es tomar (o revisar) la decisión de
arquitectura antes de que el código exista, y seguir dando criterio mientras
el bot crece. La decisión más cara de deshacer es la de protocolo: Bot API
vs. MTProto. Todo lo demás (qué librería, qué estructura, qué falla primero)
se deriva de esa elección.

## Paso 0: la pregunta que decide todo

**¿El bot necesita actuar como un usuario real de Telegram (login con
teléfono, leer/escribir en chats donde nunca fue agregado como bot, acceder
a la API cruda de Telegram), o le alcanza con ser una identidad bot creada
por @BotFather?**

- Si alcanza con una identidad bot → **Bot API** (HTTP). Librerías: **PTB**,
  **aiogram**, **pyTelegramBotAPI**.
- Si necesita login de usuario o acceso a la API cruda → **MTProto**.
  Librerías vivas: **Telethon** (reimplementación pura en Python) o
  **Pytdbot** (wrapper async de la librería oficial TDLib — ver la skill
  `pytdbot` para el detalle de esa opción, no se duplica acá).

Un error común y caro: usar un bot token en Telethon/Pyrogram no lo
convierte en un wrapper de Bot API. Siguen hablando MTProto — necesitan
`api_id`/`api_hash` de `my.telegram.org` además del token, y todo lo que
implica una sesión MTProto (ver "Sesiones" más abajo) aplica igual.

**Regla de oro para mezclar protocolos** (relevante si ya tenés un bot Bot
API y necesitás algo que no puede hacer): no fuerces todo a una sola
librería. Un bot Bot API (PTB/aiogram) y un cliente MTProto (Telethon) para
la parte que lo requiere son **dos procesos separados** con sus propias
credenciales, compartiendo base de datos si hace falta — no un intento de
hacer que una librería haga lo que no fue diseñada para hacer. Esto es
exactamente el patrón que se usó para separar el bot principal de un
segundo proceso de vigilancia/respaldo en un proyecto real (ver
`references/lecciones-mictlan-alfa1.md`) — la separación de proceso no es
solo prolijidad, es lo que evita que una parte saturada arrastre a la otra.

## Paso 1: qué librería, según caso de uso

| Caso de uso | Elección | Por qué |
|---|---|---|
| Bot nuevo de propósito general, valorás documentación y ergonomía | **python-telegram-bot** | `asyncio` moderno, type hints, ejemplos oficiales muy completos |
| Backend grande, mucha concurrencia, routers/middlewares/DI/FSM | **aiogram** | Arquitectura Dispatcher+Router explícita, middlewares nativos de dos capas, testing del dispatcher sin red |
| Necesitás la última feature de Bot API cuanto antes | **aiogram** (verificar release) | Suele declarar soporte de la versión de Bot API más reciente antes que PTB |
| Bot chico, CRUD, equipo que prefiere código síncrono | **pyTelegramBotAPI (`telebot`)** | Decoradores simples, `TeleBot` síncrono con `AsyncTeleBot` disponible para crecer después |
| Userbot / cliente de usuario / acceso a API cruda | **Telethon** | MTProto activo y mantenido, login de usuario, eventos maduros |
| Ya tenías pensado usar Pyrogram | **No — andá directo a Telethon** | Ver advertencia abajo, no es una opción viable para código nuevo |

**Advertencia dura sobre Pyrogram y TgCrypto**: verificado de forma
independiente (no es una impresión vieja) — el repositorio de Pyrogram fue
**archivado el 23 de diciembre de 2024** y quedó de solo lectura, sin
mantenimiento ni soporte. **TgCrypto corre la misma suerte** — es un
proyecto satélite de Pyrogram y quedó abandonado con él. Si alguien pide
"la aceleración en C para MTProto" pensando en TgCrypto, la alternativa
viva es `cryptg` (la que recomienda la propia documentación de Telethon).
No arranques nada nuevo sobre Pyrogram/TgCrypto en 2026 — si ya existe
código así, es candidato a migración, no a expansión.

Para el detalle completo por librería (instalación, ejemplo funcional
completo, changelog reciente, comparación método a método, diagramas de
arquitectura de producción para Bot API y para MTProto) ver
`references/comparativa-completa.md` — es largo a propósito, no hace falta
tenerlo todo en memoria, consultalo cuando haga falta profundidad.

## Paso 2: problemas de construcción que vas a pisar sí o sí

Estos son los que de verdad muerden en producción, no teoría:

- **Dos pollers con el mismo token**: si el bot corre en más de una réplica
  con `polling()`/`start_polling()`/`infinity_polling()` simultáneamente,
  Telegram lo rechaza (aiogram lo modela como `TelegramConflictError`). No
  escalés un bot de polling subiendo réplicas — o un solo proceso, o
  migrás a webhook + cola.
- **Callback que se queda "cargando"**: si no respondés la `CallbackQuery`
  (`answer()`), el botón queda con el spinner de Telegram indefinidamente.
  Respondé rápido, hacé el trabajo pesado después.
- **`sqlite3.OperationalError: database is locked` (Telethon/Pyrogram)**:
  casi siempre significa que dos clientes están usando el mismo archivo de
  sesión. Una sesión por proceso/cliente, siempre.
- **Rate limits de Bot API**: como referencia práctica de Telegram, no
  superar ~1 msj/seg en chat individual, ~20/min en grupo. Ante un 429,
  respetar el `retry_after` que Telegram devuelve, con jitter — no
  reintentar a ciegas.
- **`FloodWait`/`FloodWaitError` (MTProto)**: no hay tabla pública fija de
  límites. Capturar la excepción y dormir exactamente lo que indica
  (`exc.value` en Pyrogram, `exc.seconds` en Telethon) — Telethon además
  tiene `flood_sleep_threshold` para reintentar solo por debajo de un
  umbral.
- **Sesión de Telethon "vuelve a pedir código"**: normalmente significa
  que cambió el cwd, el nombre/ruta del archivo `.session`, o se borró. No
  es un bug del bot.
- **Bloquear el event loop**: nada de CPU pesada, generación de
  documentos, o llamadas lentas directo en el handler — encolar y liberar
  el loop. En Telethon, el modo `sequential_updates=True` puede acumular
  una cola sin límite si un handler es lento.
- **Actualizar sin fijar versión**: aiogram 3.29.0 fue retirada
  (yanked) por una regresión seria poco después de publicarse. Lockfile +
  pipeline con pruebas antes de mover producción, nunca "instalar latest"
  al construir la imagen.
- **Límite de 50MB para subir archivos por Bot API**: `sendPhoto`/
  `sendVideo`/etc. rechazan cualquier archivo de más de 50MB vía
  multipart, siempre — no es Telegram "detectando automatización" ni un
  bug propio si aparece un `413`/"Request Entity Too Large". Si otro
  proceso descarga el archivo antes (típico en la arquitectura bot +
  userbot), chequear el tamaño (dato que ya viene sin costo, sin
  descargar nada) antes de gastar minutos bajando algo condenado a
  fallar al subirlo. Un "el bot se congela" reportado sin más detalle
  puede ser exactamente esto — una descarga lenta, no un deadlock.

## Paso 3: seguridad, sin negociar

- Bot token, `api_id`/`api_hash`, y cualquier session string van por
  variable de entorno o secret manager — nunca en git, Dockerfile, ni
  logs. Un `StringSession`/session string de MTProto es una credencial de
  máximo privilegio: quien lo tenga puede actuar como esa cuenta.
- Si hay webhook: usar `secret_token` y validar el header
  `X-Telegram-Bot-Api-Secret-Token` — un path "no adivinable" no alcanza
  como única defensa.
- Nunca hacer admin "con todos los permisos" a un bot por comodidad —
  otorgar solo los permisos concretos que necesita, así un token filtrado
  tiene impacto acotado.
- Antes de dar por buena una arquitectura de moderación/anti-abuso,
  revisar `references/lecciones-mictlan-alfa1.md` — documenta un ataque
  real (raid de userbots) que tumbó un bot en producción por exactamente
  este tipo de descuido (llamadas a la API sin cola ni límite de tasa
  durante un pico de carga).

## Paso 4: panel administrativo, invitaciones y coordinación entre procesos — patrones sugeridos, no obligatorios

Tres piezas que aparecen seguido en bots que crecen más allá de lo
básico, ya verificadas en producción — pero son sugerencias para **si tu
bot necesita esa estructura**, no un checklist a implementar siempre. Un
bot chico con dos comandos no necesita panel, ni sistema de invitaciones,
ni un segundo proceso. Recurrí a estas referencias cuando la necesidad
real aparezca, no antes:

- **Panel administrativo con paginación, cierre y roles**: la causa más
  común de "el botón atrás/siguiente de una lista rompe otra parte del
  panel" es dos vistas compartiendo el mismo prefijo de `callback_data`.
  La solución de fondo (no un parche) es que cada sección sea dueña de su
  propio prefijo, con un dispatcher único que enruta por sección y un
  botón de "cerrar" global compartido por todo el panel. Ver
  `references/panel-admin-paginacion-roles.md` para el patrón completo,
  incluida una jerarquía de roles (un nivel fijado por variable de
  entorno al arrancar, el resto asignable desde el propio panel).
- **Sistema de invitaciones de un solo uso**: `create_chat_invite_link(...,
  member_limit=1)` deja que Telegram invalide el link solo, sin necesitar
  revocarlo a mano. Separá "quién puede generar el link" (admin-only para
  chats sensibles, autoservicio configurable para el resto) del detalle
  que rompe en producción sin que sea obvio por qué: si el chat tiene
  restringido el guardado de contenido, un link mandado en `<code>`
  (monospace) queda sin poder copiarse NI abrirse — tiene que ir en texto
  plano para que Telegram lo autodetecte como link real. Ver
  `references/sistema-invitaciones.md`.
- **Coordinación entre dos procesos separados** (la regla de oro del Paso
  0 en código): si además de compartir base de datos de solo-lectura
  necesitan pedirse acciones entre sí (que el userbot se una a un chat,
  que el bot mande un mensaje), una tabla de tareas por dirección
  (`task_type`/`payload`/`status`/`result`) consumida por un poller propio
  de cada proceso — nunca una llamada directa entre los dos. Ver
  `references/lecciones-botsc.md` y `references/arquitectura-dos-procesos-docker.md`
  (esta última además cubre el patrón de infraestructura más simple:
  misma imagen, dos servicios de `docker-compose`, un volumen compartido).

## Paso 5: todo lo que ve un usuario se escribe en lenguaje humano, nunca de sistema

Regla sin excepción para cualquier texto que un botón, panel, menú o
mensaje del bot le muestre a una persona — sin importar si es un error,
una confirmación, o el label de un botón: **se escribe en el idioma y
tono del usuario, nunca en el vocabulario interno del código o de la
infraestructura.** Nombres de excepciones, códigos HTTP, nombres de
contenedores/servicios, rutas de archivo, o el texto crudo que devuelve
una librería no son para un usuario final, aunque sean exactamente lo que
está pasando por dentro.

| Mal (lenguaje de sistema) | Bien (lenguaje humano) |
|---|---|
| `TelegramConflictError: terminated by other getUpdates request` | "Hubo un problema técnico, ya lo estamos revisando." |
| `BadRequest: message is not modified` (dejado escapar al usuario) | (nunca llega al usuario — se maneja en silencio, ver `references/panel-admin-paginacion-roles.md`) |
| "Error 429: Too Many Requests, retry_after=30" | "Estamos recibiendo muchos pedidos, dale unos segundos y probá de nuevo." |
| "El contenedor del bot no responde" / "reinicié el servicio de Docker" | "Tuvimos un corte breve, ya está funcionando de nuevo." |
| `chat_id: -1001234567890` mostrado a un miembro común | El nombre del grupo/canal, no su ID — el ID crudo es información de administración, no de cara al usuario |
| "Tu rol es: ROLE_ADMIN" | "Sos administrador de este grupo." |

Esto **no** significa esconder información real de lo que pasó (ver Paso
16 de `references/lecciones-botsc.md` sobre nunca dar un "debería
funcionar" sin evidencia) — significa traducir esa información a algo que
alguien sin contexto técnico pueda entender y accionar, sin la palabra
"excepción", "contenedor", "callback", "endpoint" ni un stack trace de
por medio.

Matiz para paneles de administración: un admin sí puede necesitar ver un
ID técnico (`user_id`, `chat_id`) para hacer su trabajo — eso es
información operativa legítima en una vista de admin, no un error de
diseño. La regla de lenguaje humano aplica igual de fuerte a **cómo se
explica una falla** (nunca un stack trace ni el nombre de una excepción,
ni para un admin) pero no prohíbe mostrar identificadores técnicos donde
de verdad hacen falta para administrar algo.

## Paso 6: instalar el bot como servicio, si el despliegue es en un VPS sin Docker

Aplica solo si el destino real es un VPS sin contenedores — si el
proyecto ya usa Docker, ver el Paso 4 en su lugar. Cuando sí aplica, el
bot corre mejor como servicio systemd nativo que como un proceso suelto
en una terminal que se corta al cerrar la sesión.
`references/despliegue-systemd-sudoers.md` tiene la guía completa (asume
**Ubuntu 24.04 LTS headless**, sin entorno gráfico — en otra distro los
paths de binarios cambian): confirmar el entorno real antes de escribir
infraestructura, el `.service` con hardening (`ProtectSystem=strict`,
`ProtectHome=read-only` + `ReadWritePaths` si el proyecto vive bajo
`/home`). Si además hace falta administrar el servicio desde una sesión
sin TTY interactiva, la guía cubre cómo armar una regla de `sudoers`
acotada a comandos exactos — acá sí, sin margen: nunca `NOPASSWD:ALL` ni
un wildcard de `systemctl`/`journalctl` sin `--no-pager`, porque eso
habilita un escape de privilegios real (documentado con GTFOBins), no
solo una regla menos prolija.

## Paso 7: si el VPS es compartido con otros proyectos/servicios — dos reglas sin excepción

Un VPS real casi nunca aloja solo este bot. Si en algún momento el
trabajo de arquitectura o despliegue toca un VPS donde conviven otros
proyectos, otros usuarios del sistema, o servicios de base de datos
compartidos, estas dos reglas no se negocian ni se relajan "solo para
diagnosticar":

1. **No tomes contexto de otro directorio del VPS que no sea el del
   proyecto en el que estás trabajando.** No leas, no listes, ni
   explores carpetas de otros proyectos/servicios que compartan el mismo
   usuario de sistema o la misma máquina, aunque aparezcan accesibles.
   Si una tarea legítimamente necesita datos o rozar algo de otro
   proyecto (por ejemplo, una base de datos que se comparte a propósito),
   decilo explícitamente y esperá confirmación antes de leer nada ahí —
   nunca lo asumas por tu cuenta.
2. **PostgreSQL queda estrictamente prohibido de tocar**, si el VPS corre
   una instancia compartida con otro servicio: nada de reiniciar
   `postgresql.service`, editar `pg_hba.conf`/`postgresql.conf`, crear o
   alterar roles/bases, ni conectarte a escribir en una base que no es la
   que este proyecto es dueño — ni siquiera "solo para revisar algo". Si
   el bot necesita Postgres y la instancia ya existe por otro proyecto,
   la vía es coordinar (pedir una base/rol propio, aislado) explícitamente
   con quien administra esa instancia, nunca tocarla directo vos mismo.

Esto no es una sugerencia de buenas prácticas — es el mismo tipo de regla
que ya existe en cualquier VPS real con más de un inquilino, y romperla
por apuro o porque "total era de solo lectura" es exactamente el tipo de
decisión que después no se puede deshacer.

## Paso 8: qué preguntar antes de recomendar código

No escribas código de arranque sin haber confirmado con quien te lo pide:

1. ¿Bot API o necesita algo de MTProto? (Paso 0) — si no está seguro,
   preguntá qué necesita hacer el bot exactamente, no asumas.
2. Si es Bot API: ¿cuánta concurrencia/estructura espera a futuro?
   (define PTB vs. aiogram vs. telebot).
3. Si ya existe un bot y la pregunta es "sumar algo nuevo": ¿ese algo
   nuevo pertenece al mismo proceso, o necesita ser un proceso separado
   con sus propias credenciales? (ver regla de oro del Paso 0).
4. ¿Va a correr por polling o webhook? Define si hace falta pensar en
   colas/idempotencia desde el día uno o no.

## Referencias

- `references/comparativa-completa.md` — informe técnico completo:
  instalación y ejemplo funcional de cada librería, tablas de
  equivalencia método a método, diagramas de arquitectura de producción
  (Bot API y MTProto), guía de migración por librería, y enlaces a la
  documentación oficial de cada una.
- `references/lecciones-mictlan-alfa1.md` — lecciones reales de
  incidentes de producción (no hipotéticas): el raid de userbots que
  tumbó un bot por falta de cola/límite de tasa, el diseño de un segundo
  proceso de respaldo con credenciales propias, y un bug real de
  configuración duplicada (una bandera on/off que vivía en dos lugares a
  la vez y por eso "no se podía apagar").
- `references/panel-admin-paginacion-roles.md` — construcción correcta de
  un panel inline con paginación (atrás/siguiente), cierre global, y
  jerarquía de roles asignable desde Telegram, después de resolver los
  empalmes de `callback_data` de una versión anterior.
- `references/sistema-invitaciones.md` — links de invitación de un solo
  uso, autoservicio configurable desde el panel, y por qué el link tiene
  que ir en texto plano (no monospace) si el chat protege su contenido.
- `references/arquitectura-dos-procesos-docker.md` — patrón de
  infraestructura para dos procesos que comparten base de datos (misma
  imagen, `docker-compose` con dos servicios, un volumen), incluido un
  healthcheck heredado que puede marcar "unhealthy" a un proceso sano.
- `references/lecciones-botsc.md` — gotchas reales de un bot Bot API +
  userbot MTProto en producción: `StringSession` vs. archivo, el ID
  marcado de Telegram vs. el crudo de Telethon, `msg.document` capturando
  más de lo que parece, excepciones que en realidad son éxitos, y una
  tabla de tareas como canal de coordinación entre los dos procesos.
- `references/despliegue-systemd-sudoers.md` — instalar el bot como
  servicio systemd en un VPS Ubuntu 24.04 headless (sin Docker): el
  `.service` con hardening, y una regla de `sudoers` acotada a comandos
  exactos (nunca `NOPASSWD:ALL`) para administrarlo sin TTY interactiva.
- Skill `pytdbot` — si la decisión del Paso 0 apunta a MTProto vía TDLib
  en vez de Telethon.
- Skill `telegram-bot-builder` — para UX/producto del bot (onboarding,
  monetización, diseño de conversación), una vez resuelta la arquitectura.
- Skill `bot-api-bump` — para mantener al día el soporte de versión de
  Bot API en aiogram una vez el bot ya está en producción.
