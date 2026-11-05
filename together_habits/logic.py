"""Pure functions for streaks, statistics and couple compatibility."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, timedelta

MOOD_EMOJI = {1: "😞", 2: "😕", 3: "😐", 4: "🙂", 5: "😄"}


def completed_days(checkins: Mapping[date, set[int]], members: Iterable[int]) -> set[date]:
    """Days on which every member checked in."""
    required = set(members)
    if not required:
        return set()
    return {day for day, users in checkins.items() if required <= users}


def current_streak(days: set[date], today: date) -> int:
    """Consecutive completed days ending today (or yesterday, if today is still open)."""
    cursor = today if today in days else today - timedelta(days=1)
    streak = 0
    while cursor in days:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def best_streak(days: set[date]) -> int:
    best = 0
    for day in days:
        if day - timedelta(days=1) in days:
            continue
        length = 1
        while day + timedelta(days=length) in days:
            length += 1
        best = max(best, length)
    return best


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
