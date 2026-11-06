"""Edit, yesterday, archive, goals, quantitative habits, notes and streak freezes."""

from datetime import date, timedelta

import pytest

from together_habits import logic
from together_habits.db import Storage, validate_habit_options
from tests.test_webapp import make_pair, run


def test_validate_habit_options():
    assert validate_habit_options("do", target_amount=8, unit=" стаканов ")["unit"] == "стаканов"
    assert validate_habit_options("do", target_amount=8)["step"] == 1
    assert validate_habit_options("do", unit="мин")["unit"] is None  # unit only with an amount
    for bad in (
        {"weekly_target": 7},
        {"goal_days": 0},
        {"target_amount": True},
        {"remind_at": "7:00"},
        {"remind_at": "24:00"},
        {"weekly_target": 3, "target_amount": 5},
    ):
        with pytest.raises(ValueError):
            validate_habit_options("do", **bad)
    with pytest.raises(ValueError):
        validate_habit_options("avoid", target_amount=3)
    assert validate_habit_options("do", remind_at="07:30")["remind_at"] == "07:30"


def test_storage_amounts_notes_freezes():
    db = Storage(":memory:")
    db.upsert_user(1, "A")
    day = date(2026, 11, 5)
    water = db.add_habit("Вода", "💧", owner_id=1, target_amount=8, unit="стаканов", created=day)
    assert db.set_amount(water, 1, day, 5) is False and db.checkins(water.id) == {}
    assert db.set_amount(water, 1, day, 8) is True and db.checkins(water.id) == {day: {1}}
    assert db.set_amount(water, 1, day, 0) is False and db.amounts(water.id) == {}

    db.set_note(water.id, 1, day, "  выпил всё  ")
    assert db.notes(water.id) == [(day, 1, "выпил всё")]
    db.set_note(water.id, 1, day, "")
    assert db.notes(water.id) == []

    db.add_freeze(water.id, 1, day)
    with pytest.raises(ValueError):
        db.add_freeze(water.id, 1, day)  # already frozen
    db.add_freeze(water.id, 1, day - timedelta(days=1))
    with pytest.raises(ValueError):
        db.add_freeze(water.id, 1, day - timedelta(days=2))  # monthly quota (2) used
    assert db.freezes_used(1, day) == 2
    assert db.remove_freeze(water.id, 1, day) and db.freezes_used(1, day) == 1

    edited = db.update_habit(water.id, title="Вода 2л", goal_days=30, unit="мл", target_amount=2000, step=250)
    assert (edited.title, edited.goal_days, edited.target_amount, edited.step) == ("Вода 2л", 30, 2000, 250)
    edited = db.update_habit(water.id, goal_days=None)
    assert edited.goal_days is None and edited.target_amount == 2000  # untouched fields kept

    db.archive_habit(water.id)
    assert db.list_habits(owner_id=1) == [] and [h.id for h in db.list_archived(owner_id=1)] == [water.id]
    db.restore_habit(water.id)
    assert db.get_habit(water.id) is not None
    db.delete_habit_forever(water.id)
    assert db.get_habit(water.id, include_archived=True) is None
    db.close()


def test_freeze_keeps_streak():
    today = date(2026, 11, 5)
    done = {today - timedelta(days=o) for o in (0, 1, 3, 4)}  # day -2 missed
    assert logic.current_streak(done, today) == 2
    fixed = logic.with_freezes("do", done, {today - timedelta(days=2)})
    assert logic.current_streak(fixed, today) == 5
    assert logic.with_freezes("avoid", done, {today}) == done  # no freezes for don't-do
    assert logic.freezable_days(done, set(), today, start=today - timedelta(days=4)) == [today - timedelta(days=2)]


