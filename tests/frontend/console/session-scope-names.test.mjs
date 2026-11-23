import assert from "node:assert/strict";
import test from "node:test";
import { renderSessionScope, renderSessionInspector } from "../../../console/session-inspector.js";

test("范围将名称与原编码分两行，编码复用既有复制动作", () => {
  const html = renderSessionScope({
    appId: "budget001", appName: "年度预算", spaceId: "budget", spaceName: "预算管理空间",
  });
  assert.match(html, /inspector-scope-names[^]*?年度预算[^]*?预算管理空间[^]*?inspector-scope-codes[^]*?budget001[^]*?budget/);
  assert.match(html, /data-inspector-copy="budget001"/);
  assert.match(html, /data-inspector-copy="budget"/);
  assert.match(html, /title="会话创建时记录的应用与空间"/);
  assert.match(html, /title="年度预算"/);
});

test("无编码与名称查询失败明确区分，不能用当前调试范围补历史", () => {
  const empty = renderSessionScope({});
  assert.match(empty, /未关联应用[^]*?未关联空间/);
  assert.equal((empty.match(/<code>—<\/code>/g) || []).length, 2);
  assert.doesNotMatch(empty, /data-inspector-copy/);
  const unavailable = renderSessionScope({ appId: "old001", spaceId: "old" });
  assert.equal((unavailable.match(/>名称未获取</g) || []).length, 2);
  const historical = renderSessionInspector({
    state: { sessionId: "history" }, activeSession: { appId: "old001" },
    context: { app: "current001", app_name: "当前应用" },
  });
  assert.match(historical, /old001/);
  assert.doesNotMatch(historical, /current001|当前应用/);
});

test("新会话提示有效调试范围，长名称及编码按文本转义", () => {
  const html = renderSessionScope({
    appId: '<app&"001>', appName: '<应用&">', spaceId: "space", spaceName: "空间".repeat(30),
  }, { saved: false });
  assert.match(html, /title="新会话将使用当前有效调试范围"/);
  assert.match(html, /data-inspector-copy="&lt;app&amp;&quot;001&gt;"/);
  assert.match(html, /title="&lt;应用&amp;&quot;&gt;"/);
  assert.doesNotMatch(html, /<应用|<app&/);
});
