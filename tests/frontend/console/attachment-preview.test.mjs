import { readPresentationSource } from "./shared-presentation-source.mjs";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  attachmentPreviewAttributes,
  attachmentPreviewUrl,
  attachmentDownloadUrl,
  isImageAttachment,
  createImageGallery,
  bindAttachmentImagePreviews,
} from "../../../console/attachment-preview.js";

test("点击图片只收集同一条用户消息的附件，不跨轮次", () => {
  const handlers = {};
  const root = { addEventListener: (name, handler) => { handlers[name] = handler; } };
  const buttons = ["a", "b", "next-turn"].map(name => ({ dataset: { attachmentImageUrl: `blob:${name}`, attachmentImageName: name } }));
  const ownMessage = { querySelectorAll: () => buttons.slice(0, 2) };
  const conversation = { querySelectorAll: () => buttons };
  const selected = buttons[1];
  selected.closest = selector => ({
    "[data-attachment-image-url]": selected,
    ".msg-attachments": ownMessage,
    "#messages": conversation,
  })[selector] || null;
  const previousWindow = globalThis.window;
  globalThis.window = { addEventListener() {} };
  let result;
  try {
    bindAttachmentImagePreviews({ root, openImage: (item, items) => { result = { item, items }; } });
    handlers.click({ target: selected, preventDefault() {} });
  } finally {
    if (previousWindow === undefined) delete globalThis.window;
    else globalThis.window = previousWindow;
  }
  assert.deepEqual(result.items.map(item => item.name), ["a", "b"]);
  assert.equal(result.item.name, "b");
});

test("图片组保持区域内顺序和点击位置，忽略普通文件", () => {
  const first = { name: "a.png", previewUrl: "blob:a" };
  const second = { name: "b.png", previewUrl: "blob:b" };
  assert.deepEqual(createImageGallery(second, [first, { name: "notes.txt" }, second]), { items: [first, second], index: 1 });
  assert.deepEqual(createImageGallery(first), { items: [first], index: 0 });
});

test("大图支持前后按钮、方向键和内容区域空白关闭", async () => {
  const source = await readPresentationSource("chat.js");
  assert.match(source, /event.target.classList.contains\("attachment-image-dialog-content"\)/);
  assert.match(source, /event.key === "ArrowLeft" \|\| event.key === "ArrowRight"/);
  assert.match(source, /openAttachmentImagePreview\(att, state.pendingAttachments\)/);
  const template = await readFile(new URL("../../../console/workspace-template.html", import.meta.url), "utf8");
  assert.match(template, /aria-label="上一张图片"/);
  assert.match(template, /aria-label="下一张图片"/);
});

test("图片识别兼容 History MIME 和 Event MIME，不把普通文件当图片", () => {
  assert.equal(isImageAttachment({ mime: "image/jpeg" }), true);
  assert.equal(isImageAttachment({ mime_type: "image/png" }), true);
  assert.equal(isImageAttachment({ name: "photo.JPG" }), true);
  assert.equal(isImageAttachment({ mime: "application/pdf", name: "photo.jpg" }), false);
  assert.equal(attachmentPreviewUrl({ id: "local_a", name: "data.xlsx" }, "https://example.test/console/"), null);
});

test("本地历史图片使用鉴权下载接口，保留服务挂载前缀", () => {
  const reference = { id: "local_a/b", mime: "image/jpeg", previewUrl: "blob:expired" };
  assert.equal(attachmentPreviewUrl(reference, "https://example.test/services/agent/console/"),
    "https://example.test/services/agent/api/v1/local-files/local_a%2Fb/download");
  assert.equal(reference.previewUrl, "blob:expired");
});

test("平台图片复用平台文件路由，不走本地文件接口或转发凭据", () => {
  const url = new URL(attachmentPreviewUrl({ id: "file&1", name: "图片.png", mime_type: "image/png" },
    "https://example.test/prefix/deepfos-server/deep-agent/console/"));
  assert.equal(url.pathname, "/prefix/deepfos-server/platform-file-server/files/download");
  assert.equal(url.searchParams.get("id"), "file&1");
  assert.equal(url.searchParams.get("fileName"), "图片.png");
  assert.equal(url.searchParams.has("token"), false);
});

test("缺少文件引用不预览，文件名不能注入 HTML", () => {
  assert.equal(attachmentPreviewAttributes({ mime: "image/png" }, "https://example.test/console/"), "");
  const attributes = attachmentPreviewAttributes({ id: "local_a", name: '\"><img onerror="bad">', mime: "image/png" },
    "https://example.test/console/");
  assert.ok(attributes.includes("data-attachment-image-url="));
  assert.ok(attributes.includes("&quot;&gt;&lt;img"));
  assert.ok(!attributes.includes("<img"));
});

test("直播和历史共用的用户气泡把附件与上下文放在正文前", async () => {
  const source = await readPresentationSource("chat.js");
  const addMessage = source.match(/function addMessage\([\s\S]*?\n\}/)?.[0];
  assert.ok(addMessage);
  assert.ok(addMessage.indexOf("renderMessageAttachments(bubble") < addMessage.indexOf("bubble.appendChild(textEl)"));
  assert.ok(addMessage.indexOf("renderMessageContextRefs(bubble") < addMessage.indexOf("bubble.appendChild(textEl)"));
  assert.match(source, /bindAttachmentImagePreviews\(\{ root: document.getElementById\("workspaceGrid"\), openImage: openAttachmentImagePreview \}\)/);
});

test("附件下载区分所有者和管理员会话路由，平台沿用原权限", () => {
  const attachment = { id: "local_a", name: "sales.xlsx" };
  const base = "https://example.test/prefix/console/";
  assert.equal(attachmentDownloadUrl(attachment, base), "https://example.test/prefix/api/v1/local-files/local_a/download");
  assert.equal(attachmentDownloadUrl(attachment, base, { diagnosticSessionId: "session/b" }), "https://example.test/prefix/api/v1/diagnostics/sessions/session%2Fb/files/local_a/download");
  const platform = new URL(attachmentDownloadUrl({ id: "platform-a", name: "sales.xlsx" }, base, { diagnosticSessionId: "s" }));
  assert.equal(platform.pathname, "/deepfos-server/platform-file-server/files/download");
  assert.equal(attachmentDownloadUrl({}, base), null);
});

test("长附件名和上下文在窄气泡中省略，不撑出对话区域", async () => {
  const css = await readFile(new URL("../../../console/workspace.css", import.meta.url), "utf8");
  assert.match(css, /\.msg-row\.user \.bubble \{\s*max-width: 100%;\s*min-width: 0;/);
  assert.match(css, /\.attachment-name \{ min-width: 0;/);
  assert.match(css, /\.msg-context-refs \.context-ref-chip \{ min-width: 0; max-width: min\(180px, 100%\); border-radius: 999px;/);
});
