import assert from "node:assert/strict";
import test from "node:test";
import vm from "node:vm";
import { readFile } from "node:fs/promises";
const source = await readFile(new URL("../../../console/configuration/tools.js", import.meta.url), "utf8");
function setup(refreshers = {}) {
  function element(dataset = {}) {
    return { dataset, attributes: {}, handlers: {}, classList: { toggle() {} },
      setAttribute(key, value) { this.attributes[key] = value; },
      addEventListener(key, fn) { (this.handlers[key] ||= []).push(fn); },
      focus() { this.focused = true; } };
  }
  const tabs = [element({ toolsView: "ui" }), element({ toolsView: "builtin" })];
  const panels = [element({ toolsPanel: "ui" }), element({ toolsPanel: "builtin" })];
  const root = element(); root.querySelectorAll = () => tabs;
  const newButton = element(), description = element();
  const calls = [];
  const window = Object.fromEntries(["uiTools", "builtinTools", "systemTools"].map(name => [name + "Admin", {
    refresh: () => { calls.push(name); return refreshers[name]?.(); },
  }]));
  const document = {
    getElementById: id => ({ "panel-tools": root, newUiToolPackageBtn: newButton, toolsViewDescription: description })[id],
    querySelectorAll: selector => selector === "[data-tools-view]" ? tabs : panels,
    querySelector: selector => tabs.find(tab => selector.includes(`"${tab.dataset.toolsView}"`)),
    addEventListener() {},
  };
  vm.runInNewContext(source, { window, document });
  window.toolsAdmin.init();
  return { api: window.toolsAdmin, tabs, panels, newButton, description, calls };
}
test("工具默认 UI 视图，新建入口和无障碍选择状态同步", () => {
  const { api, tabs, panels, newButton } = setup();
  assert.equal(api.currentView(), "ui");
  assert.deepEqual(panels.map(p => p.hidden), [false, true]);
  assert.deepEqual(tabs.map(t => t.attributes["aria-selected"]), ["true", "false"]);
  assert.deepEqual(tabs.map(t => t.tabIndex), [0, -1]);
  assert.equal(newButton.hidden, false);
  api.init();
  assert.equal(tabs[0].handlers.click.length, 1);
});
test("切换只刷新对应目录，新建仅在 UI 视图显示", async () => {
  const { api, panels, newButton, description, calls } = setup();
  await api.select("builtin");
  assert.deepEqual(calls, ["builtinTools", "systemTools"]);
  assert.deepEqual(panels.map(p => p.hidden), [true, false]);
  assert.equal(newButton.hidden, true);
  assert.match(description.textContent, /运行时/);
  await api.select("ui");
  assert.equal(calls.at(-1), "uiTools");
  assert.equal(newButton.hidden, false);
  await api.select("toString");
  assert.equal(api.currentView(), "ui");
  assert.equal(calls.length, 3);
});
test("键盘方向键和 Home/End 同步切换与焦点", async () => {
  const { api, tabs } = setup();
  const press = (index, key) => tabs[index].handlers.keydown[0]({ key, preventDefault() {} });
  press(0, "ArrowRight");
  assert.equal(api.currentView(), "builtin"); assert.equal(tabs[1].focused, true);
  press(1, "Home"); assert.equal(api.currentView(), "ui");
  press(0, "End"); assert.equal(api.currentView(), "builtin");
  press(1, "ArrowLeft"); assert.equal(api.currentView(), "ui");
  await api.refresh();
});
test("快速往返切换合并未完成请求，迟到结果不改变当前视图", async () => {
  let finishUi, finishBuiltin;
  const { api, calls, panels, newButton } = setup({
    uiTools: () => new Promise(resolve => { finishUi = resolve; }),
    builtinTools: () => new Promise(resolve => { finishBuiltin = resolve; }),
  });
  const first = api.refresh();
  const builtin = api.select("builtin");
  const last = api.select("ui");
  assert.equal(first, last);
  await Promise.resolve();
  assert.deepEqual(calls, ["uiTools", "builtinTools", "systemTools"]);
  finishBuiltin(); await builtin;
  assert.equal(api.currentView(), "ui");
  assert.deepEqual(panels.map(p => p.hidden), [false, true]);
  assert.equal(newButton.hidden, false);
  finishUi(); await last;
});
test("刷新失败后可重新请求，不锁死工具视图", async () => {
  let attempts = 0;
  const { api } = setup({ uiTools: () => { if (++attempts === 1) throw Error("offline"); } });
  await assert.rejects(api.refresh(), /offline/);
  await api.refresh();
  assert.equal(attempts, 2);
});
