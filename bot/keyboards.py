from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

# Botones inline con estilos de color usando el parámetro `style` de aiogram 3.
# Telegram soporta:
#   - "primary" → azul
#   - "success" → verde
#   - "danger"  → rojo

# Custom emoji premium para iconos de botones (requiere Premium en la cuenta dueña del bot)
MENU_ICON_EMOJI = "5021905410089550576"

BUTTONS = {
    # Opciones principales del menú de inicio (icono custom emoji se muestra solo via icon_custom_emoji_id)
    "perfil": ("Perfil", "menu_perfil", "primary"),
    "gates": ("Gates", "menu_gates", "primary"),
    "precios": ("Precios", "menu_precios", "primary"),
    "contacto": ("Contacto", "menu_contacto", "primary"),
    "herramientas": ("Herramientas", "menu_tools", "primary"),

    # Navegación
    "close": ("Cerrar menú", "menu_close", "danger"),
    "back": ("Volver", "menu_back", "primary"),
    "confirm": ("Confirmar", "menu_confirm", "success"),
    "cancel": ("Cancelar", "menu_cancel", "danger"),
}


def _build_button(key: str, icon_emoji: str | None = None) -> InlineKeyboardButton:
    text, callback, style = BUTTONS[key]
    return InlineKeyboardButton(
        text=text,
        callback_data=callback,
        style=style,
        icon_custom_emoji_id=icon_emoji,
    )


def main_menu(is_admin: bool = False) -> InlineKeyboardMarkup:
    """Menú principal al estilo de la captura: PERFIL, GATES, PRECIOS, CONTACTO."""
    builder = InlineKeyboardBuilder()

    # Opciones principales en 2 columnas, con icono custom emoji premium
    builder.row(
        _build_button("perfil", icon_emoji=MENU_ICON_EMOJI),
        _build_button("gates", icon_emoji=MENU_ICON_EMOJI),
    )
    builder.row(
        _build_button("precios", icon_emoji=MENU_ICON_EMOJI),
        _build_button("contacto", icon_emoji=MENU_ICON_EMOJI),
    )
    builder.row(_build_button("herramientas", icon_emoji=MENU_ICON_EMOJI)),

    # Botón cerrar al final, ancho completo, rojo
    builder.row(_build_button("close"))

    return builder.as_markup()


def back_button() -> InlineKeyboardMarkup:
    """Teclado simple con botón volver (azul) y cerrar (rojo)."""
    builder = InlineKeyboardBuilder()
    builder.row(_build_button("back"), _build_button("close"))
    return builder.as_markup()


def prices_menu() -> InlineKeyboardMarkup:
    """Menú de precios con botón para volver y cerrar."""
    builder = InlineKeyboardBuilder()
    builder.row(_build_button("back"), _build_button("close"))
    return builder.as_markup()


# ─────────────────────────────────────────────────────────────
# Gates por categoría: Auth / Charged / CCN Charged
# Namespace propio "menu_gate_*" para no empalmar con el handler
# "gate_*" que ya existe en bot/handlers/gates.py.
# ─────────────────────────────────────────────────────────────

GATE_BUTTONS = {
    "ghost": ("👻 Ghost", "menu_gate_ghost", "primary"),
    "medusa": ("🐍 Medusa", "menu_gate_medusa", "primary"),
    "philotes": ("💎 Philotes", "menu_gate_philotes", "primary"),
    "eurinias": ("⚡ Eurinias", "menu_gate_eurinias", "primary"),
    "wojtek": ("🔥 Wojtek", "menu_gate_wojtek", "primary"),
    "euridice": ("🔱 Euridice", "menu_gate_euridice", "primary"),
    "back_gates": ("⬅️ Volver a Gates", "menu_gates", "primary"),
}

GATE_CATEGORIES = {
    "auth": ("Auth", "menu_gates_auth", "primary"),
    "charged": ("Charged", "menu_gates_charged", "primary"),
    "ccn": ("CCN Charged", "menu_gates_ccn", "primary"),
}

# Custom emoji para los 3 botones de categoría (Auth / Charged / CCN Charged)
GATE_CATEGORY_ICON_EMOJI = "4970142833605345805"


