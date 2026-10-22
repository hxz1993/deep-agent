import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";
import * as OutputSchema from "../../../console/output-schema-fields.js";
import { renderStructuredOutput, structuredOutputSections, serializeStructuredOutput, readOutputViewPreference, writeOutputViewPreference } from "../../../console/structured-output.js";
import { assistantActionsFromSse, assistantActionsFromHistoryItems, applyAssistantPresentationActions } from "../../../console/assistant-presentation.js";
import { projectConversationItems } from "../../../console/conversation-items.js";
import { buildTrajectory, trajectoryStats } from "../../../console/trajectory.js";

const editorSource = await readFile(new URL("../../../console/configuration/agent-output.js", import.meta.url), "utf8");
const agentSource = await readFile(new URL("../../../console/configuration/agents.js", import.meta.url), "utf8");
function editor() {
  const window = { DeepAgentOutputSchema: OutputSchema };
  vm.runInNewContext(editorSource, { window, document: { addEventListener() {} } });
  return window.DeepAgentOutputDraft;
}
const native = value => JSON.parse(JSON.stringify(value));
const config = schema => ({ structured_output_enabled: true, allow_text_output: false, output_config: { name: "lease_result", description: "合同及澄清可以组合返回", json_schema: schema } });

test("output draft is disabled and omits a never-created definition", () => {
  const { OutputDraft } = editor();
  assert.deepEqual(native(new OutputDraft().payload()), { structured_output_enabled: false, allow_text_output: false });
  const draft = new OutputDraft(config({ type: "object", properties: {}, additionalProperties: false }));
  assert.deepEqual(native(draft.payload().output_config.json_schema.properties), {});
  assert.equal(draft.payload().output_config.json_schema.minProperties, undefined);
});

test("card edits retain unknown schema keywords, internal required, and optional omission", () => {
  const { OutputDraft } = editor();
  const schema = { type: "object", properties: { text: { type: "string", title: "正文" }, contract: { type: "object", required: ["id"], properties: { id: { type: "string" } } } }, additionalProperties: false, $defs: { currency: { enum: ["CNY", "USD"] } }, "x-client-hint": "keep" };
  const draft = new OutputDraft(config(schema));
  draft.required("text", true);
  assert.deepEqual(native(draft.schema().required), ["text"]);
  draft.required("text", false);
  assert.equal(draft.schema().required, undefined);
  draft.detail("contract", { key: "contract_patch", text: JSON.stringify(schema.properties.contract) });
  const result = native(draft.payload().output_config.json_schema);
  assert.deepEqual(result.$defs, schema.$defs);
  assert.equal(result["x-client-hint"], "keep");
  assert.deepEqual(result.properties.contract_patch.required, ["id"]);
  assert.equal(result.properties.contract, undefined);
});

test("invalid native and detail input remains in the draft and blocks whole-page save even when disabled", () => {
  const { OutputDraft } = editor();
  const draft = new OutputDraft(config({ type: "object", properties: { a: { type: "string" } } }));
  const invalid = '{ "type": ';
  draft.setSchemaText(invalid);
  assert.throws(() => draft.payload(), /JSON Schema 无效/);
  assert.equal(draft.schemaText, invalid);
  draft.setSchemaText('{"type":"object","properties":{"a":{"type":"string"}}}');
  assert.throws(() => draft.detail("a", { key: "a", text: invalid }), /无效/);
  draft.enabled = false;
  assert.throws(() => draft.payload(), /结构详情/);
  assert.equal(draft.invalidDetails.get("a").text, invalid);
  draft.detail("a", { key: "renamed", text: '{"type":"integer","minimum":0}' });
  assert.equal(draft.payload().structured_output_enabled, false);
  assert.equal(draft.payload().output_config.json_schema.properties.renamed.minimum, 0);
});

test("native arrays and composition remain native rather than losing schema in the card view", () => {
  const { OutputDraft, canUseCards } = editor();
  for (const schema of [{ type: "array", items: { type: "string" }, minItems: 0 }, { type: "object", oneOf: [{ required: ["a"] }, { required: ["b"] }] }]) {
    assert.equal(canUseCards(schema), false);
    assert.deepEqual(native(new OutputDraft(config(schema)).payload().output_config.json_schema), schema);
  }
});

test("deleting a field removes only its required member and preserves the other definition", () => {
  const { OutputDraft } = editor();
  const draft = new OutputDraft(config({ type: "object", required: ["a", "b"], properties: { a: { type: "string" }, b: { type: "boolean" } }, additionalProperties: false }));
  draft.remove("a");
  assert.deepEqual(native(draft.schema().required), ["b"]);
  assert.deepEqual(native(draft.schema().properties), { b: { type: "boolean" } });
  assert.equal(draft.schema().additionalProperties, false);
});

