import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(new URL("../../../console/configuration/global-settings.js", import.meta.url), "utf8");
const template = await readFile(new URL("../../../console/configuration-template.html", import.meta.url), "utf8");
const css = await readFile(new URL("../../../console/configuration/configuration.css", import.meta.url), "utf8");
const consoleCss = await readFile(new URL("../../../console/console.css", import.meta.url), "utf8");
const providers = [
  { provider: "paddle", label: "PaddleOCR", model: "PaddleOCR-VL-1.6", formats: ["PDF", "PNG"], limits: "服务额度以账号为准" },
  { provider: "glm", label: "GLM-OCR", model: "glm-ocr", formats: ["PDF", "PNG", "JPEG"], limits: "单文件最多 100 页" },
  { provider: "mineru", label: "MinerU", model: "vlm", formats: ["PDF", "DOCX"], limits: "单文件最多 200 页" },
];
const escapeHtml = value => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
const plain = value => JSON.parse(JSON.stringify(value));

function harness(value = { mode: "unconfigured" }) {
  const nodes = new Map(), requests = [], toasts = [], events = [];
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, {
      value: "", disabled: false, open: false, dataset: {},
      classList: { toggle() {} }, closest() { return null; },
      close() { this.open = false; }, showModal() { this.open = true; },
    });
    return nodes.get(id);
  };
  const context = vm.createContext({
    window: { dispatchEvent: event => events.push(event.type) }, Event, AbortController, escapeHtml,
    document: {
      addEventListener() {}, getElementById: node,
      querySelectorAll(selector) {
        const radios = providers.map(provider => Object.assign(node(`radio-${provider.provider}`), { name: "documentParsingProvider", value: provider.provider }));
        return selector.includes("Form") ? [...radios, ...["documentParsingApiKey", "saveDocumentParsingBtn", "cancelDocumentParsingBtn", "testDocumentParsingBtn"].map(node)] : radios;
      },
    },
    apiFetch: (path, options) => new Promise((resolve, reject) => requests.push({ path, options, resolve, reject })),
    showToast: (...args) => toasts.push(args),
  });
  vm.runInContext(source, context);
  context.window.globalSettingsAdmin.documentParsing = { value, providers };
  return { context, node, requests, toasts, events, ui: context.window.__GlobalSettingsUI };
}

test("文档配置复用工具设置卡、密钥控件与一页收费测试说明", () => {
  assert.match(template, /globalSettingsDocumentParsing[\s\S]*?<h3>文档解析<\/h3>/);
  assert.match(template, /documentParsingApiKey[^>]*type="password"/);
  assert.match(template, /消耗供应商额度/);
  assert.match(template, /关闭仅停止本地等待/);
  assert.doesNotMatch(template, /name="documentParsingModel"|精准档位|轻量级解析/);
});