def _build_gate_button(key: str) -> InlineKeyboardButton:
    text, callback, style = GATE_BUTTONS[key]
    return InlineKeyboardButton(text=text, callback_data=callback, style=style)


def _build_gate_category(key: str) -> InlineKeyboardButton:
    text, callback, style = GATE_CATEGORIES[key]
    return InlineKeyboardButton(
        text=text,
        callback_data=callback,
        style=style,
        icon_custom_emoji_id=GATE_CATEGORY_ICON_EMOJI,
    )


def gates_menu() -> InlineKeyboardMarkup:
    """Menú de gates: 3 botones por categoría (Auth / Charged / CCN Charged)."""
    builder = InlineKeyboardBuilder()
    builder.row(_build_gate_category("auth"))
    builder.row(_build_gate_category("charged"))
    builder.row(_build_gate_category("ccn"))
    # Navegación
    builder.row(_build_button("back"), _build_button("close"))
    return builder.as_markup()


def gates_category_menu(category: str) -> InlineKeyboardMarkup:
    """Lista de gates dentro de cada categoría (solo texto + navegación)."""
    builder = InlineKeyboardBuilder()
    # Solo navegación: volver a las 3 categorías + cerrar
    builder.row(_build_gate_button("back_gates"))
    builder.row(_build_button("close"))
    return builder.as_markup()


def gate_detail_menu() -> InlineKeyboardMarkup:
    """Menú dentro del panel de cada gate: volver a Gates y cerrar."""
    builder = InlineKeyboardBuilder()
    builder.row(_build_gate_button("back_gates"))
    builder.row(_build_button("close"))
    return builder.as_markup()


