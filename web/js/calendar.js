/**
 * 日期集合选择 + 大日历页 + 聊天页小型勾选日历。
 * 召回过滤用 dates[]（集合），不再依赖连续 from~to。
 */

const DateSelection = (() => {
  let selected = new Set();
  const listeners = new Set();

  function notify() {
    for (const fn of listeners) {
      try {
        fn(get());
      } catch (e) {
        console.warn(e);
      }
    }
  }

  function get() {
    return [...selected].sort();
  }

  function set(dates) {
    selected = new Set(
      (dates || []).filter((d) => /^\d{4}-\d{2}-\d{2}$/.test(String(d)))
    );
    notify();
  }

  function toggle(dateStr) {
    if (selected.has(dateStr)) selected.delete(dateStr);
    else selected.add(dateStr);
    notify();
  }

  function addMany(dates) {
    for (const d of dates || []) {
      if (/^\d{4}-\d{2}-\d{2}$/.test(d)) selected.add(d);
    }
    notify();
  }

  function clear() {
    selected.clear();
    notify();
  }

  function has(dateStr) {
    return selected.has(dateStr);
  }

  function onChange(fn) {
    listeners.add(fn);
    return () => listeners.delete(fn);
  }

  function storageKey(cid) {
    return cid ? `rag_dates_${cid}` : "rag_dates_current";
  }

  function saveForConversation(cid) {
    if (!cid) return;
    localStorage.setItem(storageKey(cid), JSON.stringify(get()));
  }

  function loadForConversation(cid) {
    if (!cid) {
      set([]);
      return;
    }
    try {
      const raw = localStorage.getItem(storageKey(cid));
      set(raw ? JSON.parse(raw) : []);
    } catch {
      set([]);
    }
  }

  return {
    get,
    set,
    toggle,
    addMany,
    clear,
    has,
    onChange,
    saveForConversation,
    loadForConversation,
  };
})();

