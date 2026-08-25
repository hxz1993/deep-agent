import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(
  new URL("../../../frontend/console/configuration/database-connection.js", import.meta.url),
  "utf8",
);
const indexHtml = await readFile(
  new URL("../../../frontend/console/index.html", import.meta.url),
  "utf8",
);
const templateHtml = await readFile(
  new URL("../../../frontend/console/configuration-template.html", import.meta.url),
  "utf8",
);
const styles = await readFile(
  new URL("../../../frontend/console/configuration/configuration.css", import.meta.url),
  "utf8",
);

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function loadUi() {
  const window = {};
  const document = { addEventListener() {}, getElementById() { return null; } };
  vm.runInContext(
    source,
    vm.createContext({ apiFetch: async () => ({}), document, escapeHtml, window }),
  );
  return window.__DatabaseConnectionUI;
}

test("配置中心按 Agent 设置、系统设置和外部资源分组", () => {
  const agentGroup = indexHtml.indexOf("Agent 设置");
  const systemGroup = indexHtml.indexOf("系统设置");
  const resourceGroup = indexHtml.indexOf("外部资源");
  assert.ok(agentGroup >= 0 && agentGroup < systemGroup && systemGroup < resourceGroup);
  assert.ok(indexHtml.indexOf('data-tab="ui-tools"') < systemGroup);
  assert.ok(systemGroup < indexHtml.indexOf('data-tab="auxiliary-models"'));
  assert.ok(indexHtml.indexOf('data-tab="database-connection"') < resourceGroup);
  assert.ok(resourceGroup < indexHtml.indexOf('data-tab="catalog"'));
  assert.doesNotMatch(indexHtml, />模型资源</);
});

test("数据库连接页是只读安全摘要", () => {
  assert.match(templateHtml, /id="panel-database-connection"/);
  assert.match(templateHtml, /用户名、密码、连接参数和本地完整路径不会展示/);
  const panelHtml = templateHtml.match(
    /<section class="panel database-connection-panel"[\s\S]*?<\/section>/,
  )?.[0] || "";
  assert.doesNotMatch(panelHtml, /type="password"|name="username"|保存数据库/);

  const html = loadUi().databaseInfoHtml({
    schema_version: "002",
    database_type_label: "MySQL",
    connection_mode: "远程数据库",
    host: "test-db",
    port: 3306,
    database: "deep_agent",
    connection_summary: "MySQL · test-db:3306/deep_agent",
    credentials_hidden: true,
  });
  assert.match(html, /MySQL/);
  assert.match(html, /V2/);
  assert.match(html, /test-db/);
  assert.match(html, /deep_agent/);
  assert.match(html, /已隐藏/);
  assert.match(html, /database-connection-field/);
  assert.match(html, /database-safe-badge/);
  assert.doesNotMatch(html, /input|textarea|保存/);
  assert.match(styles, /\.database-connection-card\s*\{[^}]*border-radius: 14px;[^}]*background: var\(--console-surface/);
  assert.match(styles, /\.database-connection-grid\s*\{[^}]*repeat\(3, minmax\(0, 1fr\)\)/);
});

test("数据库连接渲染会转义服务端字段", () => {
  const html = loadUi().databaseInfoHtml({
    schema_version: "002",
    database_type_label: "<script>",
    connection_mode: "远程数据库",
    host: "<img src=x>",
    port: 3306,
    database: "safe",
    connection_summary: "<b>unsafe</b>",
    credentials_hidden: true,
  });
  assert.doesNotMatch(html, /<script>|<img|<b>/);
  assert.match(html, /&lt;script&gt;/);
});
