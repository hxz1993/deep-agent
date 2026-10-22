import assert from "node:assert/strict";
import test from "node:test";
import { canUseFieldCards, resolveFieldDisplay } from "../../../console/output-schema-fields.js";

const object = extra => ({ type: "object", properties: { text: { type: "string" }, contract: { type: "object" }, unrelated: {} }, ...extra });
const frozen = value => { if (value && typeof value === "object") { Object.values(value).forEach(frozen); Object.freeze(value); } return value; };

test("direct root fields remain editable alongside complex rules; inherited-only fields stay native", () => {
  for (const extra of [{ allOf: [{ required: ["contract"] }] }, { if: { required: ["contract"] }, then: { required: ["text"] } }, { anyOf: [{ required: ["contract"] }] }, { $ref: "#/$defs/base" }]) {
    assert.equal(canUseFieldCards(object(extra)), true);
  }
  assert.equal(canUseFieldCards({ type: "object", properties: {} }), true);
  assert.equal(canUseFieldCards({ type: "object" }), true);
  assert.equal(canUseFieldCards({ type: "object", title: "Empty", additionalProperties: false, $defs: { unused: { type: "string" } } }), true);
  assert.equal(canUseFieldCards({ type: "object", $ref: "#/$defs/root" }), false);
  assert.equal(canUseFieldCards({ type: "object", if: { required: ["a"] }, then: { required: ["b"] } }), false);
  for (const schema of [true, null, [], { type: "array", properties: {} }, { type: "object", allOf: [{ properties: { text: {} } }] }, { type: "object", properties: null }, { type: "object", properties: [] }, { type: "object", properties: { text: null } }, { type: "object", properties: {}, required: "text" }]) {
    assert.equal(canUseFieldCards(schema), false);
  }
  assert.equal(canUseFieldCards({ type: "object", properties: { a: true, b: false } }), true);
});
test("display resolves local refs without rewriting frozen original Schema or nested definitions", () => {
  const schema = frozen(object({ $defs: { contract: { type: "object", title: "合同", description: "合同定义", properties: { id: { type: "string" }, amount: { type: "number" } } }, rows: { type: "array", items: { $ref: "#/$defs/contract" } }, alias: { $ref: "#/$defs/contract" } } }));
  const field = frozen({ $ref: "#/$defs/alias", title: "本轮合同", description: "本地说明", "x-display": "keep" });
  const before = JSON.stringify(schema);
  const display = resolveFieldDisplay(schema, field);
  assert.equal(display.type, "object");
  assert.equal(display.title, "本轮合同");
  assert.equal(display.description, "本地说明");
  assert.equal(Object.keys(display.properties).length, 2);
  assert.equal(display.$ref, "#/$defs/alias");
  assert.equal(Object.keys(resolveFieldDisplay(schema, { $ref: "#/$defs/rows" }).items.properties).length, 2);
  assert.equal(JSON.stringify(schema), before);
  assert.deepEqual(field, { $ref: "#/$defs/alias", title: "本轮合同", description: "本地说明", "x-display": "keep" });
});
test("display preserves native sibling constraints and safely falls back for unsupported references", () => {
  const schema = object({ $defs: { base: { type: "number" }, a: { $ref: "#/$defs/b" }, b: { $ref: "#/$defs/a" }, disabled: false } });
  const field = { $ref: "#/$defs/base", type: "string", allOf: [{ maxLength: 3 }] };
  assert.deepEqual(resolveFieldDisplay(schema, field), field);
  for (const reference of ["https://example.invalid", "#/$defs/missing", "#/$defs/a"]) {
    const original = { $ref: reference, title: "原始字段" };
    assert.equal(resolveFieldDisplay(schema, original).title, original.title);
    assert.equal(resolveFieldDisplay(schema, original).$ref, original.$ref);
  }
  assert.deepEqual(resolveFieldDisplay(schema, { $ref: "#/$defs/disabled", title: "不可返回" }), { $ref: "#/$defs/disabled", title: "不可返回" });
  assert.equal(resolveFieldDisplay(schema, false), false);
});
test("prototype-like and escaped property names are own data and paths remain valid JSON pointers", () => {
  const schema = JSON.parse('{"type":"object","properties":{"__proto__":{},"constructor":{},"a/b~c":{}},"$defs":{"__proto__":{"title":"Own","type":"object","properties":{"x":{}}}},"allOf":[{"required":["__proto__","constructor"],"properties":{"a/b~c":{}}}]}');
  assert.equal(resolveFieldDisplay(schema, { $ref: "#/$defs/__proto__" }).title, "Own");
  assert.equal({}.polluted, undefined);
});
test("reference paths stay within root definitions and cannot ascend into card-owned properties", () => {
  const schema = object({
    $defs: { root: { properties: { inner: { $defs: { value: { type: "number", title: "Definition target" } } } } }, "汉字": { type: "string" } },
    properties: { field: { $defs: { value: { type: "boolean", title: "Card-owned" } } } },
  });
  assert.equal(resolveFieldDisplay(schema, { $ref: "#/$defs/root/properties/inner/$defs/value" }).title, "Definition target");
  assert.equal(resolveFieldDisplay(schema, { $ref: "#/$defs/%E6%B1%89%E5%AD%97" }).type, "string");
  for (const reference of ["#/properties/field/$defs/value", "#/$defs/../properties/field/$defs/value", "#/$defs/%2e%2e/properties/field/$defs/value", "#/$defs/root/../../properties/field/$defs/value"]) {
    assert.equal(resolveFieldDisplay(schema, { $ref: reference }).title, undefined);
  }
  const before = JSON.stringify(schema.$defs);
  const display = resolveFieldDisplay(schema, { $ref: "#/$defs/root/properties/inner/$defs/value" });
  assert.equal(display.type, "number");
  assert.equal(JSON.stringify(schema.$defs), before);
});
