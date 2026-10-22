import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(
  new URL("../../../console/configuration/agents.js", import.meta.url),
  "utf8",
);
const template = await readFile(
  new URL("../../../console/configuration-template.html", import.meta.url),
  "utf8",
);

test("允许文本输出位于右侧定义栏顶部，共用可聚焦说明与页头保存", () => {
  const heading = template.match(/<div class="agent-output-heading">[^]*?<\/div><\/div>/)?.[0] || "";
  assert.match(heading, /id="agentOutputEnabled"/);
  assert.doesNotMatch(heading, /agentOutputAllowText/);
  assert.match(template, /class="agent-output-definition"[^]*?id="agentOutputAllowTextControl"[^]*?id="agentOutputAllowText"[^]*?允许文本输出[^]*?结构设置/);
  assert.match(template, /<\/label><span class="field-help" tabindex="0" aria-label="允许文本输出说明" aria-describedby="agentOutputAllowTextHelp"/);
  assert.match(template, /id="agentOutputAllowTextHelp" role="tooltip">勾选后，最终结果可选择以普通 Markdown 文本返回，或按下方结构返回 JSON；不勾选则必须返回符合结构的 JSON。字段“必须”仅约束 JSON 内容。/);
  assert.equal((template.match(/id="agentOutputAllowText"/g) || []).length, 1);
  assert.match(template, /<form id="agentForm"[^]*?id="agentOutputAllowText"[^]*?<\/form>/);
});

test("结构设置使用标题和三个操作按钮，取消定义方式分段控件", () => {
  const definition = template.match(/<div class="agent-output-definition">[^]*?id="agentOutputFields"/)?.[0] || "";
  assert.match(definition, /class="agent-output-definition-title">结构设置<\/span>/);
  assert.match(definition, /id="agentOutputSchemaTab"[^]*?查看 JSON[^]*?id="agentOutputAdd"[^]*?添加结构[^]*?id="agentOutputCheck"[^]*?检查定义/);
  assert.doesNotMatch(definition, /agent-view-switch|agentOutputFieldsTab|字段配置/);
});

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

test("新建和历史 null 能力配置都不隐式选择任何工具包", () => {
  const ui = loadAgentConfigurationUi();
  const selected = ui.buildCapabilitySelectionSet(null, [
    { code: "builtin:general" },
    { code: "builtin:sandbox" },
    { code: "mcp:optional" },
    { code: "ui:builtin" },
  ]);

  assert.deepEqual([...selected], []);
});

test("内置 UI 工具与手工 UI 工具使用相同可选能力编码", () => {
  const ui = loadAgentConfigurationUi();
  const selected = ui.buildCapabilitySelectionSet(
    ["ui:builtin", "ui:demo"],
    [
      { code: "ui:builtin" },
      { code: "ui:demo" },
    ],
  );

  assert.deepEqual(
    [...selected],
    ["ui:builtin", "ui:demo"],
  );
});

test("新建 Agent 使用空数组初始化工具包关联", () => {
  assert.match(
    source,
    /openCreate\(\)[\s\S]*?renderCapabilityCheckboxes\(\[\]\)/,
  );
});

test("后端 category 直接驱动工具包分组", () => {
  const ui = loadAgentConfigurationUi();
  const grouped = ui.groupCapabilities([
    { code: "builtin:general", category: "builtin", label: "通用工具" },
    { code: "builtin:sandbox", category: "builtin", label: "Python 沙箱" },
    { code: "mcp:demo", category: "mcp" },
  ]);

  assert.deepEqual([...grouped.groups], ["builtin", "mcp"]);
  assert.deepEqual(
    Array.from(grouped.byGroup.builtin, (toolPackage) => toolPackage.code),
    ["builtin:general", "builtin:sandbox"],
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
      tools: [{ name: "execute_python", description: "执行 Python。" }],
    },
    new Set(["builtin:sandbox"]),
  );

  assert.match(card, /Python 沙箱/);
  assert.match(card, /value="builtin:sandbox"/);
  assert.doesNotMatch(card, /execute_python/);
  assert.match(card, /data-lucide="hammer"/);
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
      tools: [{ name: "get_current_time", description: "获取当前 UTC 时间。" }],
    },
    new Set(["builtin:general"]),
  );

  assert.match(card, /通用工具/);
  assert.match(card, /内置/);
  assert.match(card, /value="builtin:general"/);
  assert.doesNotMatch(card, /get_current_time/);
  assert.match(card, /data-lucide="hammer"/);
});

test("Agent 工具包卡片不展示内部披露策略且不提供工具级复选框", () => {
  const ui = loadAgentConfigurationUi();
  const card = ui.window.agentsAdmin.renderCapCard(
    {
      code: "mcp:deepfos",
      category: "mcp",
      label: "deepfos",
      source: "database",
      description: "DeepFOS 平台工具",
      tool_count: 24,
      disclosure_mode: "search",
      preselect_limit: 3,
      search_result_limit: 5,
      tools: [],
    },
    new Set(["mcp:deepfos"]),
  );

  assert.doesNotMatch(card, /自动预选|搜索 Top|disclosure-badge/);
  assert.match(card, /value="mcp:deepfos"/);
  assert.doesNotMatch(card, /tool-checkbox|name="tools"/);
});

test("Agent 将 Skill 与工具包分开配置", () => {
  assert.doesNotMatch(source, /skill:\s*"Skills"/);
  assert.match(template, /id="agentSkillChoices"/);
  assert.match(source, /skills: this\.getSelectedSkills\(\)/);
  assert.match(source, /input type="checkbox" name="agentSkill"/);
  assert.match(source, /该 Skill 当前不存在，可关闭开关解除关联/);
});

test("Agent 资源在顶部按内置、UI、MCP、Skill 排列四个 Tab", () => {
  assert.match(template, /id="capTabs"[\s\S]*data-kind="builtin"[\s\S]*data-kind="ui"[\s\S]*data-kind="mcp"[\s\S]*data-kind="skills"/);
  assert.doesNotMatch(template, /id="capSearch"|data-agent-editor-tab|data-agent-editor-pane/);
  assert.match(source, /counter\.textContent = String\(selected\)/);
  assert.doesNotMatch(source, /未选择|capabilityDisclosureLabel|filterCaps/);
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