test("JSON and grouped views preserve false, zero, null, empty lists, empty objects and literal strings", () => {
  const value = { text: '<script>alert(1)</script>\n正文', no: false, count: 0, empty: null, rows: [], map: {}, string: '{"looks":"json"}' };
  assert.deepEqual(JSON.parse(serializeStructuredOutput(value)), value);
  const sections = structuredOutputSections(value);
  assert.equal(sections[0].prose, true);
  assert.equal(sections[1].output, false);
  assert.equal(structuredOutputSections([]), null);
  assert.equal(structuredOutputSections(null), null);
  const html = renderStructuredOutput({ name: "lease_result", output: value, mode: "grouped" });
  assert.match(html, /&lt;script&gt;/);
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /false/);
  assert.match(html, /null/);
  assert.match(html, /完整 JSON/);
  assert.match(html, /按结构分组/);
  for (const output of [false, 0, null, "", [], {}]) assert.match(renderStructuredOutput({ name: "result", output }), /structured-output-json/);
});

test("long generic outputs fold at 15 lines while the serialized result stays complete", () => {
  const value = { rows: Array.from({ length: 18 }, (_, index) => index) };
  const text = serializeStructuredOutput(value);
  const html = renderStructuredOutput({ name: "lease_result", output: value });
  assert.match(html, /structured-output-json collapsed/);
  assert.match(html, /展开全部/);
  assert.match(html, /17/);
  assert.match(renderStructuredOutput({ name: "lease_result", output: value, expanded: true }), /aria-expanded="true"/);
  assert.deepEqual(JSON.parse(text), value);
});

test("only accepted output frames create ordinary reply actions, including null", () => {
  const frame = { output_id: "out-1", name: "lease_result", status: "completed", accepted: true, output: null };
  assert.equal(assistantActionsFromSse("output", frame)[0].output, null);
  assert.deepEqual(assistantActionsFromSse("output", { ...frame, accepted: false, status: "error" }), []);
  const missing = { ...frame }; delete missing.output;
  assert.deepEqual(assistantActionsFromSse("output", missing), []);
  const final = assistantActionsFromSse("final", { output: null, output_info: { name: "lease_result", output_id: "out-1" } });
  assert.equal(final[0].outputId, "out-1");
  assert.equal(final[1].output, undefined);
  const calls = [];
  applyAssistantPresentationActions({ setStructuredOutput: value => calls.push(value), complete: () => {} }, final);
  assert.deepEqual(calls, [{ name: "lease_result", output: null, id: "out-1" }]);
});

test("history output IDs become the same reply action identity and do not create normal tool cards", () => {
  const item = { kind: "structured_output", item_id: "out-1", status: "completed", name: "lease_result", output: { ok: true }, order: { sequence: 3, part_offset: 0 } };
  const actions = assistantActionsFromHistoryItems([item]);
  assert.equal(actions[0].type, "structured_output");
  assert.equal(actions[0].outputId, "out-1");
  assert.equal(actions.some(action => action.type === "tool_call"), false);
  assert.equal(projectConversationItems([item])[0].actions[0].outputId, "out-1");
});

const source = (id, sequence, kind, parts, metadata = {}) => ({ event_id: id, sequence, event_type: kind, turn_id: "turn-1", run_id: "run-1", status: "completed", payload: { source: { message_ordinal: sequence, part_index: 0 }, message: { kind: kind === "assistant/message" ? "response" : "request", parts, metadata } } });
const attempt = (id, sequence, sourceId, ordinal, accepted, output) => ({ event_id: id, sequence, event_type: "output/attempt", turn_id: "turn-1", run_id: "run-1", status: accepted ? "completed" : "failed", payload: { name: "lease_result", tool_call_id: `call-${ordinal}`, attempt: accepted ? 2 : 1, accepted, source: { event_ids: [sourceId], message_ordinal: ordinal, part_index: 0 }, ...(accepted ? { output } : { errors: [{ path: "/contract/id", message: "required" }] }) } });

test("failed and successful output attempts share an output type, keep their own candidate, and are excluded from tool counts", () => {
  const bad = source("bad-response", 2, "assistant/message", [{ part_kind: "tool-call", tool_name: "lease_result", tool_call_id: "call-2", args: { contract: {} } }]);
  const good = source("good-response", 4, "assistant/message", [{ part_kind: "tool-call", tool_name: "lease_result", tool_call_id: "call-4", args: { contract: { id: "42" } } }]);
  const rows = buildTrajectory({ items: [bad, attempt("bad", 3, "bad-response", 2, false), good, attempt("good", 5, "good-response", 4, true, { contract: { id: "42" } })] });
  const outputs = rows.filter(row => row.kind === "output");
  assert.equal(outputs.length, 2);
  assert.equal(outputs[0].status, "failed");
  assert.equal(outputs[1].status, "completed");
  assert.deepEqual(outputs[0].candidate, { contract: {} });
  assert.deepEqual(outputs[1].candidate, { contract: { id: "42" } });
  assert.equal(rows.some(row => row.kind === "tool"), false);
  assert.equal(trajectoryStats(rows).calls, 0);
});

