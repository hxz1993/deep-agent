import { readPresentationSource } from "./shared-presentation-source.mjs";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const chatSource = await readPresentationSource("chat.js");
const agentSource = await readFile(
  new URL("../../../console/configuration/agents.js", import.meta.url),
  "utf8",
);
const template = await readFile(
  new URL("../../../console/workspace-template.html", import.meta.url),
  "utf8",
);
const workspaceSource = await readPresentationSource("workspace.js");

test("附件上传不再要求可配置的附件工具包", () => {
  assert.match(agentSource, /builtin:\s*"内置工具"/);
  assert.match(agentSource, /return toolPackage\.category/);
  assert.match(
    agentSource,
    /GROUP_ORDER\s*=\s*\["builtin",\s*"mcp",\s*"ui"\]/,
  );
  assert.doesNotMatch(agentSource, /GROUP_ORDER[^;]*"skill"/);
  assert.match(chatSource, /function uploadCapabilityText\(\)/);
  assert.doesNotMatch(agentSource, /builtin:attachment/);
});

test("控制台从后端读取 MIME、数量与大小限制", () => {
  assert.match(chatSource, /capabilities\.allowed_mimes/);
  assert.match(chatSource, /accept\.includes\("text\/plain"\)/);
  assert.match(chatSource, /accept\.push\("\.log"\)/);
  assert.match(chatSource, /el\.fileInput\.accept = accept\.join\(","\)/);
  assert.match(chatSource, /state\.uploadCapabilities\?\.max_count/);
  assert.match(chatSource, /state\.uploadCapabilities\?\.max_size_mb/);
  assert.match(chatSource, /file\.size > maxSizeMb \* 1024 \* 1024/);
});

test("附件能力说明区分解析供应商支持与既定上传白名单阻止", () => {
  const definition = chatSource.match(/function uploadCapabilityText\(\) \{[\s\S]*?\n\}/)?.[0];
  assert.ok(definition);
  const context = vm.createContext({
    state: { uploadCapabilities: { kinds: ["text_file"], labels: { text_file: "TXT" }, blocked_document_mimes: ["application/pdf", "application/vnd.openxmlformats-officedocument.presentationml.presentation"], max_count: 3, max_size_mb: 5 } },
    UPLOAD_KIND_ORDER: ["text_file"],
  });
  vm.runInContext(definition, context);
  assert.match(context.uploadCapabilityText(), /支持：TXT/);
  assert.match(context.uploadCapabilityText(), /最多 3 个 · 单个 5MB/);
  assert.match(context.uploadCapabilityText(), /解析服务支持，但上传白名单不允许：PDF、PPTX/);
  context.state.uploadCapabilities.kinds = [];
  assert.doesNotMatch(context.uploadCapabilityText(), /尚未加载/);
  context.state.uploadCapabilities.blocked_document_mimes = [];
  assert.match(context.uploadCapabilityText(), /尚未加载/);
});

test("文档解析热切换刷新上传能力，空白名单清除旧 accept", async () => {
  const definition = chatSource.match(/async function loadUploadCapabilities\([^\n]*\) \{[\s\S]*?\n\}/)?.[0];
  assert.ok(definition);
  assert.match(chatSource, /window\.addEventListener\("document-parsing-settings-changed", \(\) => \{ void loadUploadCapabilities\(\{ force: true \}\); \}\)/);
  let payload = { max_count: 10, max_size_mb: 20, kinds: ["pdf"], allowed_mimes: ["application/pdf", "text/plain"], document_parsing: { provider: "paddle" } };
  const input = { accept: ".old" };
  const context = vm.createContext({
    state: {}, el: { fileInput: input }, console, uploadCapabilitiesRequestId: 0, uploadCapabilitiesRequest: null, updateComposerModelInfo() {}, serviceUrl: value => value,
    daFetch: async () => ({ ok: true, json: async () => payload }),
  });
  vm.runInContext(definition, context);
  await context.loadUploadCapabilities();
  assert.equal(input.accept, "application/pdf,text/plain,.log");
  assert.equal(context.state.uploadCapabilities.document_parsing.provider, "paddle");
  payload = { max_count: 10, max_size_mb: 20, kinds: ["text_file"], allowed_mimes: ["text/plain"], document_parsing: null };
  await context.loadUploadCapabilities();
  assert.equal(input.accept, "text/plain,.log");
  assert.equal(context.state.uploadCapabilities.document_parsing, null);
  payload = { max_count: 10, max_size_mb: 20, kinds: [], allowed_mimes: [] };
  await context.loadUploadCapabilities();
  assert.equal(input.accept, "");
});

test("热切换后的迟到上传能力响应不能恢复旧供应商类型", async () => {
  const definition = chatSource.match(/async function loadUploadCapabilities\([^\n]*\) \{[\s\S]*?\n\}/)?.[0];
  const requests = [], input = { accept: "" };
  const context = vm.createContext({
    state: {}, el: { fileInput: input }, console, uploadCapabilitiesRequestId: 0, uploadCapabilitiesRequest: null,
    updateComposerModelInfo() {}, serviceUrl: value => value,
    daFetch: () => new Promise(resolve => requests.push(resolve)),
  });
  vm.runInContext(definition, context);
  const old = context.loadUploadCapabilities();
  const current = context.loadUploadCapabilities({ force: true });
  requests[1]({ ok: true, json: async () => ({ max_count: 10, max_size_mb: 20, kinds: [], allowed_mimes: [] }) });
  await current;
  requests[0]({ ok: true, json: async () => ({ max_count: 10, max_size_mb: 20, kinds: ["pdf"], allowed_mimes: ["application/pdf"] }) });
  await old;
  assert.equal(input.accept, "");
  assert.deepEqual(Array.from(context.state.uploadCapabilities.kinds), []);
});

