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

test("null 能力配置选择系统默认能力", () => {
  const ui = loadAgentConfigurationUi();
  const selected = ui.buildCapabilitySelectionSet(null, [
    { name: "native", default_enabled: true },
    { name: "skill:optional", default_enabled: false },
    { name: "ui-builtin:confirm_action", default_enabled: true, locked: true },
  ]);

  assert.deepEqual([...selected], ["native"]);
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
