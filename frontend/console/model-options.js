function escapeOptionText(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

export function modelProvider(model) {
  return model?.provider || model?.llm_provider || null;
}

export function isModelSelectable(model) {
  if (typeof model?.selectable === "boolean") return model.selectable;
  const provider = modelProvider(model);
  return model?.enabled !== false
    && provider?.enabled !== false
    && provider?.api_key_set !== false;
}

function unavailableSuffix(model) {
  const provider = modelProvider(model);
  const reason = model?.unavailable_reason;
  if (reason === "model_disabled" || model?.enabled === false) return "（模型已停用）";
  if (reason === "provider_disabled" || provider?.enabled === false) return "（Provider 已停用）";
  if (reason === "provider_unconfigured" || provider?.api_key_set === false) return "（Provider 未配置密钥）";
  if (reason === "provider_invalid") return "（Provider 配置无效）";
  return "";
}

export function modelOptionLabel(model) {
  const name = String(model?.name || "").trim();
  const code = String(model?.model || "").trim();
  if (!name) return code;
  if (!code || name === code || name.includes(code) || code.includes(name)) return name;
  return `${name} — ${code}`;
}

export function firstSelectableModelId(models) {
  return (models || []).find(isModelSelectable)?.id || "";
}

export function renderGroupedModelOptions(models, { selectedId = "", emptyLabel = "无可用模型" } = {}) {
  if (!Array.isArray(models) || !models.length) {
    return `<option value="">${escapeOptionText(emptyLabel)}</option>`;
  }
  const groups = new Map();
  for (const model of models) {
    const provider = modelProvider(model);
    const key = provider?.id || model?.provider_id || "unassigned";
    if (!groups.has(key)) {
      groups.set(key, {
        label: provider?.name || "未关联 Provider",
        models: [],
      });
    }
    groups.get(key).models.push(model);
  }
  return [...groups.values()].map((group) => {
    const options = group.models.map((model) => {
      const selected = model.id === selectedId ? " selected" : "";
      const disabled = !isModelSelectable(model) && model.id !== selectedId ? " disabled" : "";
      const suffix = unavailableSuffix(model);
      return `<option value="${escapeOptionText(model.id)}"${selected}${disabled}>${escapeOptionText(modelOptionLabel(model))}${suffix}</option>`;
    }).join("");
    return `<optgroup label="${escapeOptionText(group.label)}">${options}</optgroup>`;
  }).join("");
}

if (typeof window !== "undefined") {
  window.DeepAgentModelOptions = {
    firstSelectableModelId,
    isModelSelectable,
    modelOptionLabel,
    modelProvider,
    renderGroupedModelOptions,
  };
}
