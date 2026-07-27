import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(
  new URL("../../../frontend/console/configuration/core.js", import.meta.url),
  "utf8",
);

function loadSaveUi() {
  const toast = {
    textContent: "",
    style: {},
    classList: { add() {}, remove() {} },
  };
  const document = {
    addEventListener() {},
    getElementById(id) {
      return id === "toast" ? toast : null;
    },
  };
  const window = {};
  vm.runInContext(
    source,
    vm.createContext({
      document,
      location: { protocol: "http:", origin: "http://127.0.0.1:8000" },
      localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
      setTimeout,
      clearTimeout,
      URL,
      window,
    }),
  );
  return { ui: window.__ConfigurationSaveUI, toast };
}

test("保存成功后的刷新失败不会再伪装成保存失败", async () => {
  const { ui, toast } = loadSaveUi();
  const refreshed = await ui.refreshAfterSuccessfulSave("MCP 配置", async () => {
    throw new Error("列表接口超时");
  });

  assert.equal(refreshed, false);
  assert.equal(
    toast.textContent,
    "MCP 配置已保存，但页面重新加载失败：列表接口超时",
  );
});

test("保存后的刷新成功不产生额外错误", async () => {
  const { ui, toast } = loadSaveUi();
  const refreshed = await ui.refreshAfterSuccessfulSave("Agent", async () => {});

  assert.equal(refreshed, true);
  assert.equal(toast.textContent, "");
});

test("删除成功后的刷新失败保留删除成功语义", async () => {
  const { ui, toast } = loadSaveUi();
  const refreshed = await ui.refreshAfterSuccessfulMutation("MCP 已删除", async () => {
    throw new Error("列表接口超时");
  });

  assert.equal(refreshed, false);
  assert.equal(
    toast.textContent,
    "MCP 已删除，但页面重新加载失败：列表接口超时",
  );
});
