/**
 * 用户手动 Tag 领域对象：创建 / 绑定，以及创建后「是否用 tag 名检索」流程。
 */
class UserTag {
  /**
   * @param {object} data
   */
  constructor(data = {}) {
    this.id = data.id || "";
    this.name = data.name || "";
    this.color = data.color || "#6b7280";
    this.folder_id = data.folder_id ?? null;
    this.sort_order = data.sort_order || 0;
    this.last_used_at = data.last_used_at || "";
    this.use_count = data.use_count || 0;
    this.created_at = data.created_at || "";
    this.bind_count = data.bind_count;
  }

  /** @param {object} data */
  static from(data) {
    return new UserTag(data || {});
  }

  toJSON() {
    const out = {
      id: this.id,
      name: this.name,
      color: this.color,
      folder_id: this.folder_id,
      sort_order: this.sort_order,
      last_used_at: this.last_used_at,
      use_count: this.use_count,
      created_at: this.created_at,
    };
    if (this.bind_count != null) out.bind_count = this.bind_count;
    return out;
  }

  /**
   * 创建 tag；默认弹出「是否用 tag 名检索」。
   * @param {{ name: string, folder_id?: string|null, color?: string|null, offerSearch?: boolean }} opts
   */
  static async create(opts = {}) {
    const name = String(opts.name || "").trim();
    if (!name) throw new Error("tag 名称不能为空");
    const body = {
      name,
      folder_id: opts.folder_id ?? null,
      color: opts.color || null,
    };
    const data = await api("/tags", {
      method: "POST",
      body: JSON.stringify(body),
    });
    const tag = UserTag.from(data);
    if (opts.offerSearch !== false) {
      await tag.offerNameSearch();
    }
    return tag;
  }

  /**
   * 任意创建路径（含人物）在拿到 tag 后调用：询问是否检索 tag 名。
   */
  async offerNameSearch() {
    const name = (this.name || "").trim();
    if (!name) return false;
    const ok = window.confirm(
      `已创建 tag「${name}」。\n是否用该名称在日记原文中检索？`
    );
    if (!ok) return false;
    if (typeof ExplorePage === "undefined" || !ExplorePage.openNameGrepModal) {
      showError?.("检索界面未就绪");
      return false;
    }
    await ExplorePage.openNameGrepModal({
      query: name,
      tagId: this.id,
      tagName: name,
    });
    return true;
  }

  /** @param {string[]} chunkIds */
  async bind(chunkIds) {
    const ids = [...new Set((chunkIds || []).map(String).filter(Boolean))];
    if (!this.id || !ids.length) {
      throw new Error("缺少 tag 或片段");
    }
    const res = await api(`/tags/${encodeURIComponent(this.id)}/bind`, {
      method: "POST",
      body: JSON.stringify({ chunk_ids: ids }),
    });
    if (res.tag) {
      Object.assign(this, UserTag.from(res.tag));
    }
    return res;
  }

  /** @param {string[]} chunkIds */
  async unbind(chunkIds) {
    const ids = [...new Set((chunkIds || []).map(String).filter(Boolean))];
    if (!this.id || !ids.length) {
      throw new Error("缺少 tag 或片段");
    }
    const res = await api(`/tags/${encodeURIComponent(this.id)}/unbind`, {
      method: "POST",
      body: JSON.stringify({ chunk_ids: ids }),
    });
    if (res.tag) {
      Object.assign(this, UserTag.from(res.tag));
    }
    return res;
  }

  /**
   * 拉取本 tag 绑定的 chunk 列表。
   * @param {{ limit?: number }} [opts]
   */
  async listChunks(opts = {}) {
    if (!this.id) throw new Error("缺少 tag id");
    const limit = opts.limit || 80;
    const data = await api(
      `/tags/${encodeURIComponent(this.id)}/chunks?limit=${encodeURIComponent(limit)}`
    );
    if (data.tag) Object.assign(this, UserTag.from(data.tag));
    return data;
  }

  /**
   * 任意位置点击 tag：展示绑定 chunk +「进入和 xxx 的故事」侧栏（故事逻辑暂空）。
   */
  async openDetail() {
    if (!this.id) throw new Error("缺少 tag id");
    if (typeof ExplorePage === "undefined" || !ExplorePage.openTagDetailModal) {
      showError?.("Tag 详情界面未就绪");
      return;
    }
    await ExplorePage.openTagDetailModal({
      tagId: this.id,
      tagName: this.name,
      color: this.color,
    });
  }
}
