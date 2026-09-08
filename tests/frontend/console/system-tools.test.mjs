import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(
  new URL("../../../console/configuration/system-tools.js", import.meta.url),
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
    source,
    vm.createContext({ apiFetch: async () => [], document, escapeHtml, window }),
  );
  return window.__SystemToolsUI;
}

test("系统工具合并进内置工具目录", () => {
  const builtinPosition = indexHtml.indexOf('data-tab="builtin-tools"');
  const systemPosition = indexHtml.indexOf('data-tab="system-tools"');
  const mcpPosition = indexHtml.indexOf('data-tab="mcp"');
  const uiPosition = indexHtml.indexOf('data-tab="ui-tools"');

  assert.ok(mcpPosition >= 0 && mcpPosition < builtinPosition);
  assert.ok(builtinPosition < uiPosition);
  assert.equal(systemPosition, -1);
  assert.doesNotMatch(templateHtml, /id="panel-system-tools"/);
  assert.match(templateHtml, /id="panel-builtin-tools"[\s\S]*运行时系统工具[\s\S]*id="systemToolDomains"/);
  assert.match(templateHtml, /不参与 Agent 能力配置/);
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
  assert.match(html, /system-tool-group-row/);
  assert.match(html, /system-tool-group-help/);
  assert.match(html, /system-tool-group-code">runtime:skill/);
  assert.match(html, /data-tooltip="按需加载 Skill。"/);
  assert.match(source, /className = "system-tool-tooltip hidden"/);
  assert.match(configurationCss, /\.system-tool-tooltip\s*\{[^}]*position: fixed;[^}]*z-index: 1000;/);
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

test("大工具结果读取作为独立系统工具只读展示", () => {
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
  assert.equal(Array.from(merged, (domain) => domain.code).join(","), "runtime:planning,runtime:mcp");
  assert.equal(Array.from(merged[1].tools, (tool) => tool.name).join(","), "search_tools,read_mcp_resource");
  assert.match(merged[1].description, /MCP 工具发现：搜索工具/);
  assert.match(merged[1].description, /MCP Resource：读取资源/);
});

test("系统工具页面保留加载与窄屏横向查看，但不提供单独刷新入口", () => {
  assert.match(source, /正在加载系统工具/);
  assert.doesNotMatch(source, /data-retry-system-tools|refreshSystemToolsBtn/);
  assert.match(templateHtml, /class="system-tool-domains"/);
  assert.match(source, /class="table-wrap console-list-a"/);
  assert.equal((source.match(/<thead>/g) || []).length, 1);
  assert.match(configurationCss, /\.system-tool-group-row th/);
});

test("所有系统工具分组复用同一组固定列宽", () => {
  assert.match(source, /<colgroup>[\s\S]*system-tool-col-name[\s\S]*system-tool-col-source[\s\S]*system-tool-col-condition[\s\S]*system-tool-col-description/);
  assert.match(configurationCss, /\.system-tools-table[\s\S]*table-layout: fixed/);
  assert.match(configurationCss, /system-tool-col-name \{ width: 18%; \}/);
  assert.match(configurationCss, /system-tool-col-source \{ width: 16%; \}/);
  assert.match(configurationCss, /system-tool-col-condition \{ width: 34%; \}/);
  assert.match(configurationCss, /system-tool-col-description \{ width: 32%; \}/);
});
