import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";
import * as catalog from "../../../console/provider-catalog.js";

const source = await readFile(new URL("../../../console/configuration/llm-providers.js", import.meta.url), "utf8");
const model = (id, label = id) => ({ provider_model_id: id, model_label: label, input_modalities: ["text"], context_window_tokens: 256000, max_output_tokens: 32000, tags: ["tool_call"] });
const directory = { items: [
  { provider_id: "deepseek", provider_label: "DeepSeek", models: [model("model-a", "模型 A"), model("model-b", "模型 B")] },
  { provider_id: "anthropic", provider_label: "Anthropic", models: [model("claude")] },
] };
const generic = { id: "customer", provider_type: "openai_compatible", name: "客户网关", base_url: "https://customer.invalid/v1", api_key: "must-not-copy" };

function harness({ request = async () => directory, existing = [], providerTypes = [] } = {}) {
  const nodes = new Map();
  const listeners = new Map();
  const calls = { requests: [], toasts: [], errors: [], refreshes: 0 };
  const node = (id) => {
    if (!nodes.has(id)) nodes.set(id, {
      id, innerHTML: "", textContent: "", value: "", disabled: false, open: false, dataset: {},
      classList: { toggle() {}, add() {}, remove() {} },
      querySelectorAll() { return []; },
      addEventListener(name, handler) { listeners.set(`${id}:${name}`, handler); },
      close() { this.open = false; }, showModal() { this.open = true; },
    });
    return nodes.get(id);
  };
  const context = {
    window: { DeepAgentProviderCatalog: catalog },
    document: { getElementById: node, querySelectorAll: () => [], addEventListener: (name, handler) => listeners.set(name, handler) },
    state: { llmModels: existing, llmProviders: [], llmProviderTypes: providerTypes },
    AbortController, URLSearchParams,
    escapeHtml: (value) => String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;"),
    apiFetch: async (url, options) => { calls.requests.push({ url, options }); return request(url, options); },
    AdminFormFeedback: { clear() {}, show: (_form, error) => calls.errors.push(String(error?.message || error)) },
    showToast: (...args) => calls.toasts.push(args),
  };
  vm.runInNewContext(source, context);
  const admin = context.window.llmProvidersAdmin;
  admin.refresh = async () => { calls.refreshes++; return true; };
  const picker = vm.runInNewContext("providerCatalogPickerState", context);
  const submit = () => admin.submitCatalogPicker({ preventDefault() {}, currentTarget: node("providerCatalogPickerForm") });
  return { admin, picker, context, node, calls, listeners, submit };
}

test("供应商类型按协议分组，并保持订阅入口独立", () => {
  const h = harness({ providerTypes: [
    { code: "google", label: "Google", model_protocol: "google" },
    { code: "stepfun", label: "阶跃", model_protocol: "openai_chat" },
    { code: "minimax", label: "MiniMax", model_protocol: "anthropic" },
    { code: "anthropic_compatible", label: "Anthropic Compatible", model_protocol: "anthropic" },
    { code: "openai_compatible", label: "OpenAI Compatible", model_protocol: "openai_chat" },
    { code: "openai_codex", label: "ChatGPT", model_protocol: "openai_codex" },
  ] });
  h.context.renderProviderTypeOptions("minimax");
  const html = h.node("llmProviderType").innerHTML;
  assert.match(html, /OpenAI 兼容协议.*stepfun.*Anthropic 兼容协议.*minimax.*其他协议.*google.*自定义接入.*openai_compatible.*anthropic_compatible/);
  assert.match(html, /value="minimax" selected/);
  assert.doesNotMatch(html, /openai_codex/);
  assert.equal(catalog.catalogProviderIdForType("minimax"), "minimax-cn");
  assert.equal(catalog.catalogProviderIdForType("stepfun"), "stepfun");
});

for (const type of ["openai_compatible", "anthropic_compatible"]) {
  test(`${type} 跨目录纯新建，不以现有模型禁选，不复制官方连接字段`, async () => {
    const h = harness({ existing: [{ provider_id: "customer", model: "model-a" }] });
    await h.admin.initializeFromCatalog({ ...generic, provider_type: type });
    assert.equal(new URLSearchParams(h.calls.requests[0].url.split("?")[1]).has("provider_id"), false);
    assert.equal(h.picker.sourceId, "");
    assert.match(h.node("providerCatalogChoiceList").innerHTML, /请先选择目录供应商/);
    h.admin.selectCatalogSource("deepseek");
    assert.equal(h.picker.choices[0].added, false);
    h.picker.selectedIds.add("model-a");
    await h.submit();
    const post = h.calls.requests.find((item) => item.options?.method === "POST");
    const payload = JSON.parse(post.options.body);
    assert.deepEqual(payload, { provider_id: "customer", name: "model-a", description: null, model: "model-a", input_modalities: ["text"], context_window_tokens: 256000, max_output_tokens: 32000, tags: ["tool_call"] });
    assert.equal(h.node("providerCatalogPickerModal").open, false);
    await h.admin.initializeFromCatalog({ ...generic, provider_type: type });
    h.admin.selectCatalogSource("deepseek");
    assert.equal(h.picker.choices[0].added, false);
    assert.equal(h.picker.createdKeys.size, 0);
  });
}

