"""Helpers de borrado diferido de mensajes (Fase 2 auditoría).

Centraliza el patrón que estaba duplicado en 8 handlers:
cada uno definía su propio `_delete_after` + `asyncio.create_task(...)`
sin registrar excepciones. Ahora hay una sola función con logging.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Union

logger = logging.getLogger(__name__)


async def delete_after(message, delay: int = 30) -> None:
    """Borra `message` tras `delay` segundos. Nunca lanza excepción."""
    try:
        await asyncio.sleep(delay)
        await message.delete()
    except Exception as exc:
        logger.debug("delete_after: no se pudo borrar mensaje: %s", exc)


def schedule_delete(message, delay: int = 30) -> "asyncio.Task":
    """Programa `delete_after` con log de excepciones inesperadas.

    Retorna la Task para que el caller pueda cancelarla si quiere.
    """
    task = asyncio.create_task(delete_after(message, delay))

    def _done(t: "asyncio.Task") -> None:
        try:
            exc = t.exception()
        except asyncio.CancelledError:
            return
        if exc is not None:
            logger.warning("schedule_delete: tarea terminó con error: %s", exc)

    task.add_done_callback(_done)
    return task


__all__ = ["delete_after", "schedule_delete"]
