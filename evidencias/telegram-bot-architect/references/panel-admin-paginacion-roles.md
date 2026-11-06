# Panel administrativo: paginación, cierre y jerarquía de roles

**Esto es una sugerencia, no una regla de construcción.** Si el bot que
estás armando no necesita un panel administrativo con listas paginadas,
varios niveles de admin, o navegación por categorías, no hace falta nada
de esto — un par de comandos sueltos puede ser lo correcto para un bot
chico. Este patrón existe para cuando el bot SÍ crece hasta necesitar esa
forma, y ya se verificó en producción, no para imponerse de entrada.

Patrón verificado en producción sobre python-telegram-bot (PTB), después de
pasar por una versión con colisiones de `callback_data` (paginación de un
listado reusada en dos contextos pisándose entre sí — mismo síntoma que en
`references/lecciones-mictlan-alfa1.md` §3, pero en la navegación, no en un
flag de configuración). Esta es la versión que resolvió el problema de raíz,
no un parche sobre la versión con empalmes. El mismo patrón aplica en
aiogram cambiando `CallbackQueryHandler`/`edit_message_text` por sus
equivalentes de `Router`.

## Idea central: nadie comparte namespace de `callback_data` por accidente

El bug de fondo de "empalmes" casi siempre es el mismo: dos vistas
distintas usan el mismo prefijo de `callback_data` para "página siguiente",
y el handler que responde no tiene forma de saber desde qué vista lo
llamaron. La solución no es "tener cuidado" — es que **cada sección del
panel sea dueña de su propio prefijo**, y que la paginación sea un helper
sin estado que arma botones pero nunca decide el prefijo:

```python
# paginacion.py — helper compartido, sin estado propio, sin opinión sobre
# el prefijo de callback_data (eso lo decide quien lo llama).
from telegram import InlineKeyboardButton

COLUMNAS = 2
FILAS_POR_PAGINA = 3
POR_PAGINA = COLUMNAS * FILAS_POR_PAGINA


def total_paginas(total_items: int) -> int:
    return max(1, -(-total_items // POR_PAGINA))


def pagina_valida(pagina: int, total_items: int) -> int:
    # Acota al rango real -- por si la lista encogió (se borró un item)
    # o llegó un número de página viejo desde un callback_data cacheado.
    return max(0, min(pagina, total_paginas(total_items) - 1))


def filas(botones: list[InlineKeyboardButton], pagina: int) -> list[list[InlineKeyboardButton]]:
    inicio = pagina * POR_PAGINA
    lote = botones[inicio : inicio + POR_PAGINA]
    return [lote[i : i + COLUMNAS] for i in range(0, len(lote), COLUMNAS)]


def fila_controles(pagina: int, total_items: int, prefijo_callback: str) -> list[InlineKeyboardButton] | None:
    """`prefijo_callback` es SIEMPRE el prefijo completo de la sección que
    llama (ej. "panel:usuarios:pagina"), nunca uno genérico compartido —
    eso es lo que evita el empalme."""
    total = total_paginas(total_items)
    if total <= 1:
        return None
    controles = []
    if pagina > 0:
        controles.append(InlineKeyboardButton("⬅️", callback_data=f"{prefijo_callback}:{pagina - 1}"))
    controles.append(InlineKeyboardButton(f"📄 {pagina + 1}/{total}", callback_data=f"{prefijo_callback}:{pagina}"))
    if pagina < total - 1:
        controles.append(InlineKeyboardButton("➡️", callback_data=f"{prefijo_callback}:{pagina + 1}"))
    return controles
```

El botón del medio (número de página) apunta a la página actual — tocarlo
solo la vuelve a mostrar. Es idempotente a propósito: así no hace falta un
`callback_data` tipo `"noop"` aparte que el router tendría que interceptar
como caso especial.

## Cerrar el panel: un solo handler global, no uno por vista

Todas las secciones del panel (usuarios, grupos, mantenimiento, lo que
sea) reusan el **mismo** botón y el **mismo** handler de cierre — no hay
un "cerrar" distinto por vista que haya que mantener sincronizado:

