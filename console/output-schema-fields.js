// Field cards own only root properties/required. Complex rules remain native Schema.
const own = (value, key) => Object.hasOwn(value, key);
const record = value => value !== null && typeof value === "object" && !Array.isArray(value);
const schemaNode = value => typeof value === "boolean" || record(value);
const pointerToken = value => String(value).replaceAll("~", "~0").replaceAll("/", "~1");
const childPath = (path, key) => `${path}/${pointerToken(key)}`;
const listApplicators = new Set(["allOf", "anyOf", "oneOf"]);
const singleApplicators = new Set(["not", "if", "then", "else"]);
const fieldNameRules = new Set(["const", "enum", "propertyNames", "patternProperties"]);
// These keywords do not apply a Schema to the current object's fields. In
// particular, annotations containing property names are not field references.
const passiveKeywords = new Set([
  "$schema", "$id", "$anchor", "$comment", "$defs",
  "title", "description", "default", "examples", "deprecated", "readOnly", "writeOnly",
  "type", "format", "contentEncoding", "contentMediaType", "contentSchema",
  "multipleOf", "maximum", "exclusiveMaximum", "minimum", "exclusiveMinimum",
  "maxLength", "minLength", "pattern", "maxProperties", "minProperties",
  "prefixItems", "items", "contains", "maxContains", "minContains",
  "maxItems", "minItems", "uniqueItems", "unevaluatedItems",
]);

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
  let path = "";
  for (const token of tokens) {
    if (/~(?![01])/.test(token)) return null;
    const key = token.replaceAll("~1", "/").replaceAll("~0", "~");
    if (!record(value) || !own(value, key)) return null;
    value = value[key];
    path = childPath(path, key);
  }
  return schemaNode(value) ? { value, path } : null;
}

export function fieldProtection(schema, key) {
  if (!record(schema) || typeof key !== "string") return ["/"];
  const paths = new Set();
  const active = new Set();
  const visited = new Set();
  let remaining = 4096;
  const protect = path => paths.add(path || "/");

  function visit(node, path, root = false, depth = 0) {
    if (--remaining < 0 || depth > 64) { protect(path); return; }
    if (typeof node === "boolean") return;
    if (!record(node)) { protect(path); return; }
    if (active.has(node)) { protect(path); return; }
    const location = `${root ? "root:" : "branch:"}${path}`;
    if (visited.has(location)) return;
    visited.add(location);
    active.add(node);
    try {
      for (const [keyword, value] of Object.entries(node)) {
        const at = childPath(path, keyword);
        if (keyword === "properties") {
          if (!record(value)) protect(at);
          else if (!root && own(value, key)) protect(childPath(at, key));
          // A property Schema describes a child instance, not the root object.
        } else if (keyword === "required") {
          if (!Array.isArray(value) || value.some(name => typeof name !== "string")) protect(at);
          else if (!root) value.forEach((name, index) => { if (name === key) protect(childPath(at, index)); });
        } else if (keyword === "dependentRequired") {
          if (!record(value)) { protect(at); continue; }
          for (const [trigger, dependencies] of Object.entries(value)) {
            const dependencyPath = childPath(at, trigger);
            if (trigger === key) protect(dependencyPath);
            if (!Array.isArray(dependencies) || dependencies.some(name => typeof name !== "string")) protect(dependencyPath);
            else dependencies.forEach((name, index) => { if (name === key) protect(childPath(dependencyPath, index)); });
          }
        } else if (keyword === "dependentSchemas") {
          if (!record(value)) { protect(at); continue; }
          for (const [trigger, child] of Object.entries(value)) {
            const triggerPath = childPath(at, trigger);
            if (trigger === key) protect(triggerPath);
            visit(child, triggerPath, false, depth + 1);
          }
        } else if (listApplicators.has(keyword)) {
          if (!Array.isArray(value)) protect(at);
          else value.forEach((child, index) => visit(child, childPath(at, index), false, depth + 1));
        } else if (singleApplicators.has(keyword)) {
          visit(value, at, false, depth + 1);
        } else if (keyword === "$ref") {
          const target = localReference(schema, value);
          if (!target || active.has(target.value)) protect(at);
          else visit(target.value, target.path, false, depth + 1);
        } else if (fieldNameRules.has(keyword)) {
          // Name patterns, whole-instance constants and enums can constrain
          // names that do not yet occur in root properties. Do not guess intent.
          protect(at);
        } else if (keyword === "unevaluatedProperties" || (keyword === "additionalProperties" && !root)) {
          if (value !== true) protect(at);
        } else if (keyword === "additionalProperties" || passiveKeywords.has(keyword) || keyword.startsWith("x-")) {
          // Root additionalProperties is interpreted against the root
          // properties that the cards own. $defs is visited only through refs.
        } else {
          // Unknown same-instance vocabulary/references might constrain names.
          // Preserve them and require native editing for structural changes.
          protect(at);
        }
      }
    } finally { active.delete(node); }
  }

  visit(schema, "", true);
  return [...paths].sort();
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
