import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(
  new URL("../../../console/configuration/agents.js", import.meta.url),
  "utf8",
);

function loadAgentUi() {
  const window = {};
  const document = { addEventListener() {} };
  vm.runInContext(
    source,
    vm.createContext({
      document,
      window,
      setTimeout,
      clearTimeout,
    }),
  );
  return window.__AgentAdminUI;
}

test("Agent 副本名称按现有唯一约束稳定递增", () => {
  const ui = loadAgentUi();
  assert.equal(ui.nextAgentCopyName("预算助手", new Set()), "预算助手 副本");
  assert.equal(
    ui.nextAgentCopyName("预算助手", new Set(["预算助手 副本", "预算助手 副本 2"])),
    "预算助手 副本 3",
  );
});

test("Agent 复制完整保留模型、提示词、温度、能力和 Skill", () => {
  assert.match(source, /title: "复制 Agent"/);
  assert.match(source, /label: "Agent 名称"/);
  assert.match(source, /value: nextAgentCopyName\(agent\.name, existingNames\)/);
  assert.match(source, /model_id: agent\.model_id/);
  assert.match(source, /prompt: agent\.prompt/);
  assert.match(source, /temperature: agent\.temperature/);
  assert.match(source, /capabilities: \[\.\.\.\(agent\.capabilities \|\| \[\]\)\]/);
  assert.match(source, /skills: \[\.\.\.\(agent\.skills \|\| \[\]\)\]/);
  assert.match(source, /data-agent-chat[\s\S]*data-copy-agent[\s\S]*data-delete/);
  assert.doesNotMatch(source, /data-edit=/);
  assert.doesNotMatch(source, /await this\.openEdit\(copied\.id\)/);
});

test("Agent 行操作不冒泡触发详情页", () => {
  assert.match(source, /\.agent-row-actions[\s\S]*event\.stopPropagation\(\)/);
  assert.match(source, /if \(event\.target\.closest\("a, button"\)\) return;[\s\S]*event\.key === "Enter"/);
});
