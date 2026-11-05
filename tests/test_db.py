from datetime import date

import pytest

from together_habits.db import PairError, Storage

DAY = date(2026, 11, 5)


@pytest.fixture
def db():
    storage = Storage(":memory:")
    storage.upsert_user(1, "Аня")
    storage.upsert_user(2, "Боря")
    storage.upsert_user(3, "Вика")
    yield storage
    storage.close()


def test_invite_and_join(db):
    pair = db.create_invite(1, "friend")
    assert db.create_invite(1, "friend").invite_code == pair.invite_code
    with pytest.raises(PairError):
        db.join_pair(pair.invite_code, 1)
    joined = db.join_pair(pair.invite_code, 2)
    assert joined.members == {1, 2}
    assert joined.other(2) == 1
    with pytest.raises(PairError):
        db.join_pair(pair.invite_code, 3)
    with pytest.raises(PairError):
        db.create_invite(2, "friend")
    # Friend and couple pairs are independent.
    assert db.get_pair(1, "couple") is None


def test_join_drops_own_pending_invite(db):
    own = db.create_invite(2, "couple")
    pair = db.create_invite(1, "couple")
    db.join_pair(pair.invite_code, 2)
    assert db.get_pair_by_id(own.id) is None


def test_habits_and_checkins(db):
    personal = db.add_habit("Чтение", "📖", owner_id=1)
    assert db.can_access(personal, 1) and not db.can_access(personal, 2)
    assert db.toggle_checkin(personal.id, 1, DAY) is True
    assert db.checkins(personal.id) == {DAY: {1}}
    assert db.toggle_checkin(personal.id, 1, DAY) is False
    assert db.checkins(personal.id) == {}
    db.archive_habit(personal.id)
    assert db.list_habits(owner_id=1) == []


def test_delete_pair_cascades(db):
    pair = db.join_pair(db.create_invite(1, "couple").invite_code, 2)
    habit = db.add_habit("Прогулка", pair_id=pair.id)
    db.toggle_checkin(habit.id, 1, DAY)
    db.set_mood(pair.id, 1, DAY, 4)
    db.delete_pair(pair.id)
    assert db.get_habit(habit.id) is None
    assert db.moods(pair.id, DAY) == {}


def test_moods_and_reminders(db):
    pair = db.join_pair(db.create_invite(1, "couple").invite_code, 2)
    db.set_mood(pair.id, 1, DAY, 3)
    db.set_mood(pair.id, 1, DAY, 5)
    db.set_mood(pair.id, 2, DAY, 4)
    assert db.moods(pair.id, DAY) == {DAY: {1: 5, 2: 4}}
    with pytest.raises(ValueError):
        db.set_mood(pair.id, 1, DAY, 6)
    db.set_reminder(1, 21)
    assert db.users_with_reminder(21) == [1]
    db.set_reminder(1, None)
    assert db.users_with_reminder(21) == []


def test_habit_validation(db):
    with pytest.raises(ValueError):
        db.add_habit("   ", owner_id=1)
    with pytest.raises(ValueError):
        db.add_habit("x", owner_id=1, pair_id=1)
    for i in range(15):
        db.add_habit(f"h{i}", owner_id=1)
    with pytest.raises(ValueError):
        db.add_habit("one more", owner_id=1)


def test_schedule_and_migration(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(
        "CREATE TABLE habits (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, "
        "emoji TEXT NOT NULL, owner_id INTEGER, pair_id INTEGER, archived INTEGER NOT NULL DEFAULT 0, "
        "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);"
        "INSERT INTO habits (title, emoji, owner_id) VALUES ('Old', '⭐', 1);"
    )
    old.commit()
    old.close()

    storage = Storage(path)
    habit = storage.list_habits(owner_id=1)[0]
    assert habit.schedule == 127 and habit.created is not None
    new = storage.add_habit("Зал", "🏋️", owner_id=1, schedule=0b0101010, created=DAY)
    assert new.schedule == 0b0101010 and new.created == DAY
    with pytest.raises(ValueError):
        storage.add_habit("Никогда", owner_id=1, schedule=0)
    storage.close()


def test_habit_kind(db):
    habit = db.add_habit("Курить", "🚭", owner_id=1, kind="avoid")
    assert habit.is_avoid and db.get_habit(habit.id).kind == "avoid"
    assert db.add_habit("Читать", owner_id=1).kind == "do"
    with pytest.raises(ValueError):
        db.add_habit("x", owner_id=1, kind="maybe")
