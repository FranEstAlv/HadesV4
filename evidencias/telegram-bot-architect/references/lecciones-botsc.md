# Lecciones de un bot Bot API + userbot MTProto real (aiogram + Telethon)

Estas son lecciones de un proyecto real, no una receta a seguir al pie de
la letra — el código de ejemplo de este archivo es una sugerencia de cómo
resolver cada problema puntual si tu bot se topa con el mismo caso, no una
estructura obligatoria para cualquier bot con userbot.

Bitácora de problemas reales de un proyecto con exactamente la arquitectura
de "regla de oro" del Paso 0 (bot Bot API + userbot MTProto, dos procesos,
coordinados por una base de datos compartida, nunca llamada directa entre
ellos). Complementa `references/lecciones-mictlan-alfa1.md` (esa es sobre
por qué separar procesos; esta es sobre gotchas puntuales de la parte
MTProto/Telethon que no salen ahí) y `references/arquitectura-dos-procesos-docker.md`
(infraestructura). Formato: **Problema → Causa → Solución**.

## Sesión de Telethon: `StringSession`, no archivo — sobre todo en Docker

**Problema**: la sesión de Telethon guardada como archivo
(`TelegramClient(session_name, ...)`) y un volumen Docker montado en una
ruta distinta a donde el proceso realmente escribía el archivo — nunca
coincidían, la sesión se perdía en cada rebuild.

**Solución**: `StringSession` — la sesión vive como texto en una variable
de entorno (`TG_SESSION_STRING`). Elimina el archivo y el volumen por
completo, no hay ruta que pueda desincronizarse:

```python
from telethon import TelegramClient
from telethon.sessions import StringSession

client = TelegramClient(StringSession(os.environ["TG_SESSION_STRING"]), api_id, api_hash)
```

Generarla una vez con un script aparte (login interactivo,
`client.session.save()` imprime el string) y guardarla como secreto —
nunca como archivo `.session` dentro de la imagen ni el repo.

## El ID que ve un bot no es el ID crudo de Telethon

**Problema**: IDs de chat guardados desde el userbot (`entity.id`) nunca
coincidían con los que usa la Bot API del lado del bot.

**Causa**: Telegram tiene un ID "marcado" — negativo para grupos básicos,
con prefijo `-100` para canales/supergrupos — que es el que ve cualquier
bot y el que trae `event.chat_id` en un handler de Telethon en vivo.
`entity.id` (el crudo, interno) es siempre positivo y **no** es el mismo
número.

**Solución**: `telethon.utils.get_peer_id(entity)` (con `add_mark=True`,
el default) da el ID correcto para guardar/comparar contra lo que use el
lado Bot API. Cualquier `.id` crudo que termine comparado contra algo
escrito por el bot es un bug — no una casualidad de datos.

## `msg.document` no es solo "documentos", en Telethon

**Problema**: una regla que filtraba por `media_types=document` también
capturaba videos, notas de voz, GIFs y stickers.

**Causa**: en Telethon, video/audio/voice/gif/sticker son
`MessageMediaDocument` por dentro — `msg.document` da un objeto no nulo
para todos ellos. `msg.video`, `msg.sticker`, etc. son chequeos *más
específicos* sobre el mismo objeto, no categorías mutuamente excluyentes.

**Solución**: para "documento genérico de verdad", verificar `document is
not None` Y que ninguno de los otros atributos específicos
(`video`, `audio`, `voice`, `gif`, `video_note`, `sticker`) esté presente.

## `InviteRequestSentError` no es un error

**Problema**: al unirse (desde el userbot) a un chat que exige aprobación
de un admin para nuevos miembros, Telethon levanta
`InviteRequestSentError` — un manejador genérico de excepciones lo
marcaba como tarea fallida.

**Causa**: el nombre suena a fallo, pero el propio mensaje de la
excepción dice que la solicitud de unirse se envió con éxito — Telegram
modela un caso "exitoso pero incompleto" (queda pendiente de aprobación
humana del otro lado) como excepción.

**Solución**: capturarla aparte de cualquier error real y marcar la tarea
como completada con una nota de "pendiente de aprobación", no como error.

## Los links de carpeta (Chatlists, `t.me/addlist/...`) no son invites de un chat

**Problema**: un link de tipo `t.me/addlist/<slug>` no funcionaba con la
lógica existente de unirse por invite privado o `@usuario` público.

**Causa**: un link de carpeta agrupa varios chats bajo un slug — no hay
un solo "chat" al que unirse, es un mecanismo distinto del protocolo.

