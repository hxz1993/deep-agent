import assert from "node:assert/strict";
import test from "node:test";
import { canUseFieldCards, fieldProtection, resolveFieldDisplay } from "../../../console/output-schema-fields.js";

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

test("the owned root properties/required and nested child instances do not protect a root field", () => {
  const schema = object({ required: ["text"], additionalProperties: false });
  schema.properties.contract = { type: "object", required: ["text"], properties: { text: { type: "string" } }, allOf: [{ required: ["text"] }] };
  schema.properties.items = { type: "array", items: { required: ["text"] } };
  assert.deepEqual(fieldProtection(schema, "text"), []);
  assert.deepEqual(fieldProtection(schema, "new_field"), []);
});

test("same-instance applicators protect named properties/required without locking an independent field", () => {
  for (const keyword of ["allOf", "anyOf", "oneOf", "not", "if", "then", "else"]) {
    const rule = { properties: { contract: { type: "object" } }, required: ["text"] };
    const schema = object({ [keyword]: ["allOf", "anyOf", "oneOf"].includes(keyword) ? [rule] : rule });
    const branch = `/${keyword}${Array.isArray(schema[keyword]) ? "/0" : ""}`;
    assert.deepEqual(fieldProtection(schema, "contract"), [`${branch}/properties/contract`]);
    assert.deepEqual(fieldProtection(schema, "text"), [`${branch}/required/0`]);
    assert.deepEqual(fieldProtection(schema, "unrelated"), []);
  }
});

test("dependencies protect triggers, required values and same-instance conditional child schemas", () => {
  const schema = object({ dependentRequired: { contract: ["text"] }, dependentSchemas: { activated: { allOf: [{ required: ["contract"] }] } } });
  assert.deepEqual(fieldProtection(schema, "text"), ["/dependentRequired/contract/0"]);
  assert.deepEqual(fieldProtection(schema, "contract"), ["/dependentRequired/contract", "/dependentSchemas/activated/allOf/0/required/0"]);
  assert.deepEqual(fieldProtection(schema, "activated"), ["/dependentSchemas/activated"]);
  assert.deepEqual(fieldProtection(schema, "unrelated"), []);
});

test("only definitions actually referenced at the root instance contribute protection", () => {
  const schema = object({
    $defs: { used: { required: ["contract"], properties: { text: {} } }, unused: { required: ["unrelated"] }, child: { required: ["unrelated"] } },
    allOf: [{ $ref: "#/$defs/used" }],
  });
  schema.properties.contract = { $ref: "#/$defs/child" };
  assert.deepEqual(fieldProtection(schema, "contract"), ["/$defs/used/required/0"]);
  assert.deepEqual(fieldProtection(schema, "text"), ["/$defs/used/properties/text"]);
  assert.deepEqual(fieldProtection(schema, "unrelated"), []);
});

test("local reference chains, escaped pointer tokens and siblings retain their own rule paths", () => {
  const schema = object({ $defs: { "a/b~c": { required: ["text"] }, chain: { $ref: "#/$defs/a~1b~0c", required: ["contract"] } }, $ref: "#/$defs/chain", if: { $ref: "#/$defs/a~1b~0c" } });
  assert.deepEqual(fieldProtection(schema, "text"), ["/$defs/a~1b~0c/required/0"]);
  assert.deepEqual(fieldProtection(schema, "contract"), ["/$defs/chain/required/0"]);
  assert.deepEqual(fieldProtection(schema, "unrelated"), []);
});

test("unresolved, external, unsupported, malformed and cyclic references conservatively protect edits", () => {
  for (const reference of ["https://example.invalid/schema", "file:///tmp/schema", "#", "#/properties/text", "#/$defs/missing", "#/$defs/%ZZ", "#/$defs/a~2b", "#/$defs/constructor"]) {
    assert.deepEqual(fieldProtection(object({ $ref: reference }), "anything"), ["/$ref"]);
  }
  const schema = object({ $defs: { a: { $ref: "#/$defs/b" }, b: { $ref: "#/$defs/a" } }, $ref: "#/$defs/a" });
  assert.deepEqual(fieldProtection(schema, "anything"), ["/$defs/b/$ref"]);
  assert.deepEqual(fieldProtection(object({ $dynamicRef: "#recursive" }), "anything"), ["/$dynamicRef"]);
});

