import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";
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
  const window = { DeepAgentJsonInput: JsonInput };
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
  output_config: { name: "lease_result", json_schema: { type: "object", properties: { text: { type: "string", title: "正文" } }, additionalProperties: false } },
};

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
  assert.throws(() => ui.controller.payload(), /JSON Schema 无效/);
  assert.equal(ui.element("agentOutputSchema").value, '{"type":');
  assert.match(ui.element("agentOutputSchemaHighlight").innerHTML, /json-key/);
  ui.input("agentOutputSchema", '{"type":"object","properties":{}}');
  assert.deepEqual(native(ui.controller.payload().output_config.json_schema.properties), {});
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
