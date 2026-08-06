/**
 * 设置页：白名单参数编辑（密钥 → .env，其余 → user_settings overlay）。
 */

const SettingsPage = (() => {
  let bound = false;
  let loaded = false;
  let dirty = false;
  let saving = false;
  let data = null;
  let probeBusy = false;

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

  function modelCatalog(field) {
    const list = field.catalog || data?.model_catalog || [];
    return Array.isArray(list) ? list : [];
  }

  function renderModelSelect(field) {
    const fid = field.id;
    const current = String(fieldValue(fid) || "").trim();
    const catalog = modelCatalog(field);
    const byFamily = {};
    for (const m of catalog) {
      const fam = m.family || "其它";
      (byFamily[fam] || (byFamily[fam] = [])).push(m);
    }
    const known = new Set(catalog.map((m) => m.id));
    if (current && !known.has(current)) {
      (byFamily["当前自定义"] || (byFamily["当前自定义"] = [])).push({
        id: current,
        label: current,
        family: "当前自定义",
        hint: "不在精选列表中，仍可继续使用",
      });
    }

    const families = Object.keys(byFamily);
    const groupsHtml = families
      .map((fam) => {
        const cards = byFamily[fam]
          .map((m) => {
            const checked = m.id === current ? "checked" : "";
            const active = m.id === current ? "is-active" : "";
            return `
              <label class="settings-model-card ${active}" data-model-id="${escapeHtml(m.id)}">
                <input
                  type="radio"
                  name="settings-answer-model"
                  data-fid="${escapeHtml(fid)}"
                  data-type="model_select"
                  value="${escapeHtml(m.id)}"
                  ${checked}
                />
                <span class="settings-model-card-body">
                  <span class="settings-model-name">${escapeHtml(m.label || m.id)}</span>
                  <span class="settings-model-id">${escapeHtml(m.id)}</span>
                  <span class="settings-model-hint">${escapeHtml(m.hint || "")}</span>
                  <span class="settings-model-probe" data-probe-for="${escapeHtml(m.id)}"></span>
                </span>
              </label>`;
          })
          .join("");
        return `
          <div class="settings-model-family">
            <h3 class="settings-model-family-title">${escapeHtml(fam)}</h3>
            <div class="settings-model-grid">${cards}</div>
          </div>`;
      })
      .join("");

    return `
      <div class="settings-field settings-field-models" data-fid="${escapeHtml(fid)}">
        <div class="settings-field-label-row">
          <span class="settings-field-label">${escapeHtml(field.label)}</span>
          <button type="button" class="settings-probe-btn" id="settings-probe-selected">
            测试当前选中
          </button>
        </div>
        ${
          field.description
            ? `<p class="settings-field-desc">${escapeHtml(field.description)}</p>`
            : ""
        }
        <div class="settings-model-catalog">${groupsHtml}</div>
      </div>`;
  }

  function renderField(field) {
    const fid = field.id;
    const val = fieldValue(fid);
    const desc = field.description
      ? `<p class="settings-field-desc">${escapeHtml(field.description)}</p>`
      : "";
    const idAttr = `settings-field-${fid.replace(/\./g, "-")}`;

    if (field.type === "model_select") {
      return renderModelSelect(field);
    }

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
      } else if (type === "model_select") {
        if (el.type === "radio") {
          if (el.checked) values[fid] = el.value;
        } else {
          values[fid] = el.value;
        }
      } else {
        values[fid] = el.value;
      }
    });
    return values;
  }

  function selectedModelId() {
    const el = document.querySelector(
      'input[name="settings-answer-model"][data-type="model_select"]:checked'
    );
    return el ? el.value : "";
  }

  function setProbeStatus(modelId, text, kind) {
    const el = document.querySelector(
      `.settings-model-probe[data-probe-for="${CSS.escape(modelId)}"]`
    );
    if (!el) return;
    el.textContent = text || "";
    el.dataset.kind = kind || "";
  }

  async function probeModel(modelId) {
    if (!modelId || probeBusy) return;
    probeBusy = true;
    const btn = $("#settings-probe-selected");
    if (btn) {
      btn.disabled = true;
      btn.textContent = "测试中…";
    }
    setProbeStatus(modelId, "探测中…", "pending");
    setStatus(`正在测试 ${modelId}…`);
    try {
      const res = await api("/settings/probe-model", {
        method: "POST",
        body: JSON.stringify({ model: modelId }),
      });
      const reply = (res.reply || "").trim();
      setProbeStatus(
        modelId,
        reply ? `可用 · ${reply}` : "可用",
        "ok"
      );
      setStatus(`${modelId} 可用`, "ok");
    } catch (err) {
      setProbeStatus(modelId, `失败 · ${err.message || "不可用"}`, "error");
      setStatus(err.message || "模型测试失败", "error");
    } finally {
      probeBusy = false;
      if (btn) {
        btn.disabled = false;
        btn.textContent = "测试当前选中";
      }
    }
  }

  function bindFieldEvents() {
    const body = $("#settings-body");
    if (!body) return;
    body.querySelectorAll("input, textarea, select").forEach((el) => {
      el.addEventListener("input", () => setDirty(true));
      el.addEventListener("change", () => {
        setDirty(true);
        if (el.dataset.type === "model_select" && el.type === "radio") {
          body.querySelectorAll(".settings-model-card").forEach((card) => {
            card.classList.toggle(
              "is-active",
              card.getAttribute("data-model-id") === el.value
            );
          });
        }
      });
    });
    $("#settings-probe-selected")?.addEventListener("click", () => {
      const mid = selectedModelId();
      if (!mid) {
        setStatus("请先选择一个模型", "error");
        return;
      }
      probeModel(mid).catch((e) => console.warn(e));
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
      data = await api("/settings", {
        method: "PUT",
        body: JSON.stringify({ values }),
      });
      render();
      const days = data?.values?.["default_recall_days"];
      if (typeof DateSelection !== "undefined" && days != null) {
        DateSelection.setDefaultDays(days);
        if (DateSelection.isUsingDefault?.()) {
          DateSelection.applyDefaultRecall(days);
          if (typeof window.persistActiveDates === "function") {
            window.persistActiveDates();
          }
          MiniDatePicker?.render?.();
        }
      }
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
