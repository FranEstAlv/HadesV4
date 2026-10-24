"""Envío de alertas y logs al canal de logs del bot."""
import html
import logging
from datetime import datetime, timezone
from typing import Optional

from aiogram import Bot
from aiogram.enums import ParseMode

from bot.config import LOGS_CHANNEL_ID

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe(value: Optional[str], default: str = "N/A") -> str:
    if value is None:
        return default
    text = str(value).replace(chr(0), "").strip()
    return html.escape(text) if text else default


async def send_log(bot: Bot, text: str, parse_mode: ParseMode = ParseMode.HTML) -> None:
    """Envía un mensaje al canal de logs sin notificación ni sonido.

    Los errores de envío se registran por logging pero no se propagan
    para no interrumpir el flujo principal.
    """
    if not LOGS_CHANNEL_ID:
        return

    try:
        await bot.send_message(
            LOGS_CHANNEL_ID,
            text,
            parse_mode=parse_mode,
            disable_notification=True,
            disable_web_page_preview=True,
        )
    except Exception as exc:
        logger.warning("No se pudo enviar log al canal %s: %s", LOGS_CHANNEL_ID, exc)


async def log_new_user(bot: Bot, user_id: int, username: Optional[str], full_name: str) -> None:
    """Alerta: nuevo usuario que inició el bot."""
    await send_log(
        bot,
        "🆕 <b>NUEVO USUARIO</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"👤 <b>Nombre:</b> <code>{_safe(full_name)}</code>\n"
        f"🔱 <b>Username:</b> <code>{_safe('@' + username if username else 'sin_username')}</code>\n"
        f"🆔 <b>ID:</b> <code>{user_id}</code>\n"
        f"🕒 <b>Fecha UTC:</b> <code>{_now_iso()}</code>",
    )


async def log_membership_assigned(
    bot: Bot,
    admin_id: int,
    beneficiary_id: int,
    plan_label: str,
    end_date: str,
) -> None:
    """Alerta: membresía asignada por un admin."""
    await send_log(
        bot,
        "📅 <b>MEMBRESÍA ASIGNADA</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"👑 <b>Admin:</b> <code>{admin_id}</code>\n"
        f"🎯 <b>Beneficiario:</b> <code>{beneficiary_id}</code>\n"
        f"🏷️ <b>Plan:</b> <code>{_safe(plan_label)}</code>\n"
        f"📅 <b>Vencimiento:</b> <code>{_safe(end_date)}</code>\n"
        f"🕒 <b>Fecha UTC:</b> <code>{_now_iso()}</code>",
    )


async def log_membership_removed(bot: Bot, admin_id: int, beneficiary_id: int) -> None:
    """Alerta: membresía retirada por un admin."""
    await send_log(
        bot,
        "➖ <b>MEMBRESÍA REMOVIDA</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"👑 <b>Admin:</b> <code>{admin_id}</code>\n"
        f"🎯 <b>Usuario:</b> <code>{beneficiary_id}</code>\n"
        f"🕒 <b>Fecha UTC:</b> <code>{_now_iso()}</code>",
    )


async def log_galletas_assigned(
    bot: Bot,
    admin_id: int,
    beneficiary_id: int,
    tipo_label: str,
    amount: int,
) -> None:
    """Alerta: paquete de galletas asignado por un admin."""
    await send_log(
        bot,
        "🍪 <b>GALLETAS ASIGNADAS</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"👑 <b>Admin:</b> <code>{admin_id}</code>\n"
        f"🎯 <b>Beneficiario:</b> <code>{beneficiary_id}</code>\n"
        f"🍪 <b>Paquete:</b> <code>{_safe(tipo_label)}</code>\n"
        f"🔢 <b>Galletas:</b> <code>{amount}</code>\n"
        f"🕒 <b>Fecha UTC:</b> <code>{_now_iso()}</code>",
    )


async def log_matanga(
    bot: Bot,
    admin_id: int,
    beneficiary_id: int,
    target: str,
    detail: str,
) -> None:
    """Alerta: matanga aplicada por un super admin (quitó galletas o créditos)."""
    await send_log(
        bot,
        "💀 <b>MATANGA</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"👑 <b>Super admin:</b> <code>{admin_id}</code>\n"
        f"🎯 <b>Usuario:</b> <code>{beneficiary_id}</code>\n"
        f"🔪 <b>Quitado:</b> <code>{_safe(target)}</code>\n"
        f"📋 <b>Detalle:</b> <code>{_safe(detail)}</code>\n"
        f"🕒 <b>Fecha UTC:</b> <code>{_now_iso()}</code>",
    )


async def log_credits_change(
    bot: Bot,
    admin_id: int,
    beneficiary_id: int,
    action: str,
    amount: int,
    new_balance: int,
) -> None:
    """Alerta: créditos asignados o retirados por un admin."""
    emoji = "➕" if action == "asignados" else "➖"
    await send_log(
        bot,
        f"{emoji} <b>CRÉDITOS {action.upper()}</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"👑 <b>Admin:</b> <code>{admin_id}</code>\n"
        f"🎯 <b>Usuario:</b> <code>{beneficiary_id}</code>\n"
        f"💳 <b>Cantidad:</b> <code>{amount}</code>\n"
        f"💰 <b>Nuevo saldo:</b> <code>{new_balance}</code>\n"
        f"🕒 <b>Fecha UTC:</b> <code>{_now_iso()}</code>",
    )


