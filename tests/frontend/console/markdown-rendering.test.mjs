import { readPresentationSource } from "./shared-presentation-source.mjs";
import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const markdownSource = await readFile(
  new URL("../../../console/markdown.js", import.meta.url),
  "utf8",
);
const chatSource = await readPresentationSource("chat.js");
const shell = await readFile(
  new URL("../../../console/index.html", import.meta.url),
  "utf8",
);
const workspaceCss = await readFile(
  new URL("../../../console/workspace.css", import.meta.url),
  "utf8",
);

// Exercise the shipped parser and renderer; browser checks cover DOMPurify.
const parserContext = vm.createContext({ console });
parserContext.window = parserContext;
parserContext.DOMPurify = { addHook() {}, sanitize: (html) => html };
vm.runInContext(await readFile(new URL("../../../console/vendor/marked.min.js", import.meta.url), "utf8"), parserContext);
vm.runInContext(await readFile(new URL("../../../console/vendor/highlight.min.js", import.meta.url), "utf8"), parserContext);
for (const asset of ["lucide.min.js", "marked-footnote.umd.js", "katex/katex.min.js", "marked-katex.umd.js"]) {
  vm.runInContext(await readFile(new URL(`../../../console/vendor/${asset}`, import.meta.url), "utf8"), parserContext);
}
vm.runInContext(markdownSource, parserContext);
const render = (text) => parserContext.DeepAgentMarkdown.render(text);

test("GFM 表格保留语义、对齐、空值和内联内容并独立滚动", () => {
  const html = render("| 名称 | 值 | 备注 |\n|:---|---:|:---:|\n| a \\| b | **12** | |\n| `code` | 3 | x<br>y |");
  assert.match(html, /class="markdown-table-scroll"[^>]*tabindex="0"/);
  assert.match(html, /<table><thead>/);
  assert.match(html, /<td align="right"><strong>12<\/strong><\/td>/);
  assert.match(html, /<td align="center"><\/td>/);
  assert.match(html, /a \| b/);
  assert.match(html, /<code>code<\/code>/);
  assert.match(html, /x<br>y/);
});

test("表头空表、无两侧管道和引用内的表格沿用 GFM 解析", () => {
  for (const source of ["| a | b |\n|---|---|", "a | b\n--- | ---\n1 | 2", "> | a | b |\n> |---|---|\n> | 1 | 2 |"] ) {
    assert.match(render(source), /markdown-table-scroll/);
  }
});

test("不将代码块或列数错误的分隔行猜测为表格", () => {
  assert.doesNotMatch(render("| a | b | c |\n|---|---|\n| 1 | 2 | 3 |"), /<table>/);
  assert.doesNotMatch(render("```\n| a | b |\n|---|---|\n```"), /<table>/);
});

test("已知语言高亮，未知标签与无标签围栏不猜测语言且保留原文", () => {
  assert.match(render("```python\nprint(True)\n```"), /hljs-/);
  for (const language of ["", "text", "plaintext", "chart", "unknown-language"]) {
    const html = render(`\`\`\`${language}\n  const x = \"<script>\";\n\t│  1  │\n\`\`\``);
    assert.doesNotMatch(html.match(/<code[^>]*>([\s\S]*?)<\/code>/)[1], /<span|<script>/);
    assert.match(html, /  const x = &quot;&lt;script&gt;&quot;;\n    │  1  │/);
  }
});

test("代码语言可见、复制按钮独立于正文且标签安全转义", () => {
  const html = render('```python title\nprint("hello")\n```');
  assert.match(html, /title="代码语言：python">python<\/span>/);
  assert.match(html, /aria-label="复制代码"/);
  assert.doesNotMatch(html.match(/<code[^>]*>([\s\S]*?)<\/code>/)[1], /复制/);
  assert.doesNotMatch(render('```x"><img/src=x>\ntext\n```'), /<img/);
});

