import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const templatePath = path.resolve(__dirname, "../../../frontend/console/configuration-template.html");
const templateHtml = fs.readFileSync(templatePath, "utf8");
const retiredNames = [
  ["text", "tools"].join("_"),
  ["doc", "search"].join("_"),
];

test("Console不再保留已下线预制MCP的名称、能力编码或旧脚本示例", () => {
  for (const retiredName of retiredNames) {
    assert.equal(templateHtml.toLowerCase().includes(retiredName), false);
    assert.equal(templateHtml.includes(`mcp:${retiredName}`), false);
    assert.equal(templateHtml.includes(`mcps/${retiredName}`), false);
  }
  assert.match(templateHtml, /path\/to\/server\.py/);
});