def confirm_cancel_menu(confirm_callback: str = "menu_confirm", cancel_callback: str = "menu_cancel") -> InlineKeyboardMarkup:
    """Menú de confirmación: verde para confirmar, rojo para cancelar."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Confirmar", callback_data=confirm_callback, style="success"),
        InlineKeyboardButton(text="❌ Cancelar", callback_data=cancel_callback, style="danger"),
    )
    return builder.as_markup()


# ─────────────────────────────────────────────────────────────
# Herramientas: Gen / Extra / BIN
# Namespace propio "menu_tool_*" para no empalmar con otros paneles.
# ─────────────────────────────────────────────────────────────

TOOL_BUTTONS = {
    "gen": ("🛠️ Generador (/gen)", "menu_tool_gen", "primary"),
    "extra": ("💳 Extrapolador (/extra)", "menu_tool_extra", "primary"),
    "bin": ("🔍 Consultar BIN (/bin)", "menu_tool_bin", "primary"),
    "rfc": ("🪪 Registro RFC (/rfc)", "menu_tool_rfc", "primary"),
    "telcel": ("📱 Telcel (/telcel)", "menu_telcel", "primary"),
    "back_tools": ("⬅️ Volver a Herramientas", "menu_tools", "primary"),
}


def _build_tool_button(key: str) -> InlineKeyboardButton:
    text, callback, style = TOOL_BUTTONS[key]
    return InlineKeyboardButton(text=text, callback_data=callback, style=style)


def tools_menu() -> InlineKeyboardMarkup:
    """Menú de Herramientas: Generador, Extrapolador, consulta BIN, RFC y Telcel."""
    builder = InlineKeyboardBuilder()
    builder.row(_build_tool_button("gen"))
    builder.row(_build_tool_button("extra"))
    builder.row(_build_tool_button("bin"))
    builder.row(_build_tool_button("rfc"))
    builder.row(_build_tool_button("telcel"))
    # Navegación
    builder.row(_build_button("back"), _build_button("close"))
    return builder.as_markup()


def tool_detail_menu() -> InlineKeyboardMarkup:
    """Menú dentro del detalle de cada herramienta: volver y cerrar."""
    builder = InlineKeyboardBuilder()
    builder.row(_build_tool_button("back_tools"))
    builder.row(_build_button("close"))
    return builder.as_markup()


# ─────────────────────────────────────────────────────────────
# Paneles de administración
# ─────────────────────────────────────────────────────────────

ADMIN_BUTTONS = {
    "assign_admins": ("👥 Asignar admins", "admin_assign", "primary"),
    "gencookie_admins": ("🍪 GenCookie admins", "admin_gencookie_admins", "primary"),
    "gencookie_users": ("🍪 GenCookie usuarios", "admin_gencookie_users", "primary"),
    "users": ("👤 Gestionar usuarios", "admin_users", "primary"),
    "memberships": ("🎟️ Membresías", "admin_memberships", "primary"),
    "galletas": ("🍪 Galletas", "admin_galletas", "primary"),
    "matanga": ("💀 Matanga", "admin_matanga", "danger"),
    "ajuste": ("🧰 Ajuste galletas", "admin_ajuste", "primary"),
    "credits": ("💳 Créditos", "admin_credits", "primary"),
    "sms": ("📱 SMS virtual", "admin_sms", "primary"),
    "messages": ("📨 Mensajes", "admin_messages", "primary"),
    "maintenance": ("🛠️ Mantenimiento", "admin_maintenance", "primary"),
    "close_admin": ("❌ Cerrar panel", "admin_close", "danger"),
    "back_admin": ("⬅️ Volver al panel", "admin_back", "primary"),
}

GENCOOKIE_BUTTONS = {
    "gc_add": ("➕ Agregar", "gencookie_add", "success"),
    "gc_remove": ("➖ Quitar", "gencookie_remove", "danger"),
    "gc_list": ("📋 Ver lista", "gencookie_list", "primary"),
    "gc_back": ("⬅️ Volver", "admin_back", "primary"),
}

MESSAGE_BUTTONS = {
    "msg_all": ("📨 A todos los usuarios", "msg_target_all", "primary"),
    "msg_admins": ("👥 A admins", "msg_target_admins", "primary"),
    "msg_eternal": ("🌐 Eternal Tartaro", "msg_target_eternal_tartaro", "primary"),
    "msg_olimpo": ("⚡ OLIMPO BINS", "msg_target_olimpo_bins", "primary"),
    "msg_user": ("👤 A un usuario", "msg_target_user", "primary"),
    "msg_back": ("⬅️ Volver", "admin_back", "primary"),
    "msg_cancel": ("❌ Cancelar", "msg_cancel", "danger"),
    "msg_confirm": ("✅ Confirmar envío", "msg_confirm", "success"),
    "msg_done": ("✅ Listo — vista previa", "msg_done", "success"),
    "msg_reset": ("🗑 Empezar de nuevo", "msg_reset", "danger"),
}

GALLETAS_BUTTONS = {
    "gal_olimpo": ("⚡ Galletas Olimpo (30)", "gal_assign_olimpo", "primary"),
    "gal_rancias": ("🍪 Galletas Rancias (20)", "gal_assign_rancias", "primary"),
    "gal_confirm": ("✅ Confirmar asignación", "gal_confirm", "success"),
    "gal_cancel": ("❌ Cancelar", "gal_cancel", "danger"),
}

MEMBERSHIP_BUTTONS = {
    "mem_semanal": ("🔱 Semanal", "mem_assign_semanal", "primary"),
    "mem_quincenal": ("🔱 Quincenal", "mem_assign_quincenal", "primary"),
    "mem_mensual": ("🔱 Mensual", "mem_assign_mensual", "primary"),
    "mem_olimpo": ("⚡ Olimpo", "mem_assign_olimpo", "primary"),
    "mem_personalizado": ("📅 Personalizado", "mem_assign_personalizado", "primary"),
    "mem_remove": ("➖ Quitar membresía", "mem_remove_start", "danger"),
    "mem_back": ("⬅️ Volver", "admin_back", "primary"),
    "mem_cancel": ("❌ Cancelar", "mem_cancel", "danger"),
    "mem_confirm": ("✅ Confirmar asignación", "mem_confirm", "success"),
    "mem_confirm_remove": ("✅ Confirmar eliminación", "mem_confirm_remove", "success"),
}

CREDITS_BUTTONS = {
    "cred_add": ("➕ Asignar créditos", "credits_add", "success"),
    "cred_withdraw": ("➖ Retirar créditos", "credits_withdraw", "danger"),
    "cred_query": ("🔍 Consultar saldo", "credits_query", "primary"),
    "cred_back": ("⬅️ Volver", "admin_back", "primary"),
    "cred_cancel": ("❌ Cancelar", "credits_cancel", "danger"),
    "cred_confirm": ("✅ Confirmar", "credits_confirm", "success"),
}

def _build_message_button(key: str) -> InlineKeyboardButton:
    text, callback, style = MESSAGE_BUTTONS[key]
    return InlineKeyboardButton(text=text, callback_data=callback, style=style)


def _build_galletas_button(key: str) -> InlineKeyboardButton:
    text, callback, style = GALLETAS_BUTTONS[key]
    return InlineKeyboardButton(text=text, callback_data=callback, style=style)


def _build_membership_button(key: str) -> InlineKeyboardButton:
    text, callback, style = MEMBERSHIP_BUTTONS[key]
    return InlineKeyboardButton(text=text, callback_data=callback, style=style)


def _build_admin_button(key: str) -> InlineKeyboardButton:
    text, callback, style = ADMIN_BUTTONS[key]
    return InlineKeyboardButton(text=text, callback_data=callback, style=style)


def _build_gencookie_button(key: str) -> InlineKeyboardButton:
    text, callback, style = GENCOOKIE_BUTTONS[key]
    return InlineKeyboardButton(text=text, callback_data=callback, style=style)


def admin_main_menu(is_super: bool = False) -> InlineKeyboardMarkup:
    """Panel principal de administración. Super admins ven opciones extra."""
    builder = InlineKeyboardBuilder()

    if is_super:
        builder.row(_build_admin_button("assign_admins"))
        builder.row(_build_admin_button("gencookie_admins"), _build_admin_button("gencookie_users"))
        builder.row(_build_admin_button("matanga"))
        builder.row(_build_admin_button("ajuste"))
    builder.row(_build_admin_button("users"), _build_admin_button("memberships"))
    builder.row(_build_admin_button("galletas"), _build_admin_button("sms"))
    builder.row(_build_admin_button("credits"), _build_admin_button("messages"))
    builder.row(_build_admin_button("maintenance"))
    builder.row(_build_admin_button("close_admin"))

    return builder.as_markup()


def gencookie_access_menu() -> InlineKeyboardMarkup:
    """Menú de gestión de acceso a /gencookie (admins o usuarios)."""
    builder = InlineKeyboardBuilder()
    builder.row(_build_gencookie_button("gc_add"), _build_gencookie_button("gc_remove"))
    builder.row(_build_gencookie_button("gc_list"))
    builder.row(_build_gencookie_button("gc_back"))
    return builder.as_markup()


def admin_assign_menu() -> InlineKeyboardMarkup:
    """Menú de asignación de administradores (solo super admins)."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="➕ Agregar admin", callback_data="admin_add", style="success"),
        InlineKeyboardButton(text="➖ Quitar admin", callback_data="admin_remove", style="danger"),
    )
    builder.row(_build_admin_button("back_admin"))
    return builder.as_markup()


