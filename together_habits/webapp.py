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
from together_habits.db import Habit, Pair, PairError, Plan, Storage
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
    ) -> None:
        self.db = storage
        self.tz = tz
        self.bot_token = bot_token
        self.handlers = handlers
        self.bot = bot  # set once the Telegram application is initialised

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
        done_days = logic.habit_done_days(habit.kind, checkins, members, today, habit.created)
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
            week.append({"date": day.isoformat(), "status": status})

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
            "streak": logic.habit_streak(
                habit.kind, done_days, today, habit.schedule, habit.created, habit.weekly_target
            ),
            "best": logic.best_streak(done_days, habit.schedule, habit.created, habit.weekly_target),
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
            series.append(
                (habit, logic.habit_done_days(habit.kind, checkins, members, today, habit.created))
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

    async def _checkin(self, request: web.Request) -> web.Response:
        user_id = request["user_id"]
        data = await self._json(request)
        habit = self._owned_habit(user_id, data.get("habitId"))
        today = self.today()
        checked = self.db.toggle_checkin(habit.id, user_id, today)
        if checked and habit.pair_id is not None and self.bot is not None:
            await self.handlers.notify_checkin(self.bot, habit, user_id, today)
        return web.json_response({"checked": checked})

    async def _add_habit(self, request: web.Request) -> web.Response:
        user_id = request["user_id"]
        data = await self._json(request)
        scope = self._scope(data.get("scope"))
        pair, _ = self._active_view(user_id, scope)
        schedule = data.get("schedule", logic.EVERY_DAY)
        kind = data.get("kind", "do")
        if kind not in ("do", "avoid"):
            raise ApiError("Неизвестный тип привычки")
        if not isinstance(schedule, int):
            raise ApiError("Некорректное расписание")
        weekly_target = data.get("weeklyTarget")
        if weekly_target is not None and (
            not isinstance(weekly_target, int) or isinstance(weekly_target, bool)
            or not 1 <= weekly_target <= 6 or kind != "do"
        ):
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
                weekly_target=weekly_target,
            )
        except ValueError as exc:
            raise ApiError("Проверь название и дни недели (максимум 15 привычек)") from exc
        return web.json_response({"id": habit.id})

    async def _delete_habit(self, request: web.Request) -> web.Response:
        habit = self._owned_habit(request["user_id"], request.match_info["habit_id"])
        self.db.archive_habit(habit.id)
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

    def _plan_payload(self, plan: Plan, user_id: int, pairs: dict[int, Pair]) -> dict[str, Any]:
        pair = pairs[plan.pair_id]
        partner_id = pair.other(user_id)
        return {
            "id": plan.id,
            "scope": KIND_SCOPE[pair.kind],
            "title": plan.title,
            "emoji": plan.emoji,
            "date": plan.day.isoformat(),
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
        plans = self.db.plans_for_user(user_id, grid_start, grid_start + timedelta(days=41))
        upcoming = [
            p for p in self.db.plans_for_user(user_id, today, today + timedelta(days=365))
            if p.status != "declined"
        ][:20]
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
                "plans": [self._plan_payload(p, user_id, by_id) for p in plans],
                "upcoming": [self._plan_payload(p, user_id, by_id) for p in upcoming],
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
