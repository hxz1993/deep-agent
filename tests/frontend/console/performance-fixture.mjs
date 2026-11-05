// Deterministic, de-identified rendering load. No customer text or credentials.
export const PERFORMANCE_LOAD = Object.freeze({
  turns: 2, responses: 17, tools: 31, thinkingChars: 61850,
  toolResultChars: 136111, answerChars: 2567, chunkChars: 8,
});

function sized(seed, length) {
  return seed.repeat(Math.ceil(length / seed.length)).slice(0, length);
}

export function createPerformanceFixture() {
  const frames = [], turns = [], expectedCalls = [];
  let remainingThinking = PERFORMANCE_LOAD.thinkingChars;
  let remainingResults = PERFORMANCE_LOAD.toolResultChars;
  let toolIndex = 0, responseIndex = 0;
  const chunks = (event, text, runId) => {
    for (let index = 0; index < text.length; index += PERFORMANCE_LOAD.chunkChars) {
      frames.push({ event, data: { run_id: runId, delta: text.slice(index, index + PERFORMANCE_LOAD.chunkChars) } });
    }
  };
  for (let turn = 0; turn < 2; turn += 1) {
    const runId = `replay-run-${turn + 1}`;
    const spec = { runId, thinking: [], calls: [], answer: sized(`核验第${turn + 1}轮：**结果完整**，保留来源与不确定项。\n\n`, turn === 0 ? 360 : 2207) };
    frames.push({ event: "replay_user", data: { turn, content: `脱敏回放第 ${turn + 1} 轮`, run_id: runId } });
    frames.push({ event: "session", data: { run_id: runId, session_id: "console-performance-replay" } });
    const responses = turn === 0 ? 3 : 14;
    for (let response = 0; response < responses; response += 1) {
      const left = 17 - responseIndex;
      const length = responseIndex === 0 ? 8719 : Math.ceil(remainingThinking / left);
      const thinking = sized(`正在核验第 ${responseIndex + 1} 次请求：读取既有证据、比较范围、保留物理页码；不补造缺失内容。\n`, length);
      remainingThinking -= thinking.length;
      spec.thinking.push(thinking);
      chunks("thinking", thinking, runId);
      const finalResponse = response === responses - 1;
      if (finalResponse) chunks("text", spec.answer, runId);
      else {
        const calls = turn === 1 && response === responses - 2 ? 3 : 2;
        for (let n = 0; n < calls; n += 1) {
          toolIndex += 1;
          const id = `replay-call-${toolIndex}`;
          const resultLength = toolIndex === 1 ? 22361 : Math.ceil(remainingResults / (32 - toolIndex));
          const content = sized(`行 ${toolIndex} | 项目 A | 预算 123.45 | 来源 第 ${toolIndex} 页\n`, resultLength);
          remainingResults -= content.length;
          const result = toolIndex % 2 ? { rows: Array.from({ length: 24 }, (_, index) => ({ index, amount: index * 1.25 })), text: content } : content;
          const artifacts = toolIndex === 1 ? [
            { kind: "sandbox_output", file_id: "local_replay_json", file_name: "replay-result.json", mime: "application/json", byte_size: 27 },
            { kind: "sandbox_output", file_id: "local_replay_txt", file_name: "replay-result.txt", mime: "text/plain", byte_size: 22 },
            { kind: "sandbox_output", file_id: "local_replay_png", file_name: "replay-image.png", mime: "image/png", byte_size: 68 },
          ] : [];
          const call = { tool: "read_file", tool_call_id: id, args: { resource: { type: "attachment", file_id: `local_input_${toolIndex}` }, offset: toolIndex, limit: 200 }, run_id: runId };
          const returned = { tool: "read_file", tool_call_id: id, result, artifacts, run_id: runId, status: "completed" };
          frames.push({ event: "tool_call", data: call }, { event: "tool_result", data: returned });
          spec.calls.push({ call, returned });
          expectedCalls.push(id);
        }
      }
      responseIndex += 1;
    }
    frames.push({ event: "final", data: { output: spec.answer, status: "completed", run_id: runId } });
    turns.push(spec);
  }
  return { frames, turns, expectedCalls, load: PERFORMANCE_LOAD };
}

export function fixtureHistory(fixture = createPerformanceFixture()) {
  const items = [];
  let sequence = 0;
  const add = (event_type, payload, runId) => items.push({
    event_id: `replay-event-${++sequence}`, event_type, sequence, payload,
    run_id: runId, turn_id: runId, created_at: "2026-11-05T00:00:00+00:00", status: "completed",
  });
  for (const turn of fixture.turns) {
    add("user/message", { message: { kind: "request", parts: [{ part_kind: "user-prompt", content: "脱敏核验" }] } }, turn.runId);
    if (items.length === 1) add("session/title", { title: "Console 脱敏性能核验" }, turn.runId);
    let callIndex = 0;
    turn.thinking.forEach((thinking, index) => {
      const parts = [{ part_kind: "thinking", content: thinking }];
      const last = index === turn.thinking.length - 1;
      const calls = last ? [] : turn.calls.slice(callIndex, callIndex + (index === turn.thinking.length - 2 && turn.calls.length === 27 ? 3 : 2));
      callIndex += calls.length;
      for (const { call } of calls) parts.push({ part_kind: "tool-call", tool_name: call.tool, tool_call_id: call.tool_call_id, args: call.args });
      if (last) parts.push({ part_kind: "text", content: turn.answer });
      add("assistant/message", { message: { kind: "response", parts } }, turn.runId);
      for (const { returned } of calls) add("tool/results", { message: { kind: "request", parts: [{ part_kind: "tool-return", tool_name: returned.tool, tool_call_id: returned.tool_call_id, content: returned.result, outcome: "success", metadata: { artifacts: returned.artifacts } }] } }, turn.runId);
    });
  }
  return { session_id: "console-performance-replay", items };
}

export function fixtureConversation(fixture = createPerformanceFixture()) {
  const items = [];
  let sequence = 0;
  const add = item => items.push({ item_id: `item-${++sequence}`, created_at: "2026-11-05T00:00:00Z", order: { sequence }, ...item });
  for (const turn of fixture.turns) {
    add({ kind: "user_message", text: "脱敏核验" });
    let callIndex = 0;
    turn.thinking.forEach((thinking, index) => {
      const last = index === turn.thinking.length - 1;
      add({ kind: "assistant_message", thinking, text: last ? turn.answer : "", phase: last ? "final" : "process", status: "completed" });
      if (last) return;
      const count = index === turn.thinking.length - 2 && turn.calls.length === 27 ? 3 : 2;
      for (const { call, returned } of turn.calls.slice(callIndex, callIndex + count)) {
        add({ kind: "tool", tool_name: call.tool, call_id: call.tool_call_id, input: call.args, output: returned.result, artifacts: returned.artifacts, status: "completed" });
      }
      callIndex += count;
    });
  }
  return items;
}
