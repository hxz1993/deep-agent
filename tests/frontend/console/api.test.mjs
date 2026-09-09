import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(new URL("../../../console/api.js", import.meta.url), "utf8");

function loadApi(
  initial = {},
  fetchImpl = async () => ({ status: 200 }),
  pageUrl = "http://localhost:8000/console/",
) {
  const values = new Map(Object.entries(initial));
  const parsedUrl = new URL(pageUrl);
  const window = {
    dispatchEvent() {},
  };
  const context = vm.createContext({
    AbortController,
    CustomEvent: class CustomEvent {},
    clearTimeout,
    document: { baseURI: pageUrl },
    DOMException,
    fetch: fetchImpl,
    location: {
      href: pageUrl,
      origin: parsedUrl.origin,
      pathname: parsedUrl.pathname,
      protocol: parsedUrl.protocol,
    },
    localStorage: {
      getItem(key) { return values.get(key) ?? null; },
      removeItem(key) { values.delete(key); },
      setItem(key, value) { values.set(key, value); },
    },
    setTimeout,
    URL,
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
    {
      "Content-Type": "application/json",
      "X-Deep-Agent-Language": "zh-cn",
    },
  );
});

test("请求固定使用中文且重置会清除 App", () => {
  const { api, values } = loadApi({
    da_console_request_context_v1: JSON.stringify({ app: " ", space: "s1" }),
  });
  assert.deepEqual(
    JSON.parse(JSON.stringify(api.buildHeaders())),
    { "X-Deep-Agent-Language": "zh-cn" },
  );
  api.clearContext();
  assert.equal(values.has(api.CONTEXT_KEY), false);
});

test("调试 App 使用网关安全 Header 而不是平台保留 app Header", () => {
  const { api } = loadApi({
    da_console_request_context_v1: JSON.stringify({ app: "iirjgi005" }),
  });

  assert.deepEqual(
    JSON.parse(JSON.stringify(api.buildHeaders())),
    {
      "X-Deep-Agent-App": "iirjgi005",
      "X-Deep-Agent-Language": "zh-cn",
    },
  );
});

test("本地控制台根据部署位置解析服务根地址并清理旧缓存", () => {
  const { api, values } = loadApi({
    da_console_base_url_v1: "http://192.0.2.1:8000",
  });
  assert.equal(api.defaultApiBaseUrl(), "http://localhost:8000");
  assert.equal(api.serviceUrl("/api/v1/agents"), "http://localhost:8000/api/v1/agents");
  assert.equal(values.has("da_console_base_url_v1"), false);
});

test("多级代理部署动态保留完整服务前缀", () => {
  const { api } = loadApi(
    {},
    undefined,
    "https://test.deepfos.com/deepfos-server/deep-agent-server1-0/console/",
  );

  assert.equal(
    api.defaultApiBaseUrl(),
    "https://test.deepfos.com/deepfos-server/deep-agent-server1-0",
  );
  assert.equal(
    api.serviceUrl("/api/v1/mcp"),
    "https://test.deepfos.com/deepfos-server/deep-agent-server1-0/api/v1/mcp",
  );
  assert.equal(
    api.consoleUrl("?agent_id=agent-1#/workspace"),
    "https://test.deepfos.com/deepfos-server/deep-agent-server1-0/console/?agent_id=agent-1#/workspace",
  );
});

test("上下文请求与业务请求使用同一个动态服务根地址", async () => {
  const requested = [];
  const fetchImpl = async (url) => {
    requested.push(String(url));
    return {
      ok: true,
      status: 200,
      text: async () => JSON.stringify({
        authenticated: true,
        user: "user-1",
        app: "iirjgi005",
        space: "iirjgi",
        language: "zh-cn",
      }),
    };
  };
  const { api } = loadApi(
    {},
    fetchImpl,
    "https://test.deepfos.com/deepfos-server/deep-agent-server1-0/console/",
  );

  await api.requestContext();
  assert.deepEqual(requested, [
    "https://test.deepfos.com/deepfos-server/deep-agent-server1-0/api/v1/request-context",
  ]);
});

test("当前用户资料通过同源适配接口读取用户名", async () => {
  const requested = [];
  const fetchImpl = async (url, options) => {
    requested.push({ url: String(url), headers: options.headers });
    return {
      ok: true,
      status: 200,
      text: async () => JSON.stringify({ user_id: "user-1", username: "周文俊" }),
    };
  };
  const { api } = loadApi(
    { da_console_request_context_v1: JSON.stringify({ app: "iirjgi005" }) },
    fetchImpl,
    "https://test.deepfos.com/deepfos-server/deep-agent-server1-0/console/",
  );

  const profile = await api.currentUser();

  assert.deepEqual(JSON.parse(JSON.stringify(profile)), {
    user_id: "user-1",
    username: "周文俊",
  });
  assert.equal(
    requested[0].url,
    "https://test.deepfos.com/deepfos-server/deep-agent-server1-0/api/v1/current-user",
  );
  assert.equal(requested[0].headers["X-Deep-Agent-App"], undefined);
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

test("控制台权限检查不携带调试 App 上下文并返回企业管理员角色", async () => {
  const requests = [];
  const fetchImpl = async (url, options) => {
    requests.push({ url: String(url), headers: options.headers });
    return {
      ok: true,
      status: 200,
      text: async () => JSON.stringify({
        allowed: true,
        roles: ["enterprise_admin"],
      }),
    };
  };
  const { api } = loadApi({
    da_console_request_context_v1: JSON.stringify({ app: "iirjgi005" }),
  }, fetchImpl);

  const result = await api.adminAccess();

  assert.deepEqual(JSON.parse(JSON.stringify(result)), {
    allowed: true,
    roles: ["enterprise_admin"],
  });
  assert.equal(requests[0].url, "http://localhost:8000/api/v1/admin/access");
  assert.equal(requests[0].headers["X-Deep-Agent-App"], undefined);
});

test("控制台权限检查超时统一映射为503", async () => {
  const pendingFetch = (_url, options) => new Promise((_resolve, reject) => {
    options.signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
  });
  const { api } = loadApi({}, pendingFetch);

  await assert.rejects(
    api.adminAccess({ timeoutMs: 5 }),
    (error) => error.code === "admin_access_timeout" && error.status === 503,
  );
});