**Solución**: `CheckChatlistInviteRequest(slug)` para ver qué falta unir,
resolver cada `Peer` liviano a su entidad completa (los `chats`/`users`
de esa misma respuesta ya traen el `access_hash` necesario), construir
`InputPeer` con `telethon.utils.get_input_peer()`, y unir todo de una con
`JoinChatlistInviteRequest(slug, peers)`.

## Un test con el cliente de Telegram mockeado puede seguir escribiendo en la base real

**Problema**: apareció una fila de prueba en la base de datos de
producción después de correr un test.

**Causa**: el test mockeaba `TelegramClient` (para no pegarle a la API
real), pero no mockeaba la capa de base de datos — la función bajo prueba
sí escribía en la base real apuntada por el `.env` cargado en esa sesión
de shell. Mockear la red no alcanza si la función también toca disco/DB.

**Solución adoptada como regla fija**: cualquier test que ejercite código
que toque la base de datos compartida corre con la variable de conexión
apuntando a un archivo/base temporal y aislada — nunca a la de producción
cargada sin querer, aunque el resto del test sí mockee la parte de red.

## Un solo dispatcher no impide correr una segunda instancia sin querer

**Problema**: `TelegramConflictError` al correr `python -m bot.main`
manualmente para probar un cambio, mientras el `.service` de systemd
seguía corriendo el código viejo con el mismo `BOT_TOKEN`.

**Causa**: dos procesos de polling con el mismo token compitiendo — el
error correcto, pero disparado por apuro de prueba, no por un bug.

**Solución/regla adoptada**: parar el servicio systemd (o el contenedor)
**antes** de cualquier prueba manual en caliente, sin excepción — nunca
asumir que "es solo un momento" es seguro con polling.

## Una tabla de "chats conocidos" poblada desde un solo proceso siempre va a tener huecos

**Problema**: una categoría del panel seguía mostrando el ID crudo de un
chat en vez de su nombre, pese a haber agregado una función que resuelve
nombres contra una tabla de chats conocidos.

**Causa**: esa tabla se llenaba únicamente desde el lado userbot (con los
chats donde esa cuenta está presente) — pero el chat en cuestión era un
destino donde solo el *bot* tiene presencia (un canal donde el bot
publica pero el userbot nunca fue agregado). Un chat así nunca iba a
aparecer en la tabla sin importar cuánto se esperara, porque el proceso
que la llena no tiene ninguna visibilidad de ese chat.

**Solución**: si dos procesos separados tienen acceso a **conjuntos
distintos** de chats, la tabla compartida de "chats conocidos" tiene que
poblarse desde los dos lados — un `ensure_known_chat()` liviano del lado
que descubre el chat (resolviendo su nombre con el método que ese lado sí
tiene disponible, ej. `bot.get_chat()`) es más simple que intentar que un
solo proceso vea todo.

**Vuelta de tuerca real, encontrada al agregar limpieza automática después**:
una vez que la tabla se poblaba desde los dos lados, hizo falta podar filas
viejas (chats de los que el userbot ya salió) — y ese borrado automático
podó por error un chat que solo el *bot* conocía, porque nunca iba a
aparecer en "los chats donde el userbot está ahora" (esa comprobación
simplemente no aplica a un chat que el userbot nunca tuvo). Una lista de
excepciones puntual (protegé estos IDs específicos) tapó el síntoma pero no
la causa: la fila no sabía de qué proceso había salido. La solución de
fondo fue una columna `source` (`"userbot"` o `"bot"`) en la propia fila —
la limpieza automática solo poda filas con `source="userbot"`, nunca las de
`source="bot"`, sin importar si en ese momento el chat sigue "en uso" o no.
**Lección**: cuando una tabla se alimenta desde dos fuentes con reglas de
validez distintas, la fila necesita saber de qué fuente vino — una lista de
excepciones es un parche que hay que mantener sincronizado a mano cada vez
que cambia qué está "en uso"; una columna de origen resuelve la pregunta
para siempre sin mantenimiento.

## Un throttle aplicado de un solo lado de dos procesos simétricos es una brecha, no una cobertura parcial

**Problema**: un pico de backfill grande disparó `Flood control exceeded`
real de Telegram en los envíos del lado bot.

**Causa**: el throttle de "no mandar todo en ráfaga" ya existía del lado
userbot (para las descargas), pero nadie lo había replicado del lado bot
(para los envíos) — se pensó como si fuera la misma protección cubriendo
los dos, cuando en realidad cada lado hace su propia llamada a la API de
Telegram y necesita su propio límite.

