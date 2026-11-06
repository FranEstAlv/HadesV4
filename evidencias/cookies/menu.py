# ⚙️ CONFIGURACIÓN
#TOKEN = "8926930255:AAGJsMcXbynbobgqMQsFg6bH4zTgwOdF8xE"  # Reemplaza con el token de tu bot

import sys
import asyncio
import os
import logging
import random
import uuid
import sqlite3
import secrets
import re
import time
from datetime import datetime, timedelta
#from  gates_bot import gen_handler
from func_bin import get_bin_info
from func_gen import cc_generar

from gen import cc_gen, cargar_bin_db, buscar_bin

from gates_bot import (
    validate_card_format,check_card_api,format_single_result,
    cargar_proxies, cargar_tokens, cargar_combo,
    guardar_combo, guardar_live, guardar_dead)

from main_stripe import (
    cargar_proxies, cargar_tokens, cargar_combo,
    guardar_combo, guardar_live, guardar_dead,
    check_card, format_proxy, log_to_file,
    registrar_cuenta, crear_cuentas_para_check,
    MONTOS, API_NO_DISPONIBLE, TOKENS_FILE,
    procesar_tarjetas_pegadas, USED_PHONES
)

from paypal import (
    card_parse_pp, paypal_charge, get_bin_info_pp
)

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

from rich.console import Console
from rich.text import Text
from rich.panel import Panel
from rich.align import Align
from rich.table import Table


# ============================================================
# CONFIGURACIÓN
# ============================================================
# Cargar variables de entorno
#load_dotenv('bot_config.env')

CUENTAS_FIJAS = 10
TARJETAS_POR_CUENTA_FIJO = 15
ERRORES_ROTACION = 2

#TOKEN = os.getenv("8926930255:AAGJsMcXbynbobgqMQsFg6bH4zTgwOdF8xE")
TOKEN = "8926930255:AAGJsMcXbynbobgqMQsFg6bH4zTgwOdF8xE"

# ID de Telegram del administrador. Cámbialo por el tuyo.
#ADMIN_ID = int(os.getenv("ADMIN_ID", "7902714031")) # 8647596487
ADMIN_ID = 8647596487

DB_FILE = "mictlan_data.db"

if not TOKEN:
    raise RuntimeError(
        "Falta BOT_TOKEN. Configúralo en Termux antes de ejecutar el bot."
    )


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

logger = logging.getLogger(__name__)


# ============================================================
# DATOS DE USUARIOS
# ============================================================

user_data_store = {}

#def get_user(user_id):
#    if user_id not in user_data_store:
#        user_data_store[user_id] = {
#            "credits": 0,
#            "clicks": 0
#        }
#    return user_data_store[user_id]


