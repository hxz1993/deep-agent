import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import test from "node:test";

const markdownSource = await readFile(
  new URL("../../../frontend/console/markdown.js", import.meta.url),
  "utf8",
);
const chatSource = await readFile(
  new URL("../../../frontend/console/chat.js", import.meta.url),
  "utf8",
);
const shell = await readFile(
  new URL("../../../frontend/console/index.html", import.meta.url),
  "utf8",
);
const workspaceCss = await readFile(
  new URL("../../../frontend/console/workspace.css", import.meta.url),
  "utf8",
);

test("Console 使用共享 Markdown 渲染器并按需加载本地 Mermaid", async () => {
  assert.match(shell, /<script src="\.\/markdown\.js\?v=/);
  assert.ok(shell.indexOf("marked.min.js") < shell.indexOf("markdown.js"));
  assert.ok(shell.indexOf("purify.min.js") < shell.indexOf("markdown.js"));
  assert.match(markdownSource, /global\.DeepAgentMarkdown = Object\.freeze/);
  assert.match(markdownSource, /language === "mermaid"/);
  assert.match(markdownSource, /data-mermaid-state="pending"/);
  assert.match(markdownSource, /new URL\("\.\.\/vendor\/mermaid\.min\.js", document\.baseURI\)/);
  assert.doesNotMatch(markdownSource, /https?:\/\//);
  await access(new URL("../../../frontend/vendor/mermaid.min.js", import.meta.url));
});

test("Mermaid 使用严格模式且失败时保留围栏源码", () => {
  assert.match(markdownSource, /startOnLoad: false/);
  assert.match(markdownSource, /securityLevel: "strict"/);
  assert.match(markdownSource, /htmlLabels: false/);
  assert.match(markdownSource, /flowchart: \{ htmlLabels: false \}/);
  assert.match(markdownSource, /await mermaid\.render\(id, definition\)/);
  assert.match(markdownSource, /Mermaid 图表语法有误，已保留源代码/);
  assert.match(markdownSource, /Mermaid 资源暂不可用，已保留源代码/);
  assert.match(workspaceCss, /\.mermaid-diagram > svg[\s\S]*max-width: 100%/);
});

test("主对话只在完整回复落定后渲染 Mermaid", () => {
  const sealStart = chatSource.indexOf("function sealFinalContent()");
  const sealEnd = chatSource.indexOf("const presenter", sealStart);
  const sealSource = chatSource.slice(sealStart, sealEnd);
  assert.match(sealSource, /finalContent\.innerHTML = renderMarkdownBody\(textBuffer\)/);
  assert.match(sealSource, /renderMarkdownDiagrams\(finalContent\)/);

  const appendStart = chatSource.indexOf("appendFinal(delta)");
  const appendEnd = chatSource.indexOf("addFinalBlock", appendStart);
  const appendSource = chatSource.slice(appendStart, appendEnd);
  assert.doesNotMatch(appendSource, /renderMarkdownDiagrams/);
});
