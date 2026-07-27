import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(new URL("../../../frontend/console/api.js", import.meta.url), "utf8");

function loadApi(initial = {}, fetchImpl = async () => ({ status: 200 })) {
  const values = new Map(Object.entries(initial));
  const window = {
    __DEEP_AGENT_APP_ROOT__: "/",
    dispatchEvent() {},
  };
  const context = vm.createContext({
    AbortController,
    CustomEvent: class CustomEvent {},
    clearTimeout,
    DOMException,
    fetch: fetchImpl,
    location: { origin: "http://localhost:8000", protocol: "http:" },
    localStorage: {
      getItem(key) { return values.get(key) ?? null; },
      removeItem(key) { values.delete(key); },
      setItem(key, value) { values.set(key, value); },
    },
    setTimeout,
    window,
  });
  vm.runInContext(source, context);
  return { api: window.DeepAgentConsoleAPI, values };
}

test("请求上下文只保存 app 并清理旧格式缓存", () => {
  const { api, values } = loadApi();
  assert.deepEqual(
    JSON.parse(JSON.stringify(api.saveContext({
      app: " mdxath003 ",
      space: "space-a",
      language: "zh-cn",
      user: "other-user",
      token: "secret",
      cookie: "secret",
    }))),
    { app: "mdxath003" },
  );
  assert.deepEqual(JSON.parse(values.get(api.CONTEXT_KEY)), { app: "mdxath003" });
});

test("读取旧上下文时只保留合法 App", () => {
  const { api, values } = loadApi({
    da_console_request_context_v1: JSON.stringify({
      app: "mdxath003",
      space: "other",
      language: "en",
    }),
  });

  assert.deepEqual(JSON.parse(JSON.stringify(api.loadContext())), { app: "mdxath003" });
  assert.deepEqual(JSON.parse(values.get(api.CONTEXT_KEY)), { app: "mdxath003" });
});

test("App 推导 Space 并拒绝非法编码", () => {
  const { api } = loadApi();

  assert.equal(api.deriveSpaceFromApp(" mdxath003 "), "mdxath");
  assert.throws(
    () => api.saveContext({ app: "mdxath" }),
    /Space 编码和三位数字/,
  );
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
  assert.deepEqual(
    JSON.parse(JSON.stringify(headers)),
    { "Content-Type": "application/json", language: "zh-cn" },
  );
});

test("请求固定使用中文且重置会清除 App", () => {
  const { api, values } = loadApi({
    da_console_request_context_v1: JSON.stringify({ app: " ", space: "s1" }),
  });
  assert.deepEqual(JSON.parse(JSON.stringify(api.buildHeaders())), { language: "zh-cn" });
  api.clearContext();
  assert.equal(values.has(api.CONTEXT_KEY), false);
});

test("后端地址固定为当前同源并清理旧缓存", () => {
  const { api, values } = loadApi({
    da_console_base_url_v1: "http://192.0.2.1:8000",
  });
  assert.equal(api.defaultApiBaseUrl(), "http://localhost:8000");
  assert.equal(values.has("da_console_base_url_v1"), false);
});

test("身份上下文请求超时后返回可识别错误", async () => {
  const pendingFetch = (_url, options) => new Promise((_resolve, reject) => {
    options.signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
  });
  const { api } = loadApi({}, pendingFetch);
  await assert.rejects(
    api.requestContext({ timeoutMs: 5 }),
    (error) => error.code === "request_timeout" && error.message.includes("连接后端超时"),
  );
});
