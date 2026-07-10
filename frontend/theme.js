"use strict";

(function () {
  const STORAGE_KEY = "da_theme";

  const THEMES = [
    { id: "dark", label: "深色" },
    { id: "light", label: "浅色" },
    { id: "paper", label: "护眼（暖色）" },
    { id: "contrast", label: "高对比" },
  ];

  const HLJS = {
    dark: "./vendor/highlight-github-dark.min.css",
    light: "./vendor/highlight-github.min.css",
  };

  function isLightTheme(id) {
    return id === "light" || id === "paper" || id === "contrast";
  }

  function applyTheme(themeId) {
    const id = THEMES.some((t) => t.id === themeId) ? themeId : "dark";
    document.documentElement.setAttribute("data-theme", id);
    localStorage.setItem(STORAGE_KEY, id);

    const hljsLink = document.getElementById("hljsTheme");
    if (hljsLink) {
      hljsLink.href = isLightTheme(id) ? HLJS.light : HLJS.dark;
    }

    const select = document.getElementById("themeSelect");
    if (select && select.value !== id) select.value = id;
  }

  function initThemeSelect(selectEl) {
    if (!selectEl) return;
    selectEl.innerHTML = THEMES.map(
      (t) => `<option value="${t.id}">${t.label}</option>`
    ).join("");
    const saved = localStorage.getItem(STORAGE_KEY) || "dark";
    selectEl.value = THEMES.some((t) => t.id === saved) ? saved : "dark";
    selectEl.addEventListener("change", () => applyTheme(selectEl.value));
  }

  function boot() {
    const saved = localStorage.getItem(STORAGE_KEY) || "dark";
    applyTheme(saved);
    initThemeSelect(document.getElementById("themeSelect"));
  }

  window.DATheme = { apply: applyTheme, initSelect: initThemeSelect, themes: THEMES };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