def db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT DEFAULT '',
            credits INTEGER DEFAULT 0,
            clicks INTEGER DEFAULT 0,
            banned INTEGER DEFAULT 0,
            ban_reason TEXT DEFAULT '',
            license_until TEXT DEFAULT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS keys (
            key TEXT PRIMARY KEY,
            duration_seconds INTEGER NOT NULL,
            duration_label TEXT NOT NULL,
            created_at TEXT NOT NULL,
            used_by INTEGER DEFAULT NULL,
            used_at TEXT DEFAULT NULL,
            revoked INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()


def ensure_user(user):
    conn = db()
    conn.execute("""
        INSERT INTO users(user_id, username)
        VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET username=excluded.username
    """, (user.id, user.username or user.first_name or "Sin nombre"))
    conn.commit()
    conn.close()


def get_user(user_id):
    conn = db()
    row = conn.execute(
        "SELECT * FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()

    if row is None:
        return {
            "credits": 0,
            "clicks": 0,
            "banned": 0,
            "ban_reason": "",
            "license_until": None
        }
    return dict(row)


def is_admin(user_id):
    return ADMIN_ID != 0 and user_id == ADMIN_ID


def is_banned(user_id):
    return bool(get_user(user_id)["banned"])


def license_active(user_id):
    value = get_user(user_id)["license_until"]
    if not value:
        return False
    try:
        return datetime.fromisoformat(value) > datetime.now()
    except ValueError:
        return False


def make_key():
    return "-".join(
        secrets.token_hex(2).upper()
        for _ in range(3)
    )


def parse_duration(value):
    """
    Formatos:
      1d = 1 día
      7d = 7 días
      2w = 2 semanas
      1m = 1 mes (30 días)
    """
    match = re.fullmatch(r"(\d+)([dwm])", value.lower())
    if not match:
        return None

    number = int(match.group(1))
    unit = match.group(2)

    if number <= 0:
        return None

    if unit == "d":
        seconds = number * 86400
        label = f"{number} día(s)"
    elif unit == "w":
        seconds = number * 7 * 86400
        label = f"{number} semana(s)"
    else:
        seconds = number * 30 * 86400
        label = f"{number} mes(es)"

    return seconds, label


def generate_key(duration_value):
    parsed = parse_duration(duration_value)
    if not parsed:
        return None

    seconds, label = parsed
    key = make_key()

    conn = db()
    conn.execute("""
        INSERT INTO keys(key, duration_seconds, duration_label, created_at)
        VALUES (?, ?, ?, ?)
    """, (key, seconds, label, datetime.now().isoformat()))
    conn.commit()
    conn.close()

    return key, label


def redeem_key(user_id, key_value):
    conn = db()
    row = conn.execute(
        "SELECT * FROM keys WHERE key = ?", (key_value.upper(),)
    ).fetchone()

    if row is None:
        conn.close()
        return False, "❌ La key no existe."

    if row["revoked"]:
        conn.close()
        return False, "❌ Esta key fue revocada."

    if row["used_by"] is not None:
        conn.close()
        return False, "❌ Esta key ya fue utilizada."

    now = datetime.now()

    user = conn.execute(
        "SELECT license_until FROM users WHERE user_id = ?",
        (user_id,)
    ).fetchone()

    current_until = None
    if user and user["license_until"]:
        try:
            current_until = datetime.fromisoformat(user["license_until"])
        except ValueError:
            current_until = None

    base = current_until if current_until and current_until > now else now
    new_until = base + timedelta(seconds=row["duration_seconds"])

    conn.execute("""
        INSERT INTO users(user_id, license_until)
        VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET license_until=excluded.license_until
    """, (user_id, new_until.isoformat()))

    conn.execute("""
        UPDATE keys
        SET used_by = ?, used_at = ?
        WHERE key = ?
    """, (user_id, now.isoformat(), key_value.upper()))

    conn.commit()
    conn.close()

    return True, new_until


def set_ban(user_id, banned, reason=""):
    conn = db()
    conn.execute("""
        INSERT INTO users(user_id, banned, ban_reason)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            banned=excluded.banned,
            ban_reason=excluded.ban_reason
    """, (user_id, int(banned), reason))
    conn.commit()
    conn.close()


def change_credits(user_id, amount):
    conn = db()
    conn.execute("""
        INSERT INTO users(user_id, credits)
        VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            credits = MAX(0, users.credits + excluded.credits)
    """, (user_id, amount))
    conn.commit()
    conn.close()


def revoke_key(key_value):
    conn = db()
    cur = conn.execute(
        "UPDATE keys SET revoked = 1 WHERE key = ? AND used_by IS NULL",
        (key_value.upper(),)
    )
    conn.commit()
    changed = cur.rowcount
    conn.close()
    return changed > 0


def admin_keys():
    conn = db()
    rows = conn.execute("""
        SELECT key, duration_label, used_by, revoked
        FROM keys
        ORDER BY created_at DESC
        LIMIT 30
    """).fetchall()
    conn.close()
    return rows


def count_users():
    conn = db()
    n = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()
    return n


init_db()


# ============================================================
# MENÚ PRINCIPAL
# ============================================================

def main_menu():

    keyboard = [

        [
            InlineKeyboardButton(
                "🚪 Amazon",
                callback_data="amazon"
            )
        ],

        [
            InlineKeyboardButton(
                "🍪 Cookie Generator",
                callback_data="cookie"
            )
        ],

        [
            InlineKeyboardButton(
                "⚡ Gates",
                callback_data="generar"
            )
        ],

        [
            InlineKeyboardButton(
                "💬 Herramientas",
                callback_data="stats"
            )
        ],

        [
            InlineKeyboardButton(
                "👤 Perfil",
                callback_data="perfil"
            )
        ],

        [
            InlineKeyboardButton(
                "🆘 HELP",
                callback_data="help"
            )
        ],

        [
            InlineKeyboardButton(
                "⚙️ Config",
                callback_data="config"
            )
        ],

        [
            InlineKeyboardButton(
                "🔧 Panel Admin",
                callback_data="admin"
            )
        ]

    ]

    return InlineKeyboardMarkup(keyboard)


# ============================================================
# MENÚ VOLVER
# ============================================================

def back_menu():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "⬅️ Volver al menú",
                callback_data="menu"
            )
        ]
    ])


# ============================================================
# MENÚ GENERAR
# ============================================================

def generar_menu():

    keyboard = [

        [
            InlineKeyboardButton(
                "🆔 Gate Stripe",
                callback_data="gen_stripe"
            )
        ],

        [
            InlineKeyboardButton(
                "🆔 Gate Paypal",
                callback_data="gen_paypal"
            )
        ],

        [
            InlineKeyboardButton(
                "🆔 Validar Auth",
                callback_data="gen_auth"
            )
        ],

        [
            InlineKeyboardButton(
                "⬅️ Volver",
                callback_data="menu"
            )
        ]

    ]

    return InlineKeyboardMarkup(keyboard)

# ============================================================
# MENÚ GENERAR
# ============================================================

def generar_herramientas():

    keyboard = [

        [
            InlineKeyboardButton(
                "💬 Generar SMS",
                callback_data="gen_sms"
            )
        ],

        [
            InlineKeyboardButton(
                "💬 Generar Tempmail",
                callback_data="gen_mail"
            )
        ],

        [
            InlineKeyboardButton(
                "💬 Datos Virtuales",
                callback_data="gen_datos"
            )
        ],

        [
            InlineKeyboardButton(
                "⬅️ Volver",
                callback_data="menu"
            )
        ]

    ]

    return InlineKeyboardMarkup(keyboard)


# ============================================================
# MENÚ CONFIG
# ============================================================

def config_menu():

    keyboard = [

        [
            InlineKeyboardButton(
                "💳 Ver créditos",
                callback_data="credits"
            )
        ],

        [
            InlineKeyboardButton(
                "🔄 Reiniciar créditos",
                callback_data="reset_credits"
            )
        ],

        [
            InlineKeyboardButton(
                "⬅️ Volver",
                callback_data="menu"
            )
        ]

    ]

    return InlineKeyboardMarkup(keyboard)


