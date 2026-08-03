/**
 * Memory Assistant — Web Chat UI
 */

const API = "/api";

const $ = (sel) => document.querySelector(sel);

const sidebar = $("#sidebar");
const sidebarBackdrop = $("#sidebar-backdrop");
const conversationList = $("#conversation-list");
const workspaceTitle = $("#workspace-title");
const messageArea = $("#message-area");
const messagesEl = $("#messages");
const emptyState = $("#empty-state");
const messageInput = $("#message-input");
const btnSend = $("#btn-send");
const btnNewChat = $("#btn-new-chat");
const btnMenu = $("#btn-menu");
const exampleQuestions = $("#example-questions");
const schemeSelect = $("#scheme-select");
const dateFromInput = null;
const dateToInput = null;
const btnDateClear = null;
const btnImport = $("#btn-import");
const importModal = $("#import-modal");
const importRoot = $("#import-root");
const importAgent = $("#import-agent");
const importVectors = $("#import-vectors");
const importStatus = $("#import-status");
const importCancel = $("#import-cancel");
const importSubmit = $("#import-submit");
const sidebarDb = $("#sidebar-db");

let conversations = [];
let activeId = null;
let isSending = false;
let userScrolledUp = false;
let currentScheme = localStorage.getItem("retrieval_scheme") || "embedding_only";
let isImporting = false;
let currentView = "chat";
let suggestedQuestionsToken = 0;

function getDateRangePayload() {
  const dates = typeof DateSelection !== "undefined" ? DateSelection.get() : [];
  if (dates.length) return { dates };
  return {};
}

function persistActiveDates() {
  if (activeId && typeof DateSelection !== "undefined") {
    DateSelection.saveForConversation(activeId);
  }
}

window.persistActiveDates = persistActiveDates;

async function createChatWithDates(dates) {
  if (typeof DateSelection !== "undefined") {
    DateSelection.set(dates || []);
  }
  const created = await api("/conversations", {
    method: "POST",
    body: JSON.stringify({
      title: dates?.length ? `日记·${dates.length}天` : "新对话",
    }),
  });
  await loadConversations();
  activeId = created.id;
  if (typeof DateSelection !== "undefined") {
    DateSelection.set(dates || []);
    DateSelection.saveForConversation(activeId);
  }
  workspaceTitle.textContent = created.title || "新对话";
  clearMessages();
  loadSuggestedQuestions();
  renderConversationList();
  switchView("chat");
  MiniDatePicker?.render?.();
  messageInput.focus();
  showError(`已新建对话，召回限定 ${dates.length} 天`);
}

window.createChatWithDates = createChatWithDates;

function formatTime(iso) {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    const now = new Date();
    const sameDay =
      d.getFullYear() === now.getFullYear() &&
      d.getMonth() === now.getMonth() &&
      d.getDate() === now.getDate();
    if (sameDay) {
      return d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
    }
    return d.toLocaleDateString("zh-CN", { month: "short", day: "numeric" });
  } catch {
    return "";
  }
}

function renderMarkdown(text) {
  if (typeof marked !== "undefined") {
    marked.setOptions({ breaks: true, gfm: true });
    return marked.parse(text || "");
  }
  return (text || "").replace(/</g, "&lt;").replace(/\n/g, "<br>");
}

function showError(msg) {
  const el = document.createElement("div");
  el.className = "error-toast";
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 4000);
}

