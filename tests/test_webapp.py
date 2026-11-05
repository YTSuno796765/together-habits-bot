"""Mini App API: init data signature checks and end-to-end flows."""

import asyncio
import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import pytest
from aiohttp.test_utils import TestClient, TestServer

from together_habits.db import Storage
from together_habits.handlers import Handlers
from together_habits.webapp import WebApi, validate_init_data

TOKEN = "123456:test-token-not-real"
TZ = ZoneInfo("UTC")


def sign(user_id: int, name: str = "Тест", token: str = TOKEN, auth_date: int | None = None) -> str:
    fields = {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "query_id": "AAEtest",
        "user": json.dumps({"id": user_id, "first_name": name}, ensure_ascii=False),
    }
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_validate_init_data():
    assert validate_init_data(sign(7, "Аня"), TOKEN)["id"] == 7
    assert validate_init_data(sign(7, token="999:other"), TOKEN) is None
    assert validate_init_data(sign(7, auth_date=int(time.time()) - 2 * 86400), TOKEN) is None
    tampered = sign(7).replace("%22id%22%3A+7", "%22id%22%3A+8")
    assert validate_init_data(tampered, TOKEN) is None
    assert validate_init_data("", TOKEN) is None
    assert validate_init_data("garbage", TOKEN) is None


class FakeBot:
    username = "test_habits_bot"

    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))


def run(coro_fn):
    """Run an async test body against a fresh API instance."""

    async def wrapper():
        db = Storage(":memory:")
        bot = FakeBot()
        handlers = Handlers(db, TZ, "https://example.invalid")
        api = WebApi(db, TZ, TOKEN, handlers, bot)
        async with TestClient(TestServer(api.build())) as client:
            env = SimpleNamespace(db=db, bot=bot, client=client)

            async def call(method, path, user=1, **kwargs):
                headers = {"Authorization": "tma " + sign(user, f"U{user}")}
                resp = await client.request(method, path, headers=headers, **kwargs)
                return resp.status, await resp.json()

            env.call = call
            await coro_fn(env)
        db.close()

    asyncio.run(wrapper())


def test_requires_signature():
    async def body(env):
        resp = await env.client.get("/api/state")
        assert resp.status == 401
        resp = await env.client.get("/api/state", headers={"Authorization": "tma " + sign(1, token="1:x")})
        assert resp.status == 401
        resp = await env.client.get("/")
        assert resp.status == 200
        assert "telegram-web-app.js" in await resp.text()

    run(body)


def test_personal_flow():
    async def body(env):
        status, state = await env.call("GET", "/api/state?scope=p")
        assert status == 200 and state["habits"] == [] and state["templates"]
        status, _ = await env.call(
            "POST", "/api/habits", json={"scope": "p", "title": "Чтение", "emoji": "📖", "schedule": 127}
        )
        assert status == 200
        state = (await env.call("GET", "/api/state?scope=p"))[1]
        habit = state["habits"][0]
        assert habit["scheduleLabel"] == "каждый день" and len(habit["week"]) == 7
        assert state["progress"] == {"done": 0, "total": 1}

        status, res = await env.call("POST", "/api/checkin", json={"habitId": habit["id"]})
        assert status == 200 and res["checked"] is True
        state = (await env.call("GET", "/api/state?scope=p"))[1]
        assert state["progress"] == {"done": 1, "total": 1}
        assert state["habits"][0]["streak"] == 1

        stats = (await env.call("GET", "/api/stats?scope=p"))[1]
        assert stats["rate7"] == 1.0 and stats["best"] == 1 and len(stats["daily"]) == 14

        # Another user cannot touch it.
        status, _ = await env.call("POST", "/api/checkin", user=2, json={"habitId": habit["id"]})
        assert status == 404
        status, _ = await env.call("DELETE", f"/api/habits/{habit['id']}", user=2)
        assert status == 404
        status, _ = await env.call("DELETE", f"/api/habits/{habit['id']}")
        assert status == 200
        assert (await env.call("GET", "/api/state?scope=p"))[1]["habits"] == []

    run(body)


