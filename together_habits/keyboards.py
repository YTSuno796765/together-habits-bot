"""Inline keyboards. Callback data stays well under Telegram's 64-byte limit."""

from __future__ import annotations

from telegram import InlineKeyboardButton as Btn
from telegram import InlineKeyboardMarkup as Markup
from telegram import WebAppInfo

from together_habits.logic import MOOD_EMOJI
from together_habits.texts import SCOPE_TITLE, TEMPLATES

BACK_MENU = Btn("⬅️ В меню", callback_data="menu")


def app_button(webapp_url: str | None) -> list[list[Btn]]:
    if not webapp_url:
        return []
    return [[Btn("📱 Открыть приложение", web_app=WebAppInfo(url=webapp_url))]]


def main_menu(
    webapp_url: str | None = None,
    titles: dict[str, str] | None = None,
    hidden: set[str] | frozenset[str] = frozenset(),
) -> Markup:
    titles = {**SCOPE_TITLE, **(titles or {})}
    shared = [Btn(titles[s], callback_data=f"mode:{s}") for s in ("f", "c") if s not in hidden]
    return Markup(
        app_button(webapp_url)
        + ([shared] if shared else [])
        + [
            [Btn(titles["p"], callback_data="mode:p")],
            [Btn("✅ Отметить сегодня", callback_data="today")],
            [Btn("⏰ Напоминания", callback_data="rem"), Btn("❓ Помощь", callback_data="help")],
        ]
    )


def back_to_mode(scope: str) -> Btn:
    return Btn("⬅️ Назад", callback_data=f"mode:{scope}")


def mode_menu(scope: str) -> Markup:
    rows = [
        [Btn("✅ Отметить сегодня", callback_data=f"list:{scope}")],
        [Btn("➕ Добавить привычку", callback_data=f"add:{scope}"), Btn("📊 Статистика", callback_data=f"stats:{scope}")],
    ]
    if scope == "c":
        rows.append([Btn("😊 Настроение", callback_data="mood")])
    rows.append([Btn("🗑 Удалить привычку", callback_data=f"manage:{scope}")])
    if scope != "p":
        rows.append([Btn("🚪 Разорвать связку", callback_data=f"unpair:{scope}")])
    rows.append([BACK_MENU])
    return Markup(rows)


def invite_menu(scope: str, has_invite: bool) -> Markup:
    rows = []
    if has_invite:
        rows.append([Btn("❌ Отменить приглашение", callback_data=f"unpair:{scope}")])
    else:
        rows.append([Btn("📨 Пригласить", callback_data=f"invite:{scope}")])
    rows.append([BACK_MENU])
    return Markup(rows)


def templates_menu(scope: str, existing_titles: set[str]) -> Markup:
    rows = [
        [Btn(f"{emoji} {title}", callback_data=f"tpl:{scope}:{index}")]
        for index, (emoji, title) in enumerate(TEMPLATES[scope])
        if title not in existing_titles
    ]
    rows.append([Btn("✍️ Своя привычка", callback_data=f"custom:{scope}")])
    rows.append([back_to_mode(scope)])
    return Markup(rows)


def checklist(scope: str, items: list[tuple[int, str]]) -> Markup:
    rows = [[Btn(label, callback_data=f"chk:{habit_id}")] for habit_id, label in items]
    if not items:
        rows.append([Btn("➕ Добавить привычку", callback_data=f"add:{scope}")])
    rows.append([back_to_mode(scope)])
    return Markup(rows)


def manage_menu(scope: str, items: list[tuple[int, str]]) -> Markup:
    rows = [[Btn(f"🗑 {label}", callback_data=f"del:{habit_id}")] for habit_id, label in items]
    rows.append([back_to_mode(scope)])
    return Markup(rows)


def confirm_unpair(scope: str) -> Markup:
    return Markup(
        [
            [Btn("Да, разорвать", callback_data=f"unpairok:{scope}")],
            [back_to_mode(scope)],
        ]
    )


def mood_menu() -> Markup:
    return Markup(
        [
            [Btn(emoji, callback_data=f"moodset:{score}") for score, emoji in MOOD_EMOJI.items()],
            [back_to_mode("c")],
        ]
    )


def reminder_menu(current: int | None) -> Markup:
    hours = [8, 9, 12, 18, 20, 21, 22]
    buttons = [
        Btn(f"{'• ' if hour == current else ''}{hour:02d}:00", callback_data=f"remset:{hour}")
        for hour in hours
    ]
    return Markup(
        [
            buttons[:4],
            buttons[4:],
            [Btn("🔕 Выключить", callback_data="remset:off")],
            [BACK_MENU],
        ]
    )


def open_today(webapp_url: str | None = None) -> Markup:
    return Markup(app_button(webapp_url) or [[Btn("✅ Отметить", callback_data="today")]])


def plan_answer(plan_id: int, webapp_url: str | None = None) -> Markup:
    rows = [[
        Btn("✅ Принять", callback_data=f"plan:{plan_id}:y"),
        Btn("❌ Отклонить", callback_data=f"plan:{plan_id}:n"),
    ]]
    if webapp_url:
        rows.append([Btn("📅 Календарь", web_app=WebAppInfo(url=webapp_url))])
    return Markup(rows)
