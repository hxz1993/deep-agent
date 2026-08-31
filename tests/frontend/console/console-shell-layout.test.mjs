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
const configurationModule = await readFile(
  new URL("../../../frontend/console/configuration.js", import.meta.url),
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
const catalogTemplate = await readFile(
  new URL("../../../frontend/console/catalog-template.html", import.meta.url),
  "utf8",
);
const systemTools = await readFile(
  new URL("../../../frontend/console/configuration/system-tools.js", import.meta.url),
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
  assert.match(agents, /let agentsLoadedOnce = false/);
  assert.match(agents, /if \(!agentsLoadedOnce\)[\s\S]*正在加载 Agent/);
  assert.match(agents, /agentsLoadedOnce = true/);
  assert.match(agents, /Agent 刷新失败/);
});

test("Agent 容器圆角收口且工具包卡片改为单列两行摘要", () => {
  assert.match(styles, /#configurationHost #panel-agents\s*\{[^}]*overflow: clip;[^}]*border-radius: var\(--console-card-radius\) !important;/);
  assert.match(styles, /\.agent-editor-pane \.cap-grid\s*\{[^}]*grid-template-columns: minmax\(0, 1fr\);/);
  assert.match(styles, /\.agent-editor-pane\s*\{[^}]*display: flex;[^}]*overflow: hidden;/);
  assert.match(styles, /\.agent-editor-pane \.cap-panels\s*\{[^}]*flex: 1 1 auto;[^}]*max-height: none;[^}]*overflow: auto;/);
  assert.match(styles, /\.agent-editor-pane \.cap-panel:not\(\.hidden\)\s*\{[^}]*min-height: 100%;[^}]*flex-direction: column;/);
  assert.match(styles, /\.agent-editor-pane \.cap-grid\s*\{[^}]*flex: 1 1 auto;[^}]*align-content: start;/);
  assert.match(styles, /\.agent-editor-pane \.cap-card\s*\{[^}]*grid-template-columns: minmax\(0, 1fr\) auto;[^}]*min-height: 58px;/);
  assert.match(styles, /\.agent-editor-pane \.cap-card-desc\s*\{[^}]*-webkit-line-clamp: 1;/);
});

test("配置弹窗统一水平居中，Agent 独立编辑页仍保持页面层级", () => {
  assert.match(styles, /dialog\.console-detail-dialog\.modal\s*\{[^}]*margin: auto;[^}]*border-radius: var\(--console-card-radius\);/);
  assert.match(styles, /dialog\.agent-editor-page\.console-detail-dialog\.modal-agent[\s\S]*?margin: 0;/);
});

