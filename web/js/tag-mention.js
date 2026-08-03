/**
 * @tag名 全局提及渲染：将文本中的 @精确 tag 名渲染为带颜色的小方框。
 */
const TagMention = (() => {
  /** @type {Map<string, { id: string, name: string, color: string }>} */
  const byName = new Map();
  /** @type {Map<string, string>} id → name（改名时清理旧键） */
  const idToName = new Map();

  function escapeHtml(s) {
    const d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  function escapeRegExp(s) {
    return String(s).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  function sortedNames() {
    return [...byName.keys()].sort((a, b) => b.length - a.length || a.localeCompare(b));
  }

  function buildPattern() {
    const names = sortedNames();
    if (!names.length) return null;
    return new RegExp(`@(?:${names.map(escapeRegExp).join("|")})`, "g");
  }

  function register(tag) {
    if (!tag || !tag.name) return;
    const name = String(tag.name).trim();
    if (!name) return;
    const id = String(tag.id || "");
    if (id && idToName.has(id)) {
      const old = idToName.get(id);
      if (old && old !== name) byName.delete(old);
    }
    const entry = {
      id,
      name,
      color: String(tag.color || "#6b7280"),
    };
    byName.set(name, entry);
    if (id) idToName.set(id, name);
  }

  function unregister(tagId) {
    const id = String(tagId || "");
    if (!id || !idToName.has(id)) return;
    const name = idToName.get(id);
    idToName.delete(id);
    if (name) byName.delete(name);
  }

  function setAll(items) {
    byName.clear();
    idToName.clear();
    for (const t of items || []) register(t);
  }

  async function refresh() {
    if (typeof api !== "function") return;
    try {
      const data = await api("/tags");
      setAll(data.items || []);
    } catch (err) {
      console.warn("TagMention.refresh failed", err);
    }
  }

  function pillEl(name, color) {
    const span = document.createElement("span");
    span.className = "tag-mention";
    span.style.setProperty("--tag-color", color || "#6b7280");
    span.dataset.tagName = name;
    span.textContent = `@${name}`;
    return span;
  }

  function pillHtml(name, color) {
    const c = escapeHtml(color || "#6b7280");
    const n = escapeHtml(name);
    return `<span class="tag-mention" style="--tag-color:${c}" data-tag-name="${n}">@${n}</span>`;
  }

  /** 纯文本 → HTML（转义 + 提及方框） */
  function decoratePlainToHtml(text) {
    const s = String(text || "");
    const pattern = buildPattern();
    if (!pattern || !s.includes("@")) return escapeHtml(s);
    let out = "";
    let last = 0;
    let m;
    pattern.lastIndex = 0;
    while ((m = pattern.exec(s))) {
      out += escapeHtml(s.slice(last, m.index));
      const name = m[0].slice(1);
      const meta = byName.get(name);
      out += pillHtml(name, meta?.color);
      last = m.index + m[0].length;
    }
    out += escapeHtml(s.slice(last));
    return out;
  }

  /** 在已有 DOM 子树的文本节点中就地替换 @tag */
  function decorateElement(root) {
    if (!root) return;
    const pattern = buildPattern();
    if (!pattern) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        const p = node.parentElement;
        if (!p) return NodeFilter.FILTER_REJECT;
        if (p.closest(".tag-mention")) return NodeFilter.FILTER_REJECT;
        if (p.closest("script, style, textarea, code, pre")) {
          return NodeFilter.FILTER_REJECT;
        }
        if (!node.nodeValue || !node.nodeValue.includes("@")) {
          return NodeFilter.FILTER_REJECT;
        }
        return NodeFilter.FILTER_ACCEPT;
      },
    });
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    for (const node of nodes) {
      const text = node.nodeValue || "";
      pattern.lastIndex = 0;
      if (!pattern.test(text)) continue;
      pattern.lastIndex = 0;
      const frag = document.createDocumentFragment();
      let last = 0;
      let m;
      while ((m = pattern.exec(text))) {
        if (m.index > last) {
          frag.appendChild(document.createTextNode(text.slice(last, m.index)));
        }
        const name = m[0].slice(1);
        const meta = byName.get(name);
        frag.appendChild(pillEl(name, meta?.color));
        last = m.index + m[0].length;
      }
      if (last < text.length) {
        frag.appendChild(document.createTextNode(text.slice(last)));
      }
      node.parentNode?.replaceChild(frag, node);
    }
  }

  /** markdown/HTML 字符串装饰后返回 HTML */
  function decorateHtml(html) {
    const tmp = document.createElement("div");
    tmp.innerHTML = html || "";
    decorateElement(tmp);
    return tmp.innerHTML;
  }

  return {
    register,
    unregister,
    setAll,
    refresh,
    decorateElement,
    decorateHtml,
    decoratePlainToHtml,
  };
})();

window.TagMention = TagMention;
