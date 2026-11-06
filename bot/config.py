import os
from pathlib import Path
from typing import Dict, List, Optional, Set
from dotenv import load_dotenv
from bot.services import proxies as _iproyal

load_dotenv()

# Directorio base del proyecto
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# Configuración del bot
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

def require_env(name: str, default: str = "") -> str:
    """Lee una variable de entorno obligatoria.

    Si falta y no hay default, lanza RuntimeError al arrancar para
    fallar rápido en vez de fallar tarde en runtime (Fase 1).
    """
    val = os.getenv(name, default)
    if not val:
        raise RuntimeError(f"Falta variable de entorno obligatoria: {name}")
    return val


# Super administradores: acceso ilimitado, pueden asignar/retirar admins.
# Estos IDs se configuran en código/env y NUNCA se muestran en paneles.
SUPER_ADMIN_IDS: Set[int] = set()
# Compatibilidad: .env histórico usa SUPER_ADMINS; el nombre canónico es
# SUPER_ADMIN_IDS. Se aceptan ambas, con prioridad a la canónica.
raw_super_admin_ids = os.getenv("SUPER_ADMIN_IDS", "") or os.getenv("SUPER_ADMINS", "")
if raw_super_admin_ids:
    SUPER_ADMIN_IDS = {int(x.strip()) for x in raw_super_admin_ids.split(",") if x.strip().isdigit()}

# Por defecto si no se configura en .env
# Fernando (Mx): 6060544328 · Segundo super admin: 5531198491.
if not SUPER_ADMIN_IDS:
    SUPER_ADMIN_IDS = {6060544328, 5531198491}

# Administradores normales: asignados por super admins. En paneles parecen iguales.
ADMIN_IDS: List[int] = []
raw_admin_ids = os.getenv("ADMIN_IDS", "")
if raw_admin_ids:
    ADMIN_IDS = [int(x.strip()) for x in raw_admin_ids.split(",") if x.strip().isdigit()]

# Archivos de persistencia temporal (hasta migrar a base de datos)
USERS_FILE = DATA_DIR / "users.json"
ADMINS_FILE = DATA_DIR / "admins.json"

# Grupos oficiales
ADMIN_CONSOLE_GROUP_ID: int = -1003869047792
ETERNAL_TARTARO_GROUP_ID: int = -1003744096880
OLIMPO_BINS_GROUP_ID: int = -1003503429012

OFFICIAL_GROUPS: Dict[str, int] = {
    "admin_console": ADMIN_CONSOLE_GROUP_ID,
    "eternal_tartaro": ETERNAL_TARTARO_GROUP_ID,
    "olimpo_bins": OLIMPO_BINS_GROUP_ID,
}

# Canal de logs para alertas automáticas del bot
LOGS_CHANNEL_ID: int = -1003338645836

# Base de datos
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://user:password@localhost/gencookie"
)

# Etiquetas de planes de membresía (deben coincidir con los callback_data de admin)
PLAN_LABELS: Dict[str, str] = {
    "semanal": "Semanal",
    "quincenal": "Quincenal",
    "mensual": "Mensual",
    "olimpo": "Olimpo",
    "personalizado": "Personalizado",
}

# Claves de encriptación (WAF Solver)
AES_KEY = bytes.fromhex(os.getenv("AES_KEY", "6f71a512b1e035eaab53d8be73120d3fb68a0ca346b9560aab3e5cdf753d5e98"))
C_SCRYPT = os.getenv("C_SCRYPT", "h72f957df656e80ba55f5d8ce2e8c7ccb59687dba3bfb273d54b08a261b2f3002")
C_SHA256 = os.getenv("C_SHA256", "h7b0c470f0cfe3a80a9e26526ad185f484f6817d0832712a4a37a908786a6a67f")
C_BANDWIDTH = os.getenv("C_BANDWIDTH", "ha9faaffd31b4d5ede2a2e19d2d7fd525f66fee61911511960dcbb52d3c48ce25")

