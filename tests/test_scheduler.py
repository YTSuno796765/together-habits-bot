"""Timed notifications: reminders, plan alerts, digests, weekly reports, exports, backups."""

import asyncio
import csv
import io
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from together_habits import logic
from together_habits.db import Storage
from together_habits.handlers import Handlers
from together_habits.scheduler import Scheduler
from tests.test_webapp import make_pair, run

TZ = ZoneInfo("UTC")
SUNDAY = date(2026, 11, 8)


class Bot:
    def __init__(self):
        self.sent = []
        self.documents = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))

    async def send_document(self, chat_id, document, caption=None, **kwargs):
        self.documents.append((chat_id, document.filename, document.input_file_content, caption))


def setup(admin_id=None):
    db = Storage(":memory:")
    db.upsert_user(1, "Аня")
    db.upsert_user(2, "Боря")
    handlers = Handlers(db, TZ)
    return db, handlers, Scheduler(db, handlers, TZ, admin_id), Bot()


def at(day, hour, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TZ)


def tick(scheduler, bot, when):
    asyncio.run(scheduler.tick(bot, when))


def test_hour_reminder_once_and_only_when_pending():
    db, handlers, scheduler, bot = setup()
    day = SUNDAY - timedelta(days=2)
    habit = db.add_habit("Чтение", owner_id=1, created=day - timedelta(days=5))
    db.set_reminder(1, 21)
    tick(scheduler, bot, at(day, 20, 59))
    assert bot.sent == []
    tick(scheduler, bot, at(day, 21, 0))
    tick(scheduler, bot, at(day, 21, 1))  # same hour again: no duplicate
    assert len(bot.sent) == 1 and "Осталось отметить привычек сегодня: 1" in bot.sent[0][1]
    next_day = day + timedelta(days=1)
    db.toggle_checkin(habit.id, 1, next_day)
    tick(scheduler, bot, at(next_day, 21, 0))
    assert len(bot.sent) == 1  # nothing pending


def test_habit_time_reminder_window_and_done_check():
    db, handlers, scheduler, bot = setup()
    day = SUNDAY - timedelta(days=3)
    pair = db.join_pair(db.create_invite(1, "friend").invite_code, 2)
    habit = db.add_habit("Зал", pair_id=pair.id, remind_at="07:30", created=day)
    db.toggle_checkin(habit.id, 2, day)  # Боря already did it
    tick(scheduler, bot, at(day, 7, 29))
    assert bot.sent == []
    tick(scheduler, bot, at(day, 7, 30))
    tick(scheduler, bot, at(day, 7, 45))
    assert [chat for chat, _ in bot.sent] == [1] and "Время" in bot.sent[0][1]
    tick(scheduler, bot, at(day + timedelta(days=1), 9, 0))  # window (1h) passed
    assert len(bot.sent) == 1
    # Hidden section → no reminder.
    db.set_mode_settings(1, "f", hidden=True)
    tick(scheduler, bot, at(day + timedelta(days=2), 7, 31))
    assert [chat for chat, _ in bot.sent].count(1) == 1


def test_plan_reminder_an_hour_before_including_weekly():
    db, handlers, scheduler, bot = setup()
    day = SUNDAY - timedelta(days=6)  # Monday
    pair = db.join_pair(db.create_invite(1, "couple").invite_code, 2)
    plan = db.add_plan(pair.id, 1, "Ужин", day, emoji="🍽", time="19:30", repeat="weekly")
    tick(scheduler, bot, at(day, 18, 40))
    assert bot.sent == []  # still pending
    db.set_plan_status(plan.id, "accepted")
    tick(scheduler, bot, at(day, 18, 20))
    assert bot.sent == []  # too early
    tick(scheduler, bot, at(day, 18, 40))
    tick(scheduler, bot, at(day, 18, 50))
    assert sorted(chat for chat, _ in bot.sent) == [1, 2] and "Ужин" in bot.sent[0][1]
    tick(scheduler, bot, at(day + timedelta(days=7), 18, 31))  # next week's occurrence
    assert len(bot.sent) == 4
    tick(scheduler, bot, at(day + timedelta(days=8), 18, 31))  # not a plan day
    assert len(bot.sent) == 4


def test_morning_digest():
    db, handlers, scheduler, bot = setup()
    day = SUNDAY - timedelta(days=1)
    db.add_habit("Чтение", "📖", owner_id=1, created=day)
    db.add_habit("Курить", owner_id=1, created=day, kind="avoid")
    pair = db.join_pair(db.create_invite(1, "couple").invite_code, 2)
    plan = db.add_plan(pair.id, 2, "Кино", day, emoji="🎬", time="21:00")
    db.set_plan_status(plan.id, "accepted")
    db.set_notification_settings(1, digest_hour=8)
    tick(scheduler, bot, at(day, 8, 3))
    tick(scheduler, bot, at(day, 8, 4))
    assert len(bot.sent) == 1
    text = bot.sent[0][1]
    assert "Доброе утро, Аня" in text and "📖 Чтение" in text and "Курить" not in text
    assert "21:00 🎬 Кино — с Боря" in text
    # Users without anything to do get no digest.
    db.set_notification_settings(2, digest_hour=8)
    tick(scheduler, bot, at(day + timedelta(days=1), 8, 0))
    assert all(chat == 1 for chat, _ in bot.sent)