const CalendarPage = (() => {
  const WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"];

  let viewYear = new Date().getFullYear();
  let viewMonth = new Date().getMonth();
  let focusDate = null; // 左侧阅读焦点（可与勾选独立）
  let diaryDates = new Set();
  let diaryCounts = {};
  let bound = false;

  function $(sel) {
    return document.querySelector(sel);
  }

  function pad(n) {
    return String(n).padStart(2, "0");
  }

  function ymd(y, m, d) {
    return `${y}-${pad(m + 1)}-${pad(d)}`;
  }

  function formatTitle(y, m) {
    return `${y}年${m + 1}月`;
  }

  async function refreshDates() {
    const data = await api("/diary/calendar");
    diaryDates = new Set(data.dates || []);
    diaryCounts = data.counts || {};
    if (diaryDates.size && data.max_date) {
      const [yy, mm] = data.max_date.split("-").map(Number);
      const hasInView = [...diaryDates].some((d) => {
        const [a, b] = d.split("-").map(Number);
        return a === viewYear && b === viewMonth + 1;
      });
      if (!hasInView) {
        viewYear = yy;
        viewMonth = mm - 1;
      }
    }
    renderGrid();
    MiniDatePicker.render();
    updateSelectionBar();
  }

  function daysInMonth(y, m) {
    return new Date(y, m + 1, 0).getDate();
  }

  /** 周一为一周起始的 weekKey */
  function weekDatesContaining(dateStr) {
    const [y, m, d] = dateStr.split("-").map(Number);
    const dt = new Date(y, m - 1, d);
    const dow = (dt.getDay() + 6) % 7; // Mon=0
    const monday = new Date(y, m - 1, d - dow);
    const out = [];
    for (let i = 0; i < 7; i++) {
      const x = new Date(monday);
      x.setDate(monday.getDate() + i);
      out.push(ymd(x.getFullYear(), x.getMonth(), x.getDate()));
    }
    return out;
  }

  function monthDates(y, m) {
    const n = daysInMonth(y, m);
    const out = [];
    for (let d = 1; d <= n; d++) out.push(ymd(y, m, d));
    return out;
  }

  function renderGrid() {
    const grid = $("#calendar-grid");
    const title = $("#calendar-month-title");
    if (!grid || !title) return;
    title.textContent = formatTitle(viewYear, viewMonth);

    const first = new Date(viewYear, viewMonth, 1);
    let startPad = first.getDay() - 1;
    if (startPad < 0) startPad = 6;
    const dim = daysInMonth(viewYear, viewMonth);

    const frag = document.createDocumentFragment();
    const head = document.createElement("div");
    head.className = "cal-weekdays";
    for (const w of WEEKDAYS) {
      const el = document.createElement("div");
      el.className = "cal-weekday";
      el.textContent = w;
      head.appendChild(el);
    }
    frag.appendChild(head);

    const cells = document.createElement("div");
    cells.className = "cal-days";

    for (let i = 0; i < startPad; i++) {
      const blank = document.createElement("div");
      blank.className = "cal-day blank";
      cells.appendChild(blank);
    }

    const today = new Date();
    const todayStr = ymd(today.getFullYear(), today.getMonth(), today.getDate());

    for (let d = 1; d <= dim; d++) {
      const dateStr = ymd(viewYear, viewMonth, d);
      const has = diaryDates.has(dateStr);
      const checked = DateSelection.has(dateStr);
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className =
        "cal-day" +
        (has ? " has-diary" : " no-diary") +
        (dateStr === todayStr ? " is-today" : "") +
        (dateStr === focusDate ? " focused" : "") +
        (checked ? " checked" : "");
      btn.dataset.date = dateStr;
      btn.innerHTML = `
        <span class="cal-day-num">${d}</span>
        <span class="cal-check" aria-hidden="true">${checked ? "✓" : ""}</span>
      `;
      btn.title = has
        ? `${dateStr} · ${diaryCounts[dateStr] || 0} 段 · 单击打开；Ctrl+单击勾选`
        : `${dateStr} · 未录入 · Ctrl+单击可勾选`;
      btn.addEventListener("click", (e) => {
        // Ctrl/Cmd：仅勾选召回范围
        if (e.ctrlKey || e.metaKey) {
          DateSelection.toggle(dateStr);
          renderGrid();
          updateSelectionBar();
          return;
        }
        focusDate = dateStr;
        openDayView(dateStr);
      });
      cells.appendChild(btn);
    }

    frag.appendChild(cells);
    grid.innerHTML = "";
    grid.appendChild(frag);
    loadMonthWordcloud();
  }

  function showMonthView() {
    const month = $("#calendar-month-view");
    const day = $("#calendar-day-view");
    if (month) month.hidden = false;
    if (day) day.hidden = true;
    loadMonthWordcloud();
  }

  function showDayViewShell() {
    const month = $("#calendar-month-view");
    const day = $("#calendar-day-view");
    if (month) month.hidden = true;
    if (day) day.hidden = false;
  }

  async function openDayView(dateStr) {
    showDayViewShell();
    await loadDayText(dateStr);
  }

  let dayInsightsSeq = 0;
  let monthCloudSeq = 0;
  let currentInsightDate = "";
  let monthCloudCacheKey = "";
  let monthCloudCacheWords = null;

  function clearDaySide() {
    const poeticStatus = $("#day-poetic-status");
    const summary = $("#day-summary");
    const verse = $("#day-verse");
    const verseSource = $("#day-verse-source");
    const verseMeta = $("#day-verse-meta");
    const verseExplain = $("#day-verse-explain");
    const verseWhy = $("#day-verse-why");
    const refreshBtn = $("#btn-poetic-refresh");
    if (poeticStatus) poeticStatus.textContent = "";
    if (summary) summary.textContent = "";
    if (verse) verse.textContent = "";
    if (verseSource) verseSource.textContent = "";
    if (verseExplain) verseExplain.textContent = "";
    if (verseWhy) verseWhy.textContent = "";
    if (verseMeta) verseMeta.hidden = true;
    if (refreshBtn) refreshBtn.hidden = true;
  }

  /** 经典螺旋布局词云（词频决定字号，无 AI）。 */
  function drawWordCloud(canvas, words) {
    if (!canvas || !words.length) return;
    const wrap = canvas.parentElement;
    const cssW = Math.max(160, wrap ? wrap.clientWidth : 280);
    const cssH = Math.max(120, wrap ? wrap.clientHeight : 200);
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.floor(cssW * dpr);
    canvas.height = Math.floor(cssH * dpr);
    canvas.style.width = cssW + "px";
    canvas.style.height = cssH + "px";

    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);

    const palette = ["#2c3e50", "#5c6b73", "#8b6914", "#4a6741", "#6b4c3b", "#3d5a80"];
    const placed = [];
    const cx = cssW / 2;
    const cy = cssH / 2;
    const maxFont = Math.min(42, Math.floor(cssW / 7));
    const minFont = 11;

    function hit(box) {
      return placed.some(
        (p) =>
          !(
            box.x + box.w < p.x ||
            p.x + p.w < box.x ||
            box.y + box.h < p.y ||
            p.y + p.h < box.y
          )
      );
    }

    const sorted = [...words].sort((a, b) => (b.weight || 0) - (a.weight || 0));
    for (let i = 0; i < sorted.length; i++) {
      const item = sorted[i];
      const text = String(item.text || "");
      if (!text) continue;
      const w = Math.max(0.15, Number(item.weight) || 0.15);
      const fontSize = Math.round(minFont + (maxFont - minFont) * w);
      ctx.font = `600 ${fontSize}px "TW Kai", "KaiTi", "Noto Serif SC", serif`;
      const tw = ctx.measureText(text).width;
      const th = fontSize * 1.15;
      let placedOk = false;
      let angle = 0;
      let radius = 0;
      for (let step = 0; step < 400; step++) {
        const x = cx + Math.cos(angle) * radius - tw / 2;
        const y = cy + Math.sin(angle) * radius - th / 2;
        const box = { x, y, w: tw + 4, h: th };
        if (x >= 2 && y >= 2 && x + tw < cssW - 2 && y + th < cssH - 2 && !hit(box)) {
          ctx.fillStyle = palette[i % palette.length];
          ctx.fillText(text, x, y + fontSize * 0.85);
          placed.push(box);
          placedOk = true;
          break;
        }
        angle += 0.35;
        radius += 0.55;
      }
      if (!placedOk && i < 8) {
        const fs2 = Math.max(minFont, fontSize - 4);
        ctx.font = `600 ${fs2}px "TW Kai", "KaiTi", serif`;
        const tw2 = ctx.measureText(text).width;
        const x = (cssW - tw2) / 2;
        const y = cssH / 2 + (i - 2) * (fs2 + 4);
        ctx.fillStyle = palette[i % palette.length];
        ctx.fillText(text, x, y);
      }
    }
  }

  async function loadMonthWordcloud(force = false) {
    const seq = ++monthCloudSeq;
    const status = $("#month-cloud-status");
    const canvas = $("#month-wordcloud");
    const ym = `${viewYear}-${String(viewMonth + 1).padStart(2, "0")}`;

    // 同月已在内存：只重绘，不请求
    if (!force && monthCloudCacheKey === ym && Array.isArray(monthCloudCacheWords)) {
      if (monthCloudCacheWords.length) {
        drawWordCloud(canvas, monthCloudCacheWords);
        if (status)
          status.textContent = `${monthCloudCacheWords.length} 词 · 内存缓存`;
      } else if (status) {
        status.textContent = "本月暂无足够词条";
      }
      return;
    }

    if (status) status.textContent = "加载本月词云…";
    if (canvas) {
      const ctx = canvas.getContext("2d");
      if (ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
    }
    try {
      const q = force ? "?refresh=true" : "";
      const data = await api(`/diary/months/${ym}/wordcloud${q}`);
      if (seq !== monthCloudSeq) return;
      const words = data.words || [];
      monthCloudCacheKey = ym;
      monthCloudCacheWords = words;
      if (!words.length) {
        if (status) status.textContent = "本月暂无足够词条";
        return;
      }
      drawWordCloud(canvas, words);
      const tag = data.cached ? "已缓存" : data.just_generated ? "刚生成" : "";
      if (status)
        status.textContent = `${data.day_count || 0} 天 · ${words.length} 词${
          tag ? " · " + tag : ""
        }`;
    } catch (err) {
      if (seq !== monthCloudSeq) return;
      if (status) status.textContent = err.message || "词云失败";
    }
  }

  function renderPoetic(data) {
    const summaryEl = $("#day-summary");
    const verseEl = $("#day-verse");
    const verseSourceEl = $("#day-verse-source");
    const verseMetaEl = $("#day-verse-meta");
    const verseExplainEl = $("#day-verse-explain");
    const verseWhyEl = $("#day-verse-why");
    const poeticStatus = $("#day-poetic-status");
    const refreshBtn = $("#btn-poetic-refresh");

    if (summaryEl) summaryEl.textContent = data.summary || "";
    if (verseEl) verseEl.textContent = data.verse || "";
    if (verseSourceEl) {
      verseSourceEl.textContent = data.verse_source
        ? `—— ${data.verse_source}`
        : "";
    }
    const explain = (data.verse_explain || "").trim();
    const why = (data.verse_why || "").trim();
    if (verseExplainEl) verseExplainEl.textContent = explain;
    if (verseWhyEl) verseWhyEl.textContent = why;
    if (verseMetaEl) verseMetaEl.hidden = !(explain || why);
    if (refreshBtn) refreshBtn.hidden = false;

    if (!(data.summary || "").trim() && !(data.verse || "").trim()) {
      if (poeticStatus)
        poeticStatus.textContent = data.parse_error
          ? "解析失败，请稍后重试"
          : "暂无印象";
      return;
    }
    let note = data.cached ? "已缓存" : data.just_generated ? "刚生成并已保存" : "";
    if (data.source === "rag_sentences") note = (note ? note + " · " : "") + "基于检索句";
    else if (data.source === "chunks_fallback")
      note = (note ? note + " · " : "") + "基于原文";
    if (poeticStatus) poeticStatus.textContent = note;
  }

  async function loadDayInsights(dateStr, hasText, refresh = false) {
    const seq = ++dayInsightsSeq;
    currentInsightDate = dateStr;
    clearDaySide();
    const poeticStatus = $("#day-poetic-status");
    if (!hasText) {
      if (poeticStatus) poeticStatus.textContent = "无日记可总结";
      return;
    }

    if (poeticStatus)
      poeticStatus.textContent = refresh ? "重新生成中…" : "加载今日印象…";

    try {
      const q = refresh ? "?refresh=true" : "";
      const data = await api(`/diary/days/${dateStr}/poetic${q}`);
      if (seq !== dayInsightsSeq) return;
      renderPoetic(data);
    } catch (err) {
      if (seq !== dayInsightsSeq) return;
      if (poeticStatus) poeticStatus.textContent = err.message || "总结失败";
    }
  }

  async function loadDayText(dateStr) {
    const title = $("#day-panel-title");
    const meta = $("#day-panel-meta");
    const body = $("#day-panel-body");
    if (!body) return;

    if (title) title.textContent = dateStr;
    if (meta) meta.textContent = "加载中…";
    body.innerHTML = `<p class="day-panel-empty">加载中…</p>`;
    clearDaySide();

    try {
      const data = await api(`/diary/days/${dateStr}`);
      const n = data.chunk_count || 0;
      const hasText = !!(n && (data.text || "").trim());
      if (!hasText) {
        if (meta) meta.textContent = "未录入";
        body.innerHTML = `<p class="day-panel-empty">这一天还没有日记。</p>`;
        await loadDayInsights(dateStr, false);
        return;
      }
      if (meta) meta.textContent = `${n} 个片段`;
      const pre = document.createElement("pre");
      pre.className = "day-text";
      pre.textContent = data.text;
      body.innerHTML = "";
      body.appendChild(pre);
      await loadDayInsights(dateStr, true);
    } catch (err) {
      if (meta) meta.textContent = "加载失败";
      body.innerHTML = `<p class="day-panel-empty">${escapeHtml(err.message)}</p>`;
      clearDaySide();
    }
  }

  function escapeHtml(s) {
    const d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  function updateSelectionBar() {
    const el = $("#cal-selection-summary");
    if (!el) return;
    const n = DateSelection.get().length;
    el.textContent = n ? `已选 ${n} 天` : "未选择日期（召回不限范围）";
    const btn = $("#cal-new-chat");
    if (btn) btn.disabled = n === 0;
  }

  function shiftMonth(delta) {
    viewMonth += delta;
    if (viewMonth < 0) {
      viewMonth = 11;
      viewYear -= 1;
    } else if (viewMonth > 11) {
      viewMonth = 0;
      viewYear += 1;
    }
    renderGrid();
    MiniDatePicker.syncMonth(viewYear, viewMonth);
  }

  function goToday() {
    const t = new Date();
    viewYear = t.getFullYear();
    viewMonth = t.getMonth();
    renderGrid();
    MiniDatePicker.syncMonth(viewYear, viewMonth);
  }

  function selectCurrentWeek() {
    const base =
      focusDate ||
      ymd(viewYear, viewMonth, Math.min(new Date().getDate(), daysInMonth(viewYear, viewMonth)));
    DateSelection.addMany(weekDatesContaining(base));
    updateSelectionBar();
    renderGrid();
  }

  function selectCurrentMonth() {
    DateSelection.addMany(monthDates(viewYear, viewMonth));
    updateSelectionBar();
    renderGrid();
  }

  function selectDiaryInMonth() {
    DateSelection.addMany(
      monthDates(viewYear, viewMonth).filter((d) => diaryDates.has(d))
    );
    updateSelectionBar();
    renderGrid();
  }

  async function newChatWithSelection() {
    const dates = DateSelection.get();
    if (!dates.length) {
      showError("请先勾选至少一个日期");
      return;
    }
    if (typeof window.createChatWithDates === "function") {
      await window.createChatWithDates(dates);
    }
  }

  function bind() {
    if (bound) return;
    bound = true;
    $("#cal-prev")?.addEventListener("click", () => shiftMonth(-1));
    $("#cal-next")?.addEventListener("click", () => shiftMonth(1));
    $("#cal-today")?.addEventListener("click", goToday);
    $("#cal-select-week")?.addEventListener("click", selectCurrentWeek);
    $("#cal-select-month")?.addEventListener("click", selectCurrentMonth);
    $("#cal-select-diary-month")?.addEventListener("click", selectDiaryInMonth);
    $("#cal-clear-selection")?.addEventListener("click", () => {
      DateSelection.clear();
      updateSelectionBar();
      renderGrid();
    });
    $("#cal-new-chat")?.addEventListener("click", () => {
      newChatWithSelection().catch((e) => showError(e.message));
    });
    $("#btn-day-back")?.addEventListener("click", () => {
      showMonthView();
      renderGrid();
    });
    $("#btn-poetic-refresh")?.addEventListener("click", () => {
      if (!currentInsightDate) return;
      loadDayInsights(currentInsightDate, true, true);
    });
    DateSelection.onChange(() => {
      updateSelectionBar();
      renderGrid();
      MiniDatePicker.render();
    });
  }

  async function show() {
    bind();
    showMonthView();
    try {
      await refreshDates();
    } catch (err) {
      console.warn("日历加载失败", err);
      renderGrid();
    }
  }

  return {
    show,
    refreshDates,
    getDiaryDates: () => diaryDates,
    getView: () => ({ year: viewYear, month: viewMonth }),
  };
})();

/** 聊天页小型勾选月历 */
const MiniDatePicker = (() => {
  let viewYear = new Date().getFullYear();
  let viewMonth = new Date().getMonth();
  let bound = false;

  function $(sel) {
    return document.querySelector(sel);
  }

  function pad(n) {
    return String(n).padStart(2, "0");
  }

  function ymd(y, m, d) {
    return `${y}-${pad(m + 1)}-${pad(d)}`;
  }

  function syncMonth(y, m) {
    viewYear = y;
    viewMonth = m;
    render();
  }

  function render() {
    const root = $("#mini-cal-grid");
    const label = $("#mini-cal-label");
    const summary = $("#mini-cal-summary");
    if (!root) return;
    if (label) label.textContent = `${viewYear}年${viewMonth + 1}月`;

    const diary = CalendarPage.getDiaryDates
      ? CalendarPage.getDiaryDates()
      : new Set();
    const first = new Date(viewYear, viewMonth, 1);
    let startPad = first.getDay() - 1;
    if (startPad < 0) startPad = 6;
    const dim = new Date(viewYear, viewMonth + 1, 0).getDate();

    const frag = document.createDocumentFragment();
    const head = document.createElement("div");
    head.className = "mini-cal-weekdays";
    for (const w of ["一", "二", "三", "四", "五", "六", "日"]) {
      const el = document.createElement("span");
      el.textContent = w;
      head.appendChild(el);
    }
    frag.appendChild(head);

    const days = document.createElement("div");
    days.className = "mini-cal-days";
    for (let i = 0; i < startPad; i++) {
      const b = document.createElement("span");
      b.className = "mini-cal-day blank";
      days.appendChild(b);
    }
    for (let d = 1; d <= dim; d++) {
      const dateStr = ymd(viewYear, viewMonth, d);
      const has = diary.has(dateStr);
      const checked = DateSelection.has(dateStr);
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className =
        "mini-cal-day" +
        (has ? " has" : "") +
        (checked ? " checked" : "");
      btn.textContent = String(d);
      btn.title = dateStr;
      btn.addEventListener("click", () => {
        DateSelection.toggle(dateStr);
        if (typeof window.persistActiveDates === "function") {
          window.persistActiveDates();
        }
        render();
      });
      days.appendChild(btn);
    }
    frag.appendChild(days);
    root.innerHTML = "";
    root.appendChild(frag);

    if (summary) {
      const n = DateSelection.get().length;
      summary.textContent = n ? `召回：已选 ${n} 天` : "召回：不限日期";
    }
  }

  function bind() {
    if (bound) return;
    bound = true;
    $("#mini-cal-prev")?.addEventListener("click", () => {
      viewMonth -= 1;
      if (viewMonth < 0) {
        viewMonth = 11;
        viewYear -= 1;
      }
      render();
    });
    $("#mini-cal-next")?.addEventListener("click", () => {
      viewMonth += 1;
      if (viewMonth > 11) {
        viewMonth = 0;
        viewYear += 1;
      }
      render();
    });
    $("#mini-cal-clear")?.addEventListener("click", () => {
      DateSelection.clear();
      if (typeof window.persistActiveDates === "function") {
        window.persistActiveDates();
      }
      render();
    });
    $("#btn-date-picker-toggle")?.addEventListener("click", () => {
      const panel = $("#mini-cal-panel");
      if (!panel) return;
      panel.hidden = !panel.hidden;
      if (!panel.hidden) render();
    });
  }

  function init() {
    bind();
    render();
  }

  return { init, render, syncMonth };
})();