def admin_subpanel_menu() -> InlineKeyboardMarkup:
    """Menú genérico para subpaneles de admin."""
    builder = InlineKeyboardBuilder()
    builder.row(_build_admin_button("back_admin"))
    return builder.as_markup()


def messages_menu() -> InlineKeyboardMarkup:
    """Menú de selección de destinatarios para mensajes."""
    builder = InlineKeyboardBuilder()
    builder.row(_build_message_button("msg_all"))
    builder.row(_build_message_button("msg_admins"))
    builder.row(_build_message_button("msg_eternal"))
    builder.row(_build_message_button("msg_olimpo"))
    builder.row(_build_message_button("msg_user"))
    builder.row(_build_message_button("msg_back"))
    return builder.as_markup()


def message_confirm_menu() -> InlineKeyboardMarkup:
    """Menú de confirmación de envío de mensaje."""
    builder = InlineKeyboardBuilder()
    builder.row(_build_message_button("msg_confirm"), _build_message_button("msg_cancel"))
    return builder.as_markup()


def message_pack_menu() -> InlineKeyboardMarkup:
    """Menú mientras se coleccionan los elementos del paquete de difusión."""
    builder = InlineKeyboardBuilder()
    builder.row(_build_message_button("msg_done"))
    builder.row(_build_message_button("msg_reset"), _build_message_button("msg_cancel"))
    return builder.as_markup()