test("脚注各消息独立编号并保留 GFM 与代码工具栏", () => {
  const source = '说明[^a]。\n\n[^a]: **注解**\n\n```js\nlet x = 1;\n```';
  const first = render(source), second = render(source);
  assert.match(first, /class="footnotes"/);
  assert.match(first, /<strong>注解<\/strong>/);
  assert.match(first, /markdown-code-copy/);
  const id = first.match(/id="(md-fn-[^\"]+)"/)[1];
  assert.ok(!second.includes(`id="${id}"`));
  assert.doesNotMatch(render('未定义[^missing]'), /class="footnotes"/);
  const repeated = render('甲[^a]，乙[^a]。\n\n[^a]: 注解');
  const refs = [...repeated.matchAll(/id="(md-fn-\d+-ref-[^"]+)"/g)].map(match => match[1]);
  assert.equal(new Set(refs).size, 2);
  for (const ref of refs) assert.ok(repeated.includes(`href="#${ref}"`));
});

test("公式渲染、错误回退以及代码中美元符号互不干扰", () => {
  assert.match(render('行内 \\(x^2\\)。'), /class="katex"/);
  assert.match(render('\\[\n\\frac{1}{2}\n\\]'), /class="katex-display"/);
  assert.match(render('行内 $x^2$。'), /class="katex"/);
  assert.match(render('$$\n\\frac{1}{2}\n$$'), /class="katex-display"/);
  assert.match(render('$\\frac{$'), /katex-error/);
  assert.doesNotMatch(render('`$x^2$`\n\n```text\n$x^2$\n```'), /class="katex"/);
  assert.doesNotMatch(render('Prices: $5 and $10.'), /class="katex"/);
  assert.doesNotMatch(render('$\\href{javascript:alert(1)}{bad}$'), /href="javascript:/);
});

test("五类提示引用与常见 GFM、HTML 折叠内容保留", () => {
  for (const type of ['NOTE', 'TIP', 'IMPORTANT', 'WARNING', 'CAUTION']) {
    assert.match(render(`> [!${type}]\n> **内容**`), new RegExp(`markdown-alert-${type.toLowerCase()}`));
  }
  assert.match(render('- [x] 已完成\n- [ ] 待办'), /type="checkbox"/);
  assert.match(render('~~删除~~ https://example.com'), /<del>删除<\/del>/);
  assert.match(render('<details><summary>详情</summary>内容</details>'), /<details>/);
  assert.doesNotMatch(render('```text\n> [!TIP]\n```'), /markdown-alert/);
});

test("Console 使用共享 Markdown 渲染器并按需加载本地 Mermaid", async () => {
  assert.match(shell, /<script src="\.\/markdown\.js\?v=/);
  assert.ok(shell.indexOf("marked.min.js") < shell.indexOf("markdown.js"));
  assert.ok(shell.indexOf("purify.min.js") < shell.indexOf("markdown.js"));
  assert.match(markdownSource, /global\.DeepAgentMarkdown = Object\.freeze/);
  assert.match(markdownSource, /language === "mermaid"/);
  assert.match(markdownSource, /data-mermaid-state="pending"/);
  assert.match(markdownSource, /new URL\("\.\.\/vendor\/mermaid\.min\.js", document\.baseURI\)/);
  assert.doesNotMatch(markdownSource, /https?:\/\//);
  await access(new URL("../../../console/vendor/mermaid.min.js", import.meta.url));
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


test("每张表格独立保留 Markdown 原文，不从 HTML 重建", () => {
  const first = '| 名称 | 值 |\n| :--- | ---: |\n| **a** \\| b | `<tag>` & "x" |\n';
  const second = '列一 | 列二\n--- | ---\n[链接](https://example.com) | $x^2$\n';
  const html = render(first + '\n间隔文字\n\n' + second);
  const sources = [...html.matchAll(/data-table-source="([^"]*)"/g)].map((match) => JSON.parse(match[1].replace(/&quot;/g, '"').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&')));
  assert.deepEqual(sources, [first + '\n', second]);
  assert.equal((html.match(/class="markdown-table-copy"/g) || []).length, 2);
  assert.doesNotMatch(render('```md\n' + first + '```'), /data-table-source=/);
});
