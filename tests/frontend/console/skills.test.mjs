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
  const listSource = source.slice(source.indexOf("renderList()"), source.indexOf("renderEmptyDetail"));
  assert.match(source, /\["内置", state\.skills\.filter/);
  assert.match(source, /\["自定义", state\.skills\.filter/);
  assert.doesNotMatch(listSource, /badge source-/);
  assert.doesNotMatch(source, /替换 ZIP/);
  assert.match(source, /data-skill-action="replace"><i data-lucide="upload"><\/i><span>上传<\/span>/);
  assert.match(source, /data-skill-more-trigger/);
  assert.match(source, /data-skill-more-menu role="menu" hidden/);
  assert.match(source, /data-skill-action="download"><i data-lucide="download"><\/i><span>导出<\/span>/);
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
  assert.doesNotMatch(source, /skill-detail-eyebrow/);
  assert.match(source, /skill-source-label/);
  assert.match(source, /skill-runtime-marker/);
  assert.match(source, /skill-detail-layout/);
  assert.match(source, /skill-detail-lane/);
  assert.match(source, /skill-resource-directory/);
  assert.match(source, /skill-resource-preview/);
  assert.doesNotMatch(source, /skill-manifest|<h4>Manifest<\/h4>/);
  assert.match(source, /data-read-resource="SKILL\.md"/);
  assert.match(source, /Skill 定义 · 完整正文/);
  assert.doesNotMatch(source, /控制台索引摘要|索引摘要/);
  assert.match(source, /apiFetch\(`\/api\/v1\/skills\/\$\{encodeURIComponent\(code\)\}\/resources/);
  assert.match(source, /selectedResourcePath: null/);
  assert.match(source, /data-resource-path=/);
  assert.match(source, /row\.classList\.toggle\("active", row\.dataset\.resourcePath === path\)/);
  assert.match(source, /if \(activeResourcePath\)/);
  assert.match(source, /this\.readResource\(skill\.code, activeResourcePath\)/);
  assert.doesNotMatch(source, /skill-field-label">说明/);
  assert.ok(source.indexOf('class="skill-resource-directory"') < source.indexOf('<aside class="skill-detail-lane skill-preview-lane">'));
  assert.ok(source.indexOf('class="skill-detail-header"') < source.indexOf('class="skill-detail-layout"'));
  assert.match(styles, /\.skill-admin-workspace[\s\S]*gap: 14px;[\s\S]*border: 0;/);
  assert.match(styles, /\.skill-list-pane\s*\{[^}]*padding: 0;[^}]*border: 0;[^}]*background: transparent;[^}]*box-shadow: none;/);
  assert.match(styles, /\.skill-detail-pane[\s\S]*border-radius: 14px/);
  assert.match(styles, /\.skill-detail-layout[\s\S]*grid-template-columns: minmax\(0, 1fr\) minmax\(360px, \.82fr\)/);
  assert.match(styles, /\.skill-detail-layout[\s\S]*align-items: stretch;[\s\S]*margin-top: 0;/);
  assert.match(styles, /\.skill-detail-lane[\s\S]*display: flex;[\s\S]*min-height: 100%;/);
  assert.match(styles, /\.skill-preview-lane\s*\{[^}]*padding: 0;[^}]*border: 0;[^}]*box-shadow: none;/);
  assert.match(styles, /\.skill-detail-header[\s\S]*margin-bottom: 16px;[\s\S]*border-bottom: 1px solid/);
  assert.match(styles, /\.skill-facts > div\s*\{[^}]*display: inline-flex;[^}]*border: 0;[^}]*background: transparent;/);
  assert.match(styles, /\.skill-resource-row\s*\{[^}]*border: 0;[^}]*border-radius: 9px;/);
  assert.match(styles, /\.skill-resource-row\.active\s*\{[^}]*background:[^}]*box-shadow: inset 3px 0 0 var\(--accent\)/);
  assert.match(styles, /\.skill-runtime-marker\.runtime-ready \.skill-runtime-dot/);
  assert.match(styles, /\.skill-section-heading/);
  assert.match(styles, /\.skill-preview-stats/);
  assert.match(styles, /\.skill-more-trigger\s*\{[^}]*border: 0 !important;[^}]*background: transparent !important;/);
  assert.match(styles, /\.skill-more-menu\s*\{[^}]*position: absolute;[^}]*min-width: 144px;/);
  assert.match(styles, /\.skill-more-menu\[hidden\] \{ display: none; \}/);
  assert.match(source, /data-lucide="ellipsis-vertical"/);
  assert.match(source, /previewStats\(content\)/);
  assert.match(source, /tokens: Math\.max\(1, Math\.ceil\(tokenWeight\)\)/);
  assert.doesNotMatch(source, /skill-detail-description/);
  assert.doesNotMatch(styles, /\.skill-manifest/);
  assert.doesNotMatch(styles, /\.skill-list-item\.active \{ box-shadow: inset 3px/);
  assert.match(styles, /@media \(max-width: 900px\)[\s\S]*\.skill-admin-workspace \{ grid-template-columns: minmax\(0, 1fr\); \}/);
});