def test_weekly_report_on_sunday_evening():
    db, handlers, scheduler, bot = setup()
    habit = db.add_habit("Зал", "🏋️", owner_id=1, created=SUNDAY - timedelta(days=20))
    for offset in range(0, 7):
        db.toggle_checkin(habit.id, 1, SUNDAY - timedelta(days=offset))
    db.toggle_checkin(habit.id, 1, SUNDAY - timedelta(days=8))
    tick(scheduler, bot, at(SUNDAY - timedelta(days=1), 21, 0))  # Saturday
    tick(scheduler, bot, at(SUNDAY, 19, 59))
    assert bot.sent == []
    tick(scheduler, bot, at(SUNDAY, 20, 0))
    tick(scheduler, bot, at(SUNDAY, 22, 0))
    assert len(bot.sent) == 1
    text = bot.sent[0][1]
    assert "Итоги недели" in text and "100%" in text and "+86% к прошлой" in text
    db.set_notification_settings(1, weekly_report=False)
    tick(scheduler, bot, at(SUNDAY + timedelta(days=7), 20, 0))
    assert len(bot.sent) == 1


def test_weekly_export_and_admin_backup():
    db, handlers, scheduler, bot = setup(admin_id=999)
    habit = db.add_habit("Вода", owner_id=1, target_amount=8, unit="стак.", created=SUNDAY)
    db.set_amount(habit, 1, SUNDAY, 8)
    db.set_note(habit.id, 1, SUNDAY, "легко")
    db.set_notification_settings(1, weekly_export=True)
    tick(scheduler, bot, at(SUNDAY, 21, 5))
    tick(scheduler, bot, at(SUNDAY, 22, 5))
    exports = [d for d in bot.documents if d[0] == 1]
    assert len(exports) == 1 and exports[0][1].endswith(".csv")
    rows = list(csv.reader(io.StringIO(exports[0][2].decode("utf-8-sig"))))
    assert rows[0][:3] == ["раздел", "привычка", "тип"]
    assert rows[1][1] == "Вода" and rows[1][5] == "выполнено" and rows[1][6] == "8" and rows[1][7] == "легко"
    backups = [d for d in bot.documents if d[0] == 999]
    assert len(backups) == 1 and backups[0][2][:15] == b"SQLite format 3"


def test_plan_occurrences():
    first = date(2026, 11, 2)  # Monday
    assert logic.plan_occurrences(first, "weekly", date(2026, 11, 10), date(2026, 11, 30)) == [
        date(2026, 11, 16), date(2026, 11, 23), date(2026, 11, 30)
    ]
    assert logic.plan_occurrences(first, None, date(2026, 11, 1), date(2026, 11, 30)) == [first]
    assert logic.plan_occurrences(first, "weekly", date(2026, 10, 1), date(2026, 10, 31)) == []
    assert logic.plan_occurs(first, "weekly", date(2026, 11, 9)) and not logic.plan_occurs(first, "weekly", date(2026, 11, 10))


def test_weekly_plans_and_notification_api():
    async def body(env):
        await make_pair(env, "f")
        day = date.today() + timedelta(days=1)
        status, res = await env.call(
            "POST", "/api/plans", json={"scope": "f", "title": "Зал", "date": day.isoformat(), "repeat": "weekly"}
        )
        assert status == 200
        assert "каждую неделю" in env.bot.sent[-1][1]
        plans = (await env.call("GET", "/api/plans?month=" + day.strftime("%Y-%m")))[1]["plans"]
        dates = [p["date"] for p in plans if p["id"] == res["id"]]
        assert len(dates) >= 4 and all(p["repeat"] == "weekly" for p in plans)

        settings = (await env.call("GET", "/api/notifications"))[1]
        assert settings == {"reminderHour": None, "digestHour": None, "weeklyReport": True, "weeklyExport": False}
        status, settings = await env.call("POST", "/api/notifications", json={"digestHour": 8, "weeklyExport": True})
        assert status == 200 and settings["digestHour"] == 8 and settings["weeklyExport"] is True
        assert (await env.call("POST", "/api/notifications", json={"digestHour": 30}))[0] == 400
        assert (await env.call("POST", "/api/notifications", json={"weeklyReport": "yes"}))[0] == 400
        status, settings = await env.call("POST", "/api/notifications", json={"digestHour": None})
        assert settings["digestHour"] is None

    run(body)
