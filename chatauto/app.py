from __future__ import annotations

import asyncio
import logging
import signal

from aiohttp import web
from telegram import Update
from telegram.ext import (
    Application,
    BusinessConnectionHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from chatauto.config import Settings, get_settings
from chatauto.gemini import GeminiReplier
from chatauto.handlers import on_business_connection, on_business_message, on_direct_message
from chatauto.store import Store

logging.basicConfig(
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

ALLOWED_UPDATES = [
    "business_connection",
    "business_message",
    "edited_business_message",
    "message",
]


def build_application(settings: Settings, *, use_updater: bool) -> Application:
    store = Store(settings.db_path)
    gemini = GeminiReplier(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        persona=settings.load_persona(),
    )

    builder = Application.builder().token(settings.bot_token)
    if not use_updater:
        builder = builder.updater(None)
    application = builder.build()

    application.bot_data["settings"] = settings
    application.bot_data["store"] = store
    application.bot_data["gemini"] = gemini

    application.add_handler(BusinessConnectionHandler(on_business_connection))
    application.add_handler(
        MessageHandler(
            filters.UpdateType.BUSINESS_MESSAGES & filters.TEXT,
            on_business_message,
        )
    )
    application.add_handler(CommandHandler("start", on_direct_message))
    application.add_handler(
        MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, on_direct_message)
    )
    return application


async def _run_polling(settings: Settings) -> None:
    application = build_application(settings, use_updater=True)
    store: Store = application.bot_data["store"]

    await store.connect()
    await application.initialize()
    await application.bot.delete_webhook(drop_pending_updates=True)
    await application.start()
    assert application.updater is not None
    await application.updater.start_polling(allowed_updates=ALLOWED_UPDATES)

    me = await application.bot.get_me()
    logger.info("Polling as @%s — connect Business chatbot, then test from another account", me.username)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    try:
        await stop.wait()
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()
        await store.close()


async def _run_webhook(settings: Settings) -> None:
    application = build_application(settings, use_updater=False)
    store: Store = application.bot_data["store"]

    await store.connect()
    await application.initialize()
    await application.start()
    await application.bot.set_webhook(
        url=settings.webhook_full_url,
        allowed_updates=ALLOWED_UPDATES,
        drop_pending_updates=True,
        secret_token=settings.webhook_secret,
    )
    me = await application.bot.get_me()
    logger.info("Webhook set for @%s → %s", me.username, settings.webhook_full_url)

    async def health(_: web.Request) -> web.Response:
        return web.Response(text="ok")

    async def telegram(request: web.Request) -> web.Response:
        secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if secret != settings.webhook_secret:
            return web.Response(status=403, text="forbidden")
        data = await request.json()
        update = Update.de_json(data, application.bot)
        await application.update_queue.put(update)
        return web.Response(text="ok")

    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/", health)
    app.router.add_post(settings.webhook_path, telegram)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", settings.port)
    await site.start()
    logger.info("Listening on 0.0.0.0:%s", settings.port)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    try:
        await stop.wait()
    finally:
        await application.stop()
        await application.shutdown()
        await store.close()
        await runner.cleanup()


def main() -> None:
    settings = get_settings()
    if settings.mode == "polling":
        asyncio.run(_run_polling(settings))
    else:
        asyncio.run(_run_webhook(settings))


if __name__ == "__main__":
    main()
