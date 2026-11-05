"""End-to-end flows through the handlers with fake Telegram objects."""

import asyncio
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from together_habits.db import Storage
from together_habits.handlers import Handlers, _split_emoji


class FakeBot:
    username = "test_habits_bot"

    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))


class FakeMessage:
    def __init__(self, text=""):
        self.text = text
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs.get("reply_markup")))


class FakeQuery:
    def __init__(self, data):
        self.data = data
        self.message = object()
        self.edits = []
        self.answers = []

    async def answer(self, text=None, **kwargs):
        self.answers.append(text)

    async def edit_message_text(self, text, **kwargs):
        self.edits.append((text, kwargs.get("reply_markup")))


class FakeUpdate:
    def __init__(self, user_id, bot, *, query=None, message=None):
        self.effective_user = SimpleNamespace(id=user_id, first_name=f"U{user_id}", username=None)
        self.callback_query = query
        self.effective_message = message
        self._bot = bot

    def get_bot(self):
        return self._bot


class Harness:
    def __init__(self):
        self.bot = FakeBot()
        self.db = Storage(":memory:")
        self.h = Handlers(self.db, ZoneInfo("UTC"))
        self.user_data = {1: {}, 2: {}}

    def ctx(self, user_id, args=None):
        return SimpleNamespace(bot=self.bot, args=args or [], user_data=self.user_data[user_id])

    def press(self, user_id, data):
        query = FakeQuery(data)
        asyncio.run(self.h.on_callback(FakeUpdate(user_id, self.bot, query=query), self.ctx(user_id)))
        return query

    def start(self, user_id, args=None):
        message = FakeMessage("/start")
        asyncio.run(self.h.start(FakeUpdate(user_id, self.bot, message=message), self.ctx(user_id, args)))
        return message

    def say(self, user_id, text):
        message = FakeMessage(text)
        asyncio.run(self.h.on_text(FakeUpdate(user_id, self.bot, message=message), self.ctx(user_id)))
        return message


@pytest.fixture
def hx():
    harness = Harness()
    yield harness
    harness.db.close()


def pair_up(hx, scope="f"):
    hx.start(1)
    hx.start(2)
    query = hx.press(1, f"invite:{scope}")
    link_text = query.edits[-1][0]
    code = link_text.split("start=join_")[1].split()[0]
    hx.start(2, [f"join_{code}"])
    return code


def test_friend_flow_notifies_partner(hx):
    pair_up(hx, "f")
    assert any(chat == 1 and "присоединяется" in text for chat, text in hx.bot.sent)

    hx.press(1, "tpl:f:0")  # Спорт
    habit = hx.db.list_habits(pair_id=hx.db.get_pair(1, "friend").id)[0]

    hx.bot.sent.clear()
    first = hx.press(1, f"chk:{habit.id}")
    assert first.answers == ["✅ Отмечено"]
    assert hx.bot.sent and hx.bot.sent[-1][0] == 2 and "Твоя очередь" in hx.bot.sent[-1][1]

    second = hx.press(2, f"chk:{habit.id}")
    assert "Серия 1" in second.answers[-1]
    assert "Вы оба" in hx.bot.sent[-1][1]


def test_custom_habit_and_stats(hx):
    hx.start(1)
    hx.press(1, "custom:p")
    reply = hx.say(1, "🥗 Полезный завтрак")
    assert "Полезный завтрак" in reply.replies[-1][0]
    habit = hx.db.list_habits(owner_id=1)[0]
    assert habit.emoji == "🥗"
    hx.press(1, f"chk:{habit.id}")
    stats = hx.press(1, "stats:p")
    assert "Серия: 1" in stats.edits[-1][0]


def test_couple_mood_and_compatibility(hx):
    pair_up(hx, "c")
    hx.press(1, "moodset:5")
    result = hx.press(2, "moodset:2")
    assert "разное настроение" in result.edits[-1][0]
    stats = hx.press(1, "stats:c")
    assert "Совместимость" in stats.edits[-1][0]


def test_shared_modes_require_pair(hx):
    hx.start(1)
    query = hx.press(1, "list:f")
    assert "Пригласить" in str(query.edits[-1][1].to_dict())
    query = hx.press(1, "mood")
    assert "Для пары" in query.edits[-1][0]


def test_stranger_cannot_toggle(hx):
    hx.start(1)
    hx.press(1, "tpl:p:0")
    habit = hx.db.list_habits(owner_id=1)[0]
    hx.start(2)
    query = hx.press(2, f"chk:{habit.id}")
    assert query.answers == ["Привычка не найдена"]
    assert hx.db.checkins(habit.id) == {}


def test_reminder_counts_pending(hx):
    from datetime import datetime

    hx.start(1)
    hx.press(1, "tpl:p:0")
    hx.press(1, "tpl:p:1")
    today = datetime.now(ZoneInfo("UTC")).date()
    assert hx.h.pending_count(1, today) == 2


def test_split_emoji():
    assert _split_emoji("🥗 Завтрак") == ("🥗", "Завтрак")
    assert _split_emoji("Бег по утрам") == ("⭐", "Бег по утрам")


def test_avoid_habit_in_bot_list(hx):
    from datetime import datetime

    hx.start(1)
    today = datetime.now(ZoneInfo("UTC")).date()
    habit = hx.db.add_habit("Курить", "🚭", owner_id=1, kind="avoid", created=today)
    listing = hx.press(1, "list:p")
    assert "🟢 🚫 🚭 Курить" in str(listing.edits[-1][1].to_dict())
    slip = hx.press(1, f"chk:{habit.id}")
    assert slip.answers == ["💥 Срыв отмечен. Начнём заново!"]
    # "Don't do" habits never trigger reminders.
    assert hx.h.pending_count(1, today) == 0
    stats = hx.press(1, "stats:p")
    assert "Без срыва: 0" in stats.edits[-1][0]


def test_plan_buttons_in_bot(hx):
    from datetime import date, timedelta

    pair_up(hx, "c")
    pair = hx.db.get_pair(1, "couple")
    plan = hx.db.add_plan(pair.id, 1, "Ужин", date.today() + timedelta(days=2), emoji="🍽", time="19:00")
    asyncio.run(hx.h.notify_plan_proposed(hx.bot, plan))
    assert hx.bot.sent[-1][0] == 2 and "Ужин" in hx.bot.sent[-1][1]

    own = hx.press(1, f"plan:{plan.id}:y")
    assert "твоё предложение" in own.edits[-1][0]
    answer = hx.press(2, f"plan:{plan.id}:y")
    assert "принимаешь" in answer.edits[-1][0]
    assert hx.db.get_plan(plan.id).status == "accepted"
    assert hx.bot.sent[-1][0] == 1 and "принят" in hx.bot.sent[-1][1]
    again = hx.press(2, f"plan:{plan.id}:n")
    assert "уже ответили" in again.edits[-1][0]