test("Agent 卡片与列表统一使用稳定标识的纯图标行操作", () => {
  assert.ok((agents.match(/class="row-actions agent-row-actions"/g) || []).length >= 2);
  assert.ok((agents.match(/data-agent-chat/g) || []).length >= 2);
  assert.ok((agents.match(/data-copy-agent/g) || []).length >= 2);
  assert.match(agents, /title="发起对话" aria-label="发起对话"><i data-lucide="message-square"/);
  assert.match(agents, /title="复制" aria-label="复制"><i data-lucide="copy"/);
  assert.doesNotMatch(agents, /title="编辑" aria-label="编辑"><i data-lucide="pencil"/);
  assert.match(agents, /title="删除" aria-label="删除"><i data-lucide="trash-2"/);
  assert.doesNotMatch(agents, /<span>发起对话<\/span>/);
  assert.match(configurationModule, /link\.matches\("\[data-agent-chat\]"\)/);
  assert.match(styles, /--config-compact-control-height: 28px/);
  assert.match(styles, /#configurationHost \.agent-row-actions \.row-action,[\s\S]*height: var\(--config-compact-control-height\);[\s\S]*border-radius: var\(--config-radius\)/);
});

test("Agent 编辑使用独立页面层级而不是模态框", () => {
  assert.match(template, /class="modal modal-agent agent-editor-page"/);
  assert.match(template, /id="closeAgentModal"[\s\S]*?<span>返回<\/span>/);
  assert.match(template, /class="agent-editor-header-actions"[\s\S]*?id="testAgentBtn"[\s\S]*?id="saveAgentBtn"/);
  assert.doesNotMatch(template, /id="cancelAgentBtn"|agent-editor-footer-spacer/);
  assert.doesNotMatch(template.match(/id="agentForm"[\s\S]*?<\/form>/)?.[0] || "", /class="modal-footer"/);
  assert.match(agents, /if \(!editor\.open\) editor\.show\(\)/);
  assert.doesNotMatch(agents, /agentModal"\)\.showModal\(\)/);
  assert.match(agents, /放弃未保存的修改/);
  assert.match(styles, /dialog\.agent-editor-page\.console-detail-dialog\.modal-agent[\s\S]*?position: fixed/);
  assert.match(styles, /inset: var\(--console-topbar\) 0 0 calc\(var\(--console-rail-width\) \+ var\(--console-resource-nav-width\) \+ var\(--console-config-padding\)\)/);
  assert.match(styles, /\.agent-editor-page \.agent-editor-back[\s\S]*?border: 0;/);
  assert.match(styles, /\.agent-editor-header-actions[\s\S]*?justify-content: flex-end/);
  assert.match(configurationCore, /agentsAdmin\?\.isEditorOpen\?\.\(\)[\s\S]*agentsAdmin\.requestCloseEditor\(\)/);
});

test("MCP 详情沿用独立页面层级并在导航前确认未保存修改", () => {
  assert.match(configurationCore, /mcpAdmin\?\.isEditorOpen\?\.\(\)[\s\S]*mcpAdmin\.requestCloseEditor\(\)/);
});

test("Agent 编辑采用 B 方案左右工作台并在右侧切换工具与 Skill", () => {
  assert.match(template, /class="agent-editor-workbench"/);
  assert.match(template, /class="agent-editor-primary"[\s\S]*id="agentName"[\s\S]*id="agentModelId"[\s\S]*id="agentPrompt"/);
  assert.match(template, /class="agent-editor-resources"[\s\S]*data-agent-editor-tab="tools"[\s\S]*data-agent-editor-tab="skills"/);
  assert.match(template, /data-agent-editor-pane="tools"[\s\S]*id="agentCapGroups"/);
  assert.match(template, /data-agent-editor-pane="skills"[\s\S]*id="agentSkillChoices"/);
  assert.match(agents, /switchEditorTab\(tab\)/);
  assert.match(agents, /this\.switchEditorTab\("tools"\)/);
  assert.match(styles, /\.agent-editor-workbench\s*\{[\s\S]*grid-template-columns: minmax\(310px, \.82fr\) minmax\(450px, 1\.18fr\)/);
  assert.match(styles, /\.agent-editor-tabs button\[aria-selected="true"\]::after/);
});

test("配置导航将 MCP 服务简化为 MCP", () => {
  assert.match(index, /data-tab="mcp"[^>]*>[\s\S]*?<span>MCP<\/span>/);
  assert.doesNotMatch(index, /<span>MCP 服务<\/span>/);
  const mcpButton = index.match(/<button class="tab resource-btn" data-tab="mcp">[\s\S]*?<\/button>/)?.[0] || "";
  assert.match(mcpButton, /<svg class="mcp-brand-icon"/);
  assert.match(mcpButton, /<path d=/);
  assert.doesNotMatch(mcpButton, /<rect|fill="black"/);
  assert.match(
    styles,
    /\.resource-btn \.mcp-brand-icon\s*\{[\s\S]*?color: var\(--text-faint\);[\s\S]*?stroke: currentColor;[\s\S]*?stroke-width: 12;/,
  );
});

test("护眼主题使用清透暖橙而不是土黄配色", () => {
  assert.match(themes, /\[data-theme="paper"\][\s\S]*--bg: #fffaf6/);
  assert.match(themes, /\[data-theme="paper"\][\s\S]*--accent: #e46f2b/);
  assert.doesNotMatch(themes, /\[data-theme="paper"\][\s\S]*--bg: #f4f1ea/);
  assert.match(styles, /html\[data-theme="paper"\][\s\S]*--console-canvas: #fff3e9/);
});

test("配置中心的主要业务表格统一使用连续轻列表风格 A", () => {
  assert.ok((template.match(/class="table-wrap console-list-a/g) || []).length >= 4);
  assert.match(catalogTemplate, /class="table-wrap console-list-a models-table-wrap"/);
  assert.match(systemTools, /class="table-wrap console-list-a"/);
  assert.match(styles, /#configurationHost \.console-list-a \.data-table th[\s\S]*border-bottom: 1px solid var\(--console-card-border\)/);
  assert.match(styles, /#configurationHost \.console-list-a \.data-table td[\s\S]*border: 0;[\s\S]*border-bottom:/);
  assert.match(styles, /tbody tr:hover > td:first-child[\s\S]*border-left-color: var\(--accent\)/);
});

test("配置中心采用 Deepfos 轻风格的统一尺寸和表头", () => {
  assert.match(styles, /--config-radius: 8px/);
  assert.match(styles, /--config-control-height: 32px/);
  assert.match(styles, /--config-header-height: 56px/);
  assert.match(styles, /#configurationHost \.panel-toolbar,[\s\S]*min-height: var\(--config-header-height\)/);
  assert.match(styles, /#configurationHost \.btn,[\s\S]*height: var\(--config-control-height\)/);
  assert.match(styles, /#configurationHost \.data-table thead th,[\s\S]*height: 36px;[\s\S]*font-size: 12px/);
  assert.match(template, /<h2>MCP 服务列表<\/h2>[\s\S]*管理 Agent 可授权使用的 MCP 服务、工具快照与连接状态/);
  assert.match(styles, /\.agent-card-body \{[^}]*border-top:[^}]*\}/);
  assert.doesNotMatch(styles, /\.agent-card-body \{[^}]*border-bottom:/);
  assert.match(styles, /\.configuration-content,\s*#configurationHost\s*\{[\s\S]*height: 100%;[\s\S]*min-height: 0;/);
  assert.match(styles, /#configurationHost > \.panel\s*\{[\s\S]*box-sizing: border-box;[\s\S]*min-height: 100%;/);
  assert.match(styles, /\.resource-nav\s*\{[\s\S]*border-radius: var\(--console-card-radius\) !important;/);
});