test("文档编辑器独立布局不拆开全局设置标题栏的公共选择器", () => {
  assert.match(css, /\.global-settings-section-heading,\s*\.global-settings-runtime-toolbar\s*\{\s*display: flex;/);
  assert.match(css, /dialog#documentParsingSettingsDialog\s*\{\s*height: min\(540px/);
  assert.match(css, /#documentParsingSettingsDialog \.modal-body\s*\{ overflow-y: auto; \}/);
});

test("文档解析与网络搜索位于同一工具设置卡片的两行", () => {
  assert.match(template, /<div class="global-settings-tool-card">\s*<section class="global-settings-section" id="globalSettingsWebSearch">[\s\S]*?<section class="global-settings-section" id="globalSettingsDocumentParsing">/);
  for (const id of ["globalSettingsWebSearch", "globalSettingsDocumentParsing"]) {
    assert.match(template, new RegExp(`class="global-settings-section" id="${id}"`));
  }
  assert.equal((template.match(/class="global-settings-tool-card"/g) || []).length, 1);
  assert.match(consoleCss, /#configurationHost \.global-settings-tool-card \{[^}]*border: 1px solid var\(--console-card-border\);[^}]*border-radius: 8px;/);
  assert.doesNotMatch(consoleCss, /\.global-settings-tool-card > \.global-settings-section \+ \.global-settings-section \{[^}]*border-top:/);
  assert.match(consoleCss, /#configurationHost \.global-settings-tool-card \.global-settings-section-title \{[^}]*flex-direction: column;[^}]*align-items: flex-start;/);
  assert.match(consoleCss, /#configurationHost \.global-settings-tool-card \.global-settings-section-actions \{ flex-shrink: 0;/);
  assert.doesNotMatch(consoleCss, /#globalSettingsWebSearch/);
});

test("摘要和支持格式取后端元数据，默认草稿使用目录首项", () => {
  const h = harness();
  h.ui.openDocumentParsingSettings();
  assert.equal(h.node("documentParsingSettingsDialog").open, true);
  assert.equal(h.node("documentParsingModel").textContent, "PaddleOCR-VL-1.6");
  assert.equal(h.node("documentParsingCapabilities").textContent, "支持格式：PDF、PNG。服务额度以账号为准");
  assert.equal(h.ui.documentParsingSummaryLabel({ value: { mode: "provider", provider: "paddle" }, providers }), "PaddleOCR");
  h.ui.selectDocumentParsingProvider("glm");
  assert.equal(h.node("documentParsingModel").textContent, "glm-ocr");
  assert.match(h.node("documentParsingCapabilities").textContent, /100 页/);
});

test("供应商按后端稳定性顺序显示，首项为新草稿默认且不覆盖已保存选择", () => {
  const ranked = [providers[1], providers[2], providers[0]];
  const h = harness();
  h.context.window.globalSettingsAdmin.documentParsing.providers = ranked;
  h.ui.openDocumentParsingSettings();
  const options = h.node("documentParsingProviderOptions").innerHTML;
  assert.ok(options.indexOf('value="glm"') < options.indexOf('value="mineru"'));
  assert.ok(options.indexOf('value="mineru"') < options.indexOf('value="paddle"'));
  assert.equal(h.node("documentParsingModel").textContent, "glm-ocr");
  h.node("documentParsingApiKey").value = "new-ranked-key";
  assert.equal(h.ui.currentDocumentParsingDraft().value.provider, "glm");

  const saved = harness({ mode: "provider", provider: "paddle", api_key_set: true });
  saved.context.window.globalSettingsAdmin.documentParsing.providers = ranked;
  saved.ui.openDocumentParsingSettings();
  assert.equal(saved.node("documentParsingModel").textContent, "PaddleOCR-VL-1.6");
  assert.equal(saved.ui.currentDocumentParsingDraft().value.provider, "paddle");
});

test("供应商目录为空时不虚构默认供应商", () => {
  const h = harness();
  h.context.window.globalSettingsAdmin.documentParsing.providers = [];
  h.ui.openDocumentParsingSettings();
  assert.equal(h.node("testDocumentParsingBtn").disabled, true);
  assert.deepEqual(plain(h.ui.currentDocumentParsingDraft()), { version: 1, value: { mode: "unconfigured" } });
});

test("切供应商保留各自草稿；保存只发送当前一家，关闭清除所有明文草稿", () => {
  const h = harness();
  h.ui.openDocumentParsingSettings();
  h.node("documentParsingApiKey").value = "paddle-new";
  h.ui.selectDocumentParsingProvider("glm");
  assert.equal(h.node("documentParsingApiKey").value, "");
  h.node("documentParsingApiKey").value = "glm-new";
  h.ui.selectDocumentParsingProvider("paddle");
  assert.equal(h.node("documentParsingApiKey").value, "paddle-new");
  assert.deepEqual(plain(h.ui.currentDocumentParsingDraft()), { version: 1, value: { mode: "provider", provider: "paddle", api_key: "paddle-new" } });
  h.ui.selectDocumentParsingProvider("unconfigured");
  assert.deepEqual(plain(h.ui.currentDocumentParsingDraft()), { version: 1, value: { mode: "unconfigured" } });
  h.ui.closeDocumentParsingSettings();
  h.ui.openDocumentParsingSettings();
  assert.equal(h.node("documentParsingApiKey").value, "");
  h.ui.selectDocumentParsingProvider("paddle");
  assert.equal(h.node("documentParsingApiKey").value, "");
});

test("原密钥脱敏值或留空仅同一家可保留，不发送占位值", () => {
  const h = harness({ mode: "provider", provider: "paddle", api_key_set: true, api_key_masked: "test-****-mask" });
  h.ui.openDocumentParsingSettings();
  assert.equal(h.node("documentParsingApiKey").value, "test-****-mask");
  assert.deepEqual(plain(h.ui.currentDocumentParsingDraft()), { version: 1, value: { mode: "provider", provider: "paddle" } });
  h.node("documentParsingApiKey").value = "";
  assert.equal(Object.hasOwn(h.ui.currentDocumentParsingDraft().value, "api_key"), false);
  h.ui.selectDocumentParsingProvider("glm");
  assert.throws(() => h.ui.currentDocumentParsingDraft(), /请输入当前供应商/);
});

test("关闭取消测试、迟到结果不能覆盖新测试，错误反馈保留供应商原因", async () => {
  const h = harness();
  h.ui.openDocumentParsingSettings();
  h.node("documentParsingApiKey").value = "fake-test-key";
  const old = h.ui.runDocumentParsingOperation("test");
  assert.equal(h.node("cancelDocumentParsingBtn").disabled, false);
  h.ui.closeDocumentParsingSettings();
  assert.equal(h.requests[0].options.signal.aborted, true);
  h.ui.openDocumentParsingSettings();
  h.node("documentParsingApiKey").value = "fake-next-key";
  const next = h.ui.runDocumentParsingOperation("test");
  h.requests[0].resolve({ ok: true, message: "旧结果" });
  await old;
  assert.equal(h.node("documentParsingTestFeedback").dataset.status, "testing");
  assert.equal(h.node("saveDocumentParsingBtn").disabled, true);
  h.requests[1].resolve({ ok: false, error_code: "credentials_invalid", message: "API Key 无效" });
  await next;
  assert.equal(h.node("documentParsingTestFeedbackText").textContent, "API Key 无效");
  assert.equal(h.node("saveDocumentParsingBtn").disabled, false);
});

test("保存锁定关闭及重复操作，成功回读，刷新失败不会声称保存失败", async () => {
  const h = harness();
  h.ui.openDocumentParsingSettings();
  h.node("documentParsingApiKey").value = "fake-test-key";
  const saving = h.ui.runDocumentParsingOperation("save");
  h.ui.closeDocumentParsingSettings();
  await h.ui.runDocumentParsingOperation("save");
  assert.equal(h.requests.length, 1);
  assert.equal(h.node("documentParsingSettingsDialog").open, true);
  h.requests[0].resolve({ value: { mode: "provider", provider: "paddle", api_key_set: true }, providers });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.node("documentParsingSettingsDialog").open, false);
  assert.equal(h.requests[1].path, "/api/v1/global-settings/document-parsing");
  h.requests[1].reject(new Error("网络中断"));
  await saving;
  assert.deepEqual(h.events, ["document-parsing-settings-changed"]);
  assert.match(h.toasts[0][0], /已保存，刷新失败/);
  assert.equal(h.node("documentParsingApiKey").value, "");
});

test("保存失败保留草稿并解锁，不执行额外解析", async () => {
  const h = harness();
  h.ui.openDocumentParsingSettings();
  h.node("documentParsingApiKey").value = "fake-test-key";
  const pending = h.ui.runDocumentParsingOperation("save");
  assert.equal(h.requests[0].options.method, "PUT");
  h.requests[0].reject(new Error("禁止修改"));
  await pending;
  assert.equal(h.node("documentParsingSettingsDialog").open, true);
  assert.equal(h.node("documentParsingApiKey").value, "fake-test-key");
  assert.equal(h.node("saveDocumentParsingBtn").disabled, false);
  assert.equal(h.node("documentParsingTestFeedbackText").textContent, "禁止修改");
  assert.equal(h.requests.length, 1);
  assert.deepEqual(h.events, []);
});

test("供应商显示名转义，服务格式说明使用纯文本", () => {
  const h = harness();
  h.context.window.globalSettingsAdmin.documentParsing.providers[0] = { ...providers[0], label: "<img onerror=bad>", limits: "<script>bad</script>" };
  h.ui.openDocumentParsingSettings();
  assert.doesNotMatch(h.node("documentParsingProviderOptions").innerHTML, /<img|<script/);
  assert.match(h.node("documentParsingProviderOptions").innerHTML, /&lt;img/);
  assert.match(h.node("documentParsingCapabilities").textContent, /<script>/);
});

 test("MinerU 禁选，测试结束解锁后仍禁选；旧配置不被静默替换", async () => {
  const h = harness();
  h.ui.openDocumentParsingSettings();
  assert.match(h.node("documentParsingProviderOptions").innerHTML, /value="mineru" disabled/);
  assert.equal(h.node("radio-mineru").disabled, true);
  h.ui.selectDocumentParsingProvider("mineru");
  assert.equal(h.node("documentParsingModel").textContent, "PaddleOCR-VL-1.6");
  h.node("documentParsingApiKey").value = "fake-test-key";
  const pending = h.ui.runDocumentParsingOperation("test");
  h.requests[0].resolve({ ok: true, message: "连接成功" });
  await pending;
  assert.equal(h.node("radio-mineru").disabled, true);
  assert.equal(h.node("radio-paddle").disabled, false);
  const stored = harness({ mode: "provider", provider: "mineru", api_key_set: true });
  stored.ui.openDocumentParsingSettings();
  assert.equal(stored.node("radio-mineru").checked, true);
  assert.equal(stored.node("saveDocumentParsingBtn").disabled, true);
  assert.equal(stored.node("testDocumentParsingBtn").disabled, true);
  await stored.ui.runDocumentParsingOperation("save");
  assert.equal(stored.requests.length, 0);
  assert.match(stored.node("documentParsingTestFeedbackText").textContent, /暂不可用/);
  stored.ui.selectDocumentParsingProvider("glm");
  assert.equal(stored.node("saveDocumentParsingBtn").disabled, false);
});