async function api(path, options = {}) {
  const res = await fetch(`${API}${path}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  if (res.status === 204) return null;
  return res.json();
}

function closeConvMenus() {
  document.querySelectorAll(".conv-menu.open").forEach((el) => {
    el.classList.remove("open");
  });
}

function renderConversationList() {
  conversationList.innerHTML = "";
  if (!conversations.length) {
    const empty = document.createElement("p");
    empty.className = "conv-meta";
    empty.style.padding = "12px";
    empty.textContent = "暂无历史对话";
    conversationList.appendChild(empty);
    return;
  }

  for (const c of conversations) {
    const row = document.createElement("div");
    row.className = "conv-item" + (c.id === activeId ? " active" : "");
    row.dataset.id = c.id;

    const main = document.createElement("button");
    main.type = "button";
    main.className = "conv-main";
    main.innerHTML = `
      <span class="conv-title">${escapeHtml(c.title)}</span>
      <span class="conv-meta">${formatTime(c.updated_at)}</span>
    `;
    main.addEventListener("click", () => {
      closeConvMenus();
      selectConversation(c.id);
    });

    const moreWrap = document.createElement("div");
    moreWrap.className = "conv-more-wrap";

    const moreBtn = document.createElement("button");
    moreBtn.type = "button";
    moreBtn.className = "conv-more-btn";
    moreBtn.setAttribute("aria-label", "更多操作");
    moreBtn.textContent = "⋯";
    moreBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      const menu = moreWrap.querySelector(".conv-menu");
      const willOpen = !menu.classList.contains("open");
      closeConvMenus();
      if (willOpen) menu.classList.add("open");
    });

    const menu = document.createElement("div");
    menu.className = "conv-menu";
    menu.innerHTML = `
      <button type="button" data-act="rename">重命名</button>
      <button type="button" data-act="export">导出 Markdown</button>
      <button type="button" data-act="delete" class="danger">删除</button>
    `;
    menu.addEventListener("click", (e) => e.stopPropagation());
    menu.querySelector('[data-act="rename"]').addEventListener("click", () => {
      closeConvMenus();
      renameConversation(c.id, c.title).catch((err) => showError(err.message));
    });
    menu.querySelector('[data-act="export"]').addEventListener("click", () => {
      closeConvMenus();
      exportConversationMd(c.id, c.title).catch((err) => showError(err.message));
    });
    menu.querySelector('[data-act="delete"]').addEventListener("click", () => {
      closeConvMenus();
      deleteConversation(c.id, c.title).catch((err) => showError(err.message));
    });

    moreWrap.appendChild(moreBtn);
    moreWrap.appendChild(menu);
    row.appendChild(main);
    row.appendChild(moreWrap);
    conversationList.appendChild(row);
  }
}

async function renameConversation(id, currentTitle) {
  const next = prompt("重命名对话", currentTitle || "新对话");
  if (next === null) return;
  const title = next.trim();
  if (!title) {
    showError("标题不能为空");
    return;
  }
  const updated = await api(`/conversations/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ title }),
  });
  const conv = conversations.find((c) => c.id === id);
  if (conv) conv.title = updated.title;
  if (id === activeId) workspaceTitle.textContent = updated.title;
  renderConversationList();
}

async function deleteConversation(id, title) {
  const label = title || "此对话";
  if (!confirm(`确定删除「${label}」？此操作不可恢复。`)) return;
  await api(`/conversations/${id}`, { method: "DELETE" });
  conversations = conversations.filter((c) => c.id !== id);
  if (activeId === id) {
    activeId = null;
    clearMessages();
    workspaceTitle.textContent = "Memory Assistant";
    if (conversations.length) {
      await selectConversation(conversations[0].id);
    } else {
      loadSuggestedQuestions();
      renderConversationList();
    }
  } else {
    renderConversationList();
  }
}

