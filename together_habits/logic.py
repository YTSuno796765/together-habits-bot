"""Pure functions for streaks, statistics and couple compatibility."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, timedelta

MOOD_EMOJI = {1: "😞", 2: "😕", 3: "😐", 4: "🙂", 5: "😄"}
EVERY_DAY = 0b1111111
WEEKDAYS_SHORT = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
ONE_DAY = timedelta(days=1)


MONTHS_GENITIVE = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


def human_date(day: date, time: str | None = None) -> str:
    """'пт, 14 ноября · 19:00'."""
    text = f"{WEEKDAYS_SHORT[day.weekday()]}, {day.day} {MONTHS_GENITIVE[day.month - 1]}"
    return f"{text} · {time}" if time else text


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


def avoid_clean_days(
    slips: Mapping[date, set[int]],
    members: Iterable[int],
    today: date,
    start: date | None,
) -> set[date]:
    """For "don't do" habits: every day from start to today without a slip by any member."""
    who = set(members)
    first = start or today
    result = set()
    cursor = first
    while cursor <= today:
        if not (slips.get(cursor, set()) & who):
            result.add(cursor)
        cursor += ONE_DAY
    return result


def habit_done_days(
    kind: str,
    checkins: Mapping[date, set[int]],
    members: Iterable[int],
    today: date,
    start: date | None,
) -> set[date]:
    """Days that count as success: all members checked in, or (avoid) nobody slipped."""
    if kind == "avoid":
        return avoid_clean_days(checkins, members, today, start)
    return completed_days(checkins, members)


def with_freezes(
    kind: str, done: set[date], frozen: Iterable[date], weekly_target: int | None = None
) -> set[date]:
    """Frozen days count as done for day-based to-do habits (not weekly / don't-do)."""
    if kind != "do" or weekly_target:
        return set(done)
    return set(done) | set(frozen)


def goal_progress(kind: str, real_done: set[date], streak: int) -> int:
    """Progress towards a habit goal: days done, or days clean in a row for don't-do habits."""
    return streak if kind == "avoid" else len(real_done)


def freezable_days(
    days: set[date],
    frozen: Iterable[date],
    today: date,
    schedule: int = EVERY_DAY,
    start: date | None = None,
    lookback: int = 7,
) -> list[date]:
    """Missed scheduled days in the last `lookback` days (not today) that may be frozen."""
    frozen_set = set(frozen)
    result = []
    for offset in range(1, lookback + 1):
        day = today - timedelta(days=offset)
        if start is not None and day < start:
            break
        if is_scheduled(schedule, day) and day not in days and day not in frozen_set:
            result.append(day)
    return result


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


def habit_streak(
    kind: str,
    days: set[date],
    today: date,
    schedule: int = EVERY_DAY,
    start: date | None = None,
    weekly_target: int | None = None,
) -> int:
    """Current streak (days, or weeks for "N times a week" habits).

    For "don't do" habits a slip today resets it to zero right away.
    """
    if weekly_target:
        return weekly_streak(days, today, weekly_target, start)
    if kind == "avoid" and today not in days and (start is None or start <= today):
        return 0
    return current_streak(days, today, schedule, start)


def best_streak(
    days: set[date],
    schedule: int = EVERY_DAY,
    start: date | None = None,
    weekly_target: int | None = None,
) -> int:
    if weekly_target:
        return best_weekly_streak(days, weekly_target, start)
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
    day: date,
    today: date,
    days: set[date],
    schedule: int = EVERY_DAY,
    start: date | None = None,
    weekly_target: int | None = None,
) -> str:
    """One of: done, today, missed, off (not scheduled / before start), future."""
    if day in days:
        return "done"
    if weekly_target:
        # Any day may count; a day without a check-in is never "missed".
        if day > today:
            return "future"
        if day == today and weekly_due_today(days, today, weekly_target):
            return "today"
        return "off"
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
    days: set[date],
    today: date,
    window: int,
    schedule: int = EVERY_DAY,
    start: date | None = None,
    kind: str = "do",
    weekly_target: int | None = None,
) -> tuple[int, int]:
    """(done, due) scheduled days in the last `window` days.

    A to-do habit's today counts once done; a "don't do" habit's today always counts
    (it is a success until a slip is marked). "N times a week" habits are counted
    in check-ins against their weekly target.
    """
    if weekly_target:
        return weekly_completion(days, today, window, weekly_target, start)
    due = due_days(today, window, schedule, start, days)
    if kind == "avoid" and is_scheduled(schedule, today) and (start is None or start <= today):
        if today not in due:
            due.append(today)
    return sum(1 for day in due if day in days), len(due)


# ---- "N times a week" habits ----------------------------------------------------


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def weekly_label(target: int) -> str:
    return f"{target} {'раз' if target in (1, 5, 6) or target > 4 else 'раза'} в неделю"


def week_count(days: set[date], monday: date, until: date | None = None) -> int:
    """Check-ins in the week starting `monday` (optionally only up to `until`, exclusive)."""
    end = monday + timedelta(days=7)
    if until is not None:
        end = min(end, until)
    return sum(1 for day in days if monday <= day < end)


def week_goal(monday: date, target: int, start: date | None) -> int:
    """The target for a week; the habit's first week may be shorter than the target."""
    if start is not None and monday < start <= monday + timedelta(days=6):
        return min(target, (monday + timedelta(days=7) - start).days)
    return target


def weekly_due_today(days: set[date], today: date, target: int) -> bool:
    """Still needed this week, counting check-ins before today."""
    monday = week_start(today)
    return week_count(days, monday, until=today) < target


