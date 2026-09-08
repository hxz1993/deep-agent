import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  firstSelectableModelId,
  isModelSelectable,
  preserveCurrentModel,
  renderGroupedModelOptions,
} from "../../../console/model-options.js";
import {
  buildProviderCatalogChoices,
  buildProviderCatalogCreatePlan,
  catalogProviderIdForType,
  providerCatalogSummary,
} from "../../../console/provider-catalog.js";

const providerSource = await readFile(
  new URL("../../../console/configuration/llm-providers.js", import.meta.url),
  "utf8",
);
const modelSource = await readFile(
  new URL("../../../console/configuration/llm-models.js", import.meta.url),
  "utf8",
);
const coreSource = await readFile(
  new URL("../../../console/configuration/core.js", import.meta.url),
  "utf8",
);
const chatSource = await readFile(
  new URL("../../../console/chat.js", import.meta.url),
  "utf8",
);
const template = await readFile(
  new URL("../../../console/configuration-template.html", import.meta.url),
  "utf8",
);
const indexHtml = await readFile(
  new URL("../../../console/index.html", import.meta.url),
  "utf8",
);

test("配置中心只保留 Provider 模型管理入口并继续保留独立模型目录", () => {
  const agent = indexHtml.indexOf('data-tab="agents"');
  const provider = indexHtml.indexOf('data-tab="llm-providers"');
  const builtin = indexHtml.indexOf('data-tab="builtin-tools"');
  assert.ok(agent < provider);
  assert.ok(provider < builtin);
  assert.doesNotMatch(indexHtml, /data-tab="llm-models"/);
  assert.doesNotMatch(template, /id="panel-llm-models"|id="llmModelsTableBody"/);
  assert.match(indexHtml, /data-tab="catalog"/);
  assert.match(template, /id="llmModelModal"/);
  assert.match(template, /id="panel-llm-providers"[\s\S]*id="llmProviderList"[\s\S]*id="llmProviderDetail"/);
});

test("Provider 使用正式 API 和后端类型目录契约", () => {
  assert.match(coreSource, /apiFetch\("\/api\/v1\/llm-providers"\)/);
  assert.match(coreSource, /apiFetch\("\/api\/v1\/llm-provider-types"\)/);
  assert.doesNotMatch(coreSource, /llm-providers\/types/);
  assert.match(providerSource, /supports_default_base_url/);
  assert.match(providerSource, /supports_custom_base_url/);
  assert.match(providerSource, /default_base_url/);
  assert.match(template, /class="field-label-actions">[\s\S]*Base URL[\s\S]*class="provider-default-switch"[\s\S]*使用默认地址[\s\S]*id="llmProviderUseDefaultBaseUrl"/);
  assert.doesNotMatch(template, /使用框架默认地址|provider-default-toggle/);
  assert.match(providerSource, /llmProviderUseDefaultBaseUrl"\)\.checked = Boolean\(type\?\.supports_default_base_url && type\?\.default_base_url\)/);
});

test("Provider Key 回显脱敏标识且未修改时不重复写入", () => {
  assert.match(providerSource, /provider\.api_key_set \? "Key 已配" : "Key 未配"/);
  assert.match(providerSource, /maskedValue: provider\.api_key_masked \|\| ""/);
  assert.match(providerSource, /apiKey !== maskedValue/);
  assert.doesNotMatch(providerSource, /\.api_key\s*\|\||value = provider\.api_key/);
  assert.match(template, /id="llmProviderClearApiKey">清除/);
  assert.match(template, /class="field-label-actions"><label for="llmProviderApiKey">API Key<\/label><button/);
  assert.doesNotMatch(template, /<label class="field">\s*<span class="field-label-actions"/);
  assert.doesNotMatch(template, /llmProviderApiKeyState|llmProviderBaseUrlHint|清除当前 API Key|API Key（编辑时留空表示不修改）/);
});

test("Provider 无启停层而关联模型可独立启停", () => {
  assert.doesNotMatch(template, /id="llmProviderEnabled"|启用 Provider/);
  assert.doesNotMatch(providerSource, /provider\.enabled|data-provider-toggle/);
  assert.match(providerSource, /model\.enabled/);
  assert.match(providerSource, /data-toggle-model/);
  assert.match(modelSource, /toggleEnabled/);
  assert.match(coreSource, /\/api\/v1\/llm-models\?selectable_only=false/);
});

test("Provider 详情操作统一使用轻风格按钮", () => {
  assert.match(providerSource, /class="row-actions provider-action-group"/);
  assert.match(providerSource, /class="provider-action" data-provider-edit/);
  assert.match(providerSource, /class="provider-action danger" data-provider-delete/);
  assert.match(providerSource, /class="provider-action" data-provider-catalog/);
  assert.match(providerSource, /class="provider-action" data-provider-model/);
  assert.match(providerSource, /data-delete-model/);
  assert.doesNotMatch(providerSource, /data-provider-toggle/);
});

test("Provider 接入信息使用紧凑图标胶囊而不是纵向定义列表", () => {
  assert.match(providerSource, /class="provider-detail-title-line">[\s\S]*class="provider-facts" aria-label="Provider 接入信息"/);
  assert.match(providerSource, /class="provider-facts" aria-label="Provider 接入信息"/);
  assert.match(providerSource, /class="provider-fact provider-fact-url"/);
  assert.match(providerSource, /data-lucide="link-2"/);
  assert.match(providerSource, /data-lucide="route"/);
  assert.match(providerSource, /Key 已配/);
  assert.doesNotMatch(providerSource, /<small><span>\$\{escapeHtml\(providerTypeLabel\(provider\)\)\}<\/span><code>/);
  assert.doesNotMatch(providerSource, /<dl class="provider-facts">|<dt>Base URL<\/dt>/);
  assert.doesNotMatch(providerSource, /个模型使用此 Provider/);
});

test("Provider 使用本地官方标识且模型行只展示 API 编码和两项能力事实", () => {
  for (const name of ["deepseek", "kimi", "qwen", "zai", "openrouter", "openai", "anthropic", "google-gemini", "openai-compatible"]) {
    assert.match(providerSource, new RegExp(`provider-logos/${name}`));
  }
  assert.match(providerSource, /<code>\$\{escapeHtml\(model\.model\)\}<\/code>/);
  assert.match(providerSource, /provider-model-facts/);
  assert.match(providerSource, /input_modalities/);
  assert.match(providerSource, /context_window_tokens/);
  assert.doesNotMatch(providerSource, /escapeHtml\(context\)\} 上下文/);
  assert.doesNotMatch(providerSource, /provider-model-chip|>LLM<|>CHAT</);
});

