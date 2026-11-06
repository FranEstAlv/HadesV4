# Lecciones reales: Mictlan y ALFA-1

Todo lo de este archivo pasó de verdad, en producción, no son escenarios
hipotéticos. Se documentan acá porque son exactamente el tipo de problema
que la teoría de una librería no te avisa que vas a tener.

## 1. El raid de userbots que tumbó un bot por falta de cola/límite de tasa

**Qué pasó**: un bot de Telegram (ALFA-1) sufrió una invasión masiva de 192
cuentas reales de Telegram automatizadas (userbots vía Telethon/Pyrogram,
**no** bots de la API — Telegram no las marca como `is_bot=True`, así que
cualquier filtro basado en esa bandera es inútil contra este vector) en 4
minutos 47 segundos. El sistema de verificación de miembros del bot
procesaba cada ingreso con una llamada **síncrona, sin cola ni límite de
tasa**, a la API de Telegram (mute + envío de mensaje por cada ingreso).
Con ~190 ingresos en minutos, esas llamadas chocaron contra el
flood-control de Telegram y **el bot completo quedó saturado y dejó de
responder** — requirió reinicio manual. El resultado: 101 de las 192
cuentas quedaron dentro del grupo con permisos normales, sin que el bot
hubiera intentado nada con ellas.

**La causa raíz tenía dos partes independientes**:
1. Ningún mecanismo de cola/throttle proactivo en las llamadas de
   moderación por ingreso — el diseño asumía volumen bajo y nunca se
   probó bajo ráfaga.
2. No existía ningún circuito de contención automática ante entrada
   masiva (tipo "si entran X usuarios en Y segundos, cerrar el grupo
   solo") — la única defensa era un comando manual, que no sirvió porque
   el bot ya estaba saturado cuando alguien intentó usarlo.

**Qué hacer distinto** (aplicable a cualquier librería, no es específico
de una):
- Cualquier acción de moderación disparada por evento de alto volumen
  (ingreso masivo, ráfaga de mensajes) va a una cola interna consumida
  por una sola tarea con throttle fijo — nunca llamadas directas
  síncronas por evento.
- Un circuito de contención automática (contador tipo ventana deslizante:
  "N eventos en M segundos → actuar") es la defensa real contra un pico
  de volumen, no un comando manual que asume que el operador va a poder
  reaccionar a tiempo.
- Si tenés un segundo proceso/bot de respaldo (ver punto 2), la
  contención automática de mayor peso vive ahí, no en el bot principal —
  por la razón exacta del punto 2.

## 2. Por qué un segundo proceso separado, no una función más en el mismo bot

Directamente derivado del incidente anterior: cuando el bot principal se
satura procesando una ráfaga de eventos, **cualquier código que viva en
ese mismo proceso comparte el mismo destino** — mismo token, mismo cupo de
flood-control ante Telegram, mismo event loop. Un "circuito anti-raid"
escrito dentro del propio bot que está siendo saturado no sirve de mucho
si el proceso entero deja de responder.

La solución real que se implementó después (para un proyecto distinto,
Mictlan): un **segundo bot, con su propio token, su propia
`Application`/cliente, corriendo como proceso completamente aparte**
(comparte base de datos con el principal, nunca se comunican por HTTP
entre sí). Ese segundo proceso, al no estar involucrado en el volumen de
eventos que satura al primero, puede actuar con la cabeza fría — cerrar
un grupo, banear, avisar — exactamente cuando el principal no puede.

Esto generaliza más allá de "seguridad": cualquier funcionalidad que un
bot Bot API no pueda hacer (login de usuario, acceso a MTProto) tampoco
se resuelve forzando una librería a hacer algo para lo que no fue
diseñada — se resuelve con un proceso separado, con su propia librería y
credenciales, compartiendo estado por base de datos. Ver el Paso 0 de
`SKILL.md`.

## 3. Una bandera de encendido/apagado duplicada en dos lugares no se puede apagar de verdad

**Qué pasó**: un panel de administración tenía un botón "activar/
desactivar anti-link" para un grupo. Activarlo o desactivarlo desde el
panel **no tenía efecto real** — el filtro seguía bloqueando (o dejando
pasar) mensajes sin importar lo que el botón mostrara. Confirmado
directamente por quien lo vivió, no es una suposición.

**Causa raíz** (reconstruida contra el código real del proyecto): la
misma bandera conceptual ("¿el grupo está cerrado?", "¿el anti-link está
activo?") existía en **dos lugares de almacenamiento distintos** — una
versión "global" del bot y una versión "por grupo/scope" — con **valores
por defecto distintos entre sí**. El botón del panel escribía en un
lugar; la función que de verdad aplicaba el filtro leía del otro. El
resultado se siente exactamente como "el botón no hace nada", porque
técnicamente no le está mintiendo a nadie: sí cambió algo, solo que no lo
que se estaba leyendo.

**Regla para no repetirlo, en cualquier librería**: cada bandera de
comportamiento por chat (cerrado/abierto, filtro activo/inactivo, modo de
ingreso, lo que sea) vive en **una sola columna, de una sola tabla, con
un único getter y un único setter**. Cualquier UI, comando, o lógica
automática que necesite leer o cambiar esa bandera pasa por esas mismas
dos funciones — nunca se agrega una segunda fuente "global" o "de
respaldo" para el mismo concepto, por más razonable que parezca en el
momento de escribirla. Antes de dar por terminada una feature de
toggle, la prueba real es: activar, confirmar el efecto, desactivar
**en el mismo proceso, sin reiniciar nada**, y confirmar que el efecto
desaparece de verdad.
