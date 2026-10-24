"""Deteccion de cuenta suspendida por Amazon.

Amazon avisa "Su cuenta de Amazon esta suspendida" (MX) o "Your account has
been suspended" (US) en la pagina que queda despues de validar el OTP. Esa
pagina antes se descartaba y la cuenta se entregaba como creada.

Los marcadores son patrones concretos ("cuenta ... suspendida"), no palabras
sueltas: una pagina normal con la sesion activa no debe dar positivo.
"""
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


def detect_suspension(text):
    """True si el cuerpo de la pagina indica que la cuenta quedo suspendida."""
    if not text:
        return False
    low = str(text).lower()
    return any(rx.search(low) for rx in _RE)


def suspension_reason(text):
    """Devuelve la frase de suspension encontrada (para logs) o cadena vacia."""
    if not text:
        return ""
    low = str(text).lower()
    for rx in _RE:
        m = rx.search(low)
        if m:
            return m.group(0)
    return ""
