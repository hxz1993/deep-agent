import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(
  new URL("../../../frontend/console/configuration/builtin-tools.js", import.meta.url),
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

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function loadBuiltinToolsUi() {
  const window = {};
  const document = {
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

test("内置工具页位于模型之后和 MCP 之前", () => {
  const modelPosition = indexHtml.indexOf('data-tab="llm-models"');
  const builtinPosition = indexHtml.indexOf('data-tab="builtin-tools"');
  const mcpPosition = indexHtml.indexOf('data-tab="mcp"');

  assert.ok(modelPosition < builtinPosition);
  assert.ok(builtinPosition < mcpPosition);
  assert.match(templateHtml, /id="panel-builtin-tools"/);
  assert.match(templateHtml, /内置工具随 DeepAgent 版本发布，无需注册/);
});

test("内置工具页只接受 Native 和 Attachment 后端能力", () => {
  const ui = loadBuiltinToolsUi();

  assert.equal(
    ui.isManagedBuiltinCapability({ source: "builtin", kind: "native" }),
    true,
  );
  assert.equal(
    ui.isManagedBuiltinCapability({ source: "builtin", kind: "attachment" }),
    true,
  );
  assert.equal(
    ui.isManagedBuiltinCapability({ source: "builtin", kind: "ui-client" }),
    false,
  );
  assert.equal(
    ui.isManagedBuiltinCapability({ source: null, kind: "mcp" }),
    false,
  );
});

test("内置工具列表展示能力、工具和默认状态且没有管理操作", () => {
  const ui = loadBuiltinToolsUi();
  const row = ui.builtinCapabilityRow({
    name: "native",
    kind: "native",
    label: "通用工具",
    source: "builtin",
    description: "进程内通用工具。",
    default_enabled: true,
    tools: [{ name: "get_current_time", description: "获取当前 UTC 时间。" }],
  });

  assert.match(row, /通用工具/);
  assert.match(row, /Native/);
  assert.match(row, /get_current_time/);
  assert.match(row, /默认启用/);
  assert.doesNotMatch(row, /编辑|删除|保存|checkbox|switch/);
});
