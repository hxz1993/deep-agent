export const PROVIDER_CATALOG_IDS = Object.freeze({
  deepseek: "deepseek",
  moonshotai: "moonshotai",
  alibaba: "alibaba",
  zai: "zhipuai",
  openai: "openai",
  anthropic: "anthropic",
  google: "google",
});

export function catalogProviderIdForType(providerType) {
  return PROVIDER_CATALOG_IDS[String(providerType || "").trim().toLowerCase()] || null;
}

function normalizedModelCode(value) {
  return String(value || "").trim().toLowerCase();
}

export function buildProviderCatalogCreatePlan({ provider, catalogModels, existingModels }) {
  const existingCodes = new Set(
    (existingModels || []).map((model) => normalizedModelCode(model.model || model.name)),
  );
  const seen = new Set(existingCodes);
  const payloads = [];
  let skipped = 0;

  for (const model of catalogModels || []) {
    const modelCode = String(model.provider_model_id || "").trim();
    const normalized = normalizedModelCode(modelCode);
    if (!normalized || seen.has(normalized)) {
      skipped += 1;
      continue;
    }
    seen.add(normalized);
    payloads.push({
      provider_id: provider.id,
      name: modelCode,
      description: null,
      model: modelCode,
      enabled: true,
      input_modalities: Array.isArray(model.input_modalities) && model.input_modalities.length
        ? model.input_modalities
        : ["text"],
      context_window_tokens: model.context_window_tokens,
      max_output_tokens: model.max_output_tokens,
      tags: Array.isArray(model.tags) ? model.tags : [],
    });
  }

  return { payloads, skipped };
}

export function providerCatalogSummary({ created, skipped, failed }) {
  if (!created && !failed && skipped) return `目录模型均已存在，已跳过 ${skipped} 个`;
  const parts = [`新增 ${created} 个`];
  if (skipped) parts.push(`跳过 ${skipped} 个`);
  if (failed) parts.push(`失败 ${failed} 个`);
  return parts.join("，");
}
