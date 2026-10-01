import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(
  new URL("../../../console/configuration/builtin-tools.js", import.meta.url),
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

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function loadBuiltinToolsUi(customDocument) {
  const window = {};
  const document = customDocument || {
    addEventListener() {},
    getElementById() {
      return null;
    },
  };
  vm.runInContext(
    source,
    vm.createContext({
      apiFetch: async () => [],
      document,
      escapeHtml,
      loadCapabilities: async () => [],
      state: { capabilities: [] },
      window,
    }),
  );
  return window.__BuiltinToolsUI;
}

test("设置目录按模型、工具、MCP、Skill排列", () => {
  const providerPosition = indexHtml.indexOf('data-tab="llm-providers"');
  const skillPosition = indexHtml.indexOf('data-tab="skills"');
  const builtinPosition = indexHtml.indexOf('data-tab="tools"');
  const mcpPosition = indexHtml.indexOf('data-tab="mcp"');
  const uiPosition = indexHtml.indexOf('data-tab="ui-tools"');

  assert.ok(providerPosition >= 0 && providerPosition < builtinPosition);
  assert.ok(builtinPosition < mcpPosition);
  assert.ok(mcpPosition < skillPosition);
  assert.equal(uiPosition, -1);
  assert.match(templateHtml, /id="panel-builtin-tools"/);
  assert.match(source, /预制工具[\s\S]*运行时工具/);
  assert.doesNotMatch(templateHtml, /data-builtin-tab|builtinPresetPanel|builtinRuntimePanel/);
  assert.doesNotMatch(templateHtml, /refreshBuiltinToolsBtn|refreshSystemToolsBtn/);
});

test("内置工具页只接受后端 builtin 分类工具包", () => {
  const ui = loadBuiltinToolsUi();

  assert.equal(
    ui.isManagedBuiltinCapability({ source: "builtin", category: "builtin" }),
    true,
  );
  assert.equal(
    ui.isManagedBuiltinCapability({ source: "builtin", category: "ui" }),
    false,
  );
  assert.equal(
    ui.isManagedBuiltinCapability({ source: null, category: "builtin" }),
    false,
  );
});

test("内置工具列表展示能力和工具且没有资源级状态或管理操作", () => {
  const ui = loadBuiltinToolsUi();
  const row = ui.toolCatalogDetails({
    code: "builtin:general",
    category: "builtin",
    label: "通用工具",
    source: "builtin",
    description: "进程内通用工具。",
    tools: [{ name: "get_current_time", description: "获取当前 UTC 时间。" }],
  });

  assert.match(row, /通用工具/);
  assert.match(row, /builtin:general/);
  assert.match(row, /get_current_time/);
  assert.doesNotMatch(row, /默认启用|默认关闭|启用|停用/);
  assert.doesNotMatch(row, /始终可用|有附件时可用/);
  assert.doesNotMatch(row, /编辑|删除|保存|checkbox|switch/);
});

test("Python 沙箱作为独立的可关联内置工具包展示", () => {
  const ui = loadBuiltinToolsUi();
  const row = ui.toolCatalogDetails({
    code: "builtin:sandbox",
    category: "builtin",
    label: "Python 沙箱",
    source: "builtin",
    description: "执行受控 Python 代码。",
    tools: [{ name: "execute_python", description: "执行 Python。" }],
  });

  assert.match(row, /<h3>沙箱<\/h3>/);
  assert.doesNotMatch(row, /Python 沙箱/);
  assert.match(row, /builtin:sandbox/);
  assert.match(row, /execute_python/);
  assert.doesNotMatch(row, /默认启用|默认关闭|启用|停用/);
});

test("附件读取不再作为可配置内置工具包", () => {
  assert.doesNotMatch(source, /builtin:attachment|read_text_attachment|read_docx|read_xlsx/);
});


test("两组目录共用详情，异步加载第二组不会丢失第一组选择", () => {
  const host = { innerHTML: "", querySelectorAll: () => [], querySelector: () => null };
  const ui = loadBuiltinToolsUi({ getElementById: () => host });
  ui.updateToolCatalogGroup(false, [{ code: "builtin:sandbox", label: "Python 沙箱", tools: [{ name: "execute_python" }] }]);
  ui.updateToolCatalogGroup(true, [{ code: "runtime:planning", label: "任务规划", tools: [{ name: "write_plan" }] }]);
  assert.match(host.innerHTML, /预制工具/);
  assert.match(host.innerHTML, /运行时工具/);
  assert.match(host.innerHTML, /data-tool-catalog-code="builtin:sandbox" aria-pressed="true"/);
  assert.match(host.innerHTML, /execute_python/);
  assert.doesNotMatch(host.innerHTML, /write_plan|Python 沙箱/);
  ui.updateToolCatalogGroup(false, []);
  assert.match(host.innerHTML, /write_plan/);
  assert.match(host.innerHTML, /实现来源/);
  assert.match(host.innerHTML, /自动注入条件/);
});

test("目录项与工具说明转义外部字符串", () => {
  const ui = loadBuiltinToolsUi();
  const item = { code: '" onclick="bad', label: '<img src=x>', description: '<script>bad</script>', tools: [{ name: '<tool>', description: '<img>' }] };
  const html = ui.toolCatalogCard(item, true) + ui.toolCatalogDetails(item);
  assert.doesNotMatch(html, /<img|<script|<tool>/);
  assert.match(html, /&quot;/);
  assert.match(html, /&lt;script&gt;/);
});


test("web_search 仅显示配置摘要和前往常规设置入口", () => {
  const ui = loadBuiltinToolsUi();
  const html = ui.toolCatalogDetails({ code: "builtin:web", tools: [{ name: "web_search" }, { name: "web_fetch" }] });
  assert.equal((html.match(/data-web-search-settings/g) || []).length, 1);
  assert.match(html, /web_search[\s\S]*data-web-search-settings[\s\S]*data-lucide="pencil"/);
  assert.doesNotMatch(html, /<input|<select/);
});

test("read_document 作为可关联工具展示且提供文档解析设置入口", () => {
  const ui = loadBuiltinToolsUi();
  const html = ui.toolCatalogDetails({ code: "builtin:document", label: "文档解析", description: "扫描件和复杂表格解析。", tools: [{ name: "read_document", description: "读取文档，可从当前 Run 缓存续读。" }] });
  assert.match(html, /<h3>文档解析<\/h3>/);
  assert.equal((html.match(/data-document-parsing-settings/g) || []).length, 1);
  assert.match(html, /read_document[\s\S]*data-document-parsing-settings[\s\S]*data-lucide="pencil"/);
  assert.doesNotMatch(html, /自动注入条件|<input|<select|保存/);
});
