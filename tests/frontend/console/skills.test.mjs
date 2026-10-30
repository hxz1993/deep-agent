import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";
import { parseSkillFrontmatter } from "../../../console/skill-frontmatter.js";

const source = await readFile(
  new URL("../../../console/configuration/skills.js", import.meta.url),
  "utf8",
);
const template = await readFile(
  new URL("../../../console/configuration-template.html", import.meta.url),
  "utf8",
);
const shell = await readFile(
  new URL("../../../console/index.html", import.meta.url),
  "utf8",
);
const styles = await readFile(
  new URL("../../../console/configuration/configuration.css", import.meta.url),
  "utf8",
);
const consoleStyles = await readFile(
  new URL("../../../console/console.css", import.meta.url),
  "utf8",
);

test("Skill 下载遵循服务端编码和版本文件名，错误响应头安全回退", () => {
  const context = { window: {}, document: { addEventListener() {} } };
  vm.runInNewContext(source, context);
  const filename = (header) => context.window.skillsAdmin.downloadFilename(
    { headers: { get: () => header } }, "example-skill.zip",
  );
  assert.equal(filename("attachment; filename*=UTF-8''example-skill-v1.1.0.zip"), "example-skill-v1.1.0.zip");
  assert.equal(filename("attachment; filename=deepagent-skills-export-20260909.zip"), "deepagent-skills-export-20260909.zip");
  assert.equal(filename("attachment; filename*=UTF-8''example-skill-v1.0%2Bbuild.zip"), "example-skill-v1.0+build.zip");
  for (const header of [null, "", "attachment; filename*=UTF-8''%ZZ.zip", 'attachment; filename="../secret.zip"',
    "attachment; filename*=UTF-8''bad%0Aname.zip"]) {
    assert.equal(filename(header), "example-skill.zip");
  }
  assert.match(source, /anchor\.download = this\.downloadFilename\(response, fallbackName\)/);
  assert.doesNotMatch(source, /skill\.file_name \|\|/);
});

function installHarness(replies, timers = {}) {
  const elements = new Map();
  const calls = [];
  const context = {
    window: { AdminFormFeedback: { formatDetail: (value) => String(value) } },
    document: { addEventListener() {}, getElementById(id) {
      if (!elements.has(id)) elements.set(id, { textContent: "", innerHTML: "", value: "", disabled: false, showModal() { this.open = true; } });
      return elements.get(id);
    } },
    escapeHtml: (value) => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;"),
    serviceUrl: (path) => path,
    FormData: class { append(key, value) { this[key] = value; } },
    AbortController, setTimeout, clearTimeout, ...timers,
    daFetch: async (path, options) => {
      calls.push({ path, ...options });
      const reply = replies.shift();
      if (reply instanceof Error) throw reply;
      return typeof reply === "function" ? reply(options) : reply;
    },
  };
  vm.runInNewContext(source, context);
  const admin = context.window.skillsAdmin;
  let refreshes = 0;
  admin.refresh = async () => { refreshes++; };
  return { admin, calls, elements, refreshes: () => refreshes };
}
const installReply = (status, code = "example-skill") => ({ ok: status === 201, status, json: async () => ({ skill: { code }, detail: "test error" }) });

test("上传约束集中于问号提示，安装包横向卡片以图标文字区分五种状态", () => {
  const h = installHarness([]);
  h.admin.installQueue = ["queued", "uploading", "done", "error", "unknown"].map((status) => ({
    status, file: { name: "<long-package>.zip", size: 2048 }, message: "<safe detail>",
  }));
  h.admin.renderInstallQueue();
  const html = h.elements.get("skillInstallResults").innerHTML;
  for (const label of ["待安装", "安装中", "已安装", "未安装", "待确认", "2.0 KiB"]) assert.ok(html.includes(label));
  assert.ok(html.includes("&lt;long-package>.zip"));
  assert.ok(html.includes("&lt;safe detail>"));
  assert.match(template, /<span>Skill 安装包<\/span><small class="field-subtitle">ZIP · 每包 ≤ 10 MiB<\/small>/);
  assert.match(template, /id="skillInstallHelp" role="tooltip">支持多包新建，已有编码不覆盖；更新请用详情“上传”。ZIP 须包含 SKILL.md，每包不超过 10 MiB；解压后不超过 50 MiB、256 个文件。“导出全部”请先解压，再选择其中的包。<\/span>/);
  assert.match(template, /<strong>选择 ZIP 文件<\/strong><small>可多选<\/small>/);
  assert.doesNotMatch(template, /skill-install-note-number|skill-install-note-label/);
  assert.match(consoleStyles, /\.skill-install-picker \{[^}]*width: 100%;[^}]*min-height: 88px;/);
  assert.match(consoleStyles, /\.skill-install-card \{[^}]*display: flex;[^}]*border: 0;/);
  assert.match(consoleStyles, /prefers-reduced-motion: reduce/);
});