# ============================================================
# START
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    data = get_user(user.id)

    username = (
        user.username
        or user.first_name
        or "Sin nombre"
    )

    text = (
        "🏛️ *BIENVENIDO A MICTLAN V1* 🎉\n\n"

        f"👤 Usuario: `{username}`\n"

        f"🆔 ID: `{user.id}`\n"

        f"💳 Créditos: `{data['credits']}`\n\n"

        "Selecciona una opción:"
    )

    await update.message.reply_text(
        text,
        reply_markup=main_menu(),
        parse_mode="Markdown"
    )


# ============================================================
# HELP
# ============================================================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    text = (
        "🆘 *AYUDA — MICTLAN V1*\n\n"

        "🚪 *Amazon*\n"
        "Abre la sección Amazon.\n\n"

        "🍪 *Cookie Generator*\n"
        "Sección de demostración.\n\n"

        "⚡ *Generar*\n"
        "Abre las herramientas de Gates.\n\n"

        "📊 *Herramientas*\n"
        "Muestra las herramientas.\n\n"

        "👤 *Perfil*\n"
        "Muestra tu información.\n\n"

        "⚙️ *Config*\n"
        "Muestra las opciones de configuración.\n\n"

        "🔧 *Panel Admin*\n"
        "Muestra el estado del panel.\n\n"

        "✅ Todos los botones están configurados."
    )

    if update.callback_query:

        await update.callback_query.answer()

        await update.callback_query.edit_message_text(
            text,
            reply_markup=back_menu(),
            parse_mode="Markdown"
        )

    else:

        await update.message.reply_text(
            text,
            reply_markup=back_menu(),
            parse_mode="Markdown"
        )


