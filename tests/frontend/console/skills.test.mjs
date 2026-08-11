import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = await readFile(
  new URL("../../../frontend/console/configuration/skills.js", import.meta.url),
  "utf8",
);
const template = await readFile(
  new URL("../../../frontend/console/configuration-template.html", import.meta.url),
  "utf8",
);
const shell = await readFile(
  new URL("../../../frontend/console/index.html", import.meta.url),
  "utf8",
);

test("配置中心提供独立 Skill 页面而不是工具包分类", () => {
  assert.match(shell, /data-tab="skills"/);
  assert.match(template, /id="panel-skills"/);
  assert.match(template, /Skill 提供任务方法与资源，不会自动授予 MCP/);
  assert.match(source, /apiFetch\("\/api\/v1\/skills"\)/);
});

test("Skill 页面区分内置和上传包的可操作边界", () => {
  assert.match(source, /代码内置 · 只读/);
  assert.match(source, /上传安装/);
  assert.match(source, /替换 ZIP/);
  assert.match(source, /下载原包/);
  assert.match(source, /重新加载/);
  assert.match(source, /\/skills\/\$\{encodeURIComponent\(skill\.code\)\}\/reload/);
  assert.match(source, /运行文件已就绪/);
  assert.match(source, /运行文件同步失败/);
  assert.match(source, /当前版本只保存，不向模型披露、读取或执行/);
});

test("Skill 上传使用 multipart 且资源按安全 API 读取", () => {
  assert.match(source, /const form = new FormData\(\)/);
  assert.match(source, /form\.append\("file", file\)/);
  assert.match(source, /\/resources\/\$\{path\.split/);
  assert.match(source, /仅展示前 32 KiB/);
  assert.match(source, /二进制 Asset，当前不可由模型读取/);
  assert.match(source, /已保存，当前不可读取或执行/);
});
