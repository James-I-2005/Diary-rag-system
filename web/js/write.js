/**
 * 写日记页：文稿模式 + 聊天模式。
 * 浏览器 localStorage 作缓存；服务端 data/write_diary 为权威存储。
 * 跨日时服务端自动归档昨日并建 chunk，日历可见。
 */

const WritePage = (() => {
  const STORAGE_KEY = "myrag_manuscripts_v1";
  const MODE_PAPERS = "papers";
  const MODE_CHAT = "chat";

  let state = {
    mode: MODE_PAPERS,
    items: [],
    active_day: "",
  };
  let activeId = null;
  let saveTimer = null;
  let syncTimer = null;
  let bound = false;
  let syncing = false;

  function $(sel) {
    return document.querySelector(sel);
  }

  function uid() {
    if (typeof crypto !== "undefined" && crypto.randomUUID) {
      return crypto.randomUUID();
    }
    return `ms_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`;
  }

  function blankManuscript() {
    const now = new Date().toISOString();
    return {
      id: uid(),
      title: "",
      content: "",
      createdAt: now,
      updatedAt: now,
    };
  }

  function applyServerPayload(data) {
    if (!data || typeof data !== "object") return false;
    const items = Array.isArray(data.items) ? data.items : [];
    state = {
      mode: data.mode === MODE_CHAT ? MODE_CHAT : MODE_PAPERS,
      items: items.length ? items : [blankManuscript()],
      active_day: data.active_day || "",
    };
    mirrorLocal();
    return true;
  }

  function mirrorLocal() {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        mode: state.mode,
        items: state.items,
        active_day: state.active_day,
      })
    );
  }

  function loadLocal() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) {
        state = { mode: MODE_PAPERS, items: [blankManuscript()], active_day: "" };
        mirrorLocal();
        return;
      }
      const data = JSON.parse(raw);
      const items = Array.isArray(data.items) ? data.items : [];
      state = {
        mode: data.mode === MODE_CHAT ? MODE_CHAT : MODE_PAPERS,
        items: items.length ? items : [blankManuscript()],
        active_day: data.active_day || "",
      };
    } catch {
      state = { mode: MODE_PAPERS, items: [blankManuscript()], active_day: "" };
    }
  }

  function persist() {
    mirrorLocal();
    scheduleServerSync();
  }

  function scheduleServerSync() {
    clearTimeout(syncTimer);
    syncTimer = setTimeout(() => {
      syncToServer().catch((err) => {
        console.warn("写日记同步失败", err);
        setSaveHint("本机已存，同步服务器失败");
      });
    }, 450);
  }

  async function syncToServer() {
    if (typeof api !== "function") return null;
    if (syncing) {
      scheduleServerSync();
      return null;
    }
    syncing = true;
    try {
      const data = await api("/write/manuscripts", {
        method: "POST",
        body: JSON.stringify({
          mode: state.mode,
          items: state.items,
        }),
      });
      if (data?.rollover?.rolled) {
        applyServerPayload(data);
        activeId = null;
        setSaveHint("新的一天：昨日日记已归档入库");
        if (typeof CalendarPage !== "undefined" && CalendarPage.refreshDates) {
          CalendarPage.refreshDates().catch(() => {});
        }
        if (state.mode === MODE_CHAT) showChatMode();
        else showGallery();
      } else if (data?.active_day) {
        state.active_day = data.active_day;
        mirrorLocal();
      }
      return data;
    } finally {
      syncing = false;
    }
  }

  async function loadFromServer() {
    if (typeof api !== "function") {
      loadLocal();
      return;
    }
    try {
      const data = await api("/write/manuscripts");
      applyServerPayload(data);
      if (data?.rollover?.rolled) {
        setSaveHint("新的一天：昨日日记已归档入库");
        if (typeof CalendarPage !== "undefined" && CalendarPage.refreshDates) {
          CalendarPage.refreshDates().catch(() => {});
        }
      }
    } catch (err) {
      console.warn("读取服务端文稿失败，改用本机缓存", err);
      loadLocal();
      scheduleServerSync();
    }
  }

  function previewText(content, max = 120) {
    const t = String(content || "")
      .replace(/\r\n/g, "\n")
      .trim();
    if (!t) return "空白文稿";
    const firstPage = t.split(/\n{2,}/)[0] || t;
    const flat = firstPage.replace(/\n/g, " ").trim();
    return flat.length > max ? flat.slice(0, max) + "…" : flat;
  }

  function displayTitle(item) {
    const t = (item.title || "").trim();
    return t || "无标题";
  }

  function contactTitle(item, index = state.items.indexOf(item)) {
    const title = (item?.title || "").trim();
    return title || `新建文稿${Math.max(0, index) + 1}`;
  }

  function findItem(id) {
    return state.items.find((x) => x.id === id);
  }

  function renderGallery() {
    const gallery = $("#manuscript-gallery");
    if (!gallery) return;
    gallery.innerHTML = "";

    state.items.forEach((item, index) => {
      const card = document.createElement("button");
      card.type = "button";
      card.className = "manuscript-card";
      card.dataset.id = item.id;
      card.style.setProperty("--tilt", `${((index % 5) - 2) * 1.2}deg`);
      card.innerHTML = `
        <div class="manuscript-sheet" aria-hidden="true">
          <p class="manuscript-preview">${escapeHtml(previewText(item.content))}</p>
        </div>
        <h2 class="manuscript-title">${escapeHtml(displayTitle(item))}</h2>
      `;
      card.addEventListener("click", () => openEditor(item.id));
      gallery.appendChild(card);
    });
  }

  function escapeHtml(s) {
    const d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  function showGallery() {
    activeId = null;
    const galleryWrap = $("#write-mode-papers");
    const editor = $("#write-editor");
    const chat = $("#write-mode-chat");
    const modeButton = $("#btn-write-mode");
    if (galleryWrap) galleryWrap.hidden = false;
    if (editor) editor.hidden = true;
    if (chat) chat.hidden = true;
    if (modeButton) modeButton.hidden = false;
    renderGallery();
    updateModeButton();
  }

  function openEditor(id) {
    const item = findItem(id);
    if (!item) return;
    activeId = id;
    const galleryWrap = $("#write-mode-papers");
    const editor = $("#write-editor");
    const chat = $("#write-mode-chat");
    const modeButton = $("#btn-write-mode");
    if (galleryWrap) galleryWrap.hidden = true;
    if (editor) editor.hidden = false;
    if (chat) chat.hidden = true;
    if (modeButton) modeButton.hidden = true;

    const titleInput = $("#write-title-input");
    const content = $("#write-content");
    if (titleInput) titleInput.value = item.title || "";
    if (content) content.value = item.content || "";
    setSaveHint("已打开");
    queueMicrotask(() => content?.focus());
  }

  function setSaveHint(text) {
    const el = $("#write-save-hint");
    if (el) el.textContent = text;
  }

  function scheduleSave() {
    if (!activeId) return;
    setSaveHint("保存中…");
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      commitEditor();
      setSaveHint("已保存（本机 + 服务器）");
    }, 280);
  }

  function commitEditor() {
    const item = findItem(activeId);
    if (!item) return;
    const titleInput = $("#write-title-input");
    const content = $("#write-content");
    item.title = titleInput ? titleInput.value : item.title;
    item.content = content ? content.value : item.content;
    item.updatedAt = new Date().toISOString();
    persist();
  }

  function createManuscript() {
    const item = blankManuscript();
    state.items.unshift(item);
    persist();
    if (state.mode === MODE_CHAT) {
      showChatMode(item.id);
    } else {
      renderGallery();
      openEditor(item.id);
    }
  }

  function deleteActive() {
    if (!activeId) return;
    if (state.items.length <= 1) {
      const only = state.items[0];
      only.title = "";
      only.content = "";
      only.updatedAt = new Date().toISOString();
      persist();
      openEditor(only.id);
      setSaveHint("已清空（至少保留一张文稿）");
      return;
    }
    if (!confirm("确定删除这张文稿？")) return;
    state.items = state.items.filter((x) => x.id !== activeId);
    persist();
    showGallery();
  }

  function updateModeButton() {
    const btn = $("#btn-write-mode");
    if (!btn) return;
    if (state.mode === MODE_PAPERS) {
      btn.innerHTML = `文稿模式 <span class="mode-hint">切换</span>`;
    } else {
      btn.innerHTML = `聊天模式 <span class="mode-hint">切换</span>`;
    }
  }

  function toggleMode() {
    if (state.mode === MODE_PAPERS) {
      if (activeId) commitEditor();
      state.mode = MODE_CHAT;
      persist();
      showChatMode(activeId || state.items[0]?.id);
      return;
    }
    state.mode = MODE_PAPERS;
    persist();
    showGallery();
  }

  function contentLines(content) {
    return String(content || "")
      .replace(/\r\n?/g, "\n")
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean);
  }

  function renderContacts() {
    const list = $("#write-chat-contact-list");
    if (!list) return;
    list.innerHTML = "";
    state.items.forEach((item, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className =
        "write-chat-contact" + (item.id === activeId ? " active" : "");
      button.dataset.id = item.id;
      const lines = contentLines(item.content);
      button.innerHTML = `
        <span class="write-contact-avatar">${escapeHtml(contactTitle(item, index).slice(0, 1))}</span>
        <span class="write-contact-copy">
          <strong>${escapeHtml(contactTitle(item, index))}</strong>
          <small>${escapeHtml(lines.at(-1) || "还没有记录")}</small>
        </span>
      `;
      button.addEventListener("click", () => selectChat(item.id));
      list.appendChild(button);
    });
  }

  function renderChatMessages(item) {
    const messages = $("#write-chat-messages");
    if (!messages) return;
    messages.innerHTML = "";
    const lines = contentLines(item?.content);
    if (!lines.length) {
      messages.innerHTML = `<p class="write-chat-empty">还没有内容，从第一句话开始吧。</p>`;
      return;
    }
    lines.forEach((line) => {
      const row = document.createElement("div");
      row.className = "write-chat-message-row";
      const bubble = document.createElement("p");
      bubble.className = "write-chat-bubble";
      bubble.textContent = line;
      row.appendChild(bubble);
      messages.appendChild(row);
    });
    requestAnimationFrame(() => {
      messages.scrollTop = messages.scrollHeight;
    });
  }

  function selectChat(id) {
    const item = findItem(id);
    if (!item) return;
    activeId = id;
    const title = $("#write-chat-title");
    if (title) title.textContent = contactTitle(item);
    renderContacts();
    renderChatMessages(item);
    $("#write-chat-input")?.focus();
  }

  function showChatMode(preferredId = null) {
    const galleryWrap = $("#write-mode-papers");
    const editor = $("#write-editor");
    const chat = $("#write-mode-chat");
    const modeButton = $("#btn-write-mode");
    if (galleryWrap) galleryWrap.hidden = true;
    if (editor) editor.hidden = true;
    if (chat) chat.hidden = false;
    if (modeButton) modeButton.hidden = false;
    updateModeButton();

    const id =
      (preferredId && findItem(preferredId) && preferredId) ||
      (activeId && findItem(activeId) && activeId) ||
      state.items[0]?.id;
    if (id) selectChat(id);
  }

  function sendChatMessage() {
    const input = $("#write-chat-input");
    const item = findItem(activeId);
    if (!input || !item) return;
    const lines = contentLines(input.value);
    if (!lines.length) return;
    const addition = lines.join("\n");
    const previous = String(item.content || "").replace(/\s+$/, "");
    item.content = previous ? `${previous}\n${addition}` : addition;
    item.updatedAt = new Date().toISOString();
    input.value = "";
    persist();
    renderContacts();
    renderChatMessages(item);
    input.focus();
  }

  function bind() {
    if (bound) return;
    bound = true;
    $("#btn-new-manuscript")?.addEventListener("click", createManuscript);
    $("#btn-write-back")?.addEventListener("click", () => {
      commitEditor();
      showGallery();
    });
    $("#btn-write-delete")?.addEventListener("click", deleteActive);
    $("#btn-write-mode")?.addEventListener("click", toggleMode);
    $("#write-title-input")?.addEventListener("input", scheduleSave);
    $("#write-content")?.addEventListener("input", scheduleSave);
    $("#btn-chat-new-manuscript")?.addEventListener("click", createManuscript);
    $("#btn-write-chat-send")?.addEventListener("click", sendChatMessage);
    $("#write-chat-input")?.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        sendChatMessage();
      }
    });
  }

  async function show() {
    bind();
    await loadFromServer();
    if (state.mode === MODE_CHAT) {
      showChatMode(activeId);
    } else if (activeId && findItem(activeId)) {
      openEditor(activeId);
    } else {
      showGallery();
    }
  }

  function flush() {
    if (state.mode === MODE_PAPERS && activeId && !$("#write-editor")?.hidden) {
      commitEditor();
    } else {
      mirrorLocal();
      scheduleServerSync();
    }
  }

  return { show, flush, MODE_PAPERS, MODE_CHAT };
})();
