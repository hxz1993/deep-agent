import assert from 'node:assert/strict';
import test from 'node:test';
import * as presentation from '../../../frontend/console/assistant-presentation.js';
import { buildTrajectory, createTrajectoryController } from '../../../frontend/console/trajectory.js';

for (const [outcome, expected, label] of [['failed', 'failed', '失败'], ['interrupted', 'interrupted', '已中断'], ['success', 'completed', '完成']]) {
  test(`SSE ${outcome} keeps outcome and uses the correct visible tool status`, () => {
    const payload = { tool: 'run_code', tool_call_id: 'call', outcome, result: 'safe result' };
    const [action] = presentation.assistantActionsFromSse('tool_result', payload);
    assert.equal(action.result.outcome, outcome);
    const status = presentation.toolResultPresentation(action.result);
    assert.equal(status.status, expected);
    assert.equal(status.label, label);
  });
  test(`persisted trajectory ${outcome} is not misrepresented as success`, () => {
    const events = buildTrajectory({ items: [{ event_id: 'result', sequence: 1, run_id: 'run', event_type: 'tool-results/message', payload: {
      message: { kind: 'request', parts: [{ part_kind: 'tool-return', tool_name: 'run_code', tool_call_id: 'call', content: 'safe result', outcome,
        metadata: { code_mode: true, nested_calls: [{ tool_name: 'safe_tool', status: 'failed' }] } }] },
    } }] });
    const tool = events.find((event) => event.tool_call_id === 'call');
    assert.equal(tool.status, expected);
    assert.equal(tool.result.metadata.code_mode, true);
    assert.equal(tool.result.metadata.nested_calls[0].tool_name, 'safe_tool');
  });
}

test('live PTC trajectory retains failed/interrupted outcome and bounded audit metadata', () => {
  const previousDocument = globalThis.document;
  const previousWindow = globalThis.window;
  const element = () => ({ value: '', classList: { toggle() {}, contains: () => false }, addEventListener() {}, setAttribute() {} });
  const elements = new Map();
  globalThis.document = { getElementById(id) { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); }, querySelector: () => null };
  globalThis.window = { requestAnimationFrame() {} };
  try {
    const trajectory = createTrajectoryController({ state: {} });
    trajectory.ingest({ event: 'session', data: { run_id: 'run' } });
    for (const outcome of ['failed', 'interrupted']) {
      trajectory.ingest({ event: 'tool_call', data: { tool: 'run_code', tool_call_id: outcome, args: { code: 'pass' } } });
      trajectory.ingest({ event: 'tool_result', data: { tool: 'run_code', tool_call_id: outcome, outcome, result: 'safe', metadata: { code_mode: true, nested_calls: [{ tool_name: 'safe_tool' }] } } });
      const event = trajectory.events().find((item) => item.tool_call_id === outcome);
      assert.equal(event.status, outcome);
      assert.equal(event.result.outcome, outcome);
      assert.equal(event.result.metadata.nested_calls[0].tool_name, 'safe_tool');
    }
    trajectory.ingest({ event: 'tool_result', data: { tool: 'run_code', tool_call_id: 'orphan', outcome: 'failed', result: 'safe' } });
    assert.equal(trajectory.events().find((item) => item.tool_call_id === 'orphan').status, 'failed');
  } finally {
    globalThis.document = previousDocument;
    globalThis.window = previousWindow;
  }
});
