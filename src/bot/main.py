import asyncio
import signal

import structlog
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from src.bot.config import Config, load_config
from src.bot.handlers import base_router, chat_router
from src.bot.health import health_handler
from src.bot.logging_config import configure_logging
from src.bot.middlewares import AuthMiddleware
from src.infrastructure.gemini.client import GeminiClient

log = structlog.get_logger()


async def _ping_handler(request: web.Request) -> web.Response:
    return web.Response(text="OK")


def _create_web_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/health", health_handler)
    app.router.add_get("/", _ping_handler)
    return app


async def _retry_get_me(bot: Bot, max_attempts: int = 3) -> None:
    for attempt in range(1, max_attempts + 1):
        try:
            me = await bot.get_me()
            log.info("bot_connected", username=me.username)
            return
        except Exception as e:
            if attempt == max_attempts:
                raise RuntimeError(
                    f"Cannot connect to Telegram API after {max_attempts} attempts: {e}"
                ) from e
            log.warning("telegram_connect_retry", attempt=attempt, error=str(e))
            await asyncio.sleep(2**attempt)


async def _run_webhook(bot: Bot, dp: Dispatcher, config: Config, app: web.Application) -> None:
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path="/webhook")
    setup_application(app, dp, bot=bot)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", config.webhook_port)
    await site.start()

    await bot.set_webhook(f"{config.webhook_url}/webhook")
    log.info("webhook_mode_started", url=config.webhook_url, port=config.webhook_port)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    try:
        await stop_event.wait()
    finally:
        await bot.delete_webhook()
        await runner.cleanup()


async def _run_polling(bot: Bot, dp: Dispatcher, config: Config, app: web.Application) -> None:
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", config.webhook_port)
    await site.start()
    log.info("polling_mode_started", health_port=config.webhook_port)

    try:
        await dp.start_polling(bot, handle_signals=True)
    finally:
        await runner.cleanup()


async def main() -> None:
    configure_logging()
    config = load_config()

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    gemini = GeminiClient(api_key=config.gemini_api_key, model_name=config.gemini_model)

    dp = Dispatcher()
    dp.update.outer_middleware(AuthMiddleware(config.allowed_user_ids))
    dp["gemini"] = gemini
    dp.include_router(base_router)   # command handlers — must come before generic handler
    dp.include_router(chat_router)

    await _retry_get_me(bot)

    app = _create_web_app()

    try:
        if config.webhook_url:
            await _run_webhook(bot, dp, config, app)
        else:
            await _run_polling(bot, dp, config, app)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
