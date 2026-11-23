import * as reasoning from "../../../console/reasoning-ui.js";
import * as providerCatalog from "../../../console/provider-catalog.js";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(new URL("../../../console/configuration/llm-models.js", import.meta.url), "utf8");
const template = await readFile(new URL("../../../console/configuration-template.html", import.meta.url), "utf8");
const marker = "single_system_message";
const inlineMarker = "inline_tool_schema_refs";

function harness(tags = [], providerId = "private") {
  const elements = new Map(), requests = [], errors = [];
  function element(id) {
    if (!elements.has(id)) {
      const classes = new Set(), handlers = new Map();
      elements.set(id, {
        id, _value: "", textContent: "", open: false, attributes: {},
        get value() { return this._value; },
        set value(value) { this._value = String(value); },
        set innerHTML(value) { this.value = value.match(/<option value="([^"]*)" selected/)?.[1] || value.match(/<option value="([^"]*)"/)?.[1] || ""; },
        classList: {
          add: (name) => classes.add(name), remove: (name) => classes.delete(name),
          contains: (name) => classes.has(name),
          toggle(name, enabled = !classes.has(name)) { if (enabled) classes.add(name); else classes.delete(name); },
        },
        setAttribute(name, value) { this.attributes[name] = value; },
        addEventListener(name, handler) { handlers.set(name, handler); },
        async fire(name) { await handlers.get(name)?.({ preventDefault() {}, currentTarget: this }); },
        focus() {}, showModal() { this.open = true; }, close() { this.open = false; },
      });
    }
    return elements.get(id);
  }
  const checks = {
    llmModelTag: ["attachment", "reasoning", "tool_call", "structured_output", "temperature", "open_weights", "interleaved"],
    llmModelModality: ["text", "image", "audio", "video"],
  };
  for (const [name, values] of Object.entries(checks)) checks[name] = values.map((value) => ({ value, checked: false }));
  const row = { id: "saved", provider_id: providerId, name: "模型", model: "qwen3.8-flash", description: "",
    context_window_tokens: 128000, max_output_tokens: 8192, input_modalities: ["text"], tags };
  const state = {
    llmProviders: [{ id: "qwen", provider_type: "alibaba" }, { id: "claude", provider_type: "anthropic" },
      { id: "private", provider_type: "openai_compatible" }],
    llmProviderTypes: [{ code: "alibaba", model_protocol: "openai_chat" }, { code: "anthropic", model_protocol: "anthropic" },
      { code: "openai_compatible", model_protocol: "openai_chat" }],
  };
  let loaded;
  const context = {
    state, window: { DeepAgentReasoning: reasoning, DeepAgentProviderCatalog: providerCatalog }, AbortController, URLSearchParams,
    escapeHtml: (value) => String(value || ""), providerTypeLabel: (provider) => provider.provider_type,
    document: {
      getElementById: element,
      querySelectorAll(selector) {
        const name = selector.match(/name="([^"]+)"/)?.[1];
        return (checks[name] || []).filter((item) => !selector.endsWith(":checked") || item.checked);
      },
      addEventListener(name, handler) { if (name === "DOMContentLoaded") loaded = handler; },
    },
    loadLlmProviders: async () => state.llmProviders,
    loadLlmProviderTypes: async () => state.llmProviderTypes,
    loadLlmModels: async () => [],
    populateModelProviderSelect: (value) => { element("llmModelProviderId").value = value; },
    apiFetch: async (path, options) => {
      requests.push({ path, ...options });
      if (context.failSave && options?.body) throw new Error("保存失败");
      if (path.includes("/catalog?") && context.catalogResponse) return context.catalogResponse();
      if (path.includes("/catalog?")) return { items: [{ model_label: "目录模型", providers: [{
        runtime_model: row.model, context_window_tokens: 256000, max_output_tokens: 16384,
        input_modalities: ["text", "image"], tags: ["structured_output"],
      }] }] };
      if (options?.body) return { ...row, ...JSON.parse(options.body) };
      return row;
    },
    AdminFormFeedback: { clear() {}, setSubmitting() {}, show(form, error) { errors.push(error.message); } },
    showToast() {},
    refreshAfterSuccessfulSave: async (label, callback) => callback(),
  };
  vm.runInNewContext(source, context);
  loaded();
  return { admin: context.window.llmModelsAdmin, context, row, state, requests, errors, element };
}

