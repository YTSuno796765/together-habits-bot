"""Year heatmap, mood vs habits, pomodoro sessions, top tasks and goals."""

import asyncio
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from together_habits import logic
from together_habits.db import Storage
from together_habits.handlers import Handlers
from together_habits.scheduler import Scheduler
from tests.test_scheduler import Bot
from tests.test_webapp import make_pair, run


def test_mood_effect_and_heat_levels():
    days = [date(2026, 11, 1) + timedelta(days=i) for i in range(10)]
    moods = {d: (5 if i % 2 == 0 else 3) for i, d in enumerate(days)}
    done = {d for i, d in enumerate(days) if i % 2 == 0}
    assert logic.mood_effect(moods, done, days) == (2.0, 5, 5)
    assert logic.mood_effect(moods, done, days[:4]) is None  # too few samples
    assert [logic.heat_level(d, t) for d, t in ((0, 0), (0, 3), (1, 4), (1, 2), (2, 3), (3, 3))] == [0, 0, 1, 2, 3, 4]


def test_stats_heatmap_and_mood_effects():
    async def body(env):
        await make_pair(env, "c")
        hid = (await env.call("POST", "/api/habits", json={"scope": "p", "title": "Зал"}))[1]["id"]
        habit = env.db.get_habit(hid)
        pair = env.db.get_pair(1, "couple")
        today = date.today()
        start = today - timedelta(days=20)
        env.db.conn.execute("UPDATE habits SET created_at = ? WHERE id = ?", (start.isoformat() + " 00:00:00", hid))
        env.db.conn.commit()
        for i in range(20):
            day = start + timedelta(days=i)
            if i % 2 == 0:
                env.db.toggle_checkin(habit.id, 1, day)
            env.db.set_mood(pair.id, 1, day, 5 if i % 2 == 0 else 2)
        stats = (await env.call("GET", "/api/stats?scope=p"))[1]
        assert len(stats["year"]) >= 365 and stats["year"][-1]["date"] == today.isoformat()
        assert date.fromisoformat(stats["year"][0]["date"]).weekday() == 0
        assert {cell["level"] for cell in stats["year"]} >= {0, 4}
        effect = stats["moodEffects"][0]
        assert effect["title"] == "Зал" and effect["diff"] == 3.0

    run(body)


def test_focus_flow_marks_habit_and_scheduler_pings():
    async def body(env):
        await make_pair(env, "f")
        shared = (await env.call("POST", "/api/habits", json={"scope": "f", "title": "Код"}))[1]["id"]
        reading = (await env.call("POST", "/api/habits", json={"scope": "p", "title": "Чтение", "targetAmount": 30, "unit": "мин"}))[1]["id"]
        assert (await env.call("POST", "/api/focus/start", json={"minutes": 0}))[0] == 400
        status, res = await env.call("POST", "/api/focus/start", json={"minutes": 25, "habitId": shared})
        assert status == 200 and res["active"]["minutes"] == 25
        sid = res["active"]["id"]
        # Too early to complete.
        assert (await env.call("POST", f"/api/focus/{sid}/complete", json={}))[0] == 400
        # Fast-forward: the session ended a minute ago.
        past = (datetime.now(ZoneInfo("UTC")) - timedelta(minutes=1)).isoformat()
        env.db.conn.execute("UPDATE focus_sessions SET ends_at = ? WHERE id = ?", (past, sid))
        env.db.conn.commit()
        status, res = await env.call("POST", f"/api/focus/{sid}/complete", json={})
        assert status == 200 and res["marked"] == "⭐ Код" and res["todayMinutes"] == 25 and res["active"] is None
        assert 1 in env.db.checkins(shared).get(date.today(), set())
        assert env.bot.sent[-1][0] == 2  # partner told about the check-in

        # Minutes-based amount habit gets the session minutes.
        sid = (await env.call("POST", "/api/focus/start", json={"minutes": 20, "habitId": reading}))[1]["active"]["id"]
        env.db.conn.execute("UPDATE focus_sessions SET ends_at = ? WHERE id = ?", (past, sid))
        env.db.conn.commit()
        await env.call("POST", f"/api/focus/{sid}/complete", json={})
        assert env.db.amounts(reading)[date.today()][1] == 20

        # Starting a new session cancels a running one; others cannot touch it.
        first = (await env.call("POST", "/api/focus/start", json={"minutes": 5}))[1]["active"]["id"]
        second = (await env.call("POST", "/api/focus/start", json={"minutes": 5}))[1]["active"]["id"]
        assert env.db.get_focus(first)["status"] == "cancelled"
        assert (await env.call("POST", f"/api/focus/{second}/cancel", user=2, json={}))[0] == 404
        assert (await env.call("POST", f"/api/focus/{second}/cancel", json={}))[0] == 200

    run(body)


