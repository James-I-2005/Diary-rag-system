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

let conversations = [];
let activeId = null;
let isSending = false;
let userScrolledUp = false;
let currentScheme = localStorage.getItem("retrieval_scheme") || "weighted_50_50";

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

function closeSidebarMobile() {
  sidebar.classList.remove("open");
  sidebarBackdrop.hidden = true;
}

function openSidebarMobile() {
  sidebar.classList.add("open");
  sidebarBackdrop.hidden = false;
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