test("输入框支持点击、粘贴和拖拽附件", () => {
  assert.match(template, /id="composerDropZone"/);
  assert.match(template, /id="composerDropOverlay"/);
  assert.match(chatSource, /addEventListener\("paste", onPaste\)/);
  assert.match(chatSource, /addEventListener\("dragenter", onComposerDragEnter\)/);
  assert.match(chatSource, /addEventListener\("drop", onComposerDrop\)/);
  assert.match(chatSource, /e\.clipboardData\?\.files/);
  assert.match(chatSource, /event\.dataTransfer\?\.files/);
  assert.match(chatSource, /支持点击、拖拽或粘贴/);
});

test("待发送图片使用本地临时 URL 提供缩略图与全屏预览", () => {
  assert.match(template, /id="attachmentImageDialog"/);
  assert.match(template, /id="attachmentImageDialogImage"/);
  assert.match(chatSource, /URL\.createObjectURL\(file\)/);
  assert.match(chatSource, /URL\.revokeObjectURL\(att\.previewUrl\)/);
  assert.match(chatSource, /pending-attachment-thumbnail/);
  assert.match(chatSource, /showModal\(\)/);
  assert.match(chatSource, /openAttachmentImagePreview\(att, state.pendingAttachments\)/);
  assert.match(workspaceSource, /host\.replaceChildren\(sidebar, sessionResizer, chat, attachmentImageDialog\)/);
});

test("附件能力说明只使用自定义浮层而不触发浏览器原生 title", () => {
  assert.match(template, /id="attachmentTooltip"/);
  assert.match(chatSource, /el\.attachBtn\.removeAttribute\("title"\)/);
  assert.doesNotMatch(template, /id="attachBtn"[^>]*title=/);
});

test("新附件请求不再把前端抽取文本写入聊天载荷", () => {
  const sendPayloadBlock = chatSource.match(
    /payload\.attachments = attachments\.map\([\s\S]*?\}\)\);/,
  )?.[0] || "";
  assert.ok(sendPayloadBlock);
  assert.doesNotMatch(sendPayloadBlock, /extracted_text/);
  assert.match(template, /\.docx/);
  assert.match(template, /\.xlsx/);
  assert.match(template, /\.log/);
  assert.match(template, /\.mp4/);
});

test("上传能力同批刷新合并请求，热保存可强制新读并丢弃旧响应", async () => {
  const definition = chatSource.match(/async function loadUploadCapabilities\([^\n]*\) \{[\s\S]*?\n\}/)?.[0];
  const requests = [], input = { accept: ".before" };
  const context = vm.createContext({
    state: { uploadCapabilities: { max_count: 10, max_size_mb: 20 } }, el: { fileInput: input }, console,
    uploadCapabilitiesRequestId: 0, uploadCapabilitiesRequest: null,
    updateComposerModelInfo() {}, serviceUrl: value => value,
    daFetch: () => new Promise(resolve => requests.push(resolve)),
  });
  vm.runInContext(definition, context);
  const first = context.loadUploadCapabilities(), same = context.loadUploadCapabilities();
  assert.equal(requests.length, 1);
  const latest = context.loadUploadCapabilities({ force: true }); assert.equal(requests.length, 2);
  requests[1]({ ok: true, json: async () => ({ max_count: 20, max_size_mb: 50, allowed_mimes: ["text/plain"] }) });
  assert.equal(await latest, true);
  requests[0]({ ok: true, json: async () => ({ max_count: 10, max_size_mb: 20, allowed_mimes: ["image/png"] }) });
  assert.equal(await first, false); assert.equal(await same, false);
  assert.equal(context.state.uploadCapabilities.max_size_mb, 50); assert.equal(input.accept, "text/plain,.log");
});

test("上传限制读取失败不伪装成默认策略，也不上传该批文件", async () => {
  const definition = chatSource.match(/async function loadUploadCapabilities\([^\n]*\) \{[\s\S]*?\n\}/)?.[0];
  const batch = chatSource.match(/async function uploadAttachmentBatch\(files\) \{[\s\S]*?\n\}/)?.[0];
  const uploaded = [], notes = [];
  const context = vm.createContext({
    state: { uploadCapabilities: { max_count: 15, max_size_mb: 50 } }, el: { fileInput: { accept: ".pdf" } },
    console: { warn() {} }, uploadCapabilitiesRequestId: 0, uploadCapabilitiesRequest: null,
    updateComposerModelInfo() {}, serviceUrl: value => value, daFetch: async () => ({ ok: false, status: 503 }),
    uploadAttachment: file => uploaded.push(file), addSystemNote: text => notes.push(text),
  });
  vm.runInContext(definition + "\n" + batch, context);
  await context.uploadAttachmentBatch([{ name: "one.pdf" }]);
  assert.deepEqual(uploaded, []); assert.match(notes[0], /获取失败/);
  assert.equal(context.state.uploadCapabilities.max_size_mb, 50);
  assert.equal(context.el.fileInput.accept, ".pdf");
});

test("一批多文件先刷新一次限制，再逐个上传", async () => {
  const batch = chatSource.match(/async function uploadAttachmentBatch\(files\) \{[\s\S]*?\n\}/)?.[0];
  const calls = [];
  const context = vm.createContext({ loadUploadCapabilities: async () => { calls.push("limits"); return true; }, uploadAttachment: async file => calls.push(file.name) });
  vm.runInContext(batch, context);
  await context.uploadAttachmentBatch([{ name: "a.pdf" }, { name: "b.pdf" }]);
  assert.deepEqual(calls, ["limits", "a.pdf", "b.pdf"]);
});
