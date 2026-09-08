import assert from "node:assert/strict";
import test from "node:test";

import { parseSchemaText } from "../../../console/schema-editor.js";

test("Schema 文本必须是合法 JSON 对象", () => {
  assert.deepEqual(parseSchemaText('{"type":"object","properties":{}}'), {
    type: "object",
    properties: {},
  });
  assert.throws(() => parseSchemaText("[1,2]"), /顶层必须是 JSON 对象/);
  assert.throws(() => parseSchemaText("{"), /JSON 格式错误/);
  assert.throws(() => parseSchemaText('{"type":"unknown"}'), /不支持的 Schema type/);
});
