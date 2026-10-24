"""Detección de cuenta suspendida por Amazon.

Amazon responde "Su cuenta de Amazon está suspendida" (MX) o "Your account has
been suspended" (US) en la página que queda después de validar el OTP. Esa
página se descartaba en el flujo: el creador reportaba "Account created!" y
entregaba al usuario cookies de cuentas ya suspendidas. Todos los archivos de
`data/amazon_accounts/` tienen `warning` vacío (129 de 129) mientras Amazon ya
suspendía cuentas frescas desde el 19-oct-2026.

Los marcadores viven aquí para que el creador y el gate apliquen el mismo
criterio. Son patrones concretos ("cuenta ... suspendida"), no palabras
sueltas: una página normal con la sesión activa no debe dar positivo.
"""
from __future__ import annotations

import re

_PATTERNS = (
    r"su cuenta (?:de amazon )?est[aá] suspendida",
    r"su cuenta ha sido suspendida",
    r"hemos suspendido su cuenta",
    r"amazon ha suspendido (?:su |tu )?cuenta",
    r"your account (?:has been |is )?suspended",
    r"we have suspended your account",
    r"account suspended",
    r"your account (?:has been )?deactivated",
    r"su cuenta (?:de amazon )?(?:ha sido |est[aá] )?(?:deshabilitada|desactivada)",
)

_RE = tuple(re.compile(p) for p in _PATTERNS)


def detect_suspension(text: str | None) -> bool:
    """True si el cuerpo de la página indica que la cuenta está suspendida."""
    if not text:
        return False
    low = str(text).lower()
    return any(rx.search(low) for rx in _RE)


def suspension_reason(text: str | None) -> str:
    """Devuelve la frase de suspensión encontrada (para logs), o cadena vacía."""
    if not text:
        return ""
    low = str(text).lower()
    for rx in _RE:
        m = rx.search(low)
        if m:
            return m.group(0)
    return ""
