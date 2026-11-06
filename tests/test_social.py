"""Challenges (pair or group, with stakes), nudges and reactions."""

import asyncio
from datetime import date, timedelta
from zoneinfo import ZoneInfo

import pytest

from together_habits import logic
from together_habits.db import PairError, Storage
from together_habits.handlers import Handlers
from tests.test_handlers import FakeBot
from tests.test_webapp import make_pair, run


def test_challenge_scores():
    start = date(2026, 11, 1)
    end = start + timedelta(days=6)
    today = start + timedelta(days=3)  # day 4 of 7
    marks = {1: {start, start + timedelta(days=1), start + timedelta(days=10)}, 2: {start}}
    assert logic.challenge_scores("do", start, end, today, marks, [1, 2, 3]) == {1: 2, 2: 1, 3: 0}
    # "avoid": elapsed days minus slips.
    assert logic.challenge_scores("avoid", start, end, today, marks, [1, 2, 3]) == {1: 2, 2: 3, 3: 4}
    assert logic.challenge_winners({1: 2, 2: 2, 3: 0}) == [1, 2]
    assert logic.challenge_winners({1: 0}) == []


def test_finalize_announces_results_with_stake():
    db = Storage(":memory:")
    for uid in (1, 2, 3):
        db.upsert_user(uid, f"U{uid}")
    start = date.today() - timedelta(days=10)
    ch = db.create_challenge(1, "Зал", kind="do", days=7, start=start, stake="кофе")
    db.join_challenge(ch.id, 2)
    db.join_challenge(ch.id, 3)
    for offset in range(5):
        db.toggle_challenge_mark(ch.id, 1, start + timedelta(days=offset))
    db.toggle_challenge_mark(ch.id, 2, start)
    bot = FakeBot()
    handlers = Handlers(db, ZoneInfo("UTC"))
    assert asyncio.run(handlers.finalize_challenges(bot, date.today())) == 1
    assert sorted(chat for chat, _ in bot.sent) == [1, 2, 3]
    text = bot.sent[0][1]
    assert "Победитель: <b>U1</b>" in text and "Ставка: кофе" in text and "5 дн." in text
    assert asyncio.run(handlers.finalize_challenges(bot, date.today())) == 0  # only once
    with pytest.raises(ValueError):
        db.join_challenge(ch.id, 1)  # finished
    db.close()


def test_challenge_join_limits():
    db = Storage(":memory:")
    for uid in range(1, 13):
        db.upsert_user(uid, f"U{uid}")
    ch = db.create_challenge(1, "Без сахара", kind="avoid", days=14, start=date.today())
    handlers = Handlers(db, ZoneInfo("UTC"))
    bot = FakeBot()
    asyncio.run(handlers.join_challenge_by_code(bot, ch.invite_code, 2))
    assert bot.sent[-1][0] == 1 and "принимает вызов" in bot.sent[-1][1]
    for uid in range(3, 11):
        db.join_challenge(ch.id, uid)
    with pytest.raises(PairError):
        asyncio.run(handlers.join_challenge_by_code(bot, ch.invite_code, 11))  # 10 max
    with pytest.raises(PairError):
        asyncio.run(handlers.join_challenge_by_code(bot, "nope", 11))
    with pytest.raises(ValueError):
        db.create_challenge(1, "x", days=5, start=date.today())
    db.close()


