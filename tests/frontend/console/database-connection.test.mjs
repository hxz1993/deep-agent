import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(
  new URL("../../../console/configuration/database-connection.js", import.meta.url),
  "utf8",
);
const indexHtml = await readFile(
  new URL("../../../console/index.html", import.meta.url),
  "utf8",
);
const templateHtml = await readFile(
  new URL("../../../console/configuration-template.html", import.meta.url),
  "utf8",
);
const styles = await readFile(
  new URL("../../../console/configuration/configuration.css", import.meta.url),
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

test("数据库连接并入全局配置，不再提供独立目录", () => {
  const systemGroup = indexHtml.indexOf(">系统</div>");
  assert.ok(systemGroup > indexHtml.indexOf('class="console-nav-footer"'));
  assert.ok(systemGroup < indexHtml.indexOf('data-tab="global-settings"'));
  assert.doesNotMatch(indexHtml, /data-tab="auxiliary-models"|辅助模型|辅助工具/);
  assert.doesNotMatch(indexHtml, /data-tab="database-connection"/);
  assert.doesNotMatch(indexHtml, /data-tab="catalog"/);
  assert.match(templateHtml, /data-settings-page="models"/);
  assert.doesNotMatch(indexHtml, />模型资源</);
});

test("数据库信息在Web Search和运行设置之间以紧凑只读摘要展示", () => {
  assert.doesNotMatch(templateHtml, /id="panel-database-connection"/);
  assert.ok(templateHtml.indexOf('id="globalSettingsWebSearch"') < templateHtml.indexOf('id="globalSettingsDatabase"'));
  assert.ok(templateHtml.indexOf('id="globalSettingsDatabase"') < templateHtml.indexOf('id="globalSettingsRuntime"'));
  const panelHtml = templateHtml.match(
    /<section class="global-settings-section" id="globalSettingsDatabase"[\s\S]*?<\/section>/,
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
  assert.equal(loadUi().schemaBadgeText({ schema_version: "002" }), "V2");
  assert.match(html, /test-db/);
  assert.match(html, /deep_agent/);
  assert.match(html, /用户名、密码、连接参数和本地完整路径不会展示/);
  assert.match(html, /database-inline-facts/);
  assert.doesNotMatch(html, /database-connection-card/);
  assert.doesNotMatch(html, /当前连接|database-connection-note|数据库结构/);
  assert.doesNotMatch(html, /input|textarea|保存/);
  assert.match(styles, /\.database-connection-card\s*\{[^}]*border-radius: 14px;[^}]*background: var\(--console-surface/);
  assert.match(styles, /\.database-connection-facts\s*\{[^}]*display: flex;/);
  assert.match(templateHtml, /id="databaseSchemaBadge"/);
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

test("字符集正常状态在第二行显示实查值与字段范围", () => {
  const html = loadUi().databaseInfoHtml({ database_type: "mysql", charset: {
    status: "ok", database: "utf8mb4", connection: "utf8mb4", checked_columns: 42, issues: [],
  } });
  assert.match(html, /data-state="ok"/);
  assert.match(html, /字符集正常/);
  assert.match(html, /utf8mb4/);
  assert.match(html, /42 项/);
  assert.ok(html.indexOf("database-charset-row") > html.indexOf("</dl>"));
});

test("字符集异常可查看具体字段且转义检查信息", () => {
  const html = loadUi().databaseCharsetHtml({ database_type: "mysql", charset: {
    status: "warning", database: "utf8mb4", connection: "utf8", checked_columns: 42,
    issues: ["chat_session_events.payload：utf8", "<script>unsafe</script>"],
  } });
  assert.match(html, /字符集需处理/);
  assert.match(html, /<details/);
  assert.match(html, /chat_session_events.payload/);
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /&lt;script&gt;/);
});

test("旧接口缺少检查结果或检查失败不能显示正常", () => {
  for (const info of [{}, { charset: { status: "unknown", issues: ["字符集检查未完成"] } }, { charset: { status: '" onmouseover="bad' } }]) {
    const html = loadUi().databaseCharsetHtml(info);
    assert.match(html, /data-state="unknown"/);
    assert.match(html, /字符集未确认/);
    assert.doesNotMatch(html, /字符集正常|onmouseover/);
  }
});
