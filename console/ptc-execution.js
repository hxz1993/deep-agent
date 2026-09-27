/** Compact execution details for run_code; legacy records remain readable. */
const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[char]));

function executionData(event) {
  const metadata = event?.result?.metadata || {};
  const diagnostic = metadata.ptc_diagnostics;
  if (diagnostic && typeof diagnostic === "object") {
    if (diagnostic.version !== 1) return { unsupported: true, steps: [] };
    return { ...diagnostic, steps: Array.isArray(diagnostic.steps) ? diagnostic.steps : [] };
  }
  if (Array.isArray(metadata.nested_calls)) return {
    legacy: true,
    steps: metadata.nested_calls.filter((step) => step && typeof step === "object" && step.tool_name).map((step, index) => ({
      call_id: step.call_id || `legacy-${index}`, tool_name: step.tool_name,
      status: step.status || "unknown", duration_ms: step.duration_ms,
      input: step.args === undefined ? null : { state: "captured", value: step.args },
      output: step.result === undefined ? null : { state: "captured", value: step.result },
    })),
  };
  return null;
}

function detailValue(record) {
  if (record == null) return `<span class="trajectory-ptc-missing">—</span>`;
  const label = { truncated: "已截断", redacted: "已脱敏", empty: "空结果", missing: "未采集" }[record.state];
  const value = record.value;
  if (value == null) return label ? `<small class="trajectory-ptc-state">${label}</small>` : `<span class="trajectory-ptc-missing">—</span>`;
  const json = JSON.stringify(value, null, 2);
  const pattern = /"(?:\\.|[^"\\])*"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|\b(?:true|false|null)\b/g;
  let cursor = 0;
  const pieces = [];
  for (const match of json.matchAll(pattern)) {
    pieces.push(escapeHtml(json.slice(cursor, match.index)));
    const token = match[0];
    const style = token.startsWith('"')
      ? /^\s*:/.test(json.slice(match.index + token.length)) ? "key" : "string"
      : token === "true" || token === "false" ? "boolean" : token === "null" ? "null" : "number";
    pieces.push(`<span class="trajectory-ptc-json-${style}">${escapeHtml(token)}</span>`);
    cursor = match.index + token.length;
  }
  pieces.push(escapeHtml(json.slice(cursor)));
  return `${label ? `<small class="trajectory-ptc-state">${label}</small>` : ""}<pre class="trajectory-ptc-json"><code>${pieces.join("")}</code></pre>`;
}