# Precios
COOKIE_COST = int(os.getenv("COOKIE_COST", "10"))
VERIFICATION_COST = int(os.getenv("VERIFICATION_COST", "5"))
VIP_COST = int(os.getenv("VIP_COST", "1000"))

# Costos de créditos del bot (fuente única — no hardcodear en handlers).
# Masivos (/dimass, /phmass, /eumass, /womass, /amzmass, /mdmass, /rdmass,
# /irmass, /himass, /tlmass): 5 créditos, gratis para admins y Olimpo.
MASS_CREDIT_COST = int(os.getenv("MASS_CREDIT_COST", "5"))
# Registro /rfc: 15 créditos, cobra a todo no-admin (incluido Olimpo).
RFC_CREDIT_COST = int(os.getenv("RFC_CREDIT_COST", "15"))

# Costo por uso de los gates Telcel (2026-11-03, ajuste 2026-11-05).
# Megas es gratis; factura y recarga cuestan 10 créditos. Saldo sigue
# definido pero oculto del panel hasta que funcione. Cada gate tiene su
# propia constante, ajustable vía variable de entorno sin tocar el código.
TELCEL_SALDO_COST = int(os.getenv("TELCEL_SALDO_COST", "5"))
TELCEL_MEGAS_COST = int(os.getenv("TELCEL_MEGAS_COST", "0"))
TELCEL_FACTURA_COST = int(os.getenv("TELCEL_FACTURA_COST", "10"))
TELCEL_RECARGA_COST = int(os.getenv("TELCEL_RECARGA_COST", "10"))
# Cooldown entre usos de cualquier comando Telcel (por usuario, segundos).
TELCEL_COOLDOWN_S = int(os.getenv("TELCEL_COOLDOWN_S", "60"))
# Proxy opcional para los gates Telcel (vacío = conexión directa).
TELCEL_PROXY = os.getenv("TELCEL_PROXY", "")
# Secrets de los gates Telcel (Fase 1: antes hardcodeados en gates.py).
# Si faltan, el bot arranca igual pero los gates Telcel fallan con mensaje
# claro al usuario; el import NO debe tumbar todo el bot.
TELCEL_DECRYPT_PASSWORD = os.getenv("TELCEL_DECRYPT_PASSWORD", "")
TELCEL_ENCRYPT_PASSWORD = os.getenv("TELCEL_ENCRYPT_PASSWORD", "")
TELCEL_T1_BEARER = os.getenv("TELCEL_T1_BEARER", "")
TELCEL_MITELCEL_BEARER = os.getenv(
    "TELCEL_MITELCEL_BEARER", "479987c7-c264-375f-91d6-55346af3a529"
)

# Costo de generación de imágenes por IA (2026-11-05).
IMAGE_GENERATION_COST = int(os.getenv("IMAGE_GENERATION_COST", "10"))

# API Key para generación de imágenes por IA (OpenRouter)
# Si falta, el módulo de IA fallará pero el bot seguirá funcionando.
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")

# Configuración general
DEFAULT_COUNTRY = os.getenv("DEFAULT_COUNTRY", "US")
GENERATION_DELAY = int(os.getenv("GENERATION_DELAY", "5"))

# ---------------------------------------------------------------------------
# Proxies IPRoyal
# ---------------------------------------------------------------------------
# La fuente única es el bloque IPROYAL_* del .env (ver bot/services/proxies.py):
#   - gates /di /ph /eu /wo /rd y sus masivos -> sticky con lifetime
#   - generador de cuentas Amazon (/gencookie) -> pool internacional de países
# Las listas GATE_PROXIES_* se conservan solo por compatibilidad; el código
# nuevo debe pedir el proxy con gate_proxy() para estrenar sesión en cada flujo.
IPROYAL_USERNAME = _iproyal.USERNAME
IPROYAL_PASSWORD_BASE = _iproyal.PASSWORD_BASE
IPROYAL_EXTRA_ARGS = _iproyal.EXTRA_ARGS
IPROYAL_PORT = _iproyal.PORT
IPROYAL_PROTOCOL = _iproyal.PROTOCOL

