import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(new URL("../../../console/configuration/attachment-upload.js", import.meta.url), "utf8");
const template = await readFile(new URL("../../../console/configuration-template.html", import.meta.url), "utf8");
const plain = value => JSON.parse(JSON.stringify(value));
const response = (value = null) => ({ version: 1, value, effective_value: { max_size_mb: 20, max_count: 10, ...value }, allowed_mimes: ["application/pdf", "text/plain", "image/png"] });
function harness() {
  const nodes = new Map(), requests = [], toasts = [], events = [], handlers = {};
  const node = id => {
    if (!nodes.has(id)) {
      const classes = new Set(id === "panel-global-settings" ? ["active"] : []);
      nodes.set(id, { value: "", textContent: "", innerHTML: "", disabled: false, open: false, listeners: {},
        classList: { add: x => classes.add(x), remove: x => classes.delete(x), contains: x => classes.has(x) },
        addEventListener(name, fn) { this.listeners[name] = fn; },
        removeAttribute(name) { delete this[name]; }, setAttribute(name, value) { this[name] = value; },
        focus() { this.focused = true; }, showModal() { this.open = true; }, close() { this.open = false; },
      });
    }
    return nodes.get(id);
  };
  const context = vm.createContext({
    window: { globalSettingsAdmin: { page: "general" }, dispatchEvent: event => events.push(event.type) },
    document: { getElementById: node, body: { dataset: { consolePage: "configuration" } }, addEventListener: (name, fn) => { handlers[name] = fn; } },
    Event, escapeHtml: text => String(text ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;"),
    showToast: (...args) => toasts.push(args),
    apiFetch: (path, options) => new Promise((resolve, reject) => requests.push({ path, options, resolve, reject })),
  });
  vm.runInContext(source, context); handlers.DOMContentLoaded();
  return { context, node, requests, events, toasts, admin: context.window.attachmentUploadAdmin, ui: context.window.__AttachmentUploadUI };
}
async function load(h, value) { const p = h.admin.refresh(); h.requests.at(-1).resolve(response(value)); await p; }

test("常规页使用分组标题、边框配置行、英文单位、只读类型与平台提醒", () => {
  assert.match(template, /paperclip[\s\S]*附件上传[\s\S]*attachment-upload-card[\s\S]*<h3>上传限制<\/h3>/);
  assert.match(template, /id="attachmentUploadMaxSize"[^>]*min="1"[^>]*max="200"[^>]*placeholder="20"/);
  assert.match(template, /id="attachmentUploadMaxCount"[^>]*min="1"[^>]*max="20"[^>]*placeholder="10"/);
  assert.match(template, /允许类型 <span>通过环境变量设置<\/span>/);
  assert.match(template, /attachment-upload-platform-note[\s\S]*平台文件服务可能有额外/);
  assert.doesNotMatch(template, /允许类型.{0,60}只读|允许上传不代表模型/);
});

test("无配置时回显默认摘要、弹框空值保留placeholder，空项保存为默认", async () => {
  const h = harness(); await load(h, null); h.admin.open();
  assert.equal(h.node("attachmentUploadSizeSummary").textContent, "20 MB");
  assert.equal(h.node("attachmentUploadCountSummary").textContent, "10 个");
  assert.equal(h.node("attachmentUploadMaxSize").value, "");
  assert.equal(h.node("attachmentUploadMaxCount").value, "");
  assert.equal(h.node("attachmentUploadSettingsDialog").open, true);
  assert.deepEqual(plain(h.ui.buildPayload()), { value: { max_size_mb: 20, max_count: 10 } });
});

test("已有配置和缺失字段分别显示保存值与默认占位", async () => {
  const h = harness(); await load(h, { max_size_mb: 80 }); h.admin.open();
  assert.equal(h.node("attachmentUploadMaxSize").value, 80);
  assert.equal(h.node("attachmentUploadMaxCount").value, "");
  assert.equal(h.node("attachmentUploadSizeSummary").textContent, "80 MB");
});

test("类型来自生效MIME，去重且不把未允许格式补成白名单", () => {
  const h = harness();
  const html = h.ui.allowedTypesHtml(["text/xml", "application/xml", "image/png", "bad/<svg>"]);
  assert.equal((html.match(/XML/g) || []).length, 1);
  assert.match(html, /PNG/); assert.doesNotMatch(html, /PDF|DOCX/);
  assert.match(html, /bad\/&lt;svg>/); assert.doesNotMatch(html, /<svg>/);
});

test("范围边界通过，小数越界和空白以外非法字符拒绝", async () => {
  const h = harness(); await load(h, null); h.admin.open();
  for (const [size, count] of [[1, 1], [200, 20]]) {
    h.node("attachmentUploadMaxSize").value = size; h.node("attachmentUploadMaxCount").value = count;
    assert.deepEqual(plain(h.ui.buildPayload()), { value: { max_size_mb: size, max_count: count } });
  }
  for (const [size, count] of [[0, 10], [201, 10], [20.5, 10], [20, 0], [20, 21], [20, 5.5], ["abc", 10]]) {
    h.node("attachmentUploadMaxSize").value = size; h.node("attachmentUploadMaxCount").value = count;
    assert.throws(() => h.ui.buildPayload(), /整数/);
  }
});

test("恢复默认只改草稿，取消重开不改变保存配置", async () => {
  const h = harness(); await load(h, { max_size_mb: 80, max_count: 15 }); h.admin.open();
  h.node("resetAttachmentUploadSettingsBtn").listeners.click();
  assert.equal(h.node("attachmentUploadMaxSize").value, 20);
  assert.equal(h.requests.length, 1);
  h.admin.close(); h.admin.open(); assert.equal(h.node("attachmentUploadMaxSize").value, 80);
});

test("加载失败禁止编辑，有重试且不把故障当未配置", async () => {
  const h = harness(); const p = h.admin.refresh(); h.requests[0].reject(new Error("HTTP 503")); await p;
  assert.equal(h.node("openAttachmentUploadSettingsBtn").disabled, true);
  assert.equal(h.node("retryAttachmentUploadSettingsBtn").classList.contains("hidden"), false);
  h.admin.open(); assert.equal(h.node("attachmentUploadSettingsDialog").open, false);
  await load(h, null); h.admin.open(); assert.equal(h.node("attachmentUploadSettingsDialog").open, true);
});

test("并行进入只请求一次，切页后的迟到响应不能打开编辑器", async () => {
  const h = harness(); const one = h.admin.refresh(), two = h.admin.refresh();
  assert.equal(h.requests.length, 1);
  h.context.window.globalSettingsAdmin.page = "operations";
  h.requests[0].resolve(response()); await Promise.all([one, two]);
  assert.equal(h.node("openAttachmentUploadSettingsBtn").disabled, true);
  h.context.window.globalSettingsAdmin.page = "general"; await load(h, null);
  assert.equal(h.node("openAttachmentUploadSettingsBtn").disabled, false);
});

test("保存锁住关闭和重复提交，回读后更新摘要并通知工作台", async () => {
  const h = harness(); await load(h, null); h.admin.open();
  h.node("attachmentUploadMaxSize").value = 50; h.node("attachmentUploadMaxCount").value = 12;
  const saving = h.admin.save(); await h.admin.save(); h.admin.close();
  assert.equal(h.node("attachmentUploadSettingsDialog").open, true);
  assert.equal(h.requests.length, 2); assert.equal(h.requests[1].options.method, "PUT");
  assert.deepEqual(JSON.parse(h.requests[1].options.body), { value: { max_size_mb: 50, max_count: 12 } });
  h.requests[1].resolve(response({ max_size_mb: 50, max_count: 12 })); await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.requests.length, 3);
  h.requests[2].resolve(response({ max_size_mb: 50, max_count: 12 })); await saving;
  assert.equal(h.node("attachmentUploadSizeSummary").textContent, "50 MB");
  assert.deepEqual(h.events, ["attachment-upload-settings-changed"]);
  assert.equal(h.node("saveAttachmentUploadSettingsBtn").disabled, false);
});

