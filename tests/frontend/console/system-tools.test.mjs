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

test("系统工具页面具备加载、失败重试和窄屏可横向查看的结构", () => {
  assert.match(source, /正在加载系统工具/);
  assert.match(source, /data-retry-system-tools/);
  assert.match(templateHtml, /class="system-tool-domains"/);
  assert.match(source, /class="table-wrap"/);
});