# ============================================================
# MANEJADOR DE BOTONES
# ============================================================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    user = query.from_user

    user_id = user.id

    data = query.data

    user_data = get_user(user_id)

    user_data["clicks"] += 1


    # ========================================================
    # MENÚ
    # ========================================================

    if data == "menu":

        username = (
            user.username
            or user.first_name
            or "Sin nombre"
        )

        text = (
            "🏛️ *MENÚ PRINCIPAL*\n\n"

            f"👤 Usuario: `{username}`\n"

            f"💳 Créditos: `{user_data['credits']}`\n\n"

            "Selecciona una opción:"
        )

        await query.edit_message_text(
            text,
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )

        return


    # ========================================================
    # AMAZON
    # ========================================================

    if data == "amazon":

        text = (
            "🚪 *AMAZON *\n\n"

            "✅ Sección activa.\n\n"

            "----------------------------------------------------------\n"

            "🔵 Comando: /gen  [12-16 digitos] | [00 mes] | [0000 año] | [000] cvv\n\n"

            "Ejemplo:\n"

            "/gen 4532640527811643|12|2025|123\n"

            "----------------------------------------------------------\n"

            "🔵 Comando: /check  [12-16 digitos] | [00 mes] | [0000 año] | [000] cvv\n\n"

            " [Al final la cookie_de_amazon_aqui ] \n"

            "Ejemplo:\n"

            "/check 4532640527811643|12|2025|123\n"

            "... hasta 10 tarjetas\n"

            " [*** tu_cookie_de_amazon_aqui ]\n"

            "----------------------------------------------------------"
        )

        await query.edit_message_text(
            text,
            reply_markup=back_menu(),
            parse_mode="Markdown"
        )

        return


    # ========================================================
    # GENERAR
    # ========================================================

    if data == "generar":

        text = (
            "⚡ *GATES*\n\n"

            "Selecciona un gate:"
        )

        await query.edit_message_text(
            text,
            reply_markup=generar_menu(),
            parse_mode="Markdown"
        )

        return


    # ========================================================
    # GENERAR STRIPE
    # ========================================================

    if data == "gen_stripe":

        text = (
            "🎲 *GENERAR STRIPE*\n\n"

             "----------------------------------------------------------\n"

            "🔵 Comando /bin cc[6-12 digitos] | mes[00] | año[0000] | generar [hasta 100]</code> | monto $1 , $20 $100 \n"

            "Ejemplo: \n"

            "/bin 4532640527811643|12|2025|20|1 \n"

            "----------------------------------------------------------\n"

            "El bot generará y procesará y te dará un reporte final."

        )

        await query.edit_message_text(
            text,
            reply_markup=back_menu(),
            parse_mode="Markdown"
        )

        return


    # ========================================================
    # GENERAR PAYPAL
    # ========================================================

    if data == "gen_paypal":

        identificador = str(
            uuid.uuid4()
        )

        text = (
            "🆔 *GENERAR PAYPAL *\n\n"

            "✅ Sección activa.\n\n"

            "----------------------------------------------------------\n"

            "🔵 Comando: /pay  [12-16 digitos] | [00 mes] | [00 año] | [000] cvv\n\n"

            "Ejemplo:\n"

            "/pay 4532640527811643|12|25|123\n"
        )

        await query.edit_message_text(
            text,
            reply_markup=back_menu(),
            parse_mode="Markdown"
        )

        return

    # ========================================================
    # GENERAR AUTH
    # ========================================================

    if data == "gen_auth":

        identificador = str(
            uuid.uuid4()
        )

        text = (
            "🆔 *VALIDAR AUTH *\n\n"

            f"`{identificador}`\n\n"

            "Identificador aleatorio de prueba."
        )

        await query.edit_message_text(
            text,
            reply_markup=back_menu(),
            parse_mode="Markdown"
        )

        return


    # ========================================================
    # PERFIL
    # ========================================================

    if data == "perfil":

        username = (
            user.username
            or user.first_name
            or "Sin nombre"
        )

        text = (
            "👤 *PERFIL*\n\n"

            f"👤 Usuario: `{username}`\n"

            f"🆔 ID: `{user_id}`\n"

            f"💳 Créditos: `{user_data['credits']}`\n"

            f"🖱️ Pulsaciones: `{user_data['clicks']}`"
        )

        await query.edit_message_text(
            text,
            reply_markup=back_menu(),
            parse_mode="Markdown"
        )

        return


    # ========================================================
    # STATS HERRAMIENTAS
    # ========================================================

    if data == "stats":

        username = (
            user.username
            or user.first_name
            or "Sin nombre"
        )

        text = (
            "📊 *HERRAMIENTAS *\n\n"

            f"👥 Usuario: `{username}`\n"

            f"💳 Créditos: `{user_data['credits']}`\n\n"

            "🟢 Estado: *ONLINE*"
        )

        await query.edit_message_text(
            text,
            reply_markup=generar_herramientas(),
            parse_mode="Markdown"
        )

        return

    # ========================================================
    # GENERAR SMS
    # ========================================================

    if data == "gen_sms":

        numero = random.randint(
            100000,
            999999
        )

        text = (
            "🎲 *GENERAR SMS *\n\n"

            f"`{numero}`\n\n"

            "Número aleatorio de prueba."
        )

        await query.edit_message_text(
            text,
            reply_markup=back_menu(),
            parse_mode="Markdown"
        )

        return

    # ========================================================
    # GENERAR TEMP MAIL
    # ========================================================

    if data == "gen_mail":

        numero = random.randint(
            100000,
            999999
        )

        text = (
            "🎲 *GENERAR TEMP MAIL *\n\n"

            f"`{numero}`\n\n"

            "Número aleatorio de prueba."
        )

        await query.edit_message_text(
            text,
            reply_markup=back_menu(),
            parse_mode="Markdown"
        )

        return

    # ========================================================
    # GENERAR DATOS USA
    # ========================================================

    if data == "gen_datos":

        numero = random.randint(
            100000,
            999999
        )

        text = (
            "🎲 *GENERAR DATOS USA*\n\n"

            f"`{numero}`\n\n"

            "Número aleatorio de prueba."
        )

        await query.edit_message_text(
            text,
            reply_markup=back_menu(),
            parse_mode="Markdown"
        )

        return


    # ========================================================
    # HELP
    # ========================================================

    if data == "help":

        await help_command(
            update,
            context
        )

        return


    # ========================================================
    # CONFIG
    # ========================================================

    if data == "config":

        text = (
            "⚙️ *CONFIGURACIÓN*\n\n"

            "Selecciona una opción:"
        )

        await query.edit_message_text(
            text,
            reply_markup=config_menu(),
            parse_mode="Markdown"
        )

        return


    # ========================================================
    # VER CRÉDITOS
    # ========================================================

    if data == "credits":

        text = (
            "💳 *CRÉDITOS*\n\n"

            f"Tienes `{user_data['credits']}` créditos."
        )

        await query.edit_message_text(
            text,
            reply_markup=back_menu(),
            parse_mode="Markdown"
        )

        return


    # ========================================================
    # REINICIAR CRÉDITOS
    # ========================================================

    if data == "reset_credits":

        user_data["credits"] = 0

        text = (
            "🔄 *CRÉDITOS REINICIADOS*\n\n"

            "Tus créditos ahora son `0`."
        )

        await query.edit_message_text(
            text,
            reply_markup=back_menu(),
            parse_mode="Markdown"
        )

        return


    # ========================================================
    # COOKIE GENERATOR
    # ========================================================

    if data == "cookie":

        text = (
            "🍪 *COOKIE GENERATOR*\n\n"

            "✅ Sección activa.\n\n"

            "Esta versión es demostrativa.\n\n"

            "No genera ni recopila cookies "
            "de cuentas o servicios de terceros."
        )

        await query.edit_message_text(
            text,
            reply_markup=back_menu(),
            parse_mode="Markdown"
        )

        return


    # ========================================================
    # ADMIN
    # ========================================================

    if data == "admin":

        usuarios = len(
            user_data_store
            #DEPRECATED_user_data_store
        )

        text = (
            "🔧 *PANEL ADMIN*\n\n"

            "🟢 Estado: ACTIVO\n\n"

            f"👥 Usuarios registrados: `{usuarios}`\n\n"

            "⭐ Comando: /admin \n"

            "Panel preparado para funciones administrativas propias."
        )

        await query.edit_message_text(
            text,
            reply_markup=back_menu(),
            parse_mode="Markdown"
        )

        #await admin_command(
        #    update,
        #    context
        #)

        return


    # ========================================================
    # DESCONOCIDO
    # ========================================================

    text = (
        "❌ *OPCIÓN DESCONOCIDA*\n\n"
        "Ese botón no está configurado."
    )

    await query.edit_message_text(
        text,
        reply_markup=main_menu(),
        parse_mode="Markdown"
    )


# ============================================================
# COMANDOS DE LICENCIAS / ADMIN
# ============================================================

async def admin_only(update, context):
    user = update.effective_user
    print("Admin Id")
    print(user.id)

    if not is_admin(user.id):
        await update.message.reply_text("⛔ No tienes permiso para usar este comando.")
        return False
    return True