def memberships_menu() -> InlineKeyboardMarkup:
    """Menú de selección de plan de membresía."""
    builder = InlineKeyboardBuilder()
    builder.row(_build_membership_button("mem_semanal"), _build_membership_button("mem_quincenal"))
    builder.row(_build_membership_button("mem_mensual"), _build_membership_button("mem_olimpo"))
    builder.row(_build_membership_button("mem_personalizado"))
    builder.row(_build_membership_button("mem_remove"))
    builder.row(_build_membership_button("mem_back"))
    return builder.as_markup()


def membership_confirm_menu() -> InlineKeyboardMarkup:
    """Menú de confirmación de asignación de membresía."""
    builder = InlineKeyboardBuilder()
    builder.row(_build_membership_button("mem_confirm"), _build_membership_button("mem_cancel"))
    return builder.as_markup()


def membership_remove_menu() -> InlineKeyboardMarkup:
    """Menú de confirmación de eliminación de membresía."""
    builder = InlineKeyboardBuilder()
    builder.row(_build_membership_button("mem_confirm_remove"), _build_membership_button("mem_cancel"))
    return builder.as_markup()


def galletas_menu() -> InlineKeyboardMarkup:
    """Menú de selección de paquete de galletas."""
    builder = InlineKeyboardBuilder()
    builder.row(_build_galletas_button("gal_olimpo"), _build_galletas_button("gal_rancias"))
    builder.row(_build_admin_button("back_admin"))
    return builder.as_markup()


def galletas_confirm_menu() -> InlineKeyboardMarkup:
    """Menú de confirmación de asignación de galletas."""
    builder = InlineKeyboardBuilder()
    builder.row(_build_galletas_button("gal_confirm"), _build_galletas_button("gal_cancel"))
    return builder.as_markup()


MATANGA_BUTTONS = {
    "mat_galletas": ("🍪 Quitar galletas", "mat_remove_galletas", "danger"),
    "mat_creditos": ("💳 Quitar créditos", "mat_remove_creditos", "danger"),
    "mat_confirm": ("✅ Confirmar", "mat_confirm", "success"),
    "mat_cancel": ("❌ Cancelar", "mat_cancel", "danger"),
}


def _build_matanga_button(key: str) -> InlineKeyboardButton:
    text, callback, style = MATANGA_BUTTONS[key]
    return InlineKeyboardButton(text=text, callback_data=callback, style=style)


def matanga_menu() -> InlineKeyboardMarkup:
    """Menú de selección de objetivo de matanga (solo super admins)."""
    builder = InlineKeyboardBuilder()
    builder.row(_build_matanga_button("mat_galletas"), _build_matanga_button("mat_creditos"))
    builder.row(_build_admin_button("back_admin"))
    return builder.as_markup()


def matanga_confirm_menu() -> InlineKeyboardMarkup:
    """Menú de confirmación de matanga."""
    builder = InlineKeyboardBuilder()
    builder.row(_build_matanga_button("mat_confirm"), _build_matanga_button("mat_cancel"))
    return builder.as_markup()


AJUSTE_BUTTONS = {
    "aj_refill": ("🔄 Recargar al total", "aj_action_refill", "primary"),
    "aj_set": ("✏️ Fijar cupo", "aj_action_set", "primary"),
    "aj_confirm": ("✅ Confirmar", "aj_confirm", "success"),
    "aj_cancel": ("❌ Cancelar", "aj_cancel", "danger"),
}


def _build_ajuste_button(key: str) -> InlineKeyboardButton:
    text, callback, style = AJUSTE_BUTTONS[key]
    return InlineKeyboardButton(text=text, callback_data=callback, style=style)


def ajuste_menu() -> InlineKeyboardMarkup:
    """Menú de ajuste manual de galletas (solo super admins)."""
    builder = InlineKeyboardBuilder()
    builder.row(_build_ajuste_button("aj_refill"), _build_ajuste_button("aj_set"))
    builder.row(_build_admin_button("back_admin"))
    return builder.as_markup()


