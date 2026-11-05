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
