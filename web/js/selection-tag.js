/**
 * 原文选区 → 添加到 tag（复用组件）。
 * 容器需 data-taggable="1"；chunk 节点需 data-chunk-id。
 */
const SelectionTag = (() => {
  let fab = null;
  let pop = null;
  let pendingChunkIds = [];
  let lastChunkIds = [];
  let hideTimer = null;
  let bound = false;

  function $(sel) {
    return document.querySelector(sel);
  }

  function escapeHtml(s) {
    const d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  function ensureUi() {
    if (fab && pop) return;
    fab = document.createElement("button");
    fab.type = "button";
    fab.id = "tag-select-fab";
    fab.className = "tag-select-fab";
    fab.hidden = true;
    fab.textContent = "添加到 tag";
    document.body.appendChild(fab);

    pop = document.createElement("div");
    pop.id = "tag-select-pop";
    pop.className = "tag-select-pop";
    pop.hidden = true;
    pop.innerHTML = `
      <div class="tag-select-pop-title">最近使用</div>
      <div class="tag-select-pop-list" id="tag-select-recent"></div>
      <button type="button" class="tag-select-more" id="tag-select-more">更多…</button>
    `;
    document.body.appendChild(pop);

    fab.addEventListener("mousedown", (e) => e.preventDefault());
    fab.addEventListener("click", onFabClick);
    $("#tag-select-more")?.addEventListener("mousedown", (e) => e.preventDefault());
    $("#tag-select-more")?.addEventListener("click", onMoreClick);
  }

  function rangesIntersect(a, b) {
    try {
      return (
        a.compareBoundaryPoints(Range.END_TO_START, b) < 0 &&
        a.compareBoundaryPoints(Range.START_TO_END, b) > 0
      );
    } catch {
      return false;
    }
  }

  function collectChunkIdsFromSelection() {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || !sel.rangeCount) return [];
    const range = sel.getRangeAt(0);
    const root =
      range.commonAncestorContainer.nodeType === Node.ELEMENT_NODE
        ? range.commonAncestorContainer
        : range.commonAncestorContainer.parentElement;
    if (!root) return [];

    const taggable = root.closest?.("[data-taggable='1']");
    if (!taggable) {
      // 选区可能跨多个 taggable；检查起点/终点
      const a = sel.anchorNode?.parentElement?.closest?.("[data-taggable='1']");
      const b = sel.focusNode?.parentElement?.closest?.("[data-taggable='1']");
      if (!a && !b) return [];
    }

    const scope = taggable || document.body;
    const nodes = scope.querySelectorAll("[data-chunk-id]");
    const ids = new Set();
    nodes.forEach((el) => {
      const id = el.getAttribute("data-chunk-id");
      if (!id) return;
      const er = document.createRange();
      try {
        er.selectNodeContents(el);
      } catch {
        return;
      }
      if (rangesIntersect(range, er)) ids.add(id);
    });

    // 单块卡片：选区在带 data-chunk-id 的根上
    if (!ids.size) {
      const hit = (sel.anchorNode?.nodeType === Node.ELEMENT_NODE
        ? sel.anchorNode
        : sel.anchorNode?.parentElement
      )?.closest?.("[data-chunk-id]");
      if (hit?.getAttribute("data-chunk-id")) {
        ids.add(hit.getAttribute("data-chunk-id"));
      }
    }
    return [...ids];
  }

  function selectionInTaggable() {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || !sel.rangeCount) return false;
    const a = (
      sel.anchorNode?.nodeType === Node.ELEMENT_NODE
        ? sel.anchorNode
        : sel.anchorNode?.parentElement
    )?.closest?.("[data-taggable='1']");
    const b = (
      sel.focusNode?.nodeType === Node.ELEMENT_NODE
        ? sel.focusNode
        : sel.focusNode?.parentElement
    )?.closest?.("[data-taggable='1']");
    return !!(a || b);
  }

  function onSelectionChange() {
    ensureUi();
    if (hideTimer) {
      clearTimeout(hideTimer);
      hideTimer = null;
    }
    if (!selectionInTaggable()) {
      // 点击 FAB / pop 时 selection 可能先清空，稍延迟隐藏
      hideTimer = setTimeout(() => {
        if (!pop || pop.hidden) hideFab();
      }, 180);
      return;
    }
    const ids = collectChunkIdsFromSelection();
    if (!ids.length) {
      hideFab();
      return;
    }
    lastChunkIds = ids;
    fab.hidden = false;
    pop.hidden = true;
  }

  function hideFab() {
    if (fab) fab.hidden = true;
    if (pop) pop.hidden = true;
  }

  async function showRecentPop() {
    ensureUi();
    if (!pendingChunkIds.length) {
      showError?.("未能识别选中的日记片段");
      return;
    }
    fab.hidden = false;
    pop.hidden = false;
    const list = $("#tag-select-recent");
    if (list) list.innerHTML = `<p class="tag-select-loading">加载中…</p>`;
    try {
      const data = await api("/tags/recent?limit=4");
      const items = data.items || [];
      if (!items.length) {
        list.innerHTML = `<p class="tag-select-loading">还没有 tag，点「更多」去创建</p>`;
        return;
      }
      list.innerHTML = items
        .map(
          (t) => `
        <button type="button" class="tag-select-chip" data-tag-id="${escapeHtml(t.id)}"
          style="--tag-color:${escapeHtml(t.color || "#6b7280")}">
          <span class="tag-pill tag-pill-compact">${escapeHtml(t.name)}</span>
        </button>`
        )
        .join("");
      list.querySelectorAll(".tag-select-chip").forEach((btn) => {
        btn.addEventListener("mousedown", (e) => e.preventDefault());
        btn.addEventListener("click", () => bindTag(btn.dataset.tagId));
      });
    } catch (err) {
      if (list) list.innerHTML = `<p class="tag-select-loading">${escapeHtml(err.message)}</p>`;
    }
  }

  async function onFabClick() {
    ensureUi();
    if (!lastChunkIds.length) {
      lastChunkIds = collectChunkIdsFromSelection();
    }
    if (!lastChunkIds.length) {
      showError?.("未能识别选中的日记片段");
      return;
    }
    pendingChunkIds = [...lastChunkIds];
    await showRecentPop();
  }

  /** 外部传入 chunk_ids，复用同一套「最近 tag / 更多」UI */
  async function openForChunks(ids) {
    const cleaned = [...new Set((ids || []).map(String).filter(Boolean))];
    if (!cleaned.length) {
      showError?.("请先选择要添加的片段");
      return;
    }
    lastChunkIds = cleaned;
    pendingChunkIds = cleaned;
    await showRecentPop();
  }

  async function bindTag(tagId) {
    const ids = pendingChunkIds.length ? pendingChunkIds : lastChunkIds;
    if (!tagId || !ids.length) return;
    try {
      const res = await api(`/tags/${encodeURIComponent(tagId)}/bind`, {
        method: "POST",
        body: JSON.stringify({ chunk_ids: ids }),
      });
      const name = res.tag?.name || "tag";
      showError?.(`已绑定到「${name}」（${res.bound?.length || ids.length} 个片段）`);
      pendingChunkIds = [];
      hideFab();
      window.getSelection()?.removeAllRanges();
      document.dispatchEvent(
        new CustomEvent("selection-tag-bound", {
          detail: { tagId, chunkIds: ids },
        })
      );
    } catch (err) {
      showError?.(err.message || "绑定失败");
    }
  }

  function onMoreClick() {
    const ids = pendingChunkIds.length ? pendingChunkIds : lastChunkIds;
    pendingChunkIds = [...ids];
    hideFab();
    if (typeof ExplorePage !== "undefined") {
      ExplorePage.openTagPickerModal({ pendingChunkIds: [...pendingChunkIds] });
    }
  }

  function getPendingChunkIds() {
    return [...pendingChunkIds];
  }

  function setPendingChunkIds(ids) {
    pendingChunkIds = (ids || []).map(String).filter(Boolean);
  }

  function clearPending() {
    pendingChunkIds = [];
  }

  function bind() {
    if (bound) return;
    bound = true;
    ensureUi();
    document.addEventListener("selectionchange", onSelectionChange);
    document.addEventListener("mousedown", (e) => {
      if (!pop || pop.hidden) return;
      if (pop.contains(e.target) || fab?.contains(e.target)) return;
      pop.hidden = true;
    });
  }

  return {
    bind,
    bindTag,
    openForChunks,
    getPendingChunkIds,
    setPendingChunkIds,
    clearPending,
    collectChunkIdsFromSelection,
  };
})();
