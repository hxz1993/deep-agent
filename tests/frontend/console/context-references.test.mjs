import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const chatSource = await readFile(
  new URL("../../../frontend/console/chat.js", import.meta.url),
  "utf8",
);
const workspaceCss = await readFile(
  new URL("../../../frontend/console/workspace.css", import.meta.url),
  "utf8",
);
const workspaceSource = await readFile(
  new URL("../../../frontend/console/workspace.js", import.meta.url),
  "utf8",
);

test("结构化上下文可以单独形成可见的用户历史轮次", () => {
  assert.match(chatSource, /msg\.context_refs && msg\.context_refs\.length/);
  assert.match(chatSource, /renderMessageContextRefs\(bubble, contextRefs, branchPoint\)/);
  assert.match(chatSource, /ref\.label \|\| ref\.type \|\| "引用"/);
  assert.match(workspaceCss, /\.context-ref-chip\s*\{/);
});

test("上下文引用使用线性信息图标在右侧栏切换只读 JSON", () => {
  assert.match(chatSource, /className = "context-ref-info"/);
  assert.match(chatSource, /info\.innerHTML = lucideIcon\("info"\)/);
  assert.doesNotMatch(chatSource, /info\.innerHTML = lucideIcon\("circle-info"\)/);
  assert.match(chatSource, /refreshLucideIcons\(\)/);
  assert.match(chatSource, /toggleContextReferenceInspector\(ref, branchPoint\)/);
  assert.match(workspaceSource, /function toggleContextReferenceInspector/);
  assert.match(workspaceSource, /JSON\.stringify\(reference, null, 2\)/);
  assert.match(workspaceSource, /if \(renderContextReferenceInspector\(host, state\)\) return/);
  assert.match(workspaceSource, /if \(selectedContextReference\)[\s\S]*openInspectorPane\(\)[\s\S]*renderInspector\(\)/);
  assert.doesNotMatch(workspaceSource, /contextReferenceDialog/);
});

test("控制台只显示中性标签且不渲染 payload 或 metadata", () => {
  const renderer = chatSource.match(
    /function renderMessageContextRefs[\s\S]*?\n\}/,
  )?.[0] || "";
  assert.ok(renderer);
  assert.doesNotMatch(renderer, /payload/);
  assert.doesNotMatch(renderer, /metadata/);
});

test("含上下文引用的用户消息只复制可见正文且不提供编辑入口", () => {
  assert.match(chatSource, /role === "user" && text/);
  assert.match(chatSource, /copyTextToClipboard\(text\)/);
  assert.doesNotMatch(chatSource, /startEditUserMessage/);
  assert.doesNotMatch(chatSource, /msg-edit-btn/);
  assert.doesNotMatch(chatSource, /payload\.context_refs\s*=/);
});
