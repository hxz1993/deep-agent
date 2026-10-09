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
  assert.match(chatSource, /state\.uploadCapabilities\.allowed_mimes/);
  assert.match(chatSource, /allowed\.includes\("text\/plain"\)/);
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
  const definition = chatSource.match(/async function loadUploadCapabilities\(\) \{[\s\S]*?\n\}/)?.[0];
  assert.ok(definition);
  assert.match(chatSource, /window\.addEventListener\("document-parsing-settings-changed", \(\) => \{ void loadUploadCapabilities\(\); \}\)/);
  let payload = { kinds: ["pdf"], allowed_mimes: ["application/pdf", "text/plain"], document_parsing: { provider: "paddle" } };
  const input = { accept: ".old" };
  const context = vm.createContext({
    state: {}, el: { fileInput: input }, console, uploadCapabilitiesRequestId: 0, updateComposerModelInfo() {}, serviceUrl: value => value,
    daFetch: async () => ({ ok: true, json: async () => payload }),
  });
  vm.runInContext(definition, context);
  await context.loadUploadCapabilities();
  assert.equal(input.accept, "application/pdf,text/plain,.log");
  assert.equal(context.state.uploadCapabilities.document_parsing.provider, "paddle");
  payload = { kinds: ["text_file"], allowed_mimes: ["text/plain"], document_parsing: null };
  await context.loadUploadCapabilities();
  assert.equal(input.accept, "text/plain,.log");
  assert.equal(context.state.uploadCapabilities.document_parsing, null);
  payload = { kinds: [], allowed_mimes: [] };
  await context.loadUploadCapabilities();
  assert.equal(input.accept, "");
});

test("热切换后的迟到上传能力响应不能恢复旧供应商类型", async () => {
  const definition = chatSource.match(/async function loadUploadCapabilities\(\) \{[\s\S]*?\n\}/)?.[0];
  const requests = [], input = { accept: "" };
  const context = vm.createContext({
    state: {}, el: { fileInput: input }, console, uploadCapabilitiesRequestId: 0,
    updateComposerModelInfo() {}, serviceUrl: value => value,
    daFetch: () => new Promise(resolve => requests.push(resolve)),
  });
  vm.runInContext(definition, context);
  const old = context.loadUploadCapabilities();
  const current = context.loadUploadCapabilities();
  requests[1]({ ok: true, json: async () => ({ kinds: [], allowed_mimes: [] }) });
  await current;
  requests[0]({ ok: true, json: async () => ({ kinds: ["pdf"], allowed_mimes: ["application/pdf"] }) });
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
