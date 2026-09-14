(function () {
  const THEMES = [
    { id: "dark", label: "深色" },
    { id: "light", label: "浅色" },
    { id: "paper", label: "护眼（暖色）" },
    { id: "contrast", label: "高对比" },
  ];

  function apply(themeId) {
    const id = THEMES.some((theme) => theme.id === themeId) ? themeId : "paper";
    document.documentElement.setAttribute("data-theme", id);
    localStorage.setItem("da_theme", id);
    const link = document.getElementById("hljsTheme");
    if (link) {
      const root = (window.__DEEP_AGENT_APP_ROOT__ || "/").replace(/\/$/, "");
      const file = ["light", "paper", "contrast"].includes(id)
        ? "highlight-github.min.css"
        : "highlight-github-dark.min.css";
      link.href = `${root}/vendor/${file}`;
    }
    const select = document.getElementById("themeSelect");
    const markdownLink = document.getElementById("markdownTheme");
    if (markdownLink) {
      const root = (window.__DEEP_AGENT_APP_ROOT__ || "/").replace(/\/$/, "");
      const variant = id === "dark" ? "dark" : "light";
      markdownLink.href = `${root}/vendor/github-markdown-${variant}.min.css`;
    }
    if (select && select.value !== id) select.value = id;
  }

  function initSelect(select) {
    if (!select) return;
    select.innerHTML = THEMES.map((theme) => `<option value="${theme.id}">${theme.label}</option>`).join("");
    select.value = localStorage.getItem("da_theme") || "paper";
    select.addEventListener("change", () => apply(select.value));
  }

  window.DATheme = { apply, initSelect, themes: THEMES };
  apply(localStorage.getItem("da_theme") || "paper");
  initSelect(document.getElementById("themeSelect"));
})();
