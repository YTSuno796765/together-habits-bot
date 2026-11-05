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
import time
from html import escape
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl
from zoneinfo import ZoneInfo

from aiohttp import web

from together_habits import logic
from together_habits.db import Habit, Pair, PairError, Storage
from together_habits.handlers import Handlers
from together_habits.texts import AVOID_TEMPLATES, SCOPE_KIND, SCOPES, TEMPLATES

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "webapp"
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
        app = web.Application(middlewares=[self._errors, self._auth], client_max_size=64 * 1024)
        app.router.add_get("/", self._index)
        app.router.add_get("/healthz", self._health)
        app.router.add_static("/static/", STATIC_DIR, append_version=False)
        app.router.add_get("/api/state", self._state)
        app.router.add_get("/api/stats", self._stats)
        app.router.add_post("/api/checkin", self._checkin)
        app.router.add_post("/api/habits", self._add_habit)
        app.router.add_delete("/api/habits/{habit_id:\\d+}", self._delete_habit)
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
        return web.FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    async def _health(self, request: web.Request) -> web.Response:
        return web.Response(text="ok")

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
        me_marked = user_id in today_users
        partner_marked = (partner_id in today_users) if partner_id else None

        week = []
        for day in logic.week_of(today):
            status = logic.day_status(day, today, done_days, habit.schedule, habit.created)
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
            "scheduleLabel": logic.schedule_label(habit.schedule),
            "kind": habit.kind,
            "dueToday": logic.is_scheduled(habit.schedule, today),
            "meMarked": me_marked,
            "partnerMarked": partner_marked,
            "meDone": me_done,
            "partnerDone": partner_done,
            "week": week,
            "streak": logic.habit_streak(habit.kind, done_days, today, habit.schedule, habit.created),
            "best": logic.best_streak(done_days, habit.schedule, habit.created),
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
                d, t = logic.completion(days, today, window, habit.schedule, habit.created, habit.kind)
                done, due = done + d, due + t
            return logic.ratio(done, due)

        def day_value(day: date) -> tuple[int, int]:
            done = due = 0
            for habit, days in series:
                if logic.is_scheduled(habit.schedule, day) and (habit.created is None or habit.created <= day):
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
            "best": max((logic.best_streak(days, h.schedule, h.created) for h, days in series), default=0),
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
                        *logic.completion(days, today, 30, h.schedule, h.created, h.kind)
                    ),
                    "streak": logic.habit_streak(h.kind, days, today, h.schedule, h.created),
                    "best": logic.best_streak(days, h.schedule, h.created),
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
        try:
            habit = self.db.add_habit(
                str(data.get("title", "")),
                str(data.get("emoji", "⭐")),
                owner_id=user_id if scope == "p" else None,
                pair_id=pair.id if pair else None,
                schedule=schedule,
                created=self.today(),
                kind=kind,
            )
        except ValueError as exc:
            raise ApiError("Проверь название и дни недели (максимум 15 привычек)") from exc
        return web.json_response({"id": habit.id})

    async def _delete_habit(self, request: web.Request) -> web.Response:
        habit = self._owned_habit(request["user_id"], request.match_info["habit_id"])
        self.db.archive_habit(habit.id)
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
