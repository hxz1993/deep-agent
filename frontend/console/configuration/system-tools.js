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
  const rows = tools.length
    ? tools.map(systemToolRow).join("")
    : `<tr><td colspan="4" class="empty-cell">该领域暂无系统工具</td></tr>`;
  return `<section class="system-tool-domain" data-system-tool-domain="${escapeHtml(domain.code || "")}">
    <header class="system-tool-domain-head">
      <div><h3>${escapeHtml(domain.label || domain.code || "未命名领域")}</h3><p>${escapeHtml(domain.description || "")}</p></div>
      <code>${escapeHtml(domain.code || "")}</code>
    </header>
    <div class="table-wrap console-list-a">
      <table class="data-table system-tools-table">
        <colgroup>
          <col class="system-tool-col-name" />
          <col class="system-tool-col-source" />
          <col class="system-tool-col-condition" />
          <col class="system-tool-col-description" />
        </colgroup>
        <thead><tr><th>工具名称</th><th>实现来源</th><th>自动注入条件</th><th>说明</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  </section>`;
}

function renderSystemTools(domains) {
  const host = document.getElementById("systemToolDomains");
  if (!host) return;
  if (!(domains || []).length) {
    host.innerHTML = `<div class="system-tools-state">暂无可展示的系统工具</div>`;
    return;
  }
  host.innerHTML = domains.map(systemToolDomain).join("");
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