test("Provider 保存由表单捕获提交模式，避免创建被异步状态改写为编辑", () => {
  assert.match(providerSource, /const editingId = String\(form\.dataset\.providerId \|\| ""\)\.trim\(\) \|\| null/);
  assert.match(providerSource, /method: editingId \? "PATCH" : "POST"/);
  assert.doesNotMatch(providerSource, /providerAdminState\.editingId/);
});

test("Provider 目录初始化只映射明确渠道并跳过当前 Provider 已有模型", () => {
  assert.equal(catalogProviderIdForType("deepseek"), "deepseek");
  assert.equal(catalogProviderIdForType("zai"), "zhipuai");
  assert.equal(catalogProviderIdForType("openrouter"), null);
  assert.equal(catalogProviderIdForType("openai_compatible"), null);
  const plan = buildProviderCatalogCreatePlan({
    provider: { id: "provider-1", api_key_set: false },
    existingModels: [{ model: "existing-model" }],
    catalogModels: [
      { provider_model_id: "existing-model", input_modalities: ["text"], context_window_tokens: 1_000, max_output_tokens: 100, tags: [] },
      { provider_model_id: "new-model", input_modalities: ["text", "image"], context_window_tokens: 8_000, max_output_tokens: 2_000, tags: ["tool_call"] },
      { provider_model_id: "new-model", input_modalities: ["text"], context_window_tokens: 8_000, max_output_tokens: 2_000, tags: [] },
    ],
  });
  assert.equal(plan.skipped, 2);
  assert.equal(plan.payloads.length, 1);
  assert.deepEqual(plan.payloads[0], {
    provider_id: "provider-1",
    name: "new-model",
    description: null,
    model: "new-model",
    input_modalities: ["text", "image"],
    context_window_tokens: 8_000,
    max_output_tokens: 2_000,
    tags: ["tool_call"],
  });
  assert.equal(providerCatalogSummary({ created: 1, skipped: 2, failed: 1 }), "新增 1 个，跳过 2 个，失败 1 个");
  const choices = buildProviderCatalogChoices({
    provider: { id: "provider-1" },
    existingModels: [{ model: "existing-model" }],
    catalogModels: [
      { provider_model_id: "existing-model", input_modalities: ["text"] },
      { provider_model_id: "new-model", input_modalities: ["text"] },
    ],
  }).choices;
  assert.deepEqual(choices.map(({ model, added }) => ({ model, added })), [
    { model: "existing-model", added: true },
    { model: "new-model", added: false },
  ]);
  assert.match(providerSource, /view: "provider"/);
  assert.match(template, /id="providerCatalogPickerModal"[\s\S]*id="providerCatalogChoiceList"[\s\S]*添加所选/);
  assert.match(providerSource, /buildProviderCatalogChoices/);
  assert.match(providerSource, /choice\.added \? "disabled"/);
  assert.match(providerSource, /submitCatalogPicker\(event\)/);
  assert.match(providerSource, /for \(const choice of selected\)/);
});

