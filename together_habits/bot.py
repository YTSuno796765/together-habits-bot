from __future__ import annotations

import logging
from functools import partial
from typing import Any

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
from together_habits.scheduler import Scheduler
from together_habits.webapp import WebApi

log = logging.getLogger(__name__)


async def export_cmd(update: Update, context: Any, handlers: Handlers, scheduler: Scheduler) -> None:
    """/export — send the user's data as a CSV file."""
    user_id = handlers._register(update)
    await scheduler.send_export(context.bot, user_id, handlers.today(), caption="🗂 Твои данные (CSV)")


def build_application(config: BotConfig) -> Application:
    storage = Storage(config.db_path)
    handlers = Handlers(storage, config.tz, config.webapp_url)
    scheduler = Scheduler(storage, handlers, config.tz, config.admin_id)
    api = WebApi(storage, config.tz, config.telegram_bot_token, handlers, scheduler=scheduler)
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
    app.add_handler(CommandHandler("guide", handlers.guide_cmd))
    app.add_handler(CommandHandler("today", handlers.today_cmd))
    app.add_handler(CommandHandler("mood", handlers.mood_cmd))
    app.add_handler(CommandHandler("export", partial(export_cmd, handlers=handlers, scheduler=scheduler)))
    app.add_handler(CallbackQueryHandler(handlers.on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.on_text))

    if app.job_queue is not None:
        # One job a minute sends every timed notification (deduplicated in the database).
        app.job_queue.run_repeating(scheduler.job, interval=60, first=10)
    return app


def run_bot(config: BotConfig) -> None:
    build_application(config).run_polling(allowed_updates=Update.ALL_TYPES)