test("mixed-response calls remain visible as not dispatched and do not inflate execution counts", () => {
  const response = source("mixed", 2, "assistant/message", [{ part_kind: "tool-call", tool_name: "lookup", tool_call_id: "lookup-1", args: {} }], { deepagent_dispatch: { rejected: true, not_dispatched_part_indices: [0] } });
  const rows = buildTrajectory({ items: [response] });
  const tool = rows.find(row => row.kind === "tool");
  assert.equal(tool.dispatched, false);
  assert.equal(tool.status, "failed");
  assert.equal(trajectoryStats(rows).calls, 0);
});

test("updater uses username, then trusted ID, and has no fabricated historical creator", () => {
  const window = { DeepAgentOutputSchema: OutputSchema };
  vm.runInNewContext(agentSource, { window, document: { addEventListener() {} } });
  assert.equal(window.__AgentAdminUI.agentUpdaterName({ updated_by: "id-42", updated_by_user: { username: "alice" } }), "alice");
  assert.equal(window.__AgentAdminUI.agentUpdaterName({ updated_by: "id-42" }), "id-42");
  assert.equal(window.__AgentAdminUI.agentUpdaterName({}), "—");
});

test("SDK output retries without a tool name pair with the actual call and do not create normal tools", () => {
  const bad = source("bad-response", 2, "assistant/message", [{ part_kind: "tool-call", tool_name: "lease_result", tool_call_id: "call-2", args: { contract: {} } }]);
  const retry = source("retry", 4, "model/request", [{ part_kind: "retry-prompt", tool_name: null, tool_call_id: "call-2", content: "required" }]);
  const rows = buildTrajectory({ items: [bad, attempt("bad", 3, "bad-response", 2, false), retry] });
  assert.equal(rows.filter(row => row.kind === "tool").length, 0);
  assert.equal(trajectoryStats(rows).calls, 0);
});

test("global output correction prompts with SDK-generated IDs are not executed tool calls", () => {
  const bad = source("bad-response", 2, "assistant/message", [{ part_kind: "tool-call", tool_name: "lease_result", tool_call_id: "call-2", args: { contract: {} } }]);
  const retry = source("retry", 4, "tool/results", [{ part_kind: "retry-prompt", tool_name: null, tool_call_id: "sdk-generated", content: "required" }], { deepagent_output_budget: { remaining_corrections: 1 } });
  const rows = buildTrajectory({ items: [bad, attempt("bad", 3, "bad-response", 2, false), retry] });
  assert.equal(rows.filter(row => row.kind === "tool").length, 0);
  assert.equal(trajectoryStats(rows).calls, 0);
});

test("Schema property names and updater names cannot escape HTML attributes", () => {
  const { escapeOutputHtml } = editor();
  const attack = 'a" onclick="alert(1)<img src=x onerror=alert(2)>\'';
  const escaped = escapeOutputHtml(attack);
  assert.equal(escaped.includes('"'), false);
  assert.equal(escaped.includes("<"), false);
  assert.match(escaped, /&quot;/);
  assert.match(escaped, /&#39;/);
  const window = { DeepAgentOutputSchema: OutputSchema };
  vm.runInNewContext(agentSource, { window, document: { addEventListener() {} }, escapeHtml: value => String(value).replaceAll("<", "&lt;").replaceAll(">", "&gt;"), formatDate: () => "now" });
  const html = window.__AgentAdminUI.renderAgentUpdater({ updated_by_user: { username: attack } });
  assert.doesNotMatch(html, /title="[^>]*" onclick="/);
  assert.match(html, /&quot;/);
});

test("prototype-like JSON keys remain own data through card mutations without changing prototypes", () => {
  const { OutputDraft } = editor();
  const schema = JSON.parse('{"type":"object","properties":{"__proto__":{"type":"string"},"constructor":{"type":"object"},"a":{"type":"number"}}}');
  const draft = new OutputDraft(config(schema));
  draft.detail("a", { key: "prototype", text: '{"type":"number"}' });
  draft.required("__proto__", true);
  const result = native(draft.payload().output_config.json_schema);
  assert.equal(Object.hasOwn(result.properties, "__proto__"), true);
  assert.deepEqual(result.properties.__proto__, { type: "string" });
  assert.equal(Object.hasOwn(result.properties, "constructor"), true);
  draft.remove("__proto__");
  assert.equal(Object.hasOwn(draft.schema().properties, "__proto__"), false);
  assert.equal({}.polluted, undefined);
});

test("display preference stores only the mode and restores across output identities and refresh", () => {
  const values = new Map();
  const storage = { getItem: key => values.get(key), setItem: (key,value) => values.set(key,value) };
  assert.equal(readOutputViewPreference(storage), "full");
  writeOutputViewPreference(storage, "grouped");
  assert.equal(readOutputViewPreference(storage), "grouped");
  assert.deepEqual([...values], [["da_console_output_view_v1", "grouped"]]);
  writeOutputViewPreference(storage, "business-value");
  assert.equal(values.size, 1);
  assert.equal(readOutputViewPreference({ getItem() { throw new Error("denied"); } }), "full");
  assert.doesNotThrow(() => writeOutputViewPreference({ setItem() { throw new Error("denied"); } }, "full"));
});
