import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import test from "node:test";

const consoleRoot = new URL("../../../console/", import.meta.url);
const configurationCore = await readFile(new URL("configuration/core.js", consoleRoot), "utf8");
const configurationStyles = await readFile(new URL("configuration/configuration.css", consoleRoot), "utf8");
const skills = await readFile(new URL("configuration/skills.js", consoleRoot), "utf8");
const agents = await readFile(new URL("configuration/agents.js", consoleRoot), "utf8");

async function readConsoleScripts() {
  const names = await readdir(consoleRoot, { recursive: true });
  const scripts = await Promise.all(
    names
      .filter((name) => name.endsWith(".js"))
      .map(async (name) => `${name}\n${await readFile(new URL(name, consoleRoot), "utf8")}`),
  );
  return scripts.join("\n");
}

test("Console 不再调用浏览器原生 alert confirm 或 prompt", async () => {
  const source = await readConsoleScripts();
  assert.doesNotMatch(source, /\b(?:window\.)?alert\s*\(/);
  assert.doesNotMatch(source, /\b(?:window\.)?confirm\s*\(/);
  assert.doesNotMatch(source, /\b(?:window\.)?prompt\s*\(/);
});

test("删除和未保存提醒复用轻量语义确认弹层", () => {
  assert.match(configurationCore, /className = "console-action-dialog"/);
  assert.match(configurationCore, /dialog\.showModal\(\)/);
  assert.match(configurationCore, /async function confirmAction/);
  assert.match(agents, /await confirmAction\(\{[\s\S]*放弃未保存的修改/);
  assert.match(agents, /tone: "danger"[\s\S]*confirmLabel: "删除"/);
  assert.match(configurationStyles, /dialog\.console-action-dialog[\s\S]*width: min\(430px/);
  assert.match(configurationStyles, /console-action-dialog\[data-tone="danger"\]/);
});

test("Skill 编辑使用一次轻量输入弹层而不是连续输入框", () => {
  assert.match(configurationCore, /async function requestActionFields/);
  assert.match(skills, /requestActionFields\(\{[\s\S]*name: "label"[\s\S]*name: "description"/);
  assert.match(configurationCore, /new FormData\(form\)/);
});
