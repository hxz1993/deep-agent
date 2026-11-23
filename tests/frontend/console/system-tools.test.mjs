import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(
  new URL("../../../console/configuration/system-tools.js", import.meta.url),
  "utf8",
);
const builtinSource = await readFile(new URL("../../../console/configuration/builtin-tools.js", import.meta.url), "utf8");
const catalogCss = await readFile(new URL("../../../console/ui-tool-catalog.css", import.meta.url), "utf8");
const indexHtml = await readFile(
  new URL("../../../console/index.html", import.meta.url),
  "utf8",
);
const templateHtml = await readFile(
  new URL("../../../console/configuration-template.html", import.meta.url),
  "utf8",
);
const configurationCss = await readFile(
  new URL("../../../console/configuration/configuration.css", import.meta.url),
  "utf8",
);

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function loadSystemToolsUi() {
  const window = {};
  const document = {
    addEventListener() {},
    getElementById() {
      return null;
    },
  };
  vm.runInContext(
    builtinSource + "\n" + source,
    vm.createContext({ apiFetch: async () => [], document, escapeHtml, window }),
  );
  return { ...window.__SystemToolsUI, systemToolDomain: (domain) => window.__BuiltinToolsUI.toolCatalogDetails(domain, true) };
}

test("系统工具合并进内置工具目录", () => {
  const builtinPosition = indexHtml.indexOf('data-tab="tools"');
  const systemPosition = indexHtml.indexOf('data-tab="system-tools"');
  const mcpPosition = indexHtml.indexOf('data-tab="mcp"');
  const uiPosition = indexHtml.indexOf('data-tab="ui-tools"');

  assert.ok(builtinPosition >= 0 && builtinPosition < mcpPosition);
  assert.equal(uiPosition, -1);
  assert.equal(systemPosition, -1);
  assert.doesNotMatch(templateHtml, /id="panel-system-tools"/);
  assert.match(templateHtml, /id="panel-builtin-tools"[\s\S]*id="builtinToolCatalog"/);
  assert.doesNotMatch(templateHtml, /builtin-catalog-tabs/);
});

test("系统工具按领域展示来源、条件和说明", () => {
  const ui = loadSystemToolsUi();
  const html = ui.systemToolDomain({
    code: "runtime:skill",
    label: "Skill",
    description: "按需加载 Skill。",
    tools: [
      {
        name: "load_capability",
        source: "Pydantic AI Harness",
        injection_condition: "Agent 已配置可用 Skill",
        description: "加载完整说明。",
      },
    ],
  });

  assert.match(html, /runtime:skill/);
  assert.match(html, /load_capability/);
  assert.match(html, /Pydantic AI Harness/);
  assert.match(html, /Agent 已配置可用 Skill/);
  assert.match(html, /加载完整说明/);
  assert.doesNotMatch(html, /编辑|删除|启用|checkbox|switch/);
  assert.match(html, /tool-catalog-detail-header/);
  assert.match(html, /按需加载 Skill。/);
  assert.match(html, /tool-catalog-runtime-table/);
});

test("任务规划只暴露完整快照写入工具", () => {
  const ui = loadSystemToolsUi();
  const html = ui.systemToolDomain({
    code: "runtime:planning",
    label: "任务规划",
    tools: ["write_plan"].map((name) => ({ name, source: "Pydantic AI Harness" })),
  });
  assert.match(html, /runtime:planning/);
  assert.match(html, /write_plan/);
  assert.doesNotMatch(html, /read_plan|update_task_statuses/);
  assert.doesNotMatch(html, /add_task|update_task_status<|remove_task/);
  assert.doesNotMatch(html, /checkbox|switch/);
});

