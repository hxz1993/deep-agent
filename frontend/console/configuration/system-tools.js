function systemToolRow(tool) {
  return `<tr>
    <td><code>${escapeHtml(tool.name || "—")}</code></td>
    <td><span class="badge">${escapeHtml(tool.source || "—")}</span></td>
    <td>${escapeHtml(tool.injection_condition || "—")}</td>
    <td>${escapeHtml(tool.description || "—")}</td>
  </tr>`;
}

function systemToolDomain(domain) {
  const tools = domain.tools || [];
  const description = domain.description || "该分组暂无补充说明。";
  const rows = tools.length
    ? tools.map(systemToolRow).join("")
    : `<tr><td colspan="4" class="empty-cell">该领域暂无系统工具</td></tr>`;
  return `<tr class="global-settings-group-row system-tool-group-row" data-system-tool-domain="${escapeHtml(domain.code || "")}">
      <th colspan="4"><span class="system-tool-group-label"><span>${escapeHtml(domain.label || domain.code || "未命名领域")}</span><code class="system-tool-group-code">${escapeHtml(domain.code || "runtime:unknown")}</code><span class="system-tool-group-help" tabindex="0" role="img" aria-label="${escapeHtml(description)}" data-tooltip="${escapeHtml(description)}"></span></span></th>
    </tr>${rows}`;
}

function mergeSystemToolDomains(domains) {
  const mcpCodes = new Set(["runtime:mcp-tool-search", "runtime:mcp-resource"]);
  const mcpDomains = (domains || []).filter((domain) => mcpCodes.has(domain.code));
  if (!mcpDomains.length) return domains || [];
  const firstMcpIndex = (domains || []).findIndex((domain) => mcpCodes.has(domain.code));
  const merged = {
    code: "runtime:mcp",
    label: "MCP",
    description: mcpDomains
      .map((domain) => `${domain.label || domain.code}：${domain.description || "暂无补充说明"}`)
      .join("；"),
    tools: mcpDomains.flatMap((domain) => domain.tools || []),
  };
  return (domains || []).reduce((result, domain, index) => {
    if (index === firstMcpIndex) result.push(merged);
    if (!mcpCodes.has(domain.code)) result.push(domain);
    return result;
  }, []);
}

function ensureSystemToolTooltip() {
  let tooltip = document.getElementById("systemToolTooltip");
  if (tooltip) return tooltip;
  tooltip = document.createElement("div");
  tooltip.id = "systemToolTooltip";
  tooltip.className = "system-tool-tooltip hidden";
  tooltip.setAttribute("role", "tooltip");
  document.body.appendChild(tooltip);
  return tooltip;
}

function bindSystemToolHelp(host) {
  const tooltip = ensureSystemToolTooltip();
  const hide = () => tooltip.classList.add("hidden");
  host.querySelectorAll(".system-tool-group-help").forEach((trigger) => {
    const show = () => {
      tooltip.textContent = trigger.dataset.tooltip || "";
      tooltip.classList.remove("hidden");
      const rect = trigger.getBoundingClientRect();
      const width = tooltip.offsetWidth;
      const height = tooltip.offsetHeight;
      const left = Math.min(window.innerWidth - width - 12, Math.max(12, rect.left + rect.width / 2 - width / 2));
      const above = rect.top - height - 8;
      tooltip.style.left = `${left}px`;
      tooltip.style.top = `${above >= 8 ? above : rect.bottom + 8}px`;
    };
    trigger.addEventListener("mouseenter", show);
    trigger.addEventListener("mouseleave", hide);
    trigger.addEventListener("focus", show);
    trigger.addEventListener("blur", hide);
  });
}

function renderSystemTools(domains) {
  const host = document.getElementById("systemToolDomains");
  if (!host) return;
  if (!(domains || []).length) {
    host.innerHTML = `<div class="system-tools-state">暂无可展示的系统工具</div>`;
    return;
  }
  const displayDomains = mergeSystemToolDomains(domains);
  host.innerHTML = `<div class="table-wrap console-list-a">
    <table class="data-table system-tools-table">
      <colgroup>
        <col class="system-tool-col-name" />
        <col class="system-tool-col-source" />
        <col class="system-tool-col-condition" />
        <col class="system-tool-col-description" />
      </colgroup>
      <thead><tr><th>工具名称</th><th>实现来源</th><th>自动注入条件</th><th>说明</th></tr></thead>
      <tbody>${displayDomains.map(systemToolDomain).join("")}</tbody>
    </table>
  </div>`;
  bindSystemToolHelp(host);
}

function renderSystemToolsError(message) {
  const host = document.getElementById("systemToolDomains");
  if (!host) return;
  host.innerHTML = `<div class="system-tools-state">
    <span class="configuration-error">${escapeHtml(message || "系统工具加载失败")}</span>
  </div>`;
}

const systemToolsAdmin = {
  async refresh() {
    const host = document.getElementById("systemToolDomains");
    if (!host) return;
    host.innerHTML = `<div class="system-tools-state">正在加载系统工具…</div>`;
    try {
      renderSystemTools(await apiFetch("/api/v1/system-tools"));
    } catch (error) {
      renderSystemToolsError(error instanceof Error ? error.message : String(error));
    }
  },
};

window.systemToolsAdmin = systemToolsAdmin;
window.__SystemToolsUI = {
  mergeSystemToolDomains,
  renderSystemTools,
  systemToolDomain,
  systemToolRow,
};
