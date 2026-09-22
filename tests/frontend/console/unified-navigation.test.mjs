import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const core = await readFile(new URL("../../../console/configuration/core.js", import.meta.url), "utf8");
const main = await readFile(new URL("../../../console/main.js", import.meta.url), "utf8");

function navigation(allowClose) {
  const events = [];
  function item(name, active = false) {
    const classes = new Set(active ? ["active"] : []);
    return { dataset: { tab: name }, classes, classList: {
      add: (value) => classes.add(value), remove: (value) => classes.delete(value),
    }, addEventListener(type, listener) { this.click = listener; } };
  }
  const tabs = [item("agents", true), item("channels")];
  const panels = [item("agents", true), item("channels")];
  const context = vm.createContext({
    window: { agentsAdmin: { isEditorOpen: () => true, requestCloseEditor: async () => allowClose } },
    document: {
      querySelectorAll: (selector) => selector === ".tab" ? tabs : panels,
      querySelector: () => null,
      getElementById: (id) => panels.find((panel) => `panel-${panel.dataset.tab}` === id),
      dispatchEvent: (event) => events.push(event),
    },
    CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } },
  });
  vm.runInContext(core.slice(core.indexOf("function initTabs()"), core.indexOf("function parseJsonOrEmpty")) + "\ninitTabs();", context);
  return { tabs, panels, events };
}

test("取消未保存确认时保留当前目录和面板，不更新导航记忆", async () => {
  const { tabs, panels, events } = navigation(false);
  await tabs[1].click();
  assert.equal(tabs[0].classes.has("active"), true);
  assert.equal(panels[0].classes.has("active"), true);
  assert.equal(events.length, 0);
});

test("成功切换目录后才通知主导航更新选中态与面包屑", async () => {
  const { tabs, panels, events } = navigation(true);
  await tabs[1].click();
  assert.equal(tabs[0].classes.has("active"), false);
  assert.equal(tabs[1].classes.has("active"), true);
  assert.equal(panels[1].classes.has("active"), true);
  assert.equal(events[0].detail.name, "channels");
  assert.equal(events[0].type, "deep-agent-configuration-tab");
});

test("全局目录跨页先加载配置，且未授权点击不进入管理处理", () => {
  assert.match(main, /resourceNav\.append\(template\.content\)/);
  assert.match(main, /if \(!consoleAllowed\) \{ event\.stopImmediatePropagation\(\); return; \}/);
  assert.match(main, /await showPage\("configuration", \{ restoreTab: false \}\);\s*tab\.click\(\)/);
  assert.match(main, /rememberConfigurationTab\(event\.detail\.name\);\s*syncNavigation\(\)/);
});

test("导航宽度连续拖动，文字渐隐且宽度限制为56至200px", () => {
  const classes = new Set();
  const widths = [];
  const petStates = [];
  const element = { setAttribute() {}, toggleAttribute(name, active) { if (name === "collapsed") petStates.push(active); }, innerHTML: "" };
  const context = vm.createContext({
    document: {
      body: { classList: { toggle(name, active) { active ? classes.add(name) : classes.delete(name); } },
        style: { setProperty(name, value) { if (name === "--console-rail-width") widths.push(value); } } },
      getElementById() { return element; },
    },
    window: { dispatchEvent() {}, lucide: { createIcons() {} } }, Event: class {},
  });
  vm.runInContext(main.slice(main.indexOf("let expandedNavigationWidth"), main.indexOf("function toggleConsoleNavigation")), context);
  for (const width of [170, 110, 96, 30]) context.setConsoleNavigationWidth(width);
  assert.equal(classes.has("console-nav-collapsed"), true);
  for (const width of [125, 230]) context.setConsoleNavigationWidth(width);
  assert.deepEqual(widths, ["170px", "110px", "96px", "56px", "125px", "200px"]);
  assert.deepEqual(petStates, [false, false, true, true, false, false]);
  assert.equal(classes.has("console-nav-collapsed"), false);
});

test("Agent对话链接在捕获阶段接管，避免行操作阻止冒泡而新开页面", async () => {
  const config = await readFile(new URL("../../../console/configuration.js", import.meta.url), "utf8");
  const agents = await readFile(new URL("../../../console/configuration/agents.js", import.meta.url), "utf8");
  assert.match(config, /addEventListener\("click", async \(event\) => \{[\s\S]*?event.preventDefault\(\);[\s\S]*?await window.DeepAgentConsole\?\.showPage\("workspace"\);[\s\S]*?await window.DeepAgentConsoleChat\?\.selectAgent\(agentId\);[\s\S]*?\}, true\)/);
  assert.doesNotMatch(agents, /data-agent-chat[^>]*target="_blank"/);
});
