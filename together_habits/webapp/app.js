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
    { id: "plan", tab: "Планы", title: "Планы" },
  ];
  const MONTHS = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"];
  const MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
  const PLAN_EMOJIS = ["📅", "🍽", "☕", "🎬", "🚶", "🏃", "🏋️", "🏀", "⚽", "🎮", "🍺", "🎉", "✈️", "🏕", "🛍", "🎤", "📚", "🧘", "🏖", "❤️"];
  const PLAN_TEMPLATES = {
    f: [["🏋️", "Зал вместе"], ["🎮", "Поиграть"], ["🍺", "Посидеть в баре"], ["🎬", "Кино"], ["⚽", "Футбол"], ["🍔", "Поесть бургеров"]],
    c: [["🍽", "Ужин"], ["🎬", "Кино"], ["🚶", "Прогулка"], ["☕", "Кофе"], ["✈️", "Поездка"], ["🏠", "Вечер дома"]],
  };
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
    gear: '<path d="M12 15.2a3.2 3.2 0 1 0 0-6.4 3.2 3.2 0 0 0 0 6.4z" stroke="currentColor" stroke-width="2.1" fill="none"/><path d="M19.4 13.5a7.6 7.6 0 0 0 0-3l2-1.6-2-3.4-2.4.9a7.5 7.5 0 0 0-2.6-1.5L14 2.4h-4l-.4 2.5A7.5 7.5 0 0 0 7 6.4l-2.4-.9-2 3.4 2 1.6a7.6 7.6 0 0 0 0 3l-2 1.6 2 3.4 2.4-.9a7.5 7.5 0 0 0 2.6 1.5l.4 2.5h4l.4-2.5a7.5 7.5 0 0 0 2.6-1.5l2.4.9 2-3.4z" stroke="currentColor" stroke-width="1.9" stroke-linejoin="round" fill="none"/>',
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
      if (["p", "f", "c", "plan"].includes(saved)) return saved;
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
    if (state.scope === "plan") {
      if (!state.month) state.month = new Date().toISOString().slice(0, 7);
      const [plans, challenges] = await Promise.all([
        api("/api/plans?month=" + state.month),
        api("/api/challenges"),
      ]);
      state.plans = plans;
      state.challenges = challenges;
      if (!state.selectedDay) state.selectedDay = state.plans.today;
      state.sections = state.plans.sections;
      return;
    }
    if (state.scope === "p") {
      const [data, tasks, goals, focus] = await Promise.all([
        api("/api/state?scope=p"), api("/api/tasks"), api("/api/goals"), api("/api/focus"),
      ]);
      state.data = data;
      state.tasks = tasks;
      state.goals = goals.goals;
      setFocus(focus);
    } else {
      state.data = await api("/api/state?scope=" + state.scope);
    }
    state.sections = state.data.sections;
    if (state.sections[state.scope] && state.sections[state.scope].hidden) {
      // The section was hidden (maybe on another device) — fall back to personal.
      state.scope = "p";
      saveScope("p");
      state.data = await api("/api/state?scope=p");
      state.sections = state.data.sections;
    }
  }

  // Section names and visibility are per user ("С другом" → "Миша").
  function sectionInfo(id) {
    return (state.sections && state.sections[id]) || {};
  }
  function tabLabel(scope) {
    return sectionInfo(scope.id).name || scope.tab;
  }
  function titleOf(id) {
    const scope = SCOPES.find((s) => s.id === id);
    return sectionInfo(id).name || scope.title;
  }
  function visibleScopes() {
    return SCOPES.filter((s) => !sectionInfo(s.id).hidden);
  }
  function settingsButton() {
    return h("button", { class: "icon-btn", "aria-label": "Настройки разделов", onclick: openSettingsSheet }, icon("gear"));
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
    if (state.scope === "plan") return renderPlans();
    const d = state.data;
    const scopeInfo = SCOPES.find((s) => s.id === state.scope);
    const pairActive = !d.pair || d.pair.status === "active";

    const header = h("div", { class: "topbar" },
      formatDate(d.today),
      h("div", { class: "actions" },
        pairActive && d.habits.length > 1 ? h("button", { class: "icon-btn", "aria-label": "Порядок привычек", onclick: openOrderSheet }, icon("sort")) : null,
        pairActive && d.habits.length ? h("button", { class: "icon-btn", "aria-label": "Статистика", onclick: openStats }, icon("chart")) : null,
        pairActive ? h("button", { class: "icon-btn", "aria-label": "Добавить привычку", onclick: () => openAddSheet("do") }, icon("plus")) : null,
        settingsButton(),
      ),
    );

    const tabs = renderTabs();

    const page = [header, h("h1", null, titleOf(scopeInfo.id))];
    if (d.pair && d.pair.status === "active") {
      page.push(h("p", { class: "subtitle" }, "Вы и ", h("b", null, d.pair.partner), state.scope === "c" ? " 💞" : " 🤝"));
    }
    page.push(tabs);

    if (!pairActive) {
      page.push(renderInvite(d));
    } else {
      page.push(renderProgress(d));
      if (state.scope === "c") page.push(renderMood(d));
      if (state.scope === "p") page.push(renderTasks(), renderFocusCard());
      page.push(...renderHabitSections(d));
      if (state.scope === "p") page.push(...renderGoals());
    }
    app.replaceChildren(h("div", { class: "fade-in" }, page));
  }

  function renderTabs() {
    const count = visibleScopes().length;
    return h("div", { class: "tabs" + (count === 4 ? " four" : count === 2 ? " two" : ""), role: "tablist" },
      visibleScopes().map((s) => h("button", {
        class: "tab" + (s.id === state.scope ? " active" : ""),
        role: "tab",
        "aria-selected": s.id === state.scope ? "true" : "false",
        onclick: () => switchScope(s.id),
      }, tabLabel(s))),
    );
  }

  // ---------- render: plans & calendar ----------
  const isoOf = (d) => d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  const dateOf = (iso) => new Date(iso + "T12:00:00");
  function dayTitle(iso) {
    const d = dateOf(iso);
    return WEEKDAYS[(d.getDay() + 6) % 7] + ", " + d.getDate() + " " + MONTHS_GEN[d.getMonth()];
  }

  function renderPlans() {
    if (state.plansView === "challenges") return renderChallenges();
    const P = state.plans;
    const activeScopes = ["f", "c"].filter((s) => P.pairs[s].status === "active" && !sectionInfo(s).hidden);
    const header = h("div", { class: "topbar" },
      formatDate(P.today),
      h("div", { class: "actions" },
        activeScopes.length ? h("button", { class: "icon-btn", "aria-label": "Предложить план", onclick: () => openPlanSheet() }, icon("plus")) : null,
        settingsButton(),
      ),
    );
    const page = [header, h("h1", null, "Планы"),
      h("p", { class: "subtitle legend-row" },
        !sectionInfo("f").hidden ? [h("span", { class: "lg f" }), sectionInfo("f").name || "с другом"] : null,
        !sectionInfo("c").hidden ? [h("span", { class: "lg c" }), sectionInfo("c").name || "с парой"] : null,
        h("span", { class: "lg pending" }), "ждёт ответа"),
      renderTabs(),
      plansSubTabs()];

    if (!activeScopes.length) {
      page.push(h("div", { class: "panel hero" },
        h("div", { class: "big" }, "📅"),
        h("h2", null, "Планируйте вместе"),
        h("p", null, "Пригласи друга во вкладке «С другом» или пару во вкладке «Пара» — и сможете предлагать встречи, а всё расписание будет на одном календаре."),
        h("button", { class: "btn", onclick: () => switchScope("f") }, "Пригласить"),
      ));
      app.replaceChildren(h("div", { class: "fade-in" }, page));
      return;
    }

    if (P.incoming.length) {
      page.push(h("div", { class: "section-title" }, "Входящие предложения · " + P.incoming.length),
        h("div", { class: "plan-list" }, P.incoming.map((x) => planCard(x, true, true))));
    }

    page.push(renderCalendar(P));

    const dayPlans = P.plans.filter((x) => x.date === state.selectedDay);
    const canPropose = state.selectedDay >= P.today;
    page.push(
      h("div", { class: "section-title row-title" },
        h("span", null, dayTitle(state.selectedDay)),
        canPropose ? h("button", { class: "mini-add", onclick: () => openPlanSheet(state.selectedDay) }, "+ предложить") : null),
      dayPlans.length
        ? h("div", { class: "plan-list" }, dayPlans.map((x) => planCard(x)))
        : h("div", { class: "empty-day" }, canPropose ? "Свободный день. Предложи что-нибудь 🙂" : "В этот день планов не было"),
    );

    const later = P.upcoming.filter((x) => x.date !== state.selectedDay);
    if (later.length) {
      page.push(h("div", { class: "section-title" }, "Ближайшие"), h("div", { class: "plan-list" }, later.slice(0, 8).map((x) => planCard(x, false, true))));
    }
    app.replaceChildren(h("div", { class: "fade-in" }, page));
  }

  function plansSubTabs() {
    const view = state.plansView || "calendar";
    const active = (state.challenges && state.challenges.active.length) || 0;
    return h("div", { class: "tabs kind-tabs sub" },
      h("button", { class: "tab" + (view === "calendar" ? " active" : ""), onclick: () => { state.plansView = "calendar"; haptic("select"); render(); } }, "📅 Календарь"),
      h("button", { class: "tab" + (view === "challenges" ? " active" : ""), onclick: () => { state.plansView = "challenges"; haptic("select"); render(); } }, "🏆 Челленджи" + (active ? " · " + active : "")),
    );
  }

  // ---------- challenges ----------
  function renderChallenges() {
    const C = state.challenges;
    const header = h("div", { class: "topbar" },
      formatDate(C.today),
      h("div", { class: "actions" },
        h("button", { class: "icon-btn", "aria-label": "Новый челлендж", onclick: openChallengeSheet }, icon("plus")),
        settingsButton(),
      ),
    );
    const page = [header, h("h1", null, "Челленджи"),
      h("p", { class: "subtitle" }, "Соревнуйтесь вдвоём или компанией до 10 человек"),
      renderTabs(), plansSubTabs()];
    if (!C.active.length) {
      page.push(h("div", { class: "panel hero" },
        h("div", { class: "big" }, "🏆"),
        h("h2", null, "Брось вызов"),
        h("p", null, "«Кто чаще сходит в зал за 2 недели» или «кто дольше без сладкого». Можно поставить на кон кофе — бот сам подведёт итог."),
        h("button", { class: "btn", onclick: openChallengeSheet }, icon("plus"), "Создать челлендж"),
      ));
    } else {
      page.push(h("div", { class: "plan-list" }, C.active.map(challengeCard)));
    }
    if (C.finished.length) {
      page.push(h("div", { class: "section-title" }, "Завершённые"),
        h("div", { class: "plan-list" }, C.finished.map((c) => h("div", { class: "plan finished" },
          h("div", { class: "plan-icon" }, c.emoji),
          h("div", { class: "plan-body" },
            h("div", { class: "plan-title" }, c.title),
            h("div", { class: "plan-meta" }, c.winners.length ? "🏆 " + c.winners.join(", ") : "без победителя"),
          ),
        ))));
    }
    app.replaceChildren(h("div", { class: "fade-in" }, page));
  }

  function challengeCard(c) {
    const isAvoid = c.kind === "avoid";
    const unit = isAvoid ? "без срыва" : "дн.";
    const medals = ["🥇", "🥈", "🥉"];
    const progress = Math.round((c.dayNumber / c.days) * 100);
    return h("div", { class: "panel challenge" + (isAvoid ? " avoid" : "") },
      h("div", { class: "ch-head" },
        h("div", { class: "plan-icon" }, c.emoji),
        h("div", { class: "plan-body" },
          h("div", { class: "plan-title" }, c.title),
          h("div", { class: "plan-meta" }, "день " + c.dayNumber + " из " + c.days + " · до " + dayTitle(c.end)),
        ),
      ),
      h("div", { class: "goal-bar ch-progress" }, h("i", { style: "width:" + progress + "%" })),
      c.stake ? h("div", { class: "ch-stake" }, "💸 " + c.stake) : null,
      h("div", { class: "leaderboard" }, c.members.map((m, i) => h("div", { class: "lb-row" + (m.me ? " me" : "") },
        h("span", { class: "lb-rank" }, medals[i] || String(i + 1)),
        h("span", { class: "lb-name" }, m.me ? "Ты" : m.name),
        isAvoid ? (m.markedToday ? h("span", { class: "lb-today bad" }, "срыв") : null)
          : (m.markedToday ? h("span", { class: "lb-today" }, "✓ сегодня") : null),
        h("span", { class: "lb-score" }, m.score + " " + unit),
      ))),
      h("div", { class: "plan-actions" },
        isAvoid
          ? h("button", { class: "pbtn " + (c.markedToday ? "no" : "danger"), onclick: () => markChallenge(c) }, c.markedToday ? "Отменить срыв" : "Срыв")
          : h("button", { class: "pbtn " + (c.markedToday ? "no" : "yes"), onclick: () => markChallenge(c) }, c.markedToday ? "Снять отметку" : "✓ Сделал сегодня"),
        h("button", { class: "pbtn no", onclick: () => shareChallenge(c) }, "Позвать"),
      ),
      h("button", { class: "link-danger ch-leave", onclick: () => leaveChallenge(c) }, c.isCreator ? "Удалить челлендж" : "Выйти из челленджа"),
    );
  }

  async function markChallenge(c) {
    if (c.kind === "avoid" && !c.markedToday && !(await confirmDialog("Отметить срыв? Остальные участники узнают."))) return;
    try {
      const res = await api("/api/challenges/" + c.id + "/mark", { method: "POST", body: {} });
      haptic(res.marked && c.kind !== "avoid" ? "success" : "light");
      await refresh();
      toast(c.kind === "avoid" ? (res.marked ? "Срыв отмечен" : "Срыв отменён") : (res.marked ? "Засчитано 💪" : "Отметка снята"));
    } catch (err) {
      toast(err.message);
    }
  }

  function shareChallenge(c) {
    if (!c.link) return toast("Ссылка пока недоступна");
    const text = c.emoji + " Челлендж «" + c.title + "» на " + c.days + " дней" + (c.stake ? ". Ставка: " + c.stake : "") + ". Залетай!";
    const url = "https://t.me/share/url?url=" + encodeURIComponent(c.link) + "&text=" + encodeURIComponent(text);
    if (tg && tg.openTelegramLink) tg.openTelegramLink(url);
    else window.open(url, "_blank");
  }

  async function leaveChallenge(c) {
    const text = c.isCreator ? "Удалить челлендж «" + c.title + "» у всех участников?" : "Выйти из челленджа «" + c.title + "»? Твои отметки удалятся.";
    if (!(await confirmDialog(text))) return;
    try {
      await api("/api/challenges/" + c.id, { method: "DELETE" });
      await refresh();
      toast(c.isCreator ? "Челлендж удалён" : "Ты больше не в челлендже");
    } catch (err) {
      toast(err.message);
    }
  }

  function openChallengeSheet() {
    haptic("light");
    const P = state.plans;
    const partners = ["f", "c"].filter((s) => P.pairs[s].status === "active");
    const form = { kind: "do", title: "", emoji: "🏆", days: 14, stake: "", invite: new Set(partners) };
    const IDEAS = { do: [["🏋️", "Зал"], ["🏃", "Пробежка"], ["📖", "Чтение 20 минут"], ["💪", "100 отжиманий"], ["🌅", "Подъём до 7:00"]], avoid: [["🍬", "Без сладкого"], ["🚭", "Без сигарет"], ["📱", "Без соцсетей"], ["🍺", "Без алкоголя"], ["🍔", "Без фастфуда"]] };
    openSheet(() => {
      const submit = h("button", { class: "btn", disabled: true, onclick: () => create() }, "Бросить вызов");
      const title = h("input", { class: "input", maxlength: "40", placeholder: "Например, «Зал»", oninput: (e) => { form.title = e.target.value; paint(); } });
      const stake = h("input", { class: "input", maxlength: "80", placeholder: "Например, «проигравший покупает кофе»", oninput: (e) => { form.stake = e.target.value; } });
      const kindButtons = [["do", "Кто чаще"], ["avoid", "Кто дольше без"]].map(([k, label]) => h("button", { class: "tab", onclick: () => { form.kind = k; paint(); haptic("select"); } }, label));
      const ideasBox = h("div", { class: "chips" });
      const dayButtons = [7, 14, 21, 30].map((n) => h("button", { class: "day", onclick: () => { form.days = n; paint(); haptic("select"); } }, n + " дн."));
      const inviteRows = partners.map((s) => {
        const sw = h("button", { class: "switch on", role: "switch", onclick: () => { form.invite.has(s) ? form.invite.delete(s) : form.invite.add(s); sw.classList.toggle("on", form.invite.has(s)); haptic("select"); } }, h("span"));
        return h("div", { class: "set-row" }, h("span", null, (s === "f" ? "🤝 " : "💞 ") + P.pairs[s].partner), sw);
      });
      function paint() {
        kindButtons.forEach((b, i) => b.classList.toggle("active", (i === 1) === (form.kind === "avoid")));
        dayButtons.forEach((b, i) => b.classList.toggle("on", [7, 14, 21, 30][i] === form.days));
        ideasBox.replaceChildren(...IDEAS[form.kind].map(([em, t]) => h("button", { class: "chip", onclick: () => { form.title = t; title.value = t; form.emoji = em; paint(); } }, em + " " + t)));
        submit.disabled = !form.title.trim();
      }
      async function create() {
        submit.disabled = true;
        try {
          const ch = await api("/api/challenges", { method: "POST", body: { title: form.title, emoji: form.emoji, kind: form.kind, days: form.days, stake: form.stake, invite: Array.from(form.invite) } });
          haptic("success");
          closeSheet();
          state.plansView = "challenges";
          await refresh();
          toast("Вызов брошен 🏆");
          if (!form.invite.size) shareChallenge(ch);
        } catch (err) {
          submit.disabled = false;
          toast(err.message);
        }
      }
      paint();
      return h("div", null,
        h("h2", null, "Новый челлендж"),
        h("div", { class: "tabs kind-tabs" }, kindButtons),
        h("div", { class: "field-label" }, "Идеи"), ideasBox,
        h("div", { class: "field-label" }, "Название"), title,
        h("div", { class: "field-label" }, "Длительность"), h("div", { class: "days four-col" }, dayButtons),
        h("div", { class: "field-label" }, "Ставка (необязательно)"), stake,
        partners.length ? [h("div", { class: "field-label" }, "Сразу позвать"), inviteRows] : null,
        h("div", { class: "section-hint", style: "margin-top:12px" }, "После создания можно позвать кого угодно по ссылке — до 10 участников."),
        submit,
      );
    });
  }

  function renderCalendar(P) {
    const [year, month] = state.month.split("-").map(Number);
    const first = new Date(year, month - 1, 1, 12);
    const start = new Date(first);
    start.setDate(1 - ((first.getDay() + 6) % 7));
    const byDay = {};
    P.plans.forEach((x) => { if (x.status !== "declined") (byDay[x.date] = byDay[x.date] || []).push(x); });

    const cells = [];
    for (let i = 0; i < 42; i++) {
      const d = new Date(start);
      d.setDate(start.getDate() + i);
      const iso = isoOf(d);
      const items = byDay[iso] || [];
      const cls = ["cal-day"];
      if (d.getMonth() !== month - 1) cls.push("other");
      if (iso === P.today) cls.push("today");
      if (iso === state.selectedDay) cls.push("sel");
      if (iso < P.today) cls.push("past");
      cells.push(h("button", {
        class: cls.join(" "),
        "aria-label": dayTitle(iso) + (items.length ? ", планов: " + items.length : ""),
        onclick: () => { state.selectedDay = iso; haptic("select"); render(); },
      },
        h("span", { class: "num" }, String(d.getDate())),
        h("span", { class: "dots" }, items.slice(0, 3).map((x) => h("i", { class: x.scope + (x.status === "pending" ? " pending" : "") }))),
      ));
    }
    return h("div", { class: "panel calendar" },
      h("div", { class: "cal-head" },
        h("button", { class: "cal-nav", "aria-label": "Предыдущий месяц", onclick: () => shiftMonth(-1) }, icon("back")),
        h("div", { class: "cal-title" }, MONTHS[month - 1] + " " + year),
        h("button", { class: "cal-nav next", "aria-label": "Следующий месяц", onclick: () => shiftMonth(1) }, icon("back")),
      ),
      h("div", { class: "cal-grid head" }, WEEKDAYS.map((w) => h("span", null, w))),
      h("div", { class: "cal-grid" }, cells),
    );
  }

  async function shiftMonth(delta) {
    const [year, month] = state.month.split("-").map(Number);
    const d = new Date(year, month - 1 + delta, 1, 12);
    state.month = isoOf(d).slice(0, 7);
    const todayIso = state.plans.today;
    state.selectedDay = todayIso.slice(0, 7) === state.month ? todayIso : state.month + "-01";
    haptic("select");
    await refresh();
  }

  function statusBadge(x) {
    if (x.status === "accepted") return h("span", { class: "badge ok" }, "Принято");
    if (x.status === "declined") return h("span", { class: "badge no" }, "Отклонено");
    return h("span", { class: "badge wait" }, x.mine ? "Ждёт ответа" : "Нужен ответ");
  }

  function planCard(x, withActions, withDate) {
    const meta = [withDate ? dayTitle(x.date) : null, x.time, x.repeat ? "🔁" : null, (x.scope === "c" ? "💞 " : "🤝 ") + x.partner].filter(Boolean).join(" · ");
    return h("div", { class: "plan " + x.scope + " " + x.status, role: "button", tabindex: "0", onclick: () => openPlanDetail(x) },
      h("div", { class: "plan-icon" }, x.emoji),
      h("div", { class: "plan-body" },
        h("div", { class: "plan-title" }, x.title),
        h("div", { class: "plan-meta" }, meta),
        withActions ? h("div", { class: "plan-actions" },
          h("button", { class: "pbtn yes", onclick: (e) => { e.stopPropagation(); answerPlan(x, true); } }, "Принять"),
          h("button", { class: "pbtn no", onclick: (e) => { e.stopPropagation(); answerPlan(x, false); } }, "Отклонить"),
        ) : null,
      ),
      withActions ? null : statusBadge(x),
    );
  }

  async function answerPlan(x, accept) {
    try {
      await api("/api/plans/" + x.id + "/respond", { method: "POST", body: { accept } });
      haptic(accept ? "success" : "light");
      closeSheet();
      await refresh();
      toast(accept ? "Принято ✅ Уведомление отправлено" : "Отклонено. Уведомление отправлено");
    } catch (err) {
      toast(err.message);
      await refresh();
    }
  }

  function openPlanDetail(x) {
    haptic("light");
    openSheet(() => h("div", null,
      h("div", { class: "detail-head" },
        h("div", { class: "habit-icon plan-icon " + x.scope }, x.emoji),
        h("div", null, h("h2", null, x.title), h("div", { class: "habit-meta" }, dayTitle(x.date) + (x.time ? " · " + x.time : ""))),
      ),
      h("div", { class: "panel plan-info" },
        h("div", null, (x.scope === "c" ? "💞 С парой: " : "🤝 С другом: "), h("b", null, x.partner)),
        h("div", null, "Предлагает: ", h("b", null, x.mine ? "ты" : x.partner)),
        h("div", null, "Статус: ", statusBadge(x)),
        x.note ? h("div", { class: "plan-note" }, "💬 " + x.note) : null,
      ),
      !x.mine && x.status === "pending" ? [
        h("button", { class: "btn", style: "margin-top:20px", onclick: () => answerPlan(x, true) }, "Принять"),
        h("button", { class: "btn danger", onclick: () => answerPlan(x, false) }, "Отклонить"),
      ] : null,
      x.mine ? h("button", {
        class: "btn danger", style: "margin-top:20px",
        onclick: async () => {
          if (!(await confirmDialog("Отменить план «" + x.title + "»? " + x.partner + " получит уведомление."))) return;
          try {
            await api("/api/plans/" + x.id, { method: "DELETE" });
            closeSheet();
            await refresh();
            toast("План отменён");
          } catch (err) {
            toast(err.message);
          }
        },
      }, "Отменить план") : null,
      h("button", { class: "btn ghost", onclick: closeSheet }, "Закрыть"),
    ));
  }

  function openPlanSheet(day) {
    haptic("light");
    const P = state.plans;
    const scopes = ["f", "c"].filter((s) => P.pairs[s].status === "active" && !sectionInfo(s).hidden);
    const form = { scope: scopes[0], title: "", emoji: "📅", date: day && day >= P.today ? day : P.today, time: "", note: "" };
    openSheet(() => {
      const submit = h("button", { class: "btn", disabled: true, onclick: () => send() }, "Отправить предложение");
      const title = h("input", { class: "input", maxlength: "60", placeholder: "Что предлагаешь?", oninput: (e) => { form.title = e.target.value; paint(); } });
      const dateInput = h("input", { class: "input", type: "date", min: P.today, value: form.date, onchange: (e) => { form.date = e.target.value; paint(); } });
      const timeInput = h("input", { class: "input", type: "time", onchange: (e) => { form.time = e.target.value; } });
      const note = h("input", { class: "input", maxlength: "200", placeholder: "Комментарий (необязательно)", oninput: (e) => { form.note = e.target.value; } });
      const repeatSwitch = h("button", { class: "switch", role: "switch", onclick: () => { form.repeat = !form.repeat; repeatSwitch.classList.toggle("on", form.repeat); haptic("select"); } }, h("span"));
      const emojiButtons = PLAN_EMOJIS.map((em) => h("button", { class: "emoji-opt", onclick: () => { form.emoji = em; paint(); haptic("select"); } }, em));
      const whoButtons = scopes.map((s) => h("button", {
        class: "tab",
        onclick: () => { form.scope = s; paint(); haptic("select"); },
      }, (s === "f" ? "🤝 " : "💞 ") + P.pairs[s].partner));
      const templatesBox = h("div", { class: "chips" });

      function paint() {
        whoButtons.forEach((b, i) => b.classList.toggle("active", scopes[i] === form.scope));
        emojiButtons.forEach((b) => b.classList.toggle("on", b.textContent === form.emoji));
        templatesBox.replaceChildren(...PLAN_TEMPLATES[form.scope].map(([em, t]) => h("button", {
          class: "chip",
          onclick: () => { form.title = t; title.value = t; form.emoji = em; paint(); haptic("select"); },
        }, em + " " + t)));
        submit.disabled = !form.title.trim() || !form.date;
      }
      async function send() {
        submit.disabled = true;
        try {
          await api("/api/plans", { method: "POST", body: { scope: form.scope, title: form.title, emoji: form.emoji, date: form.date, time: form.time || null, note: form.note, repeat: form.repeat ? "weekly" : null } });
          haptic("success");
          closeSheet();
          state.selectedDay = form.date;
          state.month = form.date.slice(0, 7);
          await refresh();
          toast("Предложение отправлено 📨");
        } catch (err) {
          submit.disabled = false;
          toast(err.message);
        }
      }
      paint();
      return h("div", null,
        h("h2", null, "Предложить план"),
        scopes.length > 1 ? h("div", { class: "tabs kind-tabs" }, whoButtons) : h("div", { class: "section-hint" }, "С " + P.pairs[form.scope].partner),
        h("div", { class: "field-label" }, "Идеи"),
        templatesBox,
        h("div", { class: "field-label" }, "Что"),
        title,
        h("div", { class: "field-label" }, "Иконка"),
        h("div", { class: "emojis" }, emojiButtons),
        h("div", { class: "two-col" },
          h("div", null, h("div", { class: "field-label" }, "Дата"), dateInput),
          h("div", null, h("div", { class: "field-label" }, "Время"), timeInput),
        ),
        h("div", { class: "set-row" }, h("span", null, "🔁 Повторять каждую неделю"), repeatSwitch),
        h("div", { class: "field-label" }, "Комментарий"),
        note,
        submit,
      );
    });
  }

  // ---------- year heatmap ----------
  function heatmap(cells) {
    const weeks = [];
    for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7));
    const done = cells.filter((c) => c.level > 0).length;
    const scroller = h("div", { class: "heat-scroll" },
      h("div", { class: "heat" }, weeks.map((week) => h("div", { class: "heat-col" },
        week.map((c) => h("span", { class: "hc l" + c.level, title: c.date }))))));
    requestAnimationFrame(() => { scroller.scrollLeft = scroller.scrollWidth; });
    return h("div", { class: "panel" },
      h("div", { class: "chart-hint" }, "Дней с выполнением: ", h("b", null, String(done)), " за год"),
      scroller,
      h("div", { class: "heat-legend" }, "меньше", [0, 1, 2, 3, 4].map((l) => h("span", { class: "hc l" + l })), "больше"),
    );
  }

  // ---------- top tasks of the day ----------
  function renderTasks() {
    const T = state.tasks || { tasks: [], carryable: 0 };
    const input = h("input", {
      class: "input task-input", maxlength: "80", placeholder: T.tasks.length >= 5 ? "Максимум 5 задач" : "Добавить главную задачу…",
      disabled: T.tasks.length >= 5, enterkeyhint: "done",
      onkeydown: (e) => { if (e.key === "Enter") addTask(e.target.value); },
    });
    const done = T.tasks.filter((t) => t.done).length;
    return h("div", { class: "panel tasks", style: "margin-top:14px" },
      h("div", { class: "row-title tasks-head" },
        h("span", null, "🎯 Главное сегодня"),
        T.tasks.length ? h("span", { class: "muted" }, done + "/" + T.tasks.length) : null),
      T.tasks.map((t) => h("div", { class: "task" + (t.done ? " done" : "") },
        h("button", { class: "task-check", "aria-label": t.done ? "Снять" : "Готово", onclick: () => taskAction(t.id, "toggle") }, t.done ? icon("check") : null),
        h("span", { class: "task-title" }, t.title),
        h("button", { class: "task-del", "aria-label": "Удалить", onclick: () => taskAction(t.id, "delete") }, "×"),
      )),
      T.tasks.length < 5 ? h("div", { class: "task-add" }, input, h("button", { class: "task-plus", "aria-label": "Добавить", onclick: () => addTask(input.value) }, icon("plus"))) : null,
      T.carryable && T.tasks.length < 5 ? h("button", { class: "mini-add", style: "margin-top:10px", onclick: () => addTask(null, true) }, "↪ Перенести невыполненные со вчера (" + T.carryable + ")") : null,
      !T.tasks.length ? h("div", { class: "section-hint", style: "margin:8px 0 0" }, "1–3 задачи, которые сделают день успешным. В полночь список обнуляется.") : null,
    );
  }

  async function addTask(title, carry) {
    if (!carry && !(title || "").trim()) return;
    try {
      state.tasks = await api("/api/tasks", { method: "POST", body: carry ? { carry: true } : { title } });
      haptic("light");
      render();
      const input = document.querySelector(".task-input");
      if (input && !carry) input.focus();
    } catch (err) {
      toast(err.message);
    }
  }

  async function taskAction(id, action) {
    try {
      state.tasks = await api("/api/tasks/" + id + (action === "toggle" ? "/toggle" : ""), { method: action === "toggle" ? "POST" : "DELETE", body: action === "toggle" ? {} : undefined });
      const t = state.tasks.tasks.find((x) => x.id === id);
      haptic(t && t.done ? "success" : "light");
      if (t && t.done && state.tasks.tasks.every((x) => x.done)) toast("Все главные задачи сделаны 🎉");
      render();
    } catch (err) {
      toast(err.message);
    }
  }

  // ---------- focus (pomodoro) ----------
  let focusTicker = null;
  function setFocus(focus) {
    state.focus = focus;
    state.focusOffset = new Date(focus.now).getTime() - Date.now();
  }
  function focusRemaining() {
    const a = state.focus && state.focus.active;
    if (!a) return null;
    return Math.max(0, Math.round((new Date(a.ends_at).getTime() - (Date.now() + state.focusOffset)) / 1000));
  }
  const mmss = (sec) => String(Math.floor(sec / 60)).padStart(2, "0") + ":" + String(sec % 60).padStart(2, "0");

  function renderFocusCard() {
    const F = state.focus || {};
    const left = focusRemaining();
    let label = "Помодоро-таймер";
    if (left !== null) {
      const end = new Date(new Date(F.active.ends_at).getTime() - state.focusOffset);
      label = left > 0 ? "Идёт фокус до " + String(end.getHours()).padStart(2, "0") + ":" + String(end.getMinutes()).padStart(2, "0") : "Фокус завершён — засчитай";
    }
    return h("button", { class: "panel focus-card" + (left !== null ? " running" : ""), onclick: openFocusSheet },
      h("span", { class: "focus-icon" }, "⏱"),
      h("span", { class: "focus-body" },
        h("span", { class: "focus-title" }, label),
        h("span", { class: "focus-meta" }, "Сегодня " + (F.todayMinutes || 0) + " мин · за неделю " + (Math.round((F.weekMinutes || 0) / 6) / 10).toString().replace(".", ",") + " ч")),
      h("span", { class: "focus-go" }, left === null ? "Старт" : "Открыть"),
    );
  }

  function openFocusSheet() {
    haptic("light");
    const form = { minutes: 25, habitId: null };
    const doHabits = (state.data.habits || []).filter((x) => x.kind !== "avoid");
    let breakEnds = null;
    openSheet(() => {
      const box = h("div", null);
      function ring(fraction, text, sub) {
        const r = 88, c = 2 * Math.PI * r;
        const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        svg.setAttribute("viewBox", "0 0 200 200");
        svg.setAttribute("class", "ring");
        svg.innerHTML = '<circle cx="100" cy="100" r="88" class="ring-bg"/><circle cx="100" cy="100" r="88" class="ring-fg" stroke-dasharray="' + c + '" stroke-dashoffset="' + (c * (1 - fraction)) + '"/>';
        return h("div", { class: "ring-wrap" }, svg, h("div", { class: "ring-text" }, h("b", null, text), h("span", null, sub)));
      }
      function paint() {
        const a = state.focus.active;
        const left = focusRemaining();
        if (breakEnds) {
          const b = Math.max(0, Math.round((breakEnds - Date.now()) / 1000));
          box.replaceChildren(ring(b / 300, mmss(b), "перерыв ☕"),
            h("button", { class: "btn ghost", onclick: () => { breakEnds = null; paint(); } }, b ? "Закончить перерыв" : "Готово"));
          if (!b) { breakEnds = null; haptic("success"); }
          return;
        }
        if (a) {
          const total = a.minutes * 60;
          box.replaceChildren(
            ring(left / total, mmss(left), a.habit ? "→ " + a.habit : "фокус"),
            left > 0
              ? h("button", { class: "btn danger", onclick: () => finishFocus(a.id, "cancel") }, "Прервать")
              : h("button", { class: "btn", onclick: () => finishFocus(a.id, "complete") }, "✅ Засчитать " + a.minutes + " мин"),
            h("div", { class: "section-hint", style: "margin-top:12px;text-align:center" }, "Можно закрыть приложение — бот напишет, когда время выйдет."),
          );
          if (left === 0 && !box.dataset.buzzed) { box.dataset.buzzed = "1"; haptic("success"); }
          return;
        }
        const chips = [15, 25, 45, 60].map((m) => h("button", { class: "day" + (form.minutes === m ? " on" : ""), onclick: () => { form.minutes = m; paint(); haptic("select"); } }, m + " мин"));
        const select = h("select", { class: "input select", onchange: (e) => { form.habitId = e.target.value ? Number(e.target.value) : null; } },
          h("option", { value: "" }, "Без привычки"),
          doHabits.map((x) => h("option", { value: String(x.id) }, x.emoji + " " + x.title)));
        select.value = form.habitId ? String(form.habitId) : "";
        box.replaceChildren(
          ring(1, form.minutes + ":00", "готов к фокусу"),
          h("div", { class: "days four-col" }, chips),
          h("div", { class: "field-label" }, "Засчитать в привычку"),
          select,
          h("button", { class: "btn", onclick: startFocus }, "▶ Начать"),
          h("div", { class: "section-hint", style: "margin-top:12px" }, "Сегодня: " + state.focus.todayMinutes + " мин · " + state.focus.todaySessions + " сессий"),
        );
      }
      async function startFocus() {
        try {
          setFocus(await api("/api/focus/start", { method: "POST", body: { minutes: form.minutes, habitId: form.habitId } }));
          haptic("success");
          paint();
        } catch (err) {
          toast(err.message);
        }
      }
      async function finishFocus(id, action) {
        if (action === "cancel" && !(await confirmDialog("Прервать фокус? Время не засчитается."))) return;
        try {
          const res = await api("/api/focus/" + id + "/" + action, { method: "POST", body: {} });
          setFocus(res);
          if (action === "complete") {
            toast(res.marked ? "Засчитано: " + res.marked + " ✅" : "Сессия засчитана ✅");
            breakEnds = Date.now() + 5 * 60 * 1000;
          }
          paint();
          loadState().then(() => { if (!closeCurrentSheet) render(); });
        } catch (err) {
          toast(err.message);
        }
      }
      paint();
      clearInterval(focusTicker);
      focusTicker = setInterval(() => {
        if (!document.body.contains(box)) { clearInterval(focusTicker); render(); return; }
        if (state.focus.active || breakEnds) paint();
      }, 1000);
      return h("div", null, h("h2", null, "⏱ Фокус"), box);
    });
  }

  // ---------- goals ----------
  function renderGoals() {
    const goals = state.goals || [];
    const out = [h("div", { class: "section-title row-title" },
      h("span", null, "🚀 Цели"),
      h("button", { class: "mini-add", onclick: openGoalSheet }, "+ добавить"))];
    if (!goals.length) {
      out.push(h("button", { class: "avoid-empty goal-empty", onclick: openGoalSheet }, "🚀 Поставь цель на месяц или квартал — например, «Похудеть на 5 кг» — и привяжи к ней привычки."));
      return out;
    }
    out.push(h("div", { class: "plan-list" }, goals.map((g) => h("div", { class: "panel goal" + (g.done ? " done" : ""), role: "button", tabindex: "0", onclick: () => openGoalDetail(g) },
      h("div", { class: "ch-head" },
        h("div", { class: "plan-icon" }, g.emoji),
        h("div", { class: "plan-body" },
          h("div", { class: "plan-title" }, (g.done ? "🏆 " : "") + g.title),
          h("div", { class: "plan-meta" }, [
            g.target ? fmtNum(g.current) + " / " + fmtNum(g.target) + (g.unit ? " " + g.unit : "") : "по привычкам",
            g.daysLeft != null ? (g.daysLeft >= 0 ? "осталось " + g.daysLeft + " дн." : "срок прошёл") : null,
          ].filter(Boolean).join(" · ")),
        ),
        h("div", { class: "goal-pct" }, Math.round(g.progress * 100) + "%"),
      ),
      h("div", { class: "goal-bar ch-progress" }, h("i", { style: "width:" + Math.round(g.progress * 100) + "%" })),
      g.habits.length ? h("div", { class: "goal-habits" }, g.habits.map((x) => h("span", { class: "gh" }, x.emoji + " " + (x.rate == null ? "—" : Math.round(x.rate * 100) + "%")))) : null,
    ))));
    return out;
  }
  const fmtNum = (n) => (Number.isInteger(n) ? String(n) : String(Math.round(n * 10) / 10)).replace(".", ",");

  function habitPicker(selected) {
    const all = (state.data.habits || []);
    const chosen = new Set(selected || []);
    const chips = all.map((x) => {
      const b = h("button", { class: "chip" + (chosen.has(x.id) ? " on" : ""), onclick: () => { chosen.has(x.id) ? chosen.delete(x.id) : chosen.add(x.id); b.classList.toggle("on", chosen.has(x.id)); haptic("select"); } }, x.emoji + " " + x.title);
      return b;
    });
    return { el: h("div", { class: "chips wrap" }, chips.length ? chips : h("span", { class: "section-hint" }, "Пока нет личных привычек")), ids: () => Array.from(chosen) };
  }

  function openGoalSheet() {
    haptic("light");
    const form = { title: "", emoji: "🚀", target: "", unit: "", deadline: "" };
    const IDEAS = [["🏃", "Похудеть", "5", "кг"], ["📚", "Прочитать книги", "4", "книг"], ["💰", "Накопить", "50000", "₽"], ["🇬🇧", "Выучить слова", "500", "слов"], ["🏋️", "Режим тренировок", "", ""]];
    openSheet(() => {
      const submit = h("button", { class: "btn", disabled: true, onclick: () => save() }, "Поставить цель");
      const title = h("input", { class: "input", maxlength: "60", placeholder: "Например, «Похудеть»", oninput: (e) => { form.title = e.target.value; submit.disabled = !form.title.trim(); } });
      const target = h("input", { class: "input", type: "number", inputmode: "decimal", placeholder: "Сколько (необяз.)", oninput: (e) => { form.target = e.target.value; } });
      const unit = h("input", { class: "input", maxlength: "12", placeholder: "кг, книг, ₽", oninput: (e) => { form.unit = e.target.value; } });
      const quarter = new Date(); quarter.setMonth(quarter.getMonth() + 3);
      const month = new Date(); month.setMonth(month.getMonth() + 1);
      const deadline = h("input", { class: "input", type: "date", min: state.data.today, onchange: (e) => { form.deadline = e.target.value; } });
      const picker = habitPicker([]);
      async function save() {
        submit.disabled = true;
        try {
          const res = await api("/api/goals", { method: "POST", body: { title: form.title, emoji: form.emoji, target: form.target ? Number(form.target.replace(",", ".")) : null, unit: form.unit, deadline: form.deadline || null, habitIds: picker.ids() } });
          state.goals = res.goals;
          haptic("success");
          closeSheet();
          render();
          toast("Цель поставлена 🚀");
        } catch (err) {
          submit.disabled = false;
          toast(err.message);
        }
      }
      return h("div", null,
        h("h2", null, "Новая цель"),
        h("div", { class: "field-label" }, "Идеи"),
        h("div", { class: "chips" }, IDEAS.map(([em, t, n, u]) => h("button", { class: "chip", onclick: () => { form.title = t; title.value = t; form.emoji = em; form.target = n; target.value = n; form.unit = u; unit.value = u; submit.disabled = false; } }, em + " " + t))),
        h("div", { class: "field-label" }, "Цель"), title,
        h("div", { class: "two-col" }, h("div", null, h("div", { class: "field-label" }, "Число"), target), h("div", null, h("div", { class: "field-label" }, "Единица"), unit)),
        h("div", { class: "field-label" }, "Срок"),
        h("div", { class: "chips wrap", style: "margin-bottom:8px" },
          h("button", { class: "chip", onclick: () => { form.deadline = isoOf(month); deadline.value = form.deadline; } }, "Месяц"),
          h("button", { class: "chip", onclick: () => { form.deadline = isoOf(quarter); deadline.value = form.deadline; } }, "Квартал")),
        deadline,
        h("div", { class: "field-label" }, "Привычки, которые ведут к цели"),
        picker.el,
        h("div", { class: "section-hint", style: "margin-top:10px" }, "Если число не указано, прогресс цели — это средний % выполнения привязанных привычек."),
        submit,
      );
    });
  }

  function openGoalDetail(g) {
    haptic("light");
    openSheet(() => {
      const value = h("input", { class: "input", type: "number", inputmode: "decimal", value: String(g.current) });
      const picker = habitPicker(g.habits.map((x) => x.id));
      const patch = async (body, message) => {
        try {
          state.goals = (await api("/api/goals/" + g.id, { method: "PATCH", body })).goals;
          haptic("success");
          closeSheet();
          render();
          toast(message);
        } catch (err) {
          toast(err.message);
        }
      };
      return h("div", null,
        h("div", { class: "detail-head" }, h("div", { class: "habit-icon" }, g.emoji),
          h("div", null, h("h2", null, g.title), h("div", { class: "habit-meta" }, g.deadline ? "до " + dayTitle(g.deadline) : "без срока"))),
        h("div", { class: "tiles two" }, tile(Math.round(g.progress * 100) + "%", "прогресс", true), tile(g.daysLeft != null ? String(Math.max(0, g.daysLeft)) : "∞", "дней осталось")),
        g.target ? [h("div", { class: "field-label" }, "Сейчас (" + fmtNum(g.target) + (g.unit ? " " + g.unit : "") + " — цель)"), value,
          h("button", { class: "btn", onclick: () => patch({ current: Number(String(value.value).replace(",", ".")) || 0 }, "Прогресс обновлён") }, "Обновить прогресс")] : null,
        h("div", { class: "field-label" }, "Привычки"),
        picker.el,
        h("button", { class: "btn ghost", onclick: () => patch({ habitIds: picker.ids() }, "Привычки обновлены") }, "Сохранить привычки"),
        h("button", { class: "btn ghost", onclick: () => patch({ done: !g.done }, g.done ? "Цель снова в работе" : "Цель достигнута 🏆") }, g.done ? "Вернуть в работу" : "🏆 Цель достигнута"),
        h("button", {
          class: "btn danger",
          onclick: async () => {
            if (!(await confirmDialog("Удалить цель «" + g.title + "»?"))) return;
            try {
              state.goals = (await api("/api/goals/" + g.id, { method: "DELETE" })).goals;
              closeSheet();
              render();
              toast("Цель удалена");
            } catch (err) {
              toast(err.message);
            }
          },
        }, "Удалить цель"),
      );
    });
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
    const weekMet = (x) => x.weeklyTarget && x.weekDone >= x.weeklyTarget;
    const todo = rest.filter((x) => x.dueToday && !x.meDone);
    const done = rest.filter((x) => (x.dueToday && x.meDone) || (weekMet(x) && !x.dueToday));
    const off = rest.filter((x) => !x.dueToday && !weekMet(x));
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
    if (d.archivedCount) {
      out.push(h("button", { class: "archive-link", onclick: openArchiveSheet }, "🗄 Архив · " + d.archivedCount));
    }
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
      : x.targetAmount
        ? h("button", {
          class: "check plus" + (x.meMarked ? " on" : ""),
          "aria-label": "Добавить " + (x.step || 1),
          onclick: (e) => { e.stopPropagation(); addAmount(x, e.currentTarget); },
        }, x.meMarked ? icon("check") : "+" + (x.step || 1))
        : h("button", {
          class: "check" + (x.meMarked ? " on" : ""),
          "aria-label": x.meMarked ? "Снять отметку" : "Отметить выполнение",
          "aria-pressed": x.meMarked ? "true" : "false",
          onclick: (e) => { e.stopPropagation(); toggle(x, e.currentTarget); },
        }, icon("check"));

    const meta = (x.pinned ? "📌 " : "") + (isAvoid
      ? (x.meMarked ? "срыв сегодня" : x.partnerMarked ? "срыв у " + state.data.pair.partner : "держусь")
      : x.targetAmount
        ? x.myAmount + "/" + x.targetAmount + (x.unit ? " " + x.unit : "") + (x.partnerAmount != null ? " · " + initial(state.data.pair.partner) + ": " + x.partnerAmount : "")
        : x.scheduleLabel + (x.weeklyTarget ? " · " + x.weekDone + "/" + x.weeklyTarget : ""));
    const goal = x.goalDays
      ? h("div", { class: "goal-line", title: "Цель: " + x.goalProgress + "/" + x.goalDays },
        h("span", { class: "goal-bar" }, h("i", { style: "width:" + Math.min(100, Math.round((x.goalProgress / x.goalDays) * 100)) + "%" })),
        h("small", null, Math.min(x.goalProgress, x.goalDays) + "/" + x.goalDays))
      : null;

    return h("div", { class: classes.join(" "), role: "button", tabindex: "0", onclick: () => openHabitSheet(x) },
      h("div", { class: "habit-top" }, h("div", { class: "habit-icon" }, x.emoji), action),
      h("div", { class: "habit-title" }, x.title),
      h("div", { class: "habit-meta" }, meta),
      goal,
      h("div", { class: "habit-bottom" },
        h("div", { class: "week", "aria-label": "Эта неделя" }, x.week.map((w) => h("span", { class: "seg " + w.status, title: w.date }))),
        h("div", { class: "habit-foot" },
          personDots(x, isAvoid ? "bad" : "good"),
          x.reactionToMe ? h("span", { class: "reaction-badge", title: "Реакция " + state.data.pair.partner }, x.reactionToMe) : null,
          h("span", { class: "streak" + (isAvoid ? " clean" : ""), "aria-label": (isAvoid ? "Без срывов " : "Серия ") + x.streak },
            isAvoid ? icon("shield") : icon("flame"), String(x.streak),
            isAvoid || x.weeklyTarget ? h("small", null, " " + x.streakUnit) : null),
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
    if (s.moodEffects && s.moodEffects.length) {
      page.push(h("div", { class: "section-title" }, "Настроение и привычки"),
        h("div", { class: "panel hlist" },
          h("div", { class: "section-hint", style: "margin:0 0 6px" }, "Среднее настроение в дни с привычкой и без неё (за 90 дней)"),
          s.moodEffects.map((e) => h("div", { class: "item" },
            h("div", { class: "ic" }, e.emoji),
            h("div", { class: "body" },
              h("div", { class: "name" }, (e.kind === "avoid" ? "🚫 " : "") + e.title),
              h("div", { class: "sub" }, "дней с ней: " + e.with + " · без: " + e.without)),
            h("div", { class: "pct " + (e.diff > 0 ? "up" : e.diff < 0 ? "down" : "") }, (e.diff > 0 ? "+" : "") + e.diff.toFixed(1)),
          ))));
    }
    if (s.year) page.push(h("div", { class: "section-title" }, "Год"), heatmap(s.year));

    page.push(
      h("div", { class: "section-title" }, "Привычки за 30 дней"),
      h("div", { class: "panel hlist" }, s.habits.map((x) => h("div", { class: "item" },
        h("div", { class: "ic" }, x.emoji),
        h("div", { class: "body" },
          h("div", { class: "name" }, (x.kind === "avoid" ? "🚫 " : "") + x.title),
          h("div", { class: "mini" }, h("span", { style: "width:" + Math.round((x.rate30 || 0) * 100) + "%" })),
          h("div", { class: "sub" }, (x.kind === "avoid" ? "без срыва " : "серия ") + x.streak + " " + x.streakUnit + " · рекорд " + x.best),
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

  async function addAmount(habit, button) {
    if (state.busy) return;
    state.busy = true;
    button.classList.add("pop");
    try {
      const res = await api("/api/amount", { method: "POST", body: { habitId: habit.id } });
      haptic(res.met ? "success" : "light");
      await loadState();
      render();
      if (res.met && !habit.meMarked) toast("Норма выполнена ✅");
      else if (!res.met) toast(res.value + " из " + habit.targetAmount + (habit.unit ? " " + habit.unit : ""));
    } catch (err) {
      toast(err.message);
    } finally {
      state.busy = false;
    }
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

  function openAddSheet(kind, existing) {
    haptic("light");
    const editing = Boolean(existing);
    const src = existing || {};
    const form = {
      kind: editing ? src.kind : (kind === "avoid" ? "avoid" : "do"),
      title: src.title || "",
      emoji: src.emoji || (kind === "avoid" ? "🚫" : "⭐"),
      schedule: src.schedule || 127,
      weekly: src.weeklyTarget || 0,
      amountOn: Boolean(src.targetAmount),
      target: src.targetAmount || 8,
      unit: src.unit || "",
      step: src.step || 1,
      goal: src.goalDays || 0,
      remindAt: src.remindAt || "",
    };
    openSheet(() => {
      const submit = h("button", { class: "btn", disabled: true, onclick: () => save() }, editing ? "Сохранить" : "Добавить");
      const input = h("input", {
        class: "input", maxlength: "40", enterkeyhint: "done", value: form.title,
        oninput: (e) => { form.title = e.target.value; paint(); },
      });
      const emojiButtons = EMOJIS.map((em) => h("button", {
        class: "emoji-opt",
        onclick: () => { form.emoji = em; paint(); haptic("select"); },
      }, em));
      const dayButtons = WEEKDAYS.map((label, i) => h("button", {
        class: "day",
        onclick: () => { form.schedule ^= 1 << i; paint(); haptic("select"); },
      }, label));
      const preset = (label, mask) => h("button", { class: "chip", onclick: () => { form.schedule = mask; paint(); } }, label);
      const daysBox = h("div", null,
        h("div", { class: "days" }, dayButtons),
        h("div", { class: "presets" }, preset("Каждый день", 127), preset("Будни", 31), preset("Выходные", 96)),
      );
      const weeklyButtons = [1, 2, 3, 4, 5, 6].map((n) => h("button", {
        class: "day",
        onclick: () => { form.weekly = n; paint(); haptic("select"); },
      }, n + "×"));
      const weeklyBox = h("div", null,
        h("div", { class: "days six" }, weeklyButtons),
        h("div", { class: "section-hint", style: "margin:10px 4px 0" }, "Отмечай в любые дни — главное набрать нужное число раз за неделю (пн–вс)."),
      );
      const modeButtons = [["days", "По дням"], ["weekly", "Раз в неделю"]].map(([mode, label]) => h("button", {
        class: "tab",
        onclick: () => { form.weekly = mode === "weekly" ? (form.weekly || 3) : 0; if (form.weekly) form.amountOn = false; paint(); haptic("select"); },
      }, label));
      const modeTabs = h("div", { class: "tabs kind-tabs" }, modeButtons);

      // Quantitative: "8 стаканов воды", one "+" adds `step`.
      const amountSwitch = h("button", { class: "switch", role: "switch", onclick: () => { form.amountOn = !form.amountOn; if (form.amountOn) form.weekly = 0; paint(); haptic("select"); } }, h("span"));
      const targetInput = h("input", { class: "input", type: "number", inputmode: "numeric", min: "1", value: String(form.target), oninput: (e) => { form.target = parseInt(e.target.value, 10) || 0; paint(); } });
      const unitInput = h("input", { class: "input", maxlength: "12", placeholder: "стаканов", value: form.unit, oninput: (e) => { form.unit = e.target.value; } });
      const stepInput = h("input", { class: "input", type: "number", inputmode: "numeric", min: "1", value: String(form.step), oninput: (e) => { form.step = parseInt(e.target.value, 10) || 0; paint(); } });
      const amountFields = h("div", { class: "amount-fields" },
        h("div", null, h("div", { class: "field-label" }, "Норма/день"), targetInput),
        h("div", null, h("div", { class: "field-label" }, "Единица"), unitInput),
        h("div", null, h("div", { class: "field-label" }, "Шаг «+»"), stepInput),
      );
      const amountBox = h("div", null,
        h("div", { class: "set-row" }, h("span", null, "Считать количество"), amountSwitch),
        h("div", { class: "section-hint", style: "margin:6px 4px 0" }, "Например, 8 стаканов воды или 30 минут чтения — нажимаешь «+» в течение дня."),
        amountFields,
      );

      const GOALS = [0, 21, 30, 66, 100];
      const goalButtons = GOALS.map((n) => h("button", { class: "chip", onclick: () => { form.goal = n; goalInput.value = ""; paint(); haptic("select"); } }, n ? n + " дн." : "Без цели"));
      const goalInput = h("input", { class: "input small", type: "number", inputmode: "numeric", min: "1", max: "3650", placeholder: "Своя", value: GOALS.includes(form.goal) ? "" : String(form.goal), oninput: (e) => { form.goal = parseInt(e.target.value, 10) || 0; paint(); } });
      const goalLabel = h("div", { class: "field-label" });

      const timeInput = h("input", { class: "input", type: "time", value: form.remindAt, onchange: (e) => { form.remindAt = e.target.value; } });
      const remindBox = h("div", null,
        h("div", { class: "field-label" }, "Напомнить в (необязательно)"),
        h("div", { class: "two-col" }, timeInput, h("button", { class: "btn ghost slim", onclick: () => { form.remindAt = ""; timeInput.value = ""; } }, "Без напоминания")),
      );

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

      function paint() {
        const isAvoid = form.kind === "avoid";
        if (isAvoid) { form.weekly = 0; form.amountOn = false; }
        const weekly = form.weekly > 0;
        emojiButtons[0].textContent = EMOJIS.includes(form.emoji) ? EMOJIS[0] : form.emoji;
        emojiButtons.forEach((b) => b.classList.toggle("on", b.textContent === form.emoji));
        modeTabs.style.display = isAvoid ? "none" : "";
        modeButtons[0].classList.toggle("active", !weekly);
        modeButtons[1].classList.toggle("active", weekly);
        daysBox.style.display = weekly ? "none" : "";
        weeklyBox.style.display = weekly ? "" : "none";
        weeklyButtons.forEach((b, i) => b.classList.toggle("on", form.weekly === i + 1));
        dayButtons.forEach((b, i) => b.classList.toggle("on", Boolean(form.schedule & (1 << i))));
        amountBox.style.display = isAvoid || weekly ? "none" : "";
        amountSwitch.classList.toggle("on", form.amountOn);
        amountFields.style.display = form.amountOn ? "" : "none";
        remindBox.style.display = isAvoid ? "none" : "";
        goalLabel.textContent = isAvoid ? "Цель: дней без срыва" : "Цель: сколько раз выполнить";
        goalButtons.forEach((b, i) => b.classList.toggle("on", GOALS[i] === form.goal));
        const amountOk = !form.amountOn || (form.target > 0 && form.step > 0);
        submit.disabled = !form.title.trim() || (!weekly && !form.schedule) || !amountOk;
      }
      function paintKind() {
        const isAvoid = form.kind === "avoid";
        kindButtons.forEach((b, i) => b.classList.toggle("active", (i === 1) === isAvoid));
        input.placeholder = isAvoid ? "Например, «Курить»" : "Например, «Прогулка 30 минут»";
        kindHint.textContent = isAvoid
          ? "Каждый день без срыва засчитывается сам. Отмечать нужно только срыв."
          : "Отмечай выполнение каждый день по расписанию.";
        const source = isAvoid ? state.data.avoidTemplates : state.data.templates;
        const templates = editing ? [] : (source || []).filter((t) => !state.data.habits.some((x) => x.title === t.title));
        templatesBox.replaceChildren(...(templates.length ? [
          h("div", { class: "field-label" }, "Шаблоны"),
          h("div", { class: "chips" }, templates.map((t) => h("button", {
            class: "chip",
            onclick: () => { form.title = t.title; input.value = t.title; form.emoji = t.emoji; paint(); haptic("select"); },
          }, t.emoji + " " + t.title))),
        ] : []));
        paint();
      }
      async function save() {
        submit.disabled = true;
        const weekly = form.weekly > 0;
        const body = {
          title: form.title, emoji: form.emoji,
          schedule: weekly ? 127 : form.schedule,
          weeklyTarget: weekly ? form.weekly : null,
          targetAmount: form.amountOn ? form.target : null,
          unit: form.amountOn ? form.unit : null,
          step: form.amountOn ? form.step : null,
          goalDays: form.goal || null,
          remindAt: form.kind === "do" && form.remindAt ? form.remindAt : null,
        };
        try {
          if (editing) {
            await api("/api/habits/" + src.id, { method: "PATCH", body });
          } else {
            await api("/api/habits", { method: "POST", body: Object.assign({ scope: state.scope, kind: form.kind }, body) });
          }
          haptic("success");
          closeSheet();
          await refresh();
          toast(editing ? "Сохранено" : form.kind === "avoid" ? "Держись! Считаю дни без срывов 🛡" : "Привычка добавлена ✨");
        } catch (err) {
          submit.disabled = false;
          toast(err.message);
        }
      }

      paintKind();
      return h("div", null,
        h("h2", null, editing ? "Изменить привычку" : "Новая привычка"),
        editing ? null : h("div", { class: "tabs kind-tabs" }, kindButtons),
        editing ? null : kindHint,
        templatesBox,
        h("div", { class: "field-label" }, "Название"),
        input,
        h("div", { class: "field-label" }, "Иконка"),
        h("div", { class: "emojis" }, emojiButtons),
        h("div", { class: "field-label" }, "Как часто"),
        modeTabs,
        daysBox,
        weeklyBox,
        amountBox,
        goalLabel,
        h("div", { class: "chips wrap" }, goalButtons, goalInput),
        remindBox,
        submit,
      );
    });
  }

  // ---------- archive ----------
  function openArchiveSheet() {
    haptic("light");
    const list = h("div", { class: "plan-list" }, h("div", { class: "empty-day" }, "Загрузка…"));
    openSheet(() => h("div", null,
      h("h2", null, "Архив"),
      h("div", { class: "section-hint" }, "Привычки в архиве не мешают, а история и статистика сохраняются. Можно вернуть в любой момент."),
      list,
      h("button", { class: "btn ghost", style: "margin-top:18px", onclick: closeSheet }, "Закрыть"),
    ));
    api("/api/archive?scope=" + state.scope).then((res) => {
      if (!res.habits.length) {
        list.replaceChildren(h("div", { class: "empty-day" }, "Архив пуст"));
        return;
      }
      list.replaceChildren(...res.habits.map((x) => h("div", { class: "plan archived" },
        h("div", { class: "plan-icon" }, x.emoji),
        h("div", { class: "plan-body" },
          h("div", { class: "plan-title" }, x.title),
          h("div", { class: "plan-meta" }, (x.kind === "avoid" ? "дней без срыва: " : "выполнено дней: ") + x.doneDays + " · рекорд " + x.best),
          h("div", { class: "plan-actions" },
            h("button", { class: "pbtn yes", onclick: () => archiveAction(x, "restore") }, "Вернуть"),
            h("button", { class: "pbtn no", onclick: () => archiveAction(x, "delete") }, "Удалить"),
          ),
        ),
      )));
    }).catch((err) => list.replaceChildren(h("div", { class: "empty-day" }, err.message)));
  }

  async function archiveAction(x, action) {
    try {
      if (action === "delete") {
        if (!(await confirmDialog("Удалить «" + x.title + "» навсегда? История отметок пропадёт."))) return;
        await api("/api/habits/" + x.id + "?forever=1", { method: "DELETE" });
        toast("Удалено навсегда");
      } else {
        await api("/api/habits/" + x.id + "/restore", { method: "POST", body: {} });
        toast("Привычка вернулась ✨");
      }
      closeSheet();
      await refresh();
    } catch (err) {
      toast(err.message);
    }
  }

  // ---------- section settings ----------
  function openSettingsSheet() {
    haptic("light");
    const DEFAULTS = { p: "Личные", f: "С другом", c: "Пара" };
    const HEADINGS = { p: "🎯 Личный раздел", f: "🤝 Раздел с другом", c: "💞 Раздел для пары" };
    const form = {};
    ["p", "f", "c"].forEach((id) => { form[id] = { name: sectionInfo(id).name || "", hidden: Boolean(sectionInfo(id).hidden) }; });

    openSheet(() => {
      const blocks = ["p", "f", "c"].map((id) => {
        const info = sectionInfo(id);
        const input = h("input", {
          class: "input", maxlength: "20", placeholder: DEFAULTS[id], value: form[id].name,
          oninput: (e) => { form[id].name = e.target.value; },
        });
        let toggle = null;
        if (id !== "p") {
          const sw = h("button", {
            class: "switch" + (form[id].hidden ? "" : " on"),
            role: "switch",
            "aria-checked": form[id].hidden ? "false" : "true",
            onclick: () => {
              form[id].hidden = !form[id].hidden;
              sw.classList.toggle("on", !form[id].hidden);
              sw.setAttribute("aria-checked", form[id].hidden ? "false" : "true");
              haptic("select");
            },
          }, h("span"));
          toggle = h("div", { class: "set-row" }, h("span", null, "Показывать вкладку"), sw);
        }
        let pairRow = null;
        if (id !== "p") {
          const status = info.pair === "active" ? "Связка: " + info.partner
            : info.pair === "pending" ? "Приглашение отправлено, ждём ответа" : "Связки нет";
          pairRow = h("div", { class: "set-row muted" },
            h("span", null, status),
            info.pair !== "none" ? h("button", { class: "link-danger", onclick: () => unpairSection(id, info) }, info.pair === "active" ? "Разорвать" : "Отменить") : null,
          );
        }
        return h("div", { class: "panel set-block" },
          h("div", { class: "set-title" }, HEADINGS[id]),
          h("div", { class: "field-label" }, "Название вкладки"),
          input,
          toggle,
          pairRow,
        );
      });

      const save = h("button", {
        class: "btn",
        onclick: async () => {
          save.disabled = true;
          try {
            for (const id of ["p", "f", "c"]) {
              const info = sectionInfo(id);
              const changed = (info.name || "") !== form[id].name.trim() || Boolean(info.hidden) !== form[id].hidden;
              if (!changed) continue;
              const body = { scope: id, name: form[id].name.trim() };
              if (id !== "p") body.hidden = form[id].hidden;
              const res = await api("/api/sections", { method: "POST", body });
              state.sections = res.sections;
            }
            haptic("success");
            closeSheet();
            if (sectionInfo(state.scope).hidden) { state.scope = "p"; saveScope("p"); }
            await refresh();
            toast("Сохранено");
          } catch (err) {
            save.disabled = false;
            toast(err.message);
          }
        },
      }, "Сохранить");

      return h("div", null,
        h("h2", null, "Настройки"),
        notificationsBlock(),
        h("div", { class: "section-title" }, "Разделы"),
        h("div", { class: "section-hint" }, "Названия и видимость вкладок — только для тебя. У второго участника всё останется как было."),
        blocks,
        save,
      );
    });
  }

  function notificationsBlock() {
    const box = h("div", { class: "panel set-block" }, h("div", { class: "set-title" }, "🔔 Уведомления"), h("div", { class: "section-hint", style: "margin-top:8px" }, "Загрузка…"));
    const hourSelect = (value, from, to, onChange) => {
      const select = h("select", { class: "input select", onchange: (e) => onChange(e.target.value === "" ? null : Number(e.target.value)) },
        h("option", { value: "" }, "Выкл"),
        Array.from({ length: to - from + 1 }, (_, i) => from + i).map((hour) => h("option", { value: String(hour) }, String(hour).padStart(2, "0") + ":00")));
      select.value = value == null ? "" : String(value);
      return select;
    };
    const save = async (patch) => {
      try {
        await api("/api/notifications", { method: "POST", body: patch });
        haptic("select");
        toast("Сохранено");
      } catch (err) {
        toast(err.message);
      }
    };
    const switchRow = (label, on, key) => {
      const sw = h("button", { class: "switch" + (on ? " on" : ""), role: "switch", onclick: () => { on = !on; sw.classList.toggle("on", on); save({ [key]: on }); } }, h("span"));
      return h("div", { class: "set-row" }, h("span", null, label), sw);
    };
    api("/api/notifications").then((n) => {
      box.replaceChildren(
        h("div", { class: "set-title" }, "🔔 Уведомления"),
        h("div", { class: "field-label" }, "Вечернее напоминание о неотмеченных"),
        hourSelect(n.reminderHour, 7, 23, (v) => save({ reminderHour: v })),
        h("div", { class: "field-label" }, "Утренний план дня"),
        hourSelect(n.digestHour, 5, 12, (v) => save({ digestHour: v })),
        switchRow("📊 Итоги недели (вс, 20:00)", n.weeklyReport, "weeklyReport"),
        switchRow("🗂 Копия данных в чат (вс)", n.weeklyExport, "weeklyExport"),
        h("button", {
          class: "btn ghost slim", style: "margin-top:14px",
          onclick: async () => {
            try {
              await api("/api/export", { method: "POST", body: {} });
              toast("Файл отправлен в чат с ботом 📨");
            } catch (err) {
              toast(err.message);
            }
          },
        }, "Выгрузить данные сейчас (CSV)"),
        h("div", { class: "section-hint", style: "margin-top:10px" }, "Время для отдельной привычки задаётся в её настройках (✏️ Изменить). Про планы бот напомнит за час."),
      );
    }).catch((err) => box.replaceChildren(h("div", { class: "section-hint" }, err.message)));
    return box;
  }

  async function unpairSection(id, info) {
    const text = info.pair === "active"
      ? "Разорвать связку с " + info.partner + "? Общие привычки, отметки, настроение и планы будут удалены у вас обоих."
      : "Отменить приглашение?";
    if (!(await confirmDialog(text))) return;
    try {
      const res = await api("/api/unpair", { method: "POST", body: { scope: id } });
      state.sections = res.sections;
      haptic("success");
      closeSheet();
      await refresh();
      toast(info.pair === "active" ? "Связка разорвана" : "Приглашение отменено");
    } catch (err) {
      toast(err.message);
    }
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

  function shortDay(iso) {
    const d = dateOf(iso);
    return WEEKDAYS[(d.getDay() + 6) % 7] + ", " + d.getDate() + " " + MONTHS_GEN[d.getMonth()];
  }

  function openHabitSheet(x) {
    haptic("light");
    const isAvoid = x.kind === "avoid";
    const extra = h("div", null, h("div", { class: "empty-day", style: "margin-top:16px" }, "Загрузка…"));
    const tiles = [
      tile(String(x.streak), isAvoid ? "дней без срыва" : x.weeklyTarget ? "недель подряд" : "текущая серия"),
      tile(String(x.best), isAvoid ? "рекорд без срыва" : x.weeklyTarget ? "рекорд, недель" : "рекорд"),
    ];
    if (x.goalDays) tiles.push(tile(Math.min(x.goalProgress, x.goalDays) + "/" + x.goalDays, x.goalProgress >= x.goalDays ? "цель достигнута 🏆" : "к цели", true));
    openSheet(() => h("div", null,
      h("div", { class: "detail-head" },
        h("div", { class: "habit-icon" }, x.emoji),
        h("div", null, h("h2", null, x.title), h("div", { class: "habit-meta" }, (isAvoid ? "не делать · " : "") + x.scheduleLabel + (x.remindAt ? " · ⏰ " + x.remindAt : ""))),
      ),
      h("div", { class: "tiles" + (tiles.length === 2 ? " two" : "") }, tiles),
      h("div", { class: "week", style: "margin:18px 0 6px" }, x.week.map((w) => h("span", { class: "seg " + w.status }))),
      h("div", { class: "labels" }, WEEKDAYS.map((w) => h("span", null, w))),
      x.targetAmount ? amountControl(x, "today", x.myAmount) : null,
      x.partnerDone !== null && x.partnerDone !== undefined && x.kind !== "avoid" ? socialBlock(x) : null,
      extra,
      h("div", { class: "detail-actions" },
        h("button", { class: "btn ghost", onclick: () => { closeSheet(); openAddSheet(x.kind, x); } }, "✏️ Изменить"),
        h("button", {
          class: "btn ghost",
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
            if (!(await confirmDialog("Убрать «" + x.title + "» в архив? История сохранится, вернуть можно в любой момент."))) return;
            try {
              await api("/api/habits/" + x.id, { method: "DELETE" });
              closeSheet();
              await refresh();
              toast("Убрано в архив 🗄");
            } catch (err) {
              toast(err.message);
            }
          },
        }, "🗄 В архив"),
        h("button", { class: "btn ghost", onclick: closeSheet }, "Закрыть"),
      ),
    ));
    loadHabitDetails(x, extra);
  }

  function socialBlock(x) {
    const partner = state.data.pair.partner;
    if (x.partnerDone) {
      const buttons = ["🔥", "👏", "💪", "❤️", "😎"].map((em) => h("button", {
        class: "mood" + (x.myReaction === em ? " on" : ""),
        onclick: async () => {
          try {
            await api("/api/react", { method: "POST", body: { habitId: x.id, emoji: x.myReaction === em ? null : em } });
            haptic("success");
            x.myReaction = x.myReaction === em ? null : em;
            buttons.forEach((b) => b.classList.toggle("on", b.textContent === x.myReaction));
            toast(x.myReaction ? "Реакция отправлена " + em : "Реакция убрана");
            loadState();
          } catch (err) {
            toast(err.message);
          }
        },
      }, em));
      return h("div", { class: "panel social" },
        h("div", { class: "field-label", style: "margin:0 0 10px" }, partner + " уже сделал(а) — поддержи"),
        h("div", { class: "mood-row" }, buttons),
        x.reactionToMe ? h("div", { class: "mood-partner" }, partner + " реагирует на тебя: " + x.reactionToMe) : null,
      );
    }
    return h("div", { class: "panel social" },
      h("div", { class: "field-label", style: "margin:0 0 10px" }, partner + " ещё не отметил(а)"),
      h("button", {
        class: "btn " + (x.nudgedToday ? "ghost" : ""), disabled: x.nudgedToday,
        onclick: async (e) => {
          try {
            await api("/api/nudge", { method: "POST", body: { habitId: x.id } });
            haptic("success");
            e.currentTarget.disabled = true;
            x.nudgedToday = true;
            toast("Напоминание отправлено 👊");
            loadState();
          } catch (err) {
            toast(err.message);
          }
        },
      }, x.nudgedToday ? "Сегодня уже напоминание было" : "👊 Пнуть " + partner),
      x.reactionToMe ? h("div", { class: "mood-partner" }, partner + " реагирует на тебя: " + x.reactionToMe) : null,
    );
  }

  function amountControl(x, when, value) {
    const step = x.step || 1;
    const label = h("div", { class: "amount-value" }, value + " / " + x.targetAmount + (x.unit ? " " + x.unit : ""));
    const send = async (body) => {
      try {
        const res = await api("/api/amount", { method: "POST", body: Object.assign({ habitId: x.id, date: when }, body) });
        label.textContent = res.value + " / " + x.targetAmount + (x.unit ? " " + x.unit : "");
        haptic(res.met ? "success" : "light");
        if (res.met && res.value - (body.delta || 0) < x.targetAmount) toast("Норма выполнена ✅");
        await loadState();
      } catch (err) {
        toast(err.message);
      }
    };
    return h("div", { class: "panel amount-box" },
      h("div", { class: "field-label", style: "margin:0 0 10px" }, when === "today" ? "Сегодня" : "Вчера"),
      h("div", { class: "amount-row" },
        h("button", { class: "amount-btn", "aria-label": "Убавить", onclick: () => send({ delta: -step }) }, "−" + step),
        label,
        h("button", { class: "amount-btn plus", "aria-label": "Добавить", onclick: () => send({ delta: step }) }, "+" + step),
      ),
    );
  }

  async function loadHabitDetails(x, box) {
    let info;
    try {
      info = await api("/api/habits/" + x.id);
    } catch (err) {
      box.replaceChildren(h("div", { class: "empty-day", style: "margin-top:16px" }, err.message));
      return;
    }
    const parts = [];
    const isAvoid = x.kind === "avoid";

    // Forgot to mark yesterday.
    if (x.targetAmount) {
      parts.push(amountControl(x, "yesterday", info.yesterdayAmount || 0));
    } else {
      const label = isAvoid
        ? (info.yesterdayMarked ? "Снять срыв за вчера" : "Отметить срыв за вчера")
        : (info.yesterdayMarked ? "Снять отметку за вчера" : "↩️ Отметить за вчера");
      parts.push(h("button", {
        class: "btn ghost", style: "margin-top:16px",
        onclick: async () => {
          if (isAvoid && !info.yesterdayMarked && !(await confirmDialog("Отметить срыв за вчера? Серия начнётся заново."))) return;
          try {
            await api("/api/checkin", { method: "POST", body: { habitId: x.id, date: "yesterday" } });
            haptic("success");
            closeSheet();
            await refresh();
            toast(info.yesterdayMarked ? "Отметка за вчера снята" : "Вчерашний день отмечен");
          } catch (err) {
            toast(err.message);
          }
        },
      }, label));
    }

    // Streak freezes.
    if (info.freezable.length || info.frozen.length) {
      parts.push(h("div", { class: "section-title" }, "❄️ Заморозка серии · осталось " + info.freezesLeft + " из 2 в месяце"));
      parts.push(h("div", { class: "section-hint" }, "Пропуск по уважительной причине засчитается, и серия не сгорит."));
      const chips = info.freezable.map((day) => h("button", {
        class: "chip freeze", disabled: info.freezesLeft === 0,
        onclick: async () => {
          try {
            await api("/api/freeze", { method: "POST", body: { habitId: x.id, date: day } });
            haptic("success");
            closeSheet();
            await refresh();
            toast("Заморожено ❄️ Серия спасена");
          } catch (err) {
            toast(err.message);
          }
        },
      }, "❄️ " + shortDay(day)));
      const frozenChips = info.frozen.filter((f) => f.mine).map((f) => h("button", {
        class: "chip on",
        onclick: async () => {
          try {
            await api("/api/freeze", { method: "POST", body: { habitId: x.id, date: f.date, remove: true } });
            closeSheet();
            await refresh();
            toast("Заморозка снята");
          } catch (err) {
            toast(err.message);
          }
        },
      }, "✕ " + shortDay(f.date)));
      parts.push(h("div", { class: "chips wrap" }, chips, frozenChips));
    }

    // Notes.
    const todayIso = state.data.today;
    const mine = info.notes.find((n) => n.mine && n.date === todayIso);
    const noteInput = h("textarea", { class: "input note", maxlength: "300", rows: "2", placeholder: isAvoid ? "Как держишься сегодня?" : "Как прошло? Например, «5 км за 28 минут»" });
    noteInput.value = mine ? mine.text : "";
    parts.push(
      h("div", { class: "section-title" }, "📝 Заметка за сегодня"),
      noteInput,
      h("button", {
        class: "btn ghost slim", style: "margin-top:8px",
        onclick: async () => {
          try {
            await api("/api/note", { method: "POST", body: { habitId: x.id, text: noteInput.value } });
            haptic("success");
            toast(noteInput.value.trim() ? "Заметка сохранена" : "Заметка удалена");
            loadHabitDetails(x, box);
          } catch (err) {
            toast(err.message);
          }
        },
      }, "Сохранить заметку"),
    );
    const past = info.notes.filter((n) => !(n.mine && n.date === todayIso));
    if (past.length) {
      parts.push(h("div", { class: "notes" }, past.map((n) => h("div", { class: "note-item" },
        h("div", { class: "note-meta" }, shortDay(n.date) + (state.data.pair ? " · " + (n.mine ? "ты" : n.author) : "")),
        h("div", null, n.text),
      ))));
    }
    parts.push(h("div", { class: "section-hint", style: "margin-top:14px" }, "Всего выполнено: " + info.totalDone + (isAvoid ? " дн. без срыва" : " раз")));
    box.replaceChildren(...parts);
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