```python
# panel_common.py
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes

CERRAR_CALLBACK = "svc:cerrar"


def agregar_boton_cerrar(teclado: InlineKeyboardMarkup | None) -> InlineKeyboardMarkup:
    filas = list(teclado.inline_keyboard) if teclado else []
    filas.append([InlineKeyboardButton("✖️ Cerrar", callback_data=CERRAR_CALLBACK)])
    return InlineKeyboardMarkup(filas)


async def cerrar_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    try:
        await query.message.delete()  # borra el mensaje entero, no "vaciar" el teclado
    except Exception:
        pass


def install_panel_common(app) -> None:
    app.add_handler(CallbackQueryHandler(cerrar_callback, pattern=f"^{CERRAR_CALLBACK}$"))
```

Notá la decisión de **borrar el mensaje** en vez de editarlo con un teclado
vacío. Ambas opciones son válidas (la segunda es la que se documenta en
`references/lecciones-mictlan-alfa1.md` para aiogram: `edit_text(...)`
sin `reply_markup` explícito NO borra los botones existentes — Telegram
los conserva, hay que pasar `InlineKeyboardMarkup(inline_keyboard=[])` a
propósito). Borrar el mensaje evita ese detalle por completo y además
limpia el chat, que es lo que casi siempre se busca con un "cerrar".

Consejo aparte que vale para cualquier mensaje de servicio de un panel:
autoborrarlo a los N minutos con el `JobQueue` (o el scheduler que use tu
librería) si nadie lo tocó — evita que paneles administrativos abiertos y
olvidados queden pegados en un chat indefinidamente.

## Un solo dispatcher por comando, con gate de permiso al frente

Nunca un `CallbackQueryHandler` por botón. Un único handler con un patrón
amplio (`^panel:`) que valida permiso **una vez** y después despacha por
el segundo segmento del `callback_data` a la sección correspondiente, que
devuelve `(texto, teclado)` y no toca la API de Telegram directamente:

```python
# panel/__init__.py
CB_MENU = "panel:menu"


def _teclado_menu_base() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Personas", callback_data=usuarios.CB_USUARIOS)],
        [InlineKeyboardButton("🏛️ Grupos", callback_data=grupos.CB_GRUPOS)],
    ])


async def _editar_seguro(query, texto: str, teclado: InlineKeyboardMarkup) -> None:
    """Soporte para 'editar el mensaje de respuesta': Telegram levanta
    BadRequest("message is not modified") si el texto/teclado nuevo es
    idéntico al actual -- pasa seguido al re-renderizar la misma página.
    Se traga ese caso puntual y deja pasar cualquier otro error real."""
    try:
        await query.edit_message_text(texto, parse_mode=ParseMode.HTML, reply_markup=teclado)
    except BadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise
        await query.answer("Sin cambios ⏸")


async def panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return

    rol = await roles.obtener_rol(user.id)
    if not roles.alcanza_rol(rol, roles.ROLE_ADMIN):
        await query.answer()  # siempre responder el callback, aunque no tenga permiso
        return
    await query.answer()

    data = query.data or ""
    if data == CB_MENU:
        await _editar_seguro(query, TEXTO_MENU, agregar_boton_cerrar(_teclado_menu_base()))
        return

    partes = data.split(":")
    seccion = partes[1] if len(partes) > 1 else None

    if seccion == "usuarios":
        texto, teclado = await usuarios.manejar(context, partes[2:])
        await _editar_seguro(query, texto, teclado)
        return
    if seccion == "grp":
        texto, teclado = await grupos.manejar(context, user.id, partes[2:])
        await _editar_seguro(query, texto, teclado)
        return


def install_panel(app) -> None:
    app.add_handler(CommandHandler("panel", panel_command))
    app.add_handler(CallbackQueryHandler(panel_callback, pattern=r"^panel:"))
```

Cada sección (`usuarios.py`, `grupos.py`, ...) expone su propia constante
`CB_<SECCION>` y su propia función `manejar(...)`, y arma sus botones de
paginación con `paginacion.fila_controles(pagina, total, _cb("pagina"))`
donde `_cb` antepone el prefijo propio de esa sección — así una sección
nueva no puede pisar la paginación de otra ya existente, ni por accidente.

## Los botones inline SÍ pueden tener color real desde Bot API 9.4 — y los emojis animados dependen de Premium, no de la librería

Dos datos puntuales que vale la pena tener actualizados, porque los dos
cambiaron o se aclararon después de que escribiera código pensando en la
versión vieja de la Bot API:

