import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher, BaseMiddleware
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Message, ReplyParameters
from typing import Any, Awaitable, Callable, Dict

from bot.config import ETERNAL_TARTARO_GROUP_ID, TELEGRAM_BOT_TOKEN
from bot.handlers.start import register_start_handlers
from bot.handlers.admin import register_admin_handlers
from bot.handlers.gates import register_gate_handlers
from bot.handlers.ghost import register_ghost_handlers
# Gates desactivados (2026-10-21): /kh + /khmass (NMI).
# from bot.handlers.kh import register_kh_handlers
# Radamantis desactivado (2026-11-02): /rd + /rdmass fuera del panel y del bot.
# from bot.handlers.rd import register_rd_handlers
from bot.handlers.md import register_md_handlers
# Iris desactivado (2026-10-23): gate sin checkout real por HTTP.
# No mapear este gate aunque el handler esté presente en el repo.
# from bot.handlers.iris import register_iris_handlers
from bot.handlers.amazon_creator import register_amazon_creator_handlers
from bot.handlers.extra import register_extra_handlers
from bot.handlers.tools import register_tools_handlers
from bot.handlers.rfc import register_rfc_handlers
from bot.handlers.sms import register_sms_handlers
from bot.handlers.correo import register_correo_handlers
from bot.handlers.telcel import register_telcel_handlers
# Atlas desactivado (2026-11-02): /tl + /tlmass fuera del panel y del bot.
# from bot.handlers.atlas import register_atlas_handlers
from bot.handlers.ai_image import register_ai_handlers
from bot.services.database import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


class AutoReplyMiddleware(BaseMiddleware):
    """Parche global tipo ALFA-1 (install_global_reply_to_patch): toda
    respuesta a un comando/mensaje cita el mensaje origen (el bloque
    "notion de reply" de la captura) salvo que el handler ya defina su
    propio reply_parameters o reply_to_message_id.

    Implementación aiogram: Message.answer() devuelve un SendMessage ya
    vinculado al bot (.as_(bot)) que al hacer await se envía directo sin
    pasar por middlewares. Por eso se parchea Message.answer: si el caller
    no definió reply, se inyecta ReplyParameters(message_id=<origen>).
    Igual que ALFA-1, que parchea Message.reply_text en PTB."""

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message) or not event.message_id:
            return await handler(event, data)
        origin_id = event.message_id
        origin_answer = Message.answer

        def _patched_answer(
            self: Message, text: str, *args: Any, **kwargs: Any
        ):
            if (
                kwargs.get("reply_parameters") is None
                and kwargs.get("reply_to_message_id") is None
            ):
                kwargs["reply_parameters"] = ReplyParameters(
                    message_id=origin_id,
                    allow_sending_without_reply=True,
                )
            return origin_answer(self, text, *args, **kwargs)

        Message.answer = _patched_answer  # type: ignore[method-assign]
        try:
            return await handler(event, data)
        finally:
            Message.answer = origin_answer


class GroupSilenceMiddleware(BaseMiddleware):
    """Silencio total en Eternal Tartaro: ningún handler responde a
    mensajes ni callbacks que lleguen de ese grupo. Los envíos del
    panel /batman (message.broadcast, difusión) salen por bot.send_*,
    no pasan por este middleware, así que la difusión sigue funcionando.

    Callbacks: sí se hace callback.answer() para que el botón no quede
    con spinner, pero sin mostrar nada ni tocar el panel.
    """

    def __init__(self, group_id: int):
        self.group_id = group_id

    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any],
    ) -> Any:
        chat = getattr(event, "chat", None)
        if chat is not None and chat.id == self.group_id:
            if isinstance(event, CallbackQuery):
                try:
                    await event.answer()
                except Exception:
                    pass
            return None
        return await handler(event, data)


class CancelStaleStateMiddleware(BaseMiddleware):
    """Si el usuario envía un comando a mitad de un flujo (p. ej. esperando
    un ID para asignar plan), se cancela el flujo anterior en vez de tratar
    el comando como respuesta del flujo."""

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any],
    ) -> Any:
        state = data.get("state")
        if (
            isinstance(event, Message)
            and event.text
            and event.text.startswith("/")
            and state is not None
        ):
            cmd = event.text.strip().split()[0].split("@")[0].lower()
            if cmd == "/cancel":
                # /cancel tiene su propio handler: se deja pasar.
                return await handler(event, data)
            try:
                current = await state.get_state()
            except Exception:
                current = None
            if current is not None and current.split(":")[0] == "AmazonGenState":
                # Una creación de /gencookie no se puede cancelar: se deja pasar.
                return await handler(event, data)
            if current is not None:
                await state.clear()
                await event.answer(
                    "❌ Operación anterior cancelada.\n\n"
                    "Vuelve a enviar el comando.",
                    parse_mode=ParseMode.HTML,
                )
                return None
        return await handler(event, data)


