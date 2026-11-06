"""Texts for the morning digest and the weekly report, and the CSV export."""

from __future__ import annotations

import csv
import io
from datetime import date, timedelta
from html import escape
from typing import TYPE_CHECKING

from together_habits import logic
from together_habits.texts import SCOPES

if TYPE_CHECKING:
    from together_habits.db import Habit, Pair
    from together_habits.handlers import Handlers


def _visible_views(handlers: Handlers, user_id: int) -> list[tuple[str, Pair | None, list[Habit]]]:
    settings = handlers.db.mode_settings(user_id)
    views = []
    for scope in SCOPES:
        if settings[scope][1]:  # hidden section
            continue
        view = handlers._view(user_id, scope)
        if view is not None:
            views.append((scope, view.pair, view.habits))
    return views


def _done_days(handlers: Handlers, habit: Habit, pair: Pair | None, user_id: int, today: date) -> set[date]:
    members = pair.members if pair else frozenset({user_id})
    real = logic.habit_done_days(habit.kind, handlers.db.checkins(habit.id), members, today, habit.created)
    return logic.with_freezes(habit.kind, real, handlers.db.freezes(habit.id), habit.weekly_target)


def morning_digest(handlers: Handlers, user_id: int, today: date) -> str | None:
    """'Plan for today': habits still to do, plans, challenges, top tasks. None if empty."""
    db = handlers.db
    lines: list[str] = []
    for scope, pair, habits in _visible_views(handlers, user_id):
        todo = []
        for habit in habits:
            if habit.is_avoid:
                continue
            mine = {d for d, users in db.checkins(habit.id).items() if user_id in users}
            if logic.due_today(habit.schedule, today, mine, habit.weekly_target) and today not in mine:
                todo.append(f"{habit.emoji} {escape(habit.title)}")
        if todo:
            lines.append(f"<b>{handlers.scope_title(user_id, scope)}</b>: " + ", ".join(todo))

    plans = []
    for plan in db.plans_for_user(user_id, today, today):
        if plan.status == "declined" or not logic.plan_occurs(plan.day, plan.repeat, today):
            continue
        pair = db.get_pair_by_id(plan.pair_id)
        partner = pair.other(user_id) if pair else None
        who = f" — с {escape(db.user_name(partner))}" if partner else ""
        status = "" if plan.status == "accepted" else " (ждёт ответа)"
        plans.append(f"{plan.time or 'весь день'} {plan.emoji} {escape(plan.title)}{who}{status}")

    challenges = []
    for challenge in db.user_challenges(user_id):
        if challenge.finished or not challenge.start <= today <= challenge.end:
            continue
        members = db.challenge_members(challenge.id)
        scores = logic.challenge_scores(
            challenge.kind, challenge.start, challenge.end, today, db.challenge_marks(challenge.id), members
        )
        place = sorted(members, key=lambda u: -scores[u]).index(user_id) + 1
        challenges.append(f"{challenge.emoji} {escape(challenge.title)} — {place}-е место из {len(members)}")

    tasks = [t for t in db.tasks(user_id, today) if not t["done"]]

    if not (lines or plans or challenges or tasks):
        return None
    text = [f"☀️ Доброе утро, {escape(db.user_name(user_id))}! План на {logic.human_date(today)}:"]
    if tasks:
        text.append("\n🎯 <b>Главное сегодня</b>\n" + "\n".join(f"• {escape(t['title'])}" for t in tasks))
    if lines:
        text.append("\n✅ <b>Привычки</b>\n" + "\n".join(lines))
    if plans:
        text.append("\n📅 <b>Планы</b>\n" + "\n".join(plans))
    if challenges:
        text.append("\n🏆 <b>Челленджи</b>\n" + "\n".join(challenges))
    text.append("\nХорошего дня! 💪")
    return "\n".join(text)


