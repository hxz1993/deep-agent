// Field cards project root properties; complex rules remain in the original Schema.
const own = (value, key) => Object.hasOwn(value, key);
const record = value => value !== null && typeof value === "object" && !Array.isArray(value);
const schemaNode = value => typeof value === "boolean" || record(value);
const listApplicators = new Set(["allOf", "anyOf", "oneOf"]);
const singleApplicators = new Set(["not", "if", "then", "else"]);
const fieldNameRules = new Set(["const", "enum", "propertyNames", "patternProperties"]);
export function canUseFieldCards(schema) {
  if (!record(schema) || !own(schema, "type") || schema.type !== "object") return false;
  if (own(schema, "required") && (!Array.isArray(schema.required) || schema.required.some(key => typeof key !== "string"))) return false;
  if (own(schema, "properties")) return record(schema.properties) && Object.values(schema.properties).every(schemaNode);
  // Preserve the original empty-object card flow, without pretending that
  // fields inherited solely from branches or references are direct fields.
  return !Object.keys(schema).some(key => listApplicators.has(key) || singleApplicators.has(key)
    || fieldNameRules.has(key) || ["$ref", "$dynamicRef", "$recursiveRef", "dependentSchemas", "dependentRequired"].includes(key));
}

function localReference(root, reference) {
  // Match the backend's admitted document-local $defs references. Never load a
  // resource, follow an inherited JS property, or evaluate a regular expression.
  if (typeof reference !== "string" || !reference.startsWith("#/$defs/")) return null;
  let fragment;
  try { fragment = decodeURIComponent(reference.slice(2)); } catch { return null; }
  const tokens = fragment.split("/");
  let value = root;
  for (const token of tokens) {
    if (/~(?![01])/.test(token)) return null;
    const key = token.replaceAll("~1", "/").replaceAll("~0", "~");
    if (!record(value) || !own(value, key)) return null;
    value = value[key];
  }
  return schemaNode(value) ? { value } : null;
}

export function resolveFieldDisplay(schema, field) {
  if (!record(field)) return field;
  const active = new Set();
  let remaining = 64;
  function display(node) {
    if (!record(node)) return node;
    const copy = Object.fromEntries(Object.entries(node));
    if (!own(node, "$ref") || --remaining < 0) return copy;
    const target = localReference(schema, node.$ref);
    if (!target || active.has(target.value)) return copy;
    active.add(target.value);
    try {
      const inherited = display(target.value);
      // This is display metadata only: local annotations win. Validation still
      // uses the untouched original $ref and all of its sibling constraints.
      return record(inherited) ? Object.fromEntries([...Object.entries(inherited), ...Object.entries(copy)]) : copy;
    } finally { active.delete(target.value); }
  }
  const result = display(field);
  // The card's array summary counts the immediate item's object properties.
  // Resolve that item's local reference too, without traversing field values.
  if (record(result) && record(result.items) && own(result.items, "$ref")) result.items = display(result.items);
  return result;
}
