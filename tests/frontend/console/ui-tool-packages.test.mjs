import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = await readFile(
  new URL("../../../frontend/console/configuration/ui-tools.js", import.meta.url),
  "utf8",
);
const template = await readFile(
  new URL("../../../frontend/console/configuration-template.html", import.meta.url),
  "utf8",
);

test("UI 工具页提供工具包与工具两级目录", () => {
  assert.match(template, /id="uiToolPackagesTableBody"/);
  assert.match(template, /id="uiToolsTableBody"/);
  assert.match(template, /Agent 按工具包整体启用/);
  assert.match(source, /api\/v1\/ui-tool-packages/);
  assert.match(source, /item\.package_code === this\.selectedPackageCode/);
});

test("手工 UI 工具保存时必须提交所属工具包", () => {
  assert.match(template, /id="uiToolPackageCode" required/);
  assert.match(source, /const package_code = document\.getElementById\("uiToolPackageCode"\)\.value/);
  assert.match(source, /JSON\.stringify\(\{[\s\S]*?package_code/);
});

test("Agent 能力页只渲染工具包级开关", async () => {
  const agents = await readFile(
    new URL("../../../frontend/console/configuration/agents.js", import.meta.url),
    "utf8",
  );
  assert.match(agents, /value="\$\{escapeHtml\(cap\.code\)\}"/);
  assert.doesNotMatch(agents, /name="tool"|data-tool-checkbox/);
});
