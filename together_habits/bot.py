from __future__ import annotations

from datetime import datetime, timedelta

from telegram import Update
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


def build_application(config: BotConfig) -> Application:
    storage = Storage(config.db_path)
    handlers = Handlers(storage, config.tz)

    app = Application.builder().token(config.telegram_bot_token).build()
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
