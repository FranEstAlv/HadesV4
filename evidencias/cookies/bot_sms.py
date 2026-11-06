from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    filters
)
ESPERANDO_ID, ESPERANDO_CANTIDAD = range(2)

import os
from dotenv import load_dotenv
import requests
import sqlite3

DB_FILE = "usuarios.db"


def inicializar_db():
    conexion = sqlite3.connect(DB_FILE)

    conexion.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            creditos INTEGER NOT NULL DEFAULT 0
        )
    """)

    conexion.commit()
    conexion.close()


def registrar_usuario(user_id, username):
    conexion = sqlite3.connect(DB_FILE)

    conexion.execute(
        """
        INSERT OR IGNORE INTO usuarios
        (user_id, username, creditos)
        VALUES (?, ?, 0)
        """,
        (user_id, username)
    )

    conexion.execute(
        """
        UPDATE usuarios
        SET username = ?
        WHERE user_id = ?
        """,
        (username, user_id)
    )

    conexion.commit()
    conexion.close()


def obtener_creditos(user_id):
    conexion = sqlite3.connect(DB_FILE)

    resultado = conexion.execute(
        "SELECT creditos FROM usuarios WHERE user_id = ?",
        (user_id,)
    ).fetchone()

    conexion.close()

    if resultado is None:
        return 0

    return resultado[0]


def cambiar_creditos(user_id, cantidad):
    conexion = sqlite3.connect(DB_FILE)

    conexion.execute(
        """
        UPDATE usuarios
        SET creditos = creditos + ?
        WHERE user_id = ?
        """,
        (cantidad, user_id)
    )

    conexion.commit()
    conexion.close()

load_dotenv("bot_sms.env")
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
print("valor token:")
print(TOKEN)

ADMIN_ID = 7902714031
#6249219406

HEROSMS_API_KEY = os.environ.get("HERO_API_KEY")
print("valor api:")
print(HEROSMS_API_KEY)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    botones = [
        [
            InlineKeyboardButton(
                "🎁 Amazon",
                callback_data="menu_amazon"
            ),
            InlineKeyboardButton(
                "📱 Gates",
                callback_data="menu_gates"
            )
        ],
        [
            InlineKeyboardButton(
                "💳 Servicios",
                callback_data="menu_varios"
            ),
            InlineKeyboardButton(
                "💬 SMS",
                callback_data="menu_sms"
            )
        ],
        [
            InlineKeyboardButton(
                "📗 Ayuda",
                callback_data="menu_ayuda"
            ),
            InlineKeyboardButton(
                "⚙ Configuracion",
                callback_data="menu_config"
            )
        ],
        [
            InlineKeyboardButton(
                "👨‍🦰 Perfil y Saldos",
                callback_data="perfil"
            )
        ],
        [
            InlineKeyboardButton(
                "🏆 Menu administrador",
                callback_data="menu_admin"
            )
        ]
    ]

    teclado = InlineKeyboardMarkup(botones)

    await update.message.reply_text(
        "👺<< Bienvenido a Anonymusclan >>👺\n"
        "**Panel Principal**\n"
        "Selecciona una opción:",
        reply_markup=teclado,
        parse_mode="Markdown"
    )

async def amazon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "sin_username"

    registrar_usuario(user_id, username)

    creditos = obtener_creditos(user_id)

    await update.message.reply_text(
        f"💰 Tu saldo\n"
        f"🪙 Créditos: {creditos} \n" 
        f"🎁🎁🎁 Bienvenido a Anonymusclan AMAZON 🎁🎁🎁 \n"
        f"------------------------------------------ \n"
        f"💳 Comando: /check 4532640527811643|12|2025|123 + cookie \n\n"
        f"Ejemplo:"
        f"/check 4532640527811643|12|2025|123 \n"
        f"4111111111111111|01|2026|456  \n"
        f"....  \n"
        f"tu_cookie_de_amazon_aqui \n\n"
        f"*** El bot generará y procesará y te dará un reporte final. ***"
    )

async def gates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "sin_username"

    registrar_usuario(user_id, username)

    creditos = obtener_creditos(user_id)

    await update.message.reply_text(
        f"💰 Tu saldo\n"
        f"🪙 Créditos: {creditos} \n" 
        f"🚀🚀🚀 Bienvenido a Anonymusclan GATES 🚀🚀🚀 \n"
        f"------------------------------------------ \n"
        f"💳 Comando: /check 4532640527811643|12|2025|123 \n"
        f"tu_cookie_de_amazon_aqui \n"
        f"------------------------------------------\n"
        f"💳 Comando: /bin 4532640527811643|12|2025|20|1 \n"
        f"/bin cc[6-12 digitos] | mes[00] | año[0000] | generar [hasta 100] | monto $1 , $20 $100 \n"
        f"------------------------------------------\n"
        f"💳Comando: /stp 4532640527811643|12|2025|000 \n"
        f"/stp cc[16 digitos] | mes[00] | año[0000] | 000 \n \n"
        f"*** El bot generará y procesará y te dará un reporte final. ***"
    )

async def varios(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "sin_username"

    registrar_usuario(user_id, username)

    creditos = obtener_creditos(user_id)

    await update.message.reply_text(
        f"💰 Tu saldo\n"
        f"🪙 Créditos: {creditos} \n" 
        f"💳💳💳 Servicios Varios de Anonymusclan 💳💳💳 \n"
        f"------------------------------------------ \n"
        f"💿 Comando: /tempmail  \n"
        f"Ejemplo:"
        f"/tempmail  (Genera una correo temporal ) \n"
        f"------------------------------------------ \n\n"
        f"💿 Comando: /gendatos + PAIS \n"
        f"Ejemplo:"
        f"/gendatos  MX (Genera datos del pais solicitado) \n"
        f"------------------------------------------ \n"
        f"*** El bot generará te dará un reporte final. ***"
    )


async def saldo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "sin_username"

    registrar_usuario(user_id, username)

    creditos = obtener_creditos(user_id)

    await update.message.reply_text(
        f"👨‍🦰 Usuario: {username}\n"
        f"🔑 Id: {user_id}\n"
        f"💰 Tu saldo\n"
        f"🪙 Créditos: {creditos}"
    )

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not HEROSMS_API_KEY:
        await update.message.reply_text(
            "❌ No se encontró HEROSMS_API_KEY."
        )
        return

    try:
        respuesta = requests.get(
            "https://hero-sms.com/stubs/handler_api.php",
            params={
                "action": "getBalance",
                "api_key": HEROSMS_API_KEY
            },
            timeout=10
        )

        await update.message.reply_text(
            f"💰 HeroSMS:\n{respuesta.text}"
        )

    except requests.RequestException:
        await update.message.reply_text(
            "❌ No se pudo conectar con HeroSMS."
        )

async def consultar_saldo_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ No tienes permiso.")
        return

    if len(context.args) != 1:
        await update.message.reply_text(
            "Uso:\n/consultarsaldo ID_USUARIO"
        )
        return

    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text(
            "❌ El ID debe ser numérico."
        )
        return

    saldo = obtener_creditos(user_id)

    await update.message.reply_text(
        f"💰 Saldo del usuario\n\n"
        f"🆔 ID: {user_id}\n"
        f"🪙 Créditos: {saldo}"
    )

async def mi_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"🆔 Tu ID es: {update.effective_user.id}"
    )

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ No tienes permiso.")
        return

    teclado = [
        [
            InlineKeyboardButton("➕ Añadir créditos", callback_data="admin_add"),
            InlineKeyboardButton("➖ Quitar créditos", callback_data="admin_remove")
        ],
        [
            InlineKeyboardButton("💰 Consultar saldo", callback_data="admin_balance")
        ]
    ]

    await update.message.reply_text(
        "⚙️ Panel de administrador",
        reply_markup=InlineKeyboardMarkup(teclado)
    )

async def admin_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        await query.edit_message_text("❌ No tienes permiso.")
        return

    await query.edit_message_text(
        "💰 Para consultar el saldo de un usuario usa:\n\n"
        "/saldo ID_USUARIO"
    )

async def numero(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not HEROSMS_API_KEY:
        await update.message.reply_text(
            "❌ No se encontró HEROSMS_API_KEY."
        )
        return

    try:
        respuesta = requests.get(
            "https://hero-sms.com/stubs/handler_api.php",
            params={
                "action": "getNumbersStatus",
                "api_key": HEROSMS_API_KEY,
                "country": "52"
            },
            timeout=10
        )

        disponibilidad = respuesta.json()

        if not isinstance(disponibilidad, dict):
            await update.message.reply_text(
                "❌ HeroSMS devolvió un formato inesperado."
            )
            return

        respuesta_servicios = requests.get(
            "https://hero-sms.com/stubs/handler_api.php",
            params={
                "action": "getServicesList",
                "api_key": HEROSMS_API_KEY
            },
            timeout=10
        )

        datos_servicios = respuesta_servicios.json()
        nombres = {}

        if isinstance(datos_servicios, dict):
            lista = datos_servicios.get("services", [])

            if isinstance(lista, list):
                for servicio in lista:
                    if isinstance(servicio, dict):
                        codigo = servicio.get("code")
                        nombre = servicio.get("name")

                        if codigo and nombre:
                            nombres[codigo] = nombre

        resultados = []

        for codigo, cantidad in disponibilidad.items():
            try:
                cantidad = int(cantidad)
                nombre = nombres.get(
                    codigo,
                    "Servicio desconocido"
                )
                resultados.append(
                    (nombre, codigo, cantidad)
                )
            except (ValueError, TypeError):
                continue

        resultados.sort(
            key=lambda elemento: elemento[2],
            reverse=True
        )

        top10 = resultados[:10]

        mensaje = (
            "📱 DISPONIBILIDAD — MÉXICO\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
        )

        for posicion, datos_servicio in enumerate(
            top10,
            start=1
        ):
            nombre, codigo, cantidad = datos_servicio

            if posicion == 1:
                numero = "🥇"
            elif posicion == 2:
                numero = "🥈"
            elif posicion == 3:
                numero = "🥉"
            else:
                numero = f"{posicion}️⃣"

            mensaje += (
                f"{numero} {nombre}\n"
                f"   🔑 Código: {codigo}\n"
                f"   📦 Disponibles: {cantidad:,}\n\n"
            )

        if not top10:
            mensaje = (
                "❌ No se encontraron datos "
                "de disponibilidad."
            )

        await update.message.reply_text(mensaje)

    except ValueError:
        await update.message.reply_text(
            "❌ HeroSMS devolvió JSON inválido."
        )

    except requests.RequestException:
        await update.message.reply_text(
            "❌ No se pudo conectar con HeroSMS."
        )

async def servicios(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not HEROSMS_API_KEY:
        await update.message.reply_text(
            "❌ No se encontró HEROSMS_API_KEY."
        )
        return

    try:
        respuesta = requests.get(
            "https://hero-sms.com/stubs/handler_api.php",
            params={
                "action": "getServicesList",
                "api_key": HEROSMS_API_KEY
            },
            timeout=10
        )

        datos = respuesta.json()

        if not isinstance(datos, dict):
            await update.message.reply_text(
                "❌ HeroSMS devolvió un formato inesperado."
            )
            return

        servicios_data = datos.get("services", [])
        if not isinstance(servicios_data, list):
            await update.message.reply_text(
                "❌ No se encontró la lista de servicios."
            )
            return

        servicios = []

        for servicio in servicios_data:
            if isinstance(servicio, dict):
                codigo = servicio.get("code", "")
                nombre = servicio.get("name", "")

                if codigo and nombre:
                    servicios.append((codigo, nombre))

        if not servicios:
            await update.message.reply_text(
                "❌ No hay servicios disponibles."
            )
            return

        context.user_data["servicios"] = servicios

        await mostrar_servicios(update, context, 0)

    except ValueError:
        await update.message.reply_text(
            "❌ HeroSMS no devolvió JSON válido."
        )

    except requests.RequestException:
        await update.message.reply_text(
            "❌ No se pudo conectar con HeroSMS."
        )

async def addcreditos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ No tienes permiso.")
        return

    if len(context.args) != 2:
        await update.message.reply_text(
            "Uso:\n/addcreditos ID_USUARIO CANTIDAD"
        )
        return

    try:
        user_id = int(context.args[0])
        cantidad = int(context.args[1])

        if cantidad <= 0:
            await update.message.reply_text(
                "❌ La cantidad debe ser mayor que 0."
            )
            return

        registrar_usuario(user_id, "usuario")
        cambiar_creditos(user_id, cantidad)

        nuevo_saldo = obtener_creditos(user_id)

        await update.message.reply_text(
            f"✅ Créditos añadidos.\n\n"
            f"🆔 Usuario: {user_id}\n"
            f"🪙 Añadidos: {cantidad}\n"
            f"💰 Nuevo saldo: {nuevo_saldo}"
        )

    except ValueError:
        await update.message.reply_text(
            "❌ El ID y la cantidad deben ser números."
        )

async def admin_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        await query.edit_message_text("❌ No tienes permiso.")
        return ConversationHandler.END

    context.user_data["accion_creditos"] = "add"

    await query.edit_message_text(
        "➕ Añadir créditos\n\n"
        "Escribe el ID del usuario:"
    )
    return ESPERANDO_ID

async def admin_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        await query.edit_message_text("❌ No tienes permiso.")
        return ConversationHandler.END

    context.user_data["accion_creditos"] = "remove"

    await query.edit_message_text(
        "➖ Quitar créditos\n\n"
        "Escribe el ID del usuario:"
    )
    return ESPERANDO_ID

async def recibir_id_creditos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END

    try:
        user_id = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text(
            "❌ El ID debe ser numérico.\n\n"
            "Escribe el ID nuevamente:"
        )
        return ESPERANDO_ID

    context.user_data["creditos_user_id"] = user_id

    await update.message.reply_text(
        f"🆔 Usuario: {user_id}\n\n"
        "Ahora escribe la cantidad de créditos:"
    )
    return ESPERANDO_CANTIDAD

async def recibir_cantidad_creditos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END

    try:
        cantidad = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text(
            "❌ La cantidad debe ser un número.\n\n"
            "Escribe la cantidad nuevamente:"
        )
        return ESPERANDO_CANTIDAD

    if cantidad <= 0:
        await update.message.reply_text(
            "❌ La cantidad debe ser mayor que 0."
        )
        return ESPERANDO_CANTIDAD

    user_id = context.user_data["creditos_user_id"]
    accion = context.user_data["accion_creditos"]

    registrar_usuario(user_id, "usuario")

    saldo_actual = obtener_creditos(user_id)

    if accion == "remove":
        if cantidad > saldo_actual:
            await update.message.reply_text(
                f"❌ No puedes quitar {cantidad} créditos.\n\n"
                f"🪙 Saldo actual: {saldo_actual}"
            )
            context.user_data.clear()
            return ConversationHandler.END

        cantidad = -cantidad

    cambiar_creditos(user_id, cantidad)

    nuevo_saldo = obtener_creditos(user_id)

    if accion == "add":
        await update.message.reply_text(
            f"✅ Créditos añadidos.\n\n"
            f"🆔 Usuario: {user_id}\n"
            f"➕ Añadidos: {cantidad}\n"
            f"💰 Nuevo saldo: {nuevo_saldo}"
        )
    else:
        await update.message.reply_text(
            f"✅ Créditos retirados.\n\n"
            f"🆔 Usuario: {user_id}\n"
            f"➖ Retirados: {abs(cantidad)}\n"
            f"💰 Nuevo saldo: {nuevo_saldo}"
        )

    context.user_data.clear()

    return ConversationHandler.END

async def menu_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    if query.data == "menu_amazon":
        await query.edit_message_text(
            "📱 Para consultar la verficacion con amazon escribe:\n\n"
            "/amazon"
        )

    elif query.data == "menu_gates":
        await query.edit_message_text(
            "📋 Para consultar los gates escribe:\n\n"
            "/gates"
        )

    elif query.data == "menu_sms":
        await query.edit_message_text(
            "📋 Para consultar los servicios escribe:\n\n"
            "/sms"
        )
        
    elif query.data == "menu_varios":
        await query.edit_message_text(
            "📋 Para consultar los servicios varios escribe:\n\n"
            "/varios"
        )

    elif query.data == "menu_ayuda":
        await query.edit_message_text(
            "📋 Para consultar la ayuda escribe:\n\n"
            "/ayuda"
        )

    elif query.data == "menu_config":
        await query.edit_message_text(
            "📋 Para consultar la configuracion escribe:\n\n"
            "/config"
        )

    elif query.data == "menu_perfil":
        await query.edit_message_text(
            "📋 Para consultar tu información escribe:\n\n"
            "/perfil"
        )

    elif query.data == "menu_admin":
        await query.edit_message_text(
            "💰 Para consultar el menu admin escribe:\n\n"
            "/admin"
        )

async def mostrar_servicios(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    pagina: int
):
    servicios = context.user_data.get("servicios", [])

    por_pagina = 15
    total_paginas = (len(servicios) + por_pagina - 1) // por_pagina

    if total_paginas == 0:
        mensaje = "❌ No hay servicios disponibles."
        teclado = None
    else:
        inicio = pagina * por_pagina
        fin = inicio + por_pagina

        pagina_actual = servicios[inicio:fin]

        mensaje = (
            f"📋 Servicios SMS disponibles\n"
            f"Página {pagina + 1}/{total_paginas}\n\n"
            "Selecciona un servicio:\n"
        )

        botones_servicios = []

        for codigo, nombre in pagina_actual:
            mensaje += f"• {codigo} — {nombre}\n"

            botones_servicios.append([
                InlineKeyboardButton(
                    nombre,
                    callback_data=f"serv_select:{codigo}"
                )
            ])

        botones_navegacion = []

        if pagina > 0:
            botones_navegacion.append(
                InlineKeyboardButton(
                    "⬅️ Anterior",
                    callback_data=f"serv_prev:{pagina}"
                )
            )

        if pagina < total_paginas - 1:
            botones_navegacion.append(
                InlineKeyboardButton(
                    "Siguiente ➡️",
                    callback_data=f"serv_next:{pagina}"
                )
            )

        teclado_lista = botones_servicios

        if botones_navegacion:
            teclado_lista.append(botones_navegacion)

        teclado = InlineKeyboardMarkup(teclado_lista)

    if update.callback_query:
        await update.callback_query.edit_message_text(
            mensaje,
            reply_markup=teclado
        )
    else:
        await update.message.reply_text(
            mensaje,
            reply_markup=teclado
        )

async def solicitar_numero(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    codigo = context.user_data.get("servicio_seleccionado")

    if not codigo:
        await query.edit_message_text(
            "❌ No hay ningún servicio seleccionado."
        )
        return

    precios = {
        "xk": 12,
        "wa": 20,
        "tg": 20,
        "ig": 15,
        "aez": 15,
        "am": 10
    }

    if codigo not in precios:
        await query.edit_message_text(
            "❌ Servicio no configurado."
        )
        return

    precio = precios[codigo]
    saldo = obtener_creditos(query.from_user.id)

    if saldo < precio:
        await query.edit_message_text(
            f"❌ No tienes suficientes créditos.\n\n"
            f"💳 Precio: {precio}\n"
            f"🪙 Tu saldo: {saldo}"
        )
        return

    await query.edit_message_text(
            "⏳ Solicitando número a HeroSMS..."
    )
    
    try:
        respuesta = requests.get("https://hero-sms.com/stubs/handler_api.php",
            params={
                "action": "getNumber",
                "api_key": HEROSMS_API_KEY,
                "service": codigo,
                "country": 54
            },
            timeout=10
        )

        resultado = respuesta.text.strip()
        print("HeroSMS getNumber:", resultado)

        partes = resultado.split(":")
        print("RESPUESTA: ", partes)

        if len(partes) >= 3 and partes[0] == "ACCESS_NUMBER":
            access_id = partes[1]
            numero = partes[2] 
            #numero = numero[-10:]
            
            #print("Número recibido:", repr(numero))
            #print("Cantidad de dígitos:", len(numero))

            #context.user_data["access_id"] = access_id
            #context.user_data["numero"] = numero
            #context.user_data["servicio_codigo"] = codigo

            print("Número recibido:", repr(numero))
            print("Cantidad de dígitos:", len(numero))

            context.user_data["access_id"] = access_id
            context.user_data["numero"] = numero
            context.user_data["servicio_codigo"] = codigo

            cambiar_creditos(
                query.from_user.id,
                -precio
            )

            teclado_sms = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔄 Consultar SMS",
                        callback_data="consultar_sms"
                    )
                ]
            ])

            await query.edit_message_text(
                f"✅ Número obtenido\n\n"
                f"📱 Número: {numero}\n"
                f"🔑 ID: {access_id}\n"
                f"💳 Costo: {precio} créditos\n\n"
                f"⏳ Esperando SMS...",
                reply_markup=teclado_sms
            )

        elif resultado.startswith("NO_NUMBERS"):
                await query.edit_message_text(
                    "❌ No hay números disponibles para este servicio."
                )

        elif resultado.startswith("NO_BALANCE"):
                await query.edit_message_text(
                    "❌ La cuenta de HeroSMS no tiene saldo suficiente."
                )

        elif resultado.startswith("BAD_KEY"):
                await query.edit_message_text(
                    "❌ La API key de HeroSMS no es válida."
                )

        else:
                await query.edit_message_text(
                    f"❌ HeroSMS respondió:\n\n{resultado}"
                )

    except requests.RequestException as e:
           print("Error HeroSMS:", e)

           await query.edit_message_text(
                "❌ No se pudo conectar con HeroSMS."
            )
   
async def consultar_sms(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    access_id = context.user_data.get("access_id")

    print("ID:")
    print(access_id)

    if not access_id:
        await query.edit_message_text(
            "❌ No hay una activación activa."
        )
        return

    try:
        respuesta = requests.get(
            "https://hero-sms.com/stubs/handler_api.php",
            params={
                "action": "getStatus",
                "api_key": HEROSMS_API_KEY,
                "id": access_id
            },
            timeout=10
        )

        resultado = respuesta.text.strip()

        print("*HeroSMS getStatus:", resultado)

        if resultado == "STATUS_WAIT_CODE":
            await query.edit_message_text(
                "⏳ Todavía no ha llegado ningún SMS.\n\n"
                "🔄 Pulsa nuevamente para consultar."
            )

        elif resultado.startswith("STATUS_OK:"):
            codigo_sms = resultado.split(":", 1)[1]

            await query.edit_message_text(
                f"📩 ¡SMS recibido!\n\n"
                f"🔐 Código: {codigo_sms}"
            )

        elif resultado.startswith("STATUS_CANCEL"):
            await query.edit_message_text(
                "❌ La activación fue cancelada."
            )

        else:
            await query.edit_message_text(
                f"📡 Estado de HeroSMS:\n\n{resultado}"
            )

    except requests.RequestException as e:
        print("Error HeroSMS getStatus:", e)

        await query.edit_message_text(
            "❌ No se pudo consultar HeroSMS."
        )

async def servicio_seleccionado(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    codigo = query.data.split(":", 1)[1]

    precios = {
        "xk": 12,
        "wa": 20,
        "tg": 20,
        "ig": 15,
        "aez": 15,
        "am": 10
    }

    nombres = {
        "xk": "Didi",
        "wa": "WhatsApp",
        "tg": "Telegram",
        "ig": "Instagram",
        "aez": "Shein",
        "am": "Amazon"
    }

    if codigo not in precios:
        await query.edit_message_text(
            "❌ Este servicio todavía no está configurado."
        )
        return

    precio = precios[codigo]
    nombre = nombres[codigo]

    saldo = obtener_creditos(query.from_user.id)

    if saldo < precio:
        await query.edit_message_text(
            f"❌ Créditos insuficientes.\n\n"
            f"📱 Servicio: {nombre}\n"
            f"💳 Precio: {precio} créditos\n"
            f"🪙 Tu saldo: {saldo} créditos\n\n"
            f"Te faltan {precio - saldo} créditos."
        )
        return

    context.user_data["servicio_seleccionado"] = codigo
    context.user_data["precio_servicio"] = precio

    boton = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📱 Solicitar número",
                callback_data="solicitar_numero"
            )
        ]
    ])

    await query.edit_message_text(
        f"✅ Servicio seleccionado\n\n"
        f"📱 Servicio: {nombre}\n"
        f"🔑 Código: {codigo}\n"
        f"💳 Precio: {precio} créditos\n"
        f"🪙 Tu saldo: {saldo} créditos\n\n"
        f"Pulsa el botón para solicitar un número.",
        reply_markup=boton
    )

async def servicios_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    pagina = int(query.data.split(":")[1])

    if query.data.startswith("serv_prev:"):
        pagina -= 1
    elif query.data.startswith("serv_next:"):
        pagina += 1

    await mostrar_servicios(update, context, pagina)

def main():
    inicializar_db()

    if not TOKEN: 
        print("❌ Falta TELEGRAM_BOT_TOKEN.")
        return
    if not HEROSMS_API_KEY:
        print("⚠️ Falta HEROSMS_API_KEY.")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        CommandHandler("gates", gates)
    )

    app.add_handler(
        CommandHandler("amazon", amazon)
    )

    app.add_handler(
        CommandHandler("varios", varios)
    )

    app.add_handler(
        CommandHandler("saldo", saldo)
    )

    app.add_handler(
        #CommandHandler("perfil", saldo)
        #CallbackQueryHandler(
        #    saldo,
        #    pattern=r"^perfil$"
        #)
    )

    app.add_handler(
        CommandHandler("balance", balance)
    )

    app.add_handler(
        CommandHandler("numero", numero)
    )

    app.add_handler(
        CommandHandler("sms", servicios)
    )

    app.add_handler(
        CallbackQueryHandler(
            menu_callback,
            pattern=r"^menu_"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            servicios_callback,
            pattern=r"^serv_(prev|next):"
        )
    )

    app.add_handler(
        CommandHandler("consultarsaldo", consultar_saldo_admin)
    )

    app.add_handler(
        CommandHandler("miid", mi_id)
    )

    app.add_handler(
        CommandHandler("addcreditos", addcreditos)
    )

    app.add_handler(
        CommandHandler("admin", admin)
    )
    app.add_handler(
        CallbackQueryHandler(
            servicios_callback,
            pattern=r"^serv_(prev|next):"
        )
    )
    
    app.add_handler(
        CallbackQueryHandler(
            servicio_seleccionado,
            pattern=r"^serv_select:"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            solicitar_numero,
            pattern=r"^solicitar_numero$"
        )
    )
    app.add_handler(
        CallbackQueryHandler(
             consultar_sms,
             pattern=r"^consultar_sms$"
        )
    )

    conv_creditos = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(
                admin_add,
                pattern="^admin_add$"
            ),
            CallbackQueryHandler(
                admin_remove,
                pattern="^admin_remove$"
            )
        ],
        states={
            ESPERANDO_ID: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    recibir_id_creditos
                )
            ],
            ESPERANDO_CANTIDAD: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    recibir_cantidad_creditos
                )
            ]
        },
        fallbacks=[]
    )

    app.add_handler(conv_creditos)

    print("🤖 Bot iniciado...")
    app.run_polling()


if __name__ == "__main__":
    main()