def weekly_streak(days: set[date], today: date, target: int, start: date | None = None) -> int:
    """Weeks in a row with the target met; the current week only adds once it is met."""
    monday = week_start(today)
    first = week_start(start) if start else (week_start(min(days)) if days else monday)
    streak = 0
    if week_count(days, monday) >= week_goal(monday, target, start):
        streak = 1
    monday -= timedelta(days=7)
    while monday >= first:
        if week_count(days, monday) < week_goal(monday, target, start):
            break
        streak += 1
        monday -= timedelta(days=7)
    return streak


def best_weekly_streak(days: set[date], target: int, start: date | None = None) -> int:
    if not days:
        return 0
    monday = week_start(min([*days, start]) if start else min(days))
    last = week_start(max(days))
    best = run = 0
    while monday <= last:
        run = run + 1 if week_count(days, monday) >= week_goal(monday, target, start) else 0
        best = max(best, run)
        monday += timedelta(days=7)
    return best


def weekly_completion(
    days: set[date], today: date, window: int, target: int, start: date | None = None
) -> tuple[int, int]:
    """(done, due) check-ins for weeks touching the window; extra check-ins don't count.

    Finished weeks are due in full; the current week only counts what is already done.
    """
    first = today - timedelta(days=window - 1)
    if start is not None:
        first = max(first, start)
    done = due = 0
    monday = week_start(first)
    current = week_start(today)
    while monday <= current:
        goal = week_goal(monday, target, start)
        got = min(week_count(days, monday), goal)
        done += got
        due += got if monday == current else goal
        monday += timedelta(days=7)
    return done, due


def habit_label(schedule: int, weekly_target: int | None = None) -> str:
    return weekly_label(weekly_target) if weekly_target else schedule_label(schedule)


def streak_unit(weekly_target: int | None = None) -> str:
    return "нед." if weekly_target else "дн."


def due_today(
    schedule: int, today: date, days: set[date], weekly_target: int | None = None
) -> bool:
    """Should a to-do habit be done today? (weekly: target not met before today)."""
    if weekly_target:
        return weekly_due_today(days, today, weekly_target) or today in days
    return is_scheduled(schedule, today)


# ---- plans -------------------------------------------------------------------------


def plan_occurs(first_day: date, repeat: str | None, day: date) -> bool:
    if repeat == "weekly":
        return day >= first_day and (day - first_day).days % 7 == 0
    return day == first_day


def plan_occurrences(first_day: date, repeat: str | None, start: date, end: date) -> list[date]:
    """Dates of a (possibly weekly) plan within [start, end]."""
    if repeat != "weekly":
        return [first_day] if start <= first_day <= end else []
    if first_day > end:
        return []
    offset = max(0, (start - first_day).days)
    cursor = first_day + timedelta(days=-(-offset // 7) * 7)
    result = []
    while cursor <= end:
        result.append(cursor)
        cursor += timedelta(days=7)
    return result


# ---- challenges -------------------------------------------------------------------


def challenge_scores(
    kind: str,
    start: date,
    end: date,
    today: date,
    marks: Mapping[int, set[date]],
    members: Iterable[int],
) -> dict[int, int]:
    """Points so far: marked days ("do") or days without a slip ("avoid")."""
    last = min(today, end)
    elapsed = (last - start).days + 1 if last >= start else 0
    scores = {}
    for user in members:
        in_range = {d for d in marks.get(user, set()) if start <= d <= last}
        scores[user] = len(in_range) if kind == "do" else max(0, elapsed - len(in_range))
    return scores


def challenge_winners(scores: Mapping[int, int]) -> list[int]:
    if not scores:
        return []
    best = max(scores.values())
    return [user for user, score in scores.items() if score == best] if best > 0 else []


def completion_between(
    days: set[date],
    first: date,
    last: date,
    schedule: int = EVERY_DAY,
    start: date | None = None,
    weekly_target: int | None = None,
) -> tuple[int, int]:
    """(done, due) for a finished period [first, last] (every day in it already passed)."""
    if start is not None:
        first = max(first, start)
    if first > last:
        return 0, 0
    if weekly_target:
        done = due = 0
        monday = week_start(first)
        while monday <= last:
            goal = week_goal(monday, weekly_target, start)
            done += min(week_count(days, monday), goal)
            due += goal
            monday += timedelta(days=7)
        return done, due
    done = due = 0
    cursor = first
    while cursor <= last:
        if is_scheduled(schedule, cursor):
            due += 1
            done += cursor in days
        cursor += ONE_DAY
    return done, due


# ---- analytics -----------------------------------------------------------------------


def mood_effect(
    moods: Mapping[date, int],
    done_days: set[date],
    due: Iterable[date],
    min_samples: int = 3,
) -> tuple[float, int, int] | None:
    """Average mood on due days with the habit done minus days without it.

    Returns (difference, samples_with, samples_without) or None if there is too little data.
    """
    with_habit: list[int] = []
    without: list[int] = []
    for day in due:
        if day not in moods:
            continue
        (with_habit if day in done_days else without).append(moods[day])
    if len(with_habit) < min_samples or len(without) < min_samples:
        return None
    diff = sum(with_habit) / len(with_habit) - sum(without) / len(without)
    return round(diff, 1), len(with_habit), len(without)


def heat_level(done: int, due: int) -> int:
    """0 (nothing due / nothing done) .. 4 (everything done) for the year heatmap."""
    if not due or not done:
        return 0
    share = done / due
    return 4 if share >= 1 else 3 if share >= 0.66 else 2 if share >= 0.33 else 1


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