test("保存失败保留草稿并反馈在弹窗里，不广播刷新", async () => {
  const h = harness(); await load(h, null); h.admin.open(); h.node("attachmentUploadMaxSize").value = 50;
  const p = h.admin.save(); h.requests[1].reject(new Error("保存权限不足")); await p;
  assert.equal(h.node("attachmentUploadSettingsDialog").open, true);
  assert.equal(h.node("attachmentUploadMaxSize").value, 50);
  assert.match(h.node("attachmentUploadFormError").textContent, /权限不足/);
  assert.deepEqual(h.events, []);
});

test("保存已成功但回读失败明确反馈，不能误报保存失败", async () => {
  const h = harness(); await load(h, null); h.admin.open();
  const p = h.admin.save(); h.requests[1].resolve(response({ max_size_mb: 20, max_count: 10 }));
  await new Promise(resolve => setImmediate(resolve)); h.requests[2].reject(new Error("HTTP 503")); await p;
  assert.match(h.toasts.at(-1)[0], /已保存，刷新失败/);
  assert.equal(h.node("attachmentUploadSettingsDialog").open, false);
  assert.deepEqual(h.events, ["attachment-upload-settings-changed"]);
});

test("弹窗编辑时后台刷新不覆盖未保存草稿", async () => {
  const h = harness(); await load(h, null); h.admin.open(); h.node("attachmentUploadMaxSize").value = 90;
  await h.admin.refresh(); assert.equal(h.requests.length, 1);
  assert.equal(h.node("attachmentUploadMaxSize").value, 90);
});