test("whole-instance and name rules protect existing and not-yet-defined field names", () => {
  for (const [keyword, value] of [["const", { text: "fixed" }], ["enum", [{ text: "one" }]], ["propertyNames", { pattern: "^a" }], ["patternProperties", { "^a": {} }], ["unevaluatedProperties", false]]) {
    const schema = object({ [keyword]: value });
    assert.deepEqual(fieldProtection(schema, "text"), [`/${keyword}`]);
    assert.deepEqual(fieldProtection(schema, "new_field"), [`/${keyword}`]);
  }
  for (const keyword of ["additionalProperties", "unevaluatedProperties"]) {
    assert.deepEqual(fieldProtection(object({ allOf: [{ [keyword]: false }] }), "new_field"), [`/allOf/0/${keyword}`]);
    assert.deepEqual(fieldProtection(object({ allOf: [{ [keyword]: true }] }), "new_field"), []);
  }
});

test("annotations, literals and unused definitions are preserved without being interpreted as rules", () => {
  const schema = frozen(object({ title: "contract", description: "required text", default: { required: ["text"] }, examples: [{ properties: { text: {} } }], "x-client": { required: ["text"], $ref: "https://example.invalid" }, $defs: { unused: { $ref: "#/$defs/missing" } } }));
  const before = JSON.stringify(schema);
  assert.deepEqual(fieldProtection(schema, "text"), []);
  assert.equal(JSON.stringify(schema), before);
  assert.deepEqual(fieldProtection(object({ customVocabulary: { field: "text" } }), "new_field"), ["/customVocabulary"]);
});

test("malformed rules fail closed without executing patterns or throwing", () => {
  for (const extra of [{ allOf: {} }, { if: [] }, { dependentSchemas: [] }, { dependentRequired: { a: false } }, { not: { properties: null } }]) {
    assert.ok(fieldProtection(object(extra), "text").length > 0);
  }
  assert.deepEqual(fieldProtection(object({ patternProperties: { "(a+)+$": {} } }), "a".repeat(1000)), ["/patternProperties"]);
  assert.deepEqual(fieldProtection(null, "text"), ["/"]);
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
  assert.deepEqual(fieldProtection(schema, "__proto__"), ["/allOf/0/required/0"]);
  assert.deepEqual(fieldProtection(schema, "constructor"), ["/allOf/0/required/1"]);
  assert.deepEqual(fieldProtection(schema, "a/b~c"), ["/allOf/0/properties/a~1b~0c"]);
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
    assert.deepEqual(fieldProtection({ ...schema, $ref: reference }, "field"), ["/$ref"]);
  }
  const before = JSON.stringify(schema.$defs);
  const display = resolveFieldDisplay(schema, { $ref: "#/$defs/root/properties/inner/$defs/value" });
  assert.equal(display.type, "number");
  assert.equal(JSON.stringify(schema.$defs), before);
});

test("deep unadmitted input and cyclic JS fixtures fail closed within a bounded traversal", () => {
  let deep = { required: ["text"] };
  for (let index = 0; index < 5000; index++) deep = { allOf: [deep] };
  assert.ok(fieldProtection(deep, "text").length > 0);
  const cycle = object();
  cycle.not = cycle;
  assert.deepEqual(fieldProtection(cycle, "text"), ["/not"]);
  const root = object({ $defs: {} });
  for (let index = 0; index < 100; index++) root.$defs[`level${index}`] = { $ref: `#/$defs/level${index + 1}` };
  root.$defs.level100 = { type: "string" };
  assert.doesNotThrow(() => resolveFieldDisplay(root, { $ref: "#/$defs/level0" }));
});
