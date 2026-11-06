"""Panel de administración oculto /batman."""
import asyncio

from aiogram import Router, F, Bot
from aiogram.types import (
    InputMediaAudio,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
    CallbackQuery,
)
from aiogram.filters import Command, StateFilter
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.keyboards import (
    admin_main_menu,
    admin_assign_menu,
    admin_subpanel_menu,
    gencookie_access_menu,
    messages_menu,
    message_confirm_menu,
    message_pack_menu,
    memberships_menu,
    membership_confirm_menu,
    membership_remove_menu,
    galletas_menu,
    galletas_confirm_menu,
    matanga_menu,
    matanga_confirm_menu,
    ajuste_menu,
    ajuste_confirm_menu,
    credits_menu,
    credits_confirm_menu,
    credits_subpanel_menu,
    sms_admin_menu,
    sms_admin_subpanel_menu,
    sms_admin_confirm_menu,
)
from bot.services import sms as sms_svc
from bot.services.credits import (
    get_balance,
    add_credits,
    deduct_credits,
    remove_credits,
    get_transactions,
)
from bot.services.logger import (
    log_membership_assigned,
    log_membership_removed,
    log_galletas_assigned,
    log_matanga,
    log_credits_change,
    log_admin_access,
    log_admin_added,
    log_admin_removed,
    log_admin_access_denied,
    log_message_broadcast,
    log_operation_failed,
)
from bot.config import ETERNAL_TARTARO_GROUP_ID, OLIMPO_BINS_GROUP_ID
from bot.services.users import get_all_users
from bot.utils.formatting import message_to_html, escape_html
from bot.services.admins import (
    is_admin,
    is_super_admin,
    get_admin_ids,
    get_all_admin_ids,
    add_admin,
    remove_admin,
)
from bot.services.gencookie_access import (
    grant_access,
    revoke_access,
    get_access_list,
)
from bot.services.memberships import (
    assign_membership,
    get_membership,
    format_end_date,
    remove_membership,
    PLAN_LABELS,
)
from bot.services.galletas import (
    grant_galletas,
    get_galletas,
    remove_galletas,
    refill_galletas,
    set_galletas,
    TIPO_AMOUNT,
    TIPO_LABELS,
)

router = Router()

# Estado temporal para flujos de admin
_pending_admin_action: dict = {}
_pending_gencookie_action: dict = {}


class AdminMessages(StatesGroup):
    """Estados para el flujo de envío de mensajes."""
    target = State()
    user_id = State()
    content = State()
    confirm = State()


class AdminMemberships(StatesGroup):
    """Estados para el flujo de asignación de membresías."""
    plan = State()
    custom_days = State()
    beneficiary = State()
    confirm = State()


class AdminRemoveMembership(StatesGroup):
    """Estados para el flujo de eliminación de membresías."""
    beneficiary = State()
    confirm = State()


class AdminGalletas(StatesGroup):
    """Estados para el flujo de asignación de galletas."""
    tipo = State()
    beneficiary = State()
    confirm = State()


class AdminMatanga(StatesGroup):
    """Estados para el flujo de matanga (quitar galletas o créditos)."""
    action = State()
    beneficiary = State()
    confirm = State()


class AdminAjuste(StatesGroup):
    """Estados para el ajuste manual de galletas (solo super admins)."""
    action = State()
    beneficiary = State()
    amount = State()
    confirm = State()


class AdminCredits(StatesGroup):
    """Estados para el flujo de gestión de créditos."""
    action = State()
    beneficiary = State()
    amount = State()
    confirm = State()


class AdminSms(StatesGroup):
    """Estados para el panel SMS virtual (solo super admins)."""
    action = State()       # qué se está configurando
    tier_piso = State()    # piso del tramo a crear/editar
    value = State()        # valor a guardar (tasa, créditos)
    confirm = State()      # confirmación de eliminar tramo


def _admin_welcome_text() -> str:
    return (
        "🛡️ <b>PANEL DE ADMINISTRACIÓN</b>\n\n"
        "Bienvenido al panel de control.\n"
        "Selecciona una opción para gestionar el bot."
    )


@router.message(Command("batman"))
async def cmd_admin(message: Message, bot):
    """Comando oculto /batman: solo accesible para admins/super admins.

    Regla de silencio absoluto: si el usuario no está autorizado,
    el bot no responde absolutamente nada.
    """
    user_id = message.from_user.id
    if not await is_admin(user_id):
        # Alerta de seguridad: acceso no autorizado a /batman
        await log_admin_access_denied(bot, user_id, message.chat.type)
        return

    # Alerta silenciosa al canal de logs
    await log_admin_access(bot, user_id, message.chat.type)

    await message.answer(
        _admin_welcome_text(),
        parse_mode=ParseMode.HTML,
        reply_markup=admin_main_menu(is_super=is_super_admin(user_id)),
    )


@router.message(Command("addadmin"))
async def cmd_add_admin(message: Message, bot: Bot):
    """Comando directo /addadmin: solo super admins."""
    user_id = message.from_user.id
    if not is_super_admin(user_id):
        return

    args = message.text.split()
    if len(args) < 2:
        await message.answer(
            "❌ Formato incorrecto. Usa:\n"
            "/addadmin <user_id>\n\n"
            "Ejemplo:\n"
            "/addadmin 123456789"
        )
        return

    try:
        target_id = int(args[1])
    except ValueError:
        await message.answer("❌ El ID de usuario debe ser un número entero.")
        return

    if is_super_admin(target_id):
        await message.answer("⛔ No se puede modificar un super admin.")
        return

    if await add_admin(target_id):
        await message.answer(
            f"✅ El usuario <code>{target_id}</code> fue agregado como administrador.",
            parse_mode=ParseMode.HTML,
        )
        await log_admin_added(bot, user_id, target_id)
    else:
        await message.answer(
            f"ℹ️ El usuario <code>{target_id}</code> ya es administrador.",
            parse_mode=ParseMode.HTML,
        )


@router.message(Command("removeadmin"))
async def cmd_remove_admin(message: Message, bot: Bot):
    """Comando directo /removeadmin: solo super admins."""
    user_id = message.from_user.id
    if not is_super_admin(user_id):
        return

    args = message.text.split()
    if len(args) < 2:
        await message.answer(
            "❌ Formato incorrecto. Usa:\n"
            "/removeadmin <user_id>\n\n"
            "Ejemplo:\n"
            "/removeadmin 123456789"
        )
        return

    try:
        target_id = int(args[1])
    except ValueError:
        await message.answer("❌ El ID de usuario debe ser un número entero.")
        return

    if is_super_admin(target_id):
        await message.answer("⛔ No se puede quitar a un super admin.")
        return

    if await remove_admin(target_id):
        await message.answer(
            f"✅ El usuario <code>{target_id}</code> fue removido como administrador.",
            parse_mode=ParseMode.HTML,
        )
        await log_admin_removed(bot, user_id, target_id)
    else:
        await message.answer(
            f"ℹ️ El usuario <code>{target_id}</code> no es administrador.",
            parse_mode=ParseMode.HTML,
        )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    """Cancela cualquier operación abierta (panel /batman o /gencookie).

    Registrado antes que los handlers con estado para que siempre
    tenga prioridad sobre un flujo a mitad. No aplica a una creación
    de /gencookie en curso (no se puede cancelar).
    """
    current = await state.get_state()
    if current is not None and current.split(":")[0] == "AmazonGenState":
        await message.answer(
            "⏳ Hay una creación de cuenta en curso y no se puede cancelar.\n\n"
            "Espera a que termine.",
            parse_mode=ParseMode.HTML,
        )
        return
    if current is None:
        await message.answer(
            "No hay ninguna operación abierta.",
            parse_mode=ParseMode.HTML,
        )
        return
    await state.clear()
    await message.answer(
        "✅ Operación cancelada.",
        parse_mode=ParseMode.HTML,
    )


@router.message(Command("status"))
async def cmd_status(message: Message):
    """Diagnóstico rápido del bot (solo admins, Fase 10).

    Verifica BD, backup diario, variables críticas y servicios externos
    sin exponer valores sensibles. Silencio absoluto si no es admin.
    """
    from bot.services.admins import is_admin as _is_admin

    if not await _is_admin(message.from_user.id):
        return
    import os
    import sqlite3
    from pathlib import Path

    from bot.config import DATA_DIR

    lines = ["<b>🖥️ Estado del bot</b>\n"]
    # BD
    try:
        db_path = DATA_DIR / "bot_database.db"
        con = sqlite3.connect(str(db_path), timeout=10)
        users = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        credits = con.execute("SELECT COUNT(*) FROM credits").fetchone()[0]
        membs = con.execute("SELECT COUNT(*) FROM memberships").fetchone()[0]
        mode = con.execute("PRAGMA journal_mode").fetchone()[0]
        con.close()
        lines.append(f"💾 BD: OK ({users} usuarios, {credits} saldos, {membs} membresías, {mode})")
    except Exception as exc:
        lines.append(f"💾 BD: ERROR ({type(exc).__name__})")
    # Backup diario
    try:
        backups = sorted((DATA_DIR / "backups").glob("bot_database.*.db"))
        if backups:
            lines.append(f"🗄️ Backup: OK ({backups[-1].name})")
        else:
            lines.append("🗄️ Backup: sin archivos")
    except Exception:
        lines.append("🗄️ Backup: ERROR")
    # Variables críticas (solo presencia, nunca valores)
    crit = ["TELEGRAM_BOT_TOKEN", "SUPER_ADMIN_IDS", "TELCEL_DECRYPT_PASSWORD",
            "TELCEL_ENCRYPT_PASSWORD", "TELCEL_T1_BEARER"]
    missing = [v for v in crit if not os.getenv(v)]
    # Compatibilidad: SUPER_ADMINS también vale
    if "SUPER_ADMIN_IDS" in missing and os.getenv("SUPER_ADMINS"):
        missing.remove("SUPER_ADMIN_IDS")
    if missing:
        lines.append(f"⚠️ Env faltantes: {', '.join(missing)}")
    else:
        lines.append("🔑 Env: OK")
    # EmailApi local
    try:
        import urllib.request

        with urllib.request.urlopen("http://127.0.0.1:3000/docs", timeout=5):
            lines.append("📧 EmailApi: OK")
    except Exception:
        lines.append("📧 EmailApi: no responde")
    await message.answer("\n".join(lines), parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "admin_back")
async def cb_admin_back(callback: CallbackQuery):
    """Volver al panel principal de admin."""
    user_id = callback.from_user.id
    if not await is_admin(user_id):
        await callback.answer()
        return

    await callback.message.edit_text(
        _admin_welcome_text(),
        parse_mode=ParseMode.HTML,
        reply_markup=admin_main_menu(is_super=is_super_admin(user_id)),
    )
    await callback.answer()


@router.callback_query(F.data == "admin_close")
async def cb_admin_close(callback: CallbackQuery):
    """Cerrar panel de admin: elimina el mensaje."""
    if not await is_admin(callback.from_user.id):
        await callback.answer()
        return
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer()


# ─────────────────────────────────────────────────────────────
# Asignar / retirar administradores (solo super admins)
# ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_assign")
async def cb_admin_assign(callback: CallbackQuery):
    """Panel de asignación de admins."""
    user_id = callback.from_user.id
    if not is_super_admin(user_id):
        await callback.answer()
        return

    admins = sorted(await get_admin_ids())
    admins_text = "\n".join(f"• <code>{aid}</code>" for aid in admins) or "<i>No hay admins asignados.</i>"

    text = (
        "👥 <b>Gestión de administradores</b>\n\n"
        "Admins actuales:\n"
        f"{admins_text}\n\n"
        "Solo los super admins pueden agregar o quitar administradores."
    )
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=admin_assign_menu())
    await callback.answer()