async def genkey_command(update, context):
    if not await admin_only(update, context):
        return
    if len(context.args) != 1:
        await update.message.reply_text(
            "Uso:\n/genkey 1d\n/genkey 7d\n/genkey 30d\n/genkey 3m\n\n"
            "d = días | w = semanas | m = meses"
        )
        return

    result = generate_key(context.args[0])
    if not result:
        await update.message.reply_text("❌ Duración inválida.")
        return

    key, label = result
    await update.message.reply_text(
        f"🔑 *KEY GENERADA*\n\n"
        f"Key: `{key}`\n"
        f"Duración: *{label}*\n"
        f"Estado: 🟢 Disponible",
        parse_mode="Markdown"
    )


async def redeem_command(update, context):
    user = update.effective_user
    ensure_user(user)

    if is_banned(user.id):
        await update.message.reply_text("🚫 Estás baneado del bot.")
        return

    if len(context.args) != 1:
        await update.message.reply_text("Uso: /redeem TU-KEY")
        return

    ok, result = redeem_key(user.id, context.args[0])
    if not ok:
        await update.message.reply_text(result)
        return

    await update.message.reply_text(
        "✅ *KEY ACTIVADA*\n\n"
        f"📅 Tu acceso vence:\n`{result.strftime('%d/%m/%Y %H:%M')}`",
        parse_mode="Markdown"
    )


async def ban_command(update, context):
    if not await admin_only(update, context):
        return
    if len(context.args) < 1:
        await update.message.reply_text("Uso: /ban USER_ID [motivo]")
        return

    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ USER_ID inválido.")
        return

    reason = " ".join(context.args[1:]) or "Incumplimiento de reglas"
    set_ban(user_id, True, reason)
    await update.message.reply_text(
        f"🚫 Usuario `{user_id}` baneado.\nMotivo: {reason}",
        parse_mode="Markdown"
    )


async def unban_command(update, context):
    if not await admin_only(update, context):
        return
    if len(context.args) != 1:
        await update.message.reply_text("Uso: /unban USER_ID")
        return

    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ USER_ID inválido.")
        return

    set_ban(user_id, False, "")
    await update.message.reply_text(f"✅ Usuario `{user_id}` desbaneado.", parse_mode="Markdown")


async def addcredits_command(update, context):
    if not await admin_only(update, context):
        return
    if len(context.args) != 2:
        await update.message.reply_text("Uso: /addcredits USER_ID CANTIDAD")
        return

    try:
        user_id = int(context.args[0])
        amount = int(context.args[1])
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Datos inválidos.")
        return

    change_credits(user_id, amount)
    data = get_user(user_id)
    await update.message.reply_text(
        f"💳 Se agregaron `{amount}` créditos.\n"
        f"Total: `{data['credits']}`",
        parse_mode="Markdown"
    )


async def remcredits_command(update, context):
    if not await admin_only(update, context):
        return
    if len(context.args) != 2:
        await update.message.reply_text("Uso: /remcredits USER_ID CANTIDAD")
        return

    try:
        user_id = int(context.args[0])
        amount = int(context.args[1])
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Datos inválidos.")
        return

    change_credits(user_id, -amount)
    data = get_user(user_id)
    await update.message.reply_text(
        f"💳 Se quitaron `{amount}` créditos.\n"
        f"Total: `{data['credits']}`",
        parse_mode="Markdown"
    )


async def user_command(update, context):
    if not await admin_only(update, context):
        return
    if len(context.args) != 1:
        await update.message.reply_text("Uso: /user USER_ID")
        return

    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ USER_ID inválido.")
        return

    data = get_user(user_id)
    await update.message.reply_text(
        f"👤 *USUARIO*\n\n"
        f"ID: `{user_id}`\n"
        f"💳 Créditos: `{data['credits']}`\n"
        f"🚫 Baneado: `{'Sí' if data['banned'] else 'No'}`\n"
        f"🔑 Licencia activa: `{'Sí' if license_active(user_id) else 'No'}`\n"
        f"📅 Vence: `{data['license_until'] or 'Sin licencia'}`",
        parse_mode="Markdown"
    )


async def revoke_command(update, context):
    if not await admin_only(update, context):
        return
    if len(context.args) != 1:
        await update.message.reply_text("Uso: /revokekey KEY")
        return

    if revoke_key(context.args[0]):
        await update.message.reply_text("✅ Key revocada.")
    else:
        await update.message.reply_text("❌ No existe, ya fue usada o ya estaba revocada.")


