/**
 * 界面字体：只读写 data-font / localStorage / --font，不触及业务逻辑。
 * 默认 twkai（全字库正楷体）。
 */
(function () {
  const KEY = "memory-assistant-font";
  const FONTS = {
    twkai: {
      id: "twkai",
      label: "正楷",
      desc: "全字库正楷体（默认）",
      stack: '"TW-Kai", "TW-Kai-98", "全字庫正楷體", "DFKai-SB", "KaiTi", "標楷體", serif',
      google: null,
    },
    songti: {
      id: "songti",
      label: "宋体",
      desc: "思源宋体 / 明朝",
      stack: '"Noto Serif SC", "Source Han Serif SC", "Songti SC", "SimSun", serif',
      google: "Noto+Serif+SC:wght@400;500;600",
    },
    heiti: {
      id: "heiti",
      label: "黑体",
      desc: "思源黑体 / 无衬线",
      stack: '"Noto Sans SC", "Source Han Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif',
      google: "Noto+Sans+SC:wght@400;500;600",
    },
    kaiti: {
      id: "kaiti",
      label: "楷体",
      desc: "系统楷体",
      stack: '"KaiTi", "STKaiti", "DFKai-SB", "標楷體", "TW-Kai", serif',
      google: null,
    },
    fangsong: {
      id: "fangsong",
      label: "仿宋",
      desc: "系统仿宋",
      stack: '"FangSong", "STFangsong", "仿宋", serif',
      google: null,
    },
    system: {
      id: "system",
      label: "系统",
      desc: "系统默认 UI 字体",
      stack: 'system-ui, -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
      google: null,
    },
  };

  const IDS = Object.keys(FONTS);
  const loadedGoogle = new Set();

  function normalize(id) {
    return FONTS[id] ? id : "twkai";
  }

  function current() {
    try {
      return normalize(localStorage.getItem(KEY) || "twkai");
    } catch (_) {
      return "twkai";
    }
  }

  function ensureGoogle(font) {
    if (!font.google || loadedGoogle.has(font.google)) return;
    const href =
      "https://fonts.googleapis.com/css2?family=" + font.google + "&display=swap";
    if (document.querySelector('link[data-font-google="' + font.id + '"]')) {
      loadedGoogle.add(font.google);
      return;
    }
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = href;
    link.setAttribute("data-font-google", font.id);
    document.head.appendChild(link);
    loadedGoogle.add(font.google);
  }

  function syncUi(id) {
    document.querySelectorAll("[data-font-option]").forEach((el) => {
      const on = el.getAttribute("data-font-option") === id;
      el.classList.toggle("is-active", on);
      if (el.tagName === "BUTTON") {
        el.setAttribute("aria-pressed", on ? "true" : "false");
      }
    });
  }

  function apply(id, persist) {
    const fid = normalize(id);
    const font = FONTS[fid];
    ensureGoogle(font);
    document.documentElement.setAttribute("data-font", fid);
    document.documentElement.style.setProperty("--font", font.stack);
    if (persist !== false) {
      try {
        localStorage.setItem(KEY, fid);
      } catch (_) {
        /* ignore */
      }
    }
    syncUi(fid);
  }

  apply(current(), false);

  function onReady(fn) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", fn, { once: true });
    } else {
      fn();
    }
  }

  onReady(() => {
    syncUi(current());
    document.addEventListener("click", (ev) => {
      const btn = ev.target.closest("[data-font-option]");
      if (!btn) return;
      apply(btn.getAttribute("data-font-option"), true);
    });
  });

  window.FontPicker = {
    apply,
    current,
    fonts: IDS.map((id) => ({
      id,
      label: FONTS[id].label,
      desc: FONTS[id].desc,
    })),
  };
})();
