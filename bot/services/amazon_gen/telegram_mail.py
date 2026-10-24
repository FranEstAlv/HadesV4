"""Proveedor de email manual para la creación de cuentas Amazon desde Telegram.

El usuario proporciona el correo al iniciar el comando y el OTP cuando el bot se lo pide.
"""
import asyncio
import threading
from typing import Callable, Coroutine, Optional


class TelegramMail:
    """Implementa la interfaz mínima que espera Creator de accountCreator.py."""

    def __init__(
        self,
        address: str,
        loop: Optional[asyncio.AbstractEventLoop] = None,
        notify_callback: Optional[Callable[[], Coroutine]] = None,
    ):
        self.address = address.strip()
        self.token = "manual"
        self.wcode: Optional[str] = None
        self._event = threading.Event()
        self._stopper = threading.Event()
        self._loop = loop
        self._notify_callback = notify_callback
        self._notified = False

    def getAddr(self):
        return {
            "activationId": self.address,
            "number": self.address,
            "normalizedNumber": self.address,
            "cost": 0.0,
            "domain": self.address.split("@")[-1],
        }

    def watch(self, *args, **kwargs):
        pass

    def waitCode(self, activation_id=None, timeout: Optional[int] = None, since=None):
        """Bloquea hasta que set_code sea llamado desde el handler de Telegram."""
        # El flujo original pide 10 segundos, pero en Telegram el usuario necesita
        # tiempo para revisar el email. Usamos siempre 5 minutos.
        effective_timeout = 300
        if not self._notified and self._notify_callback and self._loop:
            self._notified = True
            try:
                asyncio.run_coroutine_threadsafe(self._notify_callback(), self._loop)
            except Exception:
                pass
        if self._stopper.wait(timeout=0):
            return self.wcode
        ok = self._event.wait(timeout=effective_timeout)
        if not ok:
            return None
        return self.wcode

    def set_code(self, code: str):
        """Llamado por el handler de Telegram cuando el usuario manda el OTP."""
        self.wcode = code.strip()
        self._event.set()

    def stop(self):
        self._stopper.set()
        self._event.set()

    def markOk(self, *args):
        pass

    def ban(self, *args):
        pass

    def cancel(self, *args):
        self.stop()

    def finish(self, *args):
        self.stop()

    def balance(self):
        return None


# user_id -> TelegramMail activa
_pending_otp: dict[int, TelegramMail] = {}


def register_pending(user_id: int, mail: TelegramMail) -> None:
    _pending_otp[user_id] = mail


def get_pending(user_id: int) -> Optional[TelegramMail]:
    return _pending_otp.get(user_id)


def remove_pending(user_id: int) -> None:
    _pending_otp.pop(user_id, None)
