function isManagedBuiltinCapability(capability) {
  return capability?.source === "builtin" && capability?.category === "builtin";
}

function toolCatalogLabel(item) {
  return item.code === "builtin:sandbox" ? "沙箱" : item.label || item.code;
}

function toolCatalogCard(item, selected) {
  return `<button type="button" class="skill-list-item${selected ? " active" : ""}" data-tool-catalog-code="${escapeHtml(item.code)}" aria-pressed="${selected}">
    <span class="skill-list-card-head">
      <span class="skill-list-icon"><i data-lucide="hammer" aria-hidden="true"></i></span>
      <span class="skill-list-copy"><span class="skill-list-title"><strong>${escapeHtml(toolCatalogLabel(item))}</strong></span><code class="skill-list-code">${escapeHtml(item.code)}</code></span>
      <span class="skill-list-signals"><span class="skill-list-count">${(item.tools || []).length}</span></span>
    </span>
  </button>`;
}

function toolCatalogDetails(item, runtime = false) {
  const tools = item.tools || [];
  const columns = runtime ? 4 : 2;
  return `<header class="tool-catalog-detail-header">
      <div><h3>${escapeHtml(toolCatalogLabel(item))}</h3><code>${escapeHtml(item.code)}</code><span>${tools.length} 个工具</span></div>
      ${item.description ? `<p>${escapeHtml(item.description)}</p>` : ""}
    </header>
    <div class="table-wrap console-list-a">
      <table class="data-table tool-catalog-table${runtime ? " tool-catalog-runtime-table" : ""}">
        <thead><tr><th>工具名称</th>${runtime ? "<th>实现来源</th><th>自动注入条件</th>" : ""}<th>说明</th></tr></thead>
        <tbody>${tools.length ? tools.map((tool) => `<tr>
          <td><code>${escapeHtml(tool.name || "—")}</code></td>
          ${runtime ? `<td><span class="badge">${escapeHtml(tool.source || "—")}</span></td><td>${escapeHtml(tool.injection_condition || "—")}</td>` : ""}
          <td>${escapeHtml(tool.description || "—")}</td>
        </tr>`).join("") : `<tr><td colspan="${columns}" class="empty-cell">暂无工具</td></tr>`}</tbody>
      </table>
    </div>`;
}

const toolCatalogGroups = [
  { label: "预制工具", items: [], status: "正在加载内置工具…", runtime: false },
  { label: "运行时工具", items: [], status: "正在加载系统工具…", runtime: true },
];
let selectedToolCode = "";

function renderToolCatalog() {
  const host = document.getElementById("builtinToolCatalog");
  if (!host) return;
  const items = toolCatalogGroups.flatMap((group) => group.items);
  const selected = items.find((item) => item.code === selectedToolCode) || items[0];
  selectedToolCode = selected?.code || "";
  const selectedGroup = toolCatalogGroups.find((group) => group.items.includes(selected));
  host.innerHTML = `<div class="tool-catalog-workspace">
    <aside class="skill-list-pane tool-catalog-list" aria-label="内置工具分组">${toolCatalogGroups.map((group) => `
      <section class="tool-catalog-group" aria-label="${group.label}">
        <h3 class="tool-catalog-group-heading">${group.label}<span>${group.items.length}</span></h3>
        ${group.items.length ? group.items.map((item) => toolCatalogCard(item, item.code === selectedToolCode)).join("") : `<div class="skill-list-state">${escapeHtml(group.status || "暂无工具")}</div>`}
      </section>`).join("")}</aside>
    <section class="tool-catalog-detail" aria-label="当前工具详情">${selected ? toolCatalogDetails(selected, selectedGroup.runtime) : '<div class="skill-list-state">请选择左侧工具</div>'}</section>
  </div>`;
  host.querySelectorAll("[data-tool-catalog-code]").forEach((button) => {
    button.addEventListener("click", () => {
      const scrollTop = host.querySelector(".tool-catalog-list").scrollTop;
      selectedToolCode = button.dataset.toolCatalogCode;
      renderToolCatalog();
      host.querySelector(".tool-catalog-list").scrollTop = scrollTop;
      Array.from(host.querySelectorAll("[data-tool-catalog-code]")).find((next) => next.dataset.toolCatalogCode === selectedToolCode)?.focus({ preventScroll: true });
    });
  });
  window.lucide?.createIcons();
}

function updateToolCatalogGroup(runtime, items, status = "") {
  const group = toolCatalogGroups.find((group) => group.runtime === runtime);
  group.items = items;
  group.status = status;
  renderToolCatalog();
}

function renderBuiltinCapabilities(capabilities) {
  const builtins = (capabilities || []).filter(isManagedBuiltinCapability)
    .sort((left, right) => left.code.localeCompare(right.code));
  updateToolCatalogGroup(false, builtins);
}

const builtinToolsAdmin = {
  async refresh({ reload = false } = {}) {
    const host = document.getElementById("builtinToolCatalog");
    if (!host) return;
    renderToolCatalog();
    try {
      if (reload || !(state.capabilities || []).length) await loadCapabilities();
      renderBuiltinCapabilities(state.capabilities);
    } catch (error) {
      updateToolCatalogGroup(false, [], error instanceof Error ? error.message : String(error));
    }
  },
};

window.builtinToolsAdmin = builtinToolsAdmin;
window.__BuiltinToolsUI = { isManagedBuiltinCapability, renderBuiltinCapabilities, toolCatalogCard, toolCatalogDetails, renderToolCatalog, updateToolCatalogGroup };
