import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";
import * as OutputSchema from "../../../console/output-schema-fields.js";
import * as JsonInput from "../../../console/json-input.js";

const source = await readFile(new URL("../../../console/configuration/agent-output.js", import.meta.url), "utf8");
const native = value => JSON.parse(JSON.stringify(value));
function editor(config = {}) {
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) {
      const listeners = new Map();
      const classes = new Set();
      elements.set(id, {
        value: "", textContent: "", dataset: {}, parentElement: {}, scrollTop: 0, scrollLeft: 0,
        classList: { toggle(name, active) { active ? classes.add(name) : classes.delete(name); }, contains: name => classes.has(name) },
        addEventListener(name, callback) { listeners.set(name, callback); },
        dispatchEvent(event) { listeners.get(event.type)?.({ ...event, target: event.target || this }); },
        setAttribute(name, value) { this[name] = value; }, focus() { this.focused = true; },
        showModal() { this.open = true; }, close() { this.open = false; this.dispatchEvent({ type: "close" }); },
      });
    }
    return elements.get(id);
  }
  const window = { DeepAgentJsonInput: JsonInput, DeepAgentOutputSchema: OutputSchema };
  let dirty = 0;
  element("agentForm").addEventListener("input", () => dirty++);
  vm.runInNewContext(source, { window, Event, showToast() {}, document: { getElementById: element, addEventListener() {} } });
  const controller = window.DeepAgentOutputEditor;
  controller.init();
  controller.reset(config);
  const click = id => element(id).dispatchEvent({ type: "click" });
  const input = (id, value) => { element(id).value = value; element(id).dispatchEvent({ type: "input" }); };
  const edit = key => element("agentOutputFields").dispatchEvent({ type: "click", target: { closest: selector => selector === "[data-output-edit]" ? { dataset: { outputEdit: key } } : null } });
  return { controller, element, click, input, edit, dirty: () => dirty };
}
const config = {
  structured_output_enabled: true,
  allow_text_output: false,
  output_config: { name: "lease_result", json_schema: { type: "object", properties: { text: { type: "string", title: "正文" } }, additionalProperties: false } },
};

test("allow-text defaults off and appears only with the main output switch", () => {
  const ui = editor();
  assert.deepEqual(native(ui.controller.payload()), { structured_output_enabled: false, allow_text_output: false });
  assert.equal(ui.element("agentOutputAllowText").checked, false);
  assert.equal(ui.element("agentOutputAllowTextControl").classList.contains("hidden"), true);
  ui.element("agentOutputEnabled").checked = true;
  ui.element("agentOutputEnabled").dispatchEvent({ type: "change" });
  assert.equal(ui.element("agentOutputAllowTextControl").classList.contains("hidden"), false);
  assert.equal(ui.element("agentOutputAllowText").checked, false);
});

test("allow-text is an independent Agent flag; disabling output keeps it and the Schema draft", () => {
  const value = native(config);
  value.output_config.json_schema.required = ["text"];
  const ui = editor(value);
  const before = native(ui.controller.payload().output_config);
  ui.element("agentOutputAllowText").checked = true;
  ui.element("agentOutputAllowText").dispatchEvent({ type: "change" });
  assert.equal(ui.controller.payload().allow_text_output, true);
  assert.deepEqual(native(ui.controller.payload().output_config), before);
  assert.equal(ui.dirty(), 1);
  ui.element("agentOutputEnabled").checked = false;
  ui.element("agentOutputEnabled").dispatchEvent({ type: "change" });
  assert.equal(ui.element("agentOutputAllowTextControl").classList.contains("hidden"), true);
  assert.equal(ui.controller.payload().allow_text_output, true);
  assert.deepEqual(native(ui.controller.payload().output_config), before);
  ui.element("agentOutputEnabled").checked = true;
  ui.element("agentOutputEnabled").dispatchEvent({ type: "change" });
  assert.equal(ui.element("agentOutputAllowText").checked, true);
});

