function databaseInfoValue(value) {
  if (value === null || value === undefined || value === "") return "—";
  return String(value);
}

function databaseInfoHtml(info, checkState = {}) {
  const remote = info.connection_mode === "远程数据库";
  const rows = [
    ["类型", info.database_type_label],
    ["方式", info.connection_mode],
    ...(remote ? [["主机", info.host], ["端口", info.port]] : []),
    [remote ? "数据库" : "文件", info.database],
  ];
  return `<dl class="database-inline-facts" title="只读摘要；用户名、密码、连接参数和本地完整路径不会展示">
      ${rows.map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(databaseInfoValue(value))}</dd></div>`).join("")}
    </dl>${databaseCharsetHtml(info, checkState)}`;
}

function databaseCharsetHtml(info, { checking = false, checkedAt = null, error = "" } = {}) {
  const button = `<button class="btn" type="button" data-check-database-charset${checking ? " disabled" : ""}>${checking ? "检查中…" : "检查字符集"}</button>`;
  const timestamp = checkedAt ? `<span class="database-charset-note">检查时间：${escapeHtml(new Date(checkedAt).toLocaleString("zh-CN"))}</span>` : "";
  if (checking || error || info.charset === null) {
    const note = checking ? "字符集检查中…" : error ? `字符集检查失败：${error}` : "字符集尚未检查";
    return `<div class="database-charset-row" data-state="unknown" role="status"><span class="database-charset-note">${escapeHtml(note)}</span>${button}${timestamp}</div>`;
  }
  const charset = info.charset || {};
  const states = { ok: ["字符集正常", "circle-check"], warning: ["字符集需处理", "triangle-alert"], unknown: ["字符集未确认", "circle-help"], not_applicable: ["字符集未检查", "circle-help"] };
  const state = Object.hasOwn(states, charset.status) ? charset.status : "unknown";
  const [label, icon] = states[state];
  const facts = [["数据库", charset.database]];
  if (info.database_type === "mysql") {
    facts.push(["连接", charset.connection], ["业务文本字段", charset.checked_columns ? `${charset.checked_columns} 项` : "未完成检查"]);
  } else if (charset.client) facts.push(["客户端", charset.client]);
  const issues = Array.isArray(charset.issues) ? charset.issues : [];
  const note = state === "ok" ? "支持完整 Unicode 与 Emoji" : (issues[0] || "当前服务未提供完整字符集检查结果");
  return `<div class="database-charset-row" data-state="${state}" role="status">
    <span class="database-charset-badge"><i data-lucide="${icon}"></i>${label}</span>
    <dl class="database-inline-facts">${facts.map(([key, value]) => `<div><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(databaseInfoValue(value))}</dd></div>`).join("")}</dl>
    <span class="database-charset-note">${escapeHtml(note)}</span>
    ${issues.length ? `<details class="database-charset-issues"><summary>查看 ${issues.length} 项检查结果</summary><ul>${issues.map((issue) => `<li>${escapeHtml(String(issue))}</li>`).join("")}</ul></details>` : ""}
    ${button}${timestamp}
  </div>`;
}

function schemaBadgeText(info) {
  if (!info?.schema_version) return "V—";
  return `V${String(info.schema_version).replace(/^0+/, "") || "0"}`;
}

const databaseConnectionAdmin = {
  loading: false,
  checking: false,
  info: null,
  charset: null,
  checkedAt: null,
  checkError: "",
  pending: null,
  checkPending: null,

  render() {
    const target = document.getElementById("databaseConnectionContent");
    if (!target || !this.info) return;
    target.innerHTML = databaseInfoHtml({ ...this.info, charset: this.charset }, {
      checking: this.checking, checkedAt: this.checkedAt, error: this.checkError,
    });
    const schemaBadge = document.getElementById("databaseSchemaBadge");
    if (schemaBadge) schemaBadge.textContent = schemaBadgeText(this.info);
    const version = document.getElementById("databaseSchemaVersion");
    if (version) version.textContent = schemaBadgeText(this.info);
    window.lucide?.createIcons();
  },

  refresh() {
    if (this.pending) return this.pending;
    this.pending = this.loadSummary().finally(() => { this.pending = null; });
    return this.pending;
  },

  async loadSummary() {
    const target = document.getElementById("databaseConnectionContent");
    if (!target || this.loading) return;
    this.loading = true;
    target.innerHTML = `<div class="global-settings-state">正在加载数据库连接…</div>`;
    try {
      this.info = await apiFetch("/api/v1/database-info?include_charset=false") || {};
      this.render();
    } catch (error) {
      target.innerHTML = `<div class="global-settings-state error">
        <span>${escapeHtml(error.message || "数据库连接加载失败")}</span>
        <button class="btn" type="button" data-retry-database-connection>重新加载</button>
      </div>`;
    } finally {
      this.loading = false;
    }
  },

  checkCharset() {
    if (this.checkPending) return this.checkPending;
    this.checking = true;
    this.checkError = "";
    this.render();
    this.checkPending = (async () => {
      try {
        const info = await apiFetch("/api/v1/database-info");
        this.charset = info?.charset;
      } catch (error) {
        this.checkError = error.message || "检查失败";
      } finally {
        this.checkedAt = new Date().toISOString();
        this.checking = false;
        this.checkPending = null;
        this.render();
      }
    })();
    return this.checkPending;
  },
};

window.databaseConnectionAdmin = databaseConnectionAdmin;
window.__DatabaseConnectionUI = { databaseInfoHtml, databaseInfoValue, databaseCharsetHtml, schemaBadgeText };

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("databaseConnectionContent")?.addEventListener("click", (event) => {
    if (event.target.closest("[data-retry-database-connection]")) {
      databaseConnectionAdmin.refresh();
    }
    if (event.target.closest("[data-check-database-charset]")) {
      databaseConnectionAdmin.checkCharset();
    }
  });
});