def ajuste_confirm_menu() -> InlineKeyboardMarkup:
    """Menú de confirmación de ajuste manual."""
    builder = InlineKeyboardBuilder()
    builder.row(_build_ajuste_button("aj_confirm"), _build_ajuste_button("aj_cancel"))
    return builder.as_markup()


def _build_credits_button(key: str) -> InlineKeyboardButton:
    text, callback, style = CREDITS_BUTTONS[key]
    return InlineKeyboardButton(text=text, callback_data=callback, style=style)


def credits_menu() -> InlineKeyboardMarkup:
    """Menú principal de gestión de créditos."""
    builder = InlineKeyboardBuilder()
    builder.row(_build_credits_button("cred_add"), _build_credits_button("cred_withdraw"))
    builder.row(_build_credits_button("cred_query"))
    builder.row(_build_credits_button("cred_back"))
    return builder.as_markup()


def credits_confirm_menu() -> InlineKeyboardMarkup:
    """Menú de confirmación para asignar/retirar créditos."""
    builder = InlineKeyboardBuilder()
    builder.row(_build_credits_button("cred_confirm"), _build_credits_button("cred_cancel"))
    return builder.as_markup()


def credits_subpanel_menu() -> InlineKeyboardMarkup:
    """Menú genérico para subpaneles de créditos."""
    builder = InlineKeyboardBuilder()
    builder.row(_build_credits_button("cred_back"))
    return builder.as_markup()


SMS_BUTTONS = {
    "sms_stats": ("📊 Estadísticas", "smsadm_stats", "primary"),
    "sms_orders": ("📋 Últimos pedidos", "smsadm_orders", "primary"),
    "sms_tiers": ("📊 Tramos de precios", "smsadm_tiers", "primary"),
    "sms_config": ("⚙️ Configuración", "smsadm_config", "primary"),
    "sms_toggle": ("🔛 Activar/Desactivar", "smsadm_toggle", "primary"),
    "sms_refresh_prices": ("🔄 Ver precios API", "smsadm_prices", "primary"),
    "sms_tier_add": ("➕ Agregar tramo", "smsadm_tier_add", "success"),
    "sms_confirm": ("✅ Confirmar", "smsadm_confirm", "success"),
    "sms_cancel": ("❌ Cancelar", "smsadm_cancel", "danger"),
}


def _build_sms_button(key: str) -> InlineKeyboardButton:
    text, callback, style = SMS_BUTTONS[key]
    return InlineKeyboardButton(text=text, callback_data=callback, style=style)


def sms_admin_menu() -> InlineKeyboardMarkup:
    """Panel SMS virtual en /batman (solo super admins)."""
    builder = InlineKeyboardBuilder()
    builder.row(_build_sms_button("sms_stats"))
    builder.row(_build_sms_button("sms_orders"))
    builder.row(_build_sms_button("sms_tiers"))
    builder.row(_build_sms_button("sms_config"))
    builder.row(_build_admin_button("back_admin"))
    return builder.as_markup()


def sms_admin_subpanel_menu() -> InlineKeyboardMarkup:
    """Volver al panel SMS."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="⬅️ Volver a SMS", callback_data="admin_sms", style="primary"
        )
    )
    builder.row(_build_admin_button("back_admin"))
    return builder.as_markup()


def sms_admin_confirm_menu() -> InlineKeyboardMarkup:
    """Confirmar/cancelar acción del panel SMS."""
    builder = InlineKeyboardBuilder()
    builder.row(_build_sms_button("sms_confirm"), _build_sms_button("sms_cancel"))
    return builder.as_markup()


# ─────────────────────────────────────────────────────────────
# Telcel (2026-11-05: saldo oculto del panel — gate no funciona).
# Namespace propio "menu_telcel" / "menu_telg_*" para no empalmar.
# ─────────────────────────────────────────────────────────────
def telcel_menu() -> InlineKeyboardMarkup:
    """Panel Telcel: megas (gratis), factura y recarga (10 créditos) + cerrar."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📶 Megas (/megas) — Gratis", callback_data="menu_telg_megas", style="primary"),
    )
    builder.row(
        InlineKeyboardButton(text="🧾 Factura (/factura) — 10 créditos", callback_data="menu_telg_factura", style="primary"),
    )
    builder.row(
        InlineKeyboardButton(text="⚡ Recarga (/recarga) — 10 créditos", callback_data="menu_telg_recarga", style="primary"),
    )
    builder.row(_build_button("close"))
    return builder.as_markup()