- **Color de botón**: desde **Bot API 9.4 (9 de febrero de 2026)**,
  `InlineKeyboardButton` acepta un campo `style` opcional —
  `"danger"` (rojo), `"success"` (verde), `"primary"` (azul). Antes de esa
  fecha no existía ningún campo de color y el estado de un toggle solo se
  podía comunicar con emoji/texto (`🟢 Activado` / `🔴 Desactivado`) — ese
  patrón sigue siendo válido y más portable (no todos los clientes de
  Telegram necesariamente soportan `style` al mismo tiempo), `style` es
  una opción adicional, no un reemplazo obligatorio:
  ```python
  InlineKeyboardButton(text="🟢 Anti-link: Activado", callback_data="antilink:toggle", style="success")
  ```
  Si en algún momento leíste u obtuviste (de este mismo asistente o de
  cualquier otra fuente) la afirmación de que "la Bot API no tiene ningún
  campo de color" — era cierto antes del 9 de febrero de 2026 y dejó de
  serlo ese día. Confirmar contra el changelog oficial
  (`https://core.telegram.org/bots/api-changelog`) en vez de asumir
  cualquiera de las dos versiones de memoria.
- **Emoji animado / custom emoji** (`custom_emoji_id` en un
  `MessageEntity`): es una capacidad del **Bot API común**, no depende de
  usar aiogram en particular ni de tener un userbot MTProto de
  acompañamiento — cualquier librería de Bot API que exponga entidades de
  mensaje puede usarla. El requisito real, y la razón por la que puede
  "no funcionar" sin que sea un bug: **la cuenta dueña del bot (la que lo
  creó en @BotFather) necesita Telegram Premium** — sin eso, Telegram
  devuelve el emoji normal en vez del custom emoji pedido, silenciosamente,
  no como error. (Vía alternativa más vieja: comprar un username adicional
  para el bot en Fragment, Bot API 6.6.) Si el plan es usar un userbot
  MTProto para esto en su lugar, el mismo requisito aplica de forma
  simétrica del lado de esa cuenta — no es un atajo.

## Callback que "se siente trabado": el orden de `callback.answer()` importa, no solo que exista

El Paso 2 de este skill ya advierte "respondé el callback rápido, hacé el
trabajo pesado después" — esto es el caso real que lo confirma con un
matiz adicional. Un botón puede sentirse "caído" (spinner de carga que no
para) aunque el bot esté vivo y el mensaje sí se haya actualizado, si
`callback.answer()` se llama recién al final del handler y tarda en
llegar (o el callback ya expiró del lado de Telegram — conexión
inestable, tap que llega tarde). Eso dispara un error del tipo "query is
too old and response timeout expired" que además puede tirar un
traceback completo por evento si nada lo atrapa.

Dos ajustes, complementarios:

```python
async def safe_answer(callback: CallbackQuery, *args, **kwargs) -> None:
    """Envolver TODO callback.answer() del bot con esto. Traga
    específicamente el error de query vencida/inválida (no hay nada que
    hacer del lado del bot si Telegram ya descartó ese callback_query) --
    cualquier otro error se relanza, no se esconde."""
    try:
        await callback.answer(*args, **kwargs)
    except TelegramBadRequest as exc:
        if "query is too old" not in str(exc).lower() and "query id is invalid" not in str(exc).lower():
            raise
```