def test_validation_errors():
    async def body(env):
        assert (await env.call("POST", "/api/habits", json={"scope": "p", "title": " "}))[0] == 400
        assert (await env.call("POST", "/api/habits", json={"scope": "p", "title": "x", "schedule": 0}))[0] == 400
        assert (await env.call("POST", "/api/habits", json={"scope": "zz", "title": "x"}))[0] == 400
        assert (await env.call("POST", "/api/habits", json={"scope": "f", "title": "x"}))[0] == 400
        assert (await env.call("POST", "/api/mood", json={"score": 3}))[0] == 400
        assert (await env.call("GET", "/api/stats?scope=c"))[0] == 400

    run(body)


def test_couple_flow():
    async def body(env):
        status, res = await env.call("POST", "/api/invite", json={"scope": "c"})
        assert status == 200 and res["link"].startswith("https://t.me/test_habits_bot?start=join_")
        state = (await env.call("GET", "/api/state?scope=c"))[1]
        assert state["pair"]["status"] == "pending"

        code = res["link"].split("join_")[1]
        env.db.upsert_user(2, "U2")
        env.db.join_pair(code, 2)

        state = (await env.call("GET", "/api/state?scope=c"))[1]
        assert state["pair"] == {"status": "active", "partner": "U2", "link": None}
        assert state["modes"]["c"] == "active"

        await env.call("POST", "/api/habits", json={"scope": "c", "title": "Прогулка", "emoji": "🚶"})
        habit = (await env.call("GET", "/api/state?scope=c"))[1]["habits"][0]

        await env.call("POST", "/api/checkin", json={"habitId": habit["id"]})
        assert env.bot.sent[-1][0] == 2 and "Твоя очередь" in env.bot.sent[-1][1]
        mine = (await env.call("GET", "/api/state?scope=c"))[1]["habits"][0]
        assert mine["meDone"] and mine["partnerDone"] is False
        assert "half" in [w["status"] for w in mine["week"]]

        await env.call("POST", "/api/checkin", user=2, json={"habitId": habit["id"]})
        assert env.bot.sent[-1][0] == 1 and "Вы оба" in env.bot.sent[-1][1]
        assert (await env.call("GET", "/api/state?scope=c"))[1]["habits"][0]["streak"] == 1

        assert (await env.call("POST", "/api/mood", json={"score": 5}))[0] == 200
        assert (await env.call("POST", "/api/mood", user=2, json={"score": 4}))[0] == 200
        state = (await env.call("GET", "/api/state?scope=c"))[1]
        assert state["mood"] == {"me": 5, "partner": 4}

        stats = (await env.call("GET", "/api/stats?scope=c"))[1]
        assert stats["pair"]["compatibility"] == 100
        assert stats["pair"]["moodWeek"][-1] == {"label": stats["pair"]["moodWeek"][-1]["label"], "me": 5, "partner": 4}

    run(body)


def test_avoid_habits():
    async def body(env):
        status, _ = await env.call(
            "POST", "/api/habits", json={"scope": "p", "kind": "avoid", "title": "Курить", "emoji": "🚭"}
        )
        assert status == 200
        assert (await env.call("POST", "/api/habits", json={"scope": "p", "kind": "zzz", "title": "x"}))[0] == 400
        state = (await env.call("GET", "/api/state?scope=p"))[1]
        assert state["avoidTemplates"]
        habit = state["habits"][0]
        assert habit["kind"] == "avoid" and habit["meDone"] is True and habit["meMarked"] is False
        assert habit["streak"] == 1 and habit["week"][habit_index_today(state)]["status"] == "done"
        # "Don't do" habits are not part of the daily to-do progress.
        assert state["progress"] == {"done": 0, "total": 0}

        await env.call("POST", "/api/checkin", json={"habitId": habit["id"]})  # slip
        state = (await env.call("GET", "/api/state?scope=p"))[1]
        habit = state["habits"][0]
        assert habit["meMarked"] is True and habit["meDone"] is False and habit["streak"] == 0
        assert habit["week"][habit_index_today(state)]["status"] == "missed"
        stats = (await env.call("GET", "/api/stats?scope=p"))[1]
        assert stats["habits"][0]["kind"] == "avoid" and stats["rate7"] == 0.0

    run(body)


