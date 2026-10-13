export const PROVIDER_CATALOG_IDS = Object.freeze({
  deepseek: "deepseek",
  moonshotai: "moonshotai",
  alibaba: "alibaba",
  zai: "zhipuai",
  openai: "openai",
  openai_codex: "openai",
  anthropic: "anthropic",
  google: "google",
  minimax: "minimax-cn",
  stepfun: "stepfun",
});

export function isGenericCatalogProvider(providerType) {
  return ["openai_compatible", "anthropic_compatible"].includes(String(providerType || "").trim().toLowerCase());
}

export function groupProviderTypes(types) {
  const groups = [
    { label: "OpenAI 兼容协议", protocol: "openai_chat", items: [] },
    { label: "Anthropic 兼容协议", protocol: "anthropic", items: [] },
    { label: "其他协议", protocol: null, items: [] },
  ];
  const custom = { label: "自定义接入", items: [] };
  for (const type of types || []) {
    if (isGenericCatalogProvider(type.code)) {
      custom.items.push(type);
      continue;
    }
    const group = groups.find((item) => item.protocol === type.model_protocol) || groups[2];
    group.items.push(type);
  }
  custom.items.sort((a, b) => Number(a.code === "anthropic_compatible") - Number(b.code === "anthropic_compatible"));
  return [...groups, custom].filter((group) => group.items.length);
}

export function catalogProviderIdForType(providerType) {
  return PROVIDER_CATALOG_IDS[String(providerType || "").trim().toLowerCase()] || null;
}

function normalizedModelCode(value) {
  return String(value || "").trim().toLowerCase();
}

export function buildProviderCatalogCreatePlan({ provider, catalogModels, existingModels }) {
  const { choices, skipped } = buildProviderCatalogChoices({
    provider,
    catalogModels,
    existingModels,
  });
  return {
    payloads: choices.filter((choice) => !choice.added).map((choice) => choice.payload),
    skipped,
  };
}

export function buildProviderCatalogChoices({ provider, catalogModels, existingModels }) {
  const createOnly = isGenericCatalogProvider(provider?.provider_type);
  const existingCodes = new Set(
    (existingModels || []).map((model) => normalizedModelCode(model.model || model.name)),
  );
  const seen = new Set();
  const choices = [];
  let skipped = 0;

  for (const model of catalogModels || []) {
    const modelCode = String(model.provider_model_id || "").trim();
    const normalized = normalizedModelCode(modelCode);
    if (!normalized || seen.has(normalized)) {
      skipped += 1;
      continue;
    }
    seen.add(normalized);
    const added = !createOnly && existingCodes.has(normalized);
    if (added) skipped += 1;
    choices.push({
      id: normalized,
      model: modelCode,
      label: String(model.model_label || modelCode),
      added,
      payload: {
        provider_id: provider.id,
        name: modelCode,
        description: null,
        model: modelCode,
        input_modalities: Array.isArray(model.input_modalities) && model.input_modalities.length
          ? model.input_modalities
          : ["text"],
        context_window_tokens: model.context_window_tokens,
        max_output_tokens: model.max_output_tokens,
        tags: Array.isArray(model.tags) ? model.tags : [],
      },
    });
  }

  return { choices, skipped };
}

export function providerCatalogSummary({ created, skipped, failed }) {
  if (!created && !failed && skipped) return `目录模型均已存在，已跳过 ${skipped} 个`;
  const parts = [`新增 ${created} 个`];
  if (skipped) parts.push(`跳过 ${skipped} 个`);
  if (failed) parts.push(`失败 ${failed} 个`);
  return parts.join("，");
}