**Lección**: en una arquitectura de dos procesos, cualquier regla de
"esto no puede pasar en ráfaga" que aplique a un tipo de llamada a la API
de Telegram se evalúa **por proceso que hace esa llamada**, no una vez
para todo el sistema — verificar explícitamente que el lado simétrico
(si el userbot tiene throttle en descargas, ¿lo tiene el bot en envíos?)
también lo tenga, en vez de asumir que ya está cubierto porque el
concepto general ya se aplicó en algún lado.

## Una función que inserta a partir de un tap de UI necesita ser idempotente de entrada

**Problema**: dos taps al mismo botón (crear una regla sobre un chat)
terminaron mandando el mismo contenido duplicado al destino.

**Causa**: la función que inserta la regla nunca comprobaba si ya existía
una igual — cada tap insertaba una fila nueva sin excepción, y cada regla
duplicada generaba su propio envío.

**Solución/regla general**: cualquier función que inserte una fila a
partir de una acción de UI repetible (un tap, un comando) se pregunta de
entrada "¿qué pasa si esto se llama dos veces con los mismos
argumentos?" — si la respuesta razonable es "no debería pasar nada
distinto la segunda vez", la función busca una fila existente con esos
mismos valores antes de insertar, en vez de confiar en que la interfaz
nunca vaya a dejar tocar el mismo botón dos veces (una conexión lenta, un
doble tap accidental, o simplemente correr el mismo comando dos veces
alcanzan para romper ese supuesto).

## Un error HTTP genérico suele tener un límite documentado detrás — y lo barato de chequear se checkea antes de lo caro

**Problema**: varias tareas de reenvío de multimedia fallaban con `HTTP
Client says - Request Entity Too Large`, sin que fuera obvio si era un bug
propio o algo de Telegram deteccionando "comportamiento automatizado".

**Causa**: no era ninguna de las dos cosas raras — es un límite duro y
documentado de la Bot API: `sendPhoto`/`sendVideo`/etc. rechazan cualquier
archivo de más de 50MB por multipart, siempre, sin importar reintentos. El
proceso que descarga el archivo (en una arquitectura de dos procesos, el
lado MTProto, que no tiene ese límite) lo bajaba completo — gastando
minutos de descarga y disco — y recién el lado que lo sube vía Bot API
descubría el límite al fallar.

**Solución**: chequear el dato barato (`message.file.size`, que Telethon
expone sin descargar nada) **antes** de la operación cara
(`client.download_media()`). Si supera el límite conocido, cortar ahí con
un error explícito ("archivo de X MB supera el límite de 50MB") en vez de
descargar igual y fallar después con un HTTP genérico.