def test_shared_avoid_slip_notifies_partner():
    async def body(env):
        link = (await env.call("POST", "/api/invite", json={"scope": "f"}))[1]["link"]
        env.db.upsert_user(2, "U2")
        env.db.join_pair(link.split("join_")[1], 2)
        await env.call("POST", "/api/habits", json={"scope": "f", "kind": "avoid", "title": "Курить"})
        habit = (await env.call("GET", "/api/state?scope=f"))[1]["habits"][0]
        await env.call("POST", "/api/checkin", user=2, json={"habitId": habit["id"]})
        assert env.bot.sent[-1][0] == 1 and "срыв" in env.bot.sent[-1][1]
        mine = (await env.call("GET", "/api/state?scope=f"))[1]["habits"][0]
        assert mine["meDone"] is True and mine["partnerDone"] is False and mine["partnerMarked"] is True
        assert mine["streak"] == 0  # the shared clean streak is broken
        stats = (await env.call("GET", "/api/stats?scope=f"))[1]
        assert stats["pair"]["sync"] is None  # slips do not count as shared activity

    run(body)


def habit_index_today(state):
    from datetime import date

    return date.fromisoformat(state["today"]).weekday()


def test_order_and_pin_api():
    async def body(env):
        for title in ("A", "B", "C"):
            await env.call("POST", "/api/habits", json={"scope": "p", "title": title})
        await env.call("POST", "/api/habits", json={"scope": "p", "title": "Курить", "kind": "avoid"})
        habits = (await env.call("GET", "/api/state?scope=p"))[1]["habits"]
        ids = {h["title"]: h["id"] for h in habits}

        order = [ids["C"], ids["A"], ids["B"], ids["Курить"]]
        status, _ = await env.call("POST", "/api/order", json={"scope": "p", "ids": order, "pinned": [ids["B"]]})
        assert status == 200
        habits = (await env.call("GET", "/api/state?scope=p"))[1]["habits"]
        assert [h["title"] for h in habits] == ["B", "C", "A", "Курить"]
        assert [h["pinned"] for h in habits] == [True, False, False, False]

        status, _ = await env.call("POST", f"/api/habits/{ids['A']}/pin", json={"pinned": True})
        assert status == 200
        assert (await env.call("GET", "/api/state?scope=p"))[1]["habits"][0]["title"] in {"A", "B"}

        # Incomplete, foreign or malformed lists are rejected.
        assert (await env.call("POST", "/api/order", json={"scope": "p", "ids": order[:2]}))[0] == 409
        assert (await env.call("POST", "/api/order", json={"scope": "p", "ids": order + [999]}))[0] == 409
        assert (await env.call("POST", "/api/order", json={"scope": "p", "ids": [[1]]}))[0] == 400
        assert (await env.call("POST", "/api/order", user=2, json={"scope": "p", "ids": order}))[0] == 409
        assert (await env.call("POST", f"/api/habits/{ids['A']}/pin", user=2, json={"pinned": True}))[0] == 404
        assert (await env.call("POST", f"/api/habits/{ids['A']}/pin", json={"pinned": "yes"}))[0] == 400

    run(body)


async def make_pair(env, scope, partner=2):
    link = (await env.call("POST", "/api/invite", json={"scope": scope}))[1]["link"]
    env.db.upsert_user(partner, f"U{partner}")
    env.db.join_pair(link.split("join_")[1], partner)


def test_plans_flow():
    async def body(env):
        from datetime import date, timedelta

        await make_pair(env, "c")
        day = (date.today() + timedelta(days=3)).isoformat()
        status, res = await env.call(
            "POST", "/api/plans",
            json={"scope": "c", "title": "Ужин", "emoji": "🍽", "date": day, "time": "19:30", "note": "в 8 ресторан"},
        )
        assert status == 200
        plan_id = res["id"]
        chat, text = env.bot.sent[-1]
        assert chat == 2 and "предлагает" in text and "Ужин" in text and "19:30" in text

        mine = (await env.call("GET", "/api/plans?month=" + day[:7]))[1]
        plan = next(p for p in mine["plans"] if p["id"] == plan_id)
        assert plan["mine"] and plan["status"] == "pending" and plan["scope"] == "c"
        assert mine["incoming"] == [] and mine["pairs"]["c"]["partner"] == "U2"

        theirs = (await env.call("GET", "/api/plans?month=" + day[:7], user=2))[1]
        assert [p["id"] for p in theirs["incoming"]] == [plan_id]

        # The author cannot answer their own proposal; a stranger cannot either.
        assert (await env.call("POST", f"/api/plans/{plan_id}/respond", json={"accept": True}))[0] == 400
        assert (await env.call("POST", f"/api/plans/{plan_id}/respond", user=3, json={"accept": True}))[0] == 400

        status, res = await env.call("POST", f"/api/plans/{plan_id}/respond", user=2, json={"accept": True})
        assert status == 200 and res["status"] == "accepted"
        assert env.bot.sent[-1][0] == 1 and "принят" in env.bot.sent[-1][1]
        assert (await env.call("POST", f"/api/plans/{plan_id}/respond", user=2, json={"accept": False}))[0] == 400

        upcoming = (await env.call("GET", "/api/plans", user=2))[1]["upcoming"]
        assert upcoming and upcoming[0]["status"] == "accepted" and not upcoming[0]["mine"]

        # Only the author can cancel; the partner is told.
        assert (await env.call("DELETE", f"/api/plans/{plan_id}", user=2))[0] == 404
        assert (await env.call("DELETE", f"/api/plans/{plan_id}"))[0] == 200
        assert env.bot.sent[-1][0] == 2 and "отменяет" in env.bot.sent[-1][1]
        assert env.db.get_plan(plan_id) is None

    run(body)


