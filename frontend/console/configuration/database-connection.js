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
  return `<article class="database-connection-card">
    <header class="database-connection-badge">
      <span class="database-connection-icon"><i data-lucide="database"></i></span>
      <div><span class="database-card-eyebrow">当前连接</span><strong>${escapeHtml(databaseInfoValue(info.database_type_label))}</strong><span>${escapeHtml(databaseInfoValue(info.connection_mode))}</span></div>
      <span class="database-safe-badge"><i data-lucide="shield-check"></i>${info.credentials_hidden ? "凭证已隐藏" : "只读摘要"}</span>
    </header>
    <dl class="database-connection-grid">
      ${rows.map(([label, value]) => `<div class="database-connection-field${label === "连接摘要" ? " is-wide" : ""}"><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(databaseInfoValue(value))}</dd></div>`).join("")}
    </dl>
    <footer class="database-connection-note"><i data-lucide="info"></i><span>展示进程启动时生效的只读连接摘要；修改数据库环境变量后需重启 DeepAgent。</span></footer>
  </article>`;
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