def test_api_edit_yesterday_amount_notes_freeze_archive():
    async def body(env):
        status, res = await env.call(
            "POST", "/api/habits",
            json={"scope": "p", "title": "Вода", "emoji": "💧", "targetAmount": 8, "unit": "стаканов", "goalDays": 30},
        )
        assert status == 200
        water = res["id"]
        habit = (await env.call("GET", "/api/state?scope=p"))[1]["habits"][0]
        assert habit["targetAmount"] == 8 and habit["myAmount"] == 0 and habit["goalProgress"] == 0

        # Quantitative: "+" adds one step; plain check-ins are refused.
        assert (await env.call("POST", "/api/checkin", json={"habitId": water}))[0] == 400
        for _ in range(7):
            await env.call("POST", "/api/amount", json={"habitId": water})
        status, res = await env.call("POST", "/api/amount", json={"habitId": water})
        assert res == {"value": 8, "met": True}
        habit = (await env.call("GET", "/api/state?scope=p"))[1]["habits"][0]
        assert habit["meDone"] and habit["myAmount"] == 8 and habit["goalProgress"] == 1
        assert (await env.call("POST", "/api/amount", json={"habitId": water, "value": 3}))[1]["met"] is False
        assert (await env.call("POST", "/api/amount", json={"habitId": water, "value": "3"}))[0] == 400

        # Edit.
        assert (await env.call("PATCH", f"/api/habits/{water}", json={"title": "Вода 2л", "remindAt": "09:00"}))[0] == 200
        habit = (await env.call("GET", "/api/state?scope=p"))[1]["habits"][0]
        assert habit["title"] == "Вода 2л" and habit["remindAt"] == "09:00" and habit["targetAmount"] == 8
        assert (await env.call("PATCH", f"/api/habits/{water}", json={"remindAt": "9am"}))[0] == 400
        assert (await env.call("PATCH", f"/api/habits/{water}", json={"title": "x"}, user=2))[0] == 404

        # Yesterday (and only yesterday) can be marked.
        status, res = await env.call("POST", "/api/habits", json={"scope": "p", "title": "Чтение"})
        read = res["id"]
        assert (await env.call("POST", "/api/checkin", json={"habitId": read, "date": "yesterday"}))[1]["checked"]
        details = (await env.call("GET", f"/api/habits/{read}"))[1]
        assert details["yesterdayMarked"] is True
        old = (date.today() - timedelta(days=3)).isoformat()
        assert (await env.call("POST", "/api/checkin", json={"habitId": read, "date": old}))[0] == 400

        # Notes.
        assert (await env.call("POST", "/api/note", json={"habitId": read, "text": "20 страниц"}))[0] == 200
        notes = (await env.call("GET", f"/api/habits/{read}"))[1]["notes"]
        assert notes[0]["text"] == "20 страниц" and notes[0]["mine"]

        # Archive, list, restore, delete forever.
        assert (await env.call("DELETE", f"/api/habits/{read}"))[0] == 200
        archived = (await env.call("GET", "/api/archive?scope=p"))[1]["habits"]
        assert [h["title"] for h in archived] == ["Чтение"] and archived[0]["doneDays"] == 1
        assert (await env.call("POST", f"/api/habits/{read}/restore", user=2))[0] == 404
        assert (await env.call("POST", f"/api/habits/{read}/restore"))[0] == 200
        assert len((await env.call("GET", "/api/state?scope=p"))[1]["habits"]) == 2
        await env.call("DELETE", f"/api/habits/{read}")
        assert (await env.call("DELETE", f"/api/habits/{read}?forever=1"))[0] == 200
        assert (await env.call("GET", "/api/archive?scope=p"))[1]["habits"] == []

    run(body)


def test_api_freeze():
    async def body(env):
        start = date.today() - timedelta(days=5)
        env.db.upsert_user(1, "U1")
        habit = env.db.add_habit("Зал", owner_id=1, created=start)
        for offset in (0, 1, 3, 4, 5):
            env.db.toggle_checkin(habit.id, 1, date.today() - timedelta(days=offset))
        state = (await env.call("GET", "/api/state?scope=p"))[1]["habits"][0]
        assert state["streak"] == 2
        details = (await env.call("GET", f"/api/habits/{habit.id}"))[1]
        missed = (date.today() - timedelta(days=2)).isoformat()
        assert details["freezable"] == [missed] and details["freezesLeft"] == 2

        assert (await env.call("POST", "/api/freeze", json={"habitId": habit.id, "date": date.today().isoformat()}))[0] == 400
        assert (await env.call("POST", "/api/freeze", json={"habitId": habit.id, "date": missed}))[0] == 200
        state = (await env.call("GET", "/api/state?scope=p"))[1]["habits"][0]
        assert state["streak"] == 6
        assert "frozen" in [w["status"] for w in state["week"]] or date.fromisoformat(missed).weekday() > date.today().weekday()
        details = (await env.call("GET", f"/api/habits/{habit.id}"))[1]
        assert details["freezesLeft"] == 1 and details["frozen"][0]["date"] == missed

        assert (await env.call("POST", "/api/freeze", json={"habitId": habit.id, "date": missed, "remove": True}))[0] == 200
        assert (await env.call("GET", "/api/state?scope=p"))[1]["habits"][0]["streak"] == 2

    run(body)


def test_shared_amount_notifies_on_completion():
    async def body(env):
        await make_pair(env, "f")
        res = (await env.call("POST", "/api/habits", json={"scope": "f", "title": "Отжимания", "targetAmount": 50, "step": 10}))[1]
        sent = len(env.bot.sent)
        await env.call("POST", "/api/amount", json={"habitId": res["id"]})
        assert len(env.bot.sent) == sent  # not met yet → no message
        await env.call("POST", "/api/amount", json={"habitId": res["id"], "value": 50})
        assert env.bot.sent[-1][0] == 2 and "Твоя очередь" in env.bot.sent[-1][1]

    run(body)
