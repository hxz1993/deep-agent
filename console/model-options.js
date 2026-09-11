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
  if (model?.enabled === false) return false;
  if (typeof model?.selectable === "boolean") return model.selectable;
  const provider = modelProvider(model);
  return provider?.api_key_set !== false;
}

export function preserveCurrentModel(models, currentModel) {
  const available = Array.isArray(models) ? [...models] : [];
  if (!currentModel?.id || available.some((model) => model.id === currentModel.id)) return available;
  const disabled = currentModel.enabled === false;
  return [
    ...available,
    {
      ...currentModel,
      selectable: false,
      unavailable_reason: disabled ? "model_disabled" : "provider_invalid",
      provider: {
        id: `current-agent:${currentModel.id}`,
        name: "当前 Agent 模型",
        api_key_set: !disabled,
      },
    },
  ];
}

function unavailableSuffix(model) {
  const provider = modelProvider(model);
  const reason = model?.unavailable_reason;
  if (reason === "model_disabled" || model?.enabled === false) return "（模型已停用）";
  if (reason === "provider_unconfigured" || provider?.api_key_set === false) return "（供应商未配置密钥）";
  if (reason === "provider_invalid") return "（供应商配置无效）";
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
  const visibleModels = Array.isArray(models)
    ? models.filter((model) => isModelSelectable(model) || model.id === selectedId)
    : [];
  if (!visibleModels.length) {
    return `<option value="">${escapeOptionText(emptyLabel)}</option>`;
  }
  const groups = new Map();
  for (const model of visibleModels) {
    const provider = modelProvider(model);
    const key = provider?.id || model?.provider_id || "unassigned";
    if (!groups.has(key)) {
      groups.set(key, {
        label: provider?.name || "未关联供应商",
        models: [],
      });
    }
    groups.get(key).models.push(model);
  }
  return [...groups.values()].map((group) => {
    const options = group.models.map((model) => {
      const selected = model.id === selectedId ? " selected" : "";
      const suffix = unavailableSuffix(model);
      return `<option value="${escapeOptionText(model.id)}"${selected}>${escapeOptionText(modelOptionLabel(model))}${suffix}</option>`;
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
    preserveCurrentModel,
    renderGroupedModelOptions,
  };
}