test("运行环境作为只读运行时领域展示时间工具与时区说明", () => {
  const ui = loadSystemToolsUi();
  const html = ui.systemToolDomain({
    code: "runtime:environment",
    label: "运行环境",
    description: "所有业务 Agent 默认可用，时间默认 UTC。",
    tools: [{ name: "get_current_time", display_name: "获取当前时间", source: "DeepAgent", injection_condition: "所有业务 Agent Run 自动注入", description: "支持可选 timezone 参数，例如 Asia/Shanghai。" }],
  });
  assert.match(html, /runtime:environment/);
  assert.match(html, /运行环境/);
  assert.match(html, /get_current_time/);
  assert.match(html, /timezone/);
  assert.match(html, /Asia\/Shanghai/);
  assert.doesNotMatch(html, /checkbox|switch|builtin:general/);
});

test("Skill 三个工具归 Skill，文件工具独立归文件领域", () => {
  const ui = loadSystemToolsUi();
  const skillHtml = ui.systemToolDomain({
    code: "runtime:skill",
    label: "Skill",
    tools: [
      { name: "load_capability" },
      { name: "list_skill_resources" },
      { name: "execute_skill_script" },
    ],
  });
  const fileHtml = ui.systemToolDomain({
    code: "runtime:file",
    label: "文件",
    tools: [
      { name: "read_file" },
      { name: "export_file_to_download_center" },
    ],
  });
  assert.match(skillHtml, /load_capability/);
  assert.match(skillHtml, /list_skill_resources/);
  assert.match(skillHtml, /execute_skill_script/);
  assert.doesNotMatch(skillHtml, /read_file/);
  assert.match(fileHtml, /read_file/);
  assert.match(fileHtml, /export_file_to_download_center/);
});

test("大工具结果读取详情保留真实来源和只读行为", () => {
  const ui = loadSystemToolsUi();
  const html = ui.systemToolDomain({
    code: "runtime:tool-result",
    label: "工具结果",
    tools: [{ name: "read_tool_result", source: "Pydantic AI Harness" }],
  });
  assert.match(html, /runtime:tool-result/);
  assert.match(html, /read_tool_result/);
  assert.match(html, /Pydantic AI Harness/);
});

test("MCP 工具发现与 Resource 只在前端合并为一个展示分组", () => {
  const ui = loadSystemToolsUi();
  const merged = ui.mergeSystemToolDomains([
    { code: "runtime:planning", label: "任务规划", tools: [{ name: "write_plan" }] },
    { code: "runtime:mcp-tool-search", label: "MCP 工具发现", description: "搜索工具。", tools: [{ name: "search_tools" }] },
    { code: "runtime:mcp-resource", label: "MCP Resource", description: "读取资源。", tools: [{ name: "read_mcp_resource" }] },
  ]);
  assert.equal(Array.from(merged, (domain) => domain.code).join(","), "runtime:basic,runtime:mcp");
  assert.equal(Array.from(merged[1].tools, (tool) => tool.name).join(","), "search_tools,read_mcp_resource");
  assert.match(merged[1].description, /MCP 工具发现：搜索工具/);
  assert.match(merged[1].description, /MCP Resource：读取资源/);
});

test("运行时目录复用预制工具的卡片与详情，并保留加载与窄屏查看", () => {
  assert.match(source, /updateToolCatalogGroup/);
  assert.doesNotMatch(source, /data-retry-system-tools|refreshSystemToolsBtn/);
  assert.match(templateHtml, /id="builtinToolCatalog"/);
  assert.match(source, /window\.__BuiltinToolsUI\.renderToolCatalog/);
  assert.match(catalogCss, /tool-catalog-workspace[\s\S]*grid-template-columns: minmax\(230px, 290px\) minmax\(0, 1fr\)/);
  assert.match(catalogCss, /@media \(max-width: 800px\)/);
  assert.match(catalogCss, /tool-catalog-runtime-table \{ min-width: 560px; \}/);
});

test("运行时分组切换只读现有目录，不增加执行或配置接口", () => {
  assert.match(source, /apiFetch\("\/api\/v1\/system-tools"\)/);
  assert.doesNotMatch(source, /method:|POST|PATCH|DELETE/);
  assert.match(source, /updateToolCatalogGroup\(true, mergeSystemToolDomains\(domains\)\)/);
  assert.match(source, /renderSystemTools\(domains\)/);
});


