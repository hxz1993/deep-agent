import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(
  new URL("../../../console/configuration/mcp.js", import.meta.url),
  "utf8",
);
const template = await readFile(
  new URL("../../../console/configuration-template.html", import.meta.url),
  "utf8",
);

function loadMcpUi() {
  const window = {};
  const document = { addEventListener() {} };
  vm.runInContext(
    source,
    vm.createContext({
      document,
      escapeHtml: (value) => String(value),
      formatDate: (value) => String(value),
      window,
    }),
  );
  return window.__McpInspectionUI;
}

test("只有 URL MCP 提供独立重命名操作", () => {
  const ui = loadMcpUi();
  assert.equal(ui.canRenameMcp("url"), true);
  assert.equal(ui.canRenameMcp("builtin"), false);
  assert.equal(ui.canRenameMcp("stdio"), false);
  assert.match(source, /data-rename-mcp/);
  assert.match(source, /data-mcp-more-trigger[\s\S]*ellipsis-vertical/);
  assert.match(source, /data-inspect-mcp=[\s\S]*data-lucide="refresh-cw"[\s\S]*<span>检测<\/span>/);
  assert.doesNotMatch(source, /data-edit=[\s\S]*data-lucide="pencil-line"[\s\S]*<span>编辑<\/span>/);
  assert.match(source, /data-open-mcp/);
  assert.match(source, /data-mcp-more-menu[\s\S]*重命名[\s\S]*删除/);
});

test("普通编辑名称只读且重命名使用独立弹窗", () => {
  assert.match(template, /id="mcpNameReadOnlyHint"/);
  assert.match(template, /id="mcpRenameModal"/);
  assert.match(template, /id="mcpRenameCurrentName"[^>]*readonly/);
  assert.match(template, /历史工具调用不会被修改/);
  assert.match(source, /nameInput\.readOnly = true/);
  assert.match(source, /\/api\/v1\/mcp\/\$\{encodeURIComponent\(oldName\)\}\/rename/);
});

test("重命名成功提示包含同步更新的 Agent 数量", () => {
  const ui = loadMcpUi();
  assert.equal(
    ui.renameSuccessMessage(["Agent A", "Agent B"]),
    "MCP 已重命名，已同步更新 2 个 Agent",
  );
});
