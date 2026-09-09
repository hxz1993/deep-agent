import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(new URL("../../../console/configuration/llm-providers.js", import.meta.url), "utf8");
function harness(request = async () => ({ ok: true, elapsed_ms: 12, message: "连接成功" })) {
  const calls = [], views = [];
  const context = vm.createContext({
    window: {}, document: { addEventListener() {}, getElementById() { return null; } },
    state: { llmProviders: [{ id: "p" }], llmModels: [
      { id: "a", provider_id: "p", enabled: true },
      { id: "disabled", provider_id: "p", enabled: false },
      { id: "b", provider_id: "p", enabled: true },
      { id: "other", provider_id: "q", enabled: true },
    ] },
    AbortController, setTimeout, clearTimeout, encodeURIComponent,
    apiFetch: async (...args) => { calls.push(args); return request(...args); },
  });
  vm.runInContext(source, context);
  const admin = context.window.llmProvidersAdmin;
  const render = admin.updateModelTests.bind(admin);
  const results = () => vm.runInContext("Array.from(providerAdminState.modelTests)", context);
  admin.updateModelTests = () => views.push(results().map(([id, result]) => [id, result.status]));
  return { admin, context, calls, views, results, render };
}

test("切走页面时立即恢复按钮和行内状态，不依赖重新加载详情", async () => {
  let release;
  const h = harness(() => new Promise((resolve) => { release = resolve; }));
  const label = { textContent: "" };
  const button = { disabled: false, querySelector: () => label };
  const result = { dataset: { modelTestResult: "a" }, setAttribute() {} };
  h.context.document.getElementById = () => ({
    querySelectorAll: () => [result], querySelector: () => button,
  });
  vm.runInContext('providerAdminState.selectedId = "p"', h.context);
  h.admin.updateModelTests = h.render;
  const run = h.admin.testModels({ id: "p" });
  assert.equal(button.disabled, true);
  h.admin.cancelModelTests();
  assert.equal(button.disabled, false);
  assert.equal(label.textContent, "测试模型");
  assert.equal(result.textContent, "");
  release({ ok: true });
  await run;
  assert.equal(result.textContent, "");
});

test("仅测试当前 Provider 已开启模型，串行执行且逐行返回，不保存结果", async () => {
  let active = 0, maxActive = 0;
  const h = harness(async () => {
    maxActive = Math.max(maxActive, ++active);
    await Promise.resolve(); active--;
    return { ok: true, elapsed_ms: 12 };
  });
  await h.admin.testModels({ id: "p" });
  assert.equal(h.calls.length, 2);
  assert.match(h.calls[0][0], /llm-models\/a\/test\?provider_id=p$/);
  assert.match(h.calls[1][0], /llm-models\/b\/test/);
  assert.equal(maxActive, 1);
  assert.ok(h.views.some((view) => view.some(([id, status]) => id === "a" && status === "success") && view.some(([id, status]) => id === "b" && status === "testing")));
  assert.ok(h.calls.every(([, options]) => options.method === "POST" && !options.body));
  assert.doesNotMatch(source, /localStorage|sessionStorage/);
  assert.equal(harness().results().length, 0);
});

test("失败继续测试下一项，重复点击不会新建批次", async () => {
  let release;
  const h = harness(async () => { await new Promise((resolve) => { release = resolve; }); throw new Error("认证失败"); });
  const first = h.admin.testModels({ id: "p" });
  await h.admin.testModels({ id: "p" });
  assert.equal(h.calls.length, 1);
  release();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(h.calls.length, 2);
  release(); await first;
  assert.ok(h.results().every(([, result]) => result.status === "error" && result.message === "认证失败"));
});

test("切换时中止旧请求，迟到结果不写入，也不测试下一项", async () => {
  let release;
  const h = harness(() => new Promise((resolve) => { release = resolve; }));
  const run = h.admin.testModels({ id: "p" });
  h.admin.cancelModelTests();
  assert.equal(h.calls[0][1].signal.aborted, true);
  release({ ok: true }); await run;
  assert.equal(h.calls.length, 1);
  assert.equal(h.results().length, 0);
});

test("等待期间被停用的模型不会继续发请求", async () => {
  let release;
  const h = harness(() => new Promise((resolve) => { release = resolve; }));
  const run = h.admin.testModels({ id: "p" });
  h.context.state.llmModels.find((m) => m.id === "b").enabled = false;
  release({ ok: true }); await run;
  assert.equal(h.calls.length, 1);
});

test("按钮在从目录添加之前，结果紧邻开关左侧且无 HTML 注入", () => {
  assert.ok(source.indexOf("data-provider-test-models title=") < source.indexOf("data-provider-catalog>"));
  assert.match(source, /data-model-test-result=.*[\s\S]*?class="model-enabled-switch/);
  assert.match(source, /element\.textContent = result/);
  assert.match(source, /element\.title = result\?\.message/);
});
