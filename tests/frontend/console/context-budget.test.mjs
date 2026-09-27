import assert from "node:assert/strict";
import test from "node:test";
import { contextOccupancyPresentation, tokenValue } from "../../../console/context-budget-presentation.js";
import { contextBudgetPercent, renderContextBudgetOverview, renderRunUsageOverview } from "../../../console/session-inspector.js";

const fullBudget = {
  estimate_scope: "full_model_request", context_window_tokens: 20000,
  context_occupancy_tokens: 12500, estimated_history_tokens: 4000,
  estimated_request_tokens: 16000, usable_request_tokens: 17000,
  predicted_output_tokens: 2000, safety_margin_tokens: 1000,
  occupancy_source: "provider_usage_plus_estimate", usage_anchor_id: "response-2",
};

test("Run totals are cumulative usage and never subtract from the context window", () => {
  // 10,000 + 1,000 then 12,000 + 500: 23,500 used, not 23,500 occupied.
  const html = renderRunUsageOverview({}, { input_tokens: 22000, output_tokens: 1500 }, fullBudget);
  assert.match(html, /累计使用<\/small><strong>23,500/);
  assert.match(html, /22,000 \/ 1,500/);
  assert.match(html, /width:93\.62%/);
  assert.match(html, /width:6\.38%/);
  assert.doesNotMatch(html, /预计剩余|上下文剩余|class="remaining"/);
  assert.equal(html, renderRunUsageOverview({}, { input_tokens: 22000, output_tokens: 1500 }, { context_window_tokens: 100 }));
});

