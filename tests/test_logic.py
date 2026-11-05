from datetime import date, timedelta

from together_habits import logic

TODAY = date(2026, 11, 5)


def d(offset: int) -> date:
    return TODAY - timedelta(days=offset)


def test_completed_days_requires_all_members():
    checkins = {d(0): {1, 2}, d(1): {1}, d(2): {2, 1}}
    assert logic.completed_days(checkins, {1, 2}) == {d(0), d(2)}
    assert logic.completed_days(checkins, {1}) == {d(0), d(1), d(2)}


def test_current_streak_keeps_open_today():
    days = {d(1), d(2), d(3), d(5)}
    assert logic.current_streak(days, TODAY) == 3
    assert logic.current_streak(days | {d(0)}, TODAY) == 4
    assert logic.current_streak({d(2)}, TODAY) == 0


def test_best_streak():
    assert logic.best_streak({d(0), d(1), d(5), d(6), d(7), d(8)}) == 4
    assert logic.best_streak(set()) == 0


def test_week_marks_oldest_first():
    assert logic.week_marks({d(0), d(6)}, TODAY) == "✅⬜⬜⬜⬜⬜✅"


def test_sync_rate_across_habits():
    first = {d(0): {1, 2}, d(1): {1}}
    second = {d(0): {2}, d(40): {1, 2}}  # d(40) is outside the window
    assert logic.sync_rate([first, second], {1, 2}, TODAY) == 1 / 3
    assert logic.sync_rate([], {1, 2}, TODAY) is None


def test_mood_match_and_compatibility():
    moods = {d(0): {1: 5, 2: 4}, d(1): {1: 1, 2: 5}, d(2): {1: 3}}
    assert logic.mood_match(moods, {1, 2}) == 0.5
    assert logic.average_mood(moods, 1) == 3
    assert logic.compatibility_score(1.0, 0.5) == 75
    assert logic.compatibility_score(None, None) is None
