import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const chatSource = await readFile(
  new URL("../../../frontend/console/chat.js", import.meta.url),
  "utf8",
);
const agentSource = await readFile(
  new URL("../../../frontend/console/configuration/agents.js", import.meta.url),
  "utf8",
);
const template = await readFile(
  new URL("../../../frontend/console/workspace-template.html", import.meta.url),
  "utf8",
);
const workspaceSource = await readFile(
  new URL("../../../frontend/console/workspace.js", import.meta.url),
  "utf8",
);

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
