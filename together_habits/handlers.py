"""Telegram handlers: menus, pairing, check-ins, stats, moods and reminders."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html import escape
from typing import Any
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

from together_habits import keyboards as kb
from together_habits import logic
from together_habits.db import Habit, Pair, PairError, Plan, Storage
from together_habits.texts import (
    HELP,
    KIND_SCOPE,
    MODE_INTRO,
    PARTNER_WORD,
    SCOPE_KIND,
    SCOPE_TITLE,
    SCOPES,
    TEMPLATES,
    WELCOME,
)

log = logging.getLogger(__name__)
Context = ContextTypes.DEFAULT_TYPE


@dataclass
class ScopeView:
    scope: str
    pair: Pair | None
    habits: list[Habit]


class Handlers:
    def __init__(self, storage: Storage, tz: ZoneInfo, webapp_url: str | None = None) -> None:
        self.db = storage
        self.tz = tz
        self.webapp_url = webapp_url

    # ---- helpers -----------------------------------------------------------

    def today(self) -> date:
        return datetime.now(self.tz).date()

    def _register(self, update: Update) -> int:
        user = update.effective_user
        assert user is not None
        self.db.upsert_user(user.id, user.first_name or user.username or "Друг")
        return user.id

    def _name(self, user_id: int) -> str:
        return escape(self.db.user_name(user_id))

    async def _show(self, update: Update, text: str, markup: Any = None) -> None:
        query = update.callback_query
        if query is not None and query.message is not None:
            try:
                await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
            except BadRequest as exc:
                if "not modified" not in str(exc).lower():
                    raise
            return
        if update.effective_message is not None:
            await update.effective_message.reply_text(
                text, parse_mode=ParseMode.HTML, reply_markup=markup
            )

    async def _notify(self, context: Context, user_id: int | None, text: str, markup: Any = None) -> None:
        if user_id is not None:
            await self.send(context.bot, user_id, text, markup)

    async def notify_checkin(self, bot: Any, habit: Habit, user_id: int, today: date) -> None:
        """Tell the partner about a fresh check-in on a shared habit."""
        if habit.pair_id is None:
            return
        pair = self.db.get_pair_by_id(habit.pair_id)
        partner_id = pair.other(user_id) if pair else None
        if partner_id is None:
            return
        members = self.db.habit_members(habit)
        checkins = self.db.checkins(habit.id)
        title = f"{habit.emoji} <b>{escape(habit.title)}</b>"
        if habit.is_avoid:
            text = f"💥 У {self._name(user_id)} срыв: {title}. Поддержи 🤝"
            await self.send(bot, partner_id, text)
            return
        if members <= checkins.get(today, set()):
            streak = logic.current_streak(
                logic.completed_days(checkins, members), today, habit.schedule, habit.created
            )
            text, markup = f"🔥 Вы оба выполнили {title}! Совместная серия: {streak} дн.", None
        else:
            text = f"👀 {self._name(user_id)} выполняет {title}. Твоя очередь!"
            markup = kb.open_today(self.webapp_url)
        await self.send(bot, partner_id, text, markup)

    async def send(self, bot: Any, user_id: int, text: str, markup: Any = None) -> None:
        try:
            await bot.send_message(user_id, text, parse_mode=ParseMode.HTML, reply_markup=markup)
        except TelegramError as exc:  # partner blocked the bot, etc.
            log.info("Could not notify user: %s", exc)

    def _view(self, user_id: int, scope: str) -> ScopeView | None:
        """Habits for a scope; None if a shared scope has no complete pair yet."""
        if scope == "p":
            return ScopeView(scope, None, self.db.list_habits(owner_id=user_id))
        pair = self.db.get_pair(user_id, SCOPE_KIND[scope])
        if pair is None or not pair.is_complete:
            return None
        return ScopeView(scope, pair, self.db.list_habits(pair_id=pair.id))

    def _habit_scope(self, habit: Habit) -> str:
        if habit.owner_id is not None:
            return "p"
        pair = self.db.get_pair_by_id(habit.pair_id) if habit.pair_id else None
        return KIND_SCOPE[pair.kind] if pair else "p"

    # ---- commands ----------------------------------------------------------

    async def start(self, update: Update, context: Context) -> None:
        user_id = self._register(update)
        context.user_data.pop("awaiting", None)
        if context.args and context.args[0].startswith("join_"):
            await self._join(update, context, user_id, context.args[0].removeprefix("join_"))
            return
        await self._show(update, WELCOME.format(name=self._name(user_id)), kb.main_menu(self.webapp_url))

    async def help(self, update: Update, context: Context) -> None:
        self._register(update)
        await self._show(update, HELP, kb.main_menu(self.webapp_url))

    async def today_cmd(self, update: Update, context: Context) -> None:
        user_id = self._register(update)
        await self._today_overview(update, user_id)

    async def mood_cmd(self, update: Update, context: Context) -> None:
        user_id = self._register(update)
        await self._mood_prompt(update, user_id)

    # ---- callback router ---------------------------------------------------

    async def on_callback(self, update: Update, context: Context) -> None:
        query = update.callback_query
        assert query is not None
        user_id = self._register(update)
        action, _, arg = (query.data or "").partition(":")
        if action != "chk":  # check-ins answer with their own toast
            await query.answer()

        if action == "menu":
            context.user_data.pop("awaiting", None)
            await self._show(update, WELCOME.format(name=self._name(user_id)), kb.main_menu(self.webapp_url))
        elif action == "help":
            await self._show(update, HELP, kb.main_menu(self.webapp_url))
        elif action == "today":
            await self._today_overview(update, user_id)
        elif action in {"mode", "list", "add", "stats", "manage", "custom", "invite", "unpair", "unpairok"}:
            if arg not in SCOPES:
                return
            await getattr(self, f"_{action}")(update, context, user_id, arg)
        elif action == "tpl":
            scope, _, index = arg.partition(":")
            await self._add_template(update, user_id, scope, index)
        elif action == "chk" and arg.isdigit():
            await self._toggle(update, context, user_id, int(arg))
        elif action == "del" and arg.isdigit():
            await self._delete_habit(update, user_id, int(arg))
        elif action == "mood":
            await self._mood_prompt(update, user_id)
        elif action == "moodset" and arg.isdigit():
            await self._set_mood(update, context, user_id, int(arg))
        elif action == "plan":
            plan_id, _, answer = arg.partition(":")
            if plan_id.isdigit() and answer in {"y", "n"}:
                await self._plan_answer(update, context, user_id, int(plan_id), answer == "y")
        elif action == "rem":
            await self._reminders(update, user_id)
        elif action == "remset":
            self.db.set_reminder(user_id, None if arg == "off" else int(arg))
            await self._reminders(update, user_id)

    # ---- modes & pairing ---------------------------------------------------

    async def _mode(self, update: Update, context: Context, user_id: int, scope: str) -> None:
        context.user_data.pop("awaiting", None)
        if scope == "p":
            count = len(self.db.list_habits(owner_id=user_id))
            text = (
                "🎯 <b>Личный рост</b>\n\n"
                "Маленькие ежедневные действия → большие результаты.\n"
                f"Привычек: {count}"
            )
            await self._show(update, text, kb.mode_menu(scope))
            return

        pair = self.db.get_pair(user_id, SCOPE_KIND[scope])
        if pair is None:
            text = MODE_INTRO[scope] + f"\n\nПригласи {self._partner_dative(scope)} — и начнём."
            await self._show(update, text, kb.invite_menu(scope, has_invite=False))
        elif not pair.is_complete:
            text = MODE_INTRO[scope] + "\n\n⏳ Ждём, пока по ссылке присоединятся:\n" + await self._invite_link(update, pair)
            await self._show(update, text, kb.invite_menu(scope, has_invite=True))
        else:
            partner = self._name(pair.other(user_id))
            count = len(self.db.list_habits(pair_id=pair.id))
            text = MODE_INTRO[scope] + f"\n\nВы в связке с <b>{partner}</b>. Общих привычек: {count}"
            await self._show(update, text, kb.mode_menu(scope))

    @staticmethod
    def _partner_dative(scope: str) -> str:
        return "друга" if scope == "f" else "свою половинку"

    async def _invite_link(self, update: Update, pair: Pair) -> str:
        bot = update.get_bot()
        username = bot.username
        return f"https://t.me/{username}?start=join_{pair.invite_code}"

    async def _invite(self, update: Update, context: Context, user_id: int, scope: str) -> None:
        if scope == "p":
            return
        try:
            self.db.create_invite(user_id, SCOPE_KIND[scope])
        except PairError as exc:
            await self._show(update, escape(str(exc)), kb.main_menu(self.webapp_url))
            return
        await self._mode(update, context, user_id, scope)

    async def _join(self, update: Update, context: Context, user_id: int, code: str) -> None:
        try:
            pair = self.db.join_pair(code, user_id)
        except PairError as exc:
            await self._show(update, "😕 " + escape(str(exc)), kb.main_menu(self.webapp_url))
            return
        scope = KIND_SCOPE[pair.kind]
        owner = pair.owner_id
        await self._notify(
            context,
            owner,
            f"🎉 <b>{self._name(user_id)}</b> присоединяется к тебе в режиме {SCOPE_TITLE[scope]}!\n"
            "Добавляйте привычки и отмечайтесь каждый день.",
            kb.mode_menu(scope),
        )
        await self._show(
            update,
            f"🎉 Готово! Вы в связке с <b>{self._name(owner)}</b> — {SCOPE_TITLE[scope]}.",
            kb.mode_menu(scope),
        )

    async def _unpair(self, update: Update, context: Context, user_id: int, scope: str) -> None:
        if scope == "p":
            return
        pair = self.db.get_pair(user_id, SCOPE_KIND[scope])
        if pair is None:
            await self._mode(update, context, user_id, scope)
        elif not pair.is_complete:
            self.db.delete_pair(pair.id)
            await self._mode(update, context, user_id, scope)
        else:
            await self._show(
                update,
                "Точно разорвать связку? Общие привычки, отметки и настроение будут удалены.",
                kb.confirm_unpair(scope),
            )

    async def _unpairok(self, update: Update, context: Context, user_id: int, scope: str) -> None:
        if scope == "p":
            return
        pair = self.db.get_pair(user_id, SCOPE_KIND[scope])
        if pair is not None:
            self.db.delete_pair(pair.id)
            if pair.is_complete:
                await self._notify(
                    context,
                    pair.other(user_id),
                    f"💔 {self._name(user_id)} разрывает связку в режиме {SCOPE_TITLE[scope]}.",
                )
        await self._mode(update, context, user_id, scope)

    # ---- habits ------------------------------------------------------------

    async def _add(self, update: Update, context: Context, user_id: int, scope: str) -> None:
        view = self._view(user_id, scope)
        if view is None:
            await self._mode(update, context, user_id, scope)
            return
        titles = {habit.title for habit in view.habits}
        await self._show(
            update,
            "Выбери привычку из шаблонов или добавь свою:",
            kb.templates_menu(scope, titles),
        )

    async def _custom(self, update: Update, context: Context, user_id: int, scope: str) -> None:
        if self._view(user_id, scope) is None:
            await self._mode(update, context, user_id, scope)
            return
        context.user_data["awaiting"] = scope
        await self._show(
            update,
            "Напиши название привычки одним сообщением (до 40 символов).\n"
            "Можно начать с эмодзи, например: <code>🥗 Полезный завтрак</code>",
            kb.Markup([[kb.back_to_mode(scope)]]),
        )

    async def on_text(self, update: Update, context: Context) -> None:
        user_id = self._register(update)
        scope = context.user_data.pop("awaiting", None)
        message = update.effective_message
        if scope is None or message is None:
            await self._show(update, "Выбери действие в меню 👇", kb.main_menu(self.webapp_url))
            return
        emoji, title = _split_emoji(message.text or "")
        await self._create_habit(update, user_id, scope, emoji, title)

    async def _add_template(self, update: Update, user_id: int, scope: str, index: str) -> None:
        if scope not in SCOPES or not index.isdigit() or int(index) >= len(TEMPLATES[scope]):
            return
        emoji, title = TEMPLATES[scope][int(index)]
        await self._create_habit(update, user_id, scope, emoji, title)

    async def _create_habit(self, update: Update, user_id: int, scope: str, emoji: str, title: str) -> None:
        view = self._view(user_id, scope)
        if view is None:
            await self._show(update, "Сначала нужно собрать связку в этом режиме.", kb.main_menu(self.webapp_url))
            return
        owner = user_id if scope == "p" else None
        pair_id = view.pair.id if view.pair else None
        try:
            habit = self.db.add_habit(
                title, emoji, owner_id=owner, pair_id=pair_id, created=self.today()
            )
        except ValueError:
            await self._show(
                update,
                "Не получилось добавить: пустое название или уже слишком много привычек (максимум 15).",
                kb.mode_menu(scope),
            )
            return
        await self._show(
            update,
            f"Добавлено: {habit.emoji} <b>{escape(habit.title)}</b>",
            kb.mode_menu(scope),
        )

    async def _manage(self, update: Update, context: Context, user_id: int, scope: str) -> None:
        view = self._view(user_id, scope)
        if view is None:
            await self._mode(update, context, user_id, scope)
            return
        items = [(h.id, f"{h.emoji} {h.title}") for h in view.habits]
        text = "Какую привычку удалить?" if items else "Пока нет привычек."
        await self._show(update, text, kb.manage_menu(scope, items))

    async def _delete_habit(self, update: Update, user_id: int, habit_id: int) -> None:
        habit = self.db.get_habit(habit_id)
        if habit is None or not self.db.can_access(habit, user_id):
            return
        scope = self._habit_scope(habit)
        self.db.archive_habit(habit_id)
        view = self._view(user_id, scope)
        items = [(h.id, f"{h.emoji} {h.title}") for h in view.habits] if view else []
        await self._show(
            update, f"Удалено: {habit.emoji} {escape(habit.title)}", kb.manage_menu(scope, items)
        )

    # ---- check-ins ---------------------------------------------------------

    async def _list(self, update: Update, context: Context, user_id: int, scope: str) -> None:
        view = self._view(user_id, scope)
        if view is None:
            await self._mode(update, context, user_id, scope)
            return
        today = self.today()
        items = []
        partner_id = view.pair.other(user_id) if view.pair else None
        for habit in view.habits:
            done = self.db.checkins(habit.id).get(today, set())
            on, off = ("💥", "🟢") if habit.is_avoid else ("✅", "⬜")
            mark = on if user_id in done else off
            if partner_id is not None:
                mark += on if partner_id in done else off
            prefix = "🚫 " if habit.is_avoid else ""
            items.append((habit.id, f"{mark} {prefix}{habit.emoji} {habit.title}"))

        header = f"{SCOPE_TITLE[scope]} — {today.strftime('%d.%m')}\n\n"
        if not items:
            body = "Привычек пока нет — добавь первую!"
        elif partner_id is not None:
            body = f"Нажми на привычку, чтобы отметить.\nСлева — ты, справа — {self._name(partner_id)}."
        else:
            body = "Нажми на привычку, чтобы отметить выполнение."
        if any(h.is_avoid for h in view.habits):
            body += "\n🚫 — «не делать»: день засчитан сам, нажми только при срыве."
        await self._show(update, header + body, kb.checklist(scope, items))

    async def _toggle(self, update: Update, context: Context, user_id: int, habit_id: int) -> None:
        habit = self.db.get_habit(habit_id)
        if habit is None or not self.db.can_access(habit, user_id):
            if update.callback_query:
                await update.callback_query.answer("Привычка не найдена")
            return
        scope = self._habit_scope(habit)
        today = self.today()
        checked = self.db.toggle_checkin(habit_id, user_id, today)
        toast = "✅ Отмечено" if checked else "Отметка снята"
        if habit.is_avoid:
            toast = "💥 Срыв отмечен. Начнём заново!" if checked else "Срыв отменён"

        if checked and habit.pair_id is not None and habit.is_avoid:
            await self.notify_checkin(context.bot, habit, user_id, today)
        elif checked and habit.pair_id is not None:
            members = self.db.habit_members(habit)
            checkins = self.db.checkins(habit_id)
            if members <= checkins.get(today, set()):
                streak = logic.current_streak(
                    logic.completed_days(checkins, members), today, habit.schedule, habit.created
                )
                toast = f"🔥 Вы оба! Серия {streak} дн."
            await self.notify_checkin(context.bot, habit, user_id, today)
        if update.callback_query:
            await update.callback_query.answer(toast)
        await self._list(update, context, user_id, scope)

    async def _today_overview(self, update: Update, user_id: int) -> None:
        today = self.today()
        lines = [f"<b>Сегодня, {today.strftime('%d.%m')}</b>\n"]
        buttons = []
        for scope in SCOPES:
            view = self._view(user_id, scope)
            if view is None:
                continue
            todo = [
                h for h in view.habits if not h.is_avoid and logic.is_scheduled(h.schedule, today)
            ]
            done = sum(1 for h in todo if user_id in self.db.checkins(h.id).get(today, set()))
            lines.append(f"{SCOPE_TITLE[scope]}: {done}/{len(todo)}")
            buttons.append([kb.Btn(SCOPE_TITLE[scope], callback_data=f"list:{scope}")])
        buttons.append([kb.BACK_MENU])
        await self._show(update, "\n".join(lines), kb.Markup(buttons))

    # ---- stats -------------------------------------------------------------

    async def _stats(self, update: Update, context: Context, user_id: int, scope: str) -> None:
        view = self._view(user_id, scope)
        if view is None:
            await self._mode(update, context, user_id, scope)
            return
        today = self.today()
        members = view.pair.members if view.pair else frozenset({user_id})
        blocks = [f"📊 <b>{SCOPE_TITLE[scope]}</b> — последние 7 дней"]
        all_checkins = []
        for habit in view.habits:
            checkins = self.db.checkins(habit.id)
            if not habit.is_avoid:  # slips are not shared activity
                all_checkins.append(checkins)
            days = logic.habit_done_days(habit.kind, checkins, members, today, habit.created)
            blocks.append(
                f"{'🚫 ' if habit.is_avoid else ''}{habit.emoji} <b>{escape(habit.title)}</b>\n"
                f"{logic.week_marks(days, today)}\n"
                f"{'🛡 Без срыва' if habit.is_avoid else '🔥 Серия'}: "
                f"{logic.habit_streak(habit.kind, days, today, habit.schedule, habit.created)} · "
                f"🏆 Рекорд: {logic.best_streak(days, habit.schedule, habit.created)}"
            )
        if not view.habits:
            blocks.append("Привычек пока нет.")

        if view.pair is not None:
            sync = logic.sync_rate(all_checkins, members, today)
            blocks.append(f"🤝 Синхронность за 30 дней: {logic.percent(sync)}")
            if scope == "c":
                blocks.append(self._compatibility_block(view.pair, user_id, sync, today))
        await self._show(update, "\n\n".join(blocks), kb.Markup([[kb.back_to_mode(scope)]]))

    def _compatibility_block(self, pair: Pair, user_id: int, sync: float | None, today: date) -> str:
        partner_id = pair.other(user_id)
        month = self.db.moods(pair.id, today - timedelta(days=29))
        week = {d: s for d, s in month.items() if d >= today - timedelta(days=6)}
        match = logic.mood_match(month, pair.members)
        score = logic.compatibility_score(sync, match)

        def avg(uid: int | None) -> str:
            value = logic.average_mood(week, uid) if uid is not None else None
            if value is None:
                return "—"
            return f"{logic.MOOD_EMOJI[round(value)]} {value:.1f}"

        return (
            f"💞 <b>Совместимость: {'—' if score is None else f'{score}/100'}</b>\n"
            f"Совпадение настроения: {logic.percent(match)}\n"
            f"Настроение за неделю: ты {avg(user_id)} · {self._name(partner_id)} {avg(partner_id)}"
        )

    # ---- mood --------------------------------------------------------------

    async def _mood_prompt(self, update: Update, user_id: int) -> None:
        pair = self.db.get_pair(user_id, "couple")
        if pair is None or not pair.is_complete:
            await self._show(
                update,
                "Трекер настроения работает в режиме 💞 «Для пары». Сначала пригласи партнёра.",
                kb.main_menu(self.webapp_url),
            )
            return
        await self._show(update, "Как ты сегодня? 💭", kb.mood_menu())

    async def _set_mood(self, update: Update, context: Context, user_id: int, score: int) -> None:
        pair = self.db.get_pair(user_id, "couple")
        if pair is None or not pair.is_complete or score not in logic.MOOD_EMOJI:
            return
        today = self.today()
        self.db.set_mood(pair.id, user_id, today, score)
        partner_id = pair.other(user_id)
        partner_score = self.db.moods(pair.id, today).get(today, {}).get(partner_id)

        emoji = logic.MOOD_EMOJI[score]
        text = f"Записано: {emoji}"
        if partner_score is not None:
            text += f"\nУ {self._name(partner_id)} сегодня: {logic.MOOD_EMOJI[partner_score]}"
            if abs(partner_score - score) >= 2:
                text += "\n\nКажется, у вас сегодня разное настроение. Может, поговорить? 💬"
        await self._notify(context, partner_id, f"💭 {self._name(user_id)} сегодня: {emoji}")
        await self._show(update, text, kb.mode_menu("c"))

    # ---- plans -------------------------------------------------------------

    def plan_scope_title(self, plan: Plan) -> str:
        pair = self.db.get_pair_by_id(plan.pair_id)
        return "🤝 С другом" if pair and pair.kind == "friend" else "💞 Для пары"

    def plan_card(self, plan: Plan) -> str:
        lines = [
            f"{plan.emoji} <b>{escape(plan.title)}</b>",
            f"🗓 {logic.human_date(plan.day, plan.time)}",
        ]
        if plan.note:
            lines.append(f"💬 {escape(plan.note)}")
        return "\n".join(lines)

    async def notify_plan_proposed(self, bot: Any, plan: Plan) -> None:
        pair = self.db.get_pair_by_id(plan.pair_id)
        partner_id = pair.other(plan.author_id) if pair else None
        if partner_id is None:
            return
        text = (
            f"📅 <b>{self._name(plan.author_id)}</b> предлагает ({self.plan_scope_title(plan)}):\n\n"
            f"{self.plan_card(plan)}"
        )
        await self.send(bot, partner_id, text, kb.plan_answer(plan.id, self.webapp_url))

    async def respond_plan(self, bot: Any, plan_id: int, user_id: int, accept: bool) -> Plan:
        """Accept or decline a pending plan proposed by the partner. Raises PairError."""
        plan = self.db.get_plan(plan_id)
        pair = self.db.get_pair_by_id(plan.pair_id) if plan else None
        if plan is None or pair is None or user_id not in pair.members:
            raise PairError("План не найден или уже отменён.")
        if plan.author_id == user_id:
            raise PairError("Это твоё предложение — ответить должен второй участник.")
        if plan.status != "pending":
            raise PairError("На это предложение уже ответили.")
        status = "accepted" if accept else "declined"
        self.db.set_plan_status(plan.id, status)
        verdict = "✅ План принят" if accept else "❌ План отклонён"
        await self.send(
            bot,
            plan.author_id,
            f"{verdict} — {self._name(user_id)}:\n\n{self.plan_card(plan)}",
        )
        updated = self.db.get_plan(plan.id)
        assert updated is not None
        return updated

    async def cancel_plan(self, bot: Any, plan_id: int, user_id: int) -> None:
        plan = self.db.get_plan(plan_id)
        pair = self.db.get_pair_by_id(plan.pair_id) if plan else None
        if plan is None or pair is None or plan.author_id != user_id:
            raise PairError("Отменить можно только своё предложение.")
        self.db.delete_plan(plan.id)
        partner_id = pair.other(user_id)
        if partner_id is not None and plan.status != "declined":
            await self.send(
                bot, partner_id, f"🚫 {self._name(user_id)} отменяет план:\n\n{self.plan_card(plan)}"
            )

    async def _plan_answer(
        self, update: Update, context: Context, user_id: int, plan_id: int, accept: bool
    ) -> None:
        try:
            plan = await self.respond_plan(context.bot, plan_id, user_id, accept)
        except PairError as exc:
            await self._show(update, "😕 " + escape(str(exc)), kb.main_menu(self.webapp_url))
            return
        result = "✅ Ты принимаешь этот план" if plan.status == "accepted" else "❌ Ты отклоняешь этот план"
        await self._show(
            update,
            f"📅 Предложение от <b>{self._name(plan.author_id)}</b>:\n\n{self.plan_card(plan)}\n\n{result}",
            kb.main_menu(self.webapp_url),
        )

    # ---- reminders ---------------------------------------------------------

    async def _reminders(self, update: Update, user_id: int) -> None:
        hour = self.db.reminder_hour(user_id)
        status = "выключены" if hour is None else f"каждый день в {hour:02d}:00"
        await self._show(
            update,
            f"⏰ Напоминания: <b>{status}</b>\n\n"
            "Я напомню, только если остались неотмеченные привычки.",
            kb.reminder_menu(hour),
        )

    def pending_count(self, user_id: int, today: date) -> int:
        pending = 0
        for scope in SCOPES:
            view = self._view(user_id, scope)
            if view is None:
                continue
            pending += sum(
                1
                for h in view.habits
                if not h.is_avoid
                and logic.is_scheduled(h.schedule, today)
                and user_id not in self.db.checkins(h.id).get(today, set())
            )
        return pending

    async def reminder_job(self, context: Context) -> None:
        now = datetime.now(self.tz)
        for user_id in self.db.users_with_reminder(now.hour):
            pending = self.pending_count(user_id, now.date())
            if pending:
                await self._notify(
                    context,
                    user_id,
                    f"⏰ Осталось отметить привычек сегодня: {pending}. Не теряй серию! 🔥",
                    kb.open_today(self.webapp_url),
                )


def _split_emoji(text: str) -> tuple[str, str]:
    text = text.strip()
    head, _, rest = text.partition(" ")
    if rest and len(head) <= 4 and not any(ch.isalnum() for ch in head):
        return head, rest.strip()
    return "⭐", text
