import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";
import { renderUserIdentity } from "../../../console/user-identity.js";

const source = (await readFile(new URL("../../../console/context.js", import.meta.url), "utf8"))
  .replace(/^import .*;\n/gm, "").replace(/export /g, "");

function harness({ inherited, resolved, override = {}, resolver, profile = async () => null } = {}) {
  const element = () => {
    const classes = new Set();
    return { textContent: "", title: "", innerHTML: "", classes,
      classList: { toggle(name, value) { if (value) classes.add(name); else classes.delete(name); } },
      setAttribute() {}, removeAttribute() {}, addEventListener() {} };
  };
  const label = element();
  const trigger = { ...element(), querySelector: () => label };
  const fields = new Map();
  const calls = [];
  const welcome = element();
  let inspectorUpdates = 0;
  const listeners = new Map();
  const model = { inherited, resolved, override };
  const context = vm.createContext({ renderUserIdentity,
    window: { DeepAgentConsoleAPI: {
      CONTEXT_KEY: "context-key",
      loadContext: () => model.override,
      deriveSpaceFromApp: app => app.slice(0, -3), DEFAULT_LANGUAGE: "zh_CN",
      requestContext: async (options = {}) => {
        calls.push(options);
        if (resolver) return resolver(options, model);
        return options.inherited ? model.inherited : model.resolved;
      },
      currentUser: profile,
    }, DeepAgentConsoleWorkspace: { renderInspector() { inspectorUpdates++; } },
    addEventListener(name, handler) { listeners.set(name, handler); } },
    document: {
      body: element(),
      addEventListener() {},
      getElementById(id) { if (!fields.has(id)) fields.set(id, element()); return fields.get(id); },
      querySelectorAll: selector => selector === "[data-open-context]" ? [trigger]
        : selector === "[data-empty-context-label]" ? [welcome] : [],
    },
  });
  vm.runInContext(source, context);
  return { model, calls, label, trigger, welcome, listeners, bind: () => context.bindContextControls(),
    get inspectorUpdates() { return inspectorUpdates; }, controller: context.window.DeepAgentConsoleContext };
}

test("已配置调试应用用中文名称回显，悬停保留编码", async () => {
  const h = harness({ inherited: {
    user: "u1", app: "budget001", space: "budget", app_name: "年度预算", space_name: "预算空间",
  } });
  await h.controller.refreshIdentity();
  assert.equal(h.label.textContent, "年度预算");
  assert.match(h.trigger.title, /App：budget001/);
  assert.equal(h.controller.effectiveContext.space_name, "预算空间");
  assert.equal(h.label.classes.has("is-unassigned"), false);
  assert.equal(h.calls.length, 1);
  assert.equal(h.welcome.textContent, "年度预算 · budget001");
  assert.ok(h.inspectorUpdates > 0);
});

test("首次加载范围不等待可选的用户资料，资料迟到仍能更新且不改变范围", async () => {
  let finishProfile;
  const h = harness({ inherited: { user: "u1", app: "budget001", space: "budget", app_name: "年度预算" },
    profile: () => new Promise(resolve => { finishProfile = resolve; }) });
  let ready = false;
  const pending = h.controller.refreshIdentity().then(() => { ready = true; });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(ready, true);
  assert.equal(h.label.textContent, "年度预算");
  finishProfile({ user_id: "u1", username: "测试用户" });
  await pending;
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.controller.identity.username, "测试用户");
  assert.equal(h.controller.effectiveContext.app, "budget001");
});

test("调试覆盖读取有效上下文名称，修改覆盖后不复用原名称", async () => {
  const h = harness({
    inherited: { user: "u1", app: "platform001", space: "platform", app_name: "平台应用" },
    resolved: { user: "u1", app: "custom001", space: "custom", app_name: "客户应用", space_name: "客户空间" },
    override: { app: "custom001" },
  });
  await h.controller.refreshIdentity();
  assert.equal(h.label.textContent, "客户应用");
  assert.equal(h.calls.length, 2);
  h.model.override = { app: "other001" };
  h.controller.render();
  assert.equal(h.label.textContent, "other001");
  assert.equal(h.controller.effectiveContext.app_name, null);
  h.model.override = {};
  h.controller.render();
  assert.equal(h.label.textContent, "平台应用");
});

test("无应用使用灰色状态；名称查询失败或身份不匹配回退编码", async () => {
  const h = harness({ inherited: { user: "u1" } });
  await h.controller.refreshIdentity();
  assert.equal(h.label.textContent, "不分应用");
  assert.equal(h.label.classes.has("is-unassigned"), true);
  h.model.override = { app: "custom001" };
  h.model.resolved = { user: "other", app: "custom001", app_name: "其他人的名称" };
  await h.controller.refreshIdentity();
  assert.equal(h.label.textContent, "custom001");
  assert.equal(h.label.classes.has("is-unassigned"), false);
  h.model.resolved = null;
  await h.controller.refreshIdentity();
  assert.equal(h.label.textContent, "custom001");
});

test("迟到的身份与名称查询不能覆盖新一代结果", async () => {
  let complete;
  let count = 0;
  const h = harness({ resolver: async () => {
    if (++count === 1) return new Promise(resolve => { complete = resolve; });
    return { user: "new", app: "new001", space: "new", app_name: "新应用" };
  } });
  const old = h.controller.refreshIdentity();
  await h.controller.refreshIdentity();
  complete({ user: "old", app: "old001", space: "old", app_name: "旧应用" });
  await old;
  assert.equal(h.label.textContent, "新应用");
  assert.equal(h.controller.identity.user, "new");
});

test("其他标签页保存调试应用后，立即回显编码并异步补齐名称", async () => {
  const h = harness({ inherited: { user: "u1" },
    resolved: { user: "u1", app: "custom001", space: "custom", app_name: "客户应用" } });
  h.bind();
  await h.controller.refreshIdentity();
  const requests = h.calls.length;
  h.listeners.get("storage")({ key: "unrelated" });
  assert.equal(h.calls.length, requests);
  h.model.override = { app: "custom001" };
  h.listeners.get("storage")({ key: "context-key" });
  assert.equal(h.label.textContent, "custom001");
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.label.textContent, "客户应用");
  assert.equal(h.welcome.textContent, "客户应用 · custom001");
  h.model.override = {};
  h.listeners.get("storage")({ key: null });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.label.textContent, "不分应用");
});

test("旧用户迟到的资料不能覆盖新用户身份", async () => {
  let finishOld;
  let calls = 0;
  const h = harness({ inherited: { user: "old" }, profile: () => ++calls === 1
    ? new Promise(resolve => { finishOld = resolve; })
    : Promise.resolve({ user_id: "new", username: "新用户" }) });
  await h.controller.refreshIdentity();
  h.model.inherited = { user: "new" };
  await h.controller.refreshIdentity();
  finishOld({ user_id: "old", username: "旧用户" });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.controller.identity.user, "new");
  assert.equal(h.controller.identity.username, "新用户");
});