@router.callback_query(F.data == "admin_add")
async def cb_admin_add(callback: CallbackQuery, state: FSMContext):
    """Inicia flujo para agregar un admin."""
    user_id = callback.from_user.id
    if not is_super_admin(user_id):
        await callback.answer()
        return

    # Limpiar cualquier estado previo para que el handler de texto pueda actuar
    await state.clear()
    _pending_admin_action[user_id] = "add"
    await callback.message.edit_text(
        "➕ <b>Agregar administrador</b>\n\n"
        "Envíame el ID de Telegram del usuario que quieres agregar como admin.",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_subpanel_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin_remove")
async def cb_admin_remove(callback: CallbackQuery, state: FSMContext):
    """Inicia flujo para quitar un admin."""
    user_id = callback.from_user.id
    if not is_super_admin(user_id):
        await callback.answer()
        return

    # Limpiar cualquier estado previo para que el handler de texto pueda actuar
    await state.clear()
    _pending_admin_action[user_id] = "remove"
    await callback.message.edit_text(
        "➖ <b>Quitar administrador</b>\n\n"
        "Envíame el ID de Telegram del admin que quieres quitar.",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_subpanel_menu(),
    )
    await callback.answer()


@router.message(F.text.regexp(r"^\d+$"), StateFilter(None))
async def process_admin_id(message: Message, bot):
    """Procesa ID enviado durante flujo de agregar/quitar admin o acceso /gencookie."""
    user_id = message.from_user.id

    # Prioridad: acciones pendientes de /gencookie
    gc_action = _pending_gencookie_action.pop(user_id, None)
    if gc_action and gc_action.get("action") in ("add", "remove"):
        if not is_super_admin(user_id):
            return

        target_id = int(message.text.strip())
        access_type = gc_action.get("access_type", "user")
        label = "administrador" if access_type == "admin" else "usuario"

        if gc_action["action"] == "add":
            if await grant_access(target_id, user_id, access_type):
                await message.answer(
                    f"✅ El {label} <code>{target_id}</code> ahora tiene acceso a /gencookie.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=admin_subpanel_menu(),
                )
            else:
                await message.answer(
                    f"ℹ️ El {label} <code>{target_id}</code> ya tiene acceso a /gencookie.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=admin_subpanel_menu(),
                )
        else:
            if await revoke_access(target_id):
                await message.answer(
                    f"✅ El {label} <code>{target_id}</code> ya no tiene acceso a /gencookie.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=admin_subpanel_menu(),
                )
            else:
                await message.answer(
                    f"ℹ️ El {label} <code>{target_id}</code> no tenía acceso a /gencookie.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=admin_subpanel_menu(),
                )
        return

    action = _pending_admin_action.pop(user_id, None)
    if action not in ("add", "remove"):
        return

    if not is_super_admin(user_id):
        return

    target_id = int(message.text.strip())

    if action == "add":
        if is_super_admin(target_id):
            await message.answer("⛔ No se puede modificar un super admin.")
            await log_operation_failed(bot, user_id, "agregar admin", "intentó modificar super admin")
        elif await add_admin(target_id):
            await message.answer(
                f"✅ El usuario <code>{target_id}</code> fue agregado como administrador.",
                parse_mode=ParseMode.HTML,
                reply_markup=admin_subpanel_menu(),
            )
            await log_admin_added(bot, user_id, target_id)
        else:
            await message.answer(
                f"ℹ️ El usuario <code>{target_id}</code> ya es administrador.",
                parse_mode=ParseMode.HTML,
                reply_markup=admin_subpanel_menu(),
            )
    elif action == "remove":
        if is_super_admin(target_id):
            await message.answer("⛔ No se puede quitar a un super admin.")
            await log_operation_failed(bot, user_id, "quitar admin", "intentó modificar super admin")
        elif await remove_admin(target_id):
            await message.answer(
                f"✅ El usuario <code>{target_id}</code> fue removido como administrador.",
                parse_mode=ParseMode.HTML,
                reply_markup=admin_subpanel_menu(),
            )
            await log_admin_removed(bot, user_id, target_id)
        else:
            await message.answer(
                f"ℹ️ El usuario <code>{target_id}</code> no es administrador.",
                parse_mode=ParseMode.HTML,
                reply_markup=admin_subpanel_menu(),
            )


# ─────────────────────────────────────────────────────────────
# Gestión de acceso a /gencookie (solo super admins)
# ─────────────────────────────────────────────────────────────

@router.callback_query(F.data.in_({"admin_gencookie_admins", "admin_gencookie_users"}))
async def cb_gencookie_access(callback: CallbackQuery, state: FSMContext):
    """Panel de gestión de acceso a /gencookie."""
    user_id = callback.from_user.id
    if not is_super_admin(user_id):
        await callback.answer()
        return

    access_type = "admin" if callback.data == "admin_gencookie_admins" else "user"
    label = "administradores" if access_type == "admin" else "usuarios"

    access_list = await get_access_list(access_type)
    count = len(access_list)

    text = (
        f"🍪 <b>Acceso a /gencookie - {label.capitalize()}</b>\n\n"
        f"Total con acceso: <b>{count}</b>\n\n"
        "Desde aquí puedes agregar, quitar o ver quién tiene acceso."
    )
    await state.update_data(gencookie_access_type=access_type)
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=gencookie_access_menu())
    await callback.answer()


@router.callback_query(F.data == "gencookie_add")
async def cb_gencookie_add(callback: CallbackQuery, state: FSMContext):
    """Inicia flujo para agregar acceso a /gencookie."""
    user_id = callback.from_user.id
    if not is_super_admin(user_id):
        await callback.answer()
        return

    data = await state.get_data()
    access_type = data.get("gencookie_access_type", "user")
    label = "administrador" if access_type == "admin" else "usuario"

    await state.clear()
    _pending_gencookie_action[user_id] = {"action": "add", "access_type": access_type}
    await callback.message.edit_text(
        f"➕ <b>Agregar acceso a /gencookie</b>\n\n"
        f"Envíame el ID de Telegram del {label} al que le daré acceso.",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_subpanel_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == "gencookie_remove")
async def cb_gencookie_remove(callback: CallbackQuery, state: FSMContext):
    """Inicia flujo para quitar acceso a /gencookie."""
    user_id = callback.from_user.id
    if not is_super_admin(user_id):
        await callback.answer()
        return

    data = await state.get_data()
    access_type = data.get("gencookie_access_type", "user")
    label = "administrador" if access_type == "admin" else "usuario"

    await state.clear()
    _pending_gencookie_action[user_id] = {"action": "remove", "access_type": access_type}
    await callback.message.edit_text(
        f"➖ <b>Quitar acceso a /gencookie</b>\n\n"
        f"Envíame el ID de Telegram del {label} al que le quitaré acceso.",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_subpanel_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == "gencookie_list")
async def cb_gencookie_list(callback: CallbackQuery, state: FSMContext):
    """Muestra la lista de usuarios con acceso a /gencookie."""
    user_id = callback.from_user.id
    if not is_super_admin(user_id):
        await callback.answer()
        return

    data = await state.get_data()
    access_type = data.get("gencookie_access_type", "user")
    label = "administradores" if access_type == "admin" else "usuarios"

    access_list = await get_access_list(access_type)
    if not access_list:
        items_text = "<i>No hay registros.</i>"
    else:
        items_text = "\n".join(
            f"• <code>{row['user_id']}</code> — {row['granted_at'][:10]}"
            for row in access_list
        )

    text = (
        f"🍪 <b>Acceso a /gencookie - {label.capitalize()}</b>\n\n"
        f"{items_text}"
    )
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=gencookie_access_menu())
    await callback.answer()


# ─────────────────────────────────────────────────────────────
# Subpaneles de administración (placeholders)
# ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_users")
async def cb_admin_users(callback: CallbackQuery):
    """Gestión de usuarios que iniciaron el bot."""
    if not await is_admin(callback.from_user.id):
        await callback.answer()
        return

    users = await get_all_users()
    count = len(users)
    sample = users[:5]
    sample_text = "\n".join(
        f"• <code>{u['user_id']}</code> - {u.get('full_name', 'N/A')}"
        for u in sample
    ) or "<i>No hay usuarios registrados.</i>"

    text = (
        "👤 <b>Gestión de usuarios</b>\n\n"
        f"Total de usuarios registrados: <b>{count}</b>\n\n"
        "Últimos usuarios:\n"
        f"{sample_text}\n\n"
        "<i>Desde aquí se podrá buscar, banear o ver detalles de cada usuario.</i>"
    )
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=admin_subpanel_menu())
    await callback.answer()


