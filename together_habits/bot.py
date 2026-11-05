from __future__ import annotations

import logging
from datetime import datetime, timedelta

from aiohttp import web
from telegram import MenuButtonWebApp, Update, WebAppInfo
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from together_habits.config import BotConfig
from together_habits.db import Storage
from together_habits.handlers import Handlers
from together_habits.webapp import WebApi

log = logging.getLogger(__name__)


def build_application(config: BotConfig) -> Application:
    storage = Storage(config.db_path)
    handlers = Handlers(storage, config.tz, config.webapp_url)
    api = WebApi(storage, config.tz, config.telegram_bot_token, handlers)
    runner: dict[str, web.AppRunner] = {}

    async def post_init(app: Application) -> None:
        api.bot = app.bot
        if not config.webapp_url:
            return
        web_runner = web.AppRunner(api.build(), access_log=None)
        await web_runner.setup()
        await web.TCPSite(web_runner, config.webapp_host, config.webapp_port).start()
        runner["web"] = web_runner
        log.info("Mini App served on %s:%s", config.webapp_host, config.webapp_port)
        try:
            await app.bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp("Привычки", WebAppInfo(url=config.webapp_url))
            )
        except TelegramError as exc:
            log.warning("Could not set the menu button: %s", exc)

    async def post_shutdown(app: Application) -> None:
        if "web" in runner:
            await runner["web"].cleanup()
        storage.close()

    app = (
        Application.builder()
        .token(config.telegram_bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    app.add_handler(CommandHandler("start", handlers.start))
    app.add_handler(CommandHandler("help", handlers.help))
    app.add_handler(CommandHandler("today", handlers.today_cmd))
    app.add_handler(CommandHandler("mood", handlers.mood_cmd))
    app.add_handler(CallbackQueryHandler(handlers.on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.on_text))

    if app.job_queue is not None:
        now = datetime.now(config.tz)
        next_hour = (now + timedelta(hours=1)).replace(minute=0, second=5, microsecond=0)
        app.job_queue.run_repeating(
            handlers.reminder_job, interval=3600, first=(next_hour - now).total_seconds()
        )
    return app


def run_bot(config: BotConfig) -> None:
    build_application(config).run_polling(allowed_updates=Update.ALL_TYPES)