test("reset restores saved allow-text state and never carries it into another Agent", () => {
  const ui = editor({ ...native(config), allow_text_output: true });
  assert.equal(ui.element("agentOutputAllowText").checked, true);
  ui.element("agentOutputAllowText").checked = false;
  ui.element("agentOutputAllowText").dispatchEvent({ type: "change" });
  assert.equal(ui.controller.payload().allow_text_output, false);
  ui.controller.reset({ ...native(config), allow_text_output: true });
  assert.equal(ui.controller.payload().allow_text_output, true);
  ui.controller.reset(config);
  assert.equal(ui.controller.payload().allow_text_output, false);
  ui.controller.reset({ structured_output_enabled: false, allow_text_output: true });
  assert.deepEqual(native(ui.controller.payload()), { structured_output_enabled: false, allow_text_output: true });
  assert.equal(ui.element("agentOutputAllowTextControl").classList.contains("hidden"), true);
});

test("allow-text does not bypass invalid or missing structured configuration", () => {
  const ui = editor({ ...native(config), allow_text_output: true });
  ui.click("agentOutputSchemaTab");
  ui.input("agentOutputSchema", '{"type":');
  assert.throws(() => ui.controller.payload(), /JSON Schema 无效/);
  ui.input("agentOutputSchema", '{}');
  ui.input("agentOutputName", "");
  assert.throws(() => ui.controller.payload(), /请先填写结果名称/);
  assert.equal(ui.element("agentOutputAllowText").checked, true);
});

test("new structure cancellation and close leave the Agent draft clean", () => {
  for (const button of ["agentOutputDetailCancel", "agentOutputDetailClose"]) {
    const ui = editor(config);
    const before = native(ui.controller.payload());
    ui.click("agentOutputAdd");
    ui.input("agentOutputFieldKey", "discarded");
    ui.input("agentOutputFieldSchema", '{"type":');
    ui.click(button);
    assert.deepEqual(native(ui.controller.payload()), before);
    assert.equal(ui.dirty(), 0);
  }
});

test("confirm applies the structure locally; invalid JSON and duplicate keys stay in the dialog", () => {
  const ui = editor(config);
  ui.click("agentOutputAdd");
  assert.throws(() => ui.controller.payload(), /先确定或取消/);
  ui.input("agentOutputFieldSchema", '{"type":');
  ui.click("agentOutputDetailConfirm");
  assert.equal(ui.element("agentOutputDetail").open, true);
  assert.equal(ui.element("agentOutputFieldSchema").value, '{"type":');
  assert.match(ui.element("agentOutputDetailError").textContent, /JSON Schema 无效/);
  assert.equal(ui.dirty(), 0);
  ui.input("agentOutputFieldSchema", '{"type":"number","minimum":0}');
  ui.input("agentOutputFieldKey", "text");
  ui.click("agentOutputDetailConfirm");
  assert.match(ui.element("agentOutputDetailError").textContent, /已存在/);
  ui.input("agentOutputFieldKey", "amount");
  ui.click("agentOutputDetailConfirm");
  assert.equal(ui.element("agentOutputDetail").open, false);
  assert.equal(ui.controller.payload().output_config.json_schema.properties.amount.minimum, 0);
  assert.equal(ui.dirty(), 1);
});

test("existing detail edits can be discarded; confirmed rename retains required and other keywords", () => {
  const value = native(config);
  value.output_config.json_schema.required = ["text"];
  value.output_config.json_schema["x-client"] = "keep";
  const ui = editor(value);
  ui.edit("text");
  ui.input("agentOutputFieldSchema", '{"type":"boolean"}');
  ui.click("agentOutputDetailCancel");
  assert.equal(ui.controller.payload().output_config.json_schema.properties.text.type, "string");
  ui.edit("text");
  ui.input("agentOutputFieldKey", "renamed");
  ui.click("agentOutputDetailConfirm");
  const schema = native(ui.controller.payload().output_config.json_schema);
  assert.deepEqual(schema.required, ["renamed"]);
  assert.equal(schema["x-client"], "keep");
  assert.equal(schema.properties.renamed.title, "正文");
  assert.equal(schema.properties.text, undefined);
});

