import assert from "node:assert/strict";
import test from "node:test";

import { renderPtcExecution } from "../../../console/ptc-execution.js";

const event = (metadata) => ({ kind: "tool", label: "run_code", result: { metadata } });

test("Calls 上方分批展示彩色调用条，下方按页签查看当前步骤 JSON 与源码", () => {
  const steps = Array.from({ length: 25 }, (_, index) => ({
    call_id: `outer__${index + 1}`, tool_name: index === 0 ? "查询工具" : `工具${index}`,
    status: index === 0 ? "failed" : "completed", duration_ms: 2.3, start_offset_ms: index * 3,
    input: { state: "captured", value: { value: index } },
    output: { state: "captured", value: { ok: true } },
  }));
  const sample = event({ ptc_diagnostics: { version: 1,
    summary: { duration_ms: 100, external_coverage_ms: 45, call_count: 25, failed_count: 1 },
    steps, completeness: "complete",
  } });
  const initial = renderPtcExecution(sample);
  assert.match(initial, /总耗时/);
  assert.match(initial, /外部调用覆盖/);
  assert.match(initial, /加载更多 · 5/);
  assert.doesNotMatch(initial, /工具24/);
  assert.doesNotMatch(initial, /&quot;value&quot;: 0/);
  assert.match(initial, /trajectory-ptc-chart-row/);
  assert.match(initial, /trajectory-ptc-bar/);
  assert.match(initial, /trajectory-ptc-bar failed/);
  assert.doesNotMatch(initial, /trajectory-ptc-rail|trajectory-ptc-axis|查看代码/);
  assert.match(initial, /data-ptc-detail-tab="code"/);
  assert.match(initial, /&quot;ok&quot;<\/span>: <span class="trajectory-ptc-json-boolean">true/);
  const input = renderPtcExecution(sample, { expanded: 0, detailTab: "input" });
  assert.match(input, /&quot;value&quot;<\/span>: <span class="trajectory-ptc-json-number">0/);
  assert.doesNotMatch(input, /&quot;ok&quot;/);
  const code = renderPtcExecution(sample, { detailTab: "code", codeView: (step) => `<pre>${step.tool_name}</pre>` });
  assert.match(code, /<pre>查询工具<\/pre>/);
  assert.doesNotMatch(code, /&quot;ok&quot;/);
  const more = renderPtcExecution(sample, { visible: 40 });
  assert.match(more, /工具24/);
  assert.doesNotMatch(more, /加载更多/);
});

test("旧格式、缺失和未知版本有安全兜底", () => {
  assert.match(renderPtcExecution(event({ nested_calls: [{ tool_name: "legacy", status: "completed" }] })), /历史记录/);
  assert.match(renderPtcExecution(event({})), /未采集执行详情/);
  assert.match(renderPtcExecution(event({ ptc_diagnostics: { version: 99 } })), /版本暂不支持/);
  assert.doesNotMatch(renderPtcExecution(event({ ptc_diagnostics: { version: 1, steps: [{ tool_name: "<script>" }] } })), /<script>/);
});
