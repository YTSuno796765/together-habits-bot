(() => {
  "use strict";

  const tg = window.Telegram && window.Telegram.WebApp;
  const initData = (tg && tg.initData) || "";
  const app = document.getElementById("app");
  const sheetRoot = document.getElementById("sheet-root");

  const SCOPES = [
    { id: "p", tab: "Личные", title: "Мои привычки" },
    { id: "f", tab: "С другом", title: "С другом" },
    { id: "c", tab: "Пара", title: "Для пары" },
  ];
  const MOODS = { 1: "😞", 2: "😕", 3: "😐", 4: "🙂", 5: "😄" };
  const WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"];
  const EMOJIS = ["⭐", "🏃", "🏋️", "📖", "🧘", "💧", "🥗", "😴", "💰", "🎯", "📝", "🇬🇧", "🚭", "📵", "🚶", "🤗",
    "💬", "🎬", "🧹", "🎸", "💊", "🌅", "🧠", "❤️"];

  const state = { scope: loadScope(), view: "home", data: null, stats: null, busy: false };

  // ---------- tiny DOM helper (textContent only, never innerHTML for data) ----------
  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === false || value == null) continue;
      if (key === "class") el.className = value;
      else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
      else if (key === "style") el.setAttribute("style", value);
      else el.setAttribute(key, value === true ? "" : value);
    }
    for (const child of children.flat(Infinity)) {
      if (child == null || child === false) continue;
      el.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return el;
  }

  const ICONS = {
    chart: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" fill="none"/>',
    plus: '<path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/>',
    check: '<path d="M5 12.5l4.5 4.5L19 7.5" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" fill="none"/>',
    back: '<path d="M15 5l-7 7 7 7" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" fill="none"/>',
    flame: '<path fill="currentColor" d="M12.6 2.2c.5 3-1.1 4.6-2.6 6.2C8.6 9.9 7 11.6 7 14.3A5 5 0 0 0 12 19.5a5 5 0 0 0 5-5.2c0-2.3-1.2-3.6-2-4.5.1 1.5-.4 2.6-1.4 3.1.5-3.4-.1-7.3-1-10.7zM12 21.8c-1.9 0-3.4-1.4-3.4-3.2 0-1.6 1.1-2.5 2-3.4.7-.7 1.3-1.4 1.4-2.5 1.4 1.1 3.4 2.9 3.4 5.6 0 1.9-1.5 3.5-3.4 3.5z" opacity=".95"/>',
    share: '<path d="M12 3v12M7 8l5-5 5 5M5 14v5a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-5" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" fill="none"/>',
  };
  function icon(name) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    svg.innerHTML = ICONS[name]; // static markup only
    return svg;
  }

  // ---------- platform ----------
  function haptic(kind) {
    try {
      if (!tg || !tg.HapticFeedback) return;
      if (kind === "success") tg.HapticFeedback.notificationOccurred("success");
      else if (kind === "select") tg.HapticFeedback.selectionChanged();
      else tg.HapticFeedback.impactOccurred(kind || "light");
    } catch (_) { /* older clients */ }
  }

  let toastTimer;
  function toast(text) {
    const el = document.getElementById("toast");
    el.textContent = text;
    el.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("show"), 2200);
  }

  function confirmDialog(text) {
    return new Promise((resolve) => {
      if (tg && tg.showConfirm && tg.isVersionAtLeast && tg.isVersionAtLeast("6.2")) tg.showConfirm(text, resolve);
      else resolve(window.confirm(text));
    });
  }

  function loadScope() {
    try {
      const saved = localStorage.getItem("habits.scope");
      if (saved === "p" || saved === "f" || saved === "c") return saved;
    } catch (_) { /* storage unavailable */ }
    return "p";
  }
  function saveScope(scope) {
    try { localStorage.setItem("habits.scope", scope); } catch (_) { /* ignore */ }
  }

  async function api(path, options) {
    const opts = options || {};
    const res = await fetch(path, {
      method: opts.method || "GET",
      headers: { Authorization: "tma " + initData, "Content-Type": "application/json" },
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    let data = null;
    try { data = await res.json(); } catch (_) { /* empty body */ }
    if (!res.ok) throw new Error((data && data.error) || "Ошибка сети, попробуй ещё раз");
    return data;
  }

  // ---------- data ----------
  async function loadState() {
    state.data = await api("/api/state?scope=" + state.scope);
  }
  async function loadStats() {
    state.stats = await api("/api/stats?scope=" + state.scope);
  }

  async function refresh() {
    try {
      await loadState();
      render();
    } catch (err) {
      renderError(err.message);
    }
  }

  // ---------- formatting ----------
  function formatDate(iso) {
    const d = new Date(iso + "T12:00:00");
    const weekday = d.toLocaleDateString("ru-RU", { weekday: "short" }).replace(".", "");
    const rest = d.toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
    const [day, month] = rest.split(" ");
    return h("div", { class: "date" }, weekday + ", ", h("b", null, day), " " + (month || ""));
  }
  const pct = (v) => (v == null ? "—" : Math.round(v * 100) + "%");
  const initial = (name) => (name || "?").trim().charAt(0).toUpperCase();

  // ---------- render: home ----------
  function render() {
    syncBackButton();
    if (state.view === "stats") return renderStats();
    const d = state.data;
    const scopeInfo = SCOPES.find((s) => s.id === state.scope);
    const pairActive = !d.pair || d.pair.status === "active";

    const header = h("div", { class: "topbar" },
      formatDate(d.today),
      h("div", { class: "actions" },
        pairActive && d.habits.length ? h("button", { class: "icon-btn", "aria-label": "Статистика", onclick: openStats }, icon("chart")) : null,
        pairActive ? h("button", { class: "icon-btn", "aria-label": "Добавить привычку", onclick: openAddSheet }, icon("plus")) : null,
      ),
    );

    const tabs = h("div", { class: "tabs", role: "tablist" },
      SCOPES.map((s) => h("button", {
        class: "tab" + (s.id === state.scope ? " active" : ""),
        role: "tab",
        "aria-selected": s.id === state.scope ? "true" : "false",
        onclick: () => switchScope(s.id),
      }, s.tab)),
    );

    const page = [header, h("h1", null, scopeInfo.title)];
    if (d.pair && d.pair.status === "active") {
      page.push(h("p", { class: "subtitle" }, "Вы и ", h("b", null, d.pair.partner), state.scope === "c" ? " 💞" : " 🤝"));
    }
    page.push(tabs);

    if (!pairActive) {
      page.push(renderInvite(d));
    } else {
      page.push(renderProgress(d));
      if (state.scope === "c") page.push(renderMood(d));
      page.push(...renderHabitSections(d));
    }
    app.replaceChildren(h("div", { class: "fade-in" }, page));
  }

  function renderProgress(d) {
    const { done, total } = d.progress;
    const width = total ? Math.round((done / total) * 100) : 0;
    const hint = !d.habits.length ? "Добавь первую привычку — нажми +"
      : total === 0 ? "Сегодня выходной от привычек 🌿"
      : done === total ? "Всё выполнено. Ты красавчик 🔥"
      : "Осталось: " + (total - done);
    return h("div", { class: "panel progress-card", style: "margin-top:4px" },
      h("div", { class: "row" }, h("span", { class: "label" }, "Сегодня"), h("span", { class: "value" }, done + " из " + total)),
      h("div", { class: "bar" }, h("span", { style: "width:" + width + "%" })),
      h("div", { class: "hint" }, hint),
    );
  }

  function renderMood(d) {
    const mood = d.mood || {};
    const partner = d.pair && d.pair.partner;
    return h("div", { class: "panel mood-card", style: "margin-top:14px" },
      h("h3", null, "Как ты сегодня?"),
      h("div", { class: "mood-row" },
        Object.entries(MOODS).map(([score, emoji]) => h("button", {
          class: "mood" + (mood.me === Number(score) ? " on" : ""),
          "aria-label": "Настроение " + score,
          onclick: () => setMood(Number(score)),
        }, emoji)),
      ),
      h("div", { class: "mood-partner" },
        partner + ": ", mood.partner ? h("b", null, MOODS[mood.partner]) : "ещё нет отметки"),
    );
  }

  function renderHabitSections(d) {
    if (!d.habits.length) {
      return [h("div", { class: "panel hero", style: "margin-top:14px" },
        h("div", { class: "big" }, state.scope === "p" ? "🎯" : state.scope === "f" ? "🤝" : "💞"),
        h("h2", null, "Пока пусто"),
        h("p", null, "Выбери привычку из шаблонов или придумай свою."),
        h("button", { class: "btn", onclick: openAddSheet }, icon("plus"), "Добавить привычку"),
      )];
    }
    const todo = d.habits.filter((x) => x.dueToday && !x.meDone);
    const done = d.habits.filter((x) => x.dueToday && x.meDone);
    const off = d.habits.filter((x) => !x.dueToday);
    const out = [];
    if (todo.length) out.push(h("div", { class: "section-title" }, "Сделать"), todo.map(habitCard));
    if (done.length) out.push(h("div", { class: "section-title" }, "Готово"), done.map(habitCard));
    if (off.length) out.push(h("div", { class: "section-title" }, "Не сегодня"), off.map(habitCard));
    return out;
  }

  function habitCard(x) {
    const partner = state.data.pair && state.data.pair.partner;
    const card = h("div", {
      class: "habit" + (x.dueToday && x.meDone && (x.partnerDone !== false) ? " is-done" : "") + (!x.dueToday ? " is-off" : ""),
      role: "button",
      tabindex: "0",
      onclick: () => openHabitSheet(x),
    },
      h("div", { class: "habit-top" },
        h("div", { class: "habit-icon" }, x.emoji),
        h("button", {
          class: "check" + (x.meDone ? " on" : ""),
          "aria-label": x.meDone ? "Снять отметку" : "Отметить выполнение",
          "aria-pressed": x.meDone ? "true" : "false",
          onclick: (e) => { e.stopPropagation(); toggle(x, e.currentTarget); },
        }, icon("check")),
      ),
      h("div", { class: "habit-title" }, x.title),
      h("div", { class: "habit-meta" }, x.scheduleLabel),
      x.partnerDone !== null && x.partnerDone !== undefined ? h("div", { class: "people" },
        h("span", { class: "person" + (x.meDone ? " on" : "") }, h("span", { class: "dot" }, x.meDone ? "✓" : initial(state.data.me.name)), "Ты"),
        h("span", { class: "person" + (x.partnerDone ? " on" : "") }, h("span", { class: "dot" }, x.partnerDone ? "✓" : initial(partner)), partner),
      ) : null,
      h("div", { class: "week", "aria-label": "Эта неделя" },
        x.week.map((w) => h("span", { class: "seg " + w.status, title: w.date })),
        h("span", { class: "streak", "aria-label": "Серия " + x.streak }, icon("flame"), String(x.streak)),
      ),
    );
    return card;
  }

  function renderInvite(d) {
    const isFriend = state.scope === "f";
    if (d.pair.status === "pending") {
      return h("div", { class: "panel hero" },
        h("div", { class: "big" }, "⏳"),
        h("h2", null, "Ждём " + (isFriend ? "друга" : "вторую половинку")),
        h("p", null, "Отправь ссылку-приглашение. Как только по ней нажмут «Start», вы окажетесь в связке."),
        h("button", { class: "btn", onclick: () => shareLink(d.pair.link) }, icon("share"), "Отправить ссылку"),
        h("button", { class: "btn ghost", onclick: refresh }, "Обновить"),
      );
    }
    return h("div", { class: "panel hero" },
      h("div", { class: "big" }, isFriend ? "🤝" : "💞"),
      h("h2", null, isFriend ? "Привычки с другом" : "Привычки для пары"),
      h("p", null, isFriend
        ? "Общие привычки, совместные серии и уведомления, когда друг уже отметился."
        : "Привычки, которые сближают, настроение каждый день и ваша совместимость."),
      h("button", { class: "btn", onclick: invite }, icon("share"), isFriend ? "Пригласить друга" : "Пригласить пару"),
    );
  }

  // ---------- render: stats ----------
  function renderStats() {
    const s = state.stats;
    const d = state.data;
    const page = [
      h("button", { class: "back", onclick: closeStats }, icon("back"), "Назад"),
      h("h1", null, "Статистика"),
      h("div", { class: "tiles" },
        tile(pct(s.rate7), "за 7 дней"),
        tile(pct(s.rate30), "за 30 дней"),
        tile(String(s.best), "лучшая серия"),
      ),
    ];

    if (s.pair) {
      const pairTiles = [tile(pct(s.pair.sync), "синхронность", true)];
      if (s.pair.compatibility !== undefined) {
        pairTiles.unshift(tile(s.pair.compatibility == null ? "—" : String(s.pair.compatibility), "совместимость из 100", true));
      }
      page.push(h("div", { class: "tiles two" }, pairTiles));
    }

    const todayIso = d.today;
    page.push(
      h("div", { class: "section-title" }, "Выполнение по дням"),
      barChart(s.daily.map((x) => ({
        label: x.label, done: x.done, due: x.due, now: x.date === todayIso,
        caption: new Date(x.date + "T12:00:00").toLocaleDateString("ru-RU", { day: "numeric", month: "long" }),
      }))),
      h("div", { class: "section-title" }, "По дням недели"),
      barChart(s.weekday.map((x) => ({ label: x.label, done: x.done, due: x.due, caption: x.label }))),
    );

    if (s.pair && s.pair.moodWeek) {
      page.push(h("div", { class: "section-title" }, "Настроение за неделю"), moodWeek(s.pair));
    }

    page.push(
      h("div", { class: "section-title" }, "Привычки за 30 дней"),
      h("div", { class: "panel hlist" }, s.habits.map((x) => h("div", { class: "item" },
        h("div", { class: "ic" }, x.emoji),
        h("div", { class: "body" },
          h("div", { class: "name" }, x.title),
          h("div", { class: "mini" }, h("span", { style: "width:" + Math.round((x.rate30 || 0) * 100) + "%" })),
          h("div", { class: "sub" }, "серия " + x.streak + " · рекорд " + x.best),
        ),
        h("div", { class: "pct" }, pct(x.rate30)),
      ))),
    );
    app.replaceChildren(h("div", { class: "fade-in" }, page));
  }

  function tile(num, caption, accent) {
    return h("div", { class: "tile" + (accent ? " accent" : "") }, h("div", { class: "num" }, num), h("div", { class: "cap" }, caption));
  }

  function barChart(items) {
    const hint = h("div", { class: "chart-hint" }, "Нажми на столбец");
    const cols = items.map((it, i) => {
      const ratio = it.due ? it.done / it.due : 0;
      const col = h("button", {
        class: "col",
        "aria-label": it.caption + ": " + it.done + " из " + it.due,
        onclick: () => {
          cols.forEach((c) => c.classList.remove("sel"));
          col.classList.add("sel");
          haptic("select");
          hint.replaceChildren(it.caption + ": ", h("b", null, it.due ? it.done + " из " + it.due + " · " + Math.round(ratio * 100) + "%" : "нет привычек"));
        },
      }, h("span", { class: ratio ? "" : "zero", style: "height:" + Math.max(ratio * 100, 2) + "%; animation-delay:" + i * 25 + "ms" }));
      return col;
    });
    return h("div", { class: "panel" },
      hint,
      h("div", { class: "chart" }, cols),
      h("div", { class: "labels" }, items.map((it) => h("span", { class: it.now ? "now" : "" }, it.label))),
    );
  }

  function moodWeek(pair) {
    const row = (key, who) => [h("div", { class: "who" }, who)].concat(
      pair.moodWeek.map((x) => h("div", { class: "e" + (x[key] ? "" : " none") }, x[key] ? MOODS[x[key]] : "·")));
    const avg = (v) => (v == null ? "—" : MOODS[Math.round(v)] + " " + v.toFixed(1));
    return h("div", { class: "panel" },
      h("div", { class: "mood-week" },
        h("div", null),
        pair.moodWeek.map((x) => h("div", { class: "d" }, x.label)),
        row("me", "Ты"),
        row("partner", pair.partner),
      ),
      h("div", { class: "legend" },
        h("span", null, "Ты: " + avg(pair.myMood)),
        h("span", null, pair.partner + ": " + avg(pair.partnerMood)),
      ),
      h("div", { class: "legend" }, h("span", null, "Совпадение настроения: " + pct(pair.moodMatch))),
    );
  }

  function renderError(message) {
    syncBackButton();
    app.replaceChildren(h("div", { class: "panel hero", style: "margin-top:30vh" },
      h("div", { class: "big" }, "😕"),
      h("h2", null, "Что-то пошло не так"),
      h("p", null, message),
      initData ? h("button", { class: "btn", onclick: refresh }, "Попробовать снова") : null,
    ));
  }

  // ---------- actions ----------
  async function switchScope(scope) {
    if (scope === state.scope) return;
    haptic("select");
    state.scope = scope;
    saveScope(scope);
    await refresh();
  }

  async function toggle(habit, button) {
    if (state.busy) return;
    state.busy = true;
    const wasDone = habit.meDone;
    habit.meDone = !wasDone;
    button.classList.toggle("on", habit.meDone);
    button.classList.add("pop");
    haptic(habit.meDone ? "success" : "light");
    try {
      await api("/api/checkin", { method: "POST", body: { habitId: habit.id } });
      await loadState();
      render();
      const fresh = state.data.habits.find((x) => x.id === habit.id);
      if (!wasDone && fresh) {
        if (fresh.partnerDone === false) toast("Отмечено! Ждём " + state.data.pair.partner + " 👀");
        else if (fresh.streak > 1) toast("Серия " + fresh.streak + " 🔥");
        else toast("Отмечено ✅");
      }
    } catch (err) {
      habit.meDone = wasDone;
      render();
      toast(err.message);
    } finally {
      state.busy = false;
    }
  }

  async function setMood(score) {
    haptic("select");
    try {
      await api("/api/mood", { method: "POST", body: { score } });
      await loadState();
      render();
      const partner = state.data.mood && state.data.mood.partner;
      if (partner && Math.abs(partner - score) >= 2) toast("У вас разное настроение — может, поговорить? 💬");
      else toast("Записано " + MOODS[score]);
    } catch (err) {
      toast(err.message);
    }
  }

  async function invite() {
    try {
      const res = await api("/api/invite", { method: "POST", body: { scope: state.scope } });
      await loadState();
      render();
      if (res.link) shareLink(res.link);
    } catch (err) {
      toast(err.message);
    }
  }

  function shareLink(link) {
    if (!link) return toast("Ссылка появится через пару секунд — нажми «Обновить»");
    const text = state.scope === "f" ? "Го вместе качать привычки 💪" : "Давай вместе следить за привычками и настроением 💞";
    const url = "https://t.me/share/url?url=" + encodeURIComponent(link) + "&text=" + encodeURIComponent(text);
    if (tg && tg.openTelegramLink) tg.openTelegramLink(url);
    else window.open(url, "_blank");
  }

  async function openStats() {
    haptic("light");
    try {
      await loadStats();
      state.view = "stats";
      render();
      window.scrollTo(0, 0);
    } catch (err) {
      toast(err.message);
    }
  }
  function closeStats() {
    state.view = "home";
    render();
  }

  // ---------- sheets ----------
  let closeCurrentSheet = null;
  function openSheet(build) {
    closeSheet();
    const backdrop = h("div", { class: "sheet-backdrop", onclick: closeSheet });
    const sheet = h("div", { class: "sheet", role: "dialog", "aria-modal": "true" }, h("div", { class: "grab" }), build());
    sheetRoot.replaceChildren(backdrop, sheet);
    document.body.style.overflow = "hidden";
    closeCurrentSheet = () => {
      sheetRoot.replaceChildren();
      document.body.style.overflow = "";
      closeCurrentSheet = null;
      syncBackButton();
    };
    syncBackButton();
  }
  function closeSheet() {
    if (closeCurrentSheet) closeCurrentSheet();
  }

  function openAddSheet() {
    haptic("light");
    const form = { title: "", emoji: "⭐", schedule: 127 };
    openSheet(() => {
      const input = h("input", {
        class: "input", maxlength: "40", placeholder: "Например, «Прогулка 30 минут»", enterkeyhint: "done",
        oninput: (e) => { form.title = e.target.value; submit.disabled = !form.title.trim(); },
      });
      const emojiButtons = EMOJIS.map((em) => h("button", {
        class: "emoji-opt" + (em === form.emoji ? " on" : ""),
        onclick: () => { form.emoji = em; emojiButtons.forEach((b) => b.classList.toggle("on", b.textContent === em)); haptic("select"); },
      }, em));
      const dayButtons = WEEKDAYS.map((label, i) => h("button", {
        class: "day on",
        onclick: () => {
          form.schedule ^= 1 << i;
          paintDays();
          haptic("select");
        },
      }, label));
      function paintDays() {
        dayButtons.forEach((b, i) => b.classList.toggle("on", Boolean(form.schedule & (1 << i))));
        submit.disabled = !form.title.trim() || !form.schedule;
      }
      const preset = (label, mask) => h("button", { class: "chip", onclick: () => { form.schedule = mask; paintDays(); } }, label);
      const templates = (state.data.templates || []).filter((t) => !state.data.habits.some((x) => x.title === t.title));
      const submit = h("button", {
        class: "btn", disabled: true,
        onclick: async () => {
          submit.disabled = true;
          try {
            await api("/api/habits", { method: "POST", body: { scope: state.scope, title: form.title, emoji: form.emoji, schedule: form.schedule } });
            haptic("success");
            closeSheet();
            await refresh();
            toast("Привычка добавлена ✨");
          } catch (err) {
            submit.disabled = false;
            toast(err.message);
          }
        },
      }, "Добавить");

      return h("div", null,
        h("h2", null, "Новая привычка"),
        templates.length ? [
          h("div", { class: "field-label" }, "Шаблоны"),
          h("div", { class: "chips" }, templates.map((t) => h("button", {
            class: "chip",
            onclick: () => {
              form.title = t.title; input.value = t.title;
              if (!EMOJIS.includes(t.emoji)) emojiButtons[0].textContent = t.emoji;
              form.emoji = t.emoji;
              emojiButtons.forEach((b) => b.classList.toggle("on", b.textContent === t.emoji));
              paintDays();
              haptic("select");
            },
          }, t.emoji + " " + t.title))),
        ] : null,
        h("div", { class: "field-label" }, "Название"),
        input,
        h("div", { class: "field-label" }, "Иконка"),
        h("div", { class: "emojis" }, emojiButtons),
        h("div", { class: "field-label" }, "Дни"),
        h("div", { class: "days" }, dayButtons),
        h("div", { class: "presets" }, preset("Каждый день", 127), preset("Будни", 31), preset("Выходные", 96)),
        submit,
      );
    });
  }

  function openHabitSheet(x) {
    haptic("light");
    openSheet(() => h("div", null,
      h("div", { class: "detail-head" },
        h("div", { class: "habit-icon" }, x.emoji),
        h("div", null, h("h2", null, x.title), h("div", { class: "habit-meta" }, x.scheduleLabel)),
      ),
      h("div", { class: "tiles two" }, tile(String(x.streak), "текущая серия"), tile(String(x.best), "рекорд")),
      h("div", { class: "week", style: "margin:18px 0 6px" }, x.week.map((w) => h("span", { class: "seg " + w.status }))),
      h("div", { class: "labels" }, WEEKDAYS.map((w) => h("span", null, w))),
      h("button", {
        class: "btn danger", style: "margin-top:26px",
        onclick: async () => {
          const ok = await confirmDialog("Удалить «" + x.title + "»? История отметок пропадёт.");
          if (!ok) return;
          try {
            await api("/api/habits/" + x.id, { method: "DELETE" });
            closeSheet();
            await refresh();
            toast("Удалено");
          } catch (err) {
            toast(err.message);
          }
        },
      }, "Удалить привычку"),
      h("button", { class: "btn ghost", onclick: closeSheet }, "Закрыть"),
    ));
  }

  // ---------- Telegram back button ----------
  function onBack() {
    if (closeCurrentSheet) closeSheet();
    else if (state.view === "stats") closeStats();
  }
  function syncBackButton() {
    if (!tg || !tg.BackButton || !tg.isVersionAtLeast || !tg.isVersionAtLeast("6.1")) return;
    if (closeCurrentSheet || state.view === "stats") tg.BackButton.show();
    else tg.BackButton.hide();
  }

  // ---------- boot ----------
  function boot() {
    if (tg) {
      tg.ready();
      tg.expand();
      try {
        if (tg.isVersionAtLeast && tg.isVersionAtLeast("6.1")) {
          tg.setHeaderColor("#0a0b0c");
          tg.setBackgroundColor("#0a0b0c");
          tg.BackButton.onClick(onBack);
        }
        if (tg.isVersionAtLeast && tg.isVersionAtLeast("7.7") && tg.disableVerticalSwipes) tg.disableVerticalSwipes();
      } catch (_) { /* older clients */ }
    }
    if (!initData) {
      renderError("Открой приложение из Telegram — кнопкой «Привычки» в чате с ботом.");
      return;
    }
    refresh();
  }

  boot();
})();
