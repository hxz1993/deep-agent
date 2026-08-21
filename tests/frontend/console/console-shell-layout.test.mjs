import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const index = await readFile(
  new URL("../../../frontend/console/index.html", import.meta.url),
  "utf8",
);
const template = await readFile(
  new URL("../../../frontend/console/configuration-template.html", import.meta.url),
  "utf8",
);
const agents = await readFile(
  new URL("../../../frontend/console/configuration/agents.js", import.meta.url),
  "utf8",
);
const configurationCore = await readFile(
  new URL("../../../frontend/console/configuration/core.js", import.meta.url),
  "utf8",
);
const styles = await readFile(
  new URL("../../../frontend/console/console.css", import.meta.url),
  "utf8",
);
const themes = await readFile(
  new URL("../../../frontend/console/themes.css", import.meta.url),
  "utf8",
);

test("原控制台把一级入口迁移到左侧图标栏且保留三个真实页面", () => {
  assert.match(index, /<aside class="console-rail"[\s\S]*?<nav class="console-nav">/);
  assert.match(index, /data-page="workspace"[\s\S]*?data-page="configuration"[\s\S]*?data-page="diagnostics"/);
  assert.doesNotMatch(index, /<header class="console-topbar">[\s\S]*?<nav class="console-nav"/);
  assert.match(styles, /grid-template-columns: var\(--console-rail-width\) minmax\(0, 1fr\)/);
  assert.match(index, /class="console-brand-icon"[\s\S]*favicon\.svg/);
  assert.doesNotMatch(index, /class="console-rail-brand"/);
  assert.match(styles, /\.console-rail\s*\{[\s\S]*grid-row: 2;/);
  assert.match(styles, /\.console-topbar\s*\{[\s\S]*grid-column: 1 \/ -1;/);
});

test("Agent 提供卡片和列表双视图并复用同一真实数据源", () => {
  assert.match(template, /data-agent-view="cards"/);
  assert.match(template, /data-agent-view="list"/);
  assert.match(template, /id="agentsCardGrid"/);
  assert.match(template, /id="agentsListView"/);
  assert.match(agents, /renderList\(agents\)/);
  assert.match(agents, /setListView\(view\)/);
  assert.match(agents, /data-open-agent/);
});

test("Agent 编辑使用独立页面层级而不是模态框", () => {
  assert.match(template, /class="modal modal-agent agent-editor-page"/);
  assert.match(template, /id="closeAgentModal"[\s\S]*?返回 Agent 列表/);
  assert.match(template, /id="testAgentBtn"/);
  assert.match(agents, /if \(!editor\.open\) editor\.show\(\)/);
  assert.doesNotMatch(agents, /agentModal"\)\.showModal\(\)/);
  assert.match(agents, /当前 Agent 有未保存修改/);
  assert.match(styles, /dialog\.agent-editor-page\.console-detail-dialog\.modal-agent[\s\S]*?position: fixed/);
  assert.match(styles, /inset: var\(--console-topbar\) 0 0 calc\(var\(--console-rail-width\) \+ var\(--console-resource-nav-width\) \+ var\(--console-config-padding\)\)/);
  assert.match(configurationCore, /agentsAdmin\?\.isEditorOpen\?\.\(\)[\s\S]*agentsAdmin\.requestCloseEditor\(\)/);
});

test("配置导航将 MCP 服务简化为 MCP", () => {
  assert.match(index, /data-tab="mcp"[^>]*>[\s\S]*?<span>MCP<\/span>/);
  assert.doesNotMatch(index, /<span>MCP 服务<\/span>/);
});

test("护眼主题使用清透暖橙而不是土黄配色", () => {
  assert.match(themes, /\[data-theme="paper"\][\s\S]*--bg: #fffaf6/);
  assert.match(themes, /\[data-theme="paper"\][\s\S]*--accent: #e46f2b/);
  assert.doesNotMatch(themes, /\[data-theme="paper"\][\s\S]*--bg: #f4f1ea/);
  assert.match(styles, /html\[data-theme="paper"\][\s\S]*--console-canvas: #fff3e9/);
});
