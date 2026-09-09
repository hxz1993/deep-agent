export function parseSchemaText(value) {
  let schema;
  try {
    schema = JSON.parse(String(value || "{}"));
  } catch (error) {
    throw new Error(`JSON 格式错误：${error.message}`);
  }
  if (!schema || typeof schema !== "object" || Array.isArray(schema)) {
    throw new Error("Schema 顶层必须是 JSON 对象");
  }
  if (schema.type && !["object", "array", "string", "number", "integer", "boolean", "null"].includes(schema.type)) {
    throw new Error(`不支持的 Schema type：${schema.type}`);
  }
  return schema;
}
