"""SQLite storage for users, pairs, habits, check-ins and moods."""

from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

PAIR_KINDS = ("friend", "couple")
EVERY_DAY = 0b1111111
HABIT_KINDS = ("do", "avoid")
HABIT_ORDER = "ORDER BY pinned DESC, position, id"
MAX_HABITS_PER_SCOPE = 15

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    reminder_hour INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS pairs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN ('friend', 'couple')),
    owner_id INTEGER NOT NULL REFERENCES users(id),
    partner_id INTEGER REFERENCES users(id),
    invite_code TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS habits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    emoji TEXT NOT NULL,
    owner_id INTEGER REFERENCES users(id),
    pair_id INTEGER REFERENCES pairs(id) ON DELETE CASCADE,
    schedule INTEGER NOT NULL DEFAULT 127,
    kind TEXT NOT NULL DEFAULT 'do',
    position INTEGER NOT NULL DEFAULT 0,
    pinned INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK ((owner_id IS NULL) <> (pair_id IS NULL))
);
CREATE TABLE IF NOT EXISTS checkins (
    habit_id INTEGER NOT NULL REFERENCES habits(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    day TEXT NOT NULL,
    PRIMARY KEY (habit_id, user_id, day)
);
CREATE TABLE IF NOT EXISTS plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pair_id INTEGER NOT NULL REFERENCES pairs(id) ON DELETE CASCADE,
    author_id INTEGER NOT NULL REFERENCES users(id),
    title TEXT NOT NULL,
    emoji TEXT NOT NULL,
    day TEXT NOT NULL,
    time TEXT,
    note TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'declined')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS plans_pair_day ON plans (pair_id, day);
