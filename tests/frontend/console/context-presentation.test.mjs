import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = (await readFile(new URL("../../../console/context.js", import.meta.url), "utf8"))
  .replace(/export /g, "");

test("应用覆盖、平台继承和无应用切换后，标题与欢迎语同步显示有效上下文", async () => {
  let overrides = {};
  let inherited = { app: "platform001", user: "user-a" };
  const node = () => ({ textContent: "", title: "", innerHTML: "", classList: { toggle() {} }, setAttribute() {}, removeAttribute() {} });
  const label = node();
  const trigger = { ...node(), querySelector: () => label };
  const prefix = node(), welcomeLabel = node(), note = node();
  const fields = new Map();
  const context = vm.createContext({
    window: { DeepAgentConsoleAPI: {
      loadContext: () => overrides,
      deriveSpaceFromApp: app => app.slice(0, -3),
      DEFAULT_LANGUAGE: "zh_CN",
      requestContext: async () => inherited,
      currentUser: async () => ({ user_id: "user-a", username: "测试用户" }),
    } },
    document: {
      body: node(),
      getElementById(id) { if (!fields.has(id)) fields.set(id, node()); return fields.get(id); },
      querySelectorAll: selector => ({
        "[data-open-context]": [trigger],
        "[data-empty-context-prefix]": [prefix],
        "[data-empty-context-label]": [welcomeLabel],
        "[data-empty-context-note]": [note],
      })[selector] || [],
    },
  });
  vm.runInContext(source, context);
  const controller = context.window.DeepAgentConsoleContext;
  await controller.refreshIdentity();
  assert.equal(label.textContent, "platform001");
  assert.equal(welcomeLabel.textContent, label.textContent);

  overrides = { app: "override002" };
  controller.render();
  assert.equal(label.textContent, "override002");
  assert.equal(prefix.textContent + welcomeLabel.textContent + note.textContent, "在应用 override002 的上下文中开始新对话。");
  assert.equal(controller.effectiveContext.space, "override");

  overrides = {};
  inherited = { user: "user-a" };
  await controller.refreshIdentity();
  assert.equal(label.textContent, "不分应用");
  assert.equal(welcomeLabel.textContent, label.textContent);
  assert.equal(prefix.textContent + welcomeLabel.textContent + note.textContent, "当前不分应用，无法调用 DeepFOS MCP 及相关平台工具。");
  assert.equal(controller.effectiveContext.app, null);
});
