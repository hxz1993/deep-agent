function databaseInfoValue(value) {
  if (value === null || value === undefined || value === "") return "—";
  return String(value);
}

function databaseInfoHtml(info) {
  const remote = info.connection_mode === "远程数据库";
  const rows = [
    ["类型", info.database_type_label],
    ["方式", info.connection_mode],
    ...(remote ? [["主机", info.host], ["端口", info.port]] : []),
    [remote ? "数据库" : "文件", info.database],
  ];
  return `<article class="database-connection-card">
    <header class="database-connection-badge">
      <span class="database-connection-icon"><i data-lucide="database"></i></span>
      <div><strong>${escapeHtml(databaseInfoValue(info.database_type_label))}</strong><span>${escapeHtml(databaseInfoValue(info.connection_summary || info.connection_mode))}</span></div>
      <span class="database-safe-badge"><i data-lucide="shield-check"></i>${info.credentials_hidden ? "凭证已隐藏" : "只读摘要"}</span>
    </header>
    <dl class="database-connection-facts">
      ${rows.map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(databaseInfoValue(value))}</dd></div>`).join("")}
    </dl>
  </article>`;
}

function schemaBadgeText(info) {
  if (!info?.schema_version) return "V—";
  return `V${String(info.schema_version).replace(/^0+/, "") || "0"}`;
}

const databaseConnectionAdmin = {
  loading: false,

  async refresh() {
    const target = document.getElementById("databaseConnectionContent");
    if (!target || this.loading) return;
    this.loading = true;
    target.innerHTML = `<div class="global-settings-state">正在加载数据库连接…</div>`;
    try {
      const info = await apiFetch("/api/v1/database-info");
      target.innerHTML = databaseInfoHtml(info || {});
      const schemaBadge = document.getElementById("databaseSchemaBadge");
      if (schemaBadge) schemaBadge.textContent = schemaBadgeText(info);
      window.lucide?.createIcons();
    } catch (error) {
      target.innerHTML = `<div class="global-settings-state error">
        <span>${escapeHtml(error.message || "数据库连接加载失败")}</span>
        <button class="btn" type="button" data-retry-database-connection>重新加载</button>
      </div>`;
    } finally {
      this.loading = false;
    }
  },
};

window.databaseConnectionAdmin = databaseConnectionAdmin;
window.__DatabaseConnectionUI = { databaseInfoHtml, databaseInfoValue, schemaBadgeText };

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("refreshDatabaseConnectionBtn")?.addEventListener("click", () => {
    databaseConnectionAdmin.refresh();
  });
  document.getElementById("databaseConnectionContent")?.addEventListener("click", (event) => {
    if (event.target.closest("[data-retry-database-connection]")) {
      databaseConnectionAdmin.refresh();
    }
  });
});
