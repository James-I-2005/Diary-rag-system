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
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "conv-item" + (c.id === activeId ? " active" : "");
    btn.dataset.id = c.id;
    btn.innerHTML = `
      <span class="conv-title">${escapeHtml(c.title)}</span>
      <span class="conv-meta">${formatTime(c.updated_at)}</span>
    `;
    btn.addEventListener("click", () => selectConversation(c.id));
    conversationList.appendChild(btn);
  }
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
  renderConversationList();
  messageInput.focus();
  closeSidebarMobile();
}

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
  currentView = name === "calendar" ? "calendar" : "chat";
  const chat = $("#view-chat");
  const cal = $("#view-calendar");
  if (chat) chat.hidden = currentView !== "chat";
  if (cal) cal.hidden = currentView !== "calendar";
  document.querySelectorAll(".nav-item").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.nav === currentView);
  });
  if (currentView === "calendar" && typeof CalendarPage !== "undefined") {
    CalendarPage.show();
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
  });

  $("#nav-chat")?.addEventListener("click", () => switchView("chat"));
  $("#nav-calendar")?.addEventListener("click", () => switchView("calendar"));

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