def _rate(
    handlers: Handlers, user_id: int, habits: list[Habit], pair: Pair | None, today: date, finished: bool
) -> float | None:
    """This week so far (finished=False) or the whole previous week (finished=True)."""
    done = due = 0
    monday = logic.week_start(today) - (timedelta(days=7) if finished else timedelta(0))
    for habit in habits:
        days = _done_days(handlers, habit, pair, user_id, today)
        if finished:
            d, t = logic.completion_between(
                days, monday, monday + timedelta(days=6), habit.schedule, habit.created, habit.weekly_target
            )
        else:
            window = (today - monday).days + 1
            d, t = logic.completion(
                days, today, window, habit.schedule, habit.created, habit.kind, habit.weekly_target
            )
        done, due = done + d, due + t
    return logic.ratio(done, due)


def weekly_report(handlers: Handlers, user_id: int, today: date) -> str | None:
    """Sunday summary: this week vs last week per section, best and weakest habits."""
    db = handlers.db
    blocks: list[str] = []
    best: tuple[float, str] | None = None
    worst: tuple[float, str] | None = None
    for scope, pair, habits in _visible_views(handlers, user_id):
        if not habits:
            continue
        this_week = _rate(handlers, user_id, habits, pair, today, finished=False)
        last_week = _rate(handlers, user_id, habits, pair, today, finished=True)
        if this_week is None:
            continue
        trend = ""
        if last_week is not None:
            diff = round((this_week - last_week) * 100)
            trend = f" ({'+' if diff >= 0 else ''}{diff}% к прошлой)"
        blocks.append(f"{handlers.scope_title(user_id, scope)}: <b>{logic.percent(this_week)}</b>{trend}")
        for habit in habits:
            days = _done_days(handlers, habit, pair, user_id, today)
            rate = logic.ratio(
                *logic.completion(days, today, 7, habit.schedule, habit.created, habit.kind, habit.weekly_target)
            )
            if rate is None:
                continue
            label = f"{habit.emoji} {escape(habit.title)}"
            if best is None or rate > best[0]:
                best = (rate, label)
            if worst is None or rate < worst[0]:
                worst = (rate, label)
        if scope == "c" and pair is not None:
            moods = db.moods(pair.id, today - timedelta(days=6))
            match = logic.mood_match(moods, pair.members)
            if match is not None:
                blocks.append(f"💞 Совпадение настроения за неделю: {logic.percent(match)}")
    if not blocks:
        return None
    text = ["📊 <b>Итоги недели</b>\n", *blocks]
    if best:
        text.append(f"\n🏆 Лучше всего: {best[1]} — {logic.percent(best[0])}")
    if worst and best and worst[1] != best[1] and worst[0] < 1:
        text.append(f"🔧 Подтянуть: {worst[1]} — {logic.percent(worst[0])}")
    focus = db.focus_minutes(user_id, logic.week_start(today), today)
    if focus:
        text.append(f"⏱ Фокус за неделю: {focus // 60} ч {focus % 60} мин")
    text.append("\nНовая неделя — новые серии 🔥")
    return "\n".join(text)


def export_csv(handlers: Handlers, user_id: int, today: date) -> bytes:
    """All the user's habit history (incl. archived and shared) as CSV for Excel/Sheets."""
    db = handlers.db
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["раздел", "привычка", "тип", "дата", "кто", "отметка", "количество", "заметка"])
    for scope in SCOPES:
        view = handlers._view(user_id, scope)
        if view is None:
            continue
        pair = view.pair
        archived = (
            db.list_archived(owner_id=user_id) if pair is None else db.list_archived(pair_id=pair.id)
        )
        title = handlers.scope_title(user_id, scope)
        for habit in [*view.habits, *archived]:
            kind = "не делать" if habit.is_avoid else "делать"
            amounts = db.amounts(habit.id)
            notes = {(d, uid): t for d, uid, t in db.notes(habit.id, limit=100000)}
            checkins = db.checkins(habit.id)
            keys = {(d, u) for d, users in checkins.items() for u in users}
            keys |= {(d, u) for d, values in amounts.items() for u in values}
            keys |= set(notes)
            for day, uid in sorted(keys):
                mark = "срыв" if habit.is_avoid else "выполнено"
                writer.writerow([
                    title, habit.title + (" (архив)" if habit.archived else ""), kind, day.isoformat(),
                    db.user_name(uid), mark if uid in checkins.get(day, set()) else "",
                    amounts.get(day, {}).get(uid, ""), notes.get((day, uid), ""),
                ])
    return out.getvalue().encode("utf-8-sig")