async def keys_command(update, context):
    if not await admin_only(update, context):
        return

    rows = admin_keys()
    if not rows:
        await update.message.reply_text("📋 No hay keys generadas.")
        return

    lines = ["📋 *ÚLTIMAS KEYS*\n"]
    for row in rows:
        if row["revoked"]:
            status = "🚫 Revocada"
        elif row["used_by"] is not None:
            status = f"🟡 Usada por `{row['used_by']}`"
        else:
            status = "🟢 Disponible"

        lines.append(
            f"`{row['key']}` — {row['duration_label']} — {status}"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def admin_command(update, context):
    if not await admin_only(update, context):
        return


    #await update.message.reply_text("📋 Llego al menu admin." , parse_mode="Markdown")

    await update.message.reply_text(
         "🔧 *PANEL ADMIN*\n\n"
         "🔑 /genkey 7d — generar key\n"
         "🎟️ /redeem KEY — activar key\n"
         "📋 /keys — listar keys\n"
         "🗑️ /revokekey KEY — revocar key\n\n"
         "🚫 /ban USER ID motivo\n"
         "✅ /unban USER ID\n"
         "🔎 /user USER ID\n\n"
         "➕ /addcredits USER ID cantidad\n"
         "➖ /remcredits USER ID cantidad" ,
         parse_mode="Markdown"
    )


# ============================================================
# RUTINAS DEL BOT
# ============================================================

#------COMANDO /GEN  --------#
async def gen_handler(update , context):    

    user = update.effective_user 
    user_id = user.id
    username = user.username  
    #bin_lasted = message.text[len('/gen '):] 

    global input

    if len(context.args) != 1:       
        input = re.findall(r'[0-9x]+', context.args[0] )
    else:
        input = re.findall(r'[0-9x]+', context.args[0])

    #await update.message.reply_text(input)
  
    if not input:
        await update.message.reply_text("_USE BIEN LA HERRAMIENTA /gen 4556747...")
        return

    #-----------FUNCION GNERADOR----------#
    if len(input)==1:
        cc = input[0]
        mes = 'x'
        ano = 'x'
        cvv = 'x'
    elif len(input)==2:
        cc = input[0]
        mes = input[1][0:2]
        ano = 'x'
        cvv = 'x'
    elif len(input)==3:
        cc = input[0]
        mes = input[1][0:2]
        ano = input[2]
        cvv = 'x'
    elif len(input)==4:
        cc = input[0]
        mes = input[1][0:2]
        ano = input[2]
        cvv = input[3]
    else:
        cc = input[0]
        mes = input[1][0:2]
        ano = input[2]
        cvv = input[3]                

    if len(input[0]) < 6: return await update.message.reply_text("Invalid Bin ⚠️")
    cc1,cc2,cc3,cc4,cc5,cc6,cc7,cc8,cc9,cc10 = cc_generar(cc,mes,ano,cvv)

    extra = str(cc) + 'xxxxxxxxxxxxxxxxxxxxxxx'
    if mes == 'x':
        mes_2 = 'rnd'
    else:
        mes_2 = mes
    if ano == 'x':
            ano_2 = 'rnd'
    else:
        ano_2 = ano
    if cvv == 'x':
        cvv_2 = 'rnd'
    else:
        cvv_2 = cvv

    x = get_bin_info(cc[0:6])

    await update.message.reply_text(f"📊 <b>Lista de Resultados: </b>\n<code> {cc1} {cc2} {cc3} {cc4} {cc5} {cc6} {cc7} {cc8} {cc9} {cc10}</code>", parse_mode='HTML')


#------COMANDO /CHECK --------#
async def handle_check_massive(update: Update , context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user 
    user_id = user.id
    username = user.username  

    try:
        lines = context.args #.split('\n')
        if len(lines) < 2:
            # Intentar parsear si enviaron todo en una línea (espacio como separador final para cookies)
            parts = context.args #.split(' ')
            if len(parts) < 3:
                await update.message.reply_text("❌ <b>Formato inválido.</b>\nUsa saltos de línea para las tarjetas y pon las cookies al final." , parse_mode='HTML')
                return
            cards_raw = [parts[1]]
            cookies = parts[2]
        else:
            # Primera línea tiene el comando y quizás la primera tarjeta
            first_line = lines[0].replace('/check', '').strip()
            cards_raw = []
            if first_line: cards_raw.append(first_line)
            
            # Líneas intermedias son tarjetas, la última son cookies
            for i in range(1, len(lines) - 1):
                if lines[i].strip(): cards_raw.append(lines[i].strip())
            
            cookies = lines[-1].strip()

        # Filtrar tarjetas válidas y limitar a 10
        valid_cards = [c for c in cards_raw if validate_card_format(c)]
        if not valid_cards:
            await update.message.reply_text("❌ No encontré tarjetas válidas en el formato <code>numero|mes|año|cvv</code>" , parse_mode='HTML')
            return
            
        if len(valid_cards) > 15:
            await update.message.reply_text("⚠️ Has enviado más de 15 tarjetas. Solo procesaré las primeras 15.")
            valid_cards = valid_cards[:15]

        status_msg = await update.message.reply_text(f"⏳ <b>Procesando {len(valid_cards)} tarjetas...</b>\nPor favor espera, esto puede tardar un momento.")
        final_report = f"📊 <b>Reporte de Verificación ({len(valid_cards)})</b>\n\n"
        
        for idx, card in enumerate(valid_cards, 1):
            # Actualizar mensaje de estado cada 2 tarjetas para dar feedback
            if idx > 1 and idx % 2 == 0:
                await status_msg.edit_text(f"⏳ <b>Procesando {idx}/{len(valid_cards)}...</b>", parse_mode='HTML')
            
            res = check_card_api(card, cookies)
            final_report += format_single_result(res, idx) + "\n" 
            time.sleep(1) # Pequeño delay para no saturar la API

        await status_msg.edit_text(final_report, parse_mode='HTML')

    except Exception as e:
        await update.message.reply_text(f"❌ <b>Error crítico:</b> {str(e)}")


#------------ GATE OLIMPO /BIN ---------------#    
async def valida_command(update: Update , context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user 
    user_id = user.id
    username = user.username  
    #gatehtml = ""
    try:
        lines = context.args

        if len(lines) < 2:
            # Intentar parsear si enviaron todo en una línea (espacio como separador final para cookies)
            parts = context.args
            cards_raw = [parts[0]]
            cookies = "" #parts[2]
        else:
            # Primera línea tiene el comando y quizás la primera tarjeta
            await update.message.reply_text(f"❌ No es válido el formato <code>numero|mes|año|cantidad</code>", parse_mode='HTML')
            return

        valid_cards = cards_raw
        final_report = f"📊 <b>Reporte de Verificación ({len(valid_cards)})</b>\n\n"
        card = valid_cards[0]
        divisor = card.split("|")
        cc = divisor[0]
        mes = divisor[1]
        anio = divisor[2]
        num = divisor[3]
        monto = int(divisor[4])

        if (len(cc) == 0) or (len(mes) == 0) or (len(anio) == 0) or (len(num) == 0) or monto == 0:
            await update.message.reply_text(f"❌ No es válido el formato enviado <code>numero|mes|año|cantidad</code>", parse_mode='HTML')
            return

        if (len(cc) > 12):
            await update.message.reply_text(f"❌ La cc no puede tener mas de 12 digitos !!! <code> cc = maximo 12 digitos </code>", parse_mode='HTML')
            return

        # se generarn las tarjetas del bin
        ccs = cc_gen(cc, mes, anio, int(num))
        guardar_combo(ccs)
        combos = cargar_combo()
        total_tarjetas = len(combos)

        monto_nombre = "$" + str(monto) + ".0 MX CARGO "
        #monto = 1
    
        proxies_list = cargar_proxies()
        bin_db = cargar_bin_db()
        
        status_msg = await update.message.reply_text(f"⏳ <b>Procesando {len(combos)} tarjetas...</b>\nPor favor espera, esto puede tardar un momento." , parse_mode='HTML')

        if os.path.exists(TOKENS_FILE):
            os.remove(TOKENS_FILE)

        USED_PHONES.clear()
        tokens_generados = crear_cuentas_para_check(CUENTAS_FIJAS, proxies_list)
    
        if tokens_generados == 0:
            logs = "  [red]No se generaron tokens. Abortando.[/red]"
            return

        tokens = cargar_tokens()
        lives_total = 0
        deads_total = 0
        errores_total = 0
        cuentas_usadas = 0
        token_expirados = 0
        api_no_disponible = False
        errores_consecutivos = 0

        i = 0
        token_idx = 0
        token_actual = None
        proxy_actual = None
        tarjetas_en_cuenta = 0

        while i < total_tarjetas and not api_no_disponible:
            if tarjetas_en_cuenta >= TARJETAS_POR_CUENTA_FIJO or token_actual is None:
                if token_idx >= len(tokens):
                    logs = "  [red]No hay mas tokens disponibles[/red]"
                    break
                token_actual = tokens[token_idx]
                token_idx += 1
                proxy_actual = format_proxy(random.choice(proxies_list)) if proxies_list else None
                cuentas_usadas += 1
                tarjetas_en_cuenta = 0
                errores_consecutivos = 0

            combo = combos[i]
            if not combo or "|" not in combo:
                i += 1
                continue
            parts = combo.strip().split("|")
            if len(parts) < 4:
                i += 1
                continue

            cc, mes, ano, cvv = parts[0], parts[1], parts[2], parts[3]
            bin_info = buscar_bin(cc, bin_db)
            brand = bin_info.get("brand", "?")
            banco = bin_info.get("Banco", "?")

            t_num = Text()
            t_num.append(f"  [{i+1}/{total_tarjetas}] ", style="white")
            t_num.append(f"{cc[:6]}...{cc[-4:]}", style="white")
            t_num.append(" | ", style="dim")
            t_num.append(f"{brand} - {banco}", style="dim")

            tipo, display, detalle = check_card(cc, mes, ano, cvv, monto, monto_nombre, token_actual, proxy_actual, bin_info)
            await status_msg.edit_text(f"⏳ <b>Procesando {i}/{len(combos)}...</b>", parse_mode='HTML')

            if tipo == "token_expired":
                token_expirados += 1
                await update.message.reply_text(f"    [yellow]TOKEN EXPIRADO | Cambiando a siguiente cuenta...[/yellow]" , parse_mode='HTML')

                token_actual = None
                tarjetas_en_cuenta = TARJETAS_POR_CUENTA_FIJO
                errores_consecutivos = 0
                continue

            if tipo == "error" and ("API APAGADA" in detalle or "API NO DISPONIBLE" in detalle):
                errores_consecutivos += 1
                await update.message.reply_text(f"    [yellow]ERROR API ({errores_consecutivos}/{ERRORES_ROTACION})[/yellow]" , parse_mode='HTML')
                
                if errores_consecutivos >= ERRORES_ROTACION:
                    await update.message.reply_text(f"    [bold red]Forzando rotación de cuenta...[/bold red]" , parse_mode='HTML')
                    token_actual = None
                    tarjetas_en_cuenta = TARJETAS_POR_CUENTA_FIJO
                    errores_consecutivos = 0
                    continue
                else:
                    errores_total += 1
                    await update.message.reply_text(f"    [red]ERROR | {detalle}[/red]" , parse_mode='HTML')
                    tarjetas_en_cuenta += 1
                    i += 1
                    continue

            errores_consecutivos = 0

            if tipo == "live":
                lives_total += 1
                guardar_live(combo, monto_nombre, bin_info)

                res = f" ✅ <b>Status: LIVE</b>"
                final_report += "Tarjeta # " + str(i) + " [" + combo + "] " + res + "\n"

            elif tipo == "dead":
                deads_total += 1
                guardar_dead(combo, detalle, bin_info)
                if "HTTP" in detalle or "Fondos" not in detalle:
                    res = f" ❌ <b>DEAD</b>"
                    final_report += "Tarjeta # " + str(i) + " [" + combo + "] " + res + "\n"
                else:
                    res = f" ❌ <b>DEAD | {detalle}</b>"
                    final_report += "Tarjeta # " + str(i) + " [" + combo + "] " + res + "\n"

            else:
                errores_total += 1
                res = f"  ❌ <b>ERROR | {detalle}</b>"
                final_report += "Tarjeta # " + str(i) + " [" + combo + "] " + res + "\n"

            tarjetas_en_cuenta += 1
            i += 1

            if i < total_tarjetas:
                time.sleep(random.uniform(0.8, 1.5))

        final_report += f"\n  <b>**FINALIZADO**</b>"
        final_report += f"  ✅ <b>Lives: {lives_total}</b>"
        final_report += f"  ❌ <b>Deads: {deads_total}</b>"
        final_report +=f"   ⚠️ <b>Errores: {errores_total}</b>"
        final_report +=f"  <b>Tokens expirados: {token_expirados}</b>"
        final_report +=f"  <b>Cuentas usadas: {cuentas_usadas}</b>"

        log_to_file(f"FINALIZADO - Lives: {lives_total} | Deads: {deads_total} | Errores: {errores_total} | Tokens expirados: {token_expirados}")
        await update.message.reply_text(final_report , parse_mode='HTML')

    except Exception as e:
        await update.message.reply_text(f"❌ <b>Error crítico:</b> {str(e)}", parse_mode='HTML')

# *aquivoy
#------------ GATE OLIMPO /PAY ---------------#    
async def valida_paypal(update: Update , context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user 
    user_id = user.id
    username = user.username  

    #if len(sys.argv) < 2:
    #    print("Uso: python paypal_checker.py \"CC|MM|YY|CVV\"")
    #    print("Ejemplo: python paypal_checker.py \"4815153061705285|01|28|498\"")
    #    sys.exit(1)

    #card_input = sys.argv[1].strip()
    
    card_input = context.args[0]
    print(f"🔍 Verificando tarjeta: {card_input}")
    await update.message.reply_text(f"🔍 Verificando tarjeta: {card_input}", parse_mode='HTML')

    try:
        cc, month, year, cvv, cctype = card_parse_pp(card_input)
    except Exception as e:
        print(f"❌ Error al parsear la tarjeta: {e}")
        await update.message.reply_text(f"❌ Error al parsear la tarjeta: {e}", parse_mode='HTML')
        return

    # Obtener info BIN
    bin_info = get_bin_info_pp(cc)
    brand = bin_info.get("brand", "N/A")
    country = bin_info.get("country", "N/A")
    flag = bin_info.get("flag", "")
    level = bin_info.get("level", "N/A")
    type2 = bin_info.get("type", "N/A")
    bin_num = bin_info.get("bin", cc[:6])
    bank = bin_info.get("bank", "N/A")

    final_report = ""
    final_report += f"📋 Información de la tarjeta:\n\n"
    final_report += f"  • BIN: {bin_num}\n"
    final_report +=f"  • Banco: {bank}\n"
    final_report +=f"  • Marca: {brand} | {type2} | {level}\n"
    final_report +=f"  • País: {country} {flag}\n"
    final_report +=f"  • Tipo: {cctype}\n"
    final_report +="\n⏳ Procesando con PayPal..."
    await update.message.reply_text(final_report, parse_mode='HTML')

    # Ejecutar función asíncrona
    #loop = asyncio.new_event_loop()
    #asyncio.set_event_loop(loop)
    try:
        #status, message = loop.run_until_complete(paypal_charge(card_input))
        status, message = await paypal_charge(card_input)
    except Exception as e:
        status = "Error! 💥"
        message = str(e)[:150]
        await update.message.reply_text("Error! 💥 " + message, parse_mode='HTML')
        return
    
    #finally:
    #    loop.close()

    final_report = ""
    final_report +="\n📊 Resultado:"
    final_report +=f"  • Estado: {status}\n"
    final_report +=f"  • Mensaje: {message}"
    await update.message.reply_text(final_report, parse_mode='HTML')


# ============================================================
# MAIN
# ============================================================

def main():

    app = (
        Application
        .builder()
        .token(TOKEN)
        .build()
    )

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    app.add_handler(
        CommandHandler(
            "help",
            help_command
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            button_handler
        )
    )

    app.add_handler(CommandHandler("gen", gen_handler))
    app.add_handler(CommandHandler("check", handle_check_massive))
    app.add_handler(CommandHandler("bin", valida_command))
    app.add_handler(CommandHandler("pay", valida_paypal))
    

    app.add_handler(CommandHandler("genkey", genkey_command))
    app.add_handler(CommandHandler("redeem", redeem_command))
    app.add_handler(CommandHandler("ban", ban_command))
    app.add_handler(CommandHandler("unban", unban_command))
    app.add_handler(CommandHandler("addcredits", addcredits_command))
    app.add_handler(CommandHandler("remcredits", remcredits_command))
    app.add_handler(CommandHandler("user", user_command))
    app.add_handler(CommandHandler("revokekey", revoke_command))
    app.add_handler(CommandHandler("keys", keys_command))
    app.add_handler(CommandHandler("admin", admin_command))

    print("")
    print("========================================")
    print("🏛️ MICTLAN V1")
    print("✅ BOT INICIADO CORRECTAMENTE")
    print("📱 Esperando mensajes...")
    print("========================================")
    print("")

    app.run_polling()


# ============================================================
# EJECUTAR
# ============================================================

if __name__ == "__main__":
    main()