test("历史空配置与新建默认关闭，打开不写配置且高级区默认收起", async () => {
  for (const tags of [null, [], ["tool_call"]]) {
    const h = harness(tags);
    await h.admin.openEdit("saved");
    assert.equal(h.element("llmModelAdvancedSettings").open, false);
    assert.equal(h.element("llmModelAdvancedSection").classList.contains("hidden"), false);
    assert.equal(h.element("llmModelMergeSystem").attributes["aria-checked"], "false");
    assert.equal(h.element("llmModelAdvancedSummary").classList.contains("hidden"), true);
    assert.equal(h.requests.some((r) => r.method), false);
    assert.equal(h.admin.modelTags().includes(marker), false);
  }
  const h = harness([marker]);
  await h.admin.openEdit("saved");
  await h.admin.openCreate();
  assert.equal(h.admin.mergeLeadingSystem, false);
  assert.equal(h.element("llmModelAdvancedSettings").open, false);
});

test("开启的回显在收起时仍可见，恢复默认只改草稿，取消后重开仍使用保存值", async () => {
  const h = harness(["tool_call", "video_url", marker]);
  await h.admin.openEdit("saved");
  assert.equal(h.element("llmModelAdvancedSummary").textContent, "已调整 1 项");
  assert.equal(h.element("restoreLlmModelAdvanced").classList.contains("hidden"), true);
  h.element("llmModelAdvancedSettings").open = true;
  await h.element("llmModelAdvancedSettings").fire("toggle");
  assert.equal(h.element("restoreLlmModelAdvanced").classList.contains("hidden"), false);
  await h.element("restoreLlmModelAdvanced").fire("click");
  assert.equal(h.admin.mergeLeadingSystem, false);
  assert.equal(h.requests.some((r) => r.method), false);
  await h.element("cancelLlmModelBtn").fire("click");
  await h.admin.openEdit("saved");
  assert.equal(h.admin.mergeLeadingSystem, true);
  assert.equal(h.element("llmModelAdvancedSettings").open, false);
});

test("目录刷新只替换能力，不丢兼容设置与其他已有标签", async () => {
  const h = harness(["tool_call", "custom:existing", "video_url", marker]);
  await h.admin.openEdit("saved");
  await h.admin.updateFromCatalog();
  await h.admin.save({ preventDefault() {}, currentTarget: h.element("llmModelForm") });
  const payload = JSON.parse(h.requests.find((r) => r.method === "PATCH").body);
  assert.deepEqual(payload.tags, ["structured_output", "custom:existing", "video_url", marker]);
  assert.deepEqual(payload.input_modalities, ["text", "image"]);
  assert.equal(payload.context_window_tokens, 256000);
});

test("MiniMax 能力刷新仅查询国内目录，国内未命中时保留现有配置", async () => {
  const h = harness([], "minimax-provider");
  h.state.llmProviders.push({ id: "minimax-provider", provider_type: "minimax" });
  h.state.llmProviderTypes.push({ code: "minimax", model_protocol: "anthropic" });
  h.row.model = "MiniMax-M2.5";
  h.row.reasoning_catalog = { provider_id: "minimax", provider_model_id: h.row.model };
  h.context.catalogResponse = () => {
    const query = h.requests.at(-1).path.split("?")[1];
    assert.equal(new URLSearchParams(query).get("provider_id"), "minimax-cn");
    return { items: [] };
  };
  await h.admin.openEdit("saved");
  await h.admin.updateFromCatalog();
  assert.equal(h.element("llmModelContextWindow").value, String(h.row.context_window_tokens));
  assert.equal(h.admin.reasoningPayload.reasoning_catalog, undefined);
  assert.match(h.element("llmModelCatalogFeedback").textContent, /没有找到/);
  h.context.catalogResponse = () => ({ items: [{ model_label: h.row.model, providers: [{
    provider_id: "minimax-cn", provider_model_id: h.row.model,
    context_window_tokens: 204800, max_output_tokens: 32768, input_modalities: ["text"], tags: [],
    reasoning_options: [{ type: "effort", values: ["high"] }],
  }] }] });
  await h.admin.updateFromCatalog();
  assert.equal(h.element("llmModelContextWindow").value, "204800");
  assert.equal(h.admin.reasoningPayload.reasoning_catalog.provider_id, "minimax-cn");
});

test("目录恢复匹配原生模型名，只恢复档位并忽略已离开的弹框请求", async () => {
  const h = harness([], "qwen");
  const catalog = { items: [{ model_label: "Qwen", providers: [{
    provider_id: "alibaba", provider_model_id: h.row.model, runtime_model: `openai:${h.row.model}`,
    context_window_tokens: 256000, max_output_tokens: 16384,
    input_modalities: ["text", "image"], tags: ["structured_output"],
    reasoning_options: [{ type: "effort", values: ["low", "high"] }],
  }] }] };
  await h.admin.openEdit("saved");
  h.element("llmModelContextWindow").value = "777777";
  h.context.catalogResponse = async () => catalog;
  await h.admin.updateFromCatalog(true);
  assert.equal(h.element("llmModelContextWindow").value, "777777");
  assert.deepEqual({ ...h.admin.reasoningPayload.reasoning_catalog }, {
    provider_id: "alibaba", provider_model_id: h.row.model,
  });

  let reply;
  h.context.catalogResponse = () => new Promise(resolve => { reply = resolve; });
  const refresh = h.admin.updateFromCatalog();
  await h.admin.openEdit("saved");
  const request = h.requests.findLast(r => r.path.includes("/catalog?"));
  assert.equal(request.signal.aborted, true);
  reply(catalog);
  await refresh;
  assert.equal(h.element("llmModelContextWindow").value, String(h.row.context_window_tokens));
  assert.equal(h.admin.reasoningPayload.reasoning_catalog, undefined);
});

