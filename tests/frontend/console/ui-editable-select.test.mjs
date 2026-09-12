import assert from "node:assert/strict";
import test from "node:test";
import vm from "node:vm";
import { readFile } from "node:fs/promises";

const source = await readFile(new URL("../../../console/ui-editable-select.js", import.meta.url), "utf8");
function setup() {
  class Element {
    constructor() { this.children = []; this.attributes = {}; this.handlers = {}; this.dataset = {}; this.style = {}; this.value = ""; this.readOnly = false; this.disabled = false; this.classList = { toggle() {} }; }
    setAttribute(name, value) { this.attributes[name] = value; }
    getAttribute(name) { return this.attributes[name]; }
    removeAttribute(name) { delete this.attributes[name]; }
    addEventListener(type, fn) { (this.handlers[type] ||= []).push(fn); }
    removeEventListener(type, fn) { this.handlers[type] = (this.handlers[type] || []).filter(handler => handler !== fn); }
    dispatchEvent(event) { [...(this.handlers[event.type] || [])].forEach(fn => fn(event)); }
    append(...children) { children.forEach(child => { child.parent = this; this.children.push(child); }); }
    before() {}
    replaceChildren() { this.children = []; }
    closest() { return null; }
    contains(node) { return node === this || this.children.some(child => child.contains(node)); }
    focus() { this.focused = true; }
    matches() { return !!this.popover; }
    showPopover() { this.popover = true; }
    hidePopover() { this.popover = false; }
    getBoundingClientRect() { return { top: 100, bottom: 132, left: 20, width: 200 }; }
    scrollIntoView() {}
    remove() { this.removed = true; }
  }
  const doc = new Element();
  doc.createElement = () => new Element();
  doc.documentElement = { clientWidth: 1200, clientHeight: 900 };
  const window = new Element();
  vm.runInNewContext(source, { window, Event });
  const input = new Element(); input.ownerDocument = doc;
  const api = window.createEditableSelect(input, { options: [{ value: "confirm", label: "确认" }, { value: "choice", label: "选择" }] });
  const root = input.parent;
  const [, clear, toggle, menu] = root.children;
  const fire = (node, type, more = {}) => node.dispatchEvent({ type, target: node, preventDefault() {}, stopPropagation() {}, ...more });
  return { api, input, root, clear, toggle, menu, fire, window, doc };
}

test("Widget 推荐列表不增加空选项，选择后收起并保留清空入口", () => {
  const { input, root, clear, menu, fire } = setup();
  fire(input, "click");
  assert.equal(menu.children.length, 2);
  assert.deepEqual(menu.children.map(row => row.children[0].textContent), ["确认", "选择"]);
  fire(root, "keydown", { key: "ArrowDown" });
  fire(root, "keydown", { key: "Enter" });
  assert.equal(input.value, "confirm");
  assert.equal(input.getAttribute("aria-expanded"), "false");
  assert.equal(clear.hidden, false);
  const changes = [];
  input.addEventListener("change", () => changes.push(input.value));
  fire(clear, "click");
  assert.equal(input.value, "");
  assert.equal(clear.hidden, true);
  assert.equal(menu.hidden, true);
  assert.deepEqual(changes, [""]);
});

test("自定义 Widget 无匹配时方向键与回车安全保留输入，仍可用叉清空", () => {
  const { input, root, clear, menu, fire } = setup();
  input.value = "custom_renderer";
  fire(input, "input");
  assert.equal(menu.children.length, 0);
  fire(root, "keydown", { key: "ArrowDown" });
  fire(root, "keydown", { key: "ArrowUp" });
  fire(root, "keydown", { key: "Enter" });
  assert.equal(input.value, "custom_renderer");
  assert.equal(menu.hidden, true);
  fire(clear, "click");
  assert.equal(input.value, "");
});


test("首次向上键从最后一个 Widget 开始选择", () => {
  const { input, root, fire } = setup();
  fire(root, "keydown", { key: "ArrowUp" });
  fire(root, "keydown", { key: "Enter" });
  assert.equal(input.value, "choice");
});

test("进入浏览器往返缓存只关闭菜单，恢复后仍能选择；正式离开释放监听", () => {
  const { api, input, root, menu, fire, window, doc } = setup();
  fire(input, "click");
  fire(window, "pagehide", { persisted: true });
  assert.equal(menu.hidden, true);
  assert.notEqual(root.removed, true);
  fire(input, "click");
  assert.equal(menu.hidden, false);
  fire(root, "keydown", { key: "ArrowDown" });
  fire(root, "keydown", { key: "Enter" });
  assert.equal(input.value, "confirm");
  fire(window, "pagehide", { persisted: false });
  assert.equal(root.removed, true);
  assert.equal(doc.handlers.pointerdown.length, 0);
  assert.equal(window.handlers.pagehide.length, 0);
  api.destroy();
  assert.equal(doc.handlers.pointerdown.length, 0);
});
