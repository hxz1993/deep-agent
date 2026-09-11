import assert from "node:assert/strict";
import test from "node:test";
import { nextSelectIndex, selectMenuPosition, observeSelectProperty, selectLabelText } from "../../../console/ui-select.js";

const option = (disabled = false, hidden = false, groupDisabled = false) => ({ disabled, hidden, parentElement: { disabled: groupDisabled } });

test("共享下拉键盘导航跳过 disabled、隐藏选项及禁用组，并支持 Home/End", () => {
  const options = [option(true), option(), option(false, true), option(), option(false, false, true)];
  assert.equal(nextSelectIndex(options, -1, "ArrowDown"), 1);
  assert.equal(nextSelectIndex(options, 1, "ArrowDown"), 3);
  assert.equal(nextSelectIndex(options, 3, "ArrowUp"), 1);
  assert.equal(nextSelectIndex(options, 3, "ArrowDown"), 3);
  assert.equal(nextSelectIndex(options, 1, "ArrowUp"), 1);
  assert.equal(nextSelectIndex(options, 3, "Home"), 1);
  assert.equal(nextSelectIndex(options, 1, "End"), 3);
  assert.equal(nextSelectIndex([option(true)], 0, "End"), -1);
  assert.equal(nextSelectIndex([], -1, "ArrowDown"), -1);
});

test("共享下拉在底部向上展开，靠右和窄屏时保持在视口内", () => {
  const viewport = { width: 600, height: 400 };
  const bottom = selectMenuPosition({ left: 550, top: 330, bottom: 362, width: 180 }, viewport);
  assert.equal(bottom.top, 45);
  assert.equal(bottom.left, 412);
  assert.equal(bottom.maxHeight, 280);
  const top = selectMenuPosition({ left: 12, top: 20, bottom: 52, width: 180 }, viewport, 100);
  assert.equal(top.top, 57);
  assert.equal(top.maxHeight, 100);
  const narrow = selectMenuPosition({ left: 0, top: 10, bottom: 42, width: 380 }, { width: 150, height: 180 });
  assert.equal(narrow.left, 8);
  assert.equal(narrow.width, 134);
  assert.equal(narrow.maxHeight, 125);
});

test("程序设置 value/selectedIndex 同步显示，但不伪造 change，销毁恢复原生属性", () => {
  class NativeSelect {
    get value() { return this._value || ""; }
    set value(value) { this._value = String(value); }
  }
  const select = new NativeSelect();
  const observed = [];
  const restore = observeSelectProperty(select, "value", () => observed.push(select.value));
  select.value = "deepseek";
  select.value = "kimi";
  assert.deepEqual(observed, ["deepseek", "kimi"]);
  assert.equal(select.value, "kimi");
  assert.equal(Object.hasOwn(select, "value"), true);
  restore();
  assert.equal(Object.hasOwn(select, "value"), false);
  select.value = "original";
  assert.equal(select.value, "original");
  assert.equal(observed.length, 2);
});

test("属性适配保留消费者原有描述器并跳过不可重定义属性", () => {
  let value = 0;
  const target = {};
  const descriptor = { configurable: true, get() { return value; }, set(next) { value = next; } };
  Object.defineProperty(target, "selectedIndex", descriptor);
  let updates = 0;
  const restore = observeSelectProperty(target, "selectedIndex", () => updates++);
  target.selectedIndex = 2;
  assert.equal(value, 2);
  assert.equal(updates, 1);
  restore();
  assert.equal(Object.getOwnPropertyDescriptor(target, "selectedIndex").get, descriptor.get);
  Object.defineProperty(target, "locked", { value: 3, configurable: false });
  observeSelectProperty(target, "locked", () => assert.fail("must not observe"))();
  assert.equal(target.locked, 3);
});


test("下拉无障碍名称只读取嵌套标题，排除帮助、说明和控件选项子树", () => {
  const text = value => ({ nodeType: 3, textContent: value });
  const element = (selector, ...children) => ({
    nodeType: 1,
    childNodes: children,
    matches: selectors => selectors.split(", ").includes(selector),
  });
  const label = element("label",
    element(".field-label-row", element("span", text(" Widget ")),
      element(".field-help", text("Widget说明"), element("[role=tooltip]", text("客户端渲染器说明"))),
      element(".field-subtitle", text("额外副标题"))),
    element(".widget-picker", element(".da-select",
      element("select", text("confirm choice custom")), element("button", text("confirm"))),
      element("input", text("自定义输入"))),
    element("small", text("保留旧说明")),
    element("[aria-hidden=true]", text("隐藏内容")));
  assert.equal(selectLabelText(label), "Widget");
  assert.equal(selectLabelText(element("label", text("名称"), element("div", element("span", text("（可选）"))))), "名称 （可选）");
});