async function exportConversationMd(id, title) {
  const response = await fetch(`/api/conversations/${id}/export.md`);
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  const blob = await response.blob();
  const disposition = response.headers.get("Content-Disposition") || "";
  let filename = `${(title || "chat").slice(0, 40)}_${id.slice(0, 8)}.md`;
  const star = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (star?.[1]) {
    try {
      filename = decodeURIComponent(star[1]);
    } catch {
      /* keep fallback */
    }
  }
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

function appendMessage(role, content, { scroll = true } = {}) {
  emptyState.classList.add("hidden");

  const wrap = document.createElement("div");
  wrap.className = `message ${role}`;

  const inner = document.createElement("div");
  inner.className = "message-inner";

  const roleLabel = document.createElement("div");
  roleLabel.className = "message-role";
  roleLabel.textContent = role === "user" ? "你" : "助手";

  const body = document.createElement("div");
  body.className = "message-content";
  body.innerHTML = renderMarkdown(content);
  if (typeof TagMention !== "undefined") {
    TagMention.decorateElement(body);
  }

  inner.appendChild(roleLabel);
  inner.appendChild(body);
  wrap.appendChild(inner);
  messagesEl.appendChild(wrap);

  if (scroll && !userScrolledUp) {
    scrollToBottom();
  }
  return wrap;
}

function showTyping() {
  emptyState.classList.add("hidden");
  const wrap = document.createElement("div");
  wrap.className = "message assistant";
  wrap.id = "typing-indicator";
  wrap.innerHTML = `
    <div class="message-inner">
      <div class="message-role">助手</div>
      <div class="typing-indicator"><span></span><span></span><span></span></div>
    </div>
  `;
  messagesEl.appendChild(wrap);
  scrollToBottom(true);
}

function hideTyping() {
  const el = $("#typing-indicator");
  if (el) el.remove();
}

function clearMessages() {
  messagesEl.innerHTML = "";
  emptyState.classList.remove("hidden");
}

function renderSuggestedQuestionsLoading() {
  if (!exampleQuestions) return;
  exampleQuestions.innerHTML =
    `<li class="example-questions-hint">正在生成推荐问题…</li>`;
}

function renderSuggestedQuestions(questions) {
  if (!exampleQuestions) return;
  const list = Array.isArray(questions)
    ? questions.map((q) => String(q || "").trim()).filter(Boolean)
    : [];
  if (!list.length) {
    exampleQuestions.innerHTML =
      `<li class="example-questions-hint">暂无推荐问题（该时间段内可能还没有日记）</li>`;
    return;
  }
  exampleQuestions.innerHTML = list
    .map((q) => {
      const escaped = q
        .replace(/&/g, "&amp;")
        .replace(/"/g, "&quot;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
      return `<li><button type="button" data-q="${escaped}">${escaped}</button></li>`;
    })
    .join("");
}

async function loadSuggestedQuestions() {
  const token = ++suggestedQuestionsToken;
  renderSuggestedQuestionsLoading();
  try {
    const data = await api("/suggested-questions");
    if (token !== suggestedQuestionsToken) return;
    renderSuggestedQuestions(data?.questions || []);
  } catch (err) {
    if (token !== suggestedQuestionsToken) return;
    console.warn("加载推荐问题失败", err);
    exampleQuestions.innerHTML =
      `<li class="example-questions-hint">推荐问题暂时不可用</li>`;
  }
}

function scrollToBottom(force = false) {
  if (!force && userScrolledUp) return;
  requestAnimationFrame(() => {
    messageArea.scrollTop = messageArea.scrollHeight;
  });
}

function isNearBottom() {
  const threshold = 80;
  return messageArea.scrollHeight - messageArea.scrollTop - messageArea.clientHeight < threshold;
}

messageArea.addEventListener("scroll", () => {
  userScrolledUp = !isNearBottom();
});

function resizeInput() {
  messageInput.style.height = "auto";
  const h = Math.min(messageInput.scrollHeight, 200);
  messageInput.style.height = `${h}px`;
}

function updateSendButton() {
  btnSend.disabled = isSending || !messageInput.value.trim();
}

async function loadConversations() {
  conversations = await api("/conversations");
  renderConversationList();
}

async function selectConversation(id) {
  if (id === activeId && messagesEl.children.length) {
    closeSidebarMobile();
    return;
  }

  activeId = id;
  userScrolledUp = false;
  if (typeof DateSelection !== "undefined") {
    DateSelection.loadForConversation(id);
    MiniDatePicker?.render?.();
  }
  renderConversationList();

  const data = await api(`/conversations/${id}`);
  workspaceTitle.textContent = data.title || "Memory Assistant";

  messagesEl.innerHTML = "";
  if (!data.messages || !data.messages.length) {
    emptyState.classList.remove("hidden");
    loadSuggestedQuestions();
  } else {
    emptyState.classList.add("hidden");
    for (const m of data.messages) {
      if (m.role === "user" || m.role === "assistant") {
        appendMessage(m.role, m.content, { scroll: false });
      }
    }
    scrollToBottom(true);
  }

  closeSidebarMobile();
}

async function createNewChat() {
  const created = await api("/conversations", {
    method: "POST",
    body: JSON.stringify({ title: "新对话" }),
  });
  await loadConversations();
  activeId = created.id;
  if (typeof DateSelection !== "undefined") {
    DateSelection.clear();
    DateSelection.saveForConversation(activeId);
    MiniDatePicker?.render?.();
  }
  workspaceTitle.textContent = "新对话";
  clearMessages();
  loadSuggestedQuestions();
  renderConversationList();
  messageInput.focus();
  closeSidebarMobile();
}

/**
 * 从 Tag 详情「进入故事」：新建对话并在输入框预填 @tag名。
 * @param {{ tagId: string, tagName: string, color?: string }} opts
 */
async function createChatWithTagStory(opts = {}) {
  const tagName = String(opts.tagName || "").trim();
  const tagId = String(opts.tagId || "").trim();
  if (!tagName) {
    showError("缺少 tag 名称");
    return;
  }
  if (typeof TagMention !== "undefined") {
    TagMention.register({
      id: tagId,
      name: tagName,
      color: opts.color || "#6b7280",
    });
  }
  switchView("chat");
  const title = `和「${tagName}」的故事`;
  const created = await api("/conversations", {
    method: "POST",
    body: JSON.stringify({ title }),
  });
  await loadConversations();
  activeId = created.id;
  if (typeof DateSelection !== "undefined") {
    DateSelection.clear();
    DateSelection.saveForConversation(activeId);
    MiniDatePicker?.render?.();
  }
  workspaceTitle.textContent = title;
  clearMessages();
  loadSuggestedQuestions();
  renderConversationList();
  messageInput.value = `@${tagName} `;
  resizeInput();
  updateSendButton();
  messageInput.focus();
  closeSidebarMobile();
}

window.createChatWithTagStory = createChatWithTagStory;

async function sendMessage() {
  const text = messageInput.value.trim();
  if (!text || isSending) return;

  if (!activeId) {
    await createNewChat();
  }

  isSending = true;
  updateSendButton();
  userScrolledUp = false;

  messageInput.value = "";
  resizeInput();

  appendMessage("user", text);
  showTyping();

  try {
    const result = await api(`/conversations/${activeId}/messages`, {
      method: "POST",
      body: JSON.stringify({
        message: text,
        use_vector: true,
        scheme: currentScheme,
        ...getDateRangePayload(),
      }),
    });

    hideTyping();
    appendMessage("assistant", result.answer);

    await loadConversations();
    const conv = conversations.find((c) => c.id === activeId);
    if (conv) {
      workspaceTitle.textContent = conv.title;
    }
    renderConversationList();
  } catch (err) {
    hideTyping();
    appendMessage("assistant", `抱歉，请求失败：${err.message}`);
    showError(err.message);
  } finally {
    isSending = false;
    updateSendButton();
    messageInput.focus();
  }
}

function switchView(name) {
  const allowed = new Set(["chat", "calendar", "write", "explore"]);
  const next = allowed.has(name) ? name : "chat";
  if (currentView === "write" && next !== "write" && typeof WritePage !== "undefined") {
    WritePage.flush?.();
  }
  currentView = next;
  const chat = $("#view-chat");
  const cal = $("#view-calendar");
  const write = $("#view-write");
  const explore = $("#view-explore");
  if (chat) chat.hidden = currentView !== "chat";
  if (cal) cal.hidden = currentView !== "calendar";
  if (write) write.hidden = currentView !== "write";
  if (explore) explore.hidden = currentView !== "explore";
  document.querySelectorAll(".nav-item").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.nav === currentView);
  });
  if (currentView === "calendar" && typeof CalendarPage !== "undefined") {
    CalendarPage.show();
  }
  if (currentView === "write" && typeof WritePage !== "undefined") {
    Promise.resolve(WritePage.show()).catch((e) => console.warn(e));
  }
  if (currentView === "explore" && typeof ExplorePage !== "undefined") {
    ExplorePage.show();
  }
  if (currentView === "chat") {
    closeSidebarMobile();
  }
}

