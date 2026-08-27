import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = await readFile(
  new URL("../../../frontend/console/configuration/ui-tools.js", import.meta.url),
  "utf8",
);
const template = await readFile(
  new URL("../../../frontend/console/configuration-template.html", import.meta.url),
  "utf8",
);
const styles = await readFile(
  new URL("../../../frontend/console/configuration/configuration.css", import.meta.url),
  "utf8",
);
const consoleStyles = await readFile(
  new URL("../../../frontend/console/console.css", import.meta.url),
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

test("UI 工具页使用与 Skill 一致的分离轻卡片层级", () => {
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
  assert.match(consoleStyles, /#configurationHost > \.panel\s*\{[^}]*border-radius: var\(--console-card-radius\) !important/);
  assert.match(consoleStyles, /#configurationHost \.ui-tool-tree-node,[\s\S]*border-radius: var\(--config-radius\) !important/);
  assert.match(consoleStyles, /#configurationHost \.btn,[\s\S]*height: var\(--config-control-height\)/);
});

test("工具创建入口依赖当前选中的自定义工具包", () => {
  assert.match(source, /selected\.source !== "manual"/);
  assert.match(source, /请先在左侧选择一个自定义 UI 工具包/);
  assert.match(source, /this\.populatePackageSelect\(selected\.code\)/);
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

test("Agent 能力页只渲染工具包级开关", async () => {
  const agents = await readFile(
    new URL("../../../frontend/console/configuration/agents.js", import.meta.url),
    "utf8",
  );
  assert.match(agents, /value="\$\{escapeHtml\(cap\.code\)\}"/);
  assert.doesNotMatch(agents, /name="tool"|data-tool-checkbox/);
});
