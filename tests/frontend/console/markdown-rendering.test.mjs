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
  assert.match(sealSource, /replaceMarkdownBody\(finalContent, textBuffer\)/);
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

test("脚注重复引用与相似标签不碰撞，每个链接都有唯一目标", () => {
  const html = render('A[^a] B[^a] C[^a-2] D[^ref-a] E[^label]\n\n[^a]: One\n[^a-2]: Two\n[^ref-a]: Three\n[^label]: Four');
  const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
  const hrefs = [...html.matchAll(/href="#([^"]+)"/g)].map(match => match[1]);
  assert.equal(ids.length, new Set(ids).size);
  assert.equal(hrefs.length, 10);
  for (const href of hrefs) assert.equal(ids.filter(id => id === href).length, 1);
  const refs = [...html.matchAll(/<a id="([^"]+)" href="#([^"]+)"/g)];
  assert.equal(refs[0][2], refs[1][2]);
  assert.notEqual(refs[0][1], refs[1][1]);
  // A raw label must never escape a renderer-owned attribute even before sanitize.
  assert.doesNotMatch(render('说明[^x" onclick="bad]。\n\n[^x" onclick="bad]: 注解'), /onclick=/);
});

test("Markdown 脚注定位只滚动最近内容面板并保持外层及路由不变", async () => {
  let click, prevented = false, focused;
  const body = { scrollTop: 0 }, html = { scrollTop: 0 };
  const pane = { parentElement: body, scrollTop: 100, clientTop: 1, clientHeight: 400, scrollHeight: 1600, getBoundingClientRect: () => ({ top: 50 }) };
  const details = { tagName: 'DETAILS', parentElement: pane, open: false };
  const target = { id: 'note', parentElement: details, getBoundingClientRect: () => ({ top: 700, bottom: 720 }), setAttribute() {}, focus: value => { focused = value; } };
  const root = { querySelectorAll: () => [target] };
  const link = { getAttribute: () => '#note', closest: () => root };
  const doc = { body, documentElement: html, addEventListener: (type, handler) => { click = handler; } };
  const context = vm.createContext({ console, document: doc, getComputedStyle: node => ({ overflowY: node === pane ? 'auto' : 'visible' }) });
  context.window = context;
  context.DOMPurify = { addHook() {}, sanitize: value => value };
  vm.runInContext(await readFile(new URL('../../../console/vendor/marked.min.js', import.meta.url), 'utf8'), context);
  vm.runInContext(markdownSource, context);
  context.DeepAgentMarkdown.init();
  await click({ target: { closest: selector => selector.includes('a[href') ? link : null }, preventDefault() { prevented = true; } });
  assert.equal(pane.scrollTop, 369);
  assert.equal(body.scrollTop, 0);
  assert.equal(html.scrollTop, 0);
  assert.equal(details.open, true);
  assert.equal(prevented, true);
  assert.equal(focused.preventScroll, true);
});

test("代码围栏以15行为边界，不计结尾换行，保留全部原文和既有按钮", () => {
  for (const language of ["json", "python", "bom_clarify_v2", ""]) {
    for (const [lines, collapsed] of [[15, false], [16, true]]) {
      const body = Array.from({length: lines}, (_, n) => `line ${n + 1}`).join("\n");
      const html = render(`\`\`\`${language}\n${body}\n\`\`\``);
      assert.equal(/<pre[^>]* hidden>/.test(html), collapsed);
      assert.match(html, new RegExp(`aria-expanded="${!collapsed}"`));
      assert.match(html, /markdown-code-toggle/); assert.match(html, /markdown-code-copy/);
      assert.match(html.replace(/<[^>]*>/g, ""), new RegExp(`line ${lines}`));
    }
  }
  assert.doesNotMatch(render('```text\r\n' + Array(15).fill('line').join('\r\n') + '\r\n```'), /<pre[^>]* hidden>/);
});

function codeDom({ expanded = false, language = 'json', wrapped = false, text = '' } = {}) {
  const classes = new Set(wrapped ? ['is-wrapped'] : []), attrs = new Map();
  const code = { textContent: text, innerHTML: '' };
  const pre = { hidden: !expanded, querySelector: () => code, closest: () => block };
  const control = kind => ({
    title: '', disabled: false, innerHTML: '', classList: { contains: name => name === kind },
    setAttribute(name,value) { attrs.set(`${kind}:${name}`, value); },
    closest(selector) { return selector === '.markdown-code-block' ? block : selector === '.markdown-body' ? root : null; },
  });
  const toggle = control('markdown-code-toggle'), wrap = control('markdown-code-wrap'), copy = control('markdown-code-copy');
  const block = { dataset: {codeLanguage:language}, classList: { contains: name => classes.has(name), toggle(name, value) { const next = value ?? !classes.has(name); if(next) classes.add(name); else classes.delete(name); return next; } },
    querySelector(selector) { return selector === 'pre' ? pre : selector === 'pre > code' ? code : selector === '.markdown-code-toggle' ? toggle : selector === '.markdown-code-wrap' ? wrap : null; } };
  const root = { querySelectorAll: () => [block] };
  return {block, root, pre, code, toggle, wrap, copy, attrs};
}

