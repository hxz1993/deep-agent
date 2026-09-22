// Presentation only: token estimation and budget policy belong to the backend.
export function tokenValue(value) {
  if (value == null || value === "" || typeof value === "boolean") return null;
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 ? number : null;
}

const fallbackLabels = {
  usage_unavailable: "模型未返回可用的 Token 用量",
  anchor_unverified: "历史用量缺少可验证的基准信息",
  model_changed: "模型已切换",
  history_changed: "历史内容已压缩或发生变化",
  request_parameters_unavailable: "当前仅能读取历史，缺少完整请求参数",
  request_parameters_changed: "工具定义或请求参数已变化",
  instructions_changed: "当前指令已变化",
};

export function contextOccupancyPresentation(status = {}) {
  const fullRequest = status.estimate_scope === "full_model_request";
  const tokens = tokenValue(status.context_occupancy_tokens)
    ?? tokenValue(fullRequest ? status.estimated_request_tokens : status.estimated_history_tokens);
  const label = fullRequest ? "预计占用" : "历史占用";
  const scope = fullRequest ? "完整请求预算" : "仅历史估算，不含完整请求参数";
  const source = status.occupancy_source === "provider_usage_plus_estimate"
    ? "Provider Usage 基准＋增量估算"
    : "纯估算";
  const details = [scope, tokens == null ? "占用暂不可用" : source];
  if (status.usage_anchor_id) details.push(`基准响应：${status.usage_anchor_id}`);
  if (status.estimation_fallback_reason) details.push(`回退原因：${fallbackLabels[status.estimation_fallback_reason] || "基准暂不可复用"}（${status.estimation_fallback_reason}）`);
  if (status.unavailable_reason) details.push(status.unavailable_reason);
  return { tokens, label, detail: details.join("；") };
}