test("a missing output code is identified next to that input, without blaming a new structure", () => {
  const ui = editor();
  ui.element("agentOutputEnabled").checked = true;
  ui.element("agentOutputEnabled").dispatchEvent({ type: "change" });
  ui.click("agentOutputAdd");
  ui.click("agentOutputDetailConfirm");
  assert.equal(ui.element("agentOutputError").textContent, "");
  ui.click("agentOutputCheck");
  assert.match(ui.element("agentOutputNameError").textContent, /请先填写结果名称/);
  assert.equal(ui.element("agentOutputName")["aria-invalid"], "true");
  assert.equal(ui.element("agentOutputName").focused, true);
  assert.equal(ui.element("agentOutputError").textContent, "");
  ui.input("agentOutputName", "lease_result");
  assert.equal(ui.element("agentOutputNameError").textContent, "");
  assert.ok(ui.controller.payload().output_config.json_schema.properties.field_1);
});

test("native Schema typing retains invalid source and blocks saving until corrected", () => {
  const ui = editor(config);
  ui.click("agentOutputSchemaTab");
  ui.input("agentOutputSchema", '{"type":');
  ui.click("agentOutputSchemaTab");
  assert.throws(() => ui.controller.payload(), /JSON Schema 无效/);
  assert.equal(ui.element("agentOutputSchema").value, '{"type":');
  assert.equal(ui.element("agentOutputSchemaEditor").classList.contains("hidden"), false);
  assert.equal(ui.element("agentOutputSchemaLabel").textContent, "返回结构");
  assert.match(ui.element("agentOutputError").textContent, /JSON Schema 无效/);
  assert.match(ui.element("agentOutputSchemaHighlight").innerHTML, /json-key/);
  ui.input("agentOutputSchema", '{"type":"object","properties":{}}');
  ui.click("agentOutputSchemaTab");
  assert.equal(ui.element("agentOutputFields").classList.contains("hidden"), false);
  assert.equal(ui.element("agentOutputSchemaLabel").textContent, "查看 JSON");
  assert.deepEqual(native(ui.controller.payload().output_config.json_schema.properties), {});
});

test("one JSON action toggles views without changing the draft or marking it dirty", () => {
  const ui = editor({ ...native(config), allow_text_output: true });
  const before = native(ui.controller.payload());
  assert.equal(ui.element("agentOutputSchemaLabel").textContent, "查看 JSON");
  assert.equal(ui.element("agentOutputAdd").disabled, false);
  assert.equal(ui.element("agentOutputSchemaEditor").classList.contains("hidden"), true);
  ui.click("agentOutputSchemaTab");
  assert.equal(ui.element("agentOutputSchemaLabel").textContent, "返回结构");
  assert.equal(ui.element("agentOutputAdd").disabled, true);
  assert.equal(ui.element("agentOutputAdd").classList.contains("hidden"), false);
  assert.equal(ui.element("agentOutputFields").classList.contains("hidden"), true);
  ui.click("agentOutputSchemaTab");
  assert.equal(ui.element("agentOutputSchemaLabel").textContent, "查看 JSON");
  assert.equal(ui.element("agentOutputAdd").disabled, false);
  assert.deepEqual(native(ui.controller.payload()), before);
  assert.equal(ui.dirty(), 0);
});

