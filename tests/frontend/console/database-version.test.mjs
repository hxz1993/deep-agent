import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(
  new URL("../../../frontend/console/configuration/core.js", import.meta.url),
  "utf8",
);
const indexHtml = await readFile(
  new URL("../../../frontend/console/index.html", import.meta.url),
  "utf8",
);
const consoleCss = await readFile(
  new URL("../../../frontend/console/console.css", import.meta.url),
  "utf8",
);
const faviconSvg = await readFile(
  new URL("../../../frontend/console/favicon.svg", import.meta.url),
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

  assert.equal(ui.formatDatabaseSchemaVersion("020"), "V20");
  assert.equal(ui.formatDatabaseSchemaVersion(" 021 "), "V21");
});

test("非数字 revision 保留原始标识，缺失版本显示未知", () => {
  const ui = loadVersionUi();

  assert.equal(ui.formatDatabaseSchemaVersion("feature_head"), "Vfeature_head");
  assert.equal(ui.formatDatabaseSchemaVersion(null), "—");
});

test("数据库版本使用独立醒目徽标", () => {
  assert.match(
    indexHtml,
    /class="database-version"[\s\S]*数据库版本[\s\S]*id="databaseSchemaVersion"/,
  );
  assert.match(consoleCss, /\.database-version strong\s*\{/);
  assert.match(consoleCss, /background:\s*var\(--accent\)/);
});

test("浏览器图标复用标题栏机器人图标", () => {
  assert.match(
    indexHtml,
    /rel="icon" type="image\/svg\+xml" href="\.\/favicon\.svg\?v=20260728-1"/,
  );
  assert.match(faviconSvg, /fill="#6366f1"/i);
  for (const botPath of [
    "M12 8V4H8",
    "M2 14h2",
    "M20 14h2",
    "M15 13v2",
    "M9 13v2",
  ]) {
    assert.ok(faviconSvg.includes(botPath));
  }
  assert.match(faviconSvg, /<rect width="16" height="12" x="4" y="8" rx="2"\/>/);
});