test("批量安装逐包 POST，冲突不覆盖，失败不回滚其他成功包", async () => {
  const h = installHarness([installReply(201, "one"), installReply(409), installReply(422), installReply(201, "four")]);
  h.admin.openInstallDialog();
  assert.equal(h.elements.get("skillInstallDialog").open, true);
  assert.equal(h.elements.get("skillInstallMessage").textContent, "");
  h.admin.selectInstallFiles(["one.zip", "two.zip", "three.zip", "four.zip"].map((name) => ({ name, size: 12 })));
  await h.admin.installSelected();
  assert.deepEqual(Array.from(h.admin.installQueue, (item) => item.status), ["done", "error", "error", "done"]);
  assert.match(h.admin.installQueue[1].message, /未覆盖/);
  assert.equal(h.calls.length, 4);
  assert.ok(h.calls.every((call) => call.method === "POST" && call.path === "/api/v1/skills"));
  assert.equal(h.refreshes(), 1);
  await h.admin.installSelected();
  assert.equal(h.calls.length, 4, "完成后再次点击不重传");
});

test("安装校验、文件名转义、权限中断与未知网络结果", async () => {
  const h = installHarness([new Error("network"), installReply(403)]);
  h.admin.selectInstallFiles([{ name: "<bad>.txt", size: 1 }, { name: "big.zip", size: 11 * 1024 * 1024 },
    { name: "one.zip", size: 1 }, { name: "two.zip", size: 1 }, { name: "three.zip", size: 1 }]);
  assert.match(h.elements.get("skillInstallResults").innerHTML, /&lt;bad>/);
  await h.admin.installSelected();
  assert.equal(h.calls.length, 2);
  assert.equal(h.admin.installQueue[2].status, "unknown");
  assert.match(h.admin.installQueue[4].message, /未上传/);
  assert.match(h.elements.get("skillInstallMessage").textContent, /结果待确认 1/);
  assert.equal(h.admin.installing, false);
});

test("安装期间阻止重复执行和更换文件，弹窗只允许完成后关闭", async () => {
  let release;
  const h = installHarness([() => new Promise((resolve) => { release = resolve; })]);
  h.admin.selectInstallFiles([{ name: "one.zip", size: 1 }]);
  const pending = h.admin.installSelected();
  await h.admin.installSelected();
  h.admin.selectInstallFiles([{ name: "other.zip", size: 1 }]);
  assert.equal(h.admin.installQueue[0].file.name, "one.zip");
  assert.equal(h.elements.get("dismissSkillInstallBtn").disabled, true);
  release(installReply(201));
  await pending;
  assert.equal(h.calls.length, 1);
  assert.equal(h.elements.get("dismissSkillInstallBtn").disabled, false);
  assert.match(source, /if \(this\.installing\) event\.preventDefault\(\)/);
  assert.match(template, /id="skillInstallFiles"[^>]*multiple/);
});

test("安装返回网关或服务错误时标记待确认，不能声称未安装", async () => {
  const h = installHarness([installReply(502), installReply(500)]);
  h.admin.selectInstallFiles([{ name: "one.zip", size: 1 }, { name: "two.zip", size: 1 }]);
  await h.admin.installSelected();
  assert.deepEqual(Array.from(h.admin.installQueue, (item) => item.status), ["unknown", "unknown"]);
  assert.equal(h.refreshes(), 1);
  assert.equal(h.calls.length, 2);
});

test("上传超时解锁弹框、保留待确认状态并停止后续包，不自动重试", async () => {
  let expire;
  const h = installHarness([(options) => new Promise((_, reject) => {
    options.signal?.addEventListener("abort", () => reject(new Error("aborted")), { once: true });
  })], { setTimeout: (fn) => { expire = fn; return 1; }, clearTimeout() {} });
  h.admin.selectInstallFiles([{ name: "one.zip", size: 1 }, { name: "two.zip", size: 1 }]);
  const pending = h.admin.installSelected();
  assert.equal(typeof expire, "function");
  expire();
  await pending;
  assert.equal(h.calls.length, 1);
  assert.equal(h.admin.installQueue[0].status, "unknown");
  assert.match(h.admin.installQueue[1].message, /未上传/);
  assert.equal(h.elements.get("dismissSkillInstallBtn").disabled, false);
});

