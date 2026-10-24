"""Adaptación del generador de cuentas Amazon para Telegram (Hades).

Basado en la lógica de /home/olimpo/gencookie/GenCookie/main.py.
Se quitó la interfaz CLI; el correo y el OTP se proporcionan desde Telegram.
"""
import asyncio
import json
import logging
import time as _time
from datetime import datetime
from pathlib import Path
from typing import Callable, Coroutine, Optional

import aiosqlite

from bot.services.amazon_gen.accountCreator import Creator, supported
from bot.services.amazon_gen.credsBypass import TempMail
from bot.services.amazon_gen.telegram_mail import TelegramMail, register_pending, remove_pending
from bot.services.database import get_connection

logger = logging.getLogger(__name__)

_AVAILABLE_COUNTRIES = ["MX", "US", "CA", "BR", "ES", "IT"]

# Tareas de creación en curso, indexadas por user_id.
_running_creations: dict[int, asyncio.Task] = {}


def _save_cookie_file(result: dict) -> Path:
    """Guarda el resultado en data/amazon_accounts/ como el main.py original."""
    base = Path("data/amazon_accounts")
    base.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    p = result.get("profile", {})
    cc = result.get("registeredCountry", "XX")
    partial = "_partial" if result.get("warning") == "address_failed" else ""
    fname = f"{cc}_{ts}{partial}.json"
    data = {
        "created": datetime.now().isoformat(),
        "country": cc,
        "domain": result.get("domain", ""),
        "email": p.get("email", ""),
        "password": p.get("password", ""),
        "name": p.get("name", ""),
        "cookies": result.get("cookies", ""),
        "time": result.get("time", 0),
        "warning": result.get("warning", ""),
    }
    path = base / fname
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def _save_attempt_file(result: dict, user_id: int, email_mode: str = "", mail_token: str = "") -> Path | None:
    """Respaldo de credenciales intentadas aunque la creación falle.

    Si Amazon sí creó la cuenta pero el flujo se atoró después (OTP ok,
    dirección, respuesta desconocida), este archivo conserva email y
    contraseña para recuperación manual. Nunca debe mostrarse crudo al
    usuario: solo email/contraseña en caso de fallo con rescate.
    """
    base = Path("data/amazon_attempts")
    base.mkdir(parents=True, exist_ok=True)
    p = result.get("profile", {})
    if not p.get("email"):
        return None
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    cc = result.get("registeredCountry", "")
    fname = f"{cc or 'XX'}_{ts}_u{user_id}.json"
    data = {
        "created": datetime.now().isoformat(),
        "user_id": user_id,
        "email_mode": email_mode,
        "country": cc,
        "domain": result.get("domain", ""),
        "email": p.get("email", ""),
        "password": p.get("password", ""),
        "name": p.get("name", ""),
        "mail_token": mail_token,
        "tech_message": result.get("message", ""),
        "warning": result.get("warning", ""),
    }
    path = base / fname
    try:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
    except Exception as exc:
        logger.warning("No se pudo guardar respaldo de intento: %s", exc)
        return None
    return path


