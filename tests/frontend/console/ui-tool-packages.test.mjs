import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

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
const editorStyles = await readFile(
  new URL("../../../console/ui-tool-editor.css", import.meta.url), "utf8",
);
const consoleStyles = await readFile(
  new URL("../../../console/console.css", import.meta.url),
  "utf8",
);
const sharedPrimitives = await readFile(new URL("../../../console/ui-primitives.css", import.meta.url), "utf8");

test("UI 工具页使用左侧工具包树和右侧单一工具表", () => {
  assert.match(template, /id="uiToolPackageTree"/);
  assert.match(template, /id="uiToolsTableBody"/);
  assert.doesNotMatch(template, /id="uiToolPackagesTableBody"/);
  assert.equal((template.match(/id="uiToolsTableBody"/g) || []).length, 1);
  assert.match(template, /由 Agent 关联使用/);
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
  assert.doesNotMatch(source, /data-lucide="wrench"/);
  assert.doesNotMatch(source, /<span class="badge">\$\{escapeHtml\(String\(selected\.tool_count/);
  assert.doesNotMatch(source, /meta\.innerHTML = `[\s\S]*source-\$\{escapeHtml\(selected\.source\)/);
  assert.match(template, /class="ui-tool-selected-title">[\s\S]*id="selectedUiToolPackageMeta"[\s\S]*<\/div>[\s\S]*id="selectedUiToolPackageDescription"/);
});

test("UI 工具页保留内部轻卡片，页面外壳直角并衔接导航", () => {
  assert.match(template, /class="ui-tools-workspace ui-tools-light-layout"/);
  assert.match(template, /class="panel-title-copy"[\s\S]*<h2>工具<\/h2>[\s\S]*panel-subtitle/);
  assert.match(template, /panel-toolbar[\s\S]*id="newUiToolPackageBtn"[\s\S]*data-lucide="plus"[\s\S]*新建/);
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
  assert.match(sharedPrimitives, /--ui-radius: 8px/);
  assert.match(sharedPrimitives, /--config-radius: var\(--ui-radius\)/);
  assert.match(sharedPrimitives, /--ui-control-height: 32px/);
  assert.match(sharedPrimitives, /--config-control-height: var\(--ui-control-height\)/);
  assert.match(consoleStyles, /#configurationHost > \.panel\s*\{[^}]*border: 0;[^}]*border-radius: 0;[^}]*box-shadow: none;/);
  assert.match(consoleStyles, /\.console-main \.configuration-shell\s*\{[^}]*padding: 0;[^}]*border-radius: 0;/);
  assert.match(sharedPrimitives, /#configurationHost \.ui-tool-tree-node,[\s\S]*border-radius: var\(--config-radius\) !important/);
  assert.match(sharedPrimitives, /height: var\(--config-control-height\)/);
});

test("工具创建入口依赖当前选中的自定义工具包", () => {
  assert.match(source, /selected\.source !== "manual"/);
  assert.match(source, /请先在左侧选择一个自定义 UI 工具包/);
  assert.match(source, /this\.populatePackageSelect\(selected\.code\)/);
});

test("UI 工具点击行打开弹框，行内仅保留删除", () => {
  assert.match(source, /class="row-actions ui-tool-row-actions compact-icon-actions"/);
  assert.doesNotMatch(source, /data-edit=|data-view=|tool\.updated_at/);
  assert.match(source, /data-open-ui-tool=/);
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

test("UI 工具为紧凑单列布局，三类 JSON 按标签切换", () => {
  const start = template.indexOf('id="uiToolModal"');
  const modal = template.slice(start, template.indexOf("</dialog>", start));
  assert.ok(modal.indexOf('id="uiToolName"') < modal.indexOf('id="uiToolWidget"'));
  assert.ok(modal.indexOf('id="uiToolWidget"') < modal.indexOf('id="uiToolDescription"'));
  assert.match(modal, /role="tablist" aria-label="工具结构"/);
  assert.equal((modal.match(/role="tabpanel"/g) || []).length, 3);
  assert.equal((modal.match(/data-schema-panel="[^"]+" hidden/g) || []).length, 2);
  assert.doesNotMatch(modal, /uiToolWidgetCustom|<select id="uiToolWidget"/);
  assert.match(editorStyles, /#uiToolModal\s*\{[^}]*width: min\(560px,/);
  assert.match(editorStyles, /#uiToolModal \.ui-tool-basic-grid\s*\{[^}]*grid-template-columns: minmax\(0, 1fr\)/);
});

test("内置 UI 工具复用自定义工具弹框并以只读模式展示", () => {
  assert.match(template, /id="saveUiToolBtn"/);
  assert.doesNotMatch(template, /id="uiToolDetailModal"|id="uiToolDetailGrid"/);
  assert.match(source, /document\.getElementById\("uiToolModalTitle"\)\.textContent = `查看 · \$\{tool\.name\}`/);
  assert.match(source, /this\.setFormMode\("readonly"\)/);
  assert.match(source, /this\.loadSchemas\(tool\.parameters_json_schema, tool\.result_schema, \{ readOnly: true \}\)/);
  assert.match(source, /const isReadOnly = mode === "readonly"/);
  assert.match(source, /document\.getElementById\("uiToolDescription"\)\.readOnly = isReadOnly/);
  assert.match(source, /document\.getElementById\("uiToolWidget"\)\.readOnly = isReadOnly/);
  assert.match(source, /document\.getElementById\("uiToolMetadata"\)\.readOnly = isReadOnly/);
  assert.match(source, /document\.getElementById\("saveUiToolBtn"\)\.classList\.toggle\("hidden", isReadOnly\)/);
  assert.match(source, /if \(this\.formMode === "readonly"\) return/);
  assert.doesNotMatch(styles, /\.ui-tool-detail-grid/);
});

test("UI 工具包标题使用内缩分割线且工具编辑弹框更紧凑", () => {
  assert.doesNotMatch(styles, /\.ui-tool-selected-header::after/);
  assert.match(editorStyles, /#uiToolModal\s*\{[^}]*width: min\(560px, calc\(100vw - 32px\)\)/);
});

test("Agent 能力页只渲染工具包级开关", async () => {
  const agents = await readFile(
    new URL("../../../console/configuration/agents.js", import.meta.url),
    "utf8",
  );
  assert.match(agents, /value="\$\{escapeHtml\(cap\.code\)\}"/);
  assert.doesNotMatch(agents, /name="tool"|data-tool-checkbox/);
});

function controllerWith(elements) {
  const sandbox = { window: {}, document: { getElementById: (id) => elements[id], addEventListener() {} } };
  vm.runInNewContext(source, sandbox);
  return sandbox.window.uiToolsAdmin;
}

test("Widget 保留未知标识和 custom 原值，清空才返回 null", () => {
  const input = { value: "" };
  const admin = controllerWith({ uiToolWidget: input });
  for (const value of ["confirm", "date_picker", "custom", "client_registered_form"]) {
    admin.setWidgetValue(value);
    assert.equal(admin.getWidgetValue(), value);
  }
  input.value = "  client_form  ";
  assert.equal(admin.getWidgetValue(), "client_form");
  admin.setWidgetValue(null);
  assert.equal(admin.getWidgetValue(), null);
  input.value = "  ";
  assert.equal(admin.getWidgetValue(), null);
});

test("切换 Schema Tabs 保留三个草稿并只显示所选面板", () => {
  const keys = ["params", "result", "metadata"];
  const tabs = keys.map((key) => ({ dataset: { schemaTab: key }, attributes: {}, setAttribute(name, value) { this.attributes[name] = value; }, focus() { this.focused = true; } }));
  const panels = keys.map((key) => ({ dataset: { schemaPanel: key }, hidden: false, draft: `{${key}}` }));
  const format = { textContent: "" };
  const admin = controllerWith({ uiToolSchemaFormat: format, uiToolModal: { querySelectorAll: (selector) => selector === "[data-schema-tab]" ? tabs : panels } });
  admin.selectSchemaTab("metadata", { focus: true });
  assert.deepEqual(panels.map((panel) => panel.hidden), [true, true, false]);
  assert.deepEqual(tabs.map((tab) => tab.tabIndex), [-1, -1, 0]);
  assert.equal(tabs[2].focused, true);
  assert.equal(format.textContent, "JSON");
  admin.selectSchemaTab("params");
  assert.equal(format.textContent, "JSON Schema");
  assert.deepEqual(panels.map((panel) => panel.draft), ["{params}", "{result}", "{metadata}"]);
  assert.deepEqual(panels.map((panel) => panel.hidden), [false, true, true]);
});


test("隐藏参数页签的 required 校验会先切回并聚焦参数，不进入保存", () => {
  const keys = ["params", "result", "metadata"];
  const tabs = keys.map((key) => ({ dataset: { schemaTab: key }, setAttribute() {}, addEventListener() {} }));
  const panels = keys.map((key) => ({ dataset: { schemaPanel: key }, hidden: key !== "metadata" }));
  const listeners = new Map();
  const modal = {
    querySelectorAll: (selector) => selector === "[data-schema-tab]" ? tabs : panels,
    addEventListener(type, callback, capture) { listeners.set(type, { callback, capture }); },
  };
  const admin = controllerWith({ uiToolModal: modal });
  let saved = 0;
  admin.save = () => { saved += 1; };
  admin.initSchemaTabs();
  const invalid = listeners.get("invalid");
  assert.equal(invalid.capture, true, "invalid does not bubble and must be captured");
  let focused = false;
  const textarea = {
    closest: () => panels[0],
    focus() {
      assert.equal(panels[0].hidden, false, "required control must be visible before focus");
      focused = true;
    },
  };
  invalid.callback({ target: textarea });
  assert.deepEqual(panels.map((panel) => panel.hidden), [false, true, true]);
  assert.equal(focused, true);
  assert.equal(saved, 0);
});
