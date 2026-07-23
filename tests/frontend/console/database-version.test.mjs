import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(
  new URL("../../../frontend/console/configuration/core.js", import.meta.url),
  "utf8",
);

function loadVersionUi() {
  const window = {};
  const document = {
    addEventListener() {},
  };
  vm.runInContext(
    source,
    vm.createContext({
      document,
      location: { protocol: "http:", origin: "http://127.0.0.1:8000" },
      localStorage: { getItem() { return null; }, setItem() {} },
      setTimeout,
      clearTimeout,
      URL,
      window,
    }),
  );
  return window.__DatabaseSchemaVersionUI;
}

test("数据库结构版本移除数字 revision 的前导零", () => {
  const ui = loadVersionUi();

  assert.equal(ui.formatDatabaseSchemaVersion("020"), "数据库配置版本 V20");
  assert.equal(ui.formatDatabaseSchemaVersion(" 021 "), "数据库配置版本 V21");
});

test("非数字 revision 保留原始标识，缺失版本显示未知", () => {
  const ui = loadVersionUi();

  assert.equal(ui.formatDatabaseSchemaVersion("feature_head"), "数据库配置版本 Vfeature_head");
  assert.equal(ui.formatDatabaseSchemaVersion(null), "数据库配置版本未知");
});
