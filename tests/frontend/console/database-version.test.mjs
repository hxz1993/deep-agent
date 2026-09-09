import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(
  new URL("../../../console/configuration/core.js", import.meta.url),
  "utf8",
);
const indexHtml = await readFile(
  new URL("../../../console/index.html", import.meta.url),
  "utf8",
);
const consoleCss = await readFile(
  new URL("../../../console/console.css", import.meta.url),
  "utf8",
);
const faviconSvg = await readFile(
  new URL("../../../console/favicon.svg", import.meta.url),
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

test("顶部连接状态不可展开且版本位于用户详情", () => {
  assert.match(
    indexHtml,
    /<div class="console-brand-copy">[\s\S]*<span class="connection-pill" id="connStatus"[\s\S]*<button[^>]*id="currentUserBtn"[^>]*aria-expanded="false"[\s\S]*class="user-popover user-account-popover"[\s\S]*id="productVersion"[\s\S]*id="databaseSchemaVersion"/,
  );
  assert.equal((indexHtml.match(/id="connStatus"/g) || []).length, 1);
  assert.doesNotMatch(indexHtml, /<(?:button|details)[^>]*id="connStatus"/);
  assert.doesNotMatch(indexHtml, /<details class="console-runtime-status"|runtime-status-popover/);
  assert.doesNotMatch(indexHtml, /class="resource-title"[\s\S]{0,240}databaseSchemaVersion/);
  assert.match(consoleCss, /\.user-account-popover\s*\{/);
  assert.match(consoleCss, /\.runtime-schema-version\s*\{/);
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