test("array and root-ref definitions stay in native JSON; editing an object enables the return action", () => {
  for (const schema of [{ type: "array", items: { type: "string" } }, { $ref: "#/$defs/value", $defs: { value: { type: "object", properties: { a: { type: "string" } } } } }]) {
    const value = { ...native(config), allow_text_output: true };
    value.output_config.description = null;
    value.output_config.json_schema = schema;
    const ui = editor(value);
    assert.equal(ui.element("agentOutputSchemaEditor").classList.contains("hidden"), false);
    assert.equal(ui.element("agentOutputSchemaTab").disabled, true);
    assert.equal(ui.element("agentOutputAdd").disabled, true);
    assert.equal(ui.element("agentOutputFields").innerHTML, "");
    assert.deepEqual(native(ui.controller.payload()), value);
    assert.equal(ui.dirty(), 0);
    ui.input("agentOutputSchema", '{"type":"object","properties":{}}');
    assert.equal(ui.element("agentOutputSchemaTab").disabled, false);
    ui.click("agentOutputSchemaTab");
    assert.equal(ui.element("agentOutputFields").classList.contains("hidden"), false);
    assert.equal(ui.element("agentOutputAllowText").checked, true);
    assert.deepEqual(native(ui.controller.payload().output_config.json_schema), { type: "object", properties: {} });
  }
});

test("shared JSON highlighter handles unsafe, partial source and scroll without rewriting input", () => {
  const input = { value: '{"x":"<script>&", "bool":false, "n":null, "num":-2e3, "partial":', scrollTop: 50, scrollLeft: 19 };
  const highlight = { parentElement: {} };
  const before = input.value;
  JsonInput.syncJsonInput(input, highlight);
  assert.equal(input.value, before);
  assert.doesNotMatch(highlight.innerHTML, /<script>/);
  assert.match(highlight.innerHTML, /&lt;script&gt;&amp;/);
  for (const kind of ["key", "string", "boolean", "null", "number"]) assert.match(highlight.innerHTML, new RegExp(`json-${kind}`));
  assert.equal(highlight.parentElement.scrollTop, 50);
  assert.equal(highlight.parentElement.scrollLeft, 19);
});

const complexConfig = () => {
  const value = native(config);
  value.output_config.description = null;
  value.output_config.json_schema = {
    type: "object", additionalProperties: false, required: ["revision", "review"],
    properties: { revision: { type: "integer", minimum: 1 }, review: { $ref: "#/$defs/review" }, note: { type: "string" } },
    $defs: { review: { type: "object", title: "审核", properties: { approved: { type: "boolean" } }, required: ["approved"] } },
    allOf: [{ if: { properties: { revision: { const: 1 } } }, then: { properties: { review: { properties: { approved: { const: false } } } } } }],
    "x-client": { preserve: true },
  };
  return value;
};

test("complex direct properties render as cards; switching modes and viewing refs preserves the entire definition", () => {
  const value = complexConfig();
  const ui = editor(value);
  assert.equal(ui.element("agentOutputSchemaTab").disabled, false);
  assert.match(ui.element("agentOutputFields").innerHTML, /审核/);
  assert.match(ui.element("agentOutputFields").innerHTML, /1 个字段/);
  ui.click("agentOutputSchemaTab"); ui.click("agentOutputSchemaTab");
  assert.deepEqual(native(ui.controller.payload()), value);
  assert.equal(ui.dirty(), 0);
  ui.edit("review");
  assert.equal(Boolean(ui.element("agentOutputFieldKey").readOnly), false);
  assert.deepEqual(JSON.parse(ui.element("agentOutputFieldSchema").value), { $ref: "#/$defs/review" });
  ui.input("agentOutputFieldSchema", '{"$ref":"#/$defs/review","title":"审核结果","description":"保留规则"}');
  ui.click("agentOutputDetailConfirm");
  const expected = native(value);
  expected.output_config.json_schema.properties.review = { $ref: "#/$defs/review", title: "审核结果", description: "保留规则" };
  assert.deepEqual(native(ui.controller.payload()), expected);
});

