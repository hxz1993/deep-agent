/* One navigation entry; each view keeps its existing data and editing controller. */
(function () {
  const descriptions = {
    ui: "管理 UI 交互工具包及其中的工具，由 Agent 关联使用。",
    builtin: "查看预制工具与运行时自动注入的工具。",
  };
  let view = "ui";
  const pending = new Map();
  function render() {
    document.querySelectorAll("[data-tools-view]").forEach((button) => {
      const selected = button.dataset.toolsView === view;
      button.classList.toggle("active", selected);
      button.setAttribute("aria-selected", String(selected));
      button.tabIndex = selected ? 0 : -1;
    });
    document.querySelectorAll("[data-tools-panel]").forEach((panel) => {
      panel.hidden = panel.dataset.toolsPanel !== view;
    });
    document.getElementById("newUiToolPackageBtn").hidden = view !== "ui";
    document.getElementById("toolsViewDescription").textContent = descriptions[view];
  }
  function refresh() {
    render();
    const requestedView = view;
    if (pending.has(requestedView)) return pending.get(requestedView);
    const task = Promise.resolve().then(() => requestedView === "ui"
      ? window.uiToolsAdmin?.refresh()
      : Promise.all([window.builtinToolsAdmin?.refresh(), window.systemToolsAdmin?.refresh()]));
    pending.set(requestedView, task);
    const cleanup = () => pending.delete(requestedView);
    task.then(cleanup, cleanup);
    return task;
  }
  function select(next, { focus = false } = {}) {
    if (!Object.hasOwn(descriptions, next)) return Promise.resolve();
    view = next;
    render();
    if (focus) document.querySelector(`[data-tools-view="${view}"]`)?.focus();
    return refresh();
  }
  function init() {
    const root = document.getElementById("panel-tools");
    if (!root || root.dataset.toolsBound) return;
    root.dataset.toolsBound = "true";
    const tabs = Array.from(root.querySelectorAll("[data-tools-view]"));
    tabs.forEach((button, index) => {
      button.addEventListener("click", () => select(button.dataset.toolsView));
      button.addEventListener("keydown", (event) => {
        const target = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1
          : ["ArrowLeft", "ArrowRight"].includes(event.key) ? (index + 1) % tabs.length : -1;
        if (target < 0) return;
        event.preventDefault();
        select(tabs[target].dataset.toolsView, { focus: true });
      });
    });
    render();
  }
  window.toolsAdmin = { init, select, refresh, currentView: () => view };
  document.addEventListener("DOMContentLoaded", init);
})();