_FRIENDLY_ERRORS = (
    ("account_suspended", "Amazon creó la cuenta pero la suspendió de inmediato. Ese registro quedó bloqueado por el patrón de registro; no es usable. Genera una cuenta nueva más tarde."),
    ("unexpected_response", "Amazon respondió algo inesperado. Es posible que la cuenta sí se haya creado: intenta entrar a Amazon con ese correo antes de generar otra."),
    ("billing_failed", "La cuenta parece creada pero no se pudo agregar la dirección de entrega. Revisa el respaldo de credenciales antes de reintentar."),
    ("email_timeout", "No llegó el código de verificación a tiempo. El correo temporal pudo vencer; genera una cuenta nueva."),
    ("OTP not received", "No llegó el código de verificación a tiempo. El correo temporal pudo vencer; genera una cuenta nueva."),
    ("otp_failed", "Amazon rechazó el código de verificación. Esa dirección ya pudo quedar registrada; verifica antes de reintentar con otra."),
    ("email_associated", "Ese correo ya está registrado en Amazon. Usa otra dirección."),
    ("number_associated", "Ese correo/teléfono ya está asociado a una cuenta Amazon. Usa otro."),
    ("unusual activity", "Amazon marcó actividad inusual y bloqueó el intento. Espera unos minutos y prueba de nuevo."),
    ("captcha_appeared", "Amazon pidió una verificación extra (captcha) y no se pudo continuar. Intenta de nuevo más tarde."),
    ("sms_not_configured", "El servicio de números por SMS no está configurado, así que no se puede completar el registro. Avísale al administrador."),
    ("phone_required", "Amazon ahora pide un número de celular para terminar el registro y ese paso todavía no está disponible. Intenta más tarde."),
    ("phone_submit_rejected", "Amazon no aceptó el número de celular. Intenta de nuevo más tarde."),
    ("phone_submit_failed", "No se pudo enviar el número de celular a Amazon. Intenta de nuevo más tarde."),
    ("phone_form_missing", "Amazon cambió la pantalla de verificación y no se pudo completar el registro. Intenta más tarde."),
    ("sms_timeout", "No llegó el código por SMS a tiempo. El número quedó sin usar; intenta de nuevo."),
    ("sms_code_rejected", "Amazon no aceptó el código que llegó por SMS. Intenta de nuevo."),
    ("whatsapp_channel", "Amazon pidió el código por WhatsApp, que en este canal no es posible. Intenta de nuevo."),
    ("timeout_total", "Amazon tardó demasiado en responder y se canceló el intento. Intenta de nuevo."),
)


def _humanize_error(message: str) -> str:
    low = (message or "").lower()
    for key, friendly in _FRIENDLY_ERRORS:
        if key.lower() in low:
            return friendly
    if message and len(message) <= 300 and " " not in message.strip(" _-"):
        return "Amazon respondió algo inesperado. Es posible que la cuenta sí se haya creado: intenta entrar a Amazon con ese correo antes de generar otra."
    return message or "Error desconocido"


async def _run_status(cb, text: str) -> None:
    try:
        await cb(text)
    except Exception:
        logger.debug("Fallo al reportar el avance", exc_info=True)


async def create_amazon_account(
    user_id: int,
    country: str,
    email: Optional[str] = None,
    email_mode: str = "own",
    notify_callback: Optional[Callable[[], Coroutine]] = None,
    on_email_callback: Optional[Callable[[str], Coroutine]] = None,
    on_result_callback: Optional[Callable[[dict], Coroutine]] = None,
    on_status_callback: Optional[Callable[[str], Coroutine]] = None,
) -> dict:
    """Crea una cuenta Amazon de forma async usando el generador adaptado.

    ``email_mode`` puede ser:
      - ``own``:  el usuario proporciona el correo y el OTP manualmente.
      - ``temp``: se genera un correo temporal con EmailApi y se lee el OTP
                  automáticamente por polling.
    """
    country = country.upper()
    if country not in _AVAILABLE_COUNTRIES:
        error = {"status": False, "message": f"País no soportado: {country}. Usa: {', '.join(_AVAILABLE_COUNTRIES)}"}
        if on_result_callback:
            try:
                await on_result_callback(error)
            except Exception:
                pass
        return error

    loop = asyncio.get_running_loop()

    mail = None
    addr_info = None
    if email_mode == "temp":
        mail = TempMail()
        addr_info = await loop.run_in_executor(None, mail.getAddr)
        email = addr_info.get("number") or addr_info.get("activationId") or ""
        mail.address = email
        if on_email_callback:
            try:
                await on_email_callback(email)
            except Exception:
                logger.exception("Error en on_email_callback")
    else:
        if not email:
            error = {"status": False, "message": "Debes proporcionar un correo en modo manual."}
            if on_result_callback:
                try:
                    await on_result_callback(error)
                except Exception:
                    pass
            return error
        mail = TelegramMail(email, loop=loop, notify_callback=notify_callback)
        register_pending(user_id, mail)

    proxy = None
    creator = Creator(country=country, proxy=proxy, mail=mail)
    # Tope global: si se agota el tiempo total se deja de reintentar, para que
    # el usuario no espere indefinidamente.
    creator.deadline = _time.time() + 300
    if on_status_callback:
        def _status(text: str) -> None:
            """El generador corre en un hilo: se salta al loop del bot."""
            try:
                loop.call_soon_threadsafe(
                    lambda t=text: asyncio.ensure_future(_run_status(on_status_callback, t))
                )
            except Exception:
                logger.debug("No se pudo enviar el avance del flujo", exc_info=True)
        creator.on_status = _status
    # Permitir un par de reintentos con cambio de proxy/identidad para no quemar IPs.
    creator.maxRetries = 2
    if email_mode == "temp" and addr_info:
        creator.phone = addr_info

    t0 = _time.time()

    def _run() -> dict:
        try:
            return creator.run()
        except Exception as exc:
            logger.exception("Error en creación de cuenta Amazon")
            return {"status": False, "message": str(exc)}

    try:
        result = await asyncio.wait_for(loop.run_in_executor(None, _run), timeout=600)
    except asyncio.TimeoutError:
        result = {"status": False, "message": "Se agotó el tiempo (10 min). ¿No llegó el OTP?"}
        try:
            cu = getattr(creator, "user", None)
            cp = getattr(creator, "phone", None) or {}
            result["profile"] = {
                "name": getattr(cu, "name", ""),
                "email": (cp.get("number", "") if isinstance(cp, dict) else "") or email or "",
                "password": getattr(cu, "password", ""),
            }
            result["registeredCountry"] = getattr(creator, "country", country)
            result["domain"] = getattr(creator, "domain", "")
        except Exception:
            pass
    finally:
        remove_pending(user_id)
        _running_creations.pop(user_id, None)

    result["time"] = round(_time.time() - t0, 1)

    if result.get("status"):
        try:
            path = _save_cookie_file(result)
            result["saved_to"] = str(path)
        except Exception as exc:
            logger.warning("No se pudo guardar archivo de cookies: %s", exc)
    else:
        # Respaldo de credenciales aunque falle: la cuenta pudo quedar creada.
        try:
            mail_token = ""
            try:
                mail_token = getattr(mail, "token", "") or ""
            except Exception:
                pass
            apath = _save_attempt_file(result, user_id, email_mode=email_mode, mail_token=mail_token)
            if apath:
                result["attempt_saved_to"] = str(apath)
                logger.info("Respaldo de intento guardado en %s", apath)
        except Exception as exc:
            logger.warning("No se pudo guardar respaldo de intento: %s", exc)
        result["tech_error"] = result.get("message", "")
        result["message"] = _humanize_error(result.get("message", ""))

    if on_result_callback:
        try:
            await on_result_callback(result)
        except Exception:
            logger.exception("Error en on_result_callback")

    return result


