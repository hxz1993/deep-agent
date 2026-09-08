import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const indexHtml = await readFile(
  new URL("../../../console/index.html", import.meta.url),
  "utf8",
);
const mainSource = await readFile(
  new URL("../../../console/main.js", import.meta.url),
  "utf8",
);
const consoleCss = await readFile(
  new URL("../../../console/console.css", import.meta.url),
  "utf8",
);

test("产品版本只在顶部用户详情展示", () => {
  assert.match(
    indexHtml,
    /class="user-popover user-account-popover"[\s\S]*class="product-version" id="productVersion"[\s\S]*v—<\/strong>/,
  );
  assert.equal((indexHtml.match(/id="productVersion"/g) || []).length, 1);
  assert.match(mainSource, /serviceUrl\("info"\)/);
  assert.match(mainSource, /target\.textContent = `v\$\{version\}`/);
  assert.match(mainSource, /target\.textContent = "v—"/);
});

test("产品和数据库结构版本收进用户详情而连接状态保持只读", () => {
  assert.match(
    indexHtml,
    /<div class="console-brand-copy">[\s\S]*<span class="connection-pill" id="connStatus"[\s\S]*<button[^>]*id="currentUserBtn"[^>]*aria-expanded="false"[\s\S]*class="user-popover user-account-popover"[\s\S]*id="productVersion"[\s\S]*数据库结构[\s\S]*id="databaseSchemaVersion"/,
  );
  assert.doesNotMatch(indexHtml, /<(?:button|details)[^>]*id="connStatus"/);
  assert.doesNotMatch(indexHtml, /<details class="console-runtime-status"|runtime-status-popover/);
  assert.doesNotMatch(indexHtml, />数据库版本</);
  assert.match(consoleCss, /\.user-popover-runtime \{[^}]*grid-template-columns: 1fr;|\.user-popover-runtime \{[^}]*display: grid;[^}]*gap: 0;/);
  assert.match(consoleCss, /\.user-popover-runtime > span \{[^}]*display: flex;[^}]*justify-content: space-between;[^}]*border-radius: 0;[^}]*background: transparent;/);
});
