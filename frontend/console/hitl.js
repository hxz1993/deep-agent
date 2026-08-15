function text(value) {
  return String(value ?? "").trim();
}

export function normalizeOption(option) {
  if (option && typeof option === "object" && !Array.isArray(option)) {
    const value = text(option.value || option.id || option.name || option.label);
    return {
      value,
      label: text(option.label || option.name || value),
      description: text(option.description),
    };
  }
  const value = text(option);
  return { value, label: value, description: "" };
}

export function buildChoiceResult(selectedValues, customInput) {
  const selected = Array.from(selectedValues || [])
    .map(text)
    .filter(Boolean);
  const custom = text(customInput);
  if (!selected.length && !custom) {
    return { ok: false, error: "请至少选择一个候选项或填写其他内容。" };
  }
  return {
    ok: true,
    result: {
      selected,
      custom_input: custom || null,
    },
  };
}

export function normalizeStructuredFields(fields) {
  if (!Array.isArray(fields)) {
    return { ok: false, error: "fields 必须是数组", fields: [] };
  }
  const normalized = [];
  for (const item of fields) {
    if (!item || typeof item !== "object" || Array.isArray(item)) {
      return { ok: false, error: "fields 中的每一项都必须是对象", fields: [] };
    }
    const id = text(item.id || item.field || item.name);
    if (!id) return { ok: false, error: "表单字段缺少 id、field 或 name", fields: [] };
    const options = item.options == null ? [] : item.options;
    if (!Array.isArray(options)) {
      return { ok: false, error: `字段 ${id} 的 options 必须是数组`, fields: [] };
    }
    normalized.push({
      id,
      label: text(item.label || item.title || item.name || id),
      description: text(item.description),
      type: text(item.widget || item.type || "text").toLowerCase(),
      placeholder: text(item.placeholder),
      required: Boolean(item.required),
      defaultValue: item.default ?? item.value ?? "",
      options: options.map(normalizeOption).filter((option) => option.value),
    });
  }
  return { ok: true, fields: normalized };
}

export function validateUiToolCall(call) {
  const args = call?.args;
  if (!args || typeof args !== "object" || Array.isArray(args) || !Object.keys(args).length) {
    return { ok: false, pending: true, error: "工具参数尚未完成" };
  }
  const widget = text(call?.widget).toLowerCase();
  if (widget === "choice" && !Array.isArray(args.options)) {
    return { ok: false, pending: false, error: "options 必须是数组" };
  }
  if (["structured", "structured_form", "form"].includes(widget) || "fields" in args) {
    const result = normalizeStructuredFields(args.fields);
    return { ...result, pending: false };
  }
  return { ok: true, pending: false };
}