test("安装完成后列表刷新等待不锁住弹框", async () => {
  let release;
  const h = installHarness([installReply(201)]);
  h.admin.refresh = () => new Promise((resolve) => { release = resolve; });
  h.admin.selectInstallFiles([{ name: "one.zip", size: 1 }]);
  const pending = h.admin.installSelected();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(h.admin.installing, false);
  assert.equal(h.elements.get("dismissSkillInstallBtn").disabled, false);
  release(); await pending;
});

test("配置中心提供独立 Skill 页面而不是工具包分类", () => {
  assert.match(shell, /data-tab="skills"/);
  assert.match(template, /id="panel-skills"/);
  assert.match(template, /Skill 提供任务方法与资源，不会自动授予 MCP/);
  assert.match(source, /apiFetch\("\/api\/v1\/skills", \{ signal:/);
});

test("Skill 页面区分内置和上传包的可操作边界", () => {
  const listSource = source.slice(source.indexOf("  renderList("), source.indexOf("  orderedReferences("));
  assert.match(listSource, /skill\.source === "builtin" \? '<span class="skill-source-label">内置<\/span>' : ""/);
  assert.doesNotMatch(listSource, /badge source-/);
  assert.doesNotMatch(source, /替换 ZIP/);
  assert.match(source, /data-skill-action="replace"><i data-lucide="upload"><\/i><span>上传<\/span>/);
  assert.match(source, /data-skill-more-trigger/);
  assert.match(source, /data-skill-more-menu role="menu" hidden/);
  assert.match(source, /class="btn skill-direct-action"[^>]*data-skill-action="download"[^>]*aria-label="下载 Skill 包"[^>]*>[^<]*<i[^>]*><\/i><span>下载<\/span>/);
  assert.doesNotMatch(source, /role="menuitem" data-skill-action="download"/);
  assert.match(template, /id="exportAllSkillsBtn"><i data-lucide="download"><\/i>导出全部/);
  assert.match(source, /"\/api\/v1\/skills\/export"/);
  assert.match(source, /重新加载/);
  assert.match(source, /\/skills\/\$\{encodeURIComponent\(skill\.code\)\}\/reload/);
  assert.match(source, /运行文件已就绪/);
  assert.match(source, /运行文件同步失败/);
  assert.doesNotMatch(source, /script_execution_status/);
  assert.match(source, /暂无 Skill，可上传 ZIP 安装/);
  assert.doesNotMatch(source, /state\.skills\.find\(\(item\) => item\.source !== "builtin"\)/);
});

test("Skill 上传使用 multipart 且资源按安全 API 读取", () => {
  assert.match(source, /const form = new FormData\(\)/);
  assert.match(source, /form\.append\("file", file\)/);
  assert.match(source, /\/resources\/\$\{path\.split/);
  assert.match(source, /\["docx", "xlsx"\]\.includes\(resource\.read_format\)/);
  assert.doesNotMatch(source, /已保存，当前不可读取或执行/);
});

test("Skill 预览续读按钮传递行号和行内字符位置", () => {
  assert.match(source, /data-preview-next/);
  assert.match(source, /offset: page\.next_offset, char_offset: page\.next_char_offset \?\? 0/);
});

test("Skill Markdown 资源复用对话渲染器，其他文本保持原文预览", () => {
  assert.match(source, /const isMarkdown = \/\\\.md\$\/i\.test\(path\)/);
  assert.match(source, /window\.DeepAgentMarkdown\?\.renderBody\(markdownDocument\?\.body \?\? page\.content\)/);
  assert.match(source, /window\.DeepAgentMarkdown\?\.renderDiagrams\(preview\)/);
  assert.match(source, /: `<pre>\$\{escapeHtml\(page\.content\)\}<\/pre>`/);
  assert.match(styles, /\.skill-preview-markdown\s*\{[\s\S]*font-size: 12px/);
  assert.match(styles, /\.skill-preview-markdown \.markdown-body/);
});

test("Skill Markdown frontmatter 展示在正文预览顶部且不混入正文解析", () => {
  const parsed = parseSkillFrontmatter(`---\nname: deepagent-diagnostics\ndescription: Diagnose runtime problems.\n---\n# DeepAgent diagnostics\n\n正文`);
  assert.equal(parsed.hasFrontmatter, true);
  assert.deepEqual(parsed.entries, [
    { key: "name", value: "deepagent-diagnostics" },
    { key: "description", value: "Diagnose runtime problems." },
  ]);
  assert.equal(parsed.body, "# DeepAgent diagnostics\n\n正文");
  assert.match(source, /skill-metadata-card/);
  assert.doesNotMatch(source, /skill-metadata-title/);
  assert.match(source, /DeepAgentSkillFrontmatter\?\.parseSkillFrontmatter/);
  assert.match(source, /renderBody\(markdownDocument\?\.body/);
  assert.match(source, /<div class="skill-preview-markdown">\$\{this\.renderMetadataCard\(markdownDocument\?\.entries\)\}\$\{window\.DeepAgentMarkdown/);
  assert.match(styles, /\.skill-metadata-card\s*\{/);
  assert.match(styles, /\.skill-metadata-card\s*\{[^}]*border-bottom:/);
  assert.match(styles, /\.skill-metadata-grid > div\s*\{[^}]*grid-template-columns: minmax\(72px, 20%\)/);
});

test("Skill 页面固定在配置视口内并把溢出交给目录和预览区", () => {
  assert.match(styles, /#configurationHost #panel-skills\.active\s*\{[^}]*height: 100%;[^}]*overflow: hidden;/);
  assert.match(styles, /\.skill-detail-pane\s*\{[^}]*display: flex;[^}]*overflow: hidden;/);
  assert.match(styles, /\.skill-detail-main\s*\{[^}]*overflow: auto;/);
  assert.match(styles, /\.skill-preview-markdown\s*\{[^}]*overflow: auto;/);
});

test("同内容包在平台原文件丢失后会提示恢复而不是误报无变化", () => {
  assert.match(source, /result\.status === "repaired"/);
  assert.match(source, /Skill 原包与运行文件已恢复/);
});

test("Skill 使用紧凑表格与第二页文件详情", () => {
  assert.match(source, /data-table skill-table/);
  assert.doesNotMatch(source, /skill-detail-eyebrow/);
  assert.match(source, /skill-source-label/);
  assert.match(source, /skill-runtime-marker/);
  assert.match(source, /skill-detail-layout/);
  assert.match(source, /skill-detail-lane/);
  assert.match(source, /skill-resource-directory/);
  assert.match(source, /skill-resource-preview/);
  assert.doesNotMatch(source, /skill-manifest|<h4>Manifest<\/h4>/);
  assert.match(source, /data-read-resource="SKILL\.md"/);
  assert.match(source, /<code title="SKILL\.md">SKILL\.md<\/code>/);
  assert.doesNotMatch(source, /控制台索引摘要|索引摘要/);
  assert.match(source, /const url = `\/api\/v1\/skills\/\$\{encodeURIComponent\(code\)\}\/resources/);
  assert.match(source, /await apiFetch\(url/);
  assert.match(source, /selectedResourcePath: null/);
  assert.match(source, /selectedResource\?\.path \|\| "SKILL\.md"/);
  assert.match(source, /data-resource-path=/);
  assert.match(source, /row\.classList\.toggle\("active", row\.dataset\.resourcePath === path\)/);
  assert.match(source, /if \(activeResourcePath\)/);
  assert.match(source, /this\.readResource\(skill\.code, activeResourcePath\)/);
  assert.doesNotMatch(source, /skill-field-label">说明/);
  assert.ok(source.indexOf('class="skill-resource-directory"') < source.indexOf('<aside class="skill-detail-lane skill-preview-lane">'));
  assert.ok(source.indexOf('class="skill-detail-header"') < source.indexOf('class="skill-detail-layout"'));
  assert.match(template, /id="skillListView"/);
  assert.match(template, /skill-detail-pane hidden/);
  assert.match(source, /data-skill-back/);
  assert.match(styles, /\.skill-detail-pane\s*\{[^}]*border: 0;[^}]*border-radius: 0/);
  assert.match(styles, /\.skill-detail-layout[\s\S]*grid-template-columns: 320px minmax\(0, 1fr\)/);
  assert.match(styles, /\.skill-detail-layout[\s\S]*align-items: stretch;[\s\S]*margin-top: 0;/);
  assert.match(styles, /\.skill-detail-lane[\s\S]*display: flex;[\s\S]*min-height: 100%;/);
  assert.match(styles, /\.skill-preview-lane\s*\{[^}]*padding: 0;[^}]*border: 0;[^}]*box-shadow: none;/);
  assert.match(styles, /\.skill-detail-header[\s\S]*margin-bottom: 20px;[\s\S]*border: 0;/);
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
  assert.match(styles, /@media \(max-width: 1000px\)[\s\S]*\.skill-detail-layout \{[\s\S]*grid-template-columns: minmax\(0, 1fr\);/);
});
