import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(new URL("../../../console/chat.js", import.meta.url), "utf8");
const binder = source.slice(source.indexOf("function bindComposerInput("), source.indexOf("function autoGrow("));

function harness() {
  const listeners = new Map();
  const input = { addEventListener: (type, handler) => listeners.set(type, handler) };
  let sends = 0;
  const context = { input, autoGrow() {}, onSend() { sends++; } };
  vm.runInNewContext(binder + "\nbindComposerInput(input);", context);
  return {
    sendCount: () => sends,
    composition: (type) => listeners.get(type)(),
    keydown(key, options = {}) {
      let prevented = false;
      listeners.get("keydown")({ key, ...options, preventDefault() { prevented = true; } });
      return prevented;
    },
  };
}

test("输入法组合期间把空格和回车交给输入法，下一次普通回车才发送", () => {
  const composer = harness();
  composer.composition("compositionstart");
  assert.equal(composer.keydown(" "), false);
  assert.equal(composer.keydown("Enter"), false);
  assert.equal(composer.sendCount(), 0);
  composer.composition("compositionend");
  assert.equal(composer.keydown("Enter", { shiftKey: true }), false);
  assert.equal(composer.sendCount(), 0);
  assert.equal(composer.keydown("Enter"), true);
  assert.equal(composer.sendCount(), 1);
});

test("兼容浏览器的组合输入标志，以及 Safari 先结束组合再派发回车", () => {
  const composer = harness();
  assert.equal(composer.keydown("Enter", { isComposing: true }), false);
  composer.composition("compositionstart");
  composer.composition("compositionend");
  assert.equal(composer.keydown("Enter", { isComposing: false, keyCode: 229 }), false);
  assert.equal(composer.keydown("Process"), false);
  assert.equal(composer.sendCount(), 0);
  assert.equal(composer.keydown("Enter"), true);
  assert.equal(composer.sendCount(), 1);
});