async function interactiveCodeHarness() {
  let click;
  const copied = [];
  const context = vm.createContext({console, document: {addEventListener: (name,handler) => {if(name === 'click') click = handler;}}, navigator: {clipboard: {writeText: async value => copied.push(value)}}, setTimeout() {}});
  context.window = context;
  context.DOMPurify = {addHook(){}, sanitize: html => html};
  vm.runInContext(await readFile(new URL('../../../console/vendor/marked.min.js', import.meta.url), 'utf8'), context);
  vm.runInContext(await readFile(new URL('../../../console/json-view.js', import.meta.url), 'utf8'), context);
  vm.runInContext(markdownSource, context);
  context.DeepAgentMarkdown.init();
  return {md:context.DeepAgentMarkdown, json:context.DeepAgentJson, copied,
    click(control, selector) { return click({target:{closest: query => query.includes(selector) ? control : null}, preventDefault(){}, stopPropagation(){}}); }};
}

test("工具参数JSON复用代码栏，保留args与轨迹类，默认阈值按实际格式化行数", async () => {
  const h = await interactiveCodeHarness();
  for (const [fields, collapsed] of [[13,false], [14,true]]) {
    const value = Object.fromEntries(Array.from({length:fields}, (_,n) => [`field${n}`, `<value ${n}>`]));
    const html = h.json.renderBlock(value, {className:'args trajectory-json is-flat',ariaLabel:'工具参数 JSON'});
    assert.equal(/<pre[^>]* hidden>/.test(html),collapsed);
    assert.match(html, /<pre class="json-view args trajectory-json is-flat" aria-label="工具参数 JSON"/);
    assert.match(html, /markdown-code-copy/); assert.match(html, /class="markdown-code-block is-wrapped"/);
    assert.doesNotMatch(html, /<value/); assert.match(html, new RegExp(`field${fields-1}`));
  }
});

test("手动展开或折叠及换行在同一正文重渲染恢复，未触碰的代码仍按新行数默认", async () => {
  const h = await interactiveCodeHarness(), first = codeDom();
  await h.click(first.toggle, 'markdown-code-toggle');
  assert.equal(first.pre.hidden,false); assert.equal(first.attrs.get('markdown-code-toggle:aria-expanded'),'true');
  await h.click(first.wrap, 'markdown-code-wrap');
  const saved = h.md.captureCodeState(first.root), next = codeDom();
  h.md.restoreCodeState(next.root,saved);
  assert.equal(next.pre.hidden,false); assert.equal(next.block.dataset.codeDisclosureTouched,'1');
  assert.equal(next.block.classList.contains('is-wrapped'),true);
  h.md.updateCodeBlock(next.block,Array(25).fill('line').join('\n'));
  assert.equal(next.pre.hidden,false, '原位更新不能覆盖手动展开');
  await h.click(next.toggle,'markdown-code-toggle');
  const final = codeDom({expanded:true}); h.md.restoreCodeState(final.root,h.md.captureCodeState(next.root));
  assert.equal(final.pre.hidden,true); assert.equal(final.attrs.get('markdown-code-toggle:aria-expanded'),'false');
  const untouched = codeDom({expanded:true}); h.md.updateCodeBlock(untouched.block,Array(16).fill('line').join('\n'));
  assert.equal(untouched.pre.hidden,true);
  const independent = codeDom({expanded:true}); h.md.restoreCodeState(independent.root,[]);
  assert.equal(independent.pre.hidden,false, '另一个代码块不继承全局状态');
});

test("折叠后复制仍取得完整原文，JSON参数更新保留code节点与手动状态", async () => {
  const h = await interactiveCodeHarness(), text = Array(20).fill('完整正文').join('\n'), dom = codeDom({text});
  await h.click(dom.copy,'markdown-code-copy');
  assert.equal(h.copied[0],text); assert.equal(dom.pre.hidden,true);
  await h.click(dom.toggle,'markdown-code-toggle');
  const code = dom.code;
  h.json.updateBlock(dom.pre,{updated:'new value',rows:Array(20).fill('item')});
  assert.equal(dom.code,code); assert.equal(dom.pre.hidden,false);
  assert.match(code.innerHTML,/new value/); assert.match(code.innerHTML,/json-key/);
  assert.match(chatSource,/DeepAgentJson\.updateBlock\(argsEl, merged\.args\)/);
  assert.match(chatSource,/restoreCodeState\?\.\(nextRow/);
});

test("流式正文替换调用真实renderer后恢复手动选择；工具代码按调用ID恢复而不串行", async () => {
  const h = await interactiveCodeHarness();
  let current = codeDom();
  await h.click(current.toggle,'markdown-code-toggle');
  const host = {querySelectorAll: () => [current.block], set innerHTML(html) {
    current = codeDom({expanded: !/<pre[^>]* hidden>/.test(html), language:'json'});
  }};
  h.md.replaceBody(host,'```json\n'+Array(20).fill('{}').join('\n')+'\n```');
  assert.equal(current.pre.hidden,false);
  assert.equal(current.block.dataset.codeDisclosureTouched,'1');
  const attachTool = (dom,id) => { dom.block.closest = () => ({dataset:{toolCallId:id}}); dom.pre.getAttribute = () => '工具参数 JSON'; return dom; };
  const old = attachTool(codeDom(),'call-a'); await h.click(old.toggle,'markdown-code-toggle');
  const saved = h.md.captureCodeState(old.root), other = attachTool(codeDom(),'call-b'), same = attachTool(codeDom(),'call-a');
  h.md.restoreCodeState({querySelectorAll: () => [other.block,same.block]},saved);
  assert.equal(other.pre.hidden,true); assert.equal(same.pre.hidden,false);
});
