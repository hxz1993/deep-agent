import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(
  new URL("../../../frontend/console/configuration/form-feedback.js", import.meta.url),
  "utf8",
);

function loadFeedback() {
  const document = {
    addEventListener() {},
  };
  const window = {};
  vm.runInContext(source, vm.createContext({ document, window }));
  return window.AdminFormFeedback;
}

test("Pydantic 字段错误会格式化为可读信息", () => {
  const feedback = loadFeedback();
  const message = feedback.formatDetail([
    { loc: ["body", "config", "request_context", "mode"], msg: "Input should be 'inherit' or 'isolated'" },
  ]);
  assert.equal(
    message,
    "config.request_context.mode: Input should be 'inherit' or 'isolated'",
  );
});

test("弹窗错误保留内容并切换提交按钮状态", () => {
  const feedback = loadFeedback();
  const classes = new Set(["hidden"]);
  const errorElement = {
    textContent: "",
    classList: {
      add(value) { classes.add(value); },
      remove(value) { classes.delete(value); },
    },
    scrollIntoView() {},
  };
  const submit = { textContent: "保存", disabled: false, dataset: {} };
  const form = {
    querySelector(selector) {
      return selector === "[data-form-error]" ? errorElement : submit;
    },
  };

  feedback.show(form, new Error("保存失败"));
  assert.equal(errorElement.textContent, "保存失败");
  assert.equal(classes.has("hidden"), false);

  feedback.setSubmitting(form, true);
  assert.equal(submit.disabled, true);
  assert.equal(submit.textContent, "保存中…");
  feedback.setSubmitting(form, false);
  assert.equal(submit.disabled, false);
  assert.equal(submit.textContent, "保存");

  submit.dataset.submittingLabel = "保存并检测中…";
  feedback.setSubmitting(form, true);
  assert.equal(submit.textContent, "保存并检测中…");
});
