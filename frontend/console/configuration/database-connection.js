function databaseInfoValue(value) {
  if (value === null || value === undefined || value === "") return "—";
  return String(value);
}

function databaseInfoHtml(info) {
  const remote = info.connection_mode === "远程数据库";
  const rows = [
    ["数据库结构", info.schema_version ? `V${String(info.schema_version).replace(/^0+/, "") || "0"}` : "—"],
    ["数据库类型", info.database_type_label],
    ["连接方式", info.connection_mode],
    ...(remote ? [["主机", info.host], ["端口", info.port]] : []),
    [remote ? "数据库" : "数据库文件", info.database],
    ["连接摘要", info.connection_summary],
    ["认证信息", info.credentials_hidden ? "已隐藏" : "—"],
  ];
  return `<div class="database-connection-card">
    <div class="database-connection-badge">
      <i data-lucide="database"></i>
      <div><strong>${escapeHtml(databaseInfoValue(info.database_type_label))}</strong><span>${escapeHtml(databaseInfoValue(info.connection_mode))}</span></div>
    </div>
    <dl class="database-connection-grid">
      ${rows.map(([label, value]) => `<dt>${escapeHtml(label)}</dt><dd>${escapeHtml(databaseInfoValue(value))}</dd>`).join("")}
    </dl>
    <p class="database-connection-note">该页面展示进程启动时生效的连接摘要。修改数据库环境变量后需要重启 DeepAgent。</p>
  </div>`;
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
window.__DatabaseConnectionUI = { databaseInfoHtml, databaseInfoValue };

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
