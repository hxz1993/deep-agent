import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const indexHtml = await readFile(
  new URL("../../../frontend/console/index.html", import.meta.url),
  "utf8",
);
const mainSource = await readFile(
  new URL("../../../frontend/console/main.js", import.meta.url),
  "utf8",
);

test("产品版本只在控制台顶部品牌区展示", () => {
  assert.match(
    indexHtml,
    /class="product-version" id="productVersion"[\s\S]*v—<\/span>/,
  );
  assert.equal((indexHtml.match(/id="productVersion"/g) || []).length, 1);
  assert.match(mainSource, /serviceUrl\("info"\)/);
  assert.match(mainSource, /target\.textContent = `v\$\{version\}`/);
  assert.match(mainSource, /target\.textContent = "v—"/);
});

test("数据库 revision 明确标注为数据库结构而非产品版本", () => {
  assert.match(
    indexHtml,
    /class="console-runtime-status"[\s\S]*数据库结构[\s\S]*id="databaseSchemaVersion"/,
  );
  assert.doesNotMatch(indexHtml, />数据库版本</);
});