function closeSidebarMobile() {
  sidebar.classList.remove("open");
  sidebarBackdrop.hidden = true;
}

function openSidebarMobile() {
  sidebar.classList.add("open");
  sidebarBackdrop.hidden = false;
}

async function loadLibraryStatus() {
  try {
    const data = await api("/library");
    const db = data.db || {};
    const last = data.last;
    let text = `库：${db.chunks ?? 0} chunks / ${db.sentences ?? 0} sentences`;
    if (last?.root) {
      const name = String(last.root).split("/").filter(Boolean).pop() || last.root;
      text += ` · 最近：${name}`;
    }
    if (sidebarDb) sidebarDb.textContent = text;
    if (last?.root && importRoot && !importRoot.value) {
      importRoot.value = last.root;
    } else if (!importRoot?.value) {
      const saved = localStorage.getItem("import_root");
      if (saved && importRoot) importRoot.value = saved;
    }
  } catch (err) {
    console.warn("加载库状态失败", err);
  }
}

function openImportModal() {
  if (!importModal) return;
  importModal.hidden = false;
  if (importStatus) {
    importStatus.hidden = true;
    importStatus.textContent = "";
  }
  const saved = localStorage.getItem("import_root");
  if (saved && importRoot && !importRoot.value) importRoot.value = saved;
  importRoot?.focus();
  importRoot?.select();
}

function closeImportModal() {
  if (isImporting) return;
  if (importModal) importModal.hidden = true;
}

