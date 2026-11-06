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


TUE_THU_SAT = 0b0101010  # bits for вт, чт, сб


def test_schedule_label():
    assert logic.schedule_label(127) == "каждый день"
    assert logic.schedule_label(31) == "по будням"
    assert logic.schedule_label(96) == "по выходным"
    assert logic.schedule_label(TUE_THU_SAT) == "вт, чт, сб"


def test_streak_skips_unscheduled_days():
    # TODAY (2026-11-05) is a Thursday.
    tue, thu, sat_before, thu_before = d(2), d(0), d(5), d(7)
    days = {tue, thu, sat_before, thu_before}
    assert logic.current_streak(days, TODAY, TUE_THU_SAT) == 4
    # Today still open does not break the streak.
    assert logic.current_streak(days - {thu}, TODAY, TUE_THU_SAT) == 3
    # Missing a scheduled Tuesday breaks it.
    assert logic.current_streak(days - {tue}, TODAY, TUE_THU_SAT) == 1
    assert logic.best_streak(days, TUE_THU_SAT) == 4


def test_day_status_and_completion():
    start = d(3)
    days = {d(1)}
    assert logic.day_status(d(1), TODAY, days, 127, start) == "done"
    assert logic.day_status(d(2), TODAY, days, 127, start) == "missed"
    assert logic.day_status(d(5), TODAY, days, 127, start) == "off"  # before habit existed
    assert logic.day_status(TODAY, TODAY, days, 127, start) == "today"
    assert logic.day_status(TODAY, TODAY, days, TUE_THU_SAT - 0b1000, start) == "off"
    assert logic.day_status(TODAY + timedelta(days=1), TODAY, days, 127, start) == "future"
    # d(3), d(2), d(1) are due; today is not done yet so it is not counted.
    assert logic.completion(days, TODAY, 7, 127, start) == (1, 3)
    assert logic.completion(days | {TODAY}, TODAY, 7, 127, start) == (2, 4)
    assert logic.week_of(TODAY)[0].weekday() == 0 and len(logic.week_of(TODAY)) == 7


def test_avoid_habit_days_and_streak():
    start = d(5)
    slips = {d(2): {1}}
    clean = logic.habit_done_days("avoid", slips, {1}, TODAY, start)
    assert clean == {d(5), d(4), d(3), d(1), d(0)}
    assert logic.habit_streak("avoid", clean, TODAY, 127, start) == 2
    assert logic.best_streak(clean, 127, start) == 3
    # A slip by the partner breaks a shared "don't do" habit too.
    assert d(1) not in logic.habit_done_days("avoid", {d(1): {2}}, {1, 2}, TODAY, start)
    # A slip today resets the streak at once (no "day still open" grace).
    slipped_today = logic.habit_done_days("avoid", {TODAY: {1}}, {1}, TODAY, start)
    assert logic.habit_streak("avoid", slipped_today, TODAY, 127, start) == 0
    assert logic.habit_streak("do", slipped_today, TODAY, 127, start) == 5


def test_weekly_target_logic():
    # TODAY (2026-11-05) is a Thursday; its week starts on Monday 2026-11-02.
    monday = logic.week_start(TODAY)
    assert monday == d(3) and logic.weekly_label(3) == "3 раза в неделю"
    assert logic.weekly_label(1) == "1 раз в неделю" and logic.weekly_label(5) == "5 раз в неделю"
    start = monday - timedelta(days=14)  # habit started two weeks before this one
    prev2 = {start, start + timedelta(days=2), start + timedelta(days=4)}  # 3 in week -2
    prev1 = {monday - timedelta(days=6), monday - timedelta(days=1)}  # only 2 in week -1
    this = {d(2), d(1)}  # 2 so far this week
    days = prev2 | prev1 | this
    # Week -1 missed the target, so the streak is 0 until this week is met.
    assert logic.weekly_streak(days, TODAY, 3, start) == 0
    assert logic.weekly_streak(days | {TODAY}, TODAY, 3, start) == 1
    assert logic.weekly_streak(days, TODAY, 2, start) == 3
    assert logic.best_weekly_streak(days, 2, start) == 3
    # Due today while the target is not met yet (counting days before today).
    assert logic.weekly_due_today(days, TODAY, 3) is True
    assert logic.weekly_due_today(days, TODAY, 2) is False
    assert logic.due_today(127, TODAY, days | {TODAY}, 2) is True  # done today stays visible
    # Days without a check-in are never "missed".
    assert logic.day_status(d(3), TODAY, days, 127, start, 3) == "off"
    assert logic.day_status(TODAY, TODAY, days, 127, start, 3) == "today"
    assert logic.day_status(TODAY, TODAY, days, 127, start, 2) == "off"
    # Completion: finished weeks in full (extra check-ins capped), this week as done so far.
    assert logic.weekly_completion(days, TODAY, 21, 3, start) == (3 + 2 + 2, 3 + 3 + 2)


def test_weekly_first_week_is_prorated():
    # Habit created on Saturday: only 2 days left that week, so a target of 3 becomes 2.
    sat = logic.week_start(TODAY) - timedelta(days=2)
    days = {sat, sat + timedelta(days=1)}
    assert logic.week_goal(logic.week_start(sat), 3, sat) == 2
    assert logic.weekly_streak(days, TODAY, 3, sat) == 1
