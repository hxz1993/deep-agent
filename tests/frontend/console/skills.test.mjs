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
const styles = await readFile(
  new URL("../../../frontend/console/configuration/configuration.css", import.meta.url),
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
  assert.match(source, /data-skill-action="download">导出/);
  assert.match(template, /id="exportAllSkillsBtn"/);
  assert.match(source, /"\/api\/v1\/skills\/export"/);
  assert.match(source, /重新加载/);
  assert.match(source, /\/skills\/\$\{encodeURIComponent\(skill\.code\)\}\/reload/);
  assert.match(source, /运行文件已就绪/);
  assert.match(source, /运行文件同步失败/);
  assert.doesNotMatch(source, /script_execution_status/);
});

test("Skill 上传使用 multipart 且资源按安全 API 读取", () => {
  assert.match(source, /const form = new FormData\(\)/);
  assert.match(source, /form\.append\("file", file\)/);
  assert.match(source, /\/resources\/\$\{path\.split/);
  assert.match(source, /Word 可读/);
  assert.match(source, /Excel 可读/);
  assert.doesNotMatch(source, /已保存，当前不可读取或执行/);
});

test("同内容包在平台原文件丢失后会提示恢复而不是误报无变化", () => {
  assert.match(source, /result\.status === "repaired"/);
  assert.match(source, /Skill 原包与运行文件已恢复/);
});

test("Skill 使用左右分离的轻卡片列表与表单详情", () => {
  assert.match(source, /skill-list-card-head/);
  assert.match(source, /skill-detail-eyebrow/);
  assert.match(source, /skill-detail-summary/);
  assert.match(styles, /\.skill-admin-workspace[\s\S]*gap: 14px;[\s\S]*border: 0;/);
  assert.match(styles, /\.skill-list-pane\s*\{[^}]*padding: 0;[^}]*border: 0;[^}]*background: transparent;[^}]*box-shadow: none;/);
  assert.match(styles, /\.skill-detail-pane[\s\S]*border-radius: 14px/);
  assert.doesNotMatch(styles, /\.skill-list-item\.active \{ box-shadow: inset 3px/);
  assert.match(styles, /@media \(max-width: 900px\)[\s\S]*\.skill-admin-workspace \{ grid-template-columns: minmax\(0, 1fr\); \}/);
});
