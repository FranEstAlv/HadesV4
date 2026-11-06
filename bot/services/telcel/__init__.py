"""Servicios Telcel: WAF solver (Stormcaster) + gates integrados.

Fuente: evidencias/apiTelcel (2026-10-20), consolidada sin borrar el original.
- wafSolver/ : copia íntegra del solver WAF de pay.telcel.com (py + .wasm).
- gates.py   : los cuatro flujos probados (saldo, megas, factura, recarga).
"""

from bot.services.telcel.gates import (
    gate_saldo,
    gate_megas,
    gate_factura,
    gate_recarga,
)

__all__ = [
    "gate_saldo",
    "gate_megas",
    "gate_factura",
    "gate_recarga",
]