def start_amazon_account_creation(
    user_id: int,
    country: str,
    email: Optional[str] = None,
    email_mode: str = "own",
    notify_callback: Optional[Callable[[], Coroutine]] = None,
    on_email_callback: Optional[Callable[[str], Coroutine]] = None,
    on_result_callback: Optional[Callable[[dict], Coroutine]] = None,
    on_status_callback: Optional[Callable[[str], Coroutine]] = None,
) -> asyncio.Task:
    """Inicia la creación de cuenta en segundo plano y devuelve la tarea."""
    task = asyncio.create_task(
        create_amazon_account(
            user_id=user_id,
            country=country,
            email=email,
            email_mode=email_mode,
            notify_callback=notify_callback,
            on_email_callback=on_email_callback,
            on_result_callback=on_result_callback,
            on_status_callback=on_status_callback,
        )
    )
    _running_creations[user_id] = task
    return task


def submit_otp(user_id: int, code: str) -> bool:
    """Entrega el OTP al hilo de creación que está esperando."""
    from bot.services.amazon_gen.telegram_mail import get_pending

    mail = get_pending(user_id)
    if not mail:
        return False
    mail.set_code(code)
    return True


async def save_amazon_account_to_db(user_id: int, result: dict) -> None:
    """Persiste una cuenta Amazon generada en la base de datos."""
    p = result.get("profile", {})
    async with get_connection() as db:
        await db.execute(
            """
            INSERT INTO amazon_accounts (user_id, country, domain, email, password, name, cookies, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                result.get("registeredCountry", ""),
                result.get("domain", ""),
                p.get("email", ""),
                p.get("password", ""),
                p.get("name", ""),
                result.get("cookies", ""),
                datetime.now().isoformat(),
            ),
        )
        await db.commit()


async def get_user_amazon_accounts(user_id: int, limit: int = 10) -> list[dict]:
    """Devuelve las últimas cuentas Amazon generadas por un usuario."""
    async with get_connection() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM amazon_accounts WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
