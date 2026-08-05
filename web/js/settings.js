/**
 * 设置页：白名单参数编辑（密钥 → .env，其余 → user_settings overlay）。
 */

const SettingsPage = (() => {
  let bound = false;
  let loaded = false;
  let dirty = false;
  let saving = false;
  let data = null;

  function $(sel) {
    return document.querySelector(sel);
  }

  function escapeHtml(s) {
    const d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  function setStatus(msg, kind) {
    const el = $("#settings-status");
    if (!el) return;
    el.textContent = msg || "";
    el.dataset.kind = kind || "";
  }

  function setDirty(v) {
    dirty = !!v;
    const btn = $("#settings-save");
    if (btn) btn.disabled = !dirty || saving;
  }

  function fieldValue(fid) {
    const v = data?.values?.[fid];
    if (v && typeof v === "object" && "masked" in v) {
      return v;
    }
    return v;
  }

  function renderField(field) {
    const fid = field.id;
    const val = fieldValue(fid);
    const desc = field.description
      ? `<p class="settings-field-desc">${escapeHtml(field.description)}</p>`
      : "";
    const idAttr = `settings-field-${fid.replace(/\./g, "-")}`;

    if (field.type === "secret") {
      const masked = val?.masked || "";
      const hint = val?.set
        ? `当前已设置：${escapeHtml(masked)}`
        : "当前未设置";
      return `
        <label class="settings-field" data-fid="${escapeHtml(fid)}">
          <span class="settings-field-label">${escapeHtml(field.label)}</span>
          ${desc}
          <input
            type="password"
            id="${idAttr}"
            data-fid="${escapeHtml(fid)}"
            data-type="secret"
            autocomplete="off"
            placeholder="留空则不修改"
          />
          <span class="settings-field-hint">${hint}</span>
        </label>`;
    }

    if (field.type === "bool") {
      const checked = val ? "checked" : "";
      return `
        <label class="settings-field settings-field-bool" data-fid="${escapeHtml(fid)}">
          <span class="settings-field-row">
            <input
              type="checkbox"
              id="${idAttr}"
              data-fid="${escapeHtml(fid)}"
              data-type="bool"
              ${checked}
            />
            <span class="settings-field-label">${escapeHtml(field.label)}</span>
          </span>
          ${desc}
        </label>`;
    }

    if (field.type === "enum") {
      const opts = (field.options || [])
        .map((o) => {
          const sel = String(val) === String(o) ? "selected" : "";
          return `<option value="${escapeHtml(o)}" ${sel}>${escapeHtml(o)}</option>`;
        })
        .join("");
      return `
        <label class="settings-field" data-fid="${escapeHtml(fid)}">
          <span class="settings-field-label">${escapeHtml(field.label)}</span>
          ${desc}
          <select id="${idAttr}" data-fid="${escapeHtml(fid)}" data-type="enum">${opts}</select>
        </label>`;
    }

    if (field.type === "text") {
      return `
        <label class="settings-field" data-fid="${escapeHtml(fid)}">
          <span class="settings-field-label">${escapeHtml(field.label)}</span>
          ${desc}
          <textarea
            id="${idAttr}"
            data-fid="${escapeHtml(fid)}"
            data-type="text"
            rows="8"
          >${escapeHtml(val ?? "")}</textarea>
        </label>`;
    }

    const inputType = field.type === "int" ? "number" : "text";
    const min = field.min != null ? `min="${field.min}"` : "";
    const max = field.max != null ? `max="${field.max}"` : "";
    return `
      <label class="settings-field" data-fid="${escapeHtml(fid)}">
        <span class="settings-field-label">${escapeHtml(field.label)}</span>
        ${desc}
        <input
          type="${inputType}"
          id="${idAttr}"
          data-fid="${escapeHtml(fid)}"
          data-type="${escapeHtml(field.type)}"
          value="${escapeHtml(val ?? "")}"
          ${min}
          ${max}
        />
      </label>`;
  }

  function render() {
    const body = $("#settings-body");
    if (!body || !data) return;

    const byGroup = {};
    for (const f of data.fields || []) {
      (byGroup[f.group] || (byGroup[f.group] = [])).push(f);
    }

    const sections = (data.groups || [])
      .map((g) => {
        const fields = byGroup[g.id] || [];
        if (!fields.length) return "";
        const hint = g.hint
          ? `<p class="settings-group-hint">${escapeHtml(g.hint)}</p>`
          : "";
        return `
          <section class="settings-group" data-group="${escapeHtml(g.id)}">
            <h2 class="settings-group-title">${escapeHtml(g.label)}</h2>
            ${hint}
            <div class="settings-group-fields">
              ${fields.map(renderField).join("")}
            </div>
          </section>`;
      })
      .join("");

    body.innerHTML = sections || `<p class="settings-loading">暂无可用设置项</p>`;
    bindFieldEvents();
    setDirty(false);
  }

  function collectValues() {
    const values = {};
    const body = $("#settings-body");
    if (!body) return values;
    body.querySelectorAll("[data-fid][data-type]").forEach((el) => {
      const fid = el.dataset.fid;
      const type = el.dataset.type;
      if (!fid) return;
      if (type === "bool") {
        values[fid] = !!el.checked;
      } else if (type === "int") {
        values[fid] = el.value === "" ? null : Number(el.value);
      } else if (type === "secret") {
        values[fid] = el.value;
      } else {
        values[fid] = el.value;
      }
    });
    return values;
  }

  function bindFieldEvents() {
    const body = $("#settings-body");
    if (!body) return;
    body.querySelectorAll("input, textarea, select").forEach((el) => {
      el.addEventListener("input", () => setDirty(true));
      el.addEventListener("change", () => setDirty(true));
    });
  }

  async function load() {
    const body = $("#settings-body");
    if (body) body.innerHTML = `<p class="settings-loading">加载设置中…</p>`;
    setStatus("");
    try {
      data = await api("/settings");
      loaded = true;
      render();
    } catch (err) {
      loaded = false;
      if (body) {
        body.innerHTML = `<p class="settings-loading">加载失败：${escapeHtml(err.message)}</p>`;
      }
      setStatus(err.message || "加载失败", "error");
    }
  }

  async function save() {
    if (saving || !dirty) return;
    saving = true;
    setDirty(true);
    const btn = $("#settings-save");
    if (btn) {
      btn.disabled = true;
      btn.textContent = "保存中…";
    }
    setStatus("正在保存…");
    try {
      const values = collectValues();
      // 密钥空串表示不修改：仍传给后端（后端会跳过）
      data = await api("/settings", {
        method: "PUT",
        body: JSON.stringify({ values }),
      });
      render();
      setStatus("已保存，部分项立即生效", "ok");
      if (typeof showError === "function") {
        showError("设置已保存");
      }
      setTimeout(() => {
        if ($("#settings-status")?.textContent === "已保存，部分项立即生效") {
          setStatus("");
        }
      }, 3500);
    } catch (err) {
      setStatus(err.message || "保存失败", "error");
      setDirty(true);
    } finally {
      saving = false;
      if (btn) btn.textContent = "保存";
      const b = $("#settings-save");
      if (b) b.disabled = !dirty;
    }
  }

  function bind() {
    if (bound) return;
    bound = true;
    $("#settings-save")?.addEventListener("click", () => {
      save().catch((e) => console.warn(e));
    });
  }

  async function show() {
    bind();
    await load();
  }

  return { show, reload: load };
})();

window.SettingsPage = SettingsPage;
