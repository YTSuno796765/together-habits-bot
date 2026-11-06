"""One job that runs every minute and sends every timed notification exactly once.

Each notification gets a key in `sent_log` (e.g. "digest:<user>:<date>"), so restarts
or a slow minute never cause duplicates.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from html import escape
from typing import Any
from zoneinfo import ZoneInfo

from telegram import InputFile

from together_habits import logic, reports
from together_habits.db import Storage
from together_habits.handlers import Handlers
from together_habits.texts import KIND_SCOPE

log = logging.getLogger(__name__)

HABIT_REMINDER_WINDOW = timedelta(minutes=60)
PLAN_LEAD_TIME = timedelta(minutes=60)
WEEKLY_REPORT_HOUR = 20
WEEKLY_EXPORT_HOUR = 21
ADMIN_BACKUP_HOUR = 3


def _at(day: date, hhmm: str, tz: ZoneInfo) -> datetime:
    hours, minutes = map(int, hhmm.split(":"))
    return datetime.combine(day, time(hours, minutes), tzinfo=tz)


def week_key(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


class Scheduler:
    def __init__(
        self, storage: Storage, handlers: Handlers, tz: ZoneInfo, admin_id: int | None = None
    ) -> None:
        self.db = storage
        self.handlers = handlers
        self.tz = tz
        self.admin_id = admin_id

    async def job(self, context: Any) -> None:
        """python-telegram-bot JobQueue callback."""
        await self.tick(context.bot)

    async def tick(self, bot: Any, now: datetime | None = None) -> None:
        now = now or datetime.now(self.tz)
        for step in (
            self.habit_hour_reminders,
            self.habit_time_reminders,
            self.plan_reminders,
            self.morning_digests,
            self.weekly_reports,
            self.weekly_exports,
            self.finalize_challenges,
            self.focus_sessions,
            self.admin_backup,
        ):
            try:
                await step(bot, now)
            except Exception:  # one broken step must not block the others
                log.exception("Scheduler step %s failed", step.__name__)

    # ---- habits ----------------------------------------------------------------

    async def habit_hour_reminders(self, bot: Any, now: datetime) -> None:
        today = now.date()
        for user_id in self.db.users_with_reminder(now.hour):
            pending = self.handlers.pending_count(user_id, today)
            if pending and self.db.mark_sent(f"rem:{user_id}:{today}"):
                await self.handlers.send(
                    bot,
                    user_id,
                    f"⏰ Осталось отметить привычек сегодня: {pending}. Не теряй серию! 🔥",
                    self.handlers.app_markup(),
                )

    def _section_hidden(self, user_id: int, habit: Any) -> bool:
        if habit.pair_id is None:
            return False
        pair = self.db.get_pair_by_id(habit.pair_id)
        if pair is None:
            return True
        return self.db.mode_settings(user_id)[KIND_SCOPE[pair.kind]][1]

    async def habit_time_reminders(self, bot: Any, now: datetime) -> None:
        """A habit with its own time ("Медитация в 7:00") reminds members who haven't done it."""
        today = now.date()
        for habit in self.db.habits_with_reminder_time():
            if habit.is_avoid or not habit.remind_at:
                continue
            start = _at(today, habit.remind_at, self.tz)
            if not start <= now < start + HABIT_REMINDER_WINDOW:
                continue
            checkins = self.db.checkins(habit.id)
            for user_id in self.db.habit_members(habit):
                mine = {d for d, users in checkins.items() if user_id in users}
                if today in mine or not logic.due_today(habit.schedule, today, mine, habit.weekly_target):
                    continue
                if self._section_hidden(user_id, habit):
                    continue
                if self.db.mark_sent(f"hrem:{habit.id}:{user_id}:{today}"):
                    await self.handlers.send(
                        bot,
                        user_id,
                        f"⏰ Время: {habit.emoji} <b>{escape(habit.title)}</b>",
                        self.handlers.app_markup(),
                    )

    # ---- plans -------------------------------------------------------------------

    async def plan_reminders(self, bot: Any, now: datetime) -> None:
        """An hour before an accepted plan with a time, remind both people."""
        today = now.date()
        for plan in self.db.accepted_plans():
            if not plan.time or not logic.plan_occurs(plan.day, plan.repeat, today):
                continue
            starts = _at(today, plan.time, self.tz)
            if not starts - PLAN_LEAD_TIME <= now < starts:
                continue
            if not self.db.mark_sent(f"plan:{plan.id}:{today}"):
                continue
            pair = self.db.get_pair_by_id(plan.pair_id)
            if pair is None:
                continue
            minutes = max(1, int((starts - now).total_seconds() // 60))
            when = "через час" if minutes >= 55 else f"через {minutes} мин"
            for user_id in pair.members:
                partner = pair.other(user_id)
                who = f" с {escape(self.db.user_name(partner))}" if partner else ""
                await self.handlers.send(
                    bot,
                    user_id,
                    f"⏰ {when.capitalize()}{who}: {plan.emoji} <b>{escape(plan.title)}</b> в {plan.time}",
                )

    # ---- digests, reports, exports --------------------------------------------------

    async def morning_digests(self, bot: Any, now: datetime) -> None:
        today = now.date()
        for user_id in self.db.users_where("digest_hour = ?", now.hour):
            text = reports.morning_digest(self.handlers, user_id, today)
            if text and self.db.mark_sent(f"digest:{user_id}:{today}"):
                await self.handlers.send(bot, user_id, text, self.handlers.app_markup())

    async def weekly_reports(self, bot: Any, now: datetime) -> None:
        if now.weekday() != 6 or now.hour < WEEKLY_REPORT_HOUR:
            return
        today = now.date()
        for user_id in self.db.users_where("weekly_report = 1"):
            key = f"week:{user_id}:{week_key(today)}"
            text = reports.weekly_report(self.handlers, user_id, today)
            if text and self.db.mark_sent(key):
                await self.handlers.send(bot, user_id, text, self.handlers.app_markup())

    async def weekly_exports(self, bot: Any, now: datetime) -> None:
        if now.weekday() != 6 or now.hour < WEEKLY_EXPORT_HOUR:
            return
        today = now.date()
        for user_id in self.db.users_where("weekly_export = 1"):
            if self.db.mark_sent(f"export:{user_id}:{week_key(today)}"):
                await self.send_export(bot, user_id, today, caption="🗂 Еженедельная копия твоих данных")

    async def send_export(self, bot: Any, user_id: int, today: date, caption: str) -> None:
        data = reports.export_csv(self.handlers, user_id, today)
        await bot.send_document(
            user_id,
            document=InputFile(data, filename=f"habits-{today.isoformat()}.csv"),
            caption=caption,
        )

    async def admin_backup(self, bot: Any, now: datetime) -> None:
        """Optional: the whole database to the bot owner once a week (HABITS_ADMIN_ID)."""
        if self.admin_id is None or now.weekday() != 6 or now.hour < ADMIN_BACKUP_HOUR:
            return
        today = now.date()
        if self.db.mark_sent(f"backup:{week_key(today)}"):
            await bot.send_document(
                self.admin_id,
                document=InputFile(self.db.backup_bytes(), filename=f"habits-backup-{today.isoformat()}.db"),
                caption="💾 Резервная копия базы",
            )

    # ---- challenges & focus ------------------------------------------------------------

    async def finalize_challenges(self, bot: Any, now: datetime) -> None:
        await self.handlers.finalize_challenges(bot, now.date())

    async def focus_sessions(self, bot: Any, now: datetime) -> None:
        for session in self.db.due_focus_sessions(now):
            if self.db.mark_sent(f"focus:{session['id']}"):
                await self.handlers.send(
                    bot,
                    session["user_id"],
                    f"⏱ Помодоро ({session['minutes']} мин) закончено! Отметь сессию и сделай перерыв ☕",
                    self.handlers.app_markup(),
                )
