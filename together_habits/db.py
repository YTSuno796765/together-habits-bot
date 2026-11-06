"""SQLite storage for users, pairs, habits, check-ins and moods."""

from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
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
    weekly_target INTEGER,
    goal_days INTEGER,
    target_amount INTEGER,
    unit TEXT,
    step INTEGER,
    remind_at TEXT,
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
CREATE TABLE IF NOT EXISTS amounts (
    habit_id INTEGER NOT NULL REFERENCES habits(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    day TEXT NOT NULL,
    value INTEGER NOT NULL,
    PRIMARY KEY (habit_id, user_id, day)
);
CREATE TABLE IF NOT EXISTS notes (
    habit_id INTEGER NOT NULL REFERENCES habits(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    day TEXT NOT NULL,
    text TEXT NOT NULL,
    PRIMARY KEY (habit_id, user_id, day)
);
CREATE TABLE IF NOT EXISTS freezes (
    habit_id INTEGER NOT NULL REFERENCES habits(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    day TEXT NOT NULL,
    PRIMARY KEY (habit_id, day)
);
CREATE TABLE IF NOT EXISTS challenges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    creator_id INTEGER NOT NULL REFERENCES users(id),
    title TEXT NOT NULL,
    emoji TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('do', 'avoid')),
    start TEXT NOT NULL,
    days INTEGER NOT NULL,
    stake TEXT,
    invite_code TEXT NOT NULL UNIQUE,
    finished INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS challenge_members (
    challenge_id INTEGER NOT NULL REFERENCES challenges(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    PRIMARY KEY (challenge_id, user_id)
);
CREATE TABLE IF NOT EXISTS challenge_marks (
    challenge_id INTEGER NOT NULL REFERENCES challenges(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    day TEXT NOT NULL,
    PRIMARY KEY (challenge_id, user_id, day)
);
CREATE TABLE IF NOT EXISTS nudges (
    habit_id INTEGER NOT NULL REFERENCES habits(id) ON DELETE CASCADE,
    sender_id INTEGER NOT NULL REFERENCES users(id),
    day TEXT NOT NULL,
    PRIMARY KEY (habit_id, sender_id, day)
);
CREATE TABLE IF NOT EXISTS reactions (
    habit_id INTEGER NOT NULL REFERENCES habits(id) ON DELETE CASCADE,
    from_id INTEGER NOT NULL REFERENCES users(id),
    day TEXT NOT NULL,
    emoji TEXT NOT NULL,
    PRIMARY KEY (habit_id, from_id, day)
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


FREEZES_PER_MONTH = 2
MAX_NOTE_LENGTH = 300


def validate_habit_options(
    kind: str,
    *,
    weekly_target: int | None = None,
    goal_days: int | None = None,
    target_amount: int | None = None,
    unit: str | None = None,
    step: int | None = None,
    remind_at: str | None = None,
) -> dict[str, object]:
    """Check optional habit settings; returns normalised values or raises ValueError."""

    def whole(value: object, low: int, high: int, name: str) -> int | None:
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
            raise ValueError(f"{name} must be {low}..{high}")
        return value

    weekly_target = whole(weekly_target, 1, 6, "weekly target")
    goal_days = whole(goal_days, 1, 3650, "goal")
    target_amount = whole(target_amount, 1, 1_000_000, "target amount")
    step = whole(step, 1, 1_000_000, "step")
    if kind != "do" and (weekly_target or target_amount):
        raise ValueError("weekly target and amounts are for to-do habits")
    if weekly_target and target_amount:
        raise ValueError("a habit is either weekly or quantitative")
    unit = (unit or "").strip()[:12] or None if target_amount else None
    step = (step or 1) if target_amount else None
    if remind_at is not None:
        text = str(remind_at)
        if len(text) != 5 or text[2] != ":" or not (text[:2] + text[3:]).isdigit() \
                or int(text[:2]) > 23 or int(text[3:]) > 59:
            raise ValueError("remind_at must be HH:MM")
        remind_at = text
    return {
        "weekly_target": weekly_target,
        "goal_days": goal_days,
        "target_amount": target_amount,
        "unit": unit,
        "step": step,
        "remind_at": remind_at,
    }


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
    weekly_target: int | None = None  # "N times a week" instead of fixed weekdays
    goal_days: int | None = None  # e.g. 100 days; progress shown on the card
    target_amount: int | None = None  # quantitative habit: daily amount to reach
    unit: str | None = None  # "стаканов", "мин", "шагов"
    step: int | None = None  # how much one "+" adds
    remind_at: str | None = None  # "HH:MM" personal reminder for this habit
    archived: bool = False

    @property
    def is_amount(self) -> bool:
        return bool(self.target_amount)

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


@dataclass(frozen=True)
class Challenge:
    id: int
    creator_id: int
    title: str
    emoji: str
    kind: str
    start: date
    days: int
    stake: str | None
    invite_code: str
    finished: bool

    @property
    def end(self) -> date:
        return self.start + timedelta(days=self.days - 1)


CHALLENGE_LENGTHS = (7, 14, 21, 30)
MAX_CHALLENGE_MEMBERS = 10
REACTIONS = ("🔥", "👏", "💪", "❤️", "😎")


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
        if "weekly_target" not in columns:
            self.conn.execute("ALTER TABLE habits ADD COLUMN weekly_target INTEGER")
        for column, ddl in (
            ("goal_days", "INTEGER"),
            ("target_amount", "INTEGER"),
            ("unit", "TEXT"),
            ("step", "INTEGER"),
            ("remind_at", "TEXT"),
        ):
            if column not in columns:
                self.conn.execute(f"ALTER TABLE habits ADD COLUMN {column} {ddl}")

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
            row["weekly_target"],
            row["goal_days"],
            row["target_amount"],
            row["unit"],
            row["step"],
            row["remind_at"],
            bool(row["archived"]),
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
        weekly_target: int | None = None,
        goal_days: int | None = None,
        target_amount: int | None = None,
        unit: str | None = None,
        step: int | None = None,
        remind_at: str | None = None,
    ) -> Habit:
        if kind not in HABIT_KINDS:
            raise ValueError(f"unknown habit kind: {kind}")
        extras = validate_habit_options(
            kind,
            weekly_target=weekly_target,
            goal_days=goal_days,
            target_amount=target_amount,
            unit=unit,
            step=step,
            remind_at=remind_at,
        )
        if weekly_target is not None:
            schedule = EVERY_DAY  # any day may count
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
            "INSERT INTO habits (title, emoji, owner_id, pair_id, schedule, created_at, kind, "
            "position, weekly_target, goal_days, target_amount, unit, step, remind_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                title, emoji, owner_id, pair_id, schedule, created_at, kind, row["next"],
                extras["weekly_target"], extras["goal_days"], extras["target_amount"],
                extras["unit"], extras["step"], extras["remind_at"],
            ),
        )
        self.conn.commit()
        habit = self.get_habit(cur.lastrowid)
        assert habit is not None
        return habit

    def get_habit(self, habit_id: int, *, include_archived: bool = False) -> Habit | None:
        query = "SELECT * FROM habits WHERE id = ?" + ("" if include_archived else " AND archived = 0")
        row = self.conn.execute(query, (habit_id,)).fetchone()
        return self._habit(row) if row else None

    def update_habit(
        self,
        habit_id: int,
        *,
        title: str | None = None,
        emoji: str | None = None,
        schedule: int | None = None,
        weekly_target: int | None | object = ...,
        goal_days: int | None | object = ...,
        target_amount: int | None | object = ...,
        unit: str | None | object = ...,
        step: int | None | object = ...,
        remind_at: str | None | object = ...,
    ) -> Habit:
        """Change a habit's settings. `...` keeps a value; None clears an optional one."""
        habit = self.get_habit(habit_id)
        if habit is None:
            raise ValueError("habit not found")
        merged = {
            "weekly_target": habit.weekly_target if weekly_target is ... else weekly_target,
            "goal_days": habit.goal_days if goal_days is ... else goal_days,
            "target_amount": habit.target_amount if target_amount is ... else target_amount,
            "unit": habit.unit if unit is ... else unit,
            "step": habit.step if step is ... else step,
            "remind_at": habit.remind_at if remind_at is ... else remind_at,
        }
        extras = validate_habit_options(habit.kind, **merged)  # type: ignore[arg-type]
        new_title = habit.title if title is None else title.strip()[:40]
        if not new_title:
            raise ValueError("habit title is empty")
        new_emoji = habit.emoji if emoji is None else (emoji.strip()[:8] or "⭐")
        new_schedule = habit.schedule if schedule is None else schedule
        if extras["weekly_target"] is not None:
            new_schedule = EVERY_DAY
        if not 1 <= new_schedule <= EVERY_DAY:
            raise ValueError("schedule must select at least one weekday")
        self.conn.execute(
            "UPDATE habits SET title = ?, emoji = ?, schedule = ?, weekly_target = ?, goal_days = ?, "
            "target_amount = ?, unit = ?, step = ?, remind_at = ? WHERE id = ?",
            (
                new_title, new_emoji, new_schedule, extras["weekly_target"], extras["goal_days"],
                extras["target_amount"], extras["unit"], extras["step"], extras["remind_at"], habit_id,
            ),
        )
        self.conn.commit()
        updated = self.get_habit(habit_id)
        assert updated is not None
        return updated

    def list_archived(self, *, owner_id: int | None = None, pair_id: int | None = None) -> list[Habit]:
        column, value = ("owner_id", owner_id) if owner_id is not None else ("pair_id", pair_id)
        rows = self.conn.execute(
            f"SELECT * FROM habits WHERE {column} = ? AND archived = 1 ORDER BY id DESC", (value,)
        )
        return [self._habit(row) for row in rows]

    def restore_habit(self, habit_id: int) -> None:
        habit = self.get_habit(habit_id, include_archived=True)
        if habit is None:
            raise ValueError("habit not found")
        active = self.list_habits(owner_id=habit.owner_id, pair_id=habit.pair_id)
        if len(active) >= MAX_HABITS_PER_SCOPE:
            raise ValueError("too many habits")
        self.conn.execute("UPDATE habits SET archived = 0 WHERE id = ?", (habit_id,))
        self.conn.commit()

    def delete_habit_forever(self, habit_id: int) -> None:
        self.conn.execute("DELETE FROM habits WHERE id = ?", (habit_id,))
        self.conn.commit()

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

    # ---- amounts (quantitative habits) ---------------------------------------

    def amounts(self, habit_id: int) -> dict[date, dict[int, int]]:
        result: dict[date, dict[int, int]] = {}
        rows = self.conn.execute(
            "SELECT user_id, day, value FROM amounts WHERE habit_id = ?", (habit_id,)
        )
        for row in rows:
            result.setdefault(date.fromisoformat(row["day"]), {})[row["user_id"]] = row["value"]
        return result

    def set_amount(self, habit: Habit, user_id: int, day: date, value: int) -> bool:
        """Store the day's amount; keeps the check-in in sync. Returns True if the target is met."""
        if not habit.target_amount:
            raise ValueError("not a quantitative habit")
        value = max(0, min(int(value), 10_000_000))
        key = (habit.id, user_id, day.isoformat())
        with self.conn:
            if value:
                self.conn.execute(
                    "INSERT INTO amounts (habit_id, user_id, day, value) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(habit_id, user_id, day) DO UPDATE SET value = excluded.value",
                    (*key, value),
                )
            else:
                self.conn.execute(
                    "DELETE FROM amounts WHERE habit_id = ? AND user_id = ? AND day = ?", key
                )
            met = value >= habit.target_amount
            if met:
                self.conn.execute(
                    "INSERT OR IGNORE INTO checkins (habit_id, user_id, day) VALUES (?, ?, ?)", key
                )
            else:
                self.conn.execute(
                    "DELETE FROM checkins WHERE habit_id = ? AND user_id = ? AND day = ?", key
                )
        return met

    # ---- notes ---------------------------------------------------------------

    def set_note(self, habit_id: int, user_id: int, day: date, text: str) -> None:
        text = text.strip()[:MAX_NOTE_LENGTH]
        key = (habit_id, user_id, day.isoformat())
        if text:
            self.conn.execute(
                "INSERT INTO notes (habit_id, user_id, day, text) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(habit_id, user_id, day) DO UPDATE SET text = excluded.text",
                (*key, text),
            )
        else:
            self.conn.execute("DELETE FROM notes WHERE habit_id = ? AND user_id = ? AND day = ?", key)
        self.conn.commit()

    def notes(self, habit_id: int, limit: int = 30) -> list[tuple[date, int, str]]:
        rows = self.conn.execute(
            "SELECT day, user_id, text FROM notes WHERE habit_id = ? ORDER BY day DESC LIMIT ?",
            (habit_id, limit),
        )
        return [(date.fromisoformat(r["day"]), r["user_id"], r["text"]) for r in rows]

    # ---- streak freezes --------------------------------------------------------

    def freezes(self, habit_id: int) -> dict[date, int]:
        """day -> user who spent the freeze."""
        rows = self.conn.execute("SELECT day, user_id FROM freezes WHERE habit_id = ?", (habit_id,))
        return {date.fromisoformat(r["day"]): r["user_id"] for r in rows}

    def freezes_used(self, user_id: int, month: date) -> int:
        prefix = month.strftime("%Y-%m")
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM freezes WHERE user_id = ? AND substr(day, 1, 7) = ?",
            (user_id, prefix),
        ).fetchone()
        return row["n"]

    def add_freeze(self, habit_id: int, user_id: int, day: date) -> None:
        if self.freezes_used(user_id, day) >= FREEZES_PER_MONTH:
            raise ValueError("no freezes left this month")
        try:
            self.conn.execute(
                "INSERT INTO freezes (habit_id, user_id, day) VALUES (?, ?, ?)",
                (habit_id, user_id, day.isoformat()),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("this day is already frozen") from exc
        self.conn.commit()

    def remove_freeze(self, habit_id: int, user_id: int, day: date) -> bool:
        deleted = self.conn.execute(
            "DELETE FROM freezes WHERE habit_id = ? AND user_id = ? AND day = ?",
            (habit_id, user_id, day.isoformat()),
        ).rowcount
        self.conn.commit()
        return bool(deleted)

    def set_checkin(self, habit_id: int, user_id: int, day: date, checked: bool) -> None:
        key = (habit_id, user_id, day.isoformat())
        if checked:
            self.conn.execute("INSERT OR IGNORE INTO checkins (habit_id, user_id, day) VALUES (?, ?, ?)", key)
        else:
            self.conn.execute("DELETE FROM checkins WHERE habit_id = ? AND user_id = ? AND day = ?", key)
        self.conn.commit()

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

    # ---- challenges (with a friend, a partner or a whole group) ---------------

    @staticmethod
    def _challenge(row: sqlite3.Row) -> Challenge:
        return Challenge(
            row["id"], row["creator_id"], row["title"], row["emoji"], row["kind"],
            date.fromisoformat(row["start"]), row["days"], row["stake"], row["invite_code"],
            bool(row["finished"]),
        )

    def create_challenge(
        self,
        creator_id: int,
        title: str,
        *,
        emoji: str = "🏆",
        kind: str = "do",
        days: int = 7,
        start: date,
        stake: str | None = None,
    ) -> Challenge:
        title = title.strip()[:40]
        if not title:
            raise ValueError("challenge title is empty")
        if kind not in HABIT_KINDS or days not in CHALLENGE_LENGTHS:
            raise ValueError("bad challenge kind or length")
        stake = (stake or "").strip()[:80] or None
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO challenges (creator_id, title, emoji, kind, start, days, stake, invite_code) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (creator_id, title, emoji.strip()[:8] or "🏆", kind, start.isoformat(), days, stake,
                 secrets.token_urlsafe(8)),
            )
            self.conn.execute(
                "INSERT INTO challenge_members (challenge_id, user_id) VALUES (?, ?)", (cur.lastrowid, creator_id)
            )
        challenge = self.get_challenge(cur.lastrowid)
        assert challenge is not None
        return challenge

    def get_challenge(self, challenge_id: int) -> Challenge | None:
        row = self.conn.execute("SELECT * FROM challenges WHERE id = ?", (challenge_id,)).fetchone()
        return self._challenge(row) if row else None

    def challenge_by_code(self, code: str) -> Challenge | None:
        row = self.conn.execute("SELECT * FROM challenges WHERE invite_code = ?", (code,)).fetchone()
        return self._challenge(row) if row else None

    def challenge_members(self, challenge_id: int) -> list[int]:
        rows = self.conn.execute(
            "SELECT user_id FROM challenge_members WHERE challenge_id = ? ORDER BY rowid", (challenge_id,)
        )
        return [r["user_id"] for r in rows]

    def join_challenge(self, challenge_id: int, user_id: int) -> bool:
        """Add a member; False if already in. Raises ValueError when full or finished."""
        challenge = self.get_challenge(challenge_id)
        if challenge is None or challenge.finished:
            raise ValueError("challenge is over")
        members = self.challenge_members(challenge_id)
        if user_id in members:
            return False
        if len(members) >= MAX_CHALLENGE_MEMBERS:
            raise ValueError("challenge is full")
        self.conn.execute(
            "INSERT INTO challenge_members (challenge_id, user_id) VALUES (?, ?)", (challenge_id, user_id)
        )
        self.conn.commit()
        return True

    def leave_challenge(self, challenge_id: int, user_id: int) -> None:
        with self.conn:
            self.conn.execute(
                "DELETE FROM challenge_members WHERE challenge_id = ? AND user_id = ?", (challenge_id, user_id)
            )
            self.conn.execute(
                "DELETE FROM challenge_marks WHERE challenge_id = ? AND user_id = ?", (challenge_id, user_id)
            )

    def delete_challenge(self, challenge_id: int) -> None:
        self.conn.execute("DELETE FROM challenges WHERE id = ?", (challenge_id,))
        self.conn.commit()

    def user_challenges(self, user_id: int) -> list[Challenge]:
        rows = self.conn.execute(
            "SELECT challenges.* FROM challenges JOIN challenge_members m ON m.challenge_id = challenges.id "
            "WHERE m.user_id = ? ORDER BY challenges.finished, challenges.start DESC, challenges.id DESC",
            (user_id,),
        )
        return [self._challenge(r) for r in rows]

    def toggle_challenge_mark(self, challenge_id: int, user_id: int, day: date) -> bool:
        key = (challenge_id, user_id, day.isoformat())
        deleted = self.conn.execute(
            "DELETE FROM challenge_marks WHERE challenge_id = ? AND user_id = ? AND day = ?", key
        ).rowcount
        if not deleted:
            self.conn.execute("INSERT INTO challenge_marks (challenge_id, user_id, day) VALUES (?, ?, ?)", key)
        self.conn.commit()
        return not deleted

    def challenge_marks(self, challenge_id: int) -> dict[int, set[date]]:
        result: dict[int, set[date]] = {}
        rows = self.conn.execute(
            "SELECT user_id, day FROM challenge_marks WHERE challenge_id = ?", (challenge_id,)
        )
        for r in rows:
            result.setdefault(r["user_id"], set()).add(date.fromisoformat(r["day"]))
        return result

    def set_challenge_finished(self, challenge_id: int) -> None:
        self.conn.execute("UPDATE challenges SET finished = 1 WHERE id = ?", (challenge_id,))
        self.conn.commit()

    def unfinished_challenges_ended_before(self, day: date) -> list[Challenge]:
        rows = self.conn.execute("SELECT * FROM challenges WHERE finished = 0")
        return [c for c in (self._challenge(r) for r in rows) if c.end < day]

    # ---- nudges & reactions -----------------------------------------------------

    def add_nudge(self, habit_id: int, sender_id: int, day: date) -> bool:
        """Record a nudge; False if this sender already nudged for this habit today."""
        try:
            self.conn.execute(
                "INSERT INTO nudges (habit_id, sender_id, day) VALUES (?, ?, ?)",
                (habit_id, sender_id, day.isoformat()),
            )
        except sqlite3.IntegrityError:
            return False
        self.conn.commit()
        return True

    def nudged(self, habit_id: int, sender_id: int, day: date) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM nudges WHERE habit_id = ? AND sender_id = ? AND day = ?",
            (habit_id, sender_id, day.isoformat()),
        ).fetchone()
        return row is not None

    def set_reaction(self, habit_id: int, from_id: int, day: date, emoji: str | None) -> None:
        if emoji is None:
            self.conn.execute(
                "DELETE FROM reactions WHERE habit_id = ? AND from_id = ? AND day = ?",
                (habit_id, from_id, day.isoformat()),
            )
        else:
            if emoji not in REACTIONS:
                raise ValueError("unknown reaction")
            self.conn.execute(
                "INSERT INTO reactions (habit_id, from_id, day, emoji) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(habit_id, from_id, day) DO UPDATE SET emoji = excluded.emoji",
                (habit_id, from_id, day.isoformat(), emoji),
            )
        self.conn.commit()

    def reactions(self, habit_id: int, day: date) -> dict[int, str]:
        """from_user -> emoji for one day."""
        rows = self.conn.execute(
            "SELECT from_id, emoji FROM reactions WHERE habit_id = ? AND day = ?", (habit_id, day.isoformat())
        )
        return {r["from_id"]: r["emoji"] for r in rows}
