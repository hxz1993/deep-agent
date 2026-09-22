import assert from "node:assert/strict";
import test from "node:test";
import { renderSessionInspector, formatCacheHitRatio } from "../../../console/session-inspector.js";
import { renderTrajectoryUsage, buildTrajectory } from "../../../console/trajectory.js";

const sessionCache = {
  response_count: 2, input_tokens: 16000, cache_read_tokens: 11000,
  cache_write_tokens: null, read_complete: true, write_complete: false,
  hit_ratio: 11000 / 16000,
};
const runCache = { ...sessionCache, response_count: 1, input_tokens: 6000, cache_read_tokens: 3000, hit_ratio: 0.5 };

test("overview and current show distinct weighted cache scopes without changing the context bar", () => {
  const overview = renderSessionInspector({ sessionCache, runCache });
  assert.match(overview, /<strong>缓存统计<\/strong>/);
  assert.match(overview, /累计命中率[\s\S]*?68\.8%/);
  assert.match(overview, /缓存读取[\s\S]*?>11,000<\/strong>/);
  assert.match(overview, /缓存写入[\s\S]*?>—<\/(?:span|strong)>/);
  assert.doesNotMatch(overview, /50\.0%/);
  const current = renderSessionInspector({ sessionCache, runCache, activeTab: "run" });
  assert.match(current, /本轮命中率[\s\S]*?50\.0%/);
  assert.match(current, /缓存读取[\s\S]*?>3,000<\/span>/);
  assert.doesNotMatch(current, /68\.8%/);
});

test("unknown, explicit zero and partial cache counts remain distinguishable", () => {
  assert.equal(formatCacheHitRatio({ ...sessionCache, hit_ratio: 0 }), "0.0%");
  assert.equal(formatCacheHitRatio({ ...sessionCache, read_complete: false }), "—");
  assert.equal(formatCacheHitRatio({ ...sessionCache, hit_ratio: Infinity }), "—");
  const partial = renderSessionInspector({ sessionCache: { ...sessionCache, read_complete: false, hit_ratio: null } });
  assert.match(partial, /累计命中率[\s\S]*?>—<\/strong>/);
  assert.match(partial, /11,000（部分）/);
  assert.match(partial, /部分请求缺失/);
  const current = renderSessionInspector({ activeTab: "run", usage: { cache_read_tokens: 0, cache_write_tokens: 0 } });
  assert.match(current, /缓存读取[\s\S]*?>—<\/span>/);
  assert.doesNotMatch(current, /0\.0%/);
});

test("trajectory shows existing Usage regardless of event kind without adding cache to input", () => {
  const event = { kind: "assistant", native: {
    kind: "response", usage: { input_tokens: 10000, output_tokens: 500, cache_read_tokens: 8000, cache_write_tokens: 0 },
    metadata: { deepagent_cache_usage: { version: 1, read_reported: true, write_reported: false } },
  } };
  const html = renderTrajectoryUsage(event);
  assert.match(html, /输入 10,000/);
  assert.match(html, /输出 500/);
  assert.match(html, /合计 10,500 Token/);
  assert.match(html, /缓存读取 8,000/);
  assert.match(html, /命中率<\/small><strong>80\.0%/);
  assert.match(html, /同一响应拆分的事件共用此统计/);
  assert.match(html, /缓存写入<\/small><strong>—/);
  assert.doesNotMatch(html, /18,500/);
  assert.equal(renderTrajectoryUsage({ ...event, kind: "tool" }), html);
  assert.match(renderTrajectoryUsage({ kind: "tool", native: {} }), /暂无供应商用量数据/);
});

test("trajectory preserves unknown cache and only displays confirmed zero", () => {
  const native = { kind: "response", usage: { input_tokens: 10, output_tokens: 1, cache_read_tokens: 0, cache_write_tokens: 0 } };
  assert.match(renderTrajectoryUsage({ kind: "assistant", native }), /缓存读取<\/small><strong>—/);
  native.metadata = { deepagent_cache_usage: { version: 1, read_reported: true, write_reported: false } };
  assert.match(renderTrajectoryUsage({ kind: "assistant", native }), /缓存读取 0/);
  assert.match(renderTrajectoryUsage({ kind: "assistant", native }), /命中率<\/small><strong>0\.0%/);
  assert.match(renderTrajectoryUsage({ kind: "assistant", native: { usage: { input_tokens: 0, output_tokens: 0 } } }), /暂无供应商用量数据/);
});

test("split persisted parts retain one request-level cache presentation", () => {
  const native = {
    kind: "response", usage: { input_tokens: 10000, output_tokens: 500, cache_read_tokens: 8000 },
    metadata: { deepagent_cache_usage: { version: 1, read_reported: true, write_reported: false } },
  };
  const rows = buildTrajectory({ items: ["thinking", "text"].map((part_kind, index) => ({
    event_id: `event-${index}`, sequence: index + 1, run_id: "run-1", event_type: "assistant/message",
    payload: { source: { message_ordinal: 0, part_index: index }, message: { ...native, parts: [{ part_kind, content: "content" }] } },
  })) });
  assert.equal(rows.length, 1);
  assert.match(renderTrajectoryUsage(rows[0]), /合计 10,500 Token/);
  assert.equal((renderTrajectoryUsage(rows[0]).match(/<small>缓存读取<\/small>/g) || []).length, 1);
});