test("品牌供应商即使使用客户地址，仍锁定本品牌目录并禁选已有模型", async () => {
  const h = harness({ existing: [{ provider_id: "customer", model: "model-a" }] });
  await h.admin.initializeFromCatalog({ ...generic, provider_type: "deepseek" });
  assert.equal(new URLSearchParams(h.calls.requests[0].url.split("?")[1]).get("provider_id"), "deepseek");
  assert.equal(h.picker.sourceId, "deepseek");
  assert.equal(h.picker.choices[0].added, true);
  h.admin.toggleCatalogPickerSelection();
  assert.deepEqual([...h.picker.selectedIds], ["model-b"]);
});

test("目录搜索支持名称和 ID，筛选保留已选项，切供应商清空选择", async () => {
  const h = harness();
  await h.admin.initializeFromCatalog(generic);
  h.admin.selectCatalogSource("deepseek");
  h.picker.query = "模型 A";
  h.admin.toggleCatalogPickerSelection();
  assert.deepEqual([...h.picker.selectedIds], ["model-a"]);
  h.picker.query = "model-b";
  h.admin.toggleCatalogPickerSelection();
  assert.deepEqual([...h.picker.selectedIds], ["model-a", "model-b"]);
  h.admin.selectCatalogSource("anthropic");
  assert.equal(h.picker.selectedIds.size, 0);
  assert.equal(h.picker.query, "");
  assert.deepEqual(h.picker.choices.map((item) => item.model), ["claude"]);
});

test("批量部分失败保留具体原因和失败选项，重试不再次提交成功项", async () => {
  let failedOnce = false;
  const h = harness({ request: async (_url, options) => {
    if (options?.method !== "POST") return directory;
    if (JSON.parse(options.body).model === "model-b" && !failedOnce) { failedOnce = true; throw new Error("当前供应商下名称已存在"); }
    return {};
  } });
  await h.admin.initializeFromCatalog(generic);
  h.admin.selectCatalogSource("deepseek");
  h.admin.toggleCatalogPickerSelection();
  await h.submit();
  assert.equal(h.node("providerCatalogPickerModal").open, true);
  assert.deepEqual([...h.picker.selectedIds], ["model-b"]);
  assert.match(h.node("providerCatalogChoiceList").innerHTML, /本次已添加/);
  assert.match(h.node("providerCatalogChoiceList").innerHTML, /当前供应商下名称已存在/);
  assert.match(h.calls.errors.at(-1), /新增 1 个，失败 1 个/);
  await h.submit();
  const posted = h.calls.requests.filter((item) => item.options?.method === "POST").map((item) => JSON.parse(item.options.body).model);
  assert.deepEqual(posted, ["model-a", "model-b", "model-b"]);
});

test("关闭和重新打开会取消旧目录请求，迟到响应不会污染新弹窗", async () => {
  let resolveFirst;
  let count = 0;
  const h = harness({ request: async () => ++count === 1 ? new Promise((resolve) => { resolveFirst = resolve; }) : directory });
  const first = h.admin.initializeFromCatalog(generic);
  const oldSignal = h.calls.requests[0].options.signal;
  h.admin.closeCatalogPicker();
  await h.admin.initializeFromCatalog({ ...generic, id: "second", name: "第二个供应商" });
  assert.equal(oldSignal.aborted, true);
  resolveFirst({ items: [{ provider_id: "stale", models: [model("stale")] }] });
  await first;
  assert.equal(h.picker.provider.id, "second");
  assert.equal(h.picker.sources[0].provider_id, "deepseek");
});

test("提交期间拒绝重复提交和普通关闭，切页面后停止剩余创建及迟到提示", async () => {
  let resolvePost;
  const h = harness({ request: async (_url, options) => options?.method === "POST" ? new Promise((resolve) => { resolvePost = resolve; }) : directory });
  await h.admin.initializeFromCatalog(generic);
  h.admin.selectCatalogSource("deepseek");
  h.admin.toggleCatalogPickerSelection();
  const first = h.submit();
  await h.submit();
  assert.equal(h.admin.closeCatalogPicker(), false);
  assert.equal(h.picker.submitting, true);
  h.admin.closeCatalogPicker({ force: true });
  resolvePost({});
  await first;
  assert.equal(h.calls.requests.filter((item) => item.options?.method === "POST").length, 1);
  assert.equal(h.calls.refreshes, 0);
  assert.equal(h.calls.toasts.length, 0);
  assert.equal(h.picker.provider, null);
});

test("目录错误在当前弹窗提示并恢复可关闭状态", async () => {
  const h = harness({ request: async () => { throw new Error("数据库暂不可用"); } });
  await h.admin.initializeFromCatalog(generic);
  assert.equal(h.picker.loading, false);
  assert.match(h.calls.errors[0], /数据库暂不可用/);
  assert.equal(h.admin.closeCatalogPicker(), true);
});
