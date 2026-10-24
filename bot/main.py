import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher, BaseMiddleware
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message
from typing import Any, Awaitable, Callable, Dict

from bot.config import TELEGRAM_BOT_TOKEN
from bot.handlers.start import register_start_handlers
from bot.handlers.admin import register_admin_handlers
from bot.handlers.gates import register_gate_handlers
from bot.handlers.ghost import register_ghost_handlers
# Gates desactivados (2026-10-21): /kh + /khmass (NMI).
# (Radamantis) ahora usa el motor radamanthysV2 (/rd + /rdmass).
# from bot.handlers.kh import register_kh_handlers
from bot.handlers.rd import register_rd_handlers
# Iris desactivado (2026-10-23): gate sin checkout real por HTTP.
# No mapear este gate aunque el handler esté presente en el repo.
# from bot.handlers.iris import register_iris_handlers
from bot.handlers.amazon_creator import register_amazon_creator_handlers
from bot.handlers.extra import register_extra_handlers
from bot.handlers.atlas import register_atlas_handlers
from bot.services.database import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


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
    dp.message.middleware(CancelStaleStateMiddleware())
    register_admin_handlers(dp)
    register_gate_handlers(dp)
    register_ghost_handlers(dp)
    # /kh + /khmass siguen desactivados.
    # register_kh_handlers(dp)
    register_rd_handlers(dp)
    # register_iris_handlers(dp)  # Iris desactivado
    register_amazon_creator_handlers(dp)
    register_extra_handlers(dp)
    register_atlas_handlers(dp)
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

    logger.info("Bot iniciado en modo polling")
    try:
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
        logger.info("Bot detenido")


if __name__ == "__main__":
    asyncio.run(main())
