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
    { code: "builtin:general", default_enabled: true, enabled: true },
    { code: "builtin:sandbox", default_enabled: false, enabled: true },
    { code: "skill:optional", default_enabled: false, enabled: true },
    { code: "ui:builtin", default_enabled: false, enabled: true },
  ]);

  assert.deepEqual([...selected], ["builtin:general"]);
});

test("内置 UI 工具与手工 UI 工具使用相同可选能力编码", () => {
  const ui = loadAgentConfigurationUi();
  const selected = ui.buildCapabilitySelectionSet(
    ["ui:builtin", "ui:demo"],
    [
      { code: "ui:builtin", default_enabled: false, enabled: true },
      { code: "ui:demo", default_enabled: false, enabled: true },
    ],
  );

  assert.deepEqual(
    [...selected],
    ["ui:builtin", "ui:demo"],
  );
});

test("新建 Agent 使用 null 初始化默认能力而不是空数组", () => {
  assert.match(
    source,
    /openCreate\(\)[\s\S]*?renderCapabilityCheckboxes\(null\)/,
  );
});

test("后端 category 直接驱动工具包分组", () => {
  const ui = loadAgentConfigurationUi();
  const grouped = ui.groupCapabilities([
    { code: "builtin:general", category: "builtin", label: "通用工具" },
    { code: "builtin:sandbox", category: "builtin", label: "Python 沙箱" },
    { code: "builtin:attachment", category: "builtin", label: "附件读取" },
    { code: "mcp:demo", category: "mcp" },
  ]);

  assert.deepEqual([...grouped.groups], ["builtin", "mcp"]);
  assert.deepEqual(
    Array.from(grouped.byGroup.builtin, (toolPackage) => toolPackage.code),
    ["builtin:general", "builtin:sandbox", "builtin:attachment"],
  );
  assert.equal(ui.capabilityGroup({ category: "builtin" }), "builtin");
  assert.equal(ui.capabilityGroup({ category: "mcp" }), "mcp");
});

test("Agent 能力页提交独立 Sandbox 工具包编码", () => {
  const ui = loadAgentConfigurationUi();
  const card = ui.window.agentsAdmin.renderCapCard(
    {
      code: "builtin:sandbox",
      category: "builtin",
      label: "Python 沙箱",
      source: "builtin",
      description: "执行受控 Python 代码",
      default_enabled: false,
      tools: [{ name: "execute_python", description: "执行 Python。" }],
    },
    new Set(["builtin:sandbox"]),
  );

  assert.match(card, /Python 沙箱/);
  assert.match(card, /value="builtin:sandbox"/);
  assert.match(card, /execute_python/);
});

test("Agent 内置工具卡片优先展示中文名称和技术类型", () => {
  const ui = loadAgentConfigurationUi();
  const card = ui.window.agentsAdmin.renderCapCard(
    {
      code: "builtin:general",
      category: "builtin",
      label: "通用工具",
      source: "builtin",
      description: "进程内通用工具",
      default_enabled: true,
      tools: [{ name: "get_current_time", description: "获取当前 UTC 时间。" }],
    },
    new Set(["builtin:general"]),
  );

  assert.match(card, /通用工具/);
  assert.match(card, /内置/);
  assert.match(card, /value="builtin:general"/);
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

test("System Prompt 标题栏展示统一后端估算的字符和 Token", () => {
  const ui = loadAgentConfigurationUi();

  assert.match(template, /id="agentPromptEstimate"/);
  assert.match(template, /0 字 \/ 0 Token/);
  assert.match(source, /apiFetch\("\/api\/v1\/agents\/prompt-estimate"/);
  assert.match(source, /model_id: modelId, prompt/);
  assert.match(source, /schedulePromptEstimate\(true\)/);
  assert.equal(ui.formatCompactCount(999), "999");
  assert.equal(ui.formatCompactCount(12_600), "12.6K");
  assert.equal(ui.formatCompactCount(200_000), "200K");
  assert.equal(ui.formatCompactCount(1_250_000), "1.3M");
});
