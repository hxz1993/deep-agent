import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const chatSource = await readFile(new URL("../../../frontend/console/chat.js", import.meta.url), "utf8");
const workspaceCss = await readFile(new URL("../../../frontend/console/workspace.css", import.meta.url), "utf8");

test("工具调用默认是无框透明的灰色普通文本行", () => {
  assert.match(workspaceCss, /\.tool-block \{[^}]*border: 0;[^}]*background: transparent;/);
  assert.match(workspaceCss, /\.tool-head \{[^}]*border: 0;[^}]*background: transparent;[^}]*color: var\(--text-faint\);[^}]*font: inherit;/);
  assert.match(workspaceCss, /\.tool-head \.tool-name \{[^}]*font-weight: 400;/);
  assert.match(chatSource, /<span class="tool-icon">\$\{lucideIcon\(/);
  assert.match(workspaceCss, /\.tool-body \{ display: none;/);
  assert.match(workspaceCss, /\.tool-block\.open \.tool-body \{ display: block; \}/);
});

test("展开箭头仅悬停、键盘焦点或触屏显示，状态保持可见", () => {
  assert.match(workspaceCss, /\.tool-head \.chevron \{[^}]*opacity: 0;/);
  assert.match(workspaceCss, /\.tool-head:hover \.chevron,\s*\.tool-head:focus-visible \.chevron \{ opacity: 1; \}/);
  assert.match(workspaceCss, /@media \(hover: none\) \{\s*\.tool-head \.chevron \{ opacity: 1; \}/);
  assert.match(chatSource, /<button type="button" class="tool-head" aria-expanded="false">/);
  assert.match(chatSource, /class="spinner" role="status" aria-label="运行中"/);
  for (const status of ["pending", "error", "interrupted"]) {
    assert.match(workspaceCss, new RegExp(`\\.tool-status-${status} \\{[^}]*color: var\\(--(?:hitl-pending-border|danger)\\);`));
  }
  assert.doesNotMatch(workspaceCss, /\.tool-head \.tool-status \{[^}]*(?:opacity: 0|display: none|visibility: hidden)/);
});

test("点击展开与等待操作自动展开同步 aria-expanded", () => {
  const helper = chatSource.match(/function setToolBlockExpanded\(block, expanded\) \{[\s\S]*?\n\}/)?.[0];
  assert.ok(helper);
  const setExpanded = vm.runInNewContext(`(${helper})`);
  const attrs = new Map();
  let isOpen = false;
  const block = {
    classList: { toggle(name, expanded) { assert.equal(name, "open"); isOpen = expanded; } },
    querySelector(selector) {
      assert.equal(selector, ".tool-head");
      return { setAttribute: (key, value) => attrs.set(key, value) };
    },
  };
  setExpanded(block, true);
  assert.equal(isOpen, true);
  assert.equal(attrs.get("aria-expanded"), "true");
  setExpanded(block, false);
  assert.equal(isOpen, false);
  assert.equal(attrs.get("aria-expanded"), "false");
  assert.match(chatSource, /setToolBlockExpanded\(block, !block\.classList\.contains\("open"\)\)/);
  assert.match(chatSource, /querySelectorAll\("\.tool-block\.hitl-pending"\)[\s\S]*?setToolBlockExpanded\(block, true\)/);
});
