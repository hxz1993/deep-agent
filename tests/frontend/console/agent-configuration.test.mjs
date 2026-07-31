import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(
  new URL("../../../frontend/console/configuration/agents.js", import.meta.url),
  "utf8",
);
const template = await readFile(
  new URL("../../../frontend/console/configuration-template.html", import.meta.url),
  "utf8",
);

function loadAgentConfigurationUi() {
  const context = vm.createContext({
    document: { addEventListener() {} },
    escapeHtml(value) {
      return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;");
    },
    window: {},
  });
  vm.runInContext(source, context);
  return context;
}

test("新建和历史 null 能力配置不选择内置 UI 工具", () => {
  const ui = loadAgentConfigurationUi();
  const selected = ui.buildCapabilitySelectionSet(null, [
    { name: "native", default_enabled: true },
    { name: "skill:optional", default_enabled: false },
    { name: "ui:builtin__confirm", default_enabled: false, locked: false },
    { name: "ui:builtin__choice", default_enabled: false, locked: false },
  ]);

  assert.deepEqual([...selected], ["native"]);
});

test("内置 UI 工具与手工 UI 工具使用相同可选能力编码", () => {
  const ui = loadAgentConfigurationUi();
  const selected = ui.buildCapabilitySelectionSet(
    ["ui:builtin__confirm", "ui:demo__form"],
    [
      { name: "ui:builtin__confirm", default_enabled: false, locked: false },
      { name: "ui:builtin__choice", default_enabled: false, locked: false },
      { name: "ui:demo__form", default_enabled: false, locked: false },
    ],
  );

  assert.deepEqual(
    [...selected],
    ["ui:builtin__confirm", "ui:demo__form"],
  );
});

test("新建 Agent 使用 null 初始化默认能力而不是空数组", () => {
  assert.match(
    source,
    /openCreate\(\)[\s\S]*?renderCapabilityCheckboxes\(null\)/,
  );
});

test("Native 和 Attachment 合并为内置工具分组但保持独立编码", () => {
  const ui = loadAgentConfigurationUi();
  const grouped = ui.groupCapabilities([
    { name: "native", kind: "native", label: "通用工具" },
    { name: "attachment", kind: "attachment", label: "附件读取" },
    { name: "mcp:demo", kind: "mcp" },
  ]);

  assert.deepEqual([...grouped.groups], ["builtin", "mcp"]);
  assert.deepEqual(
    Array.from(grouped.byGroup.builtin, (capability) => capability.name),
    ["native", "attachment"],
  );
  assert.equal(ui.capabilityGroup({ kind: "native" }), "builtin");
  assert.equal(ui.capabilityGroup({ kind: "attachment" }), "builtin");
  assert.equal(ui.capabilityGroup({ kind: "mcp" }), "mcp");
});

test("Agent 内置工具卡片优先展示中文名称和技术类型", () => {
  const ui = loadAgentConfigurationUi();
  const card = ui.window.agentsAdmin.renderCapCard(
    {
      name: "native",
      kind: "native",
      label: "通用工具",
      source: "builtin",
      description: "进程内通用工具",
      default_enabled: true,
      tools: [{ name: "get_current_time", description: "获取当前 UTC 时间。" }],
    },
    new Set(["native"]),
  );

  assert.match(card, /通用工具/);
  assert.match(card, /Native/);
  assert.match(card, /value="native"/);
  assert.match(card, /get_current_time/);
});

test("编辑 Agent 时在弹窗标题栏显示完整 Agent ID", () => {
  assert.match(template, /id="agentModalId"/);
  assert.match(
    source,
    /agentModalId"\)\.textContent = `Agent ID：\$\{agent\.id \|\| agentId\}`/,
  );
  assert.doesNotMatch(template, /<th>Agent ID<\/th>/);
});