def test_plan_validation():
    async def body(env):
        from datetime import date, timedelta

        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        # No pair yet / personal scope.
        assert (await env.call("POST", "/api/plans", json={"scope": "f", "title": "x", "date": tomorrow}))[0] == 400
        assert (await env.call("POST", "/api/plans", json={"scope": "p", "title": "x", "date": tomorrow}))[0] == 400
        await make_pair(env, "f")
        past = (date.today() - timedelta(days=1)).isoformat()
        cases = [
            {"title": "x", "date": past},
            {"title": "x", "date": "завтра"},
            {"title": " ", "date": tomorrow},
            {"title": "x", "date": tomorrow, "time": "25:00"},
            {"title": "x", "date": tomorrow, "time": 7},
        ]
        for case in cases:
            assert (await env.call("POST", "/api/plans", json={"scope": "f", **case}))[0] == 400, case
        assert (await env.call("POST", "/api/plans", json={"scope": "f", "title": "Зал", "date": tomorrow}))[0] == 200
        assert (await env.call("GET", "/api/plans?month=2026-13"))[0] == 400
        assert (await env.call("GET", "/api/plans?month=abc"))[0] == 400
        # Friend and couple plans land on one calendar.
        await make_pair(env, "c", partner=3)
        await env.call("POST", "/api/plans", json={"scope": "c", "title": "Кино", "date": tomorrow})
        plans = (await env.call("GET", "/api/plans?month=" + tomorrow[:7]))[1]["plans"]
        assert sorted(p["scope"] for p in plans) == ["c", "f"]

    run(body)


def test_sections_and_unpair():
    async def body(env):
        state = (await env.call("GET", "/api/state?scope=p"))[1]
        assert state["sections"]["f"] == {"name": None, "hidden": False, "pair": "none", "partner": None}

        status, res = await env.call("POST", "/api/sections", json={"scope": "f", "name": "С Мишей"})
        assert status == 200 and res["sections"]["f"]["name"] == "С Мишей"
        status, res = await env.call("POST", "/api/sections", json={"scope": "c", "hidden": True})
        assert res["sections"]["c"]["hidden"] is True
        assert (await env.call("POST", "/api/sections", json={"scope": "p", "hidden": True}))[0] == 400
        assert (await env.call("POST", "/api/sections", json={"scope": "f", "name": 5}))[0] == 400
        assert (await env.call("POST", "/api/sections", json={"scope": "f", "hidden": "no"}))[0] == 400
        # Settings are personal: the other user still sees defaults.
        other = (await env.call("GET", "/api/state?scope=p", user=2))[1]["sections"]
        assert other["f"]["name"] is None and other["c"]["hidden"] is False

        await make_pair(env, "f")
        await env.call("POST", "/api/habits", json={"scope": "f", "title": "Зал"})
        assert (await env.call("GET", "/api/plans"))[1]["sections"]["f"]["partner"] == "U2"

        assert (await env.call("POST", "/api/unpair", json={"scope": "p"}))[0] == 400
        status, res = await env.call("POST", "/api/unpair", json={"scope": "f"})
        assert status == 200 and res["sections"]["f"]["pair"] == "none"
        assert env.bot.sent[-1][0] == 2 and "разрывает связку" in env.bot.sent[-1][1]
        assert env.db.get_pair(2, "friend") is None
        assert (await env.call("POST", "/api/unpair", json={"scope": "f"}))[0] == 404

    run(body)
