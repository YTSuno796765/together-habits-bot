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
    "💬", "🎬", "🧹", "🎸", "💊", "🌅", "🧠", "❤️", "🚫", "🍺", "🍔", "🍬", "🎮", "🎰", "😤", "🍕"];

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
    shield: '<path fill="currentColor" d="M12 2.5l7.5 3v6c0 4.6-3.2 8.6-7.5 10-4.3-1.4-7.5-5.4-7.5-10v-6z"/><path d="M8.5 12.2l2.4 2.4 4.6-4.8" stroke="#1b1724" stroke-width="2.2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    sort: '<path d="M7 4v16M7 4L3.5 7.5M7 4l3.5 3.5M17 20V4m0 16l-3.5-3.5M17 20l3.5-3.5" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" fill="none"/>',
    pin: '<path fill="currentColor" d="M14.6 2.6l6.8 6.8-2.1.7-3.6 3.6.4 4.3-1.6 1.6-3.9-3.9-5.5 5.5-1.4-1.4 5.5-5.5L5.3 10.4 6.9 8.8l4.3.4 3.6-3.6z"/>',
    grip: '<path d="M9 6h.01M15 6h.01M9 12h.01M15 12h.01M9 18h.01M15 18h.01" stroke="currentColor" stroke-width="3.2" stroke-linecap="round"/>',
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
        pairActive && d.habits.length > 1 ? h("button", { class: "icon-btn", "aria-label": "Порядок привычек", onclick: openOrderSheet }, icon("sort")) : null,
        pairActive && d.habits.length ? h("button", { class: "icon-btn", "aria-label": "Статистика", onclick: openStats }, icon("chart")) : null,
        pairActive ? h("button", { class: "icon-btn", "aria-label": "Добавить привычку", onclick: () => openAddSheet("do") }, icon("plus")) : null,
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
    const hasDo = d.habits.some((x) => x.kind !== "avoid");
    const hint = !d.habits.length ? "Добавь первую привычку — нажми +"
      : !hasDo ? "Добавь то, что хочешь делать каждый день — нажми +"
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
    const doHabits = d.habits.filter((x) => x.kind !== "avoid");
    const avoid = d.habits.filter((x) => x.kind === "avoid");
    const pinned = doHabits.filter((x) => x.pinned);
    const rest = doHabits.filter((x) => !x.pinned);
    const todo = rest.filter((x) => x.dueToday && !x.meDone);
    const done = rest.filter((x) => x.dueToday && x.meDone);
    const off = rest.filter((x) => !x.dueToday);
    const grid = (items) => h("div", { class: "grid" }, items.map(habitCard));
    const out = [];
    if (!doHabits.length) {
      out.push(h("div", { class: "panel hero", style: "margin-top:14px" },
        h("div", { class: "big" }, state.scope === "p" ? "🎯" : state.scope === "f" ? "🤝" : "💞"),
        h("h2", null, "Пока пусто"),
        h("p", null, "Выбери привычку из шаблонов или придумай свою."),
        h("button", { class: "btn", onclick: () => openAddSheet("do") }, icon("plus"), "Добавить привычку"),
      ));
    }
    if (pinned.length) out.push(h("div", { class: "section-title" }, "📌 Закреплено"), grid(pinned));
    if (todo.length) out.push(h("div", { class: "section-title" }, "Сделать"), grid(todo));
    if (done.length) out.push(h("div", { class: "section-title" }, "Готово"), grid(done));
    if (off.length) out.push(h("div", { class: "section-title" }, "Не сегодня"), grid(off));
    out.push(
      h("div", { class: "section-title row-title" },
        h("span", null, "Не делать"),
        h("button", { class: "mini-add", onclick: () => openAddSheet("avoid") }, "+ добавить"),
      ),
      avoid.length
        ? [h("div", { class: "section-hint" }, "Каждый день засчитывается сам. Жми «Срыв», только если сорвался."), grid(avoid)]
        : h("button", { class: "avoid-empty", onclick: () => openAddSheet("avoid") },
          "🚫 Добавь то, от чего отказываешься: курение, сладкое, думскроллинг…"),
    );
    return out;
  }

  function personDots(x, markedClass) {
    if (x.partnerMarked === null || x.partnerMarked === undefined) return null;
    const partner = state.data.pair.partner;
    const dot = (name, marked) => h("span", { class: "pdot" + (marked ? " " + markedClass : ""), title: name }, initial(name));
    return h("div", { class: "pdots" }, dot(state.data.me.name, x.meMarked), dot(partner, x.partnerMarked));
  }

  function habitCard(x) {
    const isAvoid = x.kind === "avoid";
    const slipped = isAvoid && (x.meMarked || x.partnerMarked);
    const classes = ["habit", isAvoid ? "avoid" : ""];
    if (!isAvoid && x.dueToday && x.meDone && x.partnerDone !== false) classes.push("is-done");
    if (!isAvoid && !x.dueToday) classes.push("is-off");
    if (slipped) classes.push("slipped");

    const action = isAvoid
      ? h("button", {
        class: "slip" + (x.meMarked ? " on" : ""),
        "aria-pressed": x.meMarked ? "true" : "false",
        onclick: (e) => { e.stopPropagation(); toggle(x, e.currentTarget); },
      }, x.meMarked ? "Отменить" : "Срыв")
      : h("button", {
        class: "check" + (x.meMarked ? " on" : ""),
        "aria-label": x.meMarked ? "Снять отметку" : "Отметить выполнение",
        "aria-pressed": x.meMarked ? "true" : "false",
        onclick: (e) => { e.stopPropagation(); toggle(x, e.currentTarget); },
      }, icon("check"));

    const meta = (x.pinned ? "📌 " : "") + (isAvoid
      ? (x.meMarked ? "срыв сегодня" : x.partnerMarked ? "срыв у " + state.data.pair.partner : "держусь")
      : x.scheduleLabel);

    return h("div", { class: classes.join(" "), role: "button", tabindex: "0", onclick: () => openHabitSheet(x) },
      h("div", { class: "habit-top" }, h("div", { class: "habit-icon" }, x.emoji), action),
      h("div", { class: "habit-title" }, x.title),
      h("div", { class: "habit-meta" }, meta),
      h("div", { class: "habit-bottom" },
        h("div", { class: "week", "aria-label": "Эта неделя" }, x.week.map((w) => h("span", { class: "seg " + w.status, title: w.date }))),
        h("div", { class: "habit-foot" },
          personDots(x, isAvoid ? "bad" : "good"),
          h("span", { class: "streak" + (isAvoid ? " clean" : ""), "aria-label": (isAvoid ? "Без срывов " : "Серия ") + x.streak },
            isAvoid ? icon("shield") : icon("flame"), String(x.streak), isAvoid ? h("small", null, " дн.") : null),
        ),
      ),
    );
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
          h("div", { class: "name" }, (x.kind === "avoid" ? "🚫 " : "") + x.title),
          h("div", { class: "mini" }, h("span", { style: "width:" + Math.round((x.rate30 || 0) * 100) + "%" })),
          h("div", { class: "sub" }, (x.kind === "avoid" ? "без срыва " : "серия ") + x.streak + " · рекорд " + x.best),
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
    const isAvoid = habit.kind === "avoid";
    const marking = !habit.meMarked;
    if (isAvoid && marking) {
      const ok = await confirmDialog("Отметить срыв? Серия «без срывов» начнётся заново.");
      if (!ok) return;
    }
    state.busy = true;
    habit.meMarked = marking;
    button.classList.toggle("on", marking);
    button.classList.add("pop");
    haptic(isAvoid ? (marking ? "heavy" : "light") : (marking ? "success" : "light"));
    try {
      await api("/api/checkin", { method: "POST", body: { habitId: habit.id } });
      await loadState();
      render();
      const fresh = state.data.habits.find((x) => x.id === habit.id);
      if (isAvoid) {
        toast(marking ? "Срыв отмечен. Завтра — новый день 💪" : "Срыв отменён");
      } else if (marking && fresh) {
        if (fresh.partnerDone === false) toast("Отмечено! Ждём " + state.data.pair.partner + " 👀");
        else if (fresh.streak > 1) toast("Серия " + fresh.streak + " 🔥");
        else toast("Отмечено ✅");
      }
    } catch (err) {
      habit.meMarked = !marking;
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

  function openAddSheet(kind) {
    haptic("light");
    const form = { kind: kind === "avoid" ? "avoid" : "do", title: "", emoji: kind === "avoid" ? "🚫" : "⭐", schedule: 127 };
    openSheet(() => {
      const submit = h("button", { class: "btn", disabled: true, onclick: () => save() }, "Добавить");
      const input = h("input", {
        class: "input", maxlength: "40", enterkeyhint: "done",
        oninput: (e) => { form.title = e.target.value; paintDays(); },
      });
      const emojiButtons = EMOJIS.map((em) => h("button", {
        class: "emoji-opt",
        onclick: () => { form.emoji = em; paintEmoji(); haptic("select"); },
      }, em));
      const dayButtons = WEEKDAYS.map((label, i) => h("button", {
        class: "day on",
        onclick: () => { form.schedule ^= 1 << i; paintDays(); haptic("select"); },
      }, label));
      const preset = (label, mask) => h("button", { class: "chip", onclick: () => { form.schedule = mask; paintDays(); } }, label);
      const templatesBox = h("div", null);
      const kindButtons = [["do", "Делать"], ["avoid", "Не делать"]].map(([value, label]) => h("button", {
        class: "tab",
        onclick: () => {
          if (form.kind === value) return;
          form.kind = value;
          form.title = "";
          input.value = "";
          form.emoji = value === "avoid" ? "🚫" : "⭐";
          paintKind();
          haptic("select");
        },
      }, label));
      const kindHint = h("div", { class: "section-hint" });

      function paintEmoji() {
        emojiButtons[0].textContent = EMOJIS.includes(form.emoji) ? EMOJIS[0] : form.emoji;
        emojiButtons.forEach((b) => b.classList.toggle("on", b.textContent === form.emoji));
      }
      function paintDays() {
        dayButtons.forEach((b, i) => b.classList.toggle("on", Boolean(form.schedule & (1 << i))));
        submit.disabled = !form.title.trim() || !form.schedule;
      }
      function paintKind() {
        const isAvoid = form.kind === "avoid";
        kindButtons.forEach((b, i) => b.classList.toggle("active", (i === 1) === isAvoid));
        input.placeholder = isAvoid ? "Например, «Курить»" : "Например, «Прогулка 30 минут»";
        kindHint.textContent = isAvoid
          ? "Каждый день без срыва засчитывается сам. Отмечать нужно только срыв."
          : "Отмечай выполнение каждый день по расписанию.";
        const source = isAvoid ? state.data.avoidTemplates : state.data.templates;
        const templates = (source || []).filter((t) => !state.data.habits.some((x) => x.title === t.title));
        templatesBox.replaceChildren(...(templates.length ? [
          h("div", { class: "field-label" }, "Шаблоны"),
          h("div", { class: "chips" }, templates.map((t) => h("button", {
            class: "chip",
            onclick: () => {
              form.title = t.title;
              input.value = t.title;
              form.emoji = t.emoji;
              paintEmoji();
              paintDays();
              haptic("select");
            },
          }, t.emoji + " " + t.title))),
        ] : []));
        paintEmoji();
        paintDays();
      }
      async function save() {
        submit.disabled = true;
        try {
          await api("/api/habits", { method: "POST", body: { scope: state.scope, kind: form.kind, title: form.title, emoji: form.emoji, schedule: form.schedule } });
          haptic("success");
          closeSheet();
          await refresh();
          toast(form.kind === "avoid" ? "Держись! Считаю дни без срывов 🛡" : "Привычка добавлена ✨");
        } catch (err) {
          submit.disabled = false;
          toast(err.message);
        }
      }

      paintKind();
      return h("div", null,
        h("h2", null, "Новая привычка"),
        h("div", { class: "tabs kind-tabs" }, kindButtons),
        kindHint,
        templatesBox,
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

  // ---------- reorder & pin ----------
  function sortableList(items, pinnedIds) {
    const list = h("div", { class: "sort-list" });
    const lastPinned = () => {
      let last = null;
      for (const row of list.children) if (pinnedIds.has(Number(row.dataset.id))) last = row;
      return last;
    };
    items.forEach((x) => {
      const pinBtn = h("button", {
        class: "sort-pin" + (pinnedIds.has(x.id) ? " on" : ""),
        "aria-label": "Закрепить",
        onclick: () => {
          const id = x.id;
          const anchor = lastPinned();
          if (pinnedIds.has(id)) {
            pinnedIds.delete(id);
            const after = lastPinned();
            list.insertBefore(row, after ? after.nextSibling : list.firstChild);
          } else {
            pinnedIds.add(id);
            list.insertBefore(row, anchor ? anchor.nextSibling : list.firstChild);
          }
          pinBtn.classList.toggle("on", pinnedIds.has(id));
          haptic("select");
        },
      }, icon("pin"));
      const handle = h("div", { class: "sort-handle", "aria-label": "Перетащить" }, icon("grip"));
      const row = h("div", { class: "sort-row", "data-id": String(x.id) },
        h("div", { class: "sort-emoji" }, x.emoji),
        h("div", { class: "sort-title" }, x.title),
        pinBtn,
        handle,
      );
      let startY = 0;
      handle.addEventListener("pointerdown", (e) => {
        e.preventDefault();
        startY = e.clientY;
        handle.setPointerCapture(e.pointerId);
        row.classList.add("dragging");
        haptic("light");
      });
      handle.addEventListener("pointermove", (e) => {
        if (!row.classList.contains("dragging")) return;
        let dy = e.clientY - startY;
        const prev = row.previousElementSibling;
        const next = row.nextElementSibling;
        const oldTop = row.offsetTop;
        if (dy < 0 && prev && e.clientY < prev.getBoundingClientRect().top + prev.offsetHeight / 2) {
          list.insertBefore(row, prev);
        } else if (dy > 0 && next && e.clientY > next.getBoundingClientRect().top + next.offsetHeight / 2) {
          list.insertBefore(row, next.nextSibling);
        }
        const moved = row.offsetTop - oldTop;
        if (moved) {
          startY += moved;
          dy = e.clientY - startY;
          haptic("select");
        }
        row.style.transform = "translateY(" + dy + "px)";
      });
      const end = () => {
        row.classList.remove("dragging");
        row.style.transform = "";
      };
      handle.addEventListener("pointerup", end);
      handle.addEventListener("pointercancel", end);
      list.append(row);
    });
    return { el: list, ids: () => Array.from(list.children, (row) => Number(row.dataset.id)) };
  }

  function openOrderSheet() {
    haptic("light");
    const habits = state.data.habits;
    const pinnedIds = new Set(habits.filter((x) => x.pinned).map((x) => x.id));
    const doList = sortableList(habits.filter((x) => x.kind !== "avoid"), pinnedIds);
    const avoidList = sortableList(habits.filter((x) => x.kind === "avoid"), pinnedIds);
    const shared = state.scope !== "p";
    openSheet(() => {
      const save = h("button", {
        class: "btn",
        onclick: async () => {
          save.disabled = true;
          const ids = doList.ids().concat(avoidList.ids());
          try {
            await api("/api/order", { method: "POST", body: { scope: state.scope, ids, pinned: Array.from(pinnedIds) } });
            haptic("success");
            closeSheet();
            await refresh();
            toast("Порядок сохранён");
          } catch (err) {
            save.disabled = false;
            toast(err.message);
          }
        },
      }, "Сохранить");
      return h("div", null,
        h("h2", null, "Порядок"),
        h("div", { class: "section-hint" },
          "Тяни за ⠿, чтобы переставить. 📌 — закрепить наверху." + (shared ? " Порядок общий для вас обоих." : "")),
        doList.el.children.length ? [h("div", { class: "field-label" }, "Делать"), doList.el] : null,
        avoidList.el.children.length ? [h("div", { class: "field-label" }, "Не делать"), avoidList.el] : null,
        save,
      );
    });
  }

  function openHabitSheet(x) {
    haptic("light");
    openSheet(() => h("div", null,
      h("div", { class: "detail-head" },
        h("div", { class: "habit-icon" }, x.emoji),
        h("div", null, h("h2", null, x.title), h("div", { class: "habit-meta" }, x.kind === "avoid" ? "не делать · " + x.scheduleLabel : x.scheduleLabel)),
      ),
      h("div", { class: "tiles two" },
        tile(String(x.streak), x.kind === "avoid" ? "дней без срыва" : "текущая серия"),
        tile(String(x.best), x.kind === "avoid" ? "рекорд без срыва" : "рекорд")),
      h("div", { class: "week", style: "margin:18px 0 6px" }, x.week.map((w) => h("span", { class: "seg " + w.status }))),
      h("div", { class: "labels" }, WEEKDAYS.map((w) => h("span", null, w))),
      h("button", {
        class: "btn ghost", style: "margin-top:26px",
        onclick: async () => {
          try {
            await api("/api/habits/" + x.id + "/pin", { method: "POST", body: { pinned: !x.pinned } });
            haptic("success");
            closeSheet();
            await refresh();
            toast(x.pinned ? "Откреплено" : "Закреплено наверху 📌");
          } catch (err) {
            toast(err.message);
          }
        },
      }, icon("pin"), x.pinned ? "Открепить" : "Закрепить наверху"),
      h("button", {
        class: "btn danger",
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
