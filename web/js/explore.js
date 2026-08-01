/**
 * 探索页：搜索 / 人物 / 地点 / 其他 tag（用户手动 Tag 管理）。
 */

const ExplorePage = (() => {
  const PANELS = ["search", "people", "places", "tags"];
  let active = "search";
  let bound = false;
  let loaded = { people: false, places: false, tags: false };
  let currentFolderId = null;
  let pendingChunkIds = [];
  let searchHitIds = [];
  let selectedSearchIds = new Set();
  let dayModalBound = false;
  let dayModalFocusChunk = "";
  let nameGrepBound = false;
  let nameGrepOpen = false;
  let nameGrepHitIds = [];
  let selectedNameGrepIds = new Set();
  let nameGrepTagId = "";
  let nameGrepTagName = "";
  let tagDetailBound = false;
  let tagDetailOpen = false;

  function $(sel) {
    return document.querySelector(sel);
  }

  function escapeHtml(s) {
    const d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  function setTab(name) {
    if (!PANELS.includes(name)) name = "search";
    active = name;
    document.querySelectorAll(".explore-tab").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.explore === name);
    });
    document.querySelectorAll(".explore-panel").forEach((panel) => {
      panel.hidden = panel.dataset.panel !== name;
    });
    if (name === "people") loadPeople();
    if (name === "places" && !loaded.places) loadPlaces();
    if (name === "tags") loadTags(true);
  }

  function highlightPreview(preview, needle) {
    const text = preview || "";
    const key = (needle || "").trim();
    if (!key) return escapeHtml(text);
    const idx = text.indexOf(key);
    if (idx < 0) return escapeHtml(text);
    return (
      escapeHtml(text.slice(0, idx)) +
      `<mark>${escapeHtml(text.slice(idx, idx + key.length))}</mark>` +
      escapeHtml(text.slice(idx + key.length))
    );
  }

  function updateSearchSelectUi() {
    const toolbar = $("#explore-search-toolbar");
    const all = $("#explore-search-select-all");
    const label = $("#explore-search-selected");
    const addBtn = $("#explore-search-add-tag");
    const n = selectedSearchIds.size;
    const total = searchHitIds.length;
    if (toolbar) toolbar.hidden = total === 0;
    if (label) label.textContent = `已选 ${n}`;
    if (addBtn) addBtn.disabled = n === 0;
    if (all) {
      all.checked = total > 0 && n === total;
      all.indeterminate = n > 0 && n < total;
    }
    document.querySelectorAll(".explore-hit[data-chunk-id]").forEach((el) => {
      const id = el.getAttribute("data-chunk-id");
      const on = selectedSearchIds.has(id);
      el.classList.toggle("is-selected", on);
      const cb = el.querySelector(".explore-hit-check");
      if (cb) cb.checked = on;
    });
  }

  function clearSearchSelection() {
    selectedSearchIds.clear();
    updateSearchSelectUi();
  }

  function toggleSearchHit(chunkId, force) {
    if (!chunkId) return;
    const on = force == null ? !selectedSearchIds.has(chunkId) : !!force;
    if (on) selectedSearchIds.add(chunkId);
    else selectedSearchIds.delete(chunkId);
    updateSearchSelectUi();
  }

  function selectAllSearchHits(on) {
    selectedSearchIds.clear();
    if (on) searchHitIds.forEach((id) => selectedSearchIds.add(id));
    updateSearchSelectUi();
  }

  async function addSelectedToTag() {
    const ids = [...selectedSearchIds];
    if (!ids.length) return;
    if (typeof SelectionTag === "undefined" || !SelectionTag.openForChunks) {
      showError?.("Tag 选择组件未就绪");
      return;
    }
    await SelectionTag.openForChunks(ids);
  }

  function closeExploreDay() {
    const backdrop = $("#explore-day-backdrop");
    if (backdrop) backdrop.hidden = true;
    dayModalFocusChunk = "";
  }

  function bindDayModal() {
    if (dayModalBound) return;
    dayModalBound = true;
    $("#explore-day-close")?.addEventListener("click", closeExploreDay);
    $("#explore-day-backdrop")?.addEventListener("click", (e) => {
      if (e.target && e.target.id === "explore-day-backdrop") closeExploreDay();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      if (nameGrepOpen || tagPickerOpen) return;
      if (document.querySelector(".tag-picker-backdrop")) return;
      const backdrop = $("#explore-day-backdrop");
      if (backdrop && !backdrop.hidden) closeExploreDay();
    });
  }

  async function openExploreDay(dateStr, focusChunkId) {
    if (!dateStr) return;
    bindDayModal();
    dayModalFocusChunk = focusChunkId || "";
    const backdrop = $("#explore-day-backdrop");
    const title = $("#explore-day-title");
    const meta = $("#explore-day-meta");
    const body = $("#explore-day-body");
    if (!backdrop || !body) return;
    backdrop.hidden = false;
    if (title) title.textContent = dateStr;
    if (meta) meta.textContent = "加载中…";
    body.innerHTML = `<p class="explore-empty">加载中…</p>`;

    try {
      const data = await api(`/diary/days/${encodeURIComponent(dateStr)}`);
      const n = data.chunk_count || 0;
      const hasText = !!(n && (data.text || "").trim());
      if (!hasText) {
        if (meta) meta.textContent = "未录入";
        body.innerHTML = `<p class="explore-empty">这一天还没有日记。</p>`;
        return;
      }
      if (meta) meta.textContent = `${n} 个片段 · 可选中文字添加到 tag`;
      const wrap = document.createElement("div");
      wrap.className = "day-text explore-day-text";
      wrap.setAttribute("data-taggable", "1");
      const chunks = Array.isArray(data.chunks) ? data.chunks : [];
      if (chunks.length) {
        chunks.forEach((c, i) => {
          const span = document.createElement("span");
          span.className = "day-chunk";
          span.setAttribute("data-chunk-id", c.id);
          if (dayModalFocusChunk && c.id === dayModalFocusChunk) {
            span.classList.add("is-search-focus");
          }
          const raw = (c.text || "")
            .replace(/^\s+/, i === 0 ? "" : "")
            .replace(/\s+$/, "");
          span.textContent = i === 0 ? raw : "\n\n" + raw;
          wrap.appendChild(span);
        });
      } else {
        wrap.textContent = data.text || "";
      }
      body.innerHTML = "";
      body.appendChild(wrap);
      const focus = body.querySelector(".day-chunk.is-search-focus");
      if (focus) {
        focus.scrollIntoView({ block: "center", behavior: "smooth" });
      }
    } catch (err) {
      if (meta) meta.textContent = "加载失败";
      body.innerHTML = `<p class="explore-empty">${escapeHtml(err.message)}</p>`;
    }
  }

  function bindSearchHitCard(article) {
    const chunkId = article.getAttribute("data-chunk-id") || "";
    const dateStr = article.getAttribute("data-date") || "";
    const check = article.querySelector(".explore-hit-check");
    check?.addEventListener("click", (e) => e.stopPropagation());
    check?.addEventListener("change", () => {
      toggleSearchHit(chunkId, check.checked);
    });
    article.addEventListener("click", (e) => {
      if (e.target.closest(".explore-hit-check-wrap")) return;
      const sel = window.getSelection();
      if (sel && !sel.isCollapsed && article.contains(sel.anchorNode)) return;
      openExploreDay(dateStr, chunkId);
    });
  }

  function renderSearchSection(title, items, badge) {
    if (!items.length) return "";
    const cards = items
      .map((it) => {
        const chunkId = escapeHtml(it.chunk_id || "");
        const date = escapeHtml(it.date || "");
        return `
      <article class="explore-hit" data-taggable="1" data-chunk-id="${chunkId}" data-date="${date}">
        <label class="explore-hit-check-wrap" title="选择此结果">
          <input type="checkbox" class="explore-hit-check" aria-label="选择此结果" />
        </label>
        <div class="explore-hit-main">
          <header>
            <strong>${date}</strong>
            <span class="explore-hit-badge">${escapeHtml(badge)}</span>
          </header>
          <p>${highlightPreview(it.preview || "", it.matched || "")}</p>
          <footer class="explore-hit-meta">${escapeHtml(it.source_file || "")}</footer>
        </div>
      </article>`;
      })
      .join("");
    return `
      <section class="explore-search-section">
        <h3 class="explore-section-title">${escapeHtml(title)}
          <span>${items.length}</span>
        </h3>
        ${cards}
      </section>`;
  }

  function renderHitList(container, items, emptyText) {
    if (!container) return;
    if (!items.length) {
      container.innerHTML = `<p class="explore-empty">${escapeHtml(emptyText)}</p>`;
      return;
    }
    container.innerHTML = items
      .map(
        (it) => `
      <article class="explore-hit explore-hit-entity" data-taggable="1" data-chunk-id="${escapeHtml(it.chunk_id || it.id || "")}" data-date="${escapeHtml(it.date || "")}">
        <header>
          <strong>${escapeHtml(it.date || "")}</strong>
          <span>${escapeHtml(it.source_file || "")}</span>
        </header>
        <p>${escapeHtml(it.preview || "")}</p>
      </article>`
      )
      .join("");
    container.querySelectorAll(".explore-hit[data-date]").forEach((el) => {
      el.addEventListener("click", () => {
        const sel = window.getSelection();
        if (sel && !sel.isCollapsed && el.contains(sel.anchorNode)) return;
        openExploreDay(el.getAttribute("data-date") || "", el.getAttribute("data-chunk-id") || "");
      });
    });
  }

  async function runSearch(q) {
    const box = $("#explore-search-results");
    if (!box) return;
    const query = (q || "").trim();
    selectedSearchIds.clear();
    searchHitIds = [];
    updateSearchSelectUi();
    if (!query) {
      box.innerHTML = `<p class="explore-empty">输入关键词，对日记原文做 grep 搜索。</p>`;
      return;
    }
    box.innerHTML = `<p class="explore-empty">搜索中…</p>`;
    try {
      const data = await api(
        `/explore/search?q=${encodeURIComponent(query)}&limit=50`
      );
      const exact = data.exact || [];
      const near = data.near || [];
      if (!exact.length && !near.length) {
        box.innerHTML = `<p class="explore-empty">没有找到相关片段。</p>`;
        updateSearchSelectUi();
        return;
      }
      box.innerHTML =
        renderSearchSection("完全匹配", exact, "精确") +
        renderSearchSection("相近结果", near, "相近");
      searchHitIds = [...exact, ...near]
        .map((it) => String(it.chunk_id || ""))
        .filter(Boolean);
      box.querySelectorAll(".explore-hit[data-chunk-id]").forEach(bindSearchHitCard);
      updateSearchSelectUi();
    } catch (err) {
      box.innerHTML = `<p class="explore-empty">${escapeHtml(err.message)}</p>`;
      updateSearchSelectUi();
    }
  }

  function updateNameGrepSelectUi() {
    const toolbar = $("#tag-name-grep-toolbar");
    const all = $("#tag-name-grep-select-all");
    const label = $("#tag-name-grep-selected");
    const addBtn = $("#tag-name-grep-add-tag");
    const bindBtn = $("#tag-name-grep-bind-tag");
    const n = selectedNameGrepIds.size;
    const total = nameGrepHitIds.length;
    if (toolbar) toolbar.hidden = total === 0;
    if (label) label.textContent = `已选 ${n}`;
    if (addBtn) addBtn.disabled = n === 0;
    if (bindBtn) {
      bindBtn.hidden = !nameGrepTagId;
      bindBtn.disabled = n === 0;
      bindBtn.textContent = nameGrepTagName
        ? `绑定到「${nameGrepTagName}」`
        : "绑定到此 tag";
    }
    if (all) {
      all.checked = total > 0 && n === total;
      all.indeterminate = n > 0 && n < total;
    }
    document
      .querySelectorAll("#tag-name-grep-results .explore-hit[data-chunk-id]")
      .forEach((el) => {
        const id = el.getAttribute("data-chunk-id");
        const on = selectedNameGrepIds.has(id);
        el.classList.toggle("is-selected", on);
        const cb = el.querySelector(".explore-hit-check");
        if (cb) cb.checked = on;
      });
  }

  function clearNameGrepSelection() {
    selectedNameGrepIds.clear();
    updateNameGrepSelectUi();
  }

  function toggleNameGrepHit(chunkId, force) {
    if (!chunkId) return;
    const on = force == null ? !selectedNameGrepIds.has(chunkId) : !!force;
    if (on) selectedNameGrepIds.add(chunkId);
    else selectedNameGrepIds.delete(chunkId);
    updateNameGrepSelectUi();
  }

  function selectAllNameGrepHits(on) {
    selectedNameGrepIds.clear();
    if (on) nameGrepHitIds.forEach((id) => selectedNameGrepIds.add(id));
    updateNameGrepSelectUi();
  }

  function bindNameGrepHitCard(article) {
    const chunkId = article.getAttribute("data-chunk-id") || "";
    const dateStr = article.getAttribute("data-date") || "";
    const check = article.querySelector(".explore-hit-check");
    check?.addEventListener("click", (e) => e.stopPropagation());
    check?.addEventListener("change", () => {
      toggleNameGrepHit(chunkId, check.checked);
    });
    article.addEventListener("click", (e) => {
      if (e.target.closest(".explore-hit-check-wrap")) return;
      const sel = window.getSelection();
      if (sel && !sel.isCollapsed && article.contains(sel.anchorNode)) return;
      openExploreDay(dateStr, chunkId);
    });
  }

  async function runNameGrepSearch(q) {
    const box = $("#tag-name-grep-results");
    if (!box) return;
    const query = (q || "").trim();
    selectedNameGrepIds.clear();
    nameGrepHitIds = [];
    updateNameGrepSelectUi();
    if (!query) {
      box.innerHTML = `<p class="explore-empty">输入关键词，对日记原文做 grep 搜索。</p>`;
      return;
    }
    box.innerHTML = `<p class="explore-empty">搜索中…</p>`;
    try {
      const data = await api(
        `/explore/search?q=${encodeURIComponent(query)}&limit=50`
      );
      const exact = data.exact || [];
      const near = data.near || [];
      if (!exact.length && !near.length) {
        box.innerHTML = `<p class="explore-empty">没有找到相关片段。</p>`;
        updateNameGrepSelectUi();
        return;
      }
      box.innerHTML =
        renderSearchSection("完全匹配", exact, "精确") +
        renderSearchSection("相近结果", near, "相近");
      nameGrepHitIds = [...exact, ...near]
        .map((it) => String(it.chunk_id || ""))
        .filter(Boolean);
      box
        .querySelectorAll(".explore-hit[data-chunk-id]")
        .forEach(bindNameGrepHitCard);
      updateNameGrepSelectUi();
    } catch (err) {
      box.innerHTML = `<p class="explore-empty">${escapeHtml(err.message)}</p>`;
      updateNameGrepSelectUi();
    }
  }

  function closeNameGrepModal() {
    const backdrop = $("#tag-name-grep-backdrop");
    if (backdrop) backdrop.hidden = true;
    nameGrepOpen = false;
    nameGrepTagId = "";
    nameGrepTagName = "";
    nameGrepHitIds = [];
    selectedNameGrepIds.clear();
  }

  function bindNameGrepModal() {
    if (nameGrepBound) return;
    nameGrepBound = true;
    $("#tag-name-grep-close")?.addEventListener("click", closeNameGrepModal);
    $("#tag-name-grep-backdrop")?.addEventListener("click", (e) => {
      if (e.target && e.target.id === "tag-name-grep-backdrop") {
        closeNameGrepModal();
      }
    });
    $("#tag-name-grep-form")?.addEventListener("submit", (e) => {
      e.preventDefault();
      runNameGrepSearch($("#tag-name-grep-input")?.value || "");
    });
    $("#tag-name-grep-select-all")?.addEventListener("change", (e) => {
      selectAllNameGrepHits(!!e.target.checked);
    });
    $("#tag-name-grep-add-tag")?.addEventListener("click", () => {
      const ids = [...selectedNameGrepIds];
      if (!ids.length) return;
      if (typeof SelectionTag === "undefined" || !SelectionTag.openForChunks) {
        showError?.("Tag 选择组件未就绪");
        return;
      }
      SelectionTag.openForChunks(ids).catch((err) =>
        showError?.(err.message || "添加失败")
      );
    });
    $("#tag-name-grep-bind-tag")?.addEventListener("click", async () => {
      const ids = [...selectedNameGrepIds];
      if (!ids.length || !nameGrepTagId) return;
      try {
        const tag = UserTag.from({ id: nameGrepTagId, name: nameGrepTagName });
        const res = await tag.bind(ids);
        showError?.(
          `已绑定到「${tag.name || nameGrepTagName}」（${res.bound?.length || ids.length} 个片段）`
        );
        clearNameGrepSelection();
        document.dispatchEvent(
          new CustomEvent("selection-tag-bound", {
            detail: { tagId: nameGrepTagId, chunkIds: ids },
          })
        );
      } catch (err) {
        showError?.(err.message || "绑定失败");
      }
    });
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      if (!nameGrepOpen) return;
      if (document.querySelector(".tag-picker-backdrop")) return;
      if (tagPickerOpen) return;
      const day = $("#explore-day-backdrop");
      if (day && !day.hidden) return;
      closeNameGrepModal();
    });
    document.addEventListener("selection-tag-bound", () => {
      if (nameGrepOpen && selectedNameGrepIds.size) clearNameGrepSelection();
    });
  }

  async function openNameGrepModal(opts = {}) {
    bind();
    bindNameGrepModal();
    const query = String(opts.query || "").trim();
    nameGrepTagId = String(opts.tagId || "");
    nameGrepTagName = String(opts.tagName || query || "");
    nameGrepOpen = true;
    const backdrop = $("#tag-name-grep-backdrop");
    const title = $("#tag-name-grep-title");
    const sub = $("#tag-name-grep-sub");
    const input = $("#tag-name-grep-input");
    if (title) {
      title.textContent = nameGrepTagName
        ? `检索「${nameGrepTagName}」`
        : "检索 tag 名";
    }
    if (sub) {
      sub.textContent = nameGrepTagId
        ? "可选中结果并一键绑定到刚创建的 tag"
        : "与探索搜索相同，可多选后添加到 tag";
    }
    if (input) input.value = query;
    if (backdrop) backdrop.hidden = false;
    updateNameGrepSelectUi();
    await runNameGrepSearch(query);
  }

  function closeTagDetailModal() {
    const backdrop = $("#tag-detail-backdrop");
    if (backdrop) backdrop.hidden = true;
    tagDetailOpen = false;
  }

  function bindTagDetailModal() {
    if (tagDetailBound) return;
    tagDetailBound = true;
    $("#tag-detail-close")?.addEventListener("click", closeTagDetailModal);
    $("#tag-detail-backdrop")?.addEventListener("click", (e) => {
      if (e.target && e.target.id === "tag-detail-backdrop") closeTagDetailModal();
    });
    // 故事入口：逻辑暂不实现
    $("#tag-story-enter")?.addEventListener("click", (e) => {
      e.preventDefault();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      if (!tagDetailOpen) return;
      if (nameGrepOpen || tagPickerOpen) return;
      if (document.querySelector(".tag-picker-backdrop")) return;
      const day = $("#explore-day-backdrop");
      if (day && !day.hidden) return;
      closeTagDetailModal();
    });
  }

  async function openTagDetailModal(opts = {}) {
    bind();
    bindTagDetailModal();
    const tagId = String(opts.tagId || "").trim();
    if (!tagId) return;
    tagDetailOpen = true;
    const backdrop = $("#tag-detail-backdrop");
    const title = $("#tag-detail-title");
    const meta = $("#tag-detail-meta");
    const main = $("#tag-detail-main");
    const storyName = $("#tag-story-name");
    const storyBtn = $("#tag-story-enter");
    if (backdrop) backdrop.hidden = false;
    const fallbackName = String(opts.tagName || "Tag");
    if (title) {
      title.textContent = fallbackName;
      if (opts.color) title.style.setProperty("--tag-color", opts.color);
    }
    if (storyName) storyName.textContent = fallbackName;
    if (storyBtn) storyBtn.disabled = true;
    if (meta) meta.textContent = "加载中…";
    if (main) main.innerHTML = `<p class="explore-empty">加载中…</p>`;

    try {
      const tag = UserTag.from({
        id: tagId,
        name: opts.tagName,
        color: opts.color,
      });
      const data = await tag.listChunks({ limit: 80 });
      const t = data.tag || tag.toJSON();
      const name = t.name || fallbackName;
      if (title) {
        title.textContent = name;
        title.style.setProperty("--tag-color", t.color || opts.color || "#6b7280");
      }
      if (storyName) storyName.textContent = name;
      if (meta) {
        meta.textContent = `绑定片段 ${data.total || 0}`;
      }
      if (!main) return;
      main.innerHTML = "";
      const head = document.createElement("div");
      head.className = "tag-detail-pill-row";
      head.innerHTML = `<span class="tag-pill" style="--tag-color:${escapeHtml(t.color || "#6b7280")}">${escapeHtml(name)}</span>`;
      main.appendChild(head);
      const wrap = document.createElement("div");
      wrap.className = "explore-detail-hits";
      renderHitList(
        wrap,
        data.items || [],
        "还没有绑定片段。可在搜索或多选结果里「添加到 tag」。"
      );
      main.appendChild(wrap);
    } catch (err) {
      if (meta) meta.textContent = "加载失败";
      if (main) {
        main.innerHTML = `<p class="explore-empty">${escapeHtml(err.message)}</p>`;
      }
    }
  }

  function renderEntityList(listEl, detailEl, items, entityType) {
    if (!listEl || !detailEl) return;
    if (!items.length) {
      listEl.innerHTML = `<p class="explore-empty">暂无数据</p>`;
      detailEl.innerHTML = `<p class="explore-empty">库中还没有可展示的条目。</p>`;
      return;
    }
    listEl.innerHTML = "";
    items.forEach((item, idx) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "explore-list-item" + (idx === 0 ? " active" : "");
      btn.innerHTML = `
        <strong>${escapeHtml(item.name)}</strong>
        <small>${item.df || 0} 天片段 · ${item.total_tf || 0} 次</small>
      `;
      btn.addEventListener("click", () => {
        listEl.querySelectorAll(".explore-list-item").forEach((el) => {
          el.classList.remove("active");
        });
        btn.classList.add("active");
        loadEntityDetail(detailEl, item.name, entityType);
      });
      listEl.appendChild(btn);
    });
    loadEntityDetail(detailEl, items[0].name, entityType);
  }

  async function loadEntityDetail(detailEl, name, entityType) {
    if (!detailEl) return;
    detailEl.innerHTML = `<p class="explore-empty">加载「${escapeHtml(name)}」…</p>`;
    try {
      const data = await api(
        `/explore/entities/chunks?name=${encodeURIComponent(name)}&type=${encodeURIComponent(entityType)}`
      );
      detailEl.innerHTML = `<h3 class="explore-detail-title">${escapeHtml(name)}</h3>`;
      const wrap = document.createElement("div");
      wrap.className = "explore-detail-hits";
      renderHitList(wrap, data.items || [], "没有相关片段。");
      detailEl.appendChild(wrap);
    } catch (err) {
      detailEl.innerHTML = `<p class="explore-empty">${escapeHtml(err.message)}</p>`;
    }
  }

  async function loadPeople() {
    const rail = $("#explore-people-rail");
    const detail = $("#explore-people-detail");
    if (rail) rail.innerHTML = `<p class="explore-empty">加载中…</p>`;
    try {
      const data = await api("/people");
      loaded.people = true;
      renderPeopleRail(rail, detail, data.items || []);
    } catch (err) {
      if (rail) rail.innerHTML = `<p class="explore-empty">${escapeHtml(err.message)}</p>`;
    }
  }

  let activePersonId = null;
  let peopleAddBound = false;

  function personInitial(name) {
    const s = String(name || "").trim();
    return s ? s.slice(0, 1) : "?";
  }

  function personAvatarHtml(person, sizeClass = "") {
    const color = escapeHtml(person.tag_color || TAG_PALETTE[0]);
    if (person.photo_url) {
      return `<span class="people-avatar ${sizeClass}" style="border-color:${color}"><img src="${escapeHtml(person.photo_url)}" alt="" /></span>`;
    }
    return `<span class="people-avatar ${sizeClass}" style="border-color:${color};background:color-mix(in srgb, ${color} 18%, #ebe6dc)">${escapeHtml(personInitial(person.name))}</span>`;
  }

  function bindPeopleAddDialog() {
    if (peopleAddBound) return;
    peopleAddBound = true;
    const dialog = $("#people-add-dialog");
    const form = $("#people-add-form");
    const preview = $("#people-add-preview");
    const photoInput = $("#people-add-photo");
    const cancel = $("#people-add-cancel");

    cancel?.addEventListener("click", () => dialog?.close());
    photoInput?.addEventListener("change", () => {
      const file = photoInput.files && photoInput.files[0];
      if (!file || !preview) {
        if (preview) {
          preview.hidden = true;
          preview.innerHTML = "";
        }
        return;
      }
      const url = URL.createObjectURL(file);
      preview.hidden = false;
      preview.innerHTML = `<img src="${url}" alt="" />`;
    });
    form?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const name = ($("#people-add-name")?.value || "").trim();
      if (!name) return;
      const file = photoInput?.files && photoInput.files[0];
      const submit = $("#people-add-submit");
      if (submit) submit.disabled = true;
      try {
        const fd = new FormData();
        fd.append("name", name);
        if (file) fd.append("photo", file);
        const res = await fetch("/api/people", { method: "POST", body: fd });
        if (!res.ok) {
          let detail = res.statusText;
          try {
            const body = await res.json();
            detail = body.detail || detail;
          } catch {
            /* ignore */
          }
          throw new Error(
            typeof detail === "string" ? detail : JSON.stringify(detail)
          );
        }
        const person = await res.json();
        dialog?.close();
        form.reset();
        if (preview) {
          preview.hidden = true;
          preview.innerHTML = "";
        }
        loaded.people = false;
        await loadPeople();
        activePersonId = person.id;
        await loadPersonDetail($("#explore-people-detail"), person.id);
        const rail = $("#explore-people-rail");
        rail?.querySelectorAll(".people-card[data-person-id]").forEach((el) => {
          el.classList.toggle("active", el.dataset.personId === person.id);
        });
        if (person.tag_id) {
          await UserTag.from({
            id: person.tag_id,
            name: person.name,
            color: person.tag_color,
          }).offerNameSearch();
        }
      } catch (err) {
        showError?.(err.message || "添加失败");
      } finally {
        if (submit) submit.disabled = false;
      }
    });
  }

  function openPeopleAddDialog() {
    bindPeopleAddDialog();
    const dialog = $("#people-add-dialog");
    const form = $("#people-add-form");
    const preview = $("#people-add-preview");
    form?.reset();
    if (preview) {
      preview.hidden = true;
      preview.innerHTML = "";
    }
    dialog?.showModal();
    $("#people-add-name")?.focus();
  }

  function renderPeopleRail(railEl, detailEl, items) {
    if (!railEl || !detailEl) return;
    railEl.innerHTML = "";

    const addBtn = document.createElement("button");
    addBtn.type = "button";
    addBtn.className = "people-card is-add";
    addBtn.innerHTML = `
      <span class="people-avatar" aria-hidden="true">＋</span>
      <span class="people-card-name">添加</span>
    `;
    addBtn.addEventListener("click", openPeopleAddDialog);
    railEl.appendChild(addBtn);

    if (!items.length) {
      detailEl.innerHTML = `<p class="explore-empty">还没有人物。点击左侧虚线圆圈添加，并可为对方上传头像。</p>`;
      return;
    }

    items.forEach((person) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className =
        "people-card" + (person.id === activePersonId ? " active" : "");
      btn.dataset.personId = person.id;
      btn.title = person.name;
      btn.innerHTML = `
        ${personAvatarHtml(person)}
        <span class="people-card-name">${escapeHtml(person.name)}</span>
      `;
      btn.addEventListener("click", () => {
        activePersonId = person.id;
        railEl.querySelectorAll(".people-card[data-person-id]").forEach((el) => {
          el.classList.toggle("active", el.dataset.personId === person.id);
        });
        loadPersonDetail(detailEl, person.id);
      });
      railEl.appendChild(btn);
    });

    const firstId = activePersonId && items.some((p) => p.id === activePersonId)
      ? activePersonId
      : items[0].id;
    activePersonId = firstId;
    railEl.querySelectorAll(".people-card[data-person-id]").forEach((el) => {
      el.classList.toggle("active", el.dataset.personId === firstId);
    });
    loadPersonDetail(detailEl, firstId);
  }

  async function loadPersonDetail(detailEl, personId) {
    if (!detailEl || !personId) return;
    detailEl.innerHTML = `<p class="explore-empty">加载中…</p>`;
    try {
      const data = await api(
        `/people/${encodeURIComponent(personId)}/chunks?limit=80`
      );
      const person = data.person || {};
      detailEl.innerHTML = `
        <div class="people-detail-head">
          ${personAvatarHtml(person)}
          <div class="people-detail-meta">
            <strong>${escapeHtml(person.name || "")}</strong>
            <small>绑定片段 ${data.total || 0} · tag 可在「其他 tag → 人物」中管理</small>
          </div>
        </div>
      `;
      const wrap = document.createElement("div");
      wrap.className = "explore-detail-hits";
      renderHitList(
        wrap,
        data.items || [],
        "还没有绑定片段。可在搜索或多选结果里「添加到 tag」，选择此人物对应的 tag。"
      );
      detailEl.appendChild(wrap);
    } catch (err) {
      detailEl.innerHTML = `<p class="explore-empty">${escapeHtml(err.message)}</p>`;
    }
  }

  async function loadPlaces() {
    const list = $("#explore-places-list");
    const detail = $("#explore-places-detail");
    if (list) list.innerHTML = `<p class="explore-empty">加载中…</p>`;
    try {
      const data = await api("/explore/entities?type=place");
      loaded.places = true;
      renderEntityList(list, detail, data.items || [], "place");
    } catch (err) {
      if (list) list.innerHTML = `<p class="explore-empty">${escapeHtml(err.message)}</p>`;
    }
  }

  const TAG_PALETTE = [
    "#c45c48",
    "#d4893a",
    "#c9a227",
    "#5a9a6a",
    "#3d8b8b",
    "#3a6ea5",
    "#5c6bc0",
    "#7a5ea7",
    "#b85c8a",
    "#8b6f5c",
    "#6b7280",
    "#4a5568",
  ];

  let selectMode = false;
  let selectedTagIds = new Set();
  let selectedFolderIds = new Set();
  let openMenuTagId = null;
  let tagPickerOpen = false;
  let tagPickerBound = false;

  function getTagMount() {
    if (tagPickerOpen) return $("#tag-mgmt-modal-body");
    return $("#explore-tags-body");
  }

  function tagQ(sel) {
    return getTagMount()?.querySelector(sel) || null;
  }

  function tagPillHtml(t, extraClass = "") {
    const color = escapeHtml(t.color || TAG_PALETTE[0]);
    return `<span class="tag-pill ${extraClass}" style="--tag-color:${color}">${escapeHtml(t.name)}</span>`;
  }

  function closeTagMenus() {
    openMenuTagId = null;
    document.querySelectorAll(".tag-more-menu.open").forEach((el) => {
      el.classList.remove("open");
    });
  }

  function selectionCount() {
    return selectedTagIds.size + selectedFolderIds.size;
  }

  function syncSelectionFromDom() {
    selectedTagIds.clear();
    selectedFolderIds.clear();
    getTagMount()
      ?.querySelectorAll(".tag-fs-item.selected")
      .forEach((el) => {
        if (el.dataset.tagId) selectedTagIds.add(el.dataset.tagId);
        if (el.dataset.folderId) selectedFolderIds.add(el.dataset.folderId);
      });
  }

  function updateSelectUi() {
    const browser = tagQ("#tag-mgmt-browser");
    const modeBtn = tagQ("#tag-mgmt-select-mode");
    const allBtn = tagQ("#tag-mgmt-select-all");
    const delBtn = tagQ("#tag-mgmt-batch-delete");
    const clearBtn = tagQ("#tag-mgmt-clear-selection");
    const colorBtn = tagQ("#tag-mgmt-batch-color");
    const moveBtn = tagQ("#tag-mgmt-batch-move");
    const countEl = tagQ("#tag-mgmt-selection-count");
    const grid = tagQ("#tag-mgmt-grid");

    if (browser) browser.classList.toggle("is-select-mode", selectMode);
    if (grid) grid.classList.toggle("is-select-mode", selectMode);

    if (modeBtn) {
      modeBtn.textContent = selectMode ? "退出选择" : "选择";
      modeBtn.classList.toggle("active", selectMode);
    }

    const n = selectionCount();
    const showTools = selectMode;
    if (allBtn) allBtn.hidden = !showTools;
    if (delBtn) {
      delBtn.hidden = !showTools;
      delBtn.disabled = n === 0;
    }
    if (clearBtn) {
      clearBtn.hidden = !showTools;
      clearBtn.disabled = n === 0;
    }
    if (colorBtn) {
      colorBtn.hidden = !showTools;
      colorBtn.disabled = selectedTagIds.size === 0;
      colorBtn.title =
        selectedTagIds.size === 0
          ? "更换颜色仅对 tag 有效"
          : "仅更改选中的 tag 颜色";
    }
    if (moveBtn) {
      moveBtn.hidden = !showTools;
      moveBtn.disabled = n === 0;
    }
    if (countEl) {
      countEl.hidden = !showTools || n === 0;
      const parts = [];
      if (selectedTagIds.size) parts.push(`${selectedTagIds.size} tag`);
      if (selectedFolderIds.size) parts.push(`${selectedFolderIds.size} 文件夹`);
      countEl.textContent = parts.length ? `已选 ${parts.join(" · ")}` : "";
    }
  }

  function clearSelection() {
    selectedTagIds.clear();
    selectedFolderIds.clear();
    getTagMount()
      ?.querySelectorAll(".tag-fs-item.selected")
      .forEach((el) => el.classList.remove("selected"));
    updateSelectUi();
  }

  function setItemSelected(el, on) {
    if (!el || el.dataset.locked === "1") return;
    const tagId = el.dataset.tagId;
    const folderId = el.dataset.folderId;
    el.classList.toggle("selected", !!on);
    if (tagId) {
      if (on) selectedTagIds.add(tagId);
      else selectedTagIds.delete(tagId);
    }
    if (folderId) {
      if (on) selectedFolderIds.add(folderId);
      else selectedFolderIds.delete(folderId);
    }
  }

  function toggleItemSelected(el) {
    if (!el || el.dataset.locked === "1") return;
    setItemSelected(el, !el.classList.contains("selected"));
    updateSelectUi();
  }

  function enterSelectMode() {
    selectMode = true;
    getTagMount()
      ?.querySelectorAll("#tag-mgmt-grid .tag-fs-item")
      .forEach((el) => {
        el.draggable = false;
      });
    updateSelectUi();
  }

  function exitSelectMode() {
    selectMode = false;
    clearSelection();
    getTagMount()
      ?.querySelectorAll("#tag-mgmt-grid .tag-fs-item")
      .forEach((el) => {
        if (el.dataset.locked === "1") return;
        el.draggable = true;
      });
    updateSelectUi();
  }

  async function pickColor(title) {
    return new Promise((resolve) => {
      const backdrop = document.createElement("div");
      backdrop.className = "tag-picker-backdrop";
      backdrop.innerHTML = `
        <div class="tag-picker-modal" role="dialog">
          <h3>${escapeHtml(title || "选择颜色")}</h3>
          <div class="tag-palette">
            ${TAG_PALETTE.map(
              (c) =>
                `<button type="button" class="tag-palette-swatch" data-color="${c}" style="--tag-color:${c}" title="${c}"></button>`
            ).join("")}
          </div>
          <button type="button" class="tag-picker-cancel">取消</button>
        </div>`;
      const finish = (val) => {
        backdrop.remove();
        resolve(val);
      };
      backdrop.addEventListener("click", (e) => {
        if (e.target === backdrop) finish(null);
      });
      backdrop.querySelector(".tag-picker-cancel")?.addEventListener("click", () =>
        finish(null)
      );
      backdrop.querySelectorAll(".tag-palette-swatch").forEach((btn) => {
        btn.addEventListener("click", () => finish(btn.dataset.color));
      });
      document.body.appendChild(backdrop);
    });
  }

  async function pickFolder(title) {
    return new Promise((resolve) => {
      let browseId = null; // null = 根目录

      const backdrop = document.createElement("div");
      backdrop.className = "tag-picker-backdrop";
      backdrop.innerHTML = `
        <div class="tag-picker-modal tag-folder-browser" role="dialog">
          <h3>${escapeHtml(title || "移动到…")}</h3>
          <p class="tag-folder-path" id="tag-folder-path">根目录</p>
          <div class="tag-folder-list" id="tag-folder-list">
            <p class="tag-select-loading">加载中…</p>
          </div>
          <div class="tag-folder-browser-actions">
            <button type="button" class="tag-picker-cancel" id="tag-folder-cancel">取消</button>
            <button type="button" class="tag-folder-confirm" id="tag-folder-confirm">移动到此处</button>
          </div>
        </div>`;
      document.body.appendChild(backdrop);

      const finish = (val) => {
        backdrop.remove();
        resolve(val);
      };

      const pathEl = backdrop.querySelector("#tag-folder-path");
      const listEl = backdrop.querySelector("#tag-folder-list");

      const renderPath = (breadcrumb) => {
        if (!pathEl) return;
        const parts = (breadcrumb || []).map((c) => c.name);
        pathEl.textContent = parts.length ? parts.join(" › ") : "根目录";
      };

      const renderList = (tree) => {
        if (!listEl) return;
        const folders = tree.folders || [];
        const crumb = tree.breadcrumb || [];
        renderPath(crumb);
        const atRoot = browseId == null;

        const rows = [];
        if (!atRoot) {
          // breadcrumb: [根, ..., 当前]；上一级为倒数第二项
          const up =
            crumb.length >= 2 ? crumb[crumb.length - 2].id : null;
          rows.push(
            `<button type="button" class="tag-folder-opt is-up" data-nav="${up == null ? "" : escapeHtml(up)}">..</button>`
          );
        }
        folders.forEach((f) => {
          rows.push(
            `<button type="button" class="tag-folder-opt" data-nav="${escapeHtml(f.id)}" title="${escapeHtml(f.name)}">${escapeHtml(f.name)}</button>`
          );
        });
        if (!folders.length) {
          rows.push(
            `<p class="tag-select-loading">${atRoot ? "根目录下暂无子文件夹" : "此文件夹下暂无子文件夹"}</p>`
          );
        }
        listEl.innerHTML = rows.join("");
        listEl.querySelectorAll(".tag-folder-opt[data-nav]").forEach((btn) => {
          btn.addEventListener("click", () => {
            const raw = btn.getAttribute("data-nav");
            browseId = raw === "" || raw == null ? null : raw;
            loadLevel();
          });
        });
      };

      const loadLevel = async () => {
        if (listEl) listEl.innerHTML = `<p class="tag-select-loading">加载中…</p>`;
        try {
          const qs =
            browseId == null
              ? ""
              : `?folder_id=${encodeURIComponent(browseId)}`;
          const tree = await api(`/tags/tree${qs}`);
          renderList(tree);
        } catch (err) {
          if (listEl) {
            listEl.innerHTML = `<p class="tag-select-loading">${escapeHtml(err.message)}</p>`;
          }
        }
      };

      backdrop.addEventListener("click", (e) => {
        if (e.target === backdrop) finish(undefined);
      });
      backdrop.querySelector("#tag-folder-cancel")?.addEventListener("click", () =>
        finish(undefined)
      );
      backdrop.querySelector("#tag-folder-confirm")?.addEventListener("click", () =>
        finish(browseId)
      );

      loadLevel();
    });
  }

  async function moveTagsToFolder(tagIds, folderId) {
    for (const id of tagIds) {
      const body =
        folderId == null ? { clear_folder: true } : { folder_id: folderId };
      await api(`/tags/${encodeURIComponent(id)}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      });
    }
  }

  async function moveFoldersToParent(folderIds, parentId) {
    for (const id of folderIds) {
      if (parentId === id) continue;
      const body =
        parentId == null ? { clear_parent: true } : { parent_id: parentId };
      await api(`/tags/folders/${encodeURIComponent(id)}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      });
    }
  }

  async function deleteTags(tagIds) {
    for (const id of tagIds) {
      await api(`/tags/${encodeURIComponent(id)}`, { method: "DELETE" });
    }
  }

  async function deleteFolders(folderIds) {
    for (const id of folderIds) {
      await api(`/tags/folders/${encodeURIComponent(id)}?move_up=true`, {
        method: "DELETE",
      });
    }
  }

  async function recolorTags(tagIds, color) {
    for (const id of tagIds) {
      await api(`/tags/${encodeURIComponent(id)}`, {
        method: "PATCH",
        body: JSON.stringify({ color }),
      });
    }
  }

  async function onTagActivate(tagId, tagMeta = {}) {
    if (!tagId) return;
    if (pendingChunkIds.length) {
      try {
        const res = await api(`/tags/${encodeURIComponent(tagId)}/bind`, {
          method: "POST",
          body: JSON.stringify({ chunk_ids: pendingChunkIds }),
        });
        showError?.(
          `已绑定到「${res.tag?.name || "tag"}」（${res.bound?.length || 0} 个片段）`
        );
        pendingChunkIds = [];
        if (typeof SelectionTag !== "undefined") SelectionTag.clearPending();
        document.dispatchEvent(
          new CustomEvent("selection-tag-bound", {
            detail: { tagId, chunkIds: res.bound || [] },
          })
        );
        if (tagPickerOpen) {
          closeTagPickerModal({ clearPending: false });
        } else {
          await loadTags(true);
        }
      } catch (err) {
        showError?.(err.message || "绑定失败");
      }
      return;
    }
    try {
      await UserTag.from({
        id: tagId,
        name: tagMeta.name || "",
        color: tagMeta.color || "",
      }).openDetail();
    } catch (err) {
      showError?.(err.message || "打开 tag 失败");
    }
  }

  async function editTag(tagId) {
    const name = prompt("修改 tag 名称");
    if (name == null) return;
    const trimmed = name.trim();
    if (!trimmed) return;
    try {
      await api(`/tags/${encodeURIComponent(tagId)}`, {
        method: "PATCH",
        body: JSON.stringify({ name: trimmed }),
      });
      await loadTags(true);
    } catch (err) {
      showError?.(err.message);
    }
  }

  async function createTagHere() {
    const name = prompt("新建 tag 名称");
    if (name == null || !name.trim()) return;
    const color = await pickColor("选择 tag 颜色（可取消使用随机）");
    try {
      const tag = await UserTag.create({
        name: name.trim(),
        folder_id: currentFolderId,
        color: color || null,
        // 有待绑定片段时先完成绑定，再询问检索
        offerSearch: false,
      });
      if (pendingChunkIds.length) {
        await onTagActivate(tag.id);
      } else {
        await loadTags(true);
      }
      await tag.offerNameSearch();
    } catch (err) {
      showError?.(err.message);
    }
  }

  async function createFolderHere() {
    const name = prompt("新建文件夹名称");
    if (name == null || !name.trim()) return;
    try {
      await api("/tags/folders", {
        method: "POST",
        body: JSON.stringify({
          name: name.trim(),
          parent_id: currentFolderId,
        }),
      });
      await loadTags(true);
    } catch (err) {
      showError?.(err.message);
    }
  }

  function setupDrag(el, payload) {
    el.draggable = true;
    el.addEventListener("dragstart", (e) => {
      e.dataTransfer.setData("application/x-tag-item", JSON.stringify(payload));
      e.dataTransfer.effectAllowed = "move";
    });
  }

  function setupDropFolder(el, folderId) {
    el.addEventListener("dragover", (e) => {
      e.preventDefault();
      el.classList.add("drag-over");
    });
    el.addEventListener("dragleave", () => el.classList.remove("drag-over"));
    el.addEventListener("drop", async (e) => {
      e.preventDefault();
      el.classList.remove("drag-over");
      let raw = e.dataTransfer.getData("application/x-tag-item");
      if (!raw) return;
      let data;
      try {
        data = JSON.parse(raw);
      } catch {
        return;
      }
      try {
        if (data.kind === "tag") {
          const body =
            folderId == null
              ? { clear_folder: true }
              : { folder_id: folderId };
          await api(`/tags/${encodeURIComponent(data.id)}`, {
            method: "PATCH",
            body: JSON.stringify(body),
          });
        } else if (data.kind === "folder") {
          if (data.id === folderId) return;
          const body =
            folderId == null
              ? { clear_parent: true }
              : { parent_id: folderId };
          await api(`/tags/folders/${encodeURIComponent(data.id)}`, {
            method: "PATCH",
            body: JSON.stringify(body),
          });
        }
        await loadTags(true);
      } catch (err) {
        showError?.(err.message);
      }
    });
  }

  async function renameFolder(folderId, currentName) {
    const name = prompt("修改文件夹名称", currentName || "");
    if (name == null) return;
    const trimmed = name.trim();
    if (!trimmed) return;
    try {
      await api(`/tags/folders/${encodeURIComponent(folderId)}`, {
        method: "PATCH",
        body: JSON.stringify({ name: trimmed }),
      });
      await loadTags(true);
    } catch (err) {
      showError?.(err.message);
    }
  }

  function bindFolderMenu(tile, f) {
    const moreBtn = tile.querySelector(".tag-more-btn");
    const menu = tile.querySelector(".tag-more-menu");
    moreBtn?.addEventListener("click", (e) => {
      e.stopPropagation();
      const willOpen = !menu.classList.contains("open");
      closeTagMenus();
      if (willOpen) {
        menu.classList.add("open");
        openMenuTagId = f.id;
      }
    });
    menu?.querySelector("[data-act=rename]")?.addEventListener("click", (e) => {
      e.stopPropagation();
      closeTagMenus();
      renameFolder(f.id, f.name);
    });
    menu?.querySelector("[data-act=move]")?.addEventListener("click", async (e) => {
      e.stopPropagation();
      closeTagMenus();
      const dest = await pickFolder(`移动文件夹「${f.name}」到…`);
      if (dest === undefined) return;
      if (dest === f.id) return;
      try {
        const body =
          dest == null ? { clear_parent: true } : { parent_id: dest };
        await api(`/tags/folders/${encodeURIComponent(f.id)}`, {
          method: "PATCH",
          body: JSON.stringify(body),
        });
        await loadTags(true);
      } catch (err) {
        showError?.(err.message);
      }
    });
    menu?.querySelector("[data-act=delete]")?.addEventListener("click", async (e) => {
      e.stopPropagation();
      closeTagMenus();
      if (
        !confirm(
          `删除文件夹「${f.name}」？\n其中的子文件夹与 tag 会移到上一级。`
        )
      ) {
        return;
      }
      try {
        await api(
          `/tags/folders/${encodeURIComponent(f.id)}?move_up=true`,
          { method: "DELETE" }
        );
        selectedFolderIds.delete(f.id);
        await loadTags(true);
      } catch (err) {
        showError?.(err.message);
      }
    });
  }

  function bindTagRowMenu(tile, t) {
    const moreBtn = tile.querySelector(".tag-more-btn");
    const menu = tile.querySelector(".tag-more-menu");
    moreBtn?.addEventListener("click", (e) => {
      e.stopPropagation();
      const willOpen = !menu.classList.contains("open");
      closeTagMenus();
      if (willOpen) {
        menu.classList.add("open");
        openMenuTagId = t.id;
      }
    });

    menu?.querySelector("[data-act=rename]")?.addEventListener("click", (e) => {
      e.stopPropagation();
      closeTagMenus();
      editTag(t.id);
    });
    menu?.querySelector("[data-act=delete]")?.addEventListener("click", async (e) => {
      e.stopPropagation();
      closeTagMenus();
      if (!confirm(`删除 tag「${t.name}」？`)) return;
      try {
        await deleteTags([t.id]);
        selectedTagIds.delete(t.id);
        await loadTags(true);
      } catch (err) {
        showError?.(err.message);
      }
    });
    menu?.querySelector("[data-act=move]")?.addEventListener("click", async (e) => {
      e.stopPropagation();
      closeTagMenus();
      const dest = await pickFolder(`移动「${t.name}」到…`);
      if (dest === undefined) return;
      try {
        await moveTagsToFolder([t.id], dest);
        await loadTags(true);
      } catch (err) {
        showError?.(err.message);
      }
    });
    menu?.querySelector("[data-act=color]")?.addEventListener("click", async (e) => {
      e.stopPropagation();
      closeTagMenus();
      const color = await pickColor(`更改「${t.name}」颜色`);
      if (!color) return;
      try {
        await recolorTags([t.id], color);
        await loadTags(true);
      } catch (err) {
        showError?.(err.message);
      }
    });
  }

  function renderTagManager(frequent, tree) {
    const body = getTagMount();
    if (!body) return;
    const crumbs = tree.breadcrumb || [];
    const folders = tree.folders || [];
    const tags = tree.tags || [];
    const visibleTags = new Set(tags.map((t) => t.id));
    const visibleFolders = new Set(folders.map((f) => f.id));
    selectedTagIds = new Set([...selectedTagIds].filter((id) => visibleTags.has(id)));
    selectedFolderIds = new Set(
      [...selectedFolderIds].filter((id) => visibleFolders.has(id))
    );

    body.innerHTML = `
      <div class="tag-mgmt${tagPickerOpen ? " tag-mgmt-compact" : ""}">
        ${
          pendingChunkIds.length
            ? `<div class="tag-mgmt-pending">待绑定 ${pendingChunkIds.length} 个片段 — 点击任意 tag 完成绑定</div>`
            : ""
        }
        <div class="tag-mgmt-row">
          <p class="tag-mgmt-row-label">新建与常用</p>
          <button type="button" class="tag-mgmt-btn" id="tag-mgmt-new-tag">＋ 新建 tag</button>
          ${(frequent || [])
            .map(
              (t) => `
            <button type="button" class="tag-mgmt-chip-wrap" data-tag-id="${escapeHtml(t.id)}">
              ${tagPillHtml(t)}
            </button>`
            )
            .join("")}
        </div>
        <div class="tag-mgmt-browser" id="tag-mgmt-browser">
          <div class="tag-fs-toolbar">
            <div class="tag-mgmt-crumb" id="tag-mgmt-crumb"></div>
            <div class="tag-fs-toolbar-actions">
              <span class="tag-mgmt-selection-count" id="tag-mgmt-selection-count" hidden></span>
              <button type="button" class="tag-mgmt-btn" id="tag-mgmt-select-mode">选择</button>
              <button type="button" class="tag-mgmt-btn" id="tag-mgmt-select-all" hidden>全选</button>
              <button type="button" class="tag-mgmt-btn" id="tag-mgmt-batch-color" hidden>更换颜色</button>
              <button type="button" class="tag-mgmt-btn" id="tag-mgmt-batch-move" hidden>移动位置</button>
              <button type="button" class="tag-mgmt-btn danger" id="tag-mgmt-batch-delete" hidden>删除</button>
              <button type="button" class="tag-mgmt-btn" id="tag-mgmt-clear-selection" hidden>取消选中</button>
              <button type="button" class="tag-mgmt-btn" id="tag-mgmt-new-folder">＋ 新建文件夹</button>
            </div>
          </div>
          <div class="tag-mgmt-grid" id="tag-mgmt-grid"></div>
        </div>
      </div>
    `;

    body.querySelector("#tag-mgmt-new-tag")?.addEventListener("click", createTagHere);
    body.querySelector("#tag-mgmt-new-folder")?.addEventListener("click", createFolderHere);

    body.querySelectorAll(".tag-mgmt-chip-wrap[data-tag-id]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const id = btn.dataset.tagId;
        const pill = btn.querySelector(".tag-pill");
        onTagActivate(id, {
          name: (pill?.textContent || "").trim(),
          color: getComputedStyle(pill || btn).getPropertyValue("--tag-color").trim(),
        });
      });
    });

    body.querySelector("#tag-mgmt-select-mode")?.addEventListener("click", () => {
      if (selectMode) exitSelectMode();
      else enterSelectMode();
    });

    body.querySelector("#tag-mgmt-clear-selection")?.addEventListener("click", () => {
      clearSelection();
    });

    body.querySelector("#tag-mgmt-select-all")?.addEventListener("click", () => {
      body.querySelectorAll(".tag-fs-item").forEach((el) => {
        if (el.dataset.locked === "1") return;
        setItemSelected(el, true);
      });
      updateSelectUi();
    });

    body.querySelector("#tag-mgmt-batch-delete")?.addEventListener("click", async () => {
      syncSelectionFromDom();
      const tagIds = [...selectedTagIds];
      const folderIds = [...selectedFolderIds];
      if (!tagIds.length && !folderIds.length) return;
      const msg = [
        tagIds.length ? `${tagIds.length} 个 tag` : "",
        folderIds.length ? `${folderIds.length} 个文件夹` : "",
      ]
        .filter(Boolean)
        .join("、");
      if (!confirm(`确定删除选中的 ${msg}？`)) return;
      try {
        if (tagIds.length) await deleteTags(tagIds);
        if (folderIds.length) await deleteFolders(folderIds);
        clearSelection();
        await loadTags(true);
      } catch (err) {
        showError?.(err.message);
      }
    });

    body.querySelector("#tag-mgmt-batch-color")?.addEventListener("click", async () => {
      syncSelectionFromDom();
      const ids = [...selectedTagIds];
      if (!ids.length) {
        showError?.("更换颜色仅对 tag 有效，请先选中 tag");
        return;
      }
      const color = await pickColor(
        `为 ${ids.length} 个 tag 更换颜色（文件夹不受影响）`
      );
      if (!color) return;
      try {
        await recolorTags(ids, color);
        await loadTags(true);
      } catch (err) {
        showError?.(err.message);
      }
    });

    body.querySelector("#tag-mgmt-batch-move")?.addEventListener("click", async () => {
      syncSelectionFromDom();
      const tagIds = [...selectedTagIds];
      const folderIds = [...selectedFolderIds];
      if (!tagIds.length && !folderIds.length) return;
      const dest = await pickFolder(`将选中项移动到…`);
      if (dest === undefined) return;
      try {
        if (tagIds.length) await moveTagsToFolder(tagIds, dest);
        if (folderIds.length) await moveFoldersToParent(folderIds, dest);
        clearSelection();
        await loadTags(true);
      } catch (err) {
        showError?.(err.message);
      }
    });

    const crumbEl = body.querySelector("#tag-mgmt-crumb");
    if (crumbEl) {
      crumbEl.innerHTML = "";
      crumbs.forEach((c, i) => {
        if (i > 0) {
          const sep = document.createElement("span");
          sep.className = "sep";
          sep.textContent = "›";
          crumbEl.appendChild(sep);
        }
        const btn = document.createElement("button");
        btn.type = "button";
        btn.textContent = c.name;
        const fid = c.id == null ? null : c.id;
        setupDropFolder(btn, fid);
        btn.addEventListener("click", () => {
          currentFolderId = fid;
          loadTags(true);
        });
        crumbEl.appendChild(btn);
      });
    }

    const grid = body.querySelector("#tag-mgmt-grid");
    if (!grid) return;
    grid.innerHTML = "";

    folders.forEach((f) => {
      const locked = !!f.locked || f.system_key === "people";
      const tile = document.createElement("div");
      tile.className =
        "tag-fs-item tag-fs-folder" +
        (locked ? " is-locked" : "") +
        (selectedFolderIds.has(f.id) ? " selected" : "");
      tile.dataset.folderId = f.id;
      if (locked) tile.dataset.locked = "1";
      tile.innerHTML = `
        <span class="tag-pill tag-folder-pill" title="${escapeHtml(f.name)}${locked ? "（系统文件夹）" : ""}">📁 ${escapeHtml(f.name)}</span>
        <div class="tag-more-wrap">
          <button type="button" class="tag-more-btn" aria-label="更多">⋯</button>
          <div class="tag-more-menu">
            <button type="button" data-act="rename">改名</button>
            ${
              locked
                ? ""
                : `<button type="button" data-act="move">移动位置</button>
            <button type="button" data-act="delete" class="danger">删除</button>`
            }
          </div>
        </div>
      `;
      if (!locked) setupDrag(tile, { kind: "folder", id: f.id });
      setupDropFolder(tile, f.id);
      if (selectMode) tile.draggable = false;
      tile.addEventListener("click", (e) => {
        if (e.target.closest(".tag-more-wrap")) return;
        if (selectMode) {
          if (locked) return;
          e.preventDefault();
          toggleItemSelected(tile);
          return;
        }
        currentFolderId = f.id;
        loadTags(true);
      });
      bindFolderMenu(tile, f);
      grid.appendChild(tile);
    });

    tags.forEach((t) => {
      const tile = document.createElement("div");
      tile.className =
        "tag-fs-item tag-fs-tag" + (selectedTagIds.has(t.id) ? " selected" : "");
      tile.dataset.tagId = t.id;
      tile.style.setProperty("--tag-color", t.color || TAG_PALETTE[0]);
      tile.innerHTML = `
        <span class="tag-pill tag-pill-tile">${escapeHtml(t.name)}</span>
        <div class="tag-more-wrap">
          <button type="button" class="tag-more-btn" aria-label="更多">⋯</button>
          <div class="tag-more-menu">
            <button type="button" data-act="rename">改名</button>
            <button type="button" data-act="color">更换颜色</button>
            <button type="button" data-act="move">移动位置</button>
            <button type="button" data-act="delete" class="danger">删除</button>
          </div>
        </div>
      `;
      setupDrag(tile, { kind: "tag", id: t.id });
      if (selectMode) tile.draggable = false;
      tile.addEventListener("click", (e) => {
        if (e.target.closest(".tag-more-wrap")) return;
        if (selectMode) {
          e.preventDefault();
          toggleItemSelected(tile);
          return;
        }
        onTagActivate(t.id, { name: t.name, color: t.color });
      });
      bindTagRowMenu(tile, t);
      grid.appendChild(tile);
    });

    if (!folders.length && !tags.length) {
      const empty = document.createElement("p");
      empty.className = "explore-empty";
      empty.textContent = "此文件夹为空。";
      grid.appendChild(empty);
    }

    const browser = body.querySelector("#tag-mgmt-browser");
    if (browser) setupDropFolder(browser, currentFolderId);
    updateSelectUi();
  }



  async function loadTags(force) {
    const body = getTagMount();
    if (!body) return;
    if (!force && !tagPickerOpen && loaded.tags) return;
    body.innerHTML = `<p class="explore-empty">加载中…</p>`;
    try {
      const qs =
        currentFolderId == null
          ? ""
          : `?folder_id=${encodeURIComponent(currentFolderId)}`;
      const [home, tree] = await Promise.all([
        api("/explore/tags"),
        api(`/tags/tree${qs}`),
      ]);
      if (!tagPickerOpen) loaded.tags = true;
      renderTagManager(home.frequent || [], tree);
    } catch (err) {
      body.innerHTML = `<p class="explore-empty">${escapeHtml(err.message)}</p>`;
    }
  }

  function bindTagPickerModal() {
    if (tagPickerBound) return;
    tagPickerBound = true;
    $("#tag-mgmt-modal-close")?.addEventListener("click", () =>
      closeTagPickerModal()
    );
    $("#tag-mgmt-modal-backdrop")?.addEventListener("click", (e) => {
      if (e.target && e.target.id === "tag-mgmt-modal-backdrop") {
        closeTagPickerModal();
      }
    });
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      if (!tagPickerOpen) return;
      if (nameGrepOpen) return;
      // 颜色/文件夹选择器打开时优先让它们处理
      if (document.querySelector(".tag-picker-backdrop")) return;
      closeTagPickerModal();
    });
  }

  function closeTagPickerModal(opts = {}) {
    const clearPending = opts.clearPending !== false;
    const backdrop = $("#tag-mgmt-modal-backdrop");
    tagPickerOpen = false;
    if (backdrop) backdrop.hidden = true;
    selectMode = false;
    selectedTagIds.clear();
    selectedFolderIds.clear();
    if (clearPending && pendingChunkIds.length) {
      pendingChunkIds = [];
      if (typeof SelectionTag !== "undefined") SelectionTag.clearPending();
    }
    if (active === "tags") {
      loadTags(true).catch(() => {});
    }
  }

  function openTagPickerModal(opts = {}) {
    bind();
    bindTagPickerModal();
    if (opts.pendingChunkIds) {
      pendingChunkIds = [...opts.pendingChunkIds];
      if (typeof SelectionTag !== "undefined") {
        SelectionTag.setPendingChunkIds(pendingChunkIds);
      }
    }
    currentFolderId = null;
    selectMode = false;
    selectedTagIds.clear();
    selectedFolderIds.clear();
    tagPickerOpen = true;
    const backdrop = $("#tag-mgmt-modal-backdrop");
    const pendingHint = $("#tag-mgmt-modal-pending");
    if (pendingHint) {
      pendingHint.hidden = !pendingChunkIds.length;
      pendingHint.textContent = pendingChunkIds.length
        ? `待绑定 ${pendingChunkIds.length} 个片段`
        : "";
    }
    if (backdrop) backdrop.hidden = false;
    loadTags(true).catch((err) => showError?.(err.message));
  }

  function openTagManager(opts = {}) {
    if (opts.pendingChunkIds) {
      openTagPickerModal(opts);
      return;
    }
    currentFolderId = null;
    setTab("tags");
  }

  function bind() {
    if (bound) return;
    bound = true;
    $("#explore-tabs")?.addEventListener("click", (e) => {
      const btn = e.target.closest(".explore-tab");
      if (!btn) return;
      setTab(btn.dataset.explore);
    });
    $("#explore-search-form")?.addEventListener("submit", (e) => {
      e.preventDefault();
      runSearch($("#explore-search-input")?.value || "");
    });
    $("#explore-search-select-all")?.addEventListener("change", (e) => {
      selectAllSearchHits(!!e.target.checked);
    });
    $("#explore-search-add-tag")?.addEventListener("click", () => {
      addSelectedToTag().catch((err) => showError?.(err.message || "添加失败"));
    });
    document.addEventListener("selection-tag-bound", () => {
      if (selectedSearchIds.size) clearSearchSelection();
    });
    document.addEventListener("click", (e) => {
      if (e.target.closest(".tag-more-wrap")) return;
      closeTagMenus();
    });
  }

  function show() {
    bind();
    setTab(active);
  }

  return {
    show,
    setTab,
    openTagManager,
    openTagPickerModal,
    openNameGrepModal,
    openTagDetailModal,
  };
})();
