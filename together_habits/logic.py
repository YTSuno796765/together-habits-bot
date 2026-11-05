"""Pure functions for streaks, statistics and couple compatibility."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, timedelta

MOOD_EMOJI = {1: "😞", 2: "😕", 3: "😐", 4: "🙂", 5: "😄"}
EVERY_DAY = 0b1111111
WEEKDAYS_SHORT = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
ONE_DAY = timedelta(days=1)


def is_scheduled(schedule: int, day: date) -> bool:
    return bool(schedule >> day.weekday() & 1)


def schedule_label(schedule: int) -> str:
    if schedule & EVERY_DAY == EVERY_DAY:
        return "каждый день"
    if schedule == 0b0011111:
        return "по будням"
    if schedule == 0b1100000:
        return "по выходным"
    return ", ".join(name for bit, name in enumerate(WEEKDAYS_SHORT) if schedule >> bit & 1)


def completed_days(checkins: Mapping[date, set[int]], members: Iterable[int]) -> set[date]:
    """Days on which every member checked in."""
    required = set(members)
    if not required:
        return set()
    return {day for day, users in checkins.items() if required <= users}


def current_streak(
    days: set[date], today: date, schedule: int = EVERY_DAY, start: date | None = None
) -> int:
    """Completed scheduled days in a row, ending today (or earlier, if today is still open).

    Days outside the schedule neither count nor break the streak.
    """
    lower = start or (min(days) if days else today)
    cursor = today if today in days else today - ONE_DAY
    streak = 0
    while cursor >= lower:
        if is_scheduled(schedule, cursor):
            if cursor not in days:
                break
            streak += 1
        cursor -= ONE_DAY
    return streak


def best_streak(days: set[date], schedule: int = EVERY_DAY, start: date | None = None) -> int:
    if not days:
        return 0
    cursor = min([*days, start] if start else days)
    end = max(days)
    best = run = 0
    while cursor <= end:
        if is_scheduled(schedule, cursor):
            run = run + 1 if cursor in days else 0
            best = max(best, run)
        cursor += ONE_DAY
    return best


def day_status(
    day: date, today: date, days: set[date], schedule: int = EVERY_DAY, start: date | None = None
) -> str:
    """One of: done, today, missed, off (not scheduled / before start), future."""
    if day in days:
        return "done"
    if day > today:
        return "future" if is_scheduled(schedule, day) else "off"
    if not is_scheduled(schedule, day) or (start is not None and day < start):
        return "off"
    return "today" if day == today else "missed"


def week_of(today: date) -> list[date]:
    """Monday..Sunday of the week containing today."""
    monday = today - timedelta(days=today.weekday())
    return [monday + timedelta(days=offset) for offset in range(7)]


def due_days(
    today: date, window: int, schedule: int = EVERY_DAY, start: date | None = None,
    days: set[date] | None = None,
) -> list[date]:
    """Scheduled days in the window; today only counts once it is done."""
    first = today - timedelta(days=window - 1)
    if start is not None:
        first = max(first, start)
    result = []
    cursor = first
    while cursor <= today:
        if is_scheduled(schedule, cursor) and (cursor < today or (days is not None and cursor in days)):
            result.append(cursor)
        cursor += ONE_DAY
    return result


def completion(
    days: set[date], today: date, window: int, schedule: int = EVERY_DAY, start: date | None = None
) -> tuple[int, int]:
    """(done, due) scheduled days in the last `window` days."""
    due = due_days(today, window, schedule, start, days)
    return sum(1 for day in due if day in days), len(due)


def ratio(done: int, total: int) -> float | None:
    return done / total if total else None


def week_marks(days: set[date], today: date, n: int = 7) -> str:
    """A row like '✅⬜✅✅⬜✅✅' for the last n days, oldest first."""
    return "".join(
        "✅" if today - timedelta(days=offset) in days else "⬜" for offset in range(n - 1, -1, -1)
    )


def sync_rate(
    all_checkins: Iterable[Mapping[date, set[int]]],
    members: Iterable[int],
    today: date,
    window: int = 30,
) -> float | None:
    """Across habits: share of active habit-days (anyone checked in) when everybody did."""
    required = set(members)
    start = today - timedelta(days=window - 1)
    active = together = 0
    for checkins in all_checkins:
        for day, users in checkins.items():
            if start <= day <= today and users:
                active += 1
                together += required <= users
    if not active:
        return None
    return together / active


def mood_match(moods: Mapping[date, Mapping[int, int]], members: Iterable[int]) -> float | None:
    """Share of days both partners logged a mood within one point of each other."""
    required = set(members)
    shared = [scores for scores in moods.values() if required <= set(scores)]
    if len(required) < 2 or not shared:
        return None
    close = sum(1 for scores in shared if max(scores[m] for m in required) - min(scores[m] for m in required) <= 1)
    return close / len(shared)


def average_mood(moods: Mapping[date, Mapping[int, int]], user_id: int) -> float | None:
    scores = [day_scores[user_id] for day_scores in moods.values() if user_id in day_scores]
    if not scores:
        return None
    return sum(scores) / len(scores)


def compatibility_score(sync: float | None, match: float | None) -> int | None:
    """Combine habit sync and mood match into a 0-100 score."""
    parts = [value for value in (sync, match) if value is not None]
    if not parts:
        return None
    return round(100 * sum(parts) / len(parts))


def percent(value: float | None) -> str:
    return "—" if value is None else f"{round(value * 100)}%"
