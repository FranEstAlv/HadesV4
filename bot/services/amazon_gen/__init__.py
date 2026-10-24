"""Generador de cuentas Amazon adaptado para Hades."""
from .accountCreator import Creator, supported, countries
from .credsBypass import encryptPassw

__all__ = ["Creator", "supported", "countries", "encryptPassw"]
