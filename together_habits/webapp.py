"""HTTP API and static files for the Telegram Mini App.

Every /api request must carry the Mini App's signed init data in the
`Authorization: tma <initData>` header. The signature is checked with the bot
token, so users can only read and change their own habits and pairs.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import time
from html import escape
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl
from zoneinfo import ZoneInfo

from aiohttp import web

from together_habits import logic
from together_habits.db import FREEZES_PER_MONTH, Habit, Pair, PairError, Plan, Storage
from together_habits.handlers import Handlers
from together_habits.texts import AVOID_TEMPLATES, KIND_SCOPE, SCOPE_KIND, SCOPES, TEMPLATES

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "webapp"


def asset_version() -> str:
    """Short hash of the UI files; changes with every update."""
    digest = hashlib.sha256()
    for name in ("index.html", "app.js", "style.css"):
        digest.update((STATIC_DIR / name).read_bytes())
    return digest.hexdigest()[:10]
INIT_DATA_MAX_AGE = 24 * 3600


def validate_init_data(
    init_data: str, bot_token: str, max_age: int = INIT_DATA_MAX_AGE, now: float | None = None
) -> dict[str, Any] | None:
    """Return the Telegram user from signed Mini App init data, or None if invalid."""
    try:
        fields = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return None
    received_hash = fields.pop("hash", "")
    if not received_hash:
        return None
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        return None
    try:
        auth_date = int(fields.get("auth_date", "0"))
        user = json.loads(fields.get("user", "null"))
    except ValueError:
        return None
    if (now if now is not None else time.time()) - auth_date > max_age:
        return None
    if not isinstance(user, dict) or not isinstance(user.get("id"), int):
        return None
    return user


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class WebApi:
    def __init__(
        self,
        storage: Storage,
        tz: ZoneInfo,
        bot_token: str,
        handlers: Handlers,
        bot: Any = None,
        scheduler: Any = None,
    ) -> None:
        self.db = storage
        self.tz = tz
        self.bot_token = bot_token
        self.handlers = handlers
        self.bot = bot  # set once the Telegram application is initialised
        self.scheduler = scheduler

    def today(self) -> date:
        return datetime.now(self.tz).date()

    # ---- app -----------------------------------------------------------------

    def build(self) -> web.Application:
        app = web.Application(middlewares=[self._static_cache, self._errors, self._auth], client_max_size=64 * 1024)
        app.router.add_get("/", self._index)
        app.router.add_get("/healthz", self._health)
        app.router.add_get("/version", self._version)
        app.router.add_static("/static/", STATIC_DIR, append_version=False)
        app.router.add_get("/api/state", self._state)
        app.router.add_get("/api/stats", self._stats)
        app.router.add_post("/api/checkin", self._checkin)
        app.router.add_post("/api/habits", self._add_habit)
        app.router.add_delete("/api/habits/{habit_id:\\d+}", self._delete_habit)
        app.router.add_post("/api/habits/{habit_id:\\d+}/pin", self._pin_habit)
        app.router.add_patch("/api/habits/{habit_id:\\d+}", self._edit_habit)
        app.router.add_get("/api/habits/{habit_id:\\d+}", self._habit_details)
        app.router.add_post("/api/habits/{habit_id:\\d+}/restore", self._restore_habit)
        app.router.add_get("/api/archive", self._archive)
        app.router.add_post("/api/amount", self._amount)
        app.router.add_post("/api/note", self._note)
        app.router.add_post("/api/freeze", self._freeze)
        app.router.add_post("/api/nudge", self._nudge)
        app.router.add_get("/api/notifications", self._notifications)
        app.router.add_post("/api/notifications", self._update_notifications)
        app.router.add_post("/api/export", self._export)
        app.router.add_post("/api/react", self._react)
        app.router.add_get("/api/challenges", self._challenges)
        app.router.add_post("/api/challenges", self._create_challenge)
        app.router.add_post("/api/challenges/join", self._join_challenge)
        app.router.add_post("/api/challenges/{challenge_id:\\d+}/mark", self._mark_challenge)
        app.router.add_delete("/api/challenges/{challenge_id:\\d+}", self._leave_challenge)
        app.router.add_post("/api/order", self._order)
        app.router.add_get("/api/plans", self._plans)
        app.router.add_post("/api/plans", self._add_plan)
        app.router.add_post("/api/plans/{plan_id:\\d+}/respond", self._respond_plan)
        app.router.add_delete("/api/plans/{plan_id:\\d+}", self._cancel_plan)
        app.router.add_post("/api/sections", self._update_section)
        app.router.add_post("/api/unpair", self._unpair)
        app.router.add_post("/api/invite", self._invite)
        app.router.add_post("/api/mood", self._mood)
        return app

    @web.middleware
    async def _errors(self, request: web.Request, handler: Any) -> web.StreamResponse:
        try:
            return await handler(request)
        except ApiError as exc:
            return web.json_response({"error": str(exc)}, status=exc.status)

    @web.middleware
    async def _static_cache(self, request: web.Request, handler: Any) -> web.StreamResponse:
        response = await handler(request)
        if request.path.startswith("/static/"):
            # Versioned URLs (?v=hash) may be cached for long; bare ones must revalidate.
            response.headers["Cache-Control"] = (
                "public, max-age=31536000, immutable" if "v" in request.query else "no-cache"
            )
        return response

    @web.middleware
    async def _auth(self, request: web.Request, handler: Any) -> web.StreamResponse:
        if request.path.startswith("/api/"):
            header = request.headers.get("Authorization", "")
            user = validate_init_data(header[4:], self.bot_token) if header.startswith("tma ") else None
            if user is None:
                raise ApiError("Открой приложение из Telegram", 401)
            name = user.get("first_name") or user.get("username") or "Друг"
            self.db.upsert_user(user["id"], str(name))
            request["user_id"] = user["id"]
        return await handler(request)

    async def _index(self, request: web.Request) -> web.StreamResponse:
        # Asset URLs carry a content hash, so Telegram's webview never keeps an old UI.
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        version = asset_version()
        for name in ("app.js", "style.css"):
            html = html.replace(f"/static/{name}", f"/static/{name}?v={version}")
        return web.Response(
            text=html,
            content_type="text/html",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )

    async def _health(self, request: web.Request) -> web.Response:
        return web.Response(text="ok")

    async def _version(self, request: web.Request) -> web.Response:
        return web.Response(text=asset_version(), headers={"Cache-Control": "no-cache"})

    # ---- helpers ---------------------------------------------------------------

    @staticmethod
    async def _json(request: web.Request) -> dict[str, Any]:
        try:
            data = await request.json()
        except (ValueError, UnicodeDecodeError) as exc:
            raise ApiError("Некорректный запрос") from exc
        if not isinstance(data, dict):
            raise ApiError("Некорректный запрос")
        return data

    @staticmethod
    def _scope(value: Any) -> str:
        if value not in SCOPES:
            raise ApiError("Неизвестный режим")
        return value

    def _pair_for(self, user_id: int, scope: str) -> Pair | None:
        return None if scope == "p" else self.db.get_pair(user_id, SCOPE_KIND[scope])

    def _active_view(self, user_id: int, scope: str) -> tuple[Pair | None, list[Habit]]:
        if scope == "p":
            return None, self.db.list_habits(owner_id=user_id)
        pair = self._pair_for(user_id, scope)
        if pair is None or not pair.is_complete:
            raise ApiError("Сначала пригласи второго участника")
        return pair, self.db.list_habits(pair_id=pair.id)

    def _owned_habit(self, user_id: int, habit_id: Any) -> Habit:
        habit = self.db.get_habit(int(habit_id)) if str(habit_id).isdigit() else None
        if habit is None or not self.db.can_access(habit, user_id):
            raise ApiError("Привычка не найдена", 404)
        return habit

    def _invite_link(self, pair: Pair) -> str | None:
        username = getattr(self.bot, "username", None)
        return f"https://t.me/{username}?start=join_{pair.invite_code}" if username else None

    @staticmethod
    def _pair_status(pair: Pair | None) -> str:
        if pair is None:
            return "none"
        return "active" if pair.is_complete else "pending"

    def _habit_payload(self, habit: Habit, user_id: int, pair: Pair | None, today: date) -> dict[str, Any]:
        checkins = self.db.checkins(habit.id)
        members = pair.members if pair else frozenset({user_id})
        real_done = logic.habit_done_days(habit.kind, checkins, members, today, habit.created)
        frozen = set(self.db.freezes(habit.id))
        done_days = logic.with_freezes(habit.kind, real_done, frozen, habit.weekly_target)
        partner_id = pair.other(user_id) if pair else None
        today_users = checkins.get(today, set())
        my_days = {day for day, users in checkins.items() if user_id in users}
        me_marked = user_id in today_users
        partner_marked = (partner_id in today_users) if partner_id else None

        week = []
        for day in logic.week_of(today):
            status = logic.day_status(
                day, today, done_days, habit.schedule, habit.created, habit.weekly_target
            )
            if habit.is_avoid and status == "today":
                status = "missed"  # slipped today
            if not habit.is_avoid and pair and status in {"missed", "today"} and checkins.get(day):
                status = "half"  # only one of the two checked in
            if day in frozen and day not in real_done:
                status = "frozen"
            week.append({"date": day.isoformat(), "status": status})

        streak = logic.habit_streak(
            habit.kind, done_days, today, habit.schedule, habit.created, habit.weekly_target
        )
        amounts_today = self.db.amounts(habit.id).get(today, {}) if habit.is_amount else {}

        if habit.is_avoid:  # a mark means a slip
            me_done = not me_marked
            partner_done = None if partner_marked is None else not partner_marked
        else:
            me_done, partner_done = me_marked, partner_marked

        return {
            "id": habit.id,
            "title": habit.title,
            "emoji": habit.emoji,
            "schedule": habit.schedule,
            "scheduleLabel": logic.habit_label(habit.schedule, habit.weekly_target),
            "weeklyTarget": habit.weekly_target,
            "weekDone": logic.week_count(my_days, logic.week_start(today)) if habit.weekly_target else None,
            "streakUnit": logic.streak_unit(habit.weekly_target),
            "kind": habit.kind,
            "pinned": habit.pinned,
            "dueToday": logic.due_today(habit.schedule, today, my_days, habit.weekly_target),
            "meMarked": me_marked,
            "partnerMarked": partner_marked,
            "meDone": me_done,
            "partnerDone": partner_done,
            "week": week,
            "streak": streak,
            "best": logic.best_streak(done_days, habit.schedule, habit.created, habit.weekly_target),
            "goalDays": habit.goal_days,
            "goalProgress": logic.goal_progress(habit.kind, real_done, streak) if habit.goal_days else None,
            "targetAmount": habit.target_amount,
            "unit": habit.unit,
            "step": habit.step,
            "myAmount": amounts_today.get(user_id, 0) if habit.is_amount else None,
            "partnerAmount": amounts_today.get(partner_id, 0) if habit.is_amount and partner_id else None,
            "remindAt": habit.remind_at,
            **self._social(habit, user_id, partner_id, today),
        }

    def _social(self, habit: Habit, user_id: int, partner_id: int | None, today: date) -> dict[str, Any]:
        if partner_id is None:
            return {}
        reactions = self.db.reactions(habit.id, today)
        return {
            "reactionToMe": reactions.get(partner_id),
            "myReaction": reactions.get(user_id),
            "nudgedToday": self.db.nudged(habit.id, user_id, today),
        }

    # ---- endpoints -------------------------------------------------------------

    async def _state(self, request: web.Request) -> web.Response:
        user_id = request["user_id"]
        scope = self._scope(request.query.get("scope", "p"))
        today = self.today()
        pairs = {s: self._pair_for(user_id, s) for s in ("f", "c")}
        pair = pairs.get(scope)

        payload: dict[str, Any] = {
            "me": {"id": user_id, "name": self.db.user_name(user_id)},
            "today": today.isoformat(),
            "scope": scope,
            "modes": {s: self._pair_status(p) for s, p in pairs.items()},
            "templates": [{"emoji": e, "title": t} for e, t in TEMPLATES[scope]],
            "avoidTemplates": [{"emoji": e, "title": t} for e, t in AVOID_TEMPLATES[scope]],
            "pair": None,
            "habits": [],
            "progress": {"done": 0, "total": 0},
            "mood": None,
            "sections": self._sections(user_id),
        }
        if scope != "p":
            status = self._pair_status(pair)
            payload["pair"] = {
                "status": status,
                "partner": self.db.user_name(pair.other(user_id)) if status == "active" else None,
                "link": self._invite_link(pair) if status == "pending" else None,
            }
            if status != "active":
                return web.json_response(payload)

        _, habits = self._active_view(user_id, scope)
        items = [self._habit_payload(h, user_id, pair, today) for h in habits]
        due = [item for item in items if item["dueToday"] and item["kind"] == "do"]
        payload["habits"] = items
        payload["archivedCount"] = len(
            self.db.list_archived(owner_id=user_id) if pair is None else self.db.list_archived(pair_id=pair.id)
        )
        payload["progress"] = {"done": sum(1 for i in due if i["meDone"]), "total": len(due)}

        if scope == "c" and pair is not None:
            moods = self.db.moods(pair.id, today).get(today, {})
            payload["mood"] = {"me": moods.get(user_id), "partner": moods.get(pair.other(user_id))}
        return web.json_response(payload)

    async def _stats(self, request: web.Request) -> web.Response:
        user_id = request["user_id"]
        scope = self._scope(request.query.get("scope", "p"))
        today = self.today()
        pair, habits = self._active_view(user_id, scope)
        members = pair.members if pair else frozenset({user_id})

        series = []
        all_checkins = []
        for habit in habits:
            checkins = self.db.checkins(habit.id)
            if not habit.is_avoid:  # slips are not shared activity
                all_checkins.append(checkins)
            real = logic.habit_done_days(habit.kind, checkins, members, today, habit.created)
            series.append(
                (habit, logic.with_freezes(habit.kind, real, self.db.freezes(habit.id), habit.weekly_target))
            )

        def rate(window: int) -> float | None:
            done = due = 0
            for habit, days in series:
                d, t = logic.completion(
                    days, today, window, habit.schedule, habit.created, habit.kind, habit.weekly_target
                )
                done, due = done + d, due + t
            return logic.ratio(done, due)

        def day_value(day: date) -> tuple[int, int]:
            done = due = 0
            for habit, days in series:
                if habit.weekly_target:
                    # Flexible habits only show up on the days they were done.
                    if day in days:
                        due += 1
                        done += 1
                elif logic.is_scheduled(habit.schedule, day) and (habit.created is None or habit.created <= day):
                    due += 1
                    done += day in days
            return done, due

        daily = []
        for offset in range(13, -1, -1):
            day = today - timedelta(days=offset)
            done, due = day_value(day)
            daily.append({"date": day.isoformat(), "label": str(day.day), "done": done, "due": due})

        weekday_totals = [[0, 0] for _ in range(7)]
        for offset in range(30):
            day = today - timedelta(days=offset)
            done, due = day_value(day)
            weekday_totals[day.weekday()][0] += done
            weekday_totals[day.weekday()][1] += due

        payload: dict[str, Any] = {
            "rate7": rate(7),
            "rate30": rate(30),
            "best": max(
                (logic.best_streak(days, h.schedule, h.created, h.weekly_target) for h, days in series),
                default=0,
            ),
            "daily": daily,
            "weekday": [
                {"label": logic.WEEKDAYS_SHORT[i], "done": d, "due": t}
                for i, (d, t) in enumerate(weekday_totals)
            ],
            "habits": [
                {
                    "emoji": h.emoji,
                    "title": h.title,
                    "kind": h.kind,
                    "rate30": logic.ratio(
                        *logic.completion(days, today, 30, h.schedule, h.created, h.kind, h.weekly_target)
                    ),
                    "streak": logic.habit_streak(
                        h.kind, days, today, h.schedule, h.created, h.weekly_target
                    ),
                    "best": logic.best_streak(days, h.schedule, h.created, h.weekly_target),
                    "streakUnit": logic.streak_unit(h.weekly_target),
                }
                for h, days in series
            ],
            "pair": None,
        }

        if pair is not None:
            partner_id = pair.other(user_id)
            sync = logic.sync_rate(all_checkins, members, today)
            pair_block: dict[str, Any] = {"partner": self.db.user_name(partner_id), "sync": sync}
            if scope == "c":
                month = self.db.moods(pair.id, today - timedelta(days=29))
                week = {d: s for d, s in month.items() if d >= today - timedelta(days=6)}
                match = logic.mood_match(month, members)
                pair_block.update(
                    {
                        "moodMatch": match,
                        "compatibility": logic.compatibility_score(sync, match),
                        "myMood": logic.average_mood(week, user_id),
                        "partnerMood": logic.average_mood(week, partner_id) if partner_id else None,
                        "moodWeek": [
                            {
                                "label": logic.WEEKDAYS_SHORT[(today - timedelta(days=o)).weekday()],
                                "me": month.get(today - timedelta(days=o), {}).get(user_id),
                                "partner": month.get(today - timedelta(days=o), {}).get(partner_id),
                            }
                            for o in range(6, -1, -1)
                        ],
                    }
                )
            payload["pair"] = pair_block
        return web.json_response(payload)

    def _day_param(self, value: Any) -> date:
        """Only today or yesterday may be changed ("забыл отметить вчера")."""
        today = self.today()
        if value in (None, "", "today"):
            return today
        if value == "yesterday":
            return today - timedelta(days=1)
        try:
            day = date.fromisoformat(str(value))
        except ValueError as exc:
            raise ApiError("Некорректная дата") from exc
        if day not in (today, today - timedelta(days=1)):
            raise ApiError("Отмечать можно только сегодня и вчера")
        return day

    async def _checkin(self, request: web.Request) -> web.Response:
        user_id = request["user_id"]
        data = await self._json(request)
        habit = self._owned_habit(user_id, data.get("habitId"))
        day = self._day_param(data.get("date"))
        if habit.is_amount:
            raise ApiError("Для этой привычки добавляй количество")
        checked = self.db.toggle_checkin(habit.id, user_id, day)
        if checked and day == self.today() and habit.pair_id is not None and self.bot is not None:
            await self.handlers.notify_checkin(self.bot, habit, user_id, day)
        return web.json_response({"checked": checked})

    async def _amount(self, request: web.Request) -> web.Response:
        """Quantitative habits: add `delta` (e.g. one glass) or set an exact `value`."""
        user_id = request["user_id"]
        data = await self._json(request)
        habit = self._owned_habit(user_id, data.get("habitId"))
        if not habit.is_amount:
            raise ApiError("Это не количественная привычка")
        day = self._day_param(data.get("date"))
        current = self.db.amounts(habit.id).get(day, {}).get(user_id, 0)
        value, delta = data.get("value"), data.get("delta")
        for item in (value, delta):
            if item is not None and (not isinstance(item, int) or isinstance(item, bool)):
                raise ApiError("Количество — целое число")
        new_value = value if value is not None else current + (delta if delta is not None else habit.step or 1)
        was_met = current >= (habit.target_amount or 0)
        met = self.db.set_amount(habit, user_id, day, new_value)
        if met and not was_met and day == self.today() and habit.pair_id is not None and self.bot is not None:
            await self.handlers.notify_checkin(self.bot, habit, user_id, day)
        stored = self.db.amounts(habit.id).get(day, {}).get(user_id, 0)
        return web.json_response({"value": stored, "met": met})

    def _habit_options(self, data: dict[str, Any], *, partial: bool) -> dict[str, Any]:
        """Map API fields to storage keyword arguments (only those present when partial)."""
        mapping = {
            "weeklyTarget": "weekly_target",
            "goalDays": "goal_days",
            "targetAmount": "target_amount",
            "unit": "unit",
            "step": "step",
            "remindAt": "remind_at",
        }
        options: dict[str, Any] = {}
        for api_key, db_key in mapping.items():
            if api_key in data:
                value = data[api_key]
                if value == "":
                    value = None
                options[db_key] = value
            elif not partial:
                options[db_key] = None
        return options

    async def _add_habit(self, request: web.Request) -> web.Response:
        user_id = request["user_id"]
        data = await self._json(request)
        scope = self._scope(data.get("scope"))
        pair, _ = self._active_view(user_id, scope)
        schedule = data.get("schedule", logic.EVERY_DAY)
        kind = data.get("kind", "do")
        if kind not in ("do", "avoid"):
            raise ApiError("Неизвестный тип привычки")
        if not isinstance(schedule, int) or isinstance(schedule, bool):
            raise ApiError("Некорректное расписание")
        options = self._habit_options(data, partial=False)
        if options["weekly_target"] is not None and kind != "do":
            raise ApiError("Раз в неделю — от 1 до 6, только для привычек «Делать»")
        try:
            habit = self.db.add_habit(
                str(data.get("title", "")),
                str(data.get("emoji", "⭐")),
                owner_id=user_id if scope == "p" else None,
                pair_id=pair.id if pair else None,
                schedule=schedule,
                created=self.today(),
                kind=kind,
                **options,
            )
        except ValueError as exc:
            raise ApiError(
                "Проверь название, дни и настройки (раз в неделю 1–6, максимум 15 привычек)"
            ) from exc
        return web.json_response({"id": habit.id})

    async def _edit_habit(self, request: web.Request) -> web.Response:
        habit = self._owned_habit(request["user_id"], request.match_info["habit_id"])
        data = await self._json(request)
        schedule = data.get("schedule")
        if schedule is not None and (not isinstance(schedule, int) or isinstance(schedule, bool)):
            raise ApiError("Некорректное расписание")
        title, emoji = data.get("title"), data.get("emoji")
        try:
            self.db.update_habit(
                habit.id,
                title=None if title is None else str(title),
                emoji=None if emoji is None else str(emoji),
                schedule=schedule,
                **self._habit_options(data, partial=True),
            )
        except ValueError as exc:
            raise ApiError("Проверь название, дни и настройки привычки") from exc
        return web.json_response({"ok": True})

    async def _delete_habit(self, request: web.Request) -> web.Response:
        """Archive by default (history kept); `?forever=1` deletes an archived habit."""
        user_id = request["user_id"]
        habit_id = request.match_info["habit_id"]
        if request.query.get("forever") == "1":
            habit = self.db.get_habit(int(habit_id), include_archived=True)
            if habit is None or not self.db.can_access(habit, user_id):
                raise ApiError("Привычка не найдена", 404)
            self.db.delete_habit_forever(habit.id)
        else:
            habit = self._owned_habit(user_id, habit_id)
            self.db.archive_habit(habit.id)
        return web.json_response({"ok": True})

    async def _restore_habit(self, request: web.Request) -> web.Response:
        user_id = request["user_id"]
        habit = self.db.get_habit(int(request.match_info["habit_id"]), include_archived=True)
        if habit is None or not self.db.can_access(habit, user_id):
            raise ApiError("Привычка не найдена", 404)
        try:
            self.db.restore_habit(habit.id)
        except ValueError as exc:
            raise ApiError("Уже 15 привычек — сначала убери одну") from exc
        return web.json_response({"ok": True})

    async def _archive(self, request: web.Request) -> web.Response:
        user_id = request["user_id"]
        scope = self._scope(request.query.get("scope", "p"))
        pair, _ = self._active_view(user_id, scope)
        habits = (
            self.db.list_archived(owner_id=user_id) if pair is None else self.db.list_archived(pair_id=pair.id)
        )
        members = pair.members if pair else frozenset({user_id})
        today = self.today()
        items = []
        for h in habits:
            done = logic.habit_done_days(h.kind, self.db.checkins(h.id), members, today, h.created)
            items.append(
                {
                    "id": h.id,
                    "title": h.title,
                    "emoji": h.emoji,
                    "kind": h.kind,
                    "doneDays": len(done),
                    "best": logic.best_streak(done, h.schedule, h.created, h.weekly_target),
                }
            )
        return web.json_response({"habits": items})

    async def _habit_details(self, request: web.Request) -> web.Response:
        """Notes, freezes and (for shared habits) reactions of one habit."""
        user_id = request["user_id"]
        habit = self._owned_habit(user_id, request.match_info["habit_id"])
        today = self.today()
        pair = self.db.get_pair_by_id(habit.pair_id) if habit.pair_id else None
        members = pair.members if pair else frozenset({user_id})
        checkins = self.db.checkins(habit.id)
        real = logic.habit_done_days(habit.kind, checkins, members, today, habit.created)
        frozen = self.db.freezes(habit.id)
        can_freeze = habit.kind == "do" and not habit.weekly_target
        freezable = (
            logic.freezable_days(real, frozen, today, habit.schedule, habit.created) if can_freeze else []
        )
        yesterday = today - timedelta(days=1)
        my_amounts = self.db.amounts(habit.id) if habit.is_amount else {}
        return web.json_response(
            {
                "notes": [
                    {"date": d.isoformat(), "author": self.db.user_name(uid), "mine": uid == user_id, "text": t}
                    for d, uid, t in self.db.notes(habit.id)
                ],
                "freezesLeft": max(0, FREEZES_PER_MONTH - self.db.freezes_used(user_id, today)),
                "freezable": [d.isoformat() for d in freezable] if can_freeze else [],
                "frozen": [
                    {"date": d.isoformat(), "mine": uid == user_id}
                    for d, uid in sorted(frozen.items(), reverse=True)
                    if d >= today - timedelta(days=30)
                ],
                "yesterdayMarked": user_id in checkins.get(yesterday, set()),
                "yesterdayAmount": my_amounts.get(yesterday, {}).get(user_id, 0) if habit.is_amount else None,
                "totalDone": len(real),
            }
        )

    async def _note(self, request: web.Request) -> web.Response:
        user_id = request["user_id"]
        data = await self._json(request)
        habit = self._owned_habit(user_id, data.get("habitId"))
        text = data.get("text", "")
        if not isinstance(text, str):
            raise ApiError("Некорректная заметка")
        self.db.set_note(habit.id, user_id, self._day_param(data.get("date")), text)
        return web.json_response({"ok": True})

    async def _freeze(self, request: web.Request) -> web.Response:
        user_id = request["user_id"]
        data = await self._json(request)
        habit = self._owned_habit(user_id, data.get("habitId"))
        try:
            day = date.fromisoformat(str(data.get("date", "")))
        except ValueError as exc:
            raise ApiError("Некорректная дата") from exc
        if data.get("remove") is True:
            if not self.db.remove_freeze(habit.id, user_id, day):
                raise ApiError("Эту заморозку ставил не ты", 404)
            return web.json_response({"ok": True})
        today = self.today()
        pair = self.db.get_pair_by_id(habit.pair_id) if habit.pair_id else None
        members = pair.members if pair else frozenset({user_id})
        real = logic.habit_done_days(habit.kind, self.db.checkins(habit.id), members, today, habit.created)
        allowed = (
            logic.freezable_days(real, self.db.freezes(habit.id), today, habit.schedule, habit.created)
            if habit.kind == "do" and not habit.weekly_target
            else []
        )
        if day not in allowed:
            raise ApiError("Заморозить можно пропуск за последние 7 дней")
        try:
            self.db.add_freeze(habit.id, user_id, day)
        except ValueError as exc:
            raise ApiError(f"Заморозки на этот месяц закончились ({FREEZES_PER_MONTH} в месяц)") from exc
        return web.json_response({"ok": True})

    async def _pin_habit(self, request: web.Request) -> web.Response:
        habit = self._owned_habit(request["user_id"], request.match_info["habit_id"])
        data = await self._json(request)
        pinned = data.get("pinned")
        if not isinstance(pinned, bool):
            raise ApiError("Некорректный запрос")
        self.db.set_pinned(habit.id, pinned)
        return web.json_response({"ok": True})

    async def _order(self, request: web.Request) -> web.Response:
        """Save a new order (and pinned set) for all habits of one mode."""
        data = await self._json(request)
        scope = self._scope(data.get("scope"))
        _, habits = self._active_view(request["user_id"], scope)
        ids, pinned = data.get("ids"), data.get("pinned", [])
        if not isinstance(ids, list) or not isinstance(pinned, list):
            raise ApiError("Некорректный запрос")
        if not all(isinstance(i, int) and not isinstance(i, bool) for i in [*ids, *pinned]):
            raise ApiError("Некорректный запрос")
        known = {habit.id for habit in habits}
        if len(ids) != len(set(ids)) or set(ids) != known or not set(pinned) <= known:
            raise ApiError("Список привычек изменился — обнови страницу", 409)
        self.db.reorder(ids, set(pinned))
        return web.json_response({"ok": True})

    # ---- notifications & export ----------------------------------------------------

    async def _notifications(self, request: web.Request) -> web.Response:
        return web.json_response(self.db.notification_settings(request["user_id"]))

    async def _update_notifications(self, request: web.Request) -> web.Response:
        user_id = request["user_id"]
        data = await self._json(request)
        kwargs: dict[str, Any] = {}
        for api_key, db_key in (("reminderHour", "reminder_hour"), ("digestHour", "digest_hour")):
            if api_key in data:
                kwargs[db_key] = data[api_key]
        for api_key, db_key in (("weeklyReport", "weekly_report"), ("weeklyExport", "weekly_export")):
            if api_key in data:
                if not isinstance(data[api_key], bool):
                    raise ApiError("Некорректный запрос")
                kwargs[db_key] = data[api_key]
        try:
            self.db.set_notification_settings(user_id, **kwargs)
        except ValueError as exc:
            raise ApiError("Час — от 0 до 23") from exc
        return web.json_response(self.db.notification_settings(user_id))

    async def _export(self, request: web.Request) -> web.Response:
        """Send the user's data to their chat as a CSV file."""
        if self.bot is None or self.scheduler is None:
            raise ApiError("Экспорт временно недоступен", 503)
        await self.scheduler.send_export(
            self.bot, request["user_id"], self.today(), caption="🗂 Твои данные (CSV)"
        )
        return web.json_response({"ok": True})

    # ---- nudges & reactions ----------------------------------------------------

    def _shared_habit(self, user_id: int, habit_id: Any) -> tuple[Habit, int]:
        habit = self._owned_habit(user_id, habit_id)
        pair = self.db.get_pair_by_id(habit.pair_id) if habit.pair_id else None
        partner_id = pair.other(user_id) if pair else None
        if partner_id is None:
            raise ApiError("Это не общая привычка")
        return habit, partner_id

    async def _nudge(self, request: web.Request) -> web.Response:
        user_id = request["user_id"]
        data = await self._json(request)
        habit, partner_id = self._shared_habit(user_id, data.get("habitId"))
        today = self.today()
        if partner_id in self.db.checkins(habit.id).get(today, set()):
            raise ApiError("Уже выполнено — лучше поставь реакцию 🔥")
        if not self.db.add_nudge(habit.id, user_id, today):
            raise ApiError("Сегодня уже напоминал — пусть отдохнёт 🙂")
        if self.bot is not None:
            name = escape(self.db.user_name(user_id))
            await self.handlers.send(
                self.bot,
                partner_id,
                f"👊 {name} напоминает: {habit.emoji} <b>{escape(habit.title)}</b> ещё не отмечено сегодня. Давай!",
                self.handlers.app_markup(),
            )
        return web.json_response({"ok": True})

    async def _react(self, request: web.Request) -> web.Response:
        user_id = request["user_id"]
        data = await self._json(request)
        habit, partner_id = self._shared_habit(user_id, data.get("habitId"))
        emoji = data.get("emoji")
        today = self.today()
        if emoji is not None and partner_id not in self.db.checkins(habit.id).get(today, set()):
            raise ApiError("Реакцию можно поставить, когда второй уже выполнил")
        try:
            self.db.set_reaction(habit.id, user_id, today, emoji)
        except ValueError as exc:
            raise ApiError("Неизвестная реакция") from exc
        if emoji and self.bot is not None:
            name = escape(self.db.user_name(user_id))
            await self.handlers.send(
                self.bot, partner_id, f"{emoji} {name} реагирует на {habit.emoji} <b>{escape(habit.title)}</b>"
            )
        return web.json_response({"ok": True})

    # ---- challenges -------------------------------------------------------------

    def _challenge_payload(self, challenge: Any, user_id: int, today: date) -> dict[str, Any]:
        members = self.db.challenge_members(challenge.id)
        marks = self.db.challenge_marks(challenge.id)
        scores = logic.challenge_scores(challenge.kind, challenge.start, challenge.end, today, marks, members)
        username = getattr(self.bot, "username", None)
        ranked = sorted(members, key=lambda u: -scores[u])
        return {
            "id": challenge.id,
            "title": challenge.title,
            "emoji": challenge.emoji,
            "kind": challenge.kind,
            "start": challenge.start.isoformat(),
            "end": challenge.end.isoformat(),
            "days": challenge.days,
            "dayNumber": max(0, min(challenge.days, (today - challenge.start).days + 1)),
            "stake": challenge.stake,
            "finished": challenge.finished or challenge.end < today,
            "isCreator": challenge.creator_id == user_id,
            "markedToday": today in marks.get(user_id, set()),
            "link": f"https://t.me/{username}?start=ch_{challenge.invite_code}" if username else None,
            "members": [
                {
                    "name": self.db.user_name(u),
                    "me": u == user_id,
                    "score": scores[u],
                    "markedToday": today in marks.get(u, set()),
                }
                for u in ranked
            ],
            "winners": [self.db.user_name(u) for u in logic.challenge_winners(scores)]
            if challenge.end < today
            else [],
        }

    def _member_challenge(self, user_id: int, challenge_id: Any) -> Any:
        challenge = self.db.get_challenge(int(challenge_id)) if str(challenge_id).isdigit() else None
        if challenge is None or user_id not in self.db.challenge_members(challenge.id):
            raise ApiError("Челлендж не найден", 404)
        return challenge

    async def _challenges(self, request: web.Request) -> web.Response:
        user_id = request["user_id"]
        today = self.today()
        items = [self._challenge_payload(c, user_id, today) for c in self.db.user_challenges(user_id)]
        active = [c for c in items if not c["finished"]]
        finished = [c for c in items if c["finished"]][:10]
        return web.json_response({"today": today.isoformat(), "active": active, "finished": finished})

    async def _create_challenge(self, request: web.Request) -> web.Response:
        user_id = request["user_id"]
        data = await self._json(request)
        invite = data.get("invite", [])
        if not isinstance(invite, list) or not set(invite) <= {"f", "c"}:
            raise ApiError("Некорректный запрос")
        days = data.get("days", 7)
        try:
            challenge = self.db.create_challenge(
                user_id,
                str(data.get("title", "")),
                emoji=str(data.get("emoji", "🏆")),
                kind=str(data.get("kind", "do")),
                days=days if isinstance(days, int) and not isinstance(days, bool) else 0,
                start=self.today(),
                stake=str(data.get("stake") or ""),
            )
        except ValueError as exc:
            raise ApiError("Укажи название и длительность (7, 14, 21 или 30 дней)") from exc
        name = escape(self.db.user_name(user_id))
        for scope in invite:
            pair = self._pair_for(user_id, scope)
            partner = pair.other(user_id) if pair is not None and pair.is_complete else None
            if partner is None:
                continue
            self.db.join_challenge(challenge.id, partner)
            if self.bot is not None:
                stake = f"\n💸 Ставка: {escape(challenge.stake)}" if challenge.stake else ""
                await self.handlers.send(
                    self.bot,
                    partner,
                    f"🏆 {name} вызывает тебя на челлендж: {challenge.emoji} <b>{escape(challenge.title)}</b>\n"
                    f"{challenge.days} дней, до {logic.human_date(challenge.end)}{stake}",
                    self.handlers.app_markup(),
                )
        return web.json_response(self._challenge_payload(challenge, user_id, self.today()))

    async def _join_challenge(self, request: web.Request) -> web.Response:
        data = await self._json(request)
        try:
            challenge = await self.handlers.join_challenge_by_code(
                self.bot, str(data.get("code", "")), request["user_id"]
            )
        except PairError as exc:
            raise ApiError(str(exc)) from exc
        return web.json_response({"id": challenge.id})

    async def _mark_challenge(self, request: web.Request) -> web.Response:
        user_id = request["user_id"]
        challenge = self._member_challenge(user_id, request.match_info["challenge_id"])
        data = await self._json(request)
        day = self._day_param(data.get("date"))
        if not challenge.start <= day <= challenge.end:
            raise ApiError("Этот день вне челленджа")
        marked = self.db.toggle_challenge_mark(challenge.id, user_id, day)
        if marked and challenge.kind == "avoid" and self.bot is not None:
            name = escape(self.db.user_name(user_id))
            for member in self.db.challenge_members(challenge.id):
                if member != user_id:
                    await self.handlers.send(
                        self.bot, member,
                        f"💥 У {name} срыв в челлендже {challenge.emoji} <b>{escape(challenge.title)}</b>",
                    )
        return web.json_response({"marked": marked})

    async def _leave_challenge(self, request: web.Request) -> web.Response:
        user_id = request["user_id"]
        challenge = self._member_challenge(user_id, request.match_info["challenge_id"])
        if challenge.creator_id == user_id:
            self.db.delete_challenge(challenge.id)
        else:
            self.db.leave_challenge(challenge.id, user_id)
        return web.json_response({"ok": True})

    # ---- sections (per-user names / visibility) -------------------------------

    def _sections(self, user_id: int) -> dict[str, Any]:
        settings = self.db.mode_settings(user_id)
        result: dict[str, Any] = {}
        for scope in SCOPES:
            name, hidden = settings[scope]
            item: dict[str, Any] = {"name": name, "hidden": hidden}
            if scope != "p":
                pair = self._pair_for(user_id, scope)
                item["pair"] = self._pair_status(pair)
                item["partner"] = (
                    self.db.user_name(pair.other(user_id)) if pair is not None and pair.is_complete else None
                )
            result[scope] = item
        return result

    async def _update_section(self, request: web.Request) -> web.Response:
        user_id = request["user_id"]
        data = await self._json(request)
        scope = self._scope(data.get("scope"))
        name, hidden = data.get("name"), data.get("hidden")
        if name is not None and not isinstance(name, str):
            raise ApiError("Некорректное название")
        if hidden is not None and not isinstance(hidden, bool):
            raise ApiError("Некорректный запрос")
        try:
            self.db.set_mode_settings(user_id, scope, name=name, hidden=hidden)
        except ValueError as exc:
            raise ApiError("Личный раздел скрыть нельзя") from exc
        return web.json_response({"sections": self._sections(user_id)})

    async def _unpair(self, request: web.Request) -> web.Response:
        user_id = request["user_id"]
        data = await self._json(request)
        scope = self._scope(data.get("scope"))
        if scope == "p":
            raise ApiError("В личном разделе нет связки")
        if not await self.handlers.unpair(self.bot, user_id, scope):
            raise ApiError("Связки нет", 404)
        return web.json_response({"sections": self._sections(user_id)})

    # ---- plans -----------------------------------------------------------------

    def _plan_payload(
        self, plan: Plan, user_id: int, pairs: dict[int, Pair], on: date | None = None
    ) -> dict[str, Any]:
        pair = pairs[plan.pair_id]
        partner_id = pair.other(user_id)
        return {
            "id": plan.id,
            "scope": KIND_SCOPE[pair.kind],
            "title": plan.title,
            "emoji": plan.emoji,
            "date": (on or plan.day).isoformat(),
            "firstDate": plan.day.isoformat(),
            "repeat": plan.repeat,
            "time": plan.time,
            "note": plan.note,
            "status": plan.status,
            "mine": plan.author_id == user_id,
            "partner": self.db.user_name(partner_id) if partner_id else None,
        }

    async def _plans(self, request: web.Request) -> web.Response:
        user_id = request["user_id"]
        today = self.today()
        month = request.query.get("month", today.strftime("%Y-%m"))
        if not re.fullmatch(r"\d{4}-\d{2}", month):
            raise ApiError("Некорректный месяц")
        year, mon = map(int, month.split("-"))
        if not 1 <= mon <= 12:
            raise ApiError("Некорректный месяц")
        first = date(year, mon, 1)
        last = date(year + mon // 12, mon % 12 + 1, 1) - timedelta(days=1)

        pairs = {s: self._pair_for(user_id, s) for s in ("f", "c")}
        by_id = {p.id: p for p in pairs.values() if p is not None}
        # Show a whole 6-week grid around the month, plus everything upcoming.
        grid_start = first - timedelta(days=first.weekday())
        grid_end = grid_start + timedelta(days=41)
        # Weekly plans expand into one entry per occurrence.
        grid = sorted(
            (
                (occurrence, p)
                for p in self.db.plans_for_user(user_id, grid_start, grid_end)
                for occurrence in logic.plan_occurrences(p.day, p.repeat, grid_start, grid_end)
            ),
            key=lambda item: (item[0], item[1].time or "99:99", item[1].id),
        )
        upcoming_items = []
        for p in self.db.plans_for_user(user_id, today, today + timedelta(days=365)):
            if p.status == "declined":
                continue
            nxt = logic.plan_occurrences(p.day, p.repeat, today, today + timedelta(days=365))
            if nxt:
                upcoming_items.append((nxt[0], p))
        upcoming_items.sort(key=lambda item: (item[0], item[1].time or "99:99", item[1].id))
        incoming = [
            p for p in self.db.plans_for_user(user_id, today)
            if p.status == "pending" and p.author_id != user_id
        ]
        return web.json_response(
            {
                "today": today.isoformat(),
                "month": month,
                "pairs": {
                    s: {
                        "status": self._pair_status(p),
                        "partner": self.db.user_name(p.other(user_id))
                        if p is not None and p.is_complete
                        else None,
                    }
                    for s, p in pairs.items()
                },
                "sections": self._sections(user_id),
                "plans": [self._plan_payload(p, user_id, by_id, on) for on, p in grid],
                "upcoming": [self._plan_payload(p, user_id, by_id, on) for on, p in upcoming_items[:20]],
                "incoming": [self._plan_payload(p, user_id, by_id) for p in incoming],
            }
        )

    async def _add_plan(self, request: web.Request) -> web.Response:
        user_id = request["user_id"]
        data = await self._json(request)
        scope = self._scope(data.get("scope"))
        if scope == "p":
            raise ApiError("Планировать можно с другом или парой")
        pair, _ = self._active_view(user_id, scope)
        assert pair is not None
        try:
            day = date.fromisoformat(str(data.get("date", "")))
        except ValueError as exc:
            raise ApiError("Выбери дату") from exc
        today = self.today()
        if not today <= day <= today + timedelta(days=366):
            raise ApiError("Дата должна быть от сегодня и до года вперёд")
        time_value = data.get("time") or None
        if time_value is not None and not (
            isinstance(time_value, str) and re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", time_value)
        ):
            raise ApiError("Время в формате ЧЧ:ММ")
        try:
            plan = self.db.add_plan(
                pair.id,
                user_id,
                str(data.get("title", "")),
                day,
                emoji=str(data.get("emoji", "📅")),
                time=time_value,
                note=str(data.get("note", "") or ""),
                repeat="weekly" if data.get("repeat") == "weekly" else None,
            )
        except ValueError as exc:
            raise ApiError("Напиши, что предлагаешь") from exc
        if self.bot is not None:
            await self.handlers.notify_plan_proposed(self.bot, plan)
        return web.json_response({"id": plan.id})

    async def _respond_plan(self, request: web.Request) -> web.Response:
        data = await self._json(request)
        accept = data.get("accept")
        if not isinstance(accept, bool):
            raise ApiError("Некорректный запрос")
        try:
            plan = await self.handlers.respond_plan(
                self.bot, int(request.match_info["plan_id"]), request["user_id"], accept
            )
        except PairError as exc:
            raise ApiError(str(exc)) from exc
        return web.json_response({"status": plan.status})

    async def _cancel_plan(self, request: web.Request) -> web.Response:
        try:
            await self.handlers.cancel_plan(
                self.bot, int(request.match_info["plan_id"]), request["user_id"]
            )
        except PairError as exc:
            raise ApiError(str(exc), 404) from exc
        return web.json_response({"ok": True})

    async def _invite(self, request: web.Request) -> web.Response:
        data = await self._json(request)
        scope = self._scope(data.get("scope"))
        if scope == "p":
            raise ApiError("В личном режиме приглашения не нужны")
        try:
            pair = self.db.create_invite(request["user_id"], SCOPE_KIND[scope])
        except PairError as exc:
            raise ApiError(str(exc)) from exc
        return web.json_response({"link": self._invite_link(pair)})

    async def _mood(self, request: web.Request) -> web.Response:
        user_id = request["user_id"]
        data = await self._json(request)
        score = data.get("score")
        pair = self.db.get_pair(user_id, "couple")
        if pair is None or not pair.is_complete:
            raise ApiError("Настроение доступно в режиме пары")
        if not isinstance(score, int) or score not in logic.MOOD_EMOJI:
            raise ApiError("Оценка от 1 до 5")
        today = self.today()
        self.db.set_mood(pair.id, user_id, today, score)
        partner_id = pair.other(user_id)
        if partner_id is not None and self.bot is not None:
            name = escape(self.db.user_name(user_id))
            await self.handlers.send(
                self.bot, partner_id, f"💭 {name} сегодня: {logic.MOOD_EMOJI[score]}"
            )
        return web.json_response({"ok": True})
