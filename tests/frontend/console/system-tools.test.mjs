import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(
  new URL("../../../frontend/console/configuration/system-tools.js", import.meta.url),
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

test("系统工具菜单位于内置工具之后和 Skill 之前", () => {
  const builtinPosition = indexHtml.indexOf('data-tab="builtin-tools"');
  const systemPosition = indexHtml.indexOf('data-tab="system-tools"');
  const skillPosition = indexHtml.indexOf('data-tab="skills"');

  assert.ok(builtinPosition < systemPosition);
  assert.ok(systemPosition < skillPosition);
  assert.match(templateHtml, /id="panel-system-tools"/);
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
});

test("任务规划三个官方工具属于独立只读领域", () => {
  const ui = loadSystemToolsUi();
  const html = ui.systemToolDomain({
    code: "runtime:planning",
    label: "任务规划",
    tools: [
      "write_plan",
      "read_plan",
      "update_task_statuses",
    ].map((name) => ({ name, source: "Pydantic AI Harness" })),
  });
  assert.match(html, /runtime:planning/);
  assert.match(html, /write_plan/);
  assert.match(html, /update_task_statuses/);
  assert.doesNotMatch(html, /add_task|update_task_status<|remove_task/);
  assert.doesNotMatch(html, /checkbox|switch/);
});

test("Skill 三个工具归 Skill，read_file 独立归文件领域", () => {
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
    tools: [{ name: "read_file" }],
  });
  assert.match(skillHtml, /load_capability/);
  assert.match(skillHtml, /list_skill_resources/);
  assert.match(skillHtml, /execute_skill_script/);
  assert.doesNotMatch(skillHtml, /read_file/);
  assert.match(fileHtml, /read_file/);
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

test("系统工具页面具备加载、失败重试和窄屏可横向查看的结构", () => {
  assert.match(source, /正在加载系统工具/);
  assert.match(source, /data-retry-system-tools/);
  assert.match(templateHtml, /class="system-tool-domains"/);
  assert.match(source, /class="table-wrap"/);
});