Y para cualquier handler de navegación/paginación cuyo aviso final **no**
dependa del resultado del trabajo (volver, cerrar, cambiar de página):
llamar `safe_answer(callback)` como **primera línea**, antes de ir a
buscar datos o editar el mensaje — apaga el spinner de inmediato. Un
handler cuyo aviso SÍ necesita mostrar algo calculado (por ejemplo, "se
creó la regla #N") mantiene el aviso al final, pero igual protegido por
`safe_answer`.

## Asistente de varios pasos dentro del panel: botones hasta que hace falta texto libre

Cuando crear algo necesita más de un dato (origen, destino, y una palabra
clave, por ejemplo) y todos menos uno se pueden elegir de una lista, un
asistente de 3-4 pasos dentro del propio panel evita tener que salir a un
comando con argumentos. El patrón verificado (con aiogram, usando su FSM
— `StatesGroup`) es: cada paso que se puede resolver con botones queda
como `callback_query`, y **solo el paso que necesita texto libre** (no
hay forma de pedir eso con botones) cae a un handler de mensaje:

```python
class AsistenteReglaStates(StatesGroup):
    esperando_palabra_clave = State()


@router.callback_query(F.data.startswith("reglawizard:origen:"))
async def elegir_origen(callback: CallbackQuery, state: FSMContext) -> None:
    origen_id = int(callback.data.split(":")[2])
    await state.update_data(origen_id=origen_id)
    # ... editar el mensaje mostrando la lista de destinos para elegir ...


@router.callback_query(F.data.startswith("reglawizard:destino:"))
async def elegir_destino(callback: CallbackQuery, state: FSMContext) -> None:
    destino_id = int(callback.data.split(":")[2])
    await state.update_data(destino_id=destino_id)
    await state.set_state(AsistenteReglaStates.esperando_palabra_clave)
    await callback.message.edit_text("Escribí la palabra o frase clave:")
    await callback.answer()


@router.message(AsistenteReglaStates.esperando_palabra_clave)
async def recibir_palabra_clave(message: Message, state: FSMContext) -> None:
    datos = await state.get_data()
    await crear_regla(datos["origen_id"], datos["destino_id"], message.text)
    await state.clear()
    await message.answer("Listo, la regla quedó creada.")
```

Al confirmar, el asistente llama a la **misma** función que ya usa el
comando suelto equivalente (`crear_regla(...)` acá) — un asistente es una
forma alternativa de llegar a la misma llamada, nunca un camino de
creación duplicado con su propia lógica.

## Jerarquía de roles: un nivel definido por entorno, el resto asignable desde Telegram

El patrón que evita tener que tocar código para promover a alguien:
roles ordenados, un nivel superior fijado por variable de entorno al
arrancar el proceso, y todo lo demás editable desde el propio panel.

```python
# roles.py
ROLE_MEMBER = "miembro"
ROLE_ADMIN = "administrador"
ROLE_ROOT = "root"

_ORDEN = {ROLE_MEMBER: 0, ROLE_ADMIN: 1, ROLE_ROOT: 2}


def alcanza_rol(rol: str, minimo: str) -> bool:
    return _ORDEN.get(rol, 0) >= _ORDEN.get(minimo, 0)


async def establecer_rol(user_id: int, rol: str) -> None:
    if rol not in _ORDEN:
        raise ValueError(f"rol inválido: {rol!r}")
    await db.execute("UPDATE usuarios SET rol = $1 WHERE user_id = $2", rol, user_id)


async def asegurar_root(user_id: int) -> None:
    """Llamar una vez en el arranque del proceso, con el ID leído de
    entorno (nunca hardcodeado). Idempotente y con UPSERT que fuerza el
    rol en cada arranque -- así ese ID nunca puede quedar degradado por
    un error de datos o una fila tocada a mano."""
    await db.execute(
        """
        INSERT INTO usuarios (user_id, rol) VALUES ($1, $2)
        ON CONFLICT (user_id) DO UPDATE SET rol = $2
        """,
        user_id,
        ROLE_ROOT,
    )
```

```python
# al arrancar el proceso (main.py)
await roles.asegurar_root(int(os.environ["ROOT_ID"]))
```

Promover a alguien a `ROLE_ADMIN` (o degradarlo) es una vista más del
panel — una lista de botones, uno por rol posible, que llama a
`establecer_rol(user_id, rol)` y vuelve a renderizar el detalle del
usuario. No hace falta un comando de consola ni tocar el `.env` para
cada admin nuevo — solo para ese primer nivel fijado por entorno.

**Nota de seguridad sobre cómo documentar/nombrar esto**: tratá ese nivel
superior como una jerarquía de acceso ordinaria (`root` en el sentido
Unix del término: el nivel más alto, nada más) — no como un concepto
especial a explicar o destacar. No lo expongas en ningún texto que vea un
usuario del bot (comandos, mensajes de error, logs accesibles), ni lo
menciones como algo distinto de "el nivel más alto de la jerarquía de
roles" en documentación que puedan leer terceros. El ID en sí vive
únicamente en la variable de entorno del proceso — quien administra el
despliegue es quien lo conoce, nadie más lo necesita.
