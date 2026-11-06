import os
import logging
import random
import uuid
import sqlite3
import secrets
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)


# ============================================================
# CONFIGURACIÓN
# ============================================================

TOKEN = os.getenv("BOT_TOKEN")

# ID de Telegram del administrador. Cámbialo por el tuyo.
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

DB_FILE = "hades_data.db"


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
# DATOS DE USUARIOS / BASE DE DATOS
# ============================================================

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
                "🚪 Gates",
                callback_data="gates"
            )
        ],

        [
            InlineKeyboardButton(
                "⚡ Generar",
                callback_data="generar"
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
                "📊 Stats",
                callback_data="stats"
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
                "🍪 Cookie Generator",
                callback_data="cookie"
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
                "🎲 Generar número",
                callback_data="gen_num"
            )
        ],

        [
            InlineKeyboardButton(
                "🆔 Generar ID",
                callback_data="gen_id"
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

    ensure_user(user)
    if is_banned(user.id):
        await update.message.reply_text("🚫 Estás baneado del bot.")
        return
    data = get_user(user.id)

    username = (
        user.username
        or user.first_name
        or "Sin nombre"
    )

    text = (
        "🏛️ *BIENVENIDO A HADES V1* 🎉\n\n"

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
        "🆘 *AYUDA — HADES V1*\n\n"

        "🚪 *Gates*\n"
        "Abre la sección Gates.\n\n"

        "⚡ *Generar*\n"
        "Abre las herramientas de generación.\n\n"

        "👤 *Perfil*\n"
        "Muestra tu información.\n\n"

        "📊 *Stats*\n"
        "Muestra las estadísticas.\n\n"

        "⚙️ *Config*\n"
        "Muestra las opciones de configuración.\n\n"

        "🍪 *Cookie Generator*\n"
        "Sección de demostración.\n\n"

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

    ensure_user(user)
    conn = db()
    conn.execute("UPDATE users SET clicks = clicks + 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    user_data = get_user(user_id)


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
    # GATES
    # ========================================================

    if data == "gates":

        text = (
            "🚪 *GATES*\n\n"

            "✅ Sección activa.\n\n"

            "Esta sección está preparada para "
            "agregar funciones propias y legítimas."
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
            "⚡ *GENERAR*\n\n"

            "Selecciona una herramienta:"
        )

        await query.edit_message_text(
            text,
            reply_markup=generar_menu(),
            parse_mode="Markdown"
        )

        return


    # ========================================================
    # GENERAR NÚMERO
    # ========================================================

    if data == "gen_num":

        numero = random.randint(
            100000,
            999999
        )

        text = (
            "🎲 *NÚMERO GENERADO*\n\n"

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
    # GENERAR ID
    # ========================================================

    if data == "gen_id":

        identificador = str(
            uuid.uuid4()
        )

        text = (
            "🆔 *ID GENERADO*\n\n"

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
    # STATS
    # ========================================================

    if data == "stats":

        conn = db()
        usuarios = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        pulsaciones = conn.execute("SELECT COALESCE(SUM(clicks), 0) FROM users").fetchone()[0]
        creditos = conn.execute("SELECT COALESCE(SUM(credits), 0) FROM users").fetchone()[0]
        conn.close()

        text = (
            "📊 *ESTADÍSTICAS*\n\n"

            f"👥 Usuarios: `{usuarios}`\n"

            f"🖱️ Pulsaciones: `{pulsaciones}`\n"

            f"💳 Créditos: `{creditos}`\n\n"

            "🟢 Estado: *ONLINE*"
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

        conn = db()
        conn.execute("UPDATE users SET credits = 0 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()

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
            DEPRECATED_user_data_store
        )

        text = (
            "🔧 *PANEL ADMIN*\n\n"

            "🟢 Estado: ACTIVO\n\n"

            f"👥 Usuarios registrados: `{usuarios}`\n\n"

            "Panel preparado para funciones "
            "administrativas propias."
        )

        await query.edit_message_text(
            text,
            reply_markup=back_menu(),
            parse_mode="Markdown"
        )

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

    await update.message.reply_text(
        "🔧 *PANEL ADMIN*\n\n"
        "🔑 /genkey 7d — generar key\n"
        "🎟️ /redeem KEY — activar key\n"
        "📋 /keys — listar keys\n"
        "🗑️ /revokekey KEY — revocar key\n\n"
        "🚫 /ban USER_ID motivo\n"
        "✅ /unban USER_ID\n"
        "🔎 /user USER_ID\n\n"
        "➕ /addcredits USER_ID cantidad\n"
        "➖ /remcredits USER_ID cantidad",
        parse_mode="Markdown"
    )


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
    print("🏛️ HADES V1")
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