test("开启和关闭保存都保留未知标签，保存失败保留弹框及草稿", async () => {
  const h = harness(["tool_call", "custom:existing"]);
  await h.admin.openEdit("saved");
  await h.element("llmModelMergeSystem").fire("click");
  h.context.failSave = true;
  await h.admin.save({ preventDefault() {}, currentTarget: h.element("llmModelForm") });
  assert.deepEqual(h.errors, ["保存失败"]);
  assert.equal(h.element("llmModelModal").open, true);
  assert.equal(h.admin.mergeLeadingSystem, true);
  h.context.failSave = false;
  await h.admin.save({ preventDefault() {}, currentTarget: h.element("llmModelForm") });
  assert.deepEqual(JSON.parse(h.requests.at(-1).body).tags, ["tool_call", "custom:existing", marker]);
  await h.element("llmModelMergeSystem").fire("click");
  await h.admin.save({ preventDefault() {}, currentTarget: h.element("llmModelForm") });
  assert.deepEqual(JSON.parse(h.requests.at(-1).body).tags, ["tool_call", "custom:existing"]);
});

test("复制继承设置，切换不适用协议仅隐藏设置而不删除它", async () => {
  const h = harness(["tool_call", marker]);
  await h.admin.openCopy("saved");
  assert.equal(h.admin.mergeLeadingSystem, true);
  assert.equal(h.state.editingLlmModelId, null);
  h.element("llmModelProviderId").value = "claude";
  await h.element("llmModelProviderId").fire("change");
  assert.equal(h.element("llmModelAdvancedSection").classList.contains("hidden"), true);
  assert.equal(h.admin.modelTags().includes(marker), true);
  h.element("llmModelProviderId").value = "private";
  await h.element("llmModelProviderId").fire("change");
  assert.equal(h.element("llmModelAdvancedSection").classList.contains("hidden"), false);
  assert.equal(h.element("llmModelMergeSystem").attributes["aria-checked"], "true");
  await h.admin.save({ preventDefault() {}, currentTarget: h.element("llmModelForm") });
  assert.equal(h.requests.at(-1).method, "POST");
  assert.deepEqual(JSON.parse(h.requests.at(-1).body).tags, ["tool_call", marker]);
});

test("高级开关与说明可访问，兼容标记不加入能力复选框", () => {
  assert.match(template, /role="switch"[^>]*aria-checked="false"[^>]*aria-labelledby="llmModelMergeSystemLabel"/);
  assert.match(template, /aria-describedby="llmModelMergeSystemHelp"/);
  assert.doesNotMatch(template, /name="llmModelTag" value="single_system_message"/);
  assert.match(template, /role="switch"[^>]*aria-checked="false"[^>]*aria-labelledby="llmModelInlineToolRefsLabel"/);
  assert.doesNotMatch(template, /name="llmModelTag" value="inline_tool_schema_refs"/);
});

