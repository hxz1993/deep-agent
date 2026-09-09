import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = await readFile(
  new URL("../../../console/configuration/ui-tools.js", import.meta.url),
  "utf8",
);
const template = await readFile(
  new URL("../../../console/configuration-template.html", import.meta.url),
  "utf8",
);
const styles = await readFile(
  new URL("../../../console/configuration/configuration.css", import.meta.url),
  "utf8",
);
const consoleStyles = await readFile(
  new URL("../../../console/console.css", import.meta.url),
  "utf8",
);

test("UI 工具页使用左侧工具包树和右侧单一工具表", () => {
  assert.match(template, /id="uiToolPackageTree"/);
  assert.match(template, /id="uiToolsTableBody"/);
  assert.doesNotMatch(template, /id="uiToolPackagesTableBody"/);
  assert.equal((template.match(/id="uiToolsTableBody"/g) || []).length, 1);
  assert.match(template, /是否参与运行只由 Agent 关联决定/);
  assert.doesNotMatch(template, /id="uiToolPackageEnabled"|id="uiToolEnabled"/);
  assert.match(source, /api\/v1\/ui-tool-packages/);
  assert.doesNotMatch(template, /uiToolsFilters|uiToolFilterSearch|uiToolFilterEnabled/);
  assert.doesNotMatch(source, /uiToolFilterSearch|uiToolFilterEnabled/);
  assert.match(source, /items\.filter\(\(item\) => item\.package_code === this\.selectedPackageCode\)/);
  assert.match(source, /\["内置", this\.packages\.filter/);
  assert.match(source, /\["自定义", this\.packages\.filter/);
  assert.ok(source.indexOf('["自定义", this.packages.filter') < source.indexOf('["内置", this.packages.filter'));
  assert.match(source, /暂无自定义工具包，请先新建/);
  assert.match(source, /this\.packages\.find\(\(item\) => item\.source === "manual"\)/);
  assert.match(source, /class="ui-tool-tree-count" title="工具数量"/);
  assert.doesNotMatch(source, /class="ui-tool-tree-status/);
  assert.match(source, /data-lucide="wrench"/);
  assert.doesNotMatch(source, /<span class="badge">\$\{escapeHtml\(String\(selected\.tool_count/);
  assert.doesNotMatch(source, /meta\.innerHTML = `[\s\S]*source-\$\{escapeHtml\(selected\.source\)/);
  assert.match(template, /class="ui-tool-selected-title">[\s\S]*id="selectedUiToolPackageMeta"[\s\S]*<\/div>[\s\S]*id="selectedUiToolPackageDescription"/);
});

test("UI 工具页保留内部轻卡片，页面外壳直角并衔接导航", () => {
  assert.match(template, /class="ui-tools-workspace ui-tools-light-layout"/);
  assert.match(template, /class="panel-title-copy"[\s\S]*UI 工具（HITL）[\s\S]*panel-subtitle/);
  assert.match(template, /panel-toolbar[\s\S]*id="newUiToolPackageBtn"[\s\S]*data-lucide="plus"[\s\S]*新建工具包/);
  assert.doesNotMatch(template, /class="ui-tool-pane-header"/);
  assert.match(template, /id="editUiToolPackageBtn"[^>]*>[\s\S]*data-lucide="pencil"/);
  assert.match(template, /id="deleteUiToolPackageBtn"[^>]*>[\s\S]*data-lucide="trash-2"/);
  assert.match(template, /id="newUiToolBtn"[^>]*>[\s\S]*data-lucide="plus"/);
  assert.doesNotMatch(template, /class="btn primary" id="newUiToolBtn"/);
  assert.match(source, /class="ui-tool-tree-icon"[\s\S]*data-lucide=/);
  assert.match(source, /window\.lucide\?\.createIcons\(\)/);
  assert.match(styles, /\.ui-tools-workspace\s*\{[^}]*gap: 14px;[^}]*border: 0;[^}]*background: transparent;/);
  assert.match(styles, /\.ui-tool-tree-node\s*\{[^}]*border-radius: 12px;[^}]*background:/);
  assert.match(styles, /\.ui-tool-tree-node\s*\{[^}]*padding: 7px 9px/);
  assert.match(styles, /\.ui-tool-tree-node-head \{[^}]*width: 100%;[^}]*gap: 8px/);
  assert.match(styles, /\.ui-tool-tree-node-meta\s*\{[^}]*margin-left: auto;[^}]*white-space: nowrap/);
  assert.match(styles, /\.ui-tool-content-pane\s*\{[^}]*border-radius: 14px;[^}]*background:/);
  assert.doesNotMatch(styles, /\.ui-tool-selected-header \.row-actions \.btn:not\(\.primary\):not\(\.danger\)/);
  assert.match(styles, /\.ui-tool-selected-header \.row-actions \.btn\s*\{[^}]*border: 1px solid var\(--border\);[^}]*background: var\(--bg-soft\)/);
  assert.match(styles, /\.panel-title-copy \{[^}]*gap: 4px/);
  assert.match(consoleStyles, /--config-radius: 8px/);
  assert.match(consoleStyles, /--config-control-height: 32px/);
  assert.match(consoleStyles, /#configurationHost > \.panel\s*\{[^}]*border: 0;[^}]*border-radius: 0;[^}]*box-shadow: none;/);
  assert.match(consoleStyles, /\.console-main \.configuration-shell\s*\{[^}]*padding: 0;[^}]*border-radius: 0;/);
  assert.match(consoleStyles, /#configurationHost \.ui-tool-tree-node,[\s\S]*border-radius: var\(--config-radius\) !important/);
  assert.match(consoleStyles, /#configurationHost \.btn,[\s\S]*height: var\(--config-control-height\)/);
});

test("工具创建入口依赖当前选中的自定义工具包", () => {
  assert.match(source, /selected\.source !== "manual"/);
  assert.match(source, /请先在左侧选择一个自定义 UI 工具包/);
  assert.match(source, /this\.populatePackageSelect\(selected\.code\)/);
});

test("自定义 UI 工具行操作统一使用纯图标", () => {
  assert.match(source, /class="row-actions ui-tool-row-actions compact-icon-actions"/);
  assert.match(source, /data-edit="\$\{escapeHtml\(tool\.name\)\}" title="编辑" aria-label="编辑"><i data-lucide="pencil"/);
  assert.match(source, /data-delete="\$\{escapeHtml\(tool\.name\)\}"[\s\S]*title="删除" aria-label="删除"><i data-lucide="trash-2"/);
  assert.doesNotMatch(source, /data-edit="\$\{escapeHtml\(tool\.name\)\}">编辑<\/button>/);
  assert.match(styles, /\.compact-icon-actions \.icon-btn\s*\{[\s\S]*width: 28px;[\s\S]*height: 28px;[\s\S]*padding: 0;[\s\S]*border: 0;/);
  assert.match(styles, /\.ui-tool-row-actions \.icon-btn\s*\{\s*border: 1px solid var\(--border\);\s*background: var\(--bg\);/);
});

test("自定义 UI 工具保存时必须提交所属工具包", () => {
  assert.match(template, /type="hidden" id="uiToolPackageCode"/);
  assert.doesNotMatch(template, /<select id="uiToolPackageCode"/);
  assert.match(source, /editingToolPackageCode/);
  assert.match(source, /item\.code === package_code && item\.source === "manual"/);
  assert.match(source, /UI 工具必须归属于创建它的自定义工具包/);
  assert.match(source, /JSON\.stringify\(\{[\s\S]*?package_code/);
});

test("UI 工具直接编辑 JSON Schema 并在保存前校验", () => {
  assert.match(template, /id="uiToolParamsEditor"[^>]*class="mono-textarea schema-json-textarea"/);
  assert.match(template, /id="uiToolResultEditor"[^>]*class="mono-textarea schema-json-textarea"/);
  assert.match(template, /参数顶层类型必须是 object/);
  assert.doesNotMatch(template, /schema-field-header|添加字段/);
  assert.match(source, /DeepAgentSchemaText\?\.parseSchemaText/);
  assert.match(source, /requireObject && schema\.type !== "object"/);
  assert.match(source, /parameters_json_schema = parseUiToolSchema/);
  assert.match(source, /result_schema = parseUiToolSchema/);
  assert.doesNotMatch(source, /uiSchemaEditor\./);
  assert.match(styles, /\.schema-json-textarea\s*\{[^}]*min-height: 180px;/);
});

test("UI 工具基础信息左右布局且三类 JSON 并排编辑", () => {
  assert.match(template, /class="ui-tool-basic-grid"/);
  assert.match(template, /Widget 是前端渲染器标识/);
  assert.match(template, /class="ui-tool-json-grid"/);
  assert.equal((template.match(/class="field field-grow ui-tool-json-field"/g) || []).length, 3);
  assert.match(template, /Metadata（选填）[\s\S]*schema-json-format">JSON</);
  assert.match(styles, /\.ui-tool-basic-grid\s*\{[^}]*grid-template-columns: repeat\(2,/);
  assert.match(styles, /\.ui-tool-json-grid\s*\{[^}]*grid-template-columns: repeat\(3,/);
});

test("内置 UI 工具复用自定义工具弹框并以只读模式展示", () => {
  assert.match(template, /id="saveUiToolBtn"/);
  assert.doesNotMatch(template, /id="uiToolDetailModal"|id="uiToolDetailGrid"/);
  assert.match(source, /document\.getElementById\("uiToolModalTitle"\)\.textContent = `查看 · \$\{tool\.name\}`/);
  assert.match(source, /this\.setFormMode\("readonly"\)/);
  assert.match(source, /this\.loadSchemas\(tool\.parameters_json_schema, tool\.result_schema, \{ readOnly: true \}\)/);
  assert.match(source, /const isReadOnly = mode === "readonly"/);
  assert.match(source, /document\.getElementById\("uiToolDescription"\)\.readOnly = isReadOnly/);
  assert.match(source, /document\.getElementById\("uiToolWidget"\)\.disabled = isReadOnly/);
  assert.match(source, /document\.getElementById\("uiToolMetadata"\)\.readOnly = isReadOnly/);
  assert.match(source, /document\.getElementById\("saveUiToolBtn"\)\.classList\.toggle\("hidden", isReadOnly\)/);
  assert.match(source, /if \(this\.formMode === "readonly"\) return/);
  assert.doesNotMatch(styles, /\.ui-tool-detail-grid/);
});

test("UI 工具包标题使用内缩分割线且工具编辑弹框更紧凑", () => {
  assert.match(styles, /\.ui-tool-selected-header::after\s*\{[^}]*right: 18px;[^}]*left: 18px;[^}]*height: 1px/);
  assert.match(styles, /#uiToolModal\s*\{[^}]*width: min\(1040px, calc\(100vw - 48px\)\)/);
});

test("Agent 能力页只渲染工具包级开关", async () => {
  const agents = await readFile(
    new URL("../../../console/configuration/agents.js", import.meta.url),
    "utf8",
  );
  assert.match(agents, /value="\$\{escapeHtml\(cap\.code\)\}"/);
  assert.doesNotMatch(agents, /name="tool"|data-tool-checkbox/);
});
