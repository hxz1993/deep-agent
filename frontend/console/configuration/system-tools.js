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
      <th colspan="4"><span class="system-tool-group-label">${escapeHtml(domain.label || domain.code || "未命名领域")}<span class="system-tool-group-help" tabindex="0" role="img" aria-label="${escapeHtml(description)}" title="${escapeHtml(description)}" data-tooltip="${escapeHtml(description)}"></span></span></th>
    </tr>${rows}`;
}

function renderSystemTools(domains) {
  const host = document.getElementById("systemToolDomains");
  if (!host) return;
  if (!(domains || []).length) {
    host.innerHTML = `<div class="system-tools-state">暂无可展示的系统工具</div>`;
    return;
  }
  host.innerHTML = `<div class="table-wrap console-list-a">
    <table class="data-table system-tools-table">
      <colgroup>
        <col class="system-tool-col-name" />
        <col class="system-tool-col-source" />
        <col class="system-tool-col-condition" />
        <col class="system-tool-col-description" />
      </colgroup>
      <thead><tr><th>工具名称</th><th>实现来源</th><th>自动注入条件</th><th>说明</th></tr></thead>
      <tbody>${domains.map(systemToolDomain).join("")}</tbody>
    </table>
  </div>`;
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
  renderSystemTools,
  systemToolDomain,
  systemToolRow,
};
