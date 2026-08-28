function isManagedBuiltinCapability(capability) {
  return (
    capability?.source === "builtin" &&
    capability?.category === "builtin"
  );
}

function builtinToolDetails(capability) {
  const tools = capability.tools || [];
  if (!tools.length) return "暂无工具";
  return `<details class="builtin-tool-list">
    <summary>${tools.length} 个工具</summary>
    <ul>${tools
      .map(
        (tool) =>
          `<li><strong>${escapeHtml(tool.name)}</strong>${
            tool.description ? ` — ${escapeHtml(tool.description)}` : ""
          }</li>`,
      )
      .join("")}</ul>
  </details>`;
}

function builtinCapabilityRow(capability) {
  const label = capability.label || capability.code;
  return `<tr>
    <td>
      <div class="builtin-tool-name">
        <strong>${escapeHtml(label)}</strong>
        <small>内置工具包</small>
      </div>
    </td>
    <td><code>${escapeHtml(capability.code)}</code></td>
    <td>${builtinToolDetails(capability)}</td>
    <td>${escapeHtml(capability.description || "—")}</td>
  </tr>`;
}

function renderBuiltinCapabilities(capabilities) {
  const tbody = document.getElementById("builtinToolsTableBody");
  if (!tbody) return;
  const builtins = (capabilities || [])
    .filter(isManagedBuiltinCapability)
    .sort((left, right) => left.code.localeCompare(right.code));
  if (!builtins.length) {
    tbody.innerHTML = `<tr><td colspan="4" class="empty-cell">暂无可展示的内置工具</td></tr>`;
    return;
  }
  tbody.innerHTML = builtins.map(builtinCapabilityRow).join("");
}

function renderBuiltinError(message) {
  const tbody = document.getElementById("builtinToolsTableBody");
  if (!tbody) return;
  tbody.innerHTML = `<tr><td colspan="4" class="empty-cell">
    <span class="configuration-error">${escapeHtml(message || "内置工具加载失败")}</span>
  </td></tr>`;
}

const builtinToolsAdmin = {
  async refresh({ reload = false } = {}) {
    const tbody = document.getElementById("builtinToolsTableBody");
    if (!tbody) return;
    tbody.innerHTML = `<tr><td colspan="4" class="empty-cell">正在加载内置工具…</td></tr>`;
    try {
      if (reload || !(state.capabilities || []).length) {
        await loadCapabilities();
      }
      renderBuiltinCapabilities(state.capabilities);
    } catch (error) {
      renderBuiltinError(error instanceof Error ? error.message : String(error));
    }
  },
};

window.builtinToolsAdmin = builtinToolsAdmin;
window.__BuiltinToolsUI = {
  builtinCapabilityRow,
  isManagedBuiltinCapability,
  renderBuiltinCapabilities,
};