CREATE TABLE IF NOT EXISTS user_modes (
    user_id INTEGER NOT NULL REFERENCES users(id),
    scope TEXT NOT NULL CHECK (scope IN ('p', 'f', 'c')),
    name TEXT,
    hidden INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, scope)
);
CREATE TABLE IF NOT EXISTS moods (
    pair_id INTEGER NOT NULL REFERENCES pairs(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    day TEXT NOT NULL,
    score INTEGER NOT NULL CHECK (score BETWEEN 1 AND 5),
    PRIMARY KEY (pair_id, user_id, day)
);
"""


class PairError(Exception):
    """Raised when an invite or pairing operation is not allowed."""


@dataclass(frozen=True)
class Pair:
    id: int
    kind: str
    owner_id: int
    partner_id: int | None
    invite_code: str

    @property
    def is_complete(self) -> bool:
        return self.partner_id is not None

    @property
    def members(self) -> frozenset[int]:
        ids = {self.owner_id}
        if self.partner_id is not None:
            ids.add(self.partner_id)
        return frozenset(ids)

    def other(self, user_id: int) -> int | None:
        return self.partner_id if user_id == self.owner_id else self.owner_id


@dataclass(frozen=True)
class Habit:
    id: int
    title: str
    emoji: str
    owner_id: int | None
    pair_id: int | None
    schedule: int = EVERY_DAY  # bit N set = weekday N (Monday = 0)
    created: date | None = None
    kind: str = "do"  # "do": check in when done; "avoid": check in only on a slip
    pinned: bool = False
    position: int = 0

    @property
    def is_avoid(self) -> bool:
        return self.kind == "avoid"


@dataclass(frozen=True)
class Plan:
    id: int
    pair_id: int
    author_id: int
    title: str
    emoji: str
    day: date
    time: str | None
    note: str | None
    status: str


PLAN_STATUSES = ("pending", "accepted", "declined")


class Storage:
    def __init__(self, path: Path | str = ":memory:") -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(habits)")}
        if "schedule" not in columns:
            self.conn.execute("ALTER TABLE habits ADD COLUMN schedule INTEGER NOT NULL DEFAULT 127")
        if "kind" not in columns:
            self.conn.execute("ALTER TABLE habits ADD COLUMN kind TEXT NOT NULL DEFAULT 'do'")
        if "position" not in columns:
            self.conn.execute("ALTER TABLE habits ADD COLUMN position INTEGER NOT NULL DEFAULT 0")
        if "pinned" not in columns:
            self.conn.execute("ALTER TABLE habits ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")

    def close(self) -> None:
        self.conn.close()

    # ---- users -------------------------------------------------------------

    def upsert_user(self, user_id: int, name: str) -> None:
        self.conn.execute(
            "INSERT INTO users (id, name) VALUES (?, ?) "
            "ON CONFLICT(id) DO UPDATE SET name = excluded.name",
            (user_id, name[:64] or "Без имени"),
        )
        self.conn.commit()

    def user_name(self, user_id: int) -> str:
        row = self.conn.execute("SELECT name FROM users WHERE id = ?", (user_id,)).fetchone()
        return row["name"] if row else "Партнёр"

    def set_reminder(self, user_id: int, hour: int | None) -> None:
        if hour is not None and not 0 <= hour <= 23:
            raise ValueError("hour must be between 0 and 23")
        self.conn.execute("UPDATE users SET reminder_hour = ? WHERE id = ?", (hour, user_id))
        self.conn.commit()

    def reminder_hour(self, user_id: int) -> int | None:
        row = self.conn.execute(
            "SELECT reminder_hour FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return row["reminder_hour"] if row else None

    def users_with_reminder(self, hour: int) -> list[int]:
        rows = self.conn.execute("SELECT id FROM users WHERE reminder_hour = ?", (hour,))
        return [row["id"] for row in rows]

    # ---- pairs -------------------------------------------------------------

    @staticmethod
    def _pair(row: sqlite3.Row | None) -> Pair | None:
        if row is None:
            return None
        return Pair(row["id"], row["kind"], row["owner_id"], row["partner_id"], row["invite_code"])

    def get_pair(self, user_id: int, kind: str) -> Pair | None:
        row = self.conn.execute(
            "SELECT * FROM pairs WHERE kind = ? AND (owner_id = ? OR partner_id = ?)",
            (kind, user_id, user_id),
        ).fetchone()
        return self._pair(row)

    def get_pair_by_id(self, pair_id: int) -> Pair | None:
        row = self.conn.execute("SELECT * FROM pairs WHERE id = ?", (pair_id,)).fetchone()
        return self._pair(row)

    def create_invite(self, user_id: int, kind: str) -> Pair:
        if kind not in PAIR_KINDS:
            raise ValueError(f"unknown pair kind: {kind}")
        existing = self.get_pair(user_id, kind)
        if existing is not None:
            if existing.is_complete:
                raise PairError("У тебя уже есть связка в этом режиме.")
            return existing
        code = secrets.token_urlsafe(8)
        cur = self.conn.execute(
            "INSERT INTO pairs (kind, owner_id, invite_code) VALUES (?, ?, ?)",
            (kind, user_id, code),
        )
        self.conn.commit()
        pair = self.get_pair_by_id(cur.lastrowid)
        assert pair is not None
        return pair

    def join_pair(self, code: str, user_id: int) -> Pair:
        row = self.conn.execute("SELECT * FROM pairs WHERE invite_code = ?", (code,)).fetchone()
        pair = self._pair(row)
        if pair is None:
            raise PairError("Приглашение не найдено или уже отменено.")
        if pair.owner_id == user_id:
            raise PairError("Это твоё собственное приглашение — отправь ссылку другому человеку.")
        if pair.is_complete:
            if pair.partner_id == user_id:
                return pair
            raise PairError("По этому приглашению уже кто-то присоединился.")
        mine = self.get_pair(user_id, pair.kind)
        if mine is not None:
            if mine.is_complete:
                raise PairError("У тебя уже есть связка в этом режиме. Сначала разорви её.")
            # Drop the user's own pending invite of the same kind.
            self.conn.execute("DELETE FROM pairs WHERE id = ?", (mine.id,))
        self.conn.execute("UPDATE pairs SET partner_id = ? WHERE id = ?", (user_id, pair.id))
        self.conn.commit()
        joined = self.get_pair_by_id(pair.id)
        assert joined is not None
        return joined

    def delete_pair(self, pair_id: int) -> None:
        self.conn.execute("DELETE FROM pairs WHERE id = ?", (pair_id,))
        self.conn.commit()

    # ---- habits ------------------------------------------------------------

    @staticmethod
    def _habit(row: sqlite3.Row) -> Habit:
        created = date.fromisoformat(row["created_at"][:10]) if row["created_at"] else None
        return Habit(
            row["id"],
            row["title"],
            row["emoji"],
            row["owner_id"],
            row["pair_id"],
            row["schedule"],
            created,
            row["kind"],
            bool(row["pinned"]),
            row["position"],
        )

    def add_habit(
        self,
        title: str,
        emoji: str = "⭐",
        *,
        owner_id: int | None = None,
        pair_id: int | None = None,
        schedule: int = EVERY_DAY,
        created: date | None = None,
        kind: str = "do",
    ) -> Habit:
        if kind not in HABIT_KINDS:
            raise ValueError(f"unknown habit kind: {kind}")
        if (owner_id is None) == (pair_id is None):
            raise ValueError("habit needs exactly one of owner_id or pair_id")
        if not 1 <= schedule <= EVERY_DAY:
            raise ValueError("schedule must select at least one weekday")
        title = title.strip()[:40]
        emoji = emoji.strip()[:8] or "⭐"
        if not title:
            raise ValueError("habit title is empty")
        if len(self.list_habits(owner_id=owner_id, pair_id=pair_id)) >= MAX_HABITS_PER_SCOPE:
            raise ValueError("too many habits")
        created_at = (created or date.today()).isoformat() + " 00:00:00"
        scope_column = "owner_id" if owner_id is not None else "pair_id"
        row = self.conn.execute(
            f"SELECT COALESCE(MAX(position), -1) + 1 AS next FROM habits WHERE {scope_column} = ?",
            (owner_id if owner_id is not None else pair_id,),
        ).fetchone()
        cur = self.conn.execute(
            "INSERT INTO habits (title, emoji, owner_id, pair_id, schedule, created_at, kind, position) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (title, emoji, owner_id, pair_id, schedule, created_at, kind, row["next"]),
        )
        self.conn.commit()
        habit = self.get_habit(cur.lastrowid)
        assert habit is not None
        return habit

    def get_habit(self, habit_id: int) -> Habit | None:
        row = self.conn.execute(
            "SELECT * FROM habits WHERE id = ? AND archived = 0", (habit_id,)
        ).fetchone()
        return self._habit(row) if row else None

    def list_habits(self, *, owner_id: int | None = None, pair_id: int | None = None) -> list[Habit]:
        if owner_id is not None:
            rows = self.conn.execute(
                "SELECT * FROM habits WHERE owner_id = ? AND archived = 0 " + HABIT_ORDER, (owner_id,)
            )
        else:
            rows = self.conn.execute(
                "SELECT * FROM habits WHERE pair_id = ? AND archived = 0 " + HABIT_ORDER, (pair_id,)
            )
        return [self._habit(row) for row in rows]

    def set_pinned(self, habit_id: int, pinned: bool) -> None:
        self.conn.execute("UPDATE habits SET pinned = ? WHERE id = ?", (int(pinned), habit_id))
        self.conn.commit()

    def reorder(self, habit_ids: list[int], pinned: set[int] | None = None) -> None:
        """Store the given order (first = top); optionally replace the pinned set too."""
        with self.conn:
            for position, habit_id in enumerate(habit_ids):
                if pinned is None:
                    self.conn.execute(
                        "UPDATE habits SET position = ? WHERE id = ?", (position, habit_id)
                    )
                else:
                    self.conn.execute(
                        "UPDATE habits SET position = ?, pinned = ? WHERE id = ?",
                        (position, int(habit_id in pinned), habit_id),
                    )

    def archive_habit(self, habit_id: int) -> None:
        self.conn.execute("UPDATE habits SET archived = 1 WHERE id = ?", (habit_id,))
        self.conn.commit()

    def habit_members(self, habit: Habit) -> frozenset[int]:
        if habit.owner_id is not None:
            return frozenset({habit.owner_id})
        pair = self.get_pair_by_id(habit.pair_id) if habit.pair_id is not None else None
        return pair.members if pair else frozenset()

    def can_access(self, habit: Habit, user_id: int) -> bool:
        return user_id in self.habit_members(habit)

    # ---- check-ins ---------------------------------------------------------

    def toggle_checkin(self, habit_id: int, user_id: int, day: date) -> bool:
        """Toggle the user's check-in for the day. Returns True if now checked."""
        key = (habit_id, user_id, day.isoformat())
        deleted = self.conn.execute(
            "DELETE FROM checkins WHERE habit_id = ? AND user_id = ? AND day = ?", key
        ).rowcount
        if not deleted:
            self.conn.execute("INSERT INTO checkins (habit_id, user_id, day) VALUES (?, ?, ?)", key)
        self.conn.commit()
        return not deleted

    def checkins(self, habit_id: int) -> dict[date, set[int]]:
        result: dict[date, set[int]] = {}
        rows = self.conn.execute(
            "SELECT user_id, day FROM checkins WHERE habit_id = ?", (habit_id,)
        )
        for row in rows:
            result.setdefault(date.fromisoformat(row["day"]), set()).add(row["user_id"])
        return result

    # ---- moods -------------------------------------------------------------

    def set_mood(self, pair_id: int, user_id: int, day: date, score: int) -> None:
        if not 1 <= score <= 5:
            raise ValueError("score must be between 1 and 5")
        self.conn.execute(
            "INSERT INTO moods (pair_id, user_id, day, score) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(pair_id, user_id, day) DO UPDATE SET score = excluded.score",
            (pair_id, user_id, day.isoformat(), score),
        )
        self.conn.commit()

    def moods(self, pair_id: int, since: date) -> dict[date, dict[int, int]]:
        result: dict[date, dict[int, int]] = {}
        rows = self.conn.execute(
            "SELECT user_id, day, score FROM moods WHERE pair_id = ? AND day >= ?",
            (pair_id, since.isoformat()),
        )
        for row in rows:
            result.setdefault(date.fromisoformat(row["day"]), {})[row["user_id"]] = row["score"]
        return result

    # ---- plans -------------------------------------------------------------

    @staticmethod
    def _plan(row: sqlite3.Row) -> Plan:
        return Plan(
            row["id"],
            row["pair_id"],
            row["author_id"],
            row["title"],
            row["emoji"],
            date.fromisoformat(row["day"]),
            row["time"],
            row["note"],
            row["status"],
        )

    def add_plan(
        self,
        pair_id: int,
        author_id: int,
        title: str,
        day: date,
        *,
        emoji: str = "📅",
        time: str | None = None,
        note: str | None = None,
    ) -> Plan:
        title = title.strip()[:60]
        if not title:
            raise ValueError("plan title is empty")
        note = (note or "").strip()[:200] or None
        cur = self.conn.execute(
            "INSERT INTO plans (pair_id, author_id, title, emoji, day, time, note) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (pair_id, author_id, title, emoji.strip()[:8] or "📅", day.isoformat(), time, note),
        )
        self.conn.commit()
        plan = self.get_plan(cur.lastrowid)
        assert plan is not None
        return plan

    def get_plan(self, plan_id: int) -> Plan | None:
        row = self.conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
        return self._plan(row) if row else None

    def plans_for_user(
        self, user_id: int, start: date | None = None, end: date | None = None
    ) -> list[Plan]:
        """Plans in all of the user's pairs, optionally limited to [start, end]."""
        query = (
            "SELECT plans.* FROM plans JOIN pairs ON pairs.id = plans.pair_id "
            "WHERE (pairs.owner_id = ? OR pairs.partner_id = ?)"
        )
        args: list[object] = [user_id, user_id]
        if start is not None:
            query += " AND plans.day >= ?"
            args.append(start.isoformat())
        if end is not None:
            query += " AND plans.day <= ?"
            args.append(end.isoformat())
        query += " ORDER BY plans.day, COALESCE(plans.time, '99:99'), plans.id"
        return [self._plan(row) for row in self.conn.execute(query, args)]

    def set_plan_status(self, plan_id: int, status: str) -> None:
        if status not in PLAN_STATUSES:
            raise ValueError(f"unknown plan status: {status}")
        self.conn.execute("UPDATE plans SET status = ? WHERE id = ?", (status, plan_id))
        self.conn.commit()

    def delete_plan(self, plan_id: int) -> None:
        self.conn.execute("DELETE FROM plans WHERE id = ?", (plan_id,))
        self.conn.commit()

    # ---- per-user section settings ------------------------------------------

    def mode_settings(self, user_id: int) -> dict[str, tuple[str | None, bool]]:
        """scope -> (custom name or None, hidden) for p/f/c."""
        result: dict[str, tuple[str | None, bool]] = {s: (None, False) for s in ("p", "f", "c")}
        rows = self.conn.execute(
            "SELECT scope, name, hidden FROM user_modes WHERE user_id = ?", (user_id,)
        )
        for row in rows:
            result[row["scope"]] = (row["name"] or None, bool(row["hidden"]))
        return result

    def set_mode_settings(
        self, user_id: int, scope: str, *, name: str | None = None, hidden: bool | None = None
    ) -> None:
        """Update a section's name ("" resets to default) and/or visibility."""
        if scope not in ("p", "f", "c"):
            raise ValueError(f"unknown scope: {scope}")
        if scope == "p" and hidden:
            raise ValueError("the personal section cannot be hidden")
        current_name, current_hidden = self.mode_settings(user_id)[scope]
        if name is not None:
            current_name = name.strip()[:20] or None
        if hidden is not None:
            current_hidden = hidden
        self.conn.execute(
            "INSERT INTO user_modes (user_id, scope, name, hidden) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id, scope) DO UPDATE SET name = excluded.name, hidden = excluded.hidden",
            (user_id, scope, current_name, int(current_hidden)),
        )
        self.conn.commit()
