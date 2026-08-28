import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const mainSource = await readFile(
  new URL("../../../frontend/console/main.js", import.meta.url),
  "utf8",
);
const indexHtml = await readFile(
  new URL("../../../frontend/console/index.html", import.meta.url),
  "utf8",
);

test("Console先验证平台身份和企业管理员权限再加载管理页面", () => {
  const identityIndex = mainSource.indexOf("await refreshIdentity()");
  const accessIndex = mainSource.indexOf("await window.DeepAgentConsoleAPI.adminAccess()");
  const diagnosticsIndex = mainSource.indexOf("initDiagnostics();");
  const workspaceIndex = mainSource.indexOf("await initWorkspace()");

  assert.ok(identityIndex >= 0);
  assert.ok(accessIndex > identityIndex);
  assert.ok(diagnosticsIndex > accessIndex);
  assert.ok(workspaceIndex > accessIndex);
  assert.match(mainSource, /!access\?\.allowed \|\| !access\?\.roles\?\.includes\("enterprise_admin"\)/);
});

test("无权限、401和503都使用整页门禁且提前结束初始化", () => {
  assert.match(mainSource, /title: "无控制台管理权限"[\s\S]*?return;/);
  assert.match(mainSource, /error\.status === 401[\s\S]*?平台登录状态已失效/);
  assert.match(mainSource, /error\.status === 503[\s\S]*?暂时无法验证管理权限/);
  assert.match(indexHtml, /id="authLock"[\s\S]*id="authLockTitle"[\s\S]*id="authLockMessage"/);
});

test("直接Hash访问配置页时未授权不会加载配置数据", () => {
  assert.match(mainSource, /if \(consoleAllowed && next === "configuration"\)/);
  assert.match(mainSource, /const page = location\.hash/);
  assert.match(mainSource, /await showPage\(pageFromLocation\(\), \{ updateHash: true \}\);\s*return;/);
});