async function submitImport() {
  if (isImporting) return;
  const root = (importRoot?.value || "").trim();
  if (!root) {
    showError("请填写本机日记根目录");
    importRoot?.focus();
    return;
  }

  isImporting = true;
  if (importSubmit) importSubmit.disabled = true;
  if (importCancel) importCancel.disabled = true;
  if (btnImport) btnImport.disabled = true;
  if (importStatus) {
    importStatus.hidden = false;
    importStatus.textContent =
      "导入进行中…\nextract → ingest" +
      (importVectors?.checked ? " → sentences/index" : "") +
      "\n请勿关闭页面。";
  }

  try {
    const result = await api("/library/import", {
      method: "POST",
      body: JSON.stringify({
        root,
        use_agent: !!importAgent?.checked,
        build_vectors: importVectors ? !!importVectors.checked : true,
      }),
    });
    localStorage.setItem("import_root", root);
    const phases = result.phases || {};
    const lines = [
      "导入完成",
      `根目录：${result.root}`,
      `extract：${phases.extract?.entries_total ?? "?"} entries / ${phases.extract?.files_total ?? "?"} files`,
      `ingest：${phases.ingest?.chunks ?? "?"} chunks`,
    ];
    if (phases.sentences) {
      lines.push(
        `sentences：ok=${phases.sentences.ok ?? 0} fail=${phases.sentences.fail ?? 0} chroma=${phases.sentences.chroma ?? "?"}`
      );
    }
    if (importStatus) importStatus.textContent = lines.join("\n");
    await loadLibraryStatus();
    if (typeof CalendarPage !== "undefined") {
      try {
        await CalendarPage.refreshDates();
      } catch {
        /* ignore */
      }
    }
  } catch (err) {
    if (importStatus) {
      importStatus.hidden = false;
      importStatus.textContent = `导入失败：${err.message}`;
    }
    showError(err.message);
  } finally {
    isImporting = false;
    if (importSubmit) importSubmit.disabled = false;
    if (importCancel) importCancel.disabled = false;
    if (btnImport) btnImport.disabled = false;
  }
}

async function loadSchemes() {
  try {
    const data = await api("/retrieval/schemes");
    const schemes = data.schemes || [];
    const saved = localStorage.getItem("retrieval_scheme");
    currentScheme =
      saved && schemes.some((s) => s.id === saved)
        ? saved
        : data.default || schemes[0]?.id || "weighted_50_50";

    schemeSelect.innerHTML = "";
    for (const s of schemes) {
      const opt = document.createElement("option");
      opt.value = s.id;
      opt.textContent = s.label || s.id;
      if (s.description) opt.title = s.description;
      if (s.id === currentScheme) opt.selected = true;
      schemeSelect.appendChild(opt);
    }
    localStorage.setItem("retrieval_scheme", currentScheme);
  } catch (err) {
    console.warn("加载检索方案失败", err);
  }
}

async function init() {
  messageInput.addEventListener("input", () => {
    resizeInput();
    updateSendButton();
  });

  messageInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });

  btnSend.addEventListener("click", sendMessage);
  btnNewChat.addEventListener("click", createNewChat);
  btnMenu.addEventListener("click", openSidebarMobile);
  sidebarBackdrop.addEventListener("click", closeSidebarMobile);

  schemeSelect.addEventListener("change", () => {
    currentScheme = schemeSelect.value;
    localStorage.setItem("retrieval_scheme", currentScheme);
  });

  btnDateClear?.addEventListener("click", () => {});

  btnImport?.addEventListener("click", openImportModal);
  importCancel?.addEventListener("click", closeImportModal);
  importSubmit?.addEventListener("click", submitImport);
  importModal?.addEventListener("click", (e) => {
    if (e.target === importModal) closeImportModal();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && importModal && !importModal.hidden) {
      closeImportModal();
    }
    if (e.key === "Escape") closeConvMenus();
  });
  document.addEventListener("click", () => closeConvMenus());

  $("#nav-chat")?.addEventListener("click", () => switchView("chat"));
  $("#nav-calendar")?.addEventListener("click", () => switchView("calendar"));
  $("#nav-write")?.addEventListener("click", () => switchView("write"));
  $("#nav-explore")?.addEventListener("click", () => switchView("explore"));

  if (typeof SelectionTag !== "undefined") {
    SelectionTag.bind();
  }

  if (typeof MiniDatePicker !== "undefined") {
    MiniDatePicker.init();
  }

  exampleQuestions.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-q]");
    if (!btn) return;
    messageInput.value = btn.dataset.q;
    resizeInput();
    updateSendButton();
    messageInput.focus();
  });

  try {
    await api("/health");
    await loadSchemes();
    await loadLibraryStatus();
    // 预热日记日期给小型日历着色
    try {
      if (typeof CalendarPage !== "undefined") {
        await CalendarPage.refreshDates();
      }
    } catch {
      /* ignore */
    }
    if (typeof TagMention !== "undefined") {
      await TagMention.refresh();
    }
    await loadConversations();

    if (conversations.length) {
      await selectConversation(conversations[0].id);
    } else {
      await createNewChat();
    }
  } catch (err) {
    showError(`无法连接后端：${err.message}`);
    workspaceTitle.textContent = "连接失败";
  }
}

init();
