import { readPresentationSource } from "./shared-presentation-source.mjs";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const chatSource = await readPresentationSource("chat.js");
const workspaceCss = await readFile(
  new URL("../../../console/workspace.css", import.meta.url),
  "utf8",
);
const workspaceSource = await readPresentationSource("workspace.js");
const conversationItemsSource = await readFile(
  new URL("../../../console/conversation-items.js", import.meta.url),
  "utf8",
);

test("结构化上下文可以单独形成可见的用户历史轮次", () => {
  assert.match(conversationItemsSource, /contextRefs: Array\.isArray\(item\.context_refs\)/);
  assert.match(chatSource, /node\.contextRefs\.length/);
  assert.match(chatSource, /renderMessageContextRefs\(bubble, contextRefs, branchPoint\)/);
  assert.match(chatSource, /ref\.label \|\| ref\.type \|\| "引用"/);
  assert.match(workspaceCss, /\.context-ref-chip\s*\{/);
});

test("上下文引用使用线性信息图标切换到右栏上下文页签", () => {
  assert.match(chatSource, /className = "context-ref-info"/);
  assert.match(chatSource, /info\.innerHTML = lucideIcon\("info"\)/);
  assert.doesNotMatch(chatSource, /info\.innerHTML = lucideIcon\("circle-info"\)/);
  assert.match(chatSource, /refreshLucideIcons\(\)/);
  assert.match(chatSource, /onContextReference\(ref, branchPoint\)/);
  assert.match(workspaceSource, /function toggleContextReferenceInspector/);
  assert.match(workspaceSource, /renderContextReferenceContent\(selectedContextReference\?\.reference\)/);
  assert.match(workspaceSource, /inspectorActiveTab = "context";[\s\S]*openInspectorPane\(\)[\s\S]*renderInspector\(\)/);
  assert.match(workspaceSource, /data-inspector-tab="context"/);

  assert.doesNotMatch(workspaceSource, /contextReferenceDialog/);
});

test("控制台只显示中性标签且不渲染 payload 或 metadata", () => {
  const renderer = chatSource.match(
    /function renderMessageContextRefs[\s\S]*?\n\}/,
  )?.[0] || "";
  assert.ok(renderer);
  assert.doesNotMatch(renderer, /payload/);
  assert.doesNotMatch(renderer, /metadata/);
  assert.doesNotMatch(renderer, /上下文 ·/);
  assert.match(renderer, /chip\.title = ref\.label \|\| ref\.type \|\| "引用"/);
  assert.match(workspaceCss, /\.context-ref-label\s*\{[^}]*text-overflow: ellipsis;[^}]*white-space: nowrap;/);
});

test("含上下文引用的用户消息只复制可见正文且不提供编辑入口", () => {
  assert.match(chatSource, /role === "user"[\s\S]*?getText: text \? \(\) => text : null/);
  assert.match(chatSource, /copyTextToClipboard\(text\)/);
  assert.doesNotMatch(chatSource, /startEditUserMessage/);
  assert.doesNotMatch(chatSource, /msg-edit-btn/);
  assert.doesNotMatch(chatSource, /payload\.context_refs\s*=/);
});