async def log_unknown_command(bot: Bot, user_id: int, command: str, chat_type: str) -> None:
    """Alerta: comando desconocido usado por un usuario."""
    await send_log(
        bot,
        "❓ <b>COMANDO DESCONOCIDO</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"👤 <b>Usuario:</b> <code>{user_id}</code>\n"
        f"⌨️ <b>Comando:</b> <code>{_safe(command)}</code>\n"
        f"💬 <b>Chat:</b> <code>{_safe(chat_type)}</code>\n"
        f"🕒 <b>Fecha UTC:</b> <code>{_now_iso()}</code>",
    )


async def log_error(bot: Bot, context: str, error: str) -> None:
    """Alerta: error o fallo en el bot."""
    await send_log(
        bot,
        "⚠️ <b>ERROR / FALLO</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"📌 <b>Contexto:</b> <code>{_safe(context)}</code>\n"
        f"🐛 <b>Error:</b> <code>{_safe(error[:500])}</code>\n"
        f"🕒 <b>Fecha UTC:</b> <code>{_now_iso()}</code>",
    )


async def log_admin_access(bot: Bot, admin_id: int, chat_type: str) -> None:
    """Alerta: admin accede al panel /batman."""
    await send_log(
        bot,
        "🛡️ <b>ACCESO A /batman</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"👑 <b>Admin:</b> <code>{admin_id}</code>\n"
        f"💬 <b>Chat:</b> <code>{_safe(chat_type)}</code>\n"
        f"🕒 <b>Fecha UTC:</b> <code>{_now_iso()}</code>",
    )


async def log_admin_added(bot: Bot, super_admin_id: int, new_admin_id: int) -> None:
    """Alerta: super admin asigna un admin normal."""
    await send_log(
        bot,
        "➕ <b>ADMIN AGREGADO</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"👑 <b>Super admin:</b> <code>{super_admin_id}</code>\n"
        f"🎯 <b>Nuevo admin:</b> <code>{new_admin_id}</code>\n"
        f"🕒 <b>Fecha UTC:</b> <code>{_now_iso()}</code>",
    )


async def log_admin_removed(bot: Bot, super_admin_id: int, removed_admin_id: int) -> None:
    """Alerta: super admin retira un admin normal."""
    await send_log(
        bot,
        "➖ <b>ADMIN RETIRADO</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"👑 <b>Super admin:</b> <code>{super_admin_id}</code>\n"
        f"🎯 <b>Admin retirado:</b> <code>{removed_admin_id}</code>\n"
        f"🕒 <b>Fecha UTC:</b> <code>{_now_iso()}</code>",
    )


async def log_admin_access_denied(bot: Bot, user_id: int, chat_type: str) -> None:
    """Alerta de seguridad: usuario no admin intenta usar /batman."""
    await send_log(
        bot,
        "🚫 <b>INTENTO DE ACCESO A /batman</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"👤 <b>Usuario:</b> <code>{user_id}</code>\n"
        f"💬 <b>Chat:</b> <code>{_safe(chat_type)}</code>\n"
        f"🕒 <b>Fecha UTC:</b> <code>{_now_iso()}</code>",
    )


async def log_message_broadcast(
    bot: Bot,
    admin_id: int,
    target: str,
    sent: int,
    failed: int,
    pinned: int = 0,
) -> None:
    """Alerta: envío de mensajes masivos o a grupos desde el panel."""
    target_label = {
        "all": "todos los usuarios",
        "admins": "todos los admins",
        "user": "un usuario específico",
        "eternal_tartaro": "Eternal Tartaro",
        "olimpo_bins": "OLIMPO BINS",
    }.get(target, target)
    pin_text = f"\n📌 <b>Fijados:</b> <code>{pinned}</code>" if pinned else ""
    await send_log(
        bot,
        "📨 <b>BROADCAST ENVIADO</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"👑 <b>Admin:</b> <code>{admin_id}</code>\n"
        f"🎯 <b>Destino:</b> <code>{_safe(target_label)}</code>\n"
        f"📤 <b>Enviados:</b> <code>{sent}</code>\n"
        f"📥 <b>Fallidos:</b> <code>{failed}</code>"
        f"{pin_text}\n"
        f"🕒 <b>Fecha UTC:</b> <code>{_now_iso()}</code>",
    )


async def log_operation_failed(
    bot: Bot,
    admin_id: int,
    operation: str,
    reason: str,
) -> None:
    """Alerta: fallo en una operación administrativa."""
    await send_log(
        bot,
        "❌ <b>OPERACIÓN FALLIDA</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"👑 <b>Admin:</b> <code>{admin_id}</code>\n"
        f"⚙️ <b>Operación:</b> <code>{_safe(operation)}</code>\n"
        f"📌 <b>Motivo:</b> <code>{_safe(reason[:250])}</code>\n"
        f"🕒 <b>Fecha UTC:</b> <code>{_now_iso()}</code>",
    )