def test_scheduler_pings_when_focus_ends():
    db = Storage(":memory:")
    db.upsert_user(1, "A")
    tz = ZoneInfo("UTC")
    handlers = Handlers(db, tz)
    scheduler = Scheduler(db, handlers, tz)
    bot = Bot()
    now = datetime(2026, 11, 6, 10, 0, tzinfo=tz)
    db.start_focus(1, 25, now)
    asyncio.run(scheduler.tick(bot, now + timedelta(minutes=24)))
    assert bot.sent == []
    asyncio.run(scheduler.tick(bot, now + timedelta(minutes=25)))
    asyncio.run(scheduler.tick(bot, now + timedelta(minutes=26)))
    assert len(bot.sent) == 1 and "Помодоро" in bot.sent[0][1]


def test_tasks_api():
    async def body(env):
        for title in ("Сдать отчёт", "Позвонить маме"):
            assert (await env.call("POST", "/api/tasks", json={"title": title}))[0] == 200
        tasks = (await env.call("GET", "/api/tasks"))[1]["tasks"]
        assert [t["title"] for t in tasks] == ["Сдать отчёт", "Позвонить маме"]
        assert (await env.call("POST", f"/api/tasks/{tasks[0]['id']}/toggle", json={}))[1]["tasks"][0]["done"]
        assert (await env.call("POST", f"/api/tasks/{tasks[0]['id']}/toggle", user=2, json={}))[0] == 404
        for i in range(3):
            await env.call("POST", "/api/tasks", json={"title": f"t{i}"})
        assert (await env.call("POST", "/api/tasks", json={"title": "шестая"}))[0] == 400
        assert (await env.call("DELETE", f"/api/tasks/{tasks[1]['id']}"))[1]["tasks"][0]["title"] == "Сдать отчёт"

        # Carry over yesterday's unfinished tasks.
        yesterday = date.today() - timedelta(days=1)
        env.db.upsert_user(5, "U5")
        env.db.add_task(5, yesterday, "Недоделанное")
        done_id = env.db.add_task(5, yesterday, "Готово")
        env.db.toggle_task(done_id)
        assert (await env.call("GET", "/api/tasks", user=5))[1]["carryable"] == 1
        tasks = (await env.call("POST", "/api/tasks", user=5, json={"carry": True}))[1]["tasks"]
        assert [t["title"] for t in tasks] == ["Недоделанное"]

    run(body)


def test_goals_api():
    async def body(env):
        hid = (await env.call("POST", "/api/habits", json={"scope": "p", "title": "Зал"}))[1]["id"]
        await env.call("POST", "/api/checkin", json={"habitId": hid})
        deadline = (date.today() + timedelta(days=30)).isoformat()
        status, res = await env.call(
            "POST", "/api/goals",
            json={"title": "Похудеть", "emoji": "🏃", "target": 5, "unit": "кг", "deadline": deadline, "habitIds": [hid]},
        )
        assert status == 200
        goal = res["goals"][0]
        assert goal["progress"] == 0 and goal["daysLeft"] == 30 and goal["habits"][0]["rate"] == 1.0
        res = (await env.call("PATCH", f"/api/goals/{goal['id']}", json={"current": 2.5}))[1]
        assert res["goals"][0]["progress"] == 0.5
        res = (await env.call("PATCH", f"/api/goals/{goal['id']}", json={"current": 5}))[1]
        assert res["goals"][0]["done"] is True

        # Without a number the progress follows the linked habits.
        res = (await env.call("POST", "/api/goals", json={"title": "Режим", "habitIds": [hid]}))[1]
        assert next(g for g in res["goals"] if g["title"] == "Режим")["progress"] == 1.0

        assert (await env.call("POST", "/api/goals", json={"title": " "}))[0] == 400
        assert (await env.call("POST", "/api/goals", json={"title": "x", "target": "five"}))[0] == 400
        assert (await env.call("POST", "/api/goals", json={"title": "x", "habitIds": [999]}))[0] == 404
        assert (await env.call("PATCH", f"/api/goals/{goal['id']}", user=2, json={"current": 1}))[0] == 404
        assert (await env.call("DELETE", f"/api/goals/{goal['id']}"))[0] == 200
        assert len((await env.call("GET", "/api/goals"))[1]["goals"]) == 1

    run(body)
