/**
 * 色系切换：只读写 data-theme / localStorage，不触及业务逻辑。
 */
(function () {
  const KEY = "memory-assistant-theme";
  const THEMES = ["paper", "linen", "sage", "ink", "beni", "miyabi"];
  const LEGACY = {
    moon: "paper",
    sakura: "linen",
    mist: "sage",
    aurora: "ink",
  };

  function normalize(theme) {
    const mapped = LEGACY[theme] || theme;
    return THEMES.includes(mapped) ? mapped : "paper";
  }

  function current() {
    try {
      return normalize(localStorage.getItem(KEY) || "paper");
    } catch (_) {
      return "paper";
    }
  }

  function syncUi(theme) {
    document.querySelectorAll("[data-theme-option]").forEach((el) => {
      const on = el.getAttribute("data-theme-option") === theme;
      el.classList.toggle("is-active", on);
      if (el.tagName === "BUTTON" || el.getAttribute("role") === "button") {
        el.setAttribute("aria-pressed", on ? "true" : "false");
      }
    });
  }

  function apply(theme, persist) {
    const t = normalize(theme);
    document.documentElement.setAttribute("data-theme", t);
    if (persist !== false) {
      try {
        localStorage.setItem(KEY, t);
      } catch (_) {
        /* ignore */
      }
    }
    syncUi(t);
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
      const btn = ev.target.closest("[data-theme-option]");
      if (!btn) return;
      apply(btn.getAttribute("data-theme-option"), true);
    });
  });

  window.ThemePicker = { apply, current, themes: THEMES.slice() };
})();
