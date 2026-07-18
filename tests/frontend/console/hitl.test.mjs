import assert from "node:assert/strict";
import test from "node:test";

import { normalizeOption, normalizeStructuredFields, validateUiToolCall } from "../../../frontend/console/hitl.js";

test("结构化表单兼容 field 和 option.name/description", () => {
  const result = normalizeStructuredFields([
    { field: "reason", widget: "textarea", label: "原因" },
    {
      id: "account",
      type: "select",
      options: [{ value: "A100", name: "主营业务收入", description: "收入类科目" }],
    },
  ]);
  assert.equal(result.ok, true);
  assert.deepEqual(result.fields[0], {
    id: "reason",
    label: "原因",
    description: "",
    type: "textarea",
    placeholder: "",
    required: false,
    defaultValue: "",
    options: [],
  });
  assert.deepEqual(result.fields[1].options, [
    { value: "A100", label: "主营业务收入", description: "收入类科目" },
  ]);
});

test("非法 fields 和 options 会返回业务校验错误", () => {
  assert.deepEqual(validateUiToolCall({ widget: "form", args: { fields: {} } }), {
    ok: false,
    error: "fields 必须是数组",
    fields: [],
    pending: false,
  });
  assert.equal(validateUiToolCall({ widget: "choice", args: { options: {} } }).ok, false);
  assert.equal(validateUiToolCall({ widget: "form", args: {} }).pending, true);
});

test("字符串和对象选项归一化为稳定结构", () => {
  assert.deepEqual(normalizeOption("A"), { value: "A", label: "A", description: "" });
  assert.deepEqual(normalizeOption({ id: "A", label: "科目A" }), {
    value: "A",
    label: "科目A",
    description: "",
  });
});
