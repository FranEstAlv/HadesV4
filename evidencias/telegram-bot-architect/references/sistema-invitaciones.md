# Sistema de invitaciones de un solo uso, autoservicio, configurable desde el panel

**Sugerencia para cuando el bot necesita repartir invitaciones — no una
forma obligatoria de hacerlo.** Si tu bot solo necesita un link fijo de
invitación (el que ya trae cualquier grupo/canal de Telegram) alcanza con
eso, sin escribir nada de esto. Este patrón es para cuando hace falta
distinguir quién puede generar qué link, o dar links de un solo uso.

Patrón verificado en producción para dos necesidades distintas que suelen
confundirse en el mismo comando y no deberían:

1. **Invitación al chat principal / a un chat con ingreso restringido** —
   la genera solo un admin, desde el panel administrativo.
2. **Invitación de autoservicio a chats secundarios** — cualquier miembro
   que ya cumple una condición (membresía activa, ya es miembro del chat
   principal, lo que aplique en tu bot) se la genera solo, sin intervención
   de un admin, para cualquiera de los chats secundarios que el admin haya
   marcado como disponibles.

Separarlos evita el error de exponer por accidente la generación de links
de un chat sensible a cualquiera que use el comando de autoservicio.

## El link en sí: dejá que Telegram lo invalide solo

```python
async def generar_link(bot, chat_id: int, creado_por: int) -> str:
    """El bot necesita ser admin del chat con permiso de invitar --
    si no, TelegramError/BadRequest, dejalo propagar y que el llamador
    decida cómo mostrarlo (no lo atrapes silenciosamente acá)."""
    enlace = await bot.create_chat_invite_link(chat_id=chat_id, member_limit=1)
    await guardar_invitacion(chat_id=chat_id, invite_link=enlace.invite_link, creado_por=creado_por)
    return enlace.invite_link
```

`member_limit=1` le alcanza a Telegram para invalidar el link después del
primer ingreso — no hace falta un listener de `chat_member` que lo
revoque ni un job que lo rote a mano. Guardar cada link generado (chat,
quién lo pidió/generó, cuándo) es la única parte que sí tenés que hacer
vos, y sirve tanto de auditoría como para mostrar "tu último link" sin
tener que volver a pedirle uno nuevo a Telegram cada vez.

## Autoservicio configurable desde el panel, no una lista fija en código

La parte que lo hace mantenible es que el comando de autoservicio nunca
tiene una lista de chats hardcodeada — lee de una tabla que el panel
administrativo edita (activar/desactivar cuáles chats se ofrecen):

```python
CB_INVITACIONES = "invitaciones"


def _cb(*partes) -> str:
    return ":".join((CB_INVITACIONES, *(str(p) for p in partes)))


async def _cumple_condicion(user_id: int) -> bool:
    """Acá va la condición real de tu bot -- membresía activa, ya es
    miembro del chat principal, rol mínimo, lo que corresponda. Chequeala
    contra el dato vivo (ej. fecha de vencimiento), no contra un booleano
    cacheado que puede haber quedado desactualizado."""
    ...


async def _vista_lista(nota: str | None = None) -> tuple[str, InlineKeyboardMarkup]:
    disponibles = await listar_chats_disponibles()  # WHERE activo = true, editado desde el panel
    lineas = ["🔗 <b>Invitaciones disponibles</b>", ""]
    lineas.append(nota if nota else "¿A dónde quieres entrar? Te damos un enlace de un solo uso.")
    texto = "\n".join(lineas)

    filas = [[InlineKeyboardButton(c["nombre"], callback_data=_cb("link", c["chat_id"]))] for c in disponibles]
    teclado = agregar_boton_cerrar(InlineKeyboardMarkup(filas))
    return texto, teclado


async def invitaciones_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not await _cumple_condicion(user.id):
        await update.effective_message.reply_text("🔒 Todavía no cumples la condición para esto.")
        return
    texto, teclado = await _vista_lista()
    await update.effective_message.reply_text(texto, parse_mode=ParseMode.HTML, reply_markup=teclado)


async def invitaciones_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if not await _cumple_condicion(user.id):
        await query.answer("Ya no cumples la condición.", show_alert=True)
        return
    await query.answer()

    chat_id = int(query.data.split(":")[2])
    try:
        link = await generar_link(context.bot, chat_id, user.id)
    except TelegramError as exc:
        texto, teclado = await _vista_lista(f"⚠️ No se pudo crear el enlace: {exc}")
        await _editar_seguro(query, texto, teclado)
        return

    # Ver más abajo por qué esto va SIN <code> alrededor del link.
    texto, teclado = await _vista_lista(f"Úsalo una sola vez:\n{html.escape(link)}")
    await _editar_seguro(query, texto, teclado)
```

Qué chats aparecen en `listar_chats_disponibles()` se administra por
completo desde el panel (`references/panel-admin-paginacion-roles.md`):
un admin activa/desactiva cada chat con un toggle, sin tocar código ni
redeployar, para agregar o quitar de dónde el bot reparte invitaciones.

Tanto la generación admin-only (chat principal / restringido) como esta
de autoservicio **reusan el mismo `_editar_seguro`** del panel — pedir un
link nuevo actualiza el mismo mensaje en vez de acumular uno nuevo por
cada tap, exactamente el mismo soporte de "editar el mensaje de
respuesta" que ya usa el resto del panel.

## El link va en texto plano, nunca en monospace — si el chat tiene protegido el contenido, esto no es cosmético

Este es el detalle que cuesta descubrir si no te pasó ya: si el chat
donde el bot responde tiene activado "restringir guardado de contenido"
(`protect_content`), un link mandado como `<code>{link}</code>` (monospace)
dejaba de servir — la acción por defecto sobre una entidad `code` es
*copiar*, y copiar es exactamente lo que la protección de contenido
bloquea. El usuario se queda con un texto que no puede copiar y que
tampoco es un link real (una entidad `code` no es una entidad `url`, así
que tampoco se abre solo al tocarlo).

La solución es mandarlo como texto plano, sin ninguna etiqueta alrededor:

```python
# Mal: bajo protección de contenido, esto no se puede copiar NI abrir.
texto = f"Tu link: <code>{html.escape(link)}</code>"

# Bien: Telegram autodetecta la URL y la vuelve una entidad 'url' real
# -- tocarla la ABRE directo, acción que la protección de contenido no
# bloquea (solo bloquea copiar/reenviar, no abrir un link).
texto = f"Tu link:\n{html.escape(link)}"
```

`html.escape()` sigue haciendo falta (por si el link alguna vez trajera
caracteres que rompan el parseo HTML), pero sin envolverlo en `<code>` ni
`<a href=...>` — la autodetección de Telegram sobre texto plano alcanza y
es justamente lo que evita el problema. Si tu bot ya usa `parse_mode`
HTML globalmente (ver `references/panel-admin-paginacion-roles.md` y
`docs`/lecciones de aiogram sobre `DefaultBotProperties`), este es el
único lugar puntual donde conviene pasar el texto sin marcado extra
alrededor del link específicamente — el resto del mensaje puede seguir
usando HTML sin problema.