# Alias con los nombres viejos que aún usan algunos módulos/scripts.
IPROYAL_USER = IPROYAL_USERNAME
IPROYAL_PASS = IPROYAL_PASSWORD_BASE
IPROYAL_HOST = f"{_iproyal.HOST}:{_iproyal.PORT}"

GATE_STICKY_COUNTRY = _iproyal.GATE_STICKY_COUNTRY
GATE_PROXIES_MX: List[str] = _iproyal.gate_proxies("mx", 8)
GATE_PROXIES_ROT: List[str] = _iproyal.gate_proxies("rot", 4)
GATE_PROXIES: List[str] = GATE_PROXIES_MX + GATE_PROXIES_ROT


def gate_proxy(pool: str = "mx") -> Optional[str]:
    """Proxy sticky (pool='mx') o rotatorio (pool='rot') para un gate."""
    return _iproyal.gate_proxy(pool)


# Países del pool internacional que el generador Amazon sabe crear.
_ACCOUNT_SUPPORTED = {
    "MX", "US", "CA", "BR", "ES", "IT",
}
ACCOUNT_PROXY_COUNTRIES: List[str] = [
    country
    for country in _iproyal.account_countries()
    if country in _ACCOUNT_SUPPORTED
] or ["MX", "US", "CA", "BR", "ES", "IT"]


def make_account_proxy(country: str) -> Optional[str]:
    """Proxy sticky del pool para el país indicado (mapea UK -> gb)."""
    return _iproyal.account_proxy(country)


ACCOUNT_PROXIES: Dict[str, str] = {
    country: proxy
    for country in ACCOUNT_PROXY_COUNTRIES
    if (proxy := make_account_proxy(country))
}


# --- Gate Amazon MX -------------------------------------------------------
# Perfil TLS que imita curl_cffi. Debe ir coherente con el User-Agent que
# manda el gate (ver bot/gates/amazon.py::_HEADERS_BASE): un chrome124 con
# UA Chrome/152 es una huella evitable (lección 7.7 de la skill atenea-gateway).
AMAZON_IMPERSONATE: str = os.getenv("AMAZON_IMPERSONATE", "chrome150")
AMAZON_BROWSER_VERSION: str = os.getenv("AMAZON_BROWSER_VERSION", "150")

# Nombre del titular que se manda en el alta de la tarjeta. Amazon exige
# que coincida con el de la cuenta para no marcar la tarjeta como sospechosa.
AMAZON_ACCOUNT_HOLDER_NAME: str = os.getenv(
    "AMAZON_ACCOUNT_HOLDER_NAME", "OLIMPO MEDINA"
)

# Segundos que una sesión de navegador (Browserless) se reusa antes de
# renovarse. El login en navegador real es lo más caro del gate.
AMAZON_BROWSER_SESSION_TTL_S: int = int(os.getenv("AMAZON_BROWSER_SESSION_TTL_S", "600"))

# Si vale 1, el gate escala a navegador real cuando la sesión guardada no
# sirve. Si vale 0, el gate solo trabaja con la cookie guardada.
AMAZON_BROWSER_FALLBACK: bool = os.getenv("AMAZON_BROWSER_FALLBACK", "1") not in (
    "0",
    "false",
    "False",
    "no",
)


def is_super_admin(user_id: int) -> bool:
    """True si el usuario es super admin (acceso ilimitado).

    Compatibilidad: la lógica real vive en bot.services.admins
    (tabla SQLite + SUPER_ADMIN_IDS). Esta copia solo existe para
    código que la importe directo sin acceso a la BD.
    """
    return user_id in SUPER_ADMIN_IDS


def is_admin(user_id: int) -> bool:
    """True si el usuario es admin o super admin. En paneles no se distingue.

    Compatibilidad (.env ADMIN_IDS): los handlers usan
    bot.services.admins.is_admin (tabla SQLite). No mezclar.
    """
    return user_id in SUPER_ADMIN_IDS or user_id in ADMIN_IDS