export function renderPtcExecution(event, { visible = 20, expanded = null, detailTab = "result", codeView = "" } = {}) {
  const data = executionData(event);
  if (!data) return `<p class="trajectory-summary-empty">此记录未采集执行详情。</p>`;
  if (data.unsupported) return `<p class="trajectory-summary-empty">此执行详情版本暂不支持。</p>`;
  const steps = data.steps;
  const stats = data.summary || {};
  const duration = (value) => value != null && Number.isFinite(Number(value)) ? `${Number(value).toLocaleString()} ms` : "—";
  const stateName = { completed: "成功", failed: "失败", denied: "拒绝", interrupted: "取消", unknown: "未知" };
  const metrics = data.legacy ? "" : `<div class="trajectory-ptc-stats">
    <span>总耗时 <strong>${duration(stats.duration_ms)}</strong></span>
    <span>外部调用覆盖 <strong>${duration(stats.external_coverage_ms)}</strong></span>
    <span>调用 <strong>${Number(stats.call_count) || 0}</strong></span>
    <span>失败 <strong>${Number(stats.failed_count) || 0}</strong></span>
  </div>`;
  const note = data.legacy ? `<p class="trajectory-ptc-note">历史记录：仅展示当时已保存的字段。</p>`
    : data.completeness === "incomplete" ? `<p class="trajectory-ptc-note">记录不完整，进程可能在收尾前中断。</p>` : "";
  if (!steps.length) return `<section class="trajectory-ptc">${metrics}${note}<div class="trajectory-ptc-empty"><p class="trajectory-summary-empty">没有内部工具调用。</p></div></section>`;
  const selectedIndex = Number.isInteger(expanded) && expanded >= 0 && expanded < Math.min(visible, steps.length) ? expanded : 0;
  const selected = steps[selectedIndex];
  const selectedStatus = String(selected.status || "unknown");
  const intervals = steps.filter((step) => step.start_offset_ms != null && step.duration_ms != null).map((step) => ({
    start: Number(step.start_offset_ms), duration: Number(step.duration_ms),
  })).filter((item) => Number.isFinite(item.start) && Number.isFinite(item.duration) && item.start >= 0 && item.duration >= 0);
  const firstStart = intervals.length ? Math.min(...intervals.map((item) => item.start)) : 0;
  const span = Math.max(1, ...intervals.map((item) => item.start + item.duration - firstStart));
  const rows = steps.slice(0, visible).map((step, index) => {
    const active = selectedIndex === index;
    const status = String(step.status || "unknown");
    const start = Number(step.start_offset_ms);
    const elapsed = Number(step.duration_ms);
    const hasInterval = step.start_offset_ms != null && step.duration_ms != null && Number.isFinite(start) && Number.isFinite(elapsed);
    const offset = hasInterval ? Math.max(0, Math.min(98, (start - firstStart) / span * 100)) : 0;
    const width = hasInterval ? Math.max(2, Math.min(100 - offset, elapsed / span * 100)) : 0;
    const color = ["blue", "violet", "green", "orange", "pink"][index % 5];
    return `<button type="button" class="trajectory-ptc-chart-row${active ? " is-active" : ""}" data-ptc-step="${index}" aria-pressed="${active}" title="${escapeHtml(step.tool_name || "—")}">
      <span class="trajectory-ptc-chart-name"><strong>${escapeHtml(step.tool_name || "—")}</strong></span>
      ${hasInterval ? `<span class="trajectory-ptc-chart-track"><i class="trajectory-ptc-bar ${status === "completed" ? color : escapeHtml(status)}" style="left:${offset.toFixed(2)}%;width:${width.toFixed(2)}%"></i></span>` : `<span class="trajectory-ptc-chart-track is-missing">—</span>`}
      <span class="trajectory-ptc-chart-duration">${duration(step.duration_ms)}</span>
    </button>`;
  }).join("");
  const tabs = [["input", "Input"], ["result", "Result"], ["code", "Code"], ...(selected.error ? [["error", "Error"]] : [])];
  const preferredTab = detailTab === "result" && !selected.output && selected.error ? "error" : detailTab;
  const activeDetailTab = tabs.some(([id]) => id === preferredTab) ? preferredTab : "result";
  const activeDetail = selected[activeDetailTab === "result" ? "output" : activeDetailTab];
  return `<section class="trajectory-ptc">${metrics}${note}
    <div class="trajectory-ptc-chart" aria-label="内部调用耗时">${rows}${steps.length > visible ? `<button type="button" class="trajectory-ptc-more" data-ptc-load-more>加载更多 · ${steps.length - visible}</button>` : ""}</div>
    <div class="trajectory-ptc-selected">
      <header class="trajectory-ptc-selected-head"><div><span class="trajectory-ptc-step-index"><i data-lucide="list-ordered" aria-hidden="true"></i>${selectedIndex + 1}/${steps.length}</span><strong title="${escapeHtml(selected.tool_name || "—")}">${escapeHtml(selected.tool_name || "—")}</strong></div><em class="${escapeHtml(selectedStatus)}">${escapeHtml(stateName[selectedStatus] || selectedStatus)}</em></header>
      <div class="trajectory-ptc-selected-meta"><span><i data-lucide="timer" aria-hidden="true"></i><strong>${duration(selected.duration_ms)}</strong></span><span>开始 ${selected.start_offset_ms == null ? "—" : `+${duration(selected.start_offset_ms)}`}</span><span class="trajectory-ptc-call-id" title="${escapeHtml(selected.call_id || "—")}">ID ${escapeHtml(selected.call_id || "—")}</span></div>
      <nav class="trajectory-ptc-json-tabs" role="tablist" aria-label="调用数据">${tabs.map(([id, label]) => `<button type="button" role="tab" aria-selected="${id === activeDetailTab}" class="${id === activeDetailTab ? "active" : ""}" data-ptc-detail-tab="${id}">${label}</button>`).join("")}</nav>
      <div class="trajectory-ptc-json-panel" role="tabpanel">${activeDetailTab === "code" ? (typeof codeView === "function" ? codeView(selected) : codeView) : detailValue(activeDetail)}</div>
    </div>
  </section>`;
}