**Lección con dos partes**: (1) un error HTTP genérico (413, "Request
Entity Too Large", etc.) casi siempre tiene un límite documentado de la
API detrás — revisar la documentación oficial antes de sospechar un bug
propio o una detección de automatización. (2) cuando el dato que evitaría
un trabajo condenado a fallar está disponible sin costo antes de la parte
cara de la operación, usarlo para salir temprano. Efecto colateral real de
este mismo fix: un síntoma reportado por separado como "el bot se
freezea" resultó ser exactamente esto — descargas de varios minutos de
archivos condenados a fallar, no un deadlock ni un bug de concurrencia. Un
síntoma de "freeze" vale la pena confirmarlo contra I/O lento antes de
buscar un bug de sincronización que puede no existir.

## Una respuesta de la API de Telegram puede ganar una forma nueva sin que cambie la firma del método

**Problema**: unirse a un chat vía link de invitación (`t.me/+...`) rompía
con `'ChatInviteJoinResultOk' object has no attribute 'chats'`.

**Causa**: el método de Telethon usado para importar una invitación puede
devolver un wrapper con variantes (`ChatInviteJoinResultOk`, que trae los
datos reales adentro de un atributo anidado; otra variante para un flujo de
unión distinto) en vez de la respuesta "plana" de siempre — el código
accedía a `.chats` directo, asumiendo la forma vieja.

**Solución/regla general**: `getattr(resultado, "atributo", None)` en vez
de acceso directo cuando se lee un campo de una respuesta de la API que
"siempre tuvo esa forma", con un fallback seguro (no un `AttributeError`
que tira la tarea entera a estado de error) para cualquier variante no
prevista. Telegram puede agregar variantes nuevas a una respuesta ya
existente sin que la librería cambie de versión mayor ni la documentación
lo anuncie con fanfarria — el código no puede asumir que la forma
observada hoy es la única posible para siempre.

## Un `except Exception` genérico silencia justo la excepción que ya sabías manejar

**Problema**: un `FloodWaitError` de Telegram al consultar un link de
invitación quedó guardado como error permanente, sin ningún reintento —
exactamente lo que la guía de este skill dice que hay que dormir y
reintentar, no descartar.

**Causa**: el código envolvía la consulta en un `except Exception`
genérico que guardaba `str(exc)` tal cual como mensaje de error — sin
distinguir `FloodWaitError` (que trae `.seconds` y tiene un manejo
conocido) de cualquier otra falla real.

**Solución**: capturar `FloodWaitError` en su propio `except`, **antes**
del genérico, dormir `await asyncio.sleep(exc.seconds)` (nunca
`time.sleep()` — bloquearía el proceso entero, no solo esta consulta) y
reintentar una sola vez; recién si el segundo intento también falla,
guardarlo como error real.

**Lección**: antes de escribir un `except Exception` amplio, repasar qué
excepciones específicas puede tirar esa llamada según la documentación de
la librería — si alguna de ellas ya tiene un manejo conocido en otra parte
del proyecto (acá, `FloodWaitError` en el resto del código), capturarla
aparte y primero. Un `except Exception` genérico no es incorrecto por sí
solo, pero puesto antes de un caso que ya sabías resolver, lo silencia.

## Cuando dos tablas modelan la misma decisión en capas distintas, un borrado en una tiene que propagar a la otra

**Problema**: sacar un chat de la lista de "destinos por defecto" del
espejo (un toggle en el panel) no detenía los envíos ahí — el bot le
siguió mandando contenido igual, con capturas mostrando el error real
`Bad Request: chat not found` para ese mismo destino en tareas posteriores.

**Causa**: la tabla de "destinos por defecto" solo decide a dónde se manda
un origen **nuevo** de ahí en adelante — las reglas de espejo ya creadas
mientras ese destino estuvo activo viven en una tabla aparte, y son esas
reglas las que de verdad rigen el comportamiento en tiempo de ejecución.
Sacar algo de la primera tabla nunca tocaba la segunda.

**Solución**: la función que saca un destino de la lista de "por defecto"
borra también, en la misma operación, cualquier regla de espejo ya creada
que apunte a ese destino — nunca las dos tablas se editan por separado
desde dos puntos de entrada distintos sin sincronizarlas.

**Lección**: cuando el usuario ve una tabla como "la fuente de la
decisión" (acá, el toggle de destinos) pero el comportamiento real lo
ejecuta otra tabla derivada de esa (las reglas ya creadas), sacar algo de
la primera sin propagar a la segunda hace que la interfaz mienta: dice que
se sacó, pero nada cambió en la práctica. Buscar explícitamente "¿qué otra
tabla se llenó a partir de esta decisión mientras estuvo vigente?" antes
de dar por resuelta una función de borrado/desactivación.

## Canal de tareas entre procesos: una tabla, no una llamada directa

Cuando dos procesos separados (bot Bot API + userbot MTProto) necesitan
pedirse cosas entre sí — no solo leer el mismo dato, sino *accionar* algo
del otro lado (que el userbot se una a un chat, que el bot mande un
mensaje) — la forma que se mantuvo estable en producción es una tabla de
tareas por dirección, nunca una llamada directa (HTTP interno, socket,
etc.) entre los dos procesos:

```sql
CREATE TABLE userbot_tasks (   -- canal bot -> userbot
    id INTEGER PRIMARY KEY,
    task_type TEXT NOT NULL,   -- 'join_chat', 'send_message', ...
    payload TEXT NOT NULL,     -- JSON serializado
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | done | error
    result TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
);
-- 'bot_tasks' (canal userbot -> bot) es la misma forma, en la dirección
-- opuesta -- si hace falta un tercer canal, reusar esta forma tal cual.
```

Cada proceso corre un poller propio (`asyncio` + `sleep` corto, 5s es un
valor real que funcionó bien) que consume **solo** la tabla en la
dirección que le toca leer, marca `status` y guarda `result` — nunca deja
una excepción sin loggear ni sin marcar la tarea como error. Esto evita
por completo la necesidad de que un proceso exponga un puerto/endpoint
interno para que el otro lo llame, y sigue funcionando igual si en algún
momento cualquiera de los dos procesos se reinicia mientras el otro sigue
vivo — la tarea simplemente espera en la tabla.