test("运行时目录收拢为五组，所有工具及各自注入条件保留", () => {
  const ui = loadSystemToolsUi();
  const definitions = [
    ["runtime:environment", ["get_current_time"]],
    ["runtime:channel", ["channel_message"]],
    ["runtime:planning", ["write_plan"]],
    ["runtime:conversation-history", ["search_conversation_history", "read_conversation_event"]],
    ["runtime:skill", ["load_capability", "list_skill_resources", "execute_skill_script"]],
    ["runtime:file", ["read_file", "view_image", "export_file_to_download_center"]],
    ["runtime:tool-result", ["read_tool_result"]],
    ["runtime:mcp-tool-search", ["search_tools"]],
    ["runtime:mcp-resource", ["list_mcp_resources", "read_mcp_resource"]],
  ];
  const domains = definitions.map(([code, names]) => Object.freeze({
    code,
    label: code === "runtime:skill" ? "Skill" : code === "runtime:channel" ? "渠道消息" : code,
    tools: Object.freeze(names.map(name => Object.freeze({
      name, display_name: `名称 ${name}`, source: `来源 ${code}`,
      injection_condition: `条件 ${name}`, description: `说明 ${name}`,
    }))),
  }));
  Object.freeze(domains);
  const merged = Array.from(ui.mergeSystemToolDomains(domains));
  assert.deepEqual(merged.map(d => d.label), ["基础运行", "文件与结果", "Skill", "MCP", "渠道消息"]);
  assert.deepEqual(merged.map(d => d.code), ["runtime:basic", "runtime:file", "runtime:skill", "runtime:mcp", "runtime:channel"]);
  assert.deepEqual(merged.map(d => d.tools.length), [4, 4, 3, 3, 1]);
  assert.deepEqual(Array.from(merged[0].tools, t => t.name), ["get_current_time", "write_plan", "search_conversation_history", "read_conversation_event"]);
  assert.deepEqual(Array.from(merged[1].tools, t => t.name), ["read_file", "view_image", "export_file_to_download_center", "read_tool_result"]);
  const originalTools = domains.flatMap(d => d.tools);
  const displayedTools = merged.flatMap(d => Array.from(d.tools));
  assert.equal(new Set(displayedTools.map(t => t.name)).size, originalTools.length);
  assert.equal(displayedTools.length, originalTools.length);
  for (const tool of originalTools) assert.equal(displayedTools.find(t => t.name === tool.name), tool);
  const html = ui.systemToolDomain(merged[0]);
  for (const tool of originalTools.slice(0, 1).concat(domains[2].tools, domains[3].tools)) {
    assert.ok(html.includes(tool.source));
    assert.ok(html.includes(tool.injection_condition));
    assert.ok(html.includes(tool.description));
  }
});

test("运行时展示分组允许部分目录与未知领域，且不改写输入", () => {
  const ui = loadSystemToolsUi();
  assert.equal(ui.mergeSystemToolDomains(null).length, 0);
  assert.equal(ui.mergeSystemToolDomains([]).length, 0);
  const unknown = Object.freeze({ code: "runtime:future", label: "后续领域", tools: [{ name: "future_tool" }] });
  const history = Object.freeze({ code: "runtime:conversation-history", label: "会话历史", tools: Object.freeze([{ name: "read_conversation_event" }]) });
  const source = Object.freeze([unknown, history]);
  const merged = Array.from(ui.mergeSystemToolDomains(source));
  assert.deepEqual(merged.map(d => d.code), ["runtime:basic", "runtime:future"]);
  assert.equal(merged[1], unknown);
  assert.equal(merged[0].tools[0], history.tools[0]);
  assert.equal(history.code, "runtime:conversation-history");
  assert.equal(source.length, 2);
  assert.deepEqual(Array.from(ui.mergeSystemToolDomains([unknown])), [unknown]);
});