test("context budget prefers backend occupancy and identifies the measured baseline", () => {
  const html = renderContextBudgetOverview(fullBudget);
  assert.match(html, /预计占用<\/small><strong>12,500/);
  assert.match(html, /剩余预算<\/small><strong>4,500/);
  assert.match(html, /tone-history" style="width:62\.50%"/);
  assert.match(html, /完整请求预算；Provider Usage 基准＋增量估算；基准响应：response-2/);
  assert.doesNotMatch(html, /<strong>4,000|<strong>16,000/);
});

test("old full request and history-only snapshots retain distinct fallback semantics", () => {
  const full = contextOccupancyPresentation({ ...fullBudget, context_occupancy_tokens: null, occupancy_source: "estimated", usage_anchor_id: null, estimation_fallback_reason: "model_changed" });
  assert.equal(full.tokens, 16000);
  assert.match(full.detail, /纯估算；回退原因：模型已切换（model_changed）/);
  const historical = contextOccupancyPresentation({ estimated_history_tokens: 3000, estimated_request_tokens: 8000 });
  assert.equal(historical.tokens, 3000);
  assert.equal(historical.label, "历史占用");
  assert.match(historical.detail, /仅历史估算，不含完整请求参数/);
});

test("unknown token counts remain unknown, including percentages and missing reservations", () => {
  for (const value of [null, undefined, "", NaN, Infinity, -1, false]) assert.equal(tokenValue(value), null);
  assert.equal(tokenValue(0), 0);
  assert.equal(contextBudgetPercent(null, 100), null);
  const html = renderContextBudgetOverview({ context_window_tokens: 20000, estimated_history_tokens: 5000 });
  assert.match(html, /剩余预算<\/small><strong>—/);
  assert.match(html, /输出预留 — Token/);
  assert.match(html, /剩余 — Token/);
  assert.doesNotMatch(html, /width:(?!0\.00)[\d.]+%/);
  const unknownRun = renderRunUsageOverview({ total_tokens: null }, {});
  assert.match(unknownRun, /累计使用<\/small><strong>—/);
  assert.match(unknownRun, /<strong>— \/ —/);
});

test("overflow clamps only the painted bar while preserving all token values", () => {
  const html = renderContextBudgetOverview({ ...fullBudget, context_occupancy_tokens: 25000 });
  assert.match(html, /预计占用<\/small><strong>25,000/);
  assert.match(html, /tone-history" style="width:100\.00%" title="预计占用 25,000 Token"/);
  assert.match(html, /tone-output" style="width:0\.00%" title="输出预留 2,000 Token"/);
  assert.match(html, /剩余预算<\/small><strong>0/);
});

test("baseline IDs and fallback explanations are escaped in inspector tooltips", () => {
  const html = renderContextBudgetOverview({ ...fullBudget, usage_anchor_id: '\"><img src=x>', estimation_fallback_reason: '<script>' });
  assert.doesNotMatch(html, /<img|<script/);
  assert.match(html, /&lt;script&gt;/);
});

test("all-zero SDK defaults are unknown and partially reported runs are labelled", () => {
  const unknown = renderRunUsageOverview({}, { input_tokens: 0, output_tokens: 0, total_tokens: 0, requests: 1 });
  assert.match(unknown, /累计使用<\/small><strong>—/);
  const partial = renderRunUsageOverview({ usage_partial: true }, { input_tokens: 100, output_tokens: 0 });
  assert.match(partial, /累计使用（部分）<\/small><strong>100/);
  assert.match(partial, /部分响应缺失/);
  assert.match(partial, /100 \/ 0/);
});

test("shared inspector shows persisted compression before/after and retains old history scope", async () => {
  const { renderSessionInspector } = await import("../../../console/session-inspector.js");
  const status = { ...fullBudget, compaction_event_id: "compact-1", compaction_estimated_tokens: 3000,
    compaction_estimated_tokens_before: 11000, compaction_estimated_tokens_after: 4500,
    compaction_estimate_scope: "full_model_request" };
  const html = renderSessionInspector({ contextStatus: status });
  assert.match(html, /压缩前<\/small><strong>11,000/);
  assert.match(html, /压缩后<\/small><strong>4,500/);
  assert.match(html, /同次完整请求/);
  const older = renderSessionInspector({ contextStatus: { compaction_estimated_tokens: 3000 } });
  assert.match(older, /压缩前<\/small><strong>—/);
  assert.match(older, /压缩后<\/small><strong>3,000/);
  assert.match(older, /历史内容估算，不含完整请求参数/);
});


test("a normal follow-up or failed attempt cannot overwrite the saved successful compression", async () => {
  const { renderSessionInspector } = await import("../../../console/session-inspector.js");
  const saved = {
    compaction_event_id: "saved-success", compaction_estimated_tokens_before: 19714,
    compaction_estimated_tokens_after: 7604, compaction_estimate_scope: "history_only",
    estimate_scope: "full_model_request",
  };
  for (const telemetry of [
    { triggered: false, estimated_tokens_before: 21895, estimated_tokens_after: 21895 },
    { triggered: true, target_met: false, failure: "SummaryUnavailable", estimated_tokens_before: 22000, estimated_tokens_after: 22000 },
    { triggered: true, summary_applied: true, compaction_event_saved: false, estimated_tokens_before: 22000, estimated_tokens_after: 22000 },
    { triggered: true, summary_applied: true, summary_failed: true, compaction_event_saved: true, estimated_tokens_before: 22000, estimated_tokens_after: 22000 },
    { triggered: true, summary_applied: true, failure: "PersistFailed", compaction_event_saved: true, estimated_tokens_before: 22000, estimated_tokens_after: 22000 },
  ]) {
    const html = renderSessionInspector({ contextStatus: { ...saved, last_compaction: telemetry } });
    assert.match(html, /压缩前<\/small><strong>19,714/);
    assert.match(html, /压缩后<\/small><strong>7,604/);
    assert.match(html, /历史内容估算，不含完整请求参数/);
    assert.doesNotMatch(html, /21,895|22,000/);
  }
  const triggered = renderSessionInspector({ contextStatus: { ...saved, last_compaction: {
    triggered: true, summary_applied: true, compaction_event_saved: true, target_met: false,
    estimated_tokens_before: 23000, estimated_tokens_after: 8000,
  } } });
  assert.match(triggered, /压缩前<\/small><strong>23,000/);
  assert.match(triggered, /压缩后<\/small><strong>8,000/);
  assert.match(triggered, /同次完整请求/);
  assert.doesNotMatch(triggered, /目标线/);
});