def create_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    # Silencio total en Eternal Tartaro: outer middleware, corre antes que
    # cualquier router (incluidos los de admin) y mata mensajes + callbacks
    # que lleguen de ese grupo. La difusión del panel /batman sale por
    # bot.send_* directo y no pasa por aquí, así que sigue funcionando.
    dp.message.outer_middleware(GroupSilenceMiddleware(ETERNAL_TARTARO_GROUP_ID))
    dp.callback_query.outer_middleware(GroupSilenceMiddleware(ETERNAL_TARTARO_GROUP_ID))
    dp.message.middleware(CancelStaleStateMiddleware())
    # AutoReply parchea Message.answer durante el manejo del update
    # (igual que ALFA-1 parchea Message.reply_text en PTB).
    dp.message.middleware(AutoReplyMiddleware())
    register_admin_handlers(dp)
    register_gate_handlers(dp)
    register_ghost_handlers(dp)
    # /kh + /khmass siguen desactivados.
    # register_kh_handlers(dp)
    # /rd + /rdmass desactivados (2026-11-02).
    # register_rd_handlers(dp)
    register_md_handlers(dp)
    # register_iris_handlers(dp)  # Iris desactivado
    register_amazon_creator_handlers(dp)
    register_extra_handlers(dp)
    register_tools_handlers(dp)
    register_rfc_handlers(dp)
    register_sms_handlers(dp)
    register_correo_handlers(dp)
    register_telcel_handlers(dp)
    # /tl + /tlmass desactivados (2026-11-02).
    # register_atlas_handlers(dp)
    register_ai_handlers(dp)
    register_start_handlers(dp)
    return dp


async def main():
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN no está configurado. Revisa tu archivo .env")
        sys.exit(1)

    bot = Bot(
        token=TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = create_dispatcher()

    logger.info("Inicializando base de datos SQLite...")
    await init_db()
    logger.info("Base de datos lista")

    # Backup diario + purga de membresías vencidas (Fase 5).
    try:
        from bot.services.database import maybe_backup_db

        await maybe_backup_db()
    except Exception:
        logger.warning("No se pudo crear backup de BD", exc_info=True)

    try:
        from bot.services.memberships import purge_expired_memberships

        n = await purge_expired_memberships()
        if n:
            logger.info("Membresías vencidas purgadas: %d", n)
    except Exception:
        logger.warning("No se pudo purgar membresías vencidas", exc_info=True)

    try:
        from bot.services.sms import ensure_schema as sms_ensure_schema

        await sms_ensure_schema()
    except Exception:
        logger.warning("No se pudo inicializar el esquema SMS", exc_info=True)

    try:
        from bot.services.correo import ensure_schema as correo_ensure_schema

        await correo_ensure_schema()
    except Exception:
        logger.warning("No se pudo inicializar el esquema correo", exc_info=True)

    try:
        from bot.services.ai_image_generator import initialize as init_ai_image

        await init_ai_image()
        logger.info("Generador de imágenes por IA inicializado")
    except Exception:
        logger.warning("No se pudo inicializar el generador de imágenes", exc_info=True)

    logger.info("Bot iniciado en modo polling")
    try:
        try:
            import asyncio as _aio

            from bot.services.sms import reconcile_orphans as _sms_reconcile
            from bot.services.correo import purge_expired as _correo_purge

            async def _sms_reconcile_delayed() -> None:
                await _aio.sleep(30)
                try:
                    n = await _sms_reconcile()
                    if n:
                        logger.info("SMS: %d pedidos huérfanos reconciliados", n)
                except Exception:
                    logger.warning("SMS: fallo reconciliación", exc_info=True)

            _aio.create_task(_sms_reconcile_delayed())

            async def _correo_purge_loop() -> None:
                while True:
                    await _aio.sleep(3600)
                    try:
                        n = await _correo_purge()
                        if n:
                            logger.info("Correo: %d cuentas expiradas purgadas", n)
                    except Exception:
                        logger.warning("Correo: fallo purga", exc_info=True)

            _aio.create_task(_correo_purge_loop())
        except Exception:
            logger.warning("SMS: no se pudo programar reconciliación", exc_info=True)
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        # Cerrar el Chromium del gate de Amazon (si quedó abierto) para no
        # dejar ~400 MB de RAM tomados después de que el proceso muere.
        try:
            from bot.gates.amazon_playwright import close_browser

            await close_browser()
        except Exception:
            logger.debug("No se pudo cerrar el navegador de Amazon", exc_info=True)
        # Cerrar el pool dedicado del gate Amazon (Fase 4).
        try:
            from bot.gates.amazon import shutdown_executors

            shutdown_executors()
        except Exception:
            logger.debug("No se pudo cerrar el executor de Amazon", exc_info=True)
        # Cerrar el generador de imágenes por IA
        try:
            from bot.services.ai_image_generator import shutdown as shutdown_ai_image

            await shutdown_ai_image()
        except Exception:
            logger.debug("No se pudo cerrar el generador de imágenes", exc_info=True)
        logger.info("Bot detenido")


if __name__ == "__main__":
    asyncio.run(main())
