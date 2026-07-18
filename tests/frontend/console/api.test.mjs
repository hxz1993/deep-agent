import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(new URL("../../../frontend/console/api.js", import.meta.url), "utf8");

function loadApi(initial = {}) {
  const values = new Map(Object.entries(initial));
  const window = {
    __DEEP_AGENT_APP_ROOT__: "/",
    dispatchEvent() {},
  };
  const context = vm.createContext({
    CustomEvent: class CustomEvent {},
    fetch: async () => ({ status: 200 }),
    location: { origin: "http://localhost:8000", protocol: "http:" },
    localStorage: {
      getItem(key) { return values.get(key) ?? null; },
      removeItem(key) { values.delete(key); },
      setItem(key, value) { values.set(key, value); },
    },
    window,
  });
  vm.runInContext(source, context);
  return { api: window.DeepAgentConsoleAPI, values };
}

test("请求上下文只保存 app、space、language", () => {
  const { api, values } = loadApi();
  assert.deepEqual(
    JSON.parse(JSON.stringify(api.saveContext({
      app: " app-a ",
      space: "space-a",
      language: "zh-cn",
      user: "other-user",
      token: "secret",
      cookie: "secret",
    }))),
    { app: "app-a", space: "space-a", language: "zh-cn" },
  );
  assert.deepEqual(JSON.parse(values.get(api.CONTEXT_KEY)), {
    app: "app-a",
    space: "space-a",
    language: "zh-cn",
  });
});

test("敏感 Header 无法通过大小写变体绕过", () => {
  const { api } = loadApi();
  const headers = api.buildHeaders({
    Authorization: "Bearer secret",
    COOKIE: "session=secret",
    User: "other-user",
    "X-User-Token": "secret",
    "X-Debug-Header": "not-allowed",
    "Content-Type": "application/json",
  });
  assert.deepEqual(JSON.parse(JSON.stringify(headers)), { "Content-Type": "application/json" });
});

test("空白上下文不发送且重置会清除存储", () => {
  const { api, values } = loadApi({
    da_console_request_context_v1: JSON.stringify({ app: " ", space: "s1" }),
  });
  assert.deepEqual(JSON.parse(JSON.stringify(api.buildHeaders())), { space: "s1" });
  api.clearContext();
  assert.equal(values.has(api.CONTEXT_KEY), false);
});
