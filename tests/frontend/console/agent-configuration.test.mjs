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

test("编辑 Agent 时在弹窗标题栏显示完整 Agent ID", () => {
  assert.match(template, /id="agentModalId"/);
  assert.match(
    source,
    /agentModalId"\)\.textContent = `Agent ID：\$\{agent\.id \|\| agentId\}`/,
  );
  assert.doesNotMatch(template, /<th>Agent ID<\/th>/);
});
