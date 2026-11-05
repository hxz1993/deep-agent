(function initG2Frame(global) {
  "use strict";
  const CHANNEL = "deepagent-g2";
  const token = document.currentScript?.dataset.token;
  if (!token || global.parent === global) return;
  const notify = type => global.parent.postMessage({ channel: CHANNEL, type, token }, "*");
  let started = false, disposed = false, chart;

  function tooltip(_event, { title, items = [] }) {
    const root = document.createElement("div");
    root.className = "g2-safe-tooltip";
    if (title !== undefined && title !== null && String(title) !== "") {
      const heading = document.createElement("div");
      heading.className = "g2-safe-tooltip-title";
      heading.textContent = String(title);
      root.appendChild(heading);
    }
    for (const item of items) {
      const row = document.createElement("div"), name = document.createElement("span"), value = document.createElement("span");
      row.className = "g2-safe-tooltip-row";
      name.textContent = String(item.name ?? item.field ?? "");
      value.textContent = item.value === undefined || item.value === null ? "—" : String(item.value);
      row.append(name, value); root.appendChild(row);
    }
    return root;
  }

  function safeOptions(spec) {
    function theme(value) {
      if (value === undefined || value === null) return { type: global.DeepAgentG2Theme.name };
      if (typeof value === "object" && !Array.isArray(value)) return { ...value, type: value.type ?? global.DeepAgentG2Theme.name };
      return value;
    }
    // Preserve mark-level title/items/field tooltip definitions and data precision.
    function visit(node) {
      if (!node || typeof node !== "object") return node;
      const result = { ...node, animate: false };
      if (node.interaction !== false) {
        const interaction = node.interaction && typeof node.interaction === "object" ? node.interaction : {};
        result.interaction = { ...interaction };
        if (interaction.tooltip !== false) {
          const options = interaction.tooltip && typeof interaction.tooltip === "object" ? interaction.tooltip : {};
          result.interaction.tooltip = { ...options, render: tooltip };
        }
      }
      if (node.theme !== undefined) result.theme = theme(node.theme);
      if (Array.isArray(node.children)) result.children = node.children.map(visit);
      return result;
    }
    return { ...visit(spec), width: undefined, height: 300, autoFit: true, theme: theme(spec.theme) };
  }

  global.addEventListener("message", async event => {
    const data = event.data;
    if (started || disposed || event.source !== global.parent || !data || data.channel !== CHANNEL || data.token !== token || data.type !== "init") return;
    started = true;
    try {
      if (!global.G2?.Chart || !global.DeepAgentG2Theme || !data.spec || typeof data.spec !== "object" || Array.isArray(data.spec)) throw new Error("G2 unavailable");
      chart = new global.G2.Chart({ container: document.getElementById("chart"), autoFit: true, height: 300 });
      chart.options(safeOptions(data.spec));
      await chart.render();
      if (!disposed) notify("rendered");
    } catch (_) {
      chart?.destroy(); chart = null;
      if (!disposed) notify("error");
    }
  });
  global.addEventListener("pagehide", () => { disposed = true; chart?.destroy(); chart = null; }, { once: true });
  notify("ready");
})(window);