@router.callback_query(F.data == "admin_memberships")
async def cb_admin_memberships(callback: CallbackQuery, state: FSMContext):
    """Panel de membresías: selección de plan."""
    if not await is_admin(callback.from_user.id):
        await callback.answer()
        return

    await state.set_state(AdminMemberships.plan)
    await callback.message.edit_text(
        "🎟️ <b>Membresías</b>\n\n"
        "Selecciona el plan que quieres asignar.",
        parse_mode=ParseMode.HTML,
        reply_markup=memberships_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin_credits")
async def cb_admin_credits(callback: CallbackQuery, state: FSMContext):
    """Panel de gestión de créditos."""
    if not await is_admin(callback.from_user.id):
        await callback.answer()
        return

    await state.set_state(AdminCredits.action)
    await callback.message.edit_text(
        "💳 <b>Gestión de créditos</b>\n\n"
        "Selecciona una opción.",
        parse_mode=ParseMode.HTML,
        reply_markup=credits_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin_messages")
async def cb_admin_messages(callback: CallbackQuery, state: FSMContext):
    """Panel de mensajes: selección de destinatarios."""
    if not await is_admin(callback.from_user.id):
        await callback.answer()
        return

    await state.set_state(AdminMessages.target)
    await callback.message.edit_text(
        "📨 <b>Mensajes</b>\n\n"
        "Selecciona a quién quieres enviar el mensaje.",
        parse_mode=ParseMode.HTML,
        reply_markup=messages_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin_maintenance")
async def cb_admin_maintenance(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer()
        return

    await callback.message.edit_text(
        "🛠️ <b>Mantenimiento</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_subpanel_menu(),
    )
    await callback.answer()


# ─────────────────────────────────────────────────────────────
# Flujo de envío de mensajes (FSM)
# ─────────────────────────────────────────────────────────────

@router.callback_query(AdminMessages.target, F.data.startswith("msg_target_"))
async def msg_select_target(callback: CallbackQuery, state: FSMContext):
    """Selecciona el destinatario del mensaje."""
    if not await is_admin(callback.from_user.id):
        await callback.answer()
        return

    target = callback.data.replace("msg_target_", "")
    group_targets = {
        "eternal_tartaro": ("Eternal Tartaro", ETERNAL_TARTARO_GROUP_ID),
        "olimpo_bins": ("OLIMPO BINS", OLIMPO_BINS_GROUP_ID),
    }

    if target == "all":
        target_label = "todos los usuarios"
    elif target == "admins":
        target_label = "todos los admins"
    elif target == "user":
        target_label = "un usuario específico"
    elif target in group_targets:
        target_label = group_targets[target][0]
    else:
        await callback.answer("Opción inválida")
        return

    await state.update_data(target=target)

    if target == "user":
        await state.set_state(AdminMessages.user_id)
        await callback.message.edit_text(
            "👤 <b>Enviar a un usuario</b>\n\n"
            "Envíame el ID de Telegram del usuario destinatario.",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_subpanel_menu(),
        )

    else:
        action_text = "publicar y fijar" if target in group_targets else "difundir"
        await state.set_state(AdminMessages.content)
        prompt = await callback.message.edit_text(
            f"📨 <b>Enviar a {target_label}</b>\n\n"
            "Envíame el primer mensaje: texto, foto, video, documento, audio, "
            "nota de voz o GIF.\n\n"
            "Puedes enviar <b>varios elementos seguidos</b> para difundir un "
            f"paquete de multimedia. Cuando termines, toca <b>Listo — vista previa</b>.",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_subpanel_menu(),
        )
        await state.update_data(prompt_msg_id=prompt.message_id)
    await callback.answer()


@router.message(AdminMessages.user_id)
async def msg_receive_user_id(message: Message, state: FSMContext):
    """Recibe el ID de usuario para envío individual."""
    if not await is_admin(message.from_user.id):
        return

    text = message.text.strip()
    if not text.isdigit():
        await message.answer(
            "❌ ID inválido. Envíame solo el número de ID de Telegram.",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_subpanel_menu(),
        )
        return

    await state.update_data(target_user_id=int(text))
    await state.set_state(AdminMessages.content)
    prompt = await message.answer(
        "👤 <b>Contenido del mensaje</b>\n\n"
        "Envíame el primer mensaje: texto, foto, video, documento, audio, "
        "nota de voz o GIF.\n\n"
        "Puedes enviar <b>varios elementos seguidos</b> para difundir un "
        "paquete de multimedia. Cuando termines, toca <b>Listo — vista previa</b>.",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_subpanel_menu(),
    )
    await state.update_data(prompt_msg_id=prompt.message_id)


def _extract_media_info(message: Message) -> dict | None:
    """Extrae información de un mensaje multimedia para reenviarlo."""
    caption_html = message_to_html(message)
    plain_caption = message.caption or ""
    if message.photo:
        return {
            "content_type": "photo",
            "file_id": message.photo[-1].file_id,
            "caption": caption_html,
            "plain_caption": plain_caption,
        }
    if message.video:
        return {
            "content_type": "video",
            "file_id": message.video.file_id,
            "caption": caption_html,
            "plain_caption": plain_caption,
        }
    if message.document:
        return {
            "content_type": "document",
            "file_id": message.document.file_id,
            "caption": caption_html,
            "plain_caption": plain_caption,
        }
    if message.audio:
        return {
            "content_type": "audio",
            "file_id": message.audio.file_id,
            "caption": caption_html,
            "plain_caption": plain_caption,
        }
    if message.voice:
        return {
            "content_type": "voice",
            "file_id": message.voice.file_id,
            "caption": caption_html,
            "plain_caption": plain_caption,
        }
    if message.animation:
        return {
            "content_type": "animation",
            "file_id": message.animation.file_id,
            "caption": caption_html,
            "plain_caption": plain_caption,
        }
    return None


# Máximo de elementos por paquete: el límite duro de Telegram para un
# grupo multimedia es 10; de ahí nos quedamos para no romper nada.
_PACK_LIMIT = 10

_ITEM_EMOJI = {
    "photo": "🖼️",
    "video": "🎥",
    "document": "📄",
    "audio": "🎵",
    "voice": "🎙️",
    "animation": "🎞️",
    "text": "📝",
}

_ITEM_LABEL = {
    "photo": "Foto",
    "video": "Video",
    "document": "Documento",
    "audio": "Audio",
    "voice": "Nota de voz",
    "animation": "GIF",
    "text": "Texto",
}


def _item_from_message(message: Message) -> dict | None:
    """Convierte un mensaje recibido en un elemento del paquete de difusión."""
    media = _extract_media_info(message)
    if media:
        return media
    if message.text:
        return {
            "content_type": "text",
            "content": message_to_html(message),
            "plain_text": message.text,
        }
    return None


def _item_snippet(item: dict, limit: int = 80) -> str:
    plain = item.get("plain_caption") or item.get("plain_text") or ""
    if not plain and item.get("caption"):
        plain = item["caption"]
    plain = escape_html(plain.strip())
    if len(plain) > limit:
        plain = plain[:limit] + "…"
    return plain or "(sin texto)"


def _pack_status_text(items: list) -> str:
    lines = [f"📨 <b>Paquete en armado</b> · {len(items)}/{_PACK_LIMIT}", ""]
    for i, it in enumerate(items, 1):
        ctype = it.get("content_type", "")
        label = f"{_ITEM_EMOJI.get(ctype, '📎')} {_ITEM_LABEL.get(ctype, 'Archivo')}"
        lines.append(f"{i}. {label} — <i>{_item_snippet(it, 60)}</i>")
    lines.append("")
    lines.append("Sigue enviando elementos o toca <b>Listo — vista previa</b>.")
    return "\n".join(lines)


def _target_desc(data: dict) -> tuple[str, str]:
    """Etiqueta de destinatarios y verbo de la acción que confirma el panel."""
    target = data.get("target")
    group_labels = {
        "eternal_tartaro": "Eternal Tartaro",
        "olimpo_bins": "OLIMPO BINS",
    }
    if target in group_labels:
        return group_labels[target], "Enviar y fijar"
    if target == "user":
        return f"usuario <code>{data.get('target_user_id')}</code>", "Enviar"
    if target == "all":
        return "todos los usuarios", "Enviar"
    return "todos los admins", "Enviar"


def _pack_preview(data: dict) -> str:
    items = data.get("items") or []
    target_label, action_label = _target_desc(data)
    lines = [
        "✉️ <b>Vista previa del envío</b>\n",
        f"<b>Destinatarios:</b> {target_label}",
        f"<b>Elementos:</b> {len(items)}",
        "",
    ]
    for i, it in enumerate(items, 1):
        ctype = it.get("content_type", "")
        label = f"{_ITEM_EMOJI.get(ctype, '📎')} {_ITEM_LABEL.get(ctype, 'Archivo')}"
        lines.append(f"{i}. {label} — <i>{_item_snippet(it)}</i>")
    if len(items) > 1:
        lines.append("")
        lines.append("<i>Cada destinatario recibirá los elementos en este orden.</i>")
    lines.append("")
    lines.append(f"¿Confirmas {action_label.lower()}?")
    return "\n".join(lines)


async def _edit_or_new(message: Message, state: FSMContext, text: str, markup) -> None:
    """Actualiza el mensaje de trabajo del panel; si falla, envía uno nuevo."""
    from aiogram.exceptions import TelegramBadRequest

    data = await state.get_data()
    prompt_msg_id = data.get("prompt_msg_id")
    if prompt_msg_id:
        try:
            await message.bot.edit_message_text(
                text=text,
                chat_id=message.chat.id,
                message_id=prompt_msg_id,
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
            )
            return
        except TelegramBadRequest:
            pass
    sent = await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=markup)
    await state.update_data(prompt_msg_id=sent.message_id)


@router.message(AdminMessages.content)
async def msg_receive_content(message: Message, state: FSMContext):
    """Recibe cada elemento del paquete (admite texto y multimedia seguidos)."""
    if not await is_admin(message.from_user.id):
        return

    item = _item_from_message(message)
    if not item:
        await message.answer(
            "❌ Tipo de contenido no soportado.\n\n"
            "Envía texto, foto, video, documento, audio, nota de voz o GIF.",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_subpanel_menu(),
        )
        return

    data = await state.get_data()
    items = data.get("items") or []
    if len(items) >= _PACK_LIMIT:
        await message.answer(
            f"⚠️ Máximo {_PACK_LIMIT} elementos por paquete.\n\n"
            "Toca <b>Listo — vista previa</b> para continuar.",
            parse_mode=ParseMode.HTML,
            reply_markup=message_pack_menu(),
        )
        return

    items.append(item)
    await state.update_data(items=items)
    await _edit_or_new(message, state, _pack_status_text(items), message_pack_menu())


@router.callback_query(AdminMessages.content, F.data == "msg_done")
async def msg_done_items(callback: CallbackQuery, state: FSMContext):
    """Cierra el paquete y muestra la vista previa para confirmar."""
    if not await is_admin(callback.from_user.id):
        await callback.answer()
        return

    data = await state.get_data()
    items = data.get("items") or []
    if not items:
        await callback.answer("Primero envía al menos un elemento.", show_alert=True)
        return

    await state.set_state(AdminMessages.confirm)
    await callback.message.edit_text(
        _pack_preview(data),
        parse_mode=ParseMode.HTML,
        reply_markup=message_confirm_menu(),
    )
    await callback.answer()


@router.callback_query(AdminMessages.content, F.data == "msg_reset")
async def msg_reset_items(callback: CallbackQuery, state: FSMContext):
    """Descarta todo lo acumulado y vuelve a empezar el paquete."""
    if not await is_admin(callback.from_user.id):
        await callback.answer()
        return

    await state.update_data(items=[], prompt_msg_id=callback.message.message_id)
    await callback.message.edit_text(
        "🗑 <b>Empezamos de nuevo</b>\n\n"
        "Envíame el primer mensaje: texto, foto, video, documento, audio, "
        "nota de voz o GIF.",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_subpanel_menu(),
    )
    await callback.answer("Paquete limpio.")


# Tipos admitidos en un álbum de Telegram (send_media_group). Las notas
# de voz y los GIF no se pueden agrupar: van como mensajes sueltos.
_GROUPABLE = {"photo", "video", "document", "audio"}


def _item_runs(items: list) -> list[list]:
    """Divide el paquete en corridas consecutivas del mismo grupo.

    Una corrida de agrupables (máx. 10) viaja como álbum; cada elemento
    no agrupable (texto, nota de voz, GIF) es una corrida de uno.
    """
    runs: list[list] = []
    cur: list = []
    for it in items:
        if it.get("content_type") in _GROUPABLE:
            cur.append(it)
            if len(cur) == 10:
                runs.append(cur)
                cur = []
        else:
            if cur:
                runs.append(cur)
                cur = []
            runs.append([it])
    if cur:
        runs.append(cur)
    return runs


async def _send_album(bot, chat_id: int, items: list):
    """Envía varios archivos agrupables como UN álbum (send_media_group).

    Devuelve el último mensaje del álbum (útil para fijar). Si el HTML de
    alguna descripción es inválido, reintenta el álbum con texto plano.
    """
    from aiogram.exceptions import TelegramBadRequest

    def _album_media(parse_html: bool):
        cls_by_type = {
            "photo": InputMediaPhoto,
            "video": InputMediaVideo,
            "document": InputMediaDocument,
            "audio": InputMediaAudio,
        }
        media = []
        for it in items:
            cls = cls_by_type[
                it["content_type"]
                if it.get("content_type") in cls_by_type
                else "document"
            ]
            caption = it.get("caption") if parse_html else (
                it.get("plain_caption") or it.get("caption") or ""
            )
            media.append(
                cls(
                    media=it["file_id"],
                    caption=caption or None,
                    parse_mode=ParseMode.HTML if parse_html else None,
                )
            )
        return media

    try:
        msgs = await bot.send_media_group(chat_id, media=_album_media(True))
        return msgs[-1] if msgs else None
    except TelegramBadRequest as exc:
        msg_txt = str(exc).lower()
        if "parse" in msg_txt or "entity" in msg_txt:
            msgs = await bot.send_media_group(chat_id, media=_album_media(False))
            return msgs[-1] if msgs else None
        raise


async def _send_content_message(bot, chat_id: int, data: dict):
    """Envía un mensaje de texto o multimedia a un chat. Devuelve el mensaje enviado.

    Si el HTML es inválido, reintenta enviando texto plano (sin formato).
    """
    from aiogram.exceptions import TelegramBadRequest

    content_type = data.get("content_type", "text")
    parse_mode = ParseMode.HTML

    if content_type == "text":
        try:
            return await bot.send_message(chat_id, data.get("content", ""), parse_mode=parse_mode)
        except TelegramBadRequest as exc:
            if "parse" in str(exc).lower() or "entity" in str(exc).lower():
                return await bot.send_message(
                    chat_id,
                    data.get("plain_text", data.get("content", "")),
                )
            raise

    file_id = data.get("file_id")
    caption = data.get("caption", "")
    plain_caption = data.get("plain_caption", "")
    common_args = {"chat_id": chat_id, "caption": caption, "parse_mode": parse_mode}

    send_methods = {
        "photo": bot.send_photo,
        "video": bot.send_video,
        "document": bot.send_document,
        "audio": bot.send_audio,
        "voice": bot.send_voice,
        "animation": bot.send_animation,
    }

    method = send_methods.get(content_type)
    if not method:
        raise ValueError(f"Tipo de contenido no soportado: {content_type}")

    media_arg = "photo" if content_type == "photo" else content_type
    kwargs = {**common_args, media_arg: file_id}
    try:
        return await method(**kwargs)
    except TelegramBadRequest as exc:
        if "parse" in str(exc).lower() or "entity" in str(exc).lower():
            kwargs["caption"] = plain_caption
            kwargs["parse_mode"] = None
            return await method(**kwargs)
        raise


async def _send_items_to_chat(bot, chat_id: int, items: list):
    """Envía los elementos del paquete a un chat respetando el orden.

    Los archivos agrupables (foto/video/documento/audio) consecutivos se
    mandan juntos como UN álbum (hasta 10 por álbum, límite de Telegram);
    los textos, notas de voz y GIF van en su lugar como mensajes sueltos.
    Devuelve (ok: bool, ultimo_mensaje_enviado | None). Un elemento con
    fallo no corta el paquete; solo si NADA se envía se cuenta fallido.
    """
    last_ok = None
    sent_items = 0
    for run in _item_runs(items):
        try:
            if len(run) > 1:
                last_ok = await _send_album(bot, chat_id, run)
            else:
                last_ok = await _send_content_message(bot, chat_id, run[0])
            sent_items += len(run)
        except Exception:
            continue
    return sent_items > 0, last_ok


@router.callback_query(AdminMessages.confirm, F.data == "msg_confirm")
async def msg_confirm_send(callback: CallbackQuery, state: FSMContext, bot):
    """Confirma y envía el paquete (texto o multimedia) a los destinatarios."""
    if not await is_admin(callback.from_user.id):
        await callback.answer()
        return

    data = await state.get_data()
    target = data.get("target")
    target_user_id = data.get("target_user_id")

    items = data.get("items") or []
    if not items and (data.get("file_id") or data.get("content")):
        # Compatibilidad con datos de una sesión anterior.
        items = [
            {k: data[k] for k in (
                "content_type", "file_id", "content",
                "caption", "plain_caption", "plain_text",
            ) if k in data}
        ]
    if not items:
        await callback.answer("Nada pendiente por enviar.", show_alert=True)
        return

    await state.clear()

    sent = 0
    failed = 0
    pinned = 0
    pin_failed = 0

    multi = len(items) > 1

    async def _pack(chat_id: int):
        ok, last = await _send_items_to_chat(bot, chat_id, items)
        return ok, last

    if target == "user":
        ok, _ = await _pack(target_user_id)
        sent, failed = (1, 0) if ok else (0, 1)
    elif target == "all":
        users = await get_all_users()
        for i, user in enumerate(users):
            ok, _ = await _pack(user["user_id"])
            if ok:
                sent += 1
            else:
                failed += 1
            if multi and i < len(users) - 1:
                await asyncio.sleep(0.05)
    elif target == "admins":
        admin_ids = await get_all_admin_ids()
        for i, admin_id in enumerate(admin_ids):
            ok, _ = await _pack(admin_id)
            if ok:
                sent += 1
            else:
                failed += 1
            if multi and i < len(admin_ids) - 1:
                await asyncio.sleep(0.05)
    elif target in ("eternal_tartaro", "olimpo_bins"):
        group_id = ETERNAL_TARTARO_GROUP_ID if target == "eternal_tartaro" else OLIMPO_BINS_GROUP_ID
        ok, last = await _pack(group_id)
        if ok and last:
            sent = 1
            try:
                await bot.pin_chat_message(group_id, last.message_id)
                pinned = 1
            except Exception:
                pin_failed = 1
        else:
            failed = 1

    # Alerta silenciosa al canal de logs
    await log_message_broadcast(bot, callback.from_user.id, target, sent, failed, pinned)

    result_lines = ["✅ <b>Envío completado</b>", ""]
    result_lines.append(f"Elementos por destinatario: <b>{len(items)}</b>")
    if target in ("eternal_tartaro", "olimpo_bins"):
        if pin_failed:
            result_lines.append(f"⚠️ No se pudo fijar: <b>{pin_failed}</b>")
            result_lines.append("<i>Verifica que el bot sea admin con permiso para fijar.</i>")
        if failed:
            result_lines.append(f"❌ Sin entrega: <b>{failed}</b>")
        else:
            result_lines.append("📍 Publicado y fijado.")
    else:
        result_lines.append(f"Entregado a: <b>{sent}</b>")
        if failed:
            result_lines.append(f"❌ Sin entrega: <b>{failed}</b>")

    await callback.message.edit_text(
        "\n".join(result_lines),
        parse_mode=ParseMode.HTML,
        reply_markup=admin_subpanel_menu(),
    )


@router.callback_query(AdminMessages.confirm, F.data == "msg_cancel")
async def msg_cancel_send(callback: CallbackQuery, state: FSMContext):
    """Cancela el envío del mensaje."""
    if not await is_admin(callback.from_user.id):
        await callback.answer()
        return

    await state.clear()
    await callback.message.edit_text(
        "❌ <b>Envío cancelado.</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_subpanel_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == "msg_cancel")
async def msg_cancel_flow(callback: CallbackQuery, state: FSMContext):
    """Cancela el flujo de mensajes desde cualquier paso."""
    if not await is_admin(callback.from_user.id):
        await callback.answer()
        return

    await state.clear()
    await callback.message.edit_text(
        "❌ <b>Envío cancelado.</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_subpanel_menu(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("msg_"))
async def msg_fallback(callback: CallbackQuery, state: FSMContext):
    """Red de seguridad: responde botones huérfanos para no congelar."""
    if not await is_admin(callback.from_user.id):
        await callback.answer()
        return

    await state.clear()
    await callback.answer(
        "La sesión del panel expiró. Abre de nuevo con /batman.",
        show_alert=True,
    )


# ─────────────────────────────────────────────────────────────
# Flujo de asignación de membresías (FSM)
# ─────────────────────────────────────────────────────────────

@router.message(AdminMemberships.plan)
async def mem_plan_text_help(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    await state.clear()
    await message.answer(
        "🎟️ <b>Menú de membresías</b>\n\n"
        "Usa los botones de abajo para seleccionar el plan. "
        "Si cambias de opinión, envía <code>/batman</code> para reiniciar el panel.",
        parse_mode=ParseMode.HTML,
        reply_markup=memberships_menu(),
    )


@router.callback_query(AdminMemberships.plan, F.data.startswith("mem_assign_"))
async def mem_select_plan(callback: CallbackQuery, state: FSMContext):
    """Selecciona el plan de membresía a asignar."""
    if not await is_admin(callback.from_user.id):
        await callback.answer()
        return

    plan = callback.data.replace("mem_assign_", "")
    if plan not in PLAN_LABELS:
        await callback.answer("Plan inválido")
        return

    await state.update_data(plan=plan)

    if plan == "personalizado":
        await state.set_state(AdminMemberships.custom_days)
        await callback.message.edit_text(
            "📅 <b>Membresía personalizada</b>\n\n"
            "Envíame la cantidad de días que quieres asignar.",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_subpanel_menu(),
        )
    else:
        await state.set_state(AdminMemberships.beneficiary)
        await callback.message.edit_text(
            f"🎟️ <b>Asignar membresía {PLAN_LABELS[plan]}</b>\n\n"
            "Envíame el ID de Telegram del beneficiario.",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_subpanel_menu(),
        )
    await callback.answer()


@router.message(AdminMemberships.custom_days)
async def mem_receive_custom_days(message: Message, state: FSMContext):
    """Recibe la cantidad de días para membresía personalizada."""
    if not await is_admin(message.from_user.id):
        return

    text = message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await message.answer(
            "❌ Cantidad inválida. Envíame un número entero mayor a 0.",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_subpanel_menu(),
        )
        return

    days = int(text)
    await state.update_data(custom_days=days)
    await state.set_state(AdminMemberships.beneficiary)
    await message.answer(
        "👤 <b>Beneficiario</b>\n\n"
        "Envíame el ID de Telegram del beneficiario.",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_subpanel_menu(),
    )


@router.message(AdminMemberships.beneficiary)
async def mem_receive_beneficiary(message: Message, state: FSMContext):
    """Recibe el ID del beneficiario y pide confirmación."""
    if not await is_admin(message.from_user.id):
        return

    text = message.text.strip()
    if not text.isdigit():
        await message.answer(
            "❌ ID inválido. Envíame solo el número de ID de Telegram.",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_subpanel_menu(),
        )
        return

    beneficiary_id = int(text)
    data = await state.get_data()
    plan = data.get("plan")
    custom_days = data.get("custom_days")
    existing = await get_membership(beneficiary_id)
    existing_text = ""
    if existing:
        existing_text = (
            f"\n\n⚠️ El usuario ya tiene una membresía activa:\n"
            f"• Plan: <b>{existing['label']}</b>\n"
            f"• Vencimiento: <b>{format_end_date(existing)}</b>\n"
            f"Al confirmar se reemplazará."
        )

    await state.update_data(beneficiary_id=beneficiary_id)
    await state.set_state(AdminMemberships.confirm)

    plan_text = f"{PLAN_LABELS[plan]} ({custom_days} días)" if plan == "personalizado" else PLAN_LABELS[plan]

    await message.answer(
        f"🎟️ <b>Confirmar asignación</b>\n\n"
        f"• Plan: <b>{plan_text}</b>\n"
        f"• Beneficiario: <code>{beneficiary_id}</code>"
        f"{existing_text}\n\n"
        "¿Confirmas la asignación?",
        parse_mode=ParseMode.HTML,
        reply_markup=membership_confirm_menu(),
    )


@router.callback_query(AdminMemberships.confirm, F.data == "mem_confirm")
async def mem_confirm_assign(callback: CallbackQuery, state: FSMContext, bot):
    """Confirma y asigna la membresía, notificando al beneficiario."""
    if not await is_admin(callback.from_user.id):
        await callback.answer()
        return

    data = await state.get_data()
    plan = data.get("plan")
    beneficiary_id = data.get("beneficiary_id")
    custom_days = data.get("custom_days")
    await state.clear()

    try:
        record = await assign_membership(beneficiary_id, plan, custom_days=custom_days)
        end_date = format_end_date(record)
        await log_membership_assigned(
            bot,
            callback.from_user.id,
            beneficiary_id,
            record["plan"],
            end_date,
        )

        gift_emoji = '<tg-emoji emoji-id="5427315847129478207">🎁</tg-emoji>'

        notified = False
        try:
            await bot.send_message(
                beneficiary_id,
                f"{gift_emoji} <b>¡Tu membresía ha sido activada!</b>\n\n"
                f"• Plan: <b>{record['plan']}</b>\n"
                f"• Vencimiento: <b>{end_date}</b>\n\n"
                "Disfruta de tus beneficios.",
                parse_mode=ParseMode.HTML,
            )
            notified = True
        except Exception:
            notified = False

        notification_text = "✅ Usuario notificado." if notified else "⚠️ No se pudo notificar al usuario (quizás bloqueó al bot)."

        await callback.message.edit_text(
            f"✅ <b>Membresía asignada</b>\n\n"
            f"• Plan: <b>{record['plan']}</b>\n"
            f"• Beneficiario: <code>{beneficiary_id}</code>\n"
            f"• Vencimiento: <b>{end_date}</b>\n\n"
            f"{notification_text}",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_subpanel_menu(),
        )
        await callback.answer()

    except Exception as e:
        await state.clear()
        await callback.message.edit_text(
            f"❌ <b>Error al asignar la membresía.</b>\n\n"
            f"Detalle: <code>{str(e)}</code>\n\n"
            "Intenta de nuevo o reporta al administrador.",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_subpanel_menu(),
        )
        await callback.answer()


@router.callback_query(AdminMemberships.confirm, F.data == "mem_cancel")
async def mem_cancel_assign(callback: CallbackQuery, state: FSMContext):
    """Cancela la asignación de membresía."""
    if not await is_admin(callback.from_user.id):
        await callback.answer()
        return

    await state.clear()
    await callback.message.edit_text(
        "❌ <b>Asignación cancelada.</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_subpanel_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == "mem_cancel")
async def mem_cancel_flow(callback: CallbackQuery, state: FSMContext):
    """Cancela el flujo de membresías desde cualquier paso."""
    if not await is_admin(callback.from_user.id):
        await callback.answer()
        return

    await state.clear()
    await callback.message.edit_text(
        "❌ <b>Asignación cancelada.</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_subpanel_menu(),
    )
    await callback.answer()


# ─────────────────────────────────────────────────────────────
# Flujo de asignación de galletas (FSM)
# ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_galletas")
async def cb_admin_galletas(callback: CallbackQuery, state: FSMContext):
    """Panel de galletas: selección de paquete."""
    if not await is_admin(callback.from_user.id):
        await callback.answer()
        return

    await state.set_state(AdminGalletas.tipo)
    await callback.message.edit_text(
        "🍪 <b>Galletas</b>\n\n"
        "Selecciona el paquete que quieres asignar.\n"
        "Cada paquete desbloquea <b>/gencookie</b> y se suma al saldo "
        "del usuario (Olimpo: +30, Rancias: +20).",
        parse_mode=ParseMode.HTML,
        reply_markup=galletas_menu(),
    )
    await callback.answer()


@router.message(AdminGalletas.tipo)
async def gal_tipo_text_help(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    await state.clear()
    await message.answer(
        "🍪 <b>Menú de galletas</b>\n\n"
        "Usa los botones de abajo para seleccionar el paquete. "
        "Si cambias de opinión, envía <code>/batman</code> para reiniciar el panel.",
        parse_mode=ParseMode.HTML,
        reply_markup=galletas_menu(),
    )


@router.callback_query(AdminGalletas.tipo, F.data.startswith("gal_assign_"))
async def gal_select_tipo(callback: CallbackQuery, state: FSMContext):
    """Selecciona el paquete de galletas a asignar."""
    if not await is_admin(callback.from_user.id):
        await callback.answer()
        return

    tipo = callback.data.replace("gal_assign_", "")
    if tipo not in TIPO_AMOUNT:
        await callback.answer("Paquete inválido")
        return

    await state.update_data(tipo=tipo)
    await state.set_state(AdminGalletas.beneficiary)
    await callback.message.edit_text(
        f"🍪 <b>Asignar galletas {TIPO_LABELS[tipo]}</b> "
        f"(<b>{TIPO_AMOUNT[tipo]}</b> cuentas)\n\n"
        "Envíame el ID de Telegram del beneficiario.",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_subpanel_menu(),
    )
    await callback.answer()


@router.message(AdminGalletas.beneficiary)
async def gal_receive_beneficiary(message: Message, state: FSMContext):
    """Recibe el ID del beneficiario y pide confirmación."""
    if not await is_admin(message.from_user.id):
        return

    text = message.text.strip()
    if not text.isdigit():
        await message.answer(
            "❌ ID inválido. Envíame solo el número de ID de Telegram.",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_subpanel_menu(),
        )
        return

    beneficiary_id = int(text)
    data = await state.get_data()
    tipo = data.get("tipo")
    if tipo not in TIPO_AMOUNT:
        await state.clear()
        await message.answer(
            "❌ Sesión inválida. Abre de nuevo el panel con /batman.",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_subpanel_menu(),
        )
        return
    existing = await get_galletas(beneficiary_id)
    existing_text = ""
    if existing:
        final_left = existing["left"] + TIPO_AMOUNT[tipo]
        existing_text = (
            f"\n\n⚠️ El usuario ya tiene galletas:\n"
            f"• Paquete actual: <b>{existing['label']}</b>\n"
            f"• Saldo actual: <b>{existing['left']}</b>\n"
            f"Se sumarán <b>{TIPO_AMOUNT[tipo]}</b> → saldo final: <b>{final_left}</b>."
        )

    await state.update_data(beneficiary_id=beneficiary_id)
    await state.set_state(AdminGalletas.confirm)

    await message.answer(
        f"🍪 <b>Confirmar asignación</b>\n\n"
        f"• Paquete: <b>{TIPO_LABELS[tipo]}</b>\n"
        f"• Galletas: <b>{TIPO_AMOUNT[tipo]}</b>\n"
        f"• Beneficiario: <code>{beneficiary_id}</code>"
        f"{existing_text}\n\n"
        "¿Confirmas la asignación?",
        parse_mode=ParseMode.HTML,
        reply_markup=galletas_confirm_menu(),
    )


@router.callback_query(AdminGalletas.confirm, F.data == "gal_confirm")
async def gal_confirm_assign(callback: CallbackQuery, state: FSMContext, bot):
    """Confirma y asigna las galletas, desbloqueando /gencookie."""
    if not await is_admin(callback.from_user.id):
        await callback.answer()
        return

    data = await state.get_data()
    tipo = data.get("tipo")
    beneficiary_id = data.get("beneficiary_id")
    await state.clear()

    try:
        record = await grant_galletas(beneficiary_id, tipo, callback.from_user.id)
        await grant_access(beneficiary_id, callback.from_user.id, "user")
        await log_galletas_assigned(
            bot,
            callback.from_user.id,
            beneficiary_id,
            record["label"],
            record["total"],
        )

        gift_emoji = '<tg-emoji emoji-id="5427315847129478207">🎁</tg-emoji>'

        notified = False
        beneficiary_label = "USUARIO" if tipo == "rancias" else record["label"]
        try:
            await bot.send_message(
                beneficiary_id,
                f"{gift_emoji} <b>¡Recibiste galletas!</b>\n\n"
                f"• Paquete: <b>{beneficiary_label}</b>\n"
                f"• Se sumaron: <b>{record['added']}</b>\n"
                f"• Saldo actual: <b>{record['left']}</b>\n\n"
                "Ya puedes generar cuentas con /gencookie.",
                parse_mode=ParseMode.HTML,
            )
            notified = True
        except Exception:
            notified = False

        notification_text = "✅ Usuario notificado." if notified else "⚠️ No se pudo notificar al usuario (quizás bloqueó al bot)."

        await callback.message.edit_text(
            f"✅ <b>Galletas asignadas</b>\n\n"
            f"• Paquete: <b>{record['label']}</b>\n"
            f"• Se sumaron: <b>{record['added']}</b>\n"
            f"• Beneficiario: <code>{beneficiary_id}</code>\n"
            f"• Saldo actual: <b>{record['left']}</b>\n\n"
            f"{notification_text}",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_subpanel_menu(),
        )
        await callback.answer()

    except Exception as e:
        await state.clear()
        await callback.message.edit_text(
            f"❌ <b>Error al asignar las galletas.</b>\n\n"
            f"Detalle: <code>{str(e)}</code>\n\n"
            "Intenta de nuevo o reporta al administrador.",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_subpanel_menu(),
        )
        await callback.answer()


@router.callback_query(AdminGalletas.confirm, F.data == "gal_cancel")
async def gal_cancel_assign(callback: CallbackQuery, state: FSMContext):
    """Cancela la asignación de galletas."""
    if not await is_admin(callback.from_user.id):
        await callback.answer()
        return

    await state.clear()
    await callback.message.edit_text(
        "❌ <b>Asignación cancelada.</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_subpanel_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == "gal_cancel")
async def gal_cancel_flow(callback: CallbackQuery, state: FSMContext):
    """Cancela el flujo de galletas desde cualquier paso."""
    if not await is_admin(callback.from_user.id):
        await callback.answer()
        return

    await state.clear()
    await callback.message.edit_text(
        "❌ <b>Asignación cancelada.</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_subpanel_menu(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("gal_"))
async def gal_fallback(callback: CallbackQuery, state: FSMContext):
    """Red de seguridad: si el estado del panel se perdió (p. ej. reinicio
    del bot a mitad del flujo), ningún handler con estado coincide y el
    botón quedaría girando para siempre. Se responde el callback para
    no dejar el panel congelado."""
    if not await is_admin(callback.from_user.id):
        await callback.answer()
        return

    await state.clear()
    await callback.answer(
        "La sesión del panel expiró. Abre de nuevo con /batman.",
        show_alert=True,
    )


# ─────────────────────────────────────────────────────────────
# Flujo de matanga (FSM, solo super admins)
# ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_matanga")
async def cb_admin_matanga(callback: CallbackQuery, state: FSMContext):
    """Panel de matanga: quitar galletas o créditos."""
    if not is_super_admin(callback.from_user.id):
        await callback.answer()
        return

    await state.set_state(AdminMatanga.action)
    await callback.message.edit_text(
        "💀 <b>Matanga</b>\n\n"
        "Quita todo el saldo de galletas o de créditos de un usuario.\n"
        "Selecciona qué quieres quitar.",
        parse_mode=ParseMode.HTML,
        reply_markup=matanga_menu(),
    )
    await callback.answer()


@router.message(AdminMatanga.action)
async def mat_action_text_help(message: Message, state: FSMContext):
    if not is_super_admin(message.from_user.id):
        return
    await state.clear()
    await message.answer(
        "💀 <b>Menú de matanga</b>\n\n"
        "Usa los botones de abajo. "
        "Si cambias de opinión, envía <code>/batman</code> para reiniciar el panel.",
        parse_mode=ParseMode.HTML,
        reply_markup=matanga_menu(),
    )


@router.callback_query(AdminMatanga.action, F.data.startswith("mat_remove_"))
async def mat_select_action(callback: CallbackQuery, state: FSMContext):
    """Selecciona qué quitar: galletas o créditos."""
    if not is_super_admin(callback.from_user.id):
        await callback.answer()
        return

    action = callback.data.replace("mat_remove_", "")
    if action not in ("galletas", "creditos"):
        await callback.answer("Opción inválida")
        return

    await state.update_data(action=action)
    await state.set_state(AdminMatanga.beneficiary)
    await callback.message.edit_text(
        f"💀 <b>Matanga: quitar {action}</b>\n\n"
        "Envíame el ID de Telegram del usuario afectado.",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_subpanel_menu(),
    )
    await callback.answer()


@router.message(AdminMatanga.beneficiary)
async def mat_receive_beneficiary(message: Message, state: FSMContext):
    """Recibe el ID del afectado, muestra su saldo y pide confirmación."""
    if not is_super_admin(message.from_user.id):
        return

    text = message.text.strip()
    if not text.isdigit():
        await message.answer(
            "❌ ID inválido. Envíame solo el número de ID de Telegram.",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_subpanel_menu(),
        )
        return

    beneficiary_id = int(text)
    data = await state.get_data()
    action = data.get("action")

    if action == "galletas":
        current = await get_galletas(beneficiary_id)
        current_text = f"<b>{current['left']}</b> galletas ({current['label']})" if current else "sin galletas"
    else:
        balance = await get_balance(beneficiary_id)
        current_text = f"<b>{balance}</b> créditos" if balance > 0 else "sin créditos"

    if current_text.startswith("sin"):
        await state.clear()
        await message.answer(
            f"ℹ️ El usuario <code>{beneficiary_id}</code> está {current_text}. No hay nada que quitar.",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_subpanel_menu(),
        )
        return

    await state.update_data(beneficiary_id=beneficiary_id)
    await state.set_state(AdminMatanga.confirm)

    await message.answer(
        f"💀 <b>Confirmar matanga</b>\n\n"
        f"• Quitar: <b>{action}</b>\n"
        f"• Usuario: <code>{beneficiary_id}</code>\n"
        f"• Saldo actual: {current_text}\n\n"
        "¿Confirmas que quieres dejarlo en cero?",
        parse_mode=ParseMode.HTML,
        reply_markup=matanga_confirm_menu(),
    )


@router.callback_query(AdminMatanga.confirm, F.data == "mat_confirm")
async def mat_confirm_remove(callback: CallbackQuery, state: FSMContext, bot):
    """Aplica la matanga: deja en cero galletas o créditos."""
    if not is_super_admin(callback.from_user.id):
        await callback.answer()
        return

    data = await state.get_data()
    action = data.get("action")
    beneficiary_id = data.get("beneficiary_id")
    await state.clear()

    if action not in ("galletas", "creditos") or not beneficiary_id:
        await callback.message.edit_text(
            "❌ Sesión inválida. Abre de nuevo el panel con /batman.",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_subpanel_menu(),
        )
        await callback.answer()
        return

    try:
        if action == "galletas":
            removed = await remove_galletas(beneficiary_id)
            detail = "cupo de galletas eliminado"
        else:
            removed, previous = await remove_credits(beneficiary_id, callback.from_user.id)
            detail = f"{previous} créditos retirados"

        if not removed:
            await callback.message.edit_text(
                "ℹ️ El usuario ya no tenía saldo. No se hizo ningún cambio.",
                parse_mode=ParseMode.HTML,
                reply_markup=admin_subpanel_menu(),
            )
            await callback.answer()
            return

        await log_matanga(bot, callback.from_user.id, beneficiary_id, action, detail)

        await callback.message.edit_text(
            f"💀 <b>Matanga aplicada</b>\n\n"
            f"• Quitado: <b>{action}</b>\n"
            f"• Usuario: <code>{beneficiary_id}</code>\n"
            f"• Detalle: {detail}",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_subpanel_menu(),
        )
        await callback.answer()

    except Exception as e:
        await state.clear()
        await callback.message.edit_text(
            f"❌ <b>Error al aplicar la matanga.</b>\n\n"
            f"Detalle: <code>{str(e)}</code>\n\n"
            "Intenta de nuevo o reporta al administrador.",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_subpanel_menu(),
        )
        await callback.answer()


@router.callback_query(AdminMatanga.confirm, F.data == "mat_cancel")
async def mat_cancel_remove(callback: CallbackQuery, state: FSMContext):
    """Cancela la matanga."""
    if not is_super_admin(callback.from_user.id):
        await callback.answer()
        return

    await state.clear()
    await callback.message.edit_text(
        "❌ <b>Operación cancelada.</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_subpanel_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == "mat_cancel")
async def mat_cancel_flow(callback: CallbackQuery, state: FSMContext):
    """Cancela el flujo de matanga desde cualquier paso."""
    if not is_super_admin(callback.from_user.id):
        await callback.answer()
        return

    await state.clear()
    await callback.message.edit_text(
        "❌ <b>Operación cancelada.</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_subpanel_menu(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("mat_"))
async def mat_fallback(callback: CallbackQuery, state: FSMContext):
    """Red de seguridad: responde botones huérfanos para no congelar."""
    if not is_super_admin(callback.from_user.id):
        await callback.answer()
        return

    await state.clear()
    await callback.answer(
        "La sesión del panel expiró. Abre de nuevo con /batman.",
        show_alert=True,
    )


# ─────────────────────────────────────────────────────────────
# Flujo de ajuste manual de galletas (FSM, solo super admins)
# ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "admin_ajuste")
async def cb_admin_ajuste(callback: CallbackQuery, state: FSMContext):
    """Panel de ajuste manual: recargar o fijar cupo de galletas."""
    if not is_super_admin(callback.from_user.id):
        await callback.answer()
        return

    await state.set_state(AdminAjuste.action)
    await callback.message.edit_text(
        "🧰 <b>Ajuste manual de galletas</b>\n\n"
        "• <b>Recargar al total</b>: devuelve el saldo al total del paquete.\n"
        "• <b>Fijar cupo</b>: deja el cupo en el número que indiques (1-1000).",
        parse_mode=ParseMode.HTML,
        reply_markup=ajuste_menu(),
    )
    await callback.answer()


@router.message(AdminAjuste.action)
async def aj_action_text_help(message: Message, state: FSMContext):
    if not is_super_admin(message.from_user.id):
        return
    await state.clear()
    await message.answer(
        "🧰 <b>Ajuste manual de galletas</b>\n\n"
        "Usa los botones de abajo. "
        "Si cambias de opinión, envía <code>/batman</code> para reiniciar el panel.",
        parse_mode=ParseMode.HTML,
        reply_markup=ajuste_menu(),
    )


@router.callback_query(AdminAjuste.action, F.data.startswith("aj_action_"))
async def aj_select_action(callback: CallbackQuery, state: FSMContext):
    """Selecciona recargar o fijar cupo."""
    if not is_super_admin(callback.from_user.id):
        await callback.answer()
        return

    action = callback.data.replace("aj_action_", "")
    if action not in ("refill", "set"):
        await callback.answer("Opción inválida")
        return

    await state.update_data(action=action)
    await state.set_state(AdminAjuste.beneficiary)
    await callback.message.edit_text(
        "🧰 <b>Ajuste manual</b>\n\n"
        "Envíame el ID de Telegram del usuario.",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_subpanel_menu(),
    )
    await callback.answer()


@router.message(AdminAjuste.beneficiary)
async def aj_receive_beneficiary(message: Message, state: FSMContext):
    """Recibe el ID y sigue según la acción (recargar o pedir monto)."""
    if not is_super_admin(message.from_user.id):
        return

    text = message.text.strip()
    if not text.isdigit():
        await message.answer(
            "❌ ID inválido. Envíame solo el número de ID de Telegram.",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_subpanel_menu(),
        )
        return

    beneficiary_id = int(text)
    data = await state.get_data()
    action = data.get("action")
    if action not in ("refill", "set"):
        await state.clear()
        await message.answer(
            "❌ Sesión inválida. Abre de nuevo el panel con /batman.",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_subpanel_menu(),
        )
        return

    existing = await get_galletas(beneficiary_id)
    if action == "refill" and not existing:
        await state.clear()
        await message.answer(
            f"ℹ️ El usuario <code>{beneficiary_id}</code> no tiene galletas. No hay nada que recargar.",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_subpanel_menu(),
        )
        return

    if action == "refill":
        await state.update_data(beneficiary_id=beneficiary_id)
        await state.set_state(AdminAjuste.confirm)
        await message.answer(
            f"🧰 <b>Confirmar recarga</b>\n\n"
            f"• Usuario: <code>{beneficiary_id}</code>\n"
            f"• Paquete: <b>{existing['label']}</b>\n"
            f"• Saldo actual: <b>{existing['left']}</b> de {existing['total']}\n"
            f"• Saldo final: <b>{existing['total']}</b>\n\n"
            "¿Confirmas la recarga?",
            parse_mode=ParseMode.HTML,
            reply_markup=ajuste_confirm_menu(),
        )
    else:
        current_text = (
            f"• Paquete: <b>{existing['label']}</b>\n"
            f"• Saldo actual: <b>{existing['left']}</b> de {existing['total']}\n"
            if existing else "• Sin galletas actualmente.\n"
        )
        await state.update_data(beneficiary_id=beneficiary_id)
        await state.set_state(AdminAjuste.amount)
        await message.answer(
            f"🧰 <b>Fijar cupo</b>\n\n"
            f"• Usuario: <code>{beneficiary_id}</code>\n"
            f"{current_text}\n"
            "Envíame el nuevo cupo (número entre 1 y 1000).",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_subpanel_menu(),
        )


@router.message(AdminAjuste.amount)
async def aj_receive_amount(message: Message, state: FSMContext):
    """Recibe el cupo a fijar y pide confirmación."""
    if not is_super_admin(message.from_user.id):
        return

    text = message.text.strip()
    if not text.isdigit() or not 1 <= int(text) <= 1000:
        await message.answer(
            "❌ Monto inválido. Envíame un número entre 1 y 1000.",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_subpanel_menu(),
        )
        return

    amount = int(text)
    data = await state.get_data()
    beneficiary_id = data.get("beneficiary_id")
    await state.update_data(amount=amount)
    await state.set_state(AdminAjuste.confirm)

    await message.answer(
        f"🧰 <b>Confirmar cupo</b>\n\n"
        f"• Usuario: <code>{beneficiary_id}</code>\n"
        f"• Nuevo cupo: <b>{amount}</b>\n\n"
        "¿Confirmas el ajuste?",
        parse_mode=ParseMode.HTML,
        reply_markup=ajuste_confirm_menu(),
    )


@router.callback_query(AdminAjuste.confirm, F.data == "aj_confirm")
async def aj_confirm_apply(callback: CallbackQuery, state: FSMContext, bot):
    """Aplica la recarga o el cupo fijado."""
    if not is_super_admin(callback.from_user.id):
        await callback.answer()
        return

    data = await state.get_data()
    action = data.get("action")
    beneficiary_id = data.get("beneficiary_id")
    amount = data.get("amount")
    await state.clear()

    if action not in ("refill", "set") or not beneficiary_id:
        await callback.message.edit_text(
            "❌ Sesión inválida. Abre de nuevo el panel con /batman.",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_subpanel_menu(),
        )
        await callback.answer()
        return

    try:
        if action == "refill":
            record = await refill_galletas(beneficiary_id)
            if not record:
                await callback.message.edit_text(
                    "ℹ️ El usuario no tiene galletas. No se hizo ningún cambio.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=admin_subpanel_menu(),
                )
                await callback.answer()
                return
            detail = f"recarga a {record['total']}"
            new_left = record["left"]
            label = record["label"]
        else:
            record = await set_galletas(beneficiary_id, amount, callback.from_user.id)
            await grant_access(beneficiary_id, callback.from_user.id, "user")
            detail = f"cupo fijado en {amount}"
            new_left = record["left"]
            label = record["label"]

        await log_galletas_assigned(bot, callback.from_user.id, beneficiary_id, f"Ajuste ({label})", new_left)

        gift_emoji = '<tg-emoji emoji-id="5427315847129478207">🎁</tg-emoji>'
        notified = False
        try:
            await bot.send_message(
                beneficiary_id,
                f"{gift_emoji} <b>¡Recibiste galletas!</b>\n\n"
                f"• Saldo actual: <b>{new_left}</b>\n\n"
                "Ya puedes generar cuentas con /gencookie.",
                parse_mode=ParseMode.HTML,
            )
            notified = True
        except Exception:
            notified = False

        notification_text = "✅ Usuario notificado." if notified else "⚠️ No se pudo notificar al usuario (quizás bloqueó al bot)."

        await callback.message.edit_text(
            f"✅ <b>Ajuste aplicado</b>\n\n"
            f"• Usuario: <code>{beneficiary_id}</code>\n"
            f"• Acción: {detail}\n"
            f"• Saldo actual: <b>{new_left}</b>\n\n"
            f"{notification_text}",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_subpanel_menu(),
        )
        await callback.answer()

    except Exception as e:
        await state.clear()
        await callback.message.edit_text(
            f"❌ <b>Error al aplicar el ajuste.</b>\n\n"
            f"Detalle: <code>{str(e)}</code>\n\n"
            "Intenta de nuevo o reporta al administrador.",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_subpanel_menu(),
        )
        await callback.answer()


@router.callback_query(AdminAjuste.confirm, F.data == "aj_cancel")
async def aj_cancel_apply(callback: CallbackQuery, state: FSMContext):
    """Cancela el ajuste manual."""
    if not is_super_admin(callback.from_user.id):
        await callback.answer()
        return

    await state.clear()
    await callback.message.edit_text(
        "❌ <b>Operación cancelada.</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_subpanel_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == "aj_cancel")
async def aj_cancel_flow(callback: CallbackQuery, state: FSMContext):
    """Cancela el flujo de ajuste desde cualquier paso."""
    if not is_super_admin(callback.from_user.id):
        await callback.answer()
        return

    await state.clear()
    await callback.message.edit_text(
        "❌ <b>Operación cancelada.</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_subpanel_menu(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("aj_"))
async def aj_fallback(callback: CallbackQuery, state: FSMContext):
    """Red de seguridad: responde botones huérfanos para no congelar."""
    if not is_super_admin(callback.from_user.id):
        await callback.answer()
        return

    await state.clear()
    await callback.answer(
        "La sesión del panel expiró. Abre de nuevo con /batman.",
        show_alert=True,
    )


# ─────────────────────────────────────────────────────────────
# Flujo de eliminación de membresías (FSM)
# ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "mem_remove_start")
async def mem_remove_start(callback: CallbackQuery, state: FSMContext):
    """Inicia el flujo para quitar una membresía."""
    if not await is_admin(callback.from_user.id):
        await callback.answer()
        return

    await state.set_state(AdminRemoveMembership.beneficiary)
    await callback.message.edit_text(
        "➖ <b>Quitar membresía</b>\n\n"
        "Envíame el ID de Telegram del usuario afectado.",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_subpanel_menu(),
    )
    await callback.answer()


@router.message(AdminRemoveMembership.beneficiary)
async def mem_remove_receive_id(message: Message, state: FSMContext):
    """Recibe el ID del usuario cuya membresía se quiere quitar."""
    if not await is_admin(message.from_user.id):
        return

    text = message.text.strip()
    if not text.isdigit():
        await message.answer(
            "❌ ID inválido. Envíame solo el número de ID de Telegram.",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_subpanel_menu(),
        )
        return

    beneficiary_id = int(text)
    existing = await get_membership(beneficiary_id)

    if not existing:
        await message.answer(
            "ℹ️ El usuario no tiene una membresía activa.",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_subpanel_menu(),
        )
        await state.clear()
        return

    await state.update_data(beneficiary_id=beneficiary_id)
    await state.set_state(AdminRemoveMembership.confirm)

    await message.answer(
        "➖ <b>Confirmar eliminación</b>\n\n"
        f"• Usuario: <code>{beneficiary_id}</code>\n"
        f"• Plan actual: <b>{existing['label']}</b>\n"
        f"• Vencimiento: <b>{format_end_date(existing)}</b>\n\n"
        "¿Confirmas que quieres quitar esta membresía?",
        parse_mode=ParseMode.HTML,
        reply_markup=membership_remove_menu(),
    )


@router.callback_query(AdminRemoveMembership.confirm, F.data == "mem_confirm_remove")
async def mem_confirm_remove(callback: CallbackQuery, state: FSMContext, bot):
    """Confirma y elimina la membresía, notificando al usuario afectado."""
    if not await is_admin(callback.from_user.id):
        await callback.answer()
        return

    data = await state.get_data()
    beneficiary_id = data.get("beneficiary_id")
    await state.clear()

    removed = await remove_membership(beneficiary_id)

    # Alerta silenciosa al canal de logs
    await log_membership_removed(bot, callback.from_user.id, beneficiary_id)

    if removed:
        try:
            await bot.send_message(
                beneficiary_id,
                "🚫 <b>Tu membresía ha sido cancelada.</b>\n\n"
                "Si crees que es un error, contacta a un administrador.",
                parse_mode=ParseMode.HTML,
            )
            notified = True
        except Exception:
            notified = False

        notification_text = "✅ Usuario notificado." if notified else "⚠️ No se pudo notificar al usuario."
        await callback.message.edit_text(
            f"✅ <b>Membresía removida</b>\n\n"
            f"• Usuario: <code>{beneficiary_id}</code>\n\n"
            f"{notification_text}",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_subpanel_menu(),
        )
    else:
        await callback.message.edit_text(
            "ℹ️ El usuario no tenía una membresía activa.",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_subpanel_menu(),
        )
    await callback.answer()


@router.callback_query(AdminRemoveMembership.confirm, F.data == "mem_cancel")
async def mem_remove_cancel(callback: CallbackQuery, state: FSMContext):
    """Cancela la eliminación de membresía."""
    if not await is_admin(callback.from_user.id):
        await callback.answer()
        return

    await state.clear()
    await callback.message.edit_text(
        "❌ <b>Eliminación cancelada.</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_subpanel_menu(),
    )
    await callback.answer()


# ─────────────────────────────────────────────────────────────
# Flujo de gestión de créditos (FSM)
# ─────────────────────────────────────────────────────────────

@router.callback_query(AdminCredits.action, F.data.in_({"credits_add", "credits_withdraw", "credits_query"}))
async def credits_select_action(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Selecciona la acción a realizar con los créditos."""
    if not await is_admin(callback.from_user.id):
        await callback.answer()
        return

    action = callback.data.replace("credits_", "")
    await state.update_data(action=action)

    action_labels = {
        "add": "asignar créditos",
        "withdraw": "retirar créditos",
        "query": "consultar saldo",
    }

    if action == "query":
        prompt = (
            "🔍 <b>Consultar saldo</b>\n\n"
            "Envíame el ID de Telegram del usuario."
        )
    else:
        prompt = (
            f"{'➕' if action == 'add' else '➖'} <b>{action_labels[action].capitalize()}</b>\n\n"
            "Envíame el ID de Telegram del usuario afectado."
        )

    await state.set_state(AdminCredits.beneficiary)
    await callback.message.edit_text(
        prompt,
        parse_mode=ParseMode.HTML,
        reply_markup=credits_subpanel_menu(),
    )
    await callback.answer()


@router.message(AdminCredits.beneficiary)
async def credits_receive_beneficiary(message: Message, state: FSMContext, bot: Bot):
    """Recibe el ID del beneficiario."""
    if not await is_admin(message.from_user.id):
        return

    text = message.text.strip()
    if not text.isdigit():
        await message.answer(
            "❌ ID inválido. Envíame solo el número de ID de Telegram.",
            parse_mode=ParseMode.HTML,
            reply_markup=credits_subpanel_menu(),
        )
        return

    beneficiary_id = int(text)
    data = await state.get_data()
    action = data.get("action")

    # Regla: ningún admin puede asignarse o retirarse créditos a sí mismo.
    # Incluye super admins: el saldo propio no se toca desde el panel.
    # Se niega con mensaje (es un admin autorizado, no un intruso) y se
    # registra el intento en el canal de logs.
    if action in ("add", "withdraw") and beneficiary_id == message.from_user.id:
        await log_operation_failed(
            bot,
            message.from_user.id,
            f"autoasignación de créditos ({action})",
            "el admin intentó operar sobre su propio saldo",
        )
        await state.clear()
        await message.answer(
            "⛔ <b>No puedes asignarte créditos a ti mismo.</b>\n\n"
            "Pide a otro administrador que lo haga.",
            parse_mode=ParseMode.HTML,
            reply_markup=credits_subpanel_menu(),
        )
        return

    if action == "query":
        balance = await get_balance(beneficiary_id)
        transactions = await get_transactions(beneficiary_id, limit=5)

        tx_text = ""
        if transactions:
            tx_text = "\n\n<b>Últimos movimientos:</b>\n"
            for tx in transactions:
                emoji = "🟢" if tx["amount"] > 0 else "🔴"
                tx_text += (
                    f"{emoji} {tx['amount']:+d} créditos - "
                    f"{tx['reason']} (<i>{tx['timestamp'][:10]}</i>)\n"
                )
        else:
            tx_text = "\n\n<i>Sin movimientos registrados.</i>"

        await message.answer(
            f"💳 <b>Saldo del usuario</b> <code>{beneficiary_id}</code>\n\n"
            f"Balance actual: <b>{balance}</b> créditos"
            f"{tx_text}",
            parse_mode=ParseMode.HTML,
            reply_markup=credits_subpanel_menu(),
        )
        await state.clear()
        return

    await state.update_data(beneficiary_id=beneficiary_id)
    await state.set_state(AdminCredits.amount)
    await message.answer(
        f"{'➕' if action == 'add' else '➖'} <b>Indica la cantidad</b>\n\n"
        "Envíame la cantidad de créditos.",
        parse_mode=ParseMode.HTML,
        reply_markup=credits_subpanel_menu(),
    )


@router.message(AdminCredits.amount)
async def credits_receive_amount(message: Message, state: FSMContext, bot: Bot):
    """Recibe la cantidad y pide confirmación."""
    if not await is_admin(message.from_user.id):
        return

    text = message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await message.answer(
            "❌ Cantidad inválida. Envíame un número entero mayor a 0.",
            parse_mode=ParseMode.HTML,
            reply_markup=credits_subpanel_menu(),
        )
        return

    amount = int(text)
    data = await state.get_data()
    action = data.get("action")
    beneficiary_id = data.get("beneficiary_id")

    current_balance = await get_balance(beneficiary_id)

    await state.update_data(amount=amount, current_balance=current_balance)
    await state.set_state(AdminCredits.confirm)

    action_preview = {
        "add": ("Asignar", "a", "success"),
        "withdraw": ("Retirar", "de", "danger"),
    }
    verb, preposition, _ = action_preview[action]
    after_preview = current_balance + (amount if action == "add" else -amount)

    await message.answer(
        f"{'➕' if action == 'add' else '➖'} <b>Confirmar operación</b>\n\n"
        f"• Acción: <b>{verb}</b>\n"
        f"• Usuario: <code>{beneficiary_id}</code>\n"
        f"• Cantidad: <b>{amount}</b> créditos\n"
        f"• Saldo actual: <b>{current_balance}</b> créditos\n"
        + (
            f"• Saldo después: <b>{after_preview}</b> créditos\n\n"
            if after_preview >= 0
            else "• ⚠️ El retiro supera el saldo actual: la operación será rechazada.\n\n"
        )
        + f"¿Confirmas que quieres {verb.lower()} {amount} créditos {preposition} este usuario?",
        parse_mode=ParseMode.HTML,
        reply_markup=credits_confirm_menu(),
    )


@router.callback_query(AdminCredits.confirm, F.data == "credits_confirm")
async def credits_confirm_action(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Confirma y ejecuta la operación de créditos."""
    if not await is_admin(callback.from_user.id):
        await callback.answer()
        return

    data = await state.get_data()
    action = data.get("action")
    beneficiary_id = data.get("beneficiary_id")
    amount = data.get("amount")
    admin_id = callback.from_user.id

    await state.clear()

    if action not in ("add", "withdraw") or not isinstance(
        beneficiary_id, int
    ) or not isinstance(amount, int) or amount <= 0:
        await callback.message.edit_text(
            "❌ Sesión inválida. Abre de nuevo el panel con /batman.",
            parse_mode=ParseMode.HTML,
            reply_markup=credits_subpanel_menu(),
        )
        await callback.answer()
        return

    try:
        if action == "add":
            new_balance = await add_credits(
                beneficiary_id,
                amount,
                reason="Asignación desde panel de admin",
                admin_id=admin_id,
            )
            verb = "asignados"
            emoji = "➕"
        else:
            success, new_balance = await deduct_credits(
                beneficiary_id,
                amount,
                reason="Retiro desde panel de admin",
                admin_id=admin_id,
            )
            if not success:
                await log_operation_failed(
                    bot,
                    admin_id,
                    "retirar créditos",
                    f"saldo insuficiente (saldo: {new_balance}, requerido: {amount})",
                )
                await callback.message.edit_text(
                    "❌ <b>Saldo insuficiente</b>\n\n"
                    f"El usuario <code>{beneficiary_id}</code> no tiene créditos suficientes "
                    f"para retirar <b>{amount}</b>.\n"
                    f"Saldo actual: <b>{new_balance}</b> créditos.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=credits_subpanel_menu(),
                )
                await callback.answer()
                return
            verb = "retirados"
            emoji = "➖"
    except ValueError as e:
        await log_operation_failed(bot, admin_id, "gestionar créditos", str(e))
        await callback.message.edit_text(
            f"❌ <b>Error:</b> {e}",
            parse_mode=ParseMode.HTML,
            reply_markup=credits_subpanel_menu(),
        )
        await callback.answer()
        return

    # Alerta silenciosa al canal de logs
    await log_credits_change(
        bot,
        admin_id,
        beneficiary_id,
        verb,
        amount,
        new_balance,
    )

    await callback.message.edit_text(
        f"{emoji} <b>Operación completada</b>\n\n"
        f"• Usuario: <code>{beneficiary_id}</code>\n"
        f"• Créditos {verb}: <b>{amount}</b>\n"
        f"• Nuevo saldo: <b>{new_balance}</b> créditos",
        parse_mode=ParseMode.HTML,
        reply_markup=credits_subpanel_menu(),
    )
    await callback.answer()


@router.callback_query(AdminCredits.confirm, F.data == "credits_cancel")
async def credits_cancel_confirm(callback: CallbackQuery, state: FSMContext):
    """Cancela la operación de créditos desde la confirmación."""
    if not await is_admin(callback.from_user.id):
        await callback.answer()
        return

    await state.clear()
    await callback.message.edit_text(
        "❌ <b>Operación cancelada.</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=credits_subpanel_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == "credits_cancel")
async def credits_cancel_flow(callback: CallbackQuery, state: FSMContext):
    """Cancela el flujo de créditos desde cualquier paso."""
    if not await is_admin(callback.from_user.id):
        await callback.answer()
        return

    await state.clear()
    await callback.message.edit_text(
        "❌ <b>Operación cancelada.</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=credits_subpanel_menu(),
    )
    await callback.answer()


# ─────────────────────────────────────────────────────────────
# Panel SMS virtual (solo super admins)
# ─────────────────────────────────────────────────────────────

def _sms_home_text(
    stats: dict, balance: str, enabled: bool, usd_mxn: str
) -> str:
    status = "🟢 Activo" if enabled else "🔴 Inactivo"
    return (
        "📱 <b>SMS virtual — Panel de control</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"🔛 Estado: <b>{status}</b>\n"
        f"💰 Saldo HeroSMS: <b>${balance}</b>\n"
        f"💱 Tipo de cambio: <code>1 USD = ${usd_mxn} MXN</code>\n"
        "━━━━━━━━━━━━━━\n"
        f"📊 Pedidos totales: <code>{stats['total']}</code>\n"
        f"  ✅ Exitosos: <code>{stats['success']}</code>\n"
        f"  ❌ Fallidos: <code>{stats['failed']}</code>\n"
        f"  🚫 Cancelados: <code>{stats['cancelled']}</code>\n"
        f"  ⏳ Pendientes: <code>{stats['pending']}</code>"
    )


def _sms_tiers_text(tiers: dict, usd_mxn: str) -> str:
    try:
        rate = float(usd_mxn)
    except Exception:
        rate = 18.5
    pisos = sorted(tiers)
    if not pisos:
        body = "  (sin tramos configurados)"
    else:
        lines = []
        for i, piso in enumerate(pisos):
            techo = pisos[i + 1] - 1 if i + 1 < len(pisos) else "∞"
            usd_lo = piso / rate if rate else 0
            lines.append(
                f"  <code>${piso}–${techo}</code> MXN "
                f"(<code>${usd_lo:.2f} USD+</code>) → <b>{tiers[piso]} cr</b>"
            )
        body = "\n".join(lines)
    return (
        "📊 <b>Tramos de precios SMS</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"{body}\n"
        "━━━━━━━━━━━━━━\n"
        "<i>El precio en MXN sale de USD × tipo de cambio.\n"
        "Se usa el tramo más alto cuyo piso no supere el precio.</i>"
    )


def _sms_config_text(enabled: bool, usd_mxn: str) -> str:
    return (
        "⚙️ <b>Configuración SMS</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"🔛 Módulo: {'✅ Activo' if enabled else '❌ Inactivo'}\n"
        f"💱 Tipo de cambio: <code>1 USD = ${usd_mxn} MXN</code>\n"
        "━━━━━━━━━━━━━━\n"
        "<i>Los precios en créditos se configuran en Tramos de precios.</i>"
    )


@router.callback_query(F.data == "admin_sms")
async def cb_admin_sms(callback: CallbackQuery, state: FSMContext):
    """Panel SMS virtual: solo super admins."""
    if not is_super_admin(callback.from_user.id):
        await callback.answer()
        return
    await state.clear()
    stats = await sms_svc.get_stats()
    balance = await sms_svc.api_balance()
    enabled = await sms_svc.is_enabled()
    usd_mxn = await sms_svc.get_usd_to_mxn()
    await callback.message.edit_text(
        _sms_home_text(stats, balance, enabled, usd_mxn),
        parse_mode=ParseMode.HTML,
        reply_markup=sms_admin_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == "smsadm_stats")
async def smsadm_stats(callback: CallbackQuery, state: FSMContext):
    if not is_super_admin(callback.from_user.id):
        await callback.answer()
        return
    stats = await sms_svc.get_stats()
    balance = await sms_svc.api_balance()
    enabled = await sms_svc.is_enabled()
    usd_mxn = await sms_svc.get_usd_to_mxn()
    await callback.message.edit_text(
        _sms_home_text(stats, balance, enabled, usd_mxn),
        parse_mode=ParseMode.HTML,
        reply_markup=sms_admin_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == "smsadm_toggle")
async def smsadm_toggle(callback: CallbackQuery, state: FSMContext):
    if not is_super_admin(callback.from_user.id):
        await callback.answer()
        return
    enabled = await sms_svc.set_enabled(not await sms_svc.is_enabled())
    stats = await sms_svc.get_stats()
    balance = await sms_svc.api_balance()
    usd_mxn = await sms_svc.get_usd_to_mxn()
    await callback.message.edit_text(
        _sms_home_text(stats, balance, enabled, usd_mxn),
        parse_mode=ParseMode.HTML,
        reply_markup=sms_admin_menu(),
    )
    await callback.answer(f"Módulo {'activado' if enabled else 'desactivado'}")


@router.callback_query(F.data == "smsadm_orders")
async def smsadm_orders(callback: CallbackQuery, state: FSMContext):
    if not is_super_admin(callback.from_user.id):
        await callback.answer()
        return
    orders = await sms_svc.get_recent_orders(limit=10)
    if not orders:
        text = (
            "📋 <b>Últimos pedidos SMS</b>\n━━━━━━━━━━━━━━\n(sin pedidos aún)"
        )
    else:
        icons = {"success": "✅", "failed": "❌", "cancelled": "🚫", "pending": "⏳"}
        lines = ["📋 <b>Últimos pedidos SMS</b>", "━━━━━━━━━━━━━━"]
        for o in orders:
            icon = icons.get(o.get("status", ""), "❓")
            lines.append(
                f"{icon} <code>{o.get('tg_id', '?')}</code> "
                f"@{o.get('username', '') or 'N/A'}\n"
                f"   {o.get('service_name', '?')} · {o.get('country_name', '?')} "
                f" 💳 {o.get('credits_charged', 0)} cr "
                f" 📅 {(o.get('requested_at') or '')[:16].replace('T', ' ')}\n"
                f"   🎫 <code>{o.get('order_id', '')}</code>"
            )
        text = "\n".join(lines)
    await callback.message.edit_text(
        text, parse_mode=ParseMode.HTML, reply_markup=sms_admin_subpanel_menu()
    )
    await callback.answer()


@router.callback_query(F.data == "smsadm_config")
async def smsadm_config(callback: CallbackQuery, state: FSMContext):
    if not is_super_admin(callback.from_user.id):
        await callback.answer()
        return
    enabled = await sms_svc.is_enabled()
    usd_mxn = await sms_svc.get_usd_to_mxn()
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💱 Cambiar tipo de cambio",
                    callback_data="smsadm_set_rate",
                    style="primary",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔛 Activar/Desactivar",
                    callback_data="smsadm_toggle",
                    style="primary",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔄 Ver precios API",
                    callback_data="smsadm_prices",
                    style="primary",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Volver a SMS", callback_data="admin_sms", style="primary"
                )
            ],
        ]
    )
    await callback.message.edit_text(
        _sms_config_text(enabled, usd_mxn),
        parse_mode=ParseMode.HTML,
        reply_markup=markup,
    )
    await callback.answer()


@router.callback_query(F.data == "smsadm_set_rate")
async def smsadm_set_rate(callback: CallbackQuery, state: FSMContext):
    if not is_super_admin(callback.from_user.id):
        await callback.answer()
        return
    usd_mxn = await sms_svc.get_usd_to_mxn()
    await state.set_state(AdminSms.value)
    await state.update_data(sms_field="usd_to_mxn")
    await callback.message.edit_text(
        "💱 <b>Cambiar tipo de cambio USD → MXN</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"Valor actual: <code>1 USD = ${usd_mxn} MXN</code>\n\n"
        "Envíame el nuevo tipo de cambio.\n"
        "<i>Ejemplo: <code>18.5</code></i>",
        parse_mode=ParseMode.HTML,
        reply_markup=sms_admin_subpanel_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == "smsadm_prices")
async def smsadm_prices(callback: CallbackQuery, state: FSMContext):
    if not is_super_admin(callback.from_user.id):
        await callback.answer()
        return
    await callback.answer("⏳ Consultando precios...")
    try:
        services = await sms_svc.fetch_services()
        wa = next(
            (s for s in services if "whatsapp" in (s.get("name") or "").lower()),
            services[0] if services else None,
        )
        rows = await sms_svc.fetch_countries(wa["ID"]) if wa else []
    except Exception as exc:
        logger.warning("smsadm_prices: %s", exc)
        rows = []
    usd_mxn = await sms_svc.get_usd_to_mxn()
    try:
        rate = float(usd_mxn)
    except Exception:
        rate = 18.5
    lines = ["🔄 <b>Precios API (WhatsApp)</b>", "━━━━━━━━━━━━━━"]
    for r in rows[:10]:
        try:
            usd = float(r.get("price", 0))
            mxn = usd * rate
            cr = await sms_svc.calc_credits(str(r.get("price", "0")))
            lines.append(
                f"  • {r.get('name', '?')}: <code>${usd:.2f} USD</code> / "
                f"<code>${mxn:.0f} MXN</code> → <b>{cr} cr</b>"
            )
        except Exception:
            continue
    if len(lines) == 2:
        lines.append("  (sin datos)")
    await callback.message.edit_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=sms_admin_subpanel_menu(),
    )


@router.callback_query(F.data == "smsadm_tiers")
async def smsadm_tiers(callback: CallbackQuery, state: FSMContext):
    if not is_super_admin(callback.from_user.id):
        await callback.answer()
        return
    tiers = await sms_svc.get_credit_tiers()
    usd_mxn = await sms_svc.get_usd_to_mxn()
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    rows = []
    for piso in sorted(tiers):
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"✏️ ${piso} MXN+ → {tiers[piso]} cr",
                    callback_data=f"smsadm_tier_edit:{piso}",
                    style="primary",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="➕ Agregar tramo", callback_data="smsadm_tier_add", style="success"
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ Volver a SMS", callback_data="admin_sms", style="primary"
            )
        ]
    )
    await callback.message.edit_text(
        _sms_tiers_text(tiers, usd_mxn),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data == "smsadm_tier_add")
async def smsadm_tier_add(callback: CallbackQuery, state: FSMContext):
    if not is_super_admin(callback.from_user.id):
        await callback.answer()
        return
    await state.set_state(AdminSms.value)
    await state.update_data(sms_field="tier_add")
    await callback.message.edit_text(
        "➕ <b>Agregar tramo de precio</b>\n"
        "━━━━━━━━━━━━━━\n"
        "Envíame el piso en MXN y los créditos.\n\n"
        "<i>Formato: <code>piso:créditos</code></i>\n"
        "<i>Ejemplo: <code>25:55</code> → desde $25 MXN = 55 créditos</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=sms_admin_subpanel_menu(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("smsadm_tier_edit:"))
async def smsadm_tier_edit(callback: CallbackQuery, state: FSMContext):
    if not is_super_admin(callback.from_user.id):
        await callback.answer()
        return
    try:
        piso = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Tramo inválido")
        return
    tiers = await sms_svc.get_credit_tiers()
    current = tiers.get(piso, 0)
    await state.set_state(AdminSms.value)
    await state.update_data(sms_field="tier_edit", sms_piso=piso)
    await callback.message.edit_text(
        "✏️ <b>Editar tramo</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"💲 Desde <code>${piso} MXN</code>\n"
        f"Créditos actuales: <code>{current} cr</code>\n\n"
        "Envíame el nuevo número de créditos.\n"
        "<i>Envíame <code>0</code> para eliminar el tramo.</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=sms_admin_subpanel_menu(),
    )
    await callback.answer()


@router.message(AdminSms.value)
async def sms_receive_value(message: Message, state: FSMContext):
    """Recibe tasa de cambio o créditos de tramo según el campo activo."""
    if not is_super_admin(message.from_user.id):
        return
    data = await state.get_data()
    field = data.get("sms_field")
    text = (message.text or "").strip()

    if field == "usd_to_mxn":
        try:
            rate = float(text)
            assert 0 < rate <= 1000
        except Exception:
            await message.answer(
                "❌ Valor inválido. Ejemplo: <code>18.5</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=sms_admin_subpanel_menu(),
            )
            return
        saved = await sms_svc.set_usd_to_mxn(rate)
        await state.clear()
        await message.answer(
            f"✅ Tipo de cambio actualizado: <code>1 USD = ${saved} MXN</code>.",
            parse_mode=ParseMode.HTML,
            reply_markup=sms_admin_subpanel_menu(),
        )
        return

    if field == "tier_add":
        parts = text.split(":")
        if len(parts) != 2:
            await message.answer(
                "❌ Formato incorrecto. Usa <code>piso:créditos</code>\n"
                "<i>Ejemplo: <code>25:55</code></i>",
                parse_mode=ParseMode.HTML,
                reply_markup=sms_admin_subpanel_menu(),
            )
            return
        try:
            piso = int(parts[0].strip())
            cr = int(parts[1].strip())
            assert piso >= 0 and 1 <= cr <= 10000
        except Exception:
            await message.answer(
                "❌ Valores inválidos. Ejemplo: <code>25:55</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=sms_admin_subpanel_menu(),
            )
            return
        await sms_svc.set_credit_tier(piso, cr)
        await state.clear()
        await message.answer(
            f"✅ Tramo guardado: desde <code>${piso} MXN</code> → "
            f"<code>{cr} cr</code>.",
            parse_mode=ParseMode.HTML,
            reply_markup=sms_admin_subpanel_menu(),
        )
        return

    if field == "tier_edit":
        piso = data.get("sms_piso")
        try:
            cr = int(text)
            assert 0 <= cr <= 10000
        except Exception:
            await message.answer(
                "❌ Valor inválido. Envía un entero (0 para eliminar).",
                parse_mode=ParseMode.HTML,
                reply_markup=sms_admin_subpanel_menu(),
            )
            return
        if cr == 0:
            await sms_svc.delete_credit_tier(piso)
            await state.clear()
            await message.answer(
                f"🗑 Tramo <code>${piso} MXN+</code> eliminado.",
                parse_mode=ParseMode.HTML,
                reply_markup=sms_admin_subpanel_menu(),
            )
        else:
            await sms_svc.set_credit_tier(piso, cr)
            await state.clear()
            await message.answer(
                f"✅ Tramo <code>${piso} MXN+</code> → <code>{cr} cr</code> "
                "actualizado.",
                parse_mode=ParseMode.HTML,
                reply_markup=sms_admin_subpanel_menu(),
            )
        return

    await state.clear()
    await message.answer(
        "❌ Sesión inválida. Abre de nuevo el panel con /batman.",
        parse_mode=ParseMode.HTML,
        reply_markup=sms_admin_subpanel_menu(),
    )


@router.callback_query(F.data == "smsadm_cancel")
async def smsadm_cancel(callback: CallbackQuery, state: FSMContext):
    if not is_super_admin(callback.from_user.id):
        await callback.answer()
        return
    await state.clear()
    await callback.message.edit_text(
        "❌ <b>Operación cancelada.</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=sms_admin_subpanel_menu(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("smsadm_"))
async def smsadm_fallback(callback: CallbackQuery, state: FSMContext):
    """Red de seguridad: botones huérfanos no congelan el panel."""
    if not is_super_admin(callback.from_user.id):
        await callback.answer()
        return
    await state.clear()
    await callback.answer(
        "La sesión del panel expiró. Abre de nuevo con /batman.",
        show_alert=True,
    )


def register_admin_handlers(dp):
    dp.include_router(router)