def test_challenge_api_flow():
    async def body(env):
        await make_pair(env, "f")
        status, ch = await env.call(
            "POST", "/api/challenges",
            json={"title": "Отжимания", "emoji": "💪", "days": 14, "stake": "проигравший покупает кофе", "invite": ["f"]},
        )
        assert status == 200 and ch["days"] == 14 and ch["dayNumber"] == 1
        assert [m["name"] for m in ch["members"]] == ["U1", "U2"] or {m["name"] for m in ch["members"]} == {"U1", "U2"}
        assert env.bot.sent[-1][0] == 2 and "вызывает тебя" in env.bot.sent[-1][1] and "кофе" in env.bot.sent[-1][1]
        assert ch["link"].startswith("https://t.me/test_habits_bot?start=ch_")

        # A third person joins by link (group challenge).
        env.db.upsert_user(3, "U3")
        code = ch["link"].split("ch_")[1]
        assert (await env.call("POST", "/api/challenges/join", user=3, json={"code": code}))[0] == 200
        assert (await env.call("POST", "/api/challenges/join", user=3, json={"code": "bad"}))[0] == 400

        assert (await env.call("POST", f"/api/challenges/{ch['id']}/mark", json={}))[1]["marked"] is True
        listing = (await env.call("GET", "/api/challenges", user=3))[1]
        mine = listing["active"][0]
        assert len(mine["members"]) == 3 and mine["members"][0]["name"] == "U1" and mine["members"][0]["score"] == 1
        assert not mine["isCreator"]
        # Strangers cannot mark; members can leave; the creator deletes.
        env.db.upsert_user(4, "U4")
        assert (await env.call("POST", f"/api/challenges/{ch['id']}/mark", user=4, json={}))[0] == 404
        assert (await env.call("DELETE", f"/api/challenges/{ch['id']}", user=3))[0] == 200
        assert (await env.call("GET", "/api/challenges", user=3))[1]["active"] == []
        assert (await env.call("DELETE", f"/api/challenges/{ch['id']}"))[0] == 200
        assert env.db.get_challenge(ch["id"]) is None
        assert (await env.call("POST", "/api/challenges", json={"title": "x", "days": 3}))[0] == 400

        # Avoid challenge: a slip tells the others.
        ch2 = (await env.call("POST", "/api/challenges", json={"title": "Без сладкого", "kind": "avoid", "days": 7, "invite": ["f"]}))[1]
        await env.call("POST", f"/api/challenges/{ch2['id']}/mark", json={})
        assert env.bot.sent[-1][0] == 2 and "срыв" in env.bot.sent[-1][1]

    run(body)


def test_nudge_and_reactions():
    async def body(env):
        await make_pair(env, "f")
        hid = (await env.call("POST", "/api/habits", json={"scope": "f", "title": "Зал"}))[1]["id"]
        assert (await env.call("POST", "/api/nudge", json={"habitId": hid}))[0] == 200
        assert env.bot.sent[-1][0] == 2 and "напоминает" in env.bot.sent[-1][1]
        assert (await env.call("POST", "/api/nudge", json={"habitId": hid}))[0] == 400  # once a day
        habit = (await env.call("GET", "/api/state?scope=f"))[1]["habits"][0]
        assert habit["nudgedToday"] is True

        # Reactions only after the partner has done it.
        assert (await env.call("POST", "/api/react", json={"habitId": hid, "emoji": "🔥"}))[0] == 400
        await env.call("POST", "/api/checkin", user=2, json={"habitId": hid})
        assert (await env.call("POST", "/api/nudge", user=1, json={"habitId": hid}))[0] == 400  # already done
        assert (await env.call("POST", "/api/react", json={"habitId": hid, "emoji": "🤡"}))[0] == 400
        assert (await env.call("POST", "/api/react", json={"habitId": hid, "emoji": "🔥"}))[0] == 200
        assert env.bot.sent[-1][0] == 2 and "🔥" in env.bot.sent[-1][1]
        theirs = (await env.call("GET", "/api/state?scope=f", user=2))[1]["habits"][0]
        assert theirs["reactionToMe"] == "🔥"
        mine = (await env.call("GET", "/api/state?scope=f"))[1]["habits"][0]
        assert mine["myReaction"] == "🔥"

        # Personal habits have no partner.
        pid = (await env.call("POST", "/api/habits", json={"scope": "p", "title": "Чтение"}))[1]["id"]
        assert (await env.call("POST", "/api/nudge", json={"habitId": pid}))[0] == 400

    run(body)