test("模型只提交 Provider 逻辑关联并移除重复连接字段", () => {
  assert.match(modelSource, /provider_id: document\.getElementById\("llmModelProviderId"\)\.value/);
  assert.match(modelSource, /nameInput\.required = !deriveName/);
  assert.doesNotMatch(modelSource, /base_url:/);
  assert.doesNotMatch(modelSource, /api_key:/);
  assert.doesNotMatch(template, /id="llmModelBaseUrl"|id="llmModelApiKey"/);
  assert.doesNotMatch(template, /id="llmModelEnabled"|启用模型/);
  assert.doesNotMatch(modelSource, /getElementById\("llmModelEnabled"\)/);
});

test("目录按 0、1、多 Provider 分流且不再按 URL 猜测", () => {
  assert.match(modelSource, /if \(!matches\.length\)/);
  assert.match(modelSource, /if \(matches\.length > 1\)/);
  assert.match(modelSource, /title: "选择 Provider"/);
  assert.match(modelSource, /fields: \[\{/);
  assert.doesNotMatch(modelSource, /provider_api_url.*base_url|normalizeBaseUrl/);
});

test("业务模型选项隐藏不可用模型并仅保留既有 Agent 当前引用", () => {
  const models = [
    { id: "a", name: "A", model: "model-a", selectable: true, provider: { id: "p1", name: "DeepSeek", api_key_set: true } },
    { id: "b", name: "B", model: "model-b", selectable: true, provider: { id: "p2", name: "Moonshot", api_key_set: true } },
    { id: "c", name: "C", model: "model-c", selectable: false, unavailable_reason: "provider_unconfigured", provider: { id: "p3", name: "Compatible", api_key_set: false } },
    { id: "d", name: "D", model: "model-d", selectable: false, unavailable_reason: "provider_invalid", provider: { id: "p4", name: "OpenAI", api_key_set: true } },
    { id: "e", name: "E", model: "model-e", enabled: false, selectable: false, unavailable_reason: "model_disabled", provider: { id: "p1", name: "DeepSeek", api_key_set: true } },
  ];
  const html = renderGroupedModelOptions(models, { selectedId: "" });
  assert.match(html, /<optgroup label="DeepSeek">/);
  assert.match(html, /<optgroup label="Moonshot">/);
  assert.doesNotMatch(html, /value="b" disabled/);
  assert.doesNotMatch(html, /value="c"/);
  assert.doesNotMatch(html, /value="d"/);
  assert.doesNotMatch(html, /value="e"/);
  assert.equal(firstSelectableModelId(models), "a");

  const preserved = renderGroupedModelOptions(models, { selectedId: "c" });
  assert.match(preserved, /value="c" selected[^>]*>C — model-c（Provider 未配置密钥）<\/option>/);
  assert.doesNotMatch(preserved, /value="d"|value="e"/);

  assert.equal(isModelSelectable({ enabled: false, provider: { api_key_set: true } }), false);
  const retained = preserveCurrentModel(models.filter((model) => model.id !== "e"), models[4]);
  assert.equal(retained.at(-1).id, "e");
  assert.equal(retained.at(-1).selectable, false);
  assert.equal(retained.at(-1).unavailable_reason, "model_disabled");
  assert.equal(retained.at(-1).provider.name, "当前 Agent 模型");
  assert.equal(preserveCurrentModel(models, models[0]).length, models.length);
});

test("Agent 和 Chat 统一复用 Provider 分组模型选项", () => {
  assert.match(coreSource, /renderGroupedModelOptions/);
  assert.match(chatSource, /renderGroupedModelOptions/);
  assert.match(chatSource, /firstSelectableModelId/);
  assert.match(chatSource, /preserveCurrentModel\(await r\.json\(\), currentAgent\(\)\?\.model\)/);
});