def telcel_gate_menu() -> InlineKeyboardMarkup:
    """Volver al panel Telcel + cerrar."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="⬅️ Volver a Telcel", callback_data="menu_telcel", style="primary"),
    )
    builder.row(_build_button("close"))
    return builder.as_markup()


# ─────────────────────────────────────────────────────────────
# IA / Generación de imágenes por IA
# ─────────────────────────────────────────────────────────────

def ai_menu() -> InlineKeyboardMarkup:
    """Menú principal de IA: modelos, configuración, cerrar."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🤖 Modelos", callback_data="ai_models", style="primary"),
        InlineKeyboardButton(text="⚙️ Configuración", callback_data="ai_settings", style="primary"),
    )
    builder.row(_build_button("close"))
    return builder.as_markup()


def ai_settings_menu() -> InlineKeyboardMarkup:
    """Menú de configuración de IA."""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="🔍 Modelos", callback_data="ai_models", style="primary"),
    )
    
    builder.row(
        InlineKeyboardButton(text="📊 768", callback_data="ai_resolution_768", style="primary"),
        InlineKeyboardButton(text="✅ 1K", callback_data="ai_resolution_1K", style="success"),
        InlineKeyboardButton(text="🔍 1.5K", callback_data="ai_resolution_1.5K", style="primary"),
        InlineKeyboardButton(text="⚡ 2K", callback_data="ai_resolution_2K", style="primary"),
        InlineKeyboardButton(text="🔥 4K", callback_data="ai_resolution_4K", style="primary"),
    )
    
    builder.row(
        InlineKeyboardButton(text="landscape 16:9", callback_data="ai_aspect_16:9", style="success"),
        InlineKeyboardButton(text="portrait 9:16", callback_data="ai_aspect_9:16", style="primary"),
        InlineKeyboardButton(text="cuadrado 1:1", callback_data="ai_aspect_1:1", style="primary"),
    )
    
    builder.row(
        InlineKeyboardButton(text="auto", callback_data="ai_quality_auto", style="primary"),
        InlineKeyboardButton(text="low", callback_data="ai_quality_low", style="primary"),
        InlineKeyboardButton(text="medium", callback_data="ai_quality_medium", style="primary"),
        InlineKeyboardButton(text="high ✅", callback_data="ai_quality_high", style="success"),
    )
    
    builder.row(
        InlineKeyboardButton(text="PNG", callback_data="ai_format_png", style="success"),
        InlineKeyboardButton(text="JPEG", callback_data="ai_format_jpeg", style="primary"),
        InlineKeyboardButton(text="WEBP", callback_data="ai_format_webp", style="primary"),
    )
    
    builder.row(
        InlineKeyboardButton(text="🔒 Censura: 1", callback_data="ai_tolerance_1", style="danger"),
        InlineKeyboardButton(text="🛡️ Censura: 3", callback_data="ai_tolerance_3", style="primary"),
        InlineKeyboardButton(text="🔓 Censura: 5", callback_data="ai_tolerance_5", style="success"),
    )
    
    builder.row(
        InlineKeyboardButton(text="⬅️ Volver", callback_data="menu_tools", style="primary"),
        _build_button("close"),
    )
    return builder.as_markup()


def ai_model_selection_menu() -> InlineKeyboardMarkup:
    """Menú de selección de modelos."""
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="🔥 FLUX 3 Klein ✅", callback_data="ai_model_flux_3_klein", style="success"),
    )
    builder.row(
        InlineKeyboardButton(text="⚡ FLUX 3", callback_data="ai_model_flux_3", style="primary"),
    )
    builder.row(
        InlineKeyboardButton(text="🎨 Recraft V4", callback_data="ai_model_recraft_v4", style="primary"),
    )
    builder.row(
        InlineKeyboardButton(text="📷 Seedream", callback_data="ai_model_seedream", style="primary"),
    )
    
    builder.row(
        InlineKeyboardButton(text="⬅️ Volver", callback_data="ai_settings", style="primary"),
        _build_button("close"),
    )
    return builder.as_markup()