test("field cards retain editable required controls even with complex rules", () => {
  const value = complexConfig(); const ui = editor(value);
  assert.match(ui.element("agentOutputFields").innerHTML, /data-output-required="revision" checked/);
  assert.doesNotMatch(ui.element("agentOutputFields").innerHTML, /disabled/);
  assert.deepEqual(native(ui.controller.payload()), value);
  assert.equal(ui.dirty(), 0);
});

test("required changes only root required and leaves conditional rules intact", () => {
  const value = complexConfig(); const ui = editor(value);
  const checkbox = { matches: () => true, dataset: { outputRequired: "revision" }, checked: false };
  ui.element("agentOutputFields").dispatchEvent({ type: "change", target: checkbox });
  const changed = native(ui.controller.payload().output_config.json_schema);
  assert.deepEqual(changed.required, ["review"]);
  assert.deepEqual(changed.allOf, value.output_config.json_schema.allOf);
  assert.deepEqual(changed.$defs, value.output_config.json_schema.$defs);
  checkbox.checked = true;
  ui.element("agentOutputFields").dispatchEvent({ type: "change", target: checkbox });
  assert.deepEqual([...ui.controller.payload().output_config.json_schema.required].sort(), ["review", "revision"]);
});

test("failed detail confirmation then cancellation leaves the page draft clean", () => {
  const ui = editor(complexConfig()); const before = native(ui.controller.payload());
  ui.edit("review"); ui.input("agentOutputFieldSchema", '{"type":'); ui.click("agentOutputDetailConfirm");
  assert.match(ui.element("agentOutputDetailError").textContent, /无效/);
  ui.click("agentOutputDetailCancel");
  assert.deepEqual(native(ui.controller.payload()), before);
  assert.equal(ui.dirty(), 0);
});

test("complex rules do not block card edits or get silently rewritten with field changes", () => {
  const value = complexConfig();
  value.output_config.json_schema.allOf.push({ properties: { reserved: { type: "integer" } } });
  const ui = editor(value);
  ui.edit("revision"); ui.input("agentOutputFieldKey", "renamed"); ui.click("agentOutputDetailConfirm");
  assert.equal(ui.element("agentOutputDetail").open, false);
  assert.equal(ui.controller.payload().output_config.json_schema.properties.renamed.type, "integer");
  ui.click("agentOutputAdd"); ui.input("agentOutputFieldKey", "reserved"); ui.click("agentOutputDetailConfirm");
  assert.equal(ui.element("agentOutputDetail").open, false);
  ui.element("agentOutputFields").dispatchEvent({ type: "click", target: { closest: selector => selector === "[data-output-delete]" ? { dataset: { outputDelete: "review" } } : null } });
  const result = native(ui.controller.payload().output_config.json_schema);
  assert.equal(result.properties.review, undefined);
  assert.deepEqual(result.required, ["renamed"]);
  assert.deepEqual(result.allOf, value.output_config.json_schema.allOf);
  assert.deepEqual(result.$defs, value.output_config.json_schema.$defs);
});

test("a protected suggested name does not prevent choosing a safe new field name", () => {
  const value = complexConfig();
  value.output_config.json_schema.allOf.push({ properties: { field_1: { type: "integer" } } });
  const ui = editor(value);
  ui.click("agentOutputAdd");
  assert.equal(ui.element("agentOutputDetail").open, true);
  ui.input("agentOutputFieldKey", "free_note"); ui.click("agentOutputDetailConfirm");
  assert.equal(ui.controller.payload().output_config.json_schema.properties.free_note.type, "string");
});

test("a malformed native property remains editable as source without crashing card rendering", () => {
  const value = complexConfig(); value.output_config.json_schema.properties.review = null;
  const ui = editor(value);
  assert.equal(ui.element("agentOutputSchemaTab").disabled, true);
  assert.equal(ui.element("agentOutputSchemaEditor").classList.contains("hidden"), false);
  assert.deepEqual(native(ui.controller.payload()), value);
});