test("切换协议保留高级区展开状态，Responses 下禁用 Chat 兼容开关", async () => {
  const h = harness([marker, inlineMarker, 'reasoning'], 'private');
  h.row.reasoning_options = [{ type: "effort", values: ["low", "high"] }];
  h.row.reasoning_source = "manual";
  h.state.llmProviders.push({ id: 'gpt', provider_type: 'openai' });
  h.state.llmProviderTypes.push({ code: 'openai', model_protocol: 'openai_responses' });
  await h.admin.openEdit('saved');
  assert.equal(h.element('llmModelProtocolChat').checked, true);
  const details = h.element('llmModelAdvancedSettings');
  const mergeSystem = h.element('llmModelMergeSystem');
  const inlineRefs = h.element('llmModelInlineToolRefs');
  const reset = h.element('restoreLlmModelAdvanced');
  details.open = true;
  await details.fire('toggle');
  assert.equal(reset.classList.contains('hidden'), false);
  h.element('llmModelProtocolResponses').checked = true;
  await h.element('llmModelProtocolResponses').fire('change');
  assert.equal(h.element('llmModelAdvancedSection').classList.contains('hidden'), false);
  assert.equal(details.open, true);
  assert.equal(mergeSystem.disabled, true);
  assert.equal(inlineRefs.disabled, true);
  assert.equal(reset.classList.contains('hidden'), true);
  assert.equal(h.admin.modelTags().includes('openai_responses'), true);
  assert.equal(h.admin.modelTags().includes(marker), true);
  assert.deepEqual(h.admin.reasoningOptions, h.row.reasoning_options);
  h.element('llmModelProtocolChat').checked = true;
  await h.element('llmModelProtocolChat').fire('change');
  assert.equal(details.open, true);
  assert.equal(mergeSystem.disabled, false);
  assert.equal(inlineRefs.disabled, false);
  assert.equal(reset.classList.contains('hidden'), false);
  h.element('llmModelProtocolResponses').checked = true;
  await h.element('llmModelProtocolResponses').fire('change');
  await h.admin.updateFromCatalog();
  await h.admin.save({ preventDefault() {}, currentTarget: h.element('llmModelForm') });
  assert.equal(JSON.parse(h.requests.at(-1).body).tags.includes('openai_responses'), true);
  h.row.tags.push('openai_responses');
  await h.admin.openCopy('saved');
  assert.equal(h.element('llmModelProtocolResponses').checked, true);
  h.element('llmModelProviderId').value = 'qwen';
  await h.element('llmModelProviderId').fire('change');
  assert.equal(h.element('llmModelProtocolField').classList.contains('hidden'), true);
  h.element('llmModelProviderId').value = 'gpt';
  await h.element('llmModelProviderId').fire('change');
  assert.equal(h.element('llmModelProtocolField').classList.contains('hidden'), true);
  h.element('llmModelProviderId').value = 'private';
  await h.element('llmModelProviderId').fire('change');
  assert.equal(h.element('llmModelProtocolResponses').checked, true);
  h.element('llmModelProtocolChat').checked = true;
  await h.element('llmModelProtocolChat').fire('change');
  assert.equal(h.element('llmModelAdvancedSection').classList.contains('hidden'), false);
  assert.equal(h.admin.modelTags().includes('openai_responses'), false);
  assert.equal(h.admin.modelTags().includes(marker), true);
});

test("展开引用默认关闭，两个标记独立保存，复制和目录刷新继承", async () => {
  const h = harness(["tool_call", marker], "private");
  await h.admin.openEdit("saved");
  assert.equal(h.element("llmModelAdvancedSection").classList.contains("hidden"), false);
  assert.equal(h.element("llmModelInlineToolRefs").attributes["aria-checked"], "false");
  await h.element("llmModelInlineToolRefs").fire("click");
  assert.equal(h.element("llmModelAdvancedSummary").textContent, "已调整 2 项");
  await h.element("llmModelMergeSystem").fire("click");
  await h.admin.save({ preventDefault() {}, currentTarget: h.element("llmModelForm") });
  assert.deepEqual(JSON.parse(h.requests.at(-1).body).tags, ["tool_call", inlineMarker]);
  h.row.tags.push(inlineMarker);
  await h.admin.openCopy("saved");
  assert.equal(h.element("llmModelAdvancedSummary").textContent, "已调整 2 项");
  await h.admin.openEdit("saved");
  await h.admin.updateFromCatalog();
  assert.equal(Number(h.element("llmModelContextWindow").value), 256000);
  assert.equal(h.admin.modelTags().includes(inlineMarker), true);
});

test("切换到其他 Provider 隐藏整个高级区，切回后保留两项设置", async () => {
  const h = harness([marker, inlineMarker, "custom:existing"], "private");
  await h.admin.openEdit("saved");
  h.element("llmModelProviderId").value = "qwen";
  await h.element("llmModelProviderId").fire("change");
  assert.equal(h.element("llmModelAdvancedSection").classList.contains("hidden"), true);
  assert.equal(h.element("llmModelAdvancedSettings").open, false);
  assert.equal(h.element("llmModelAdvancedSummary").classList.contains("hidden"), true);
  await h.element("restoreLlmModelAdvanced").fire("click");
  assert.deepEqual(Array.from(h.admin.modelTags()), ["custom:existing", marker, inlineMarker]);
  h.element("llmModelProviderId").value = "private";
  await h.element("llmModelProviderId").fire("change");
  assert.equal(h.element("llmModelMergeSystem").attributes["aria-checked"], "true");
  assert.equal(h.element("llmModelInlineToolRefs").attributes["aria-checked"], "true");
  await h.element("restoreLlmModelAdvanced").fire("click");
  assert.deepEqual(Array.from(h.admin.modelTags()), ["custom:existing"]);
  assert.equal(h.requests.some((request) => request.method), false);
});
