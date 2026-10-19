(function initDeepAgentMarkdown(global) {
  "use strict";

  let ready = false;
  let mermaidLoader = null;
  let diagramSequence = 0;
  let renderSequence = 0;
  let markdownOptions;
  let mathExtension;

  function codeIcon(name) {
    const definition = global.lucide?.icons?.[name];
    if (!definition) return "";
    // Serialize only the trusted bundled Lucide definition, never message HTML.
    const serialize = ([tag, attributes, children = []]) => `<${tag} ${Object.entries(attributes).map(([key, value]) => `${key}="${escapeHtml(value)}"`).join(" ")}>${children.map(serialize).join("")}</${tag}>`;
    return serialize([definition[0], { ...definition[1], "aria-hidden": "true", class: "markdown-code-icon" }, definition[2]]);
  }

  const CODE_PREVIEW_LINES = 15;
  const longCode = source => String(source ?? "").replace(/\r\n?/g, "\n").replace(/\n$/, "").split("\n").length > CODE_PREVIEW_LINES;

  function codeBlock(highlighted, language, { source = "", preClass = "", ariaLabel = "", wrapped = false } = {}) {
    const label = language || "纯文本", collapsed = longCode(source);
    return `<div class="markdown-code-block${wrapped ? " is-wrapped" : ""}" data-code-language="${escapeHtml(language || "")}"><div class="markdown-code-header"><button type="button" class="markdown-code-toggle" aria-expanded="${!collapsed}" aria-label="${collapsed ? "展开代码" : "折叠代码"}">${codeIcon("Code")}<span title="代码语言：${escapeHtml(label)}">${escapeHtml(label)}</span><span class="markdown-code-chevron">${codeIcon("ChevronDown")}</span></button><div class="markdown-code-actions"><button type="button" class="markdown-code-wrap" aria-label="自动换行" title="${wrapped ? "关闭自动换行" : "自动换行"}" aria-pressed="${wrapped}">${codeIcon("WrapText")}</button><button type="button" class="markdown-code-copy" aria-label="复制代码" title="复制代码">${codeIcon("Copy")}</button></div></div><pre${preClass ? ` class="${escapeHtml(preClass)}"` : ""}${ariaLabel ? ` aria-label="${escapeHtml(ariaLabel)}"` : ""}${collapsed ? " hidden" : ""}><code class="hljs${language ? ` language-${escapeHtml(language)}` : ""}">${highlighted}</code></pre></div>`;
  }

  function setCodeExpanded(block, expanded) {
    const pre = block?.querySelector("pre"), toggle = block?.querySelector(".markdown-code-toggle");
    if (!pre || !toggle) return;
    pre.hidden = !expanded;
    toggle.setAttribute("aria-expanded", String(expanded));
    toggle.setAttribute("aria-label", expanded ? "折叠代码" : "展开代码");
  }

  function updateCodeBlock(block, source) {
    if (block && !block.dataset.codeDisclosureTouched) setCodeExpanded(block, !longCode(source));
  }

  function captureCodeState(root) {
    return [...(root?.querySelectorAll?.(".markdown-code-block") || [])].map((block, index) => ({
      index, language: block.dataset.codeLanguage || "", expanded: !block.querySelector("pre")?.hidden,
      toolCallId: block.closest?.("[data-tool-call-id]")?.dataset.toolCallId || "",
      label: block.querySelector("pre")?.getAttribute?.("aria-label") || "",
      touched: Boolean(block.dataset.codeDisclosureTouched), wrapped: block.classList.contains("is-wrapped"),
      wrapTouched: Boolean(block.dataset.codeWrapTouched),
    })).filter(item => item.touched || item.wrapTouched);
  }

  function restoreCodeState(root, saved = []) {
    const blocks = [...(root?.querySelectorAll?.(".markdown-code-block") || [])];
    for (const item of saved) {
      const block = item.toolCallId ? blocks.find(candidate => candidate.closest?.("[data-tool-call-id]")?.dataset.toolCallId === item.toolCallId && (candidate.querySelector("pre")?.getAttribute?.("aria-label") || "") === item.label) : blocks[item.index];
      if (!block || (block.dataset.codeLanguage || "") !== item.language) continue;
      if (item.touched) { block.dataset.codeDisclosureTouched = "1"; setCodeExpanded(block, item.expanded); }
      if (item.wrapTouched) {
        block.dataset.codeWrapTouched = "1";
        block.classList.toggle("is-wrapped", item.wrapped);
        const wrap = block.querySelector(".markdown-code-wrap");
        wrap?.setAttribute("aria-pressed", String(item.wrapped));
        if (wrap) wrap.title = item.wrapped ? "关闭自动换行" : "自动换行";
      }
    }
  }

  function replaceBody(root, source) {
    const saved = captureCodeState(root);
    root.innerHTML = renderBody(source);
    restoreCodeState(root, saved);
  }

  async function handleMarkdownClick(event) {
    const toggle = event.target.closest?.(".markdown-code-toggle, .markdown-code-wrap");
    if (toggle?.closest(".markdown-body")) {
      const block = toggle.closest(".markdown-code-block");
      event.preventDefault();
      event.stopPropagation();
      if (toggle.classList.contains("markdown-code-wrap")) {
        block.dataset.codeWrapTouched = "1";
        const wrapped = block.classList.toggle("is-wrapped");
        toggle.setAttribute("aria-pressed", String(wrapped));
        toggle.title = wrapped ? "关闭自动换行" : "自动换行";
      } else {
        block.dataset.codeDisclosureTouched = "1";
        setCodeExpanded(block, block.querySelector("pre").hidden);
      }
      return;
    }
    const button = event.target.closest?.(".markdown-code-copy, .markdown-table-copy");
    if (button?.closest(".markdown-body")) {
      const code = button.closest(".markdown-code-block")?.querySelector("pre > code");
      const tableSource = button.closest(".markdown-table-block")?.getAttribute("data-table-source");
      const isTable = button.classList.contains("markdown-table-copy");
      if ((isTable ? tableSource == null : !code) || button.disabled) return;
      const copyLabel = isTable ? "复制表格 Markdown" : "复制代码";
      event.preventDefault();
      event.stopPropagation();
      button.disabled = true;
      try {
        await global.navigator.clipboard.writeText(isTable ? JSON.parse(tableSource) : (code.textContent || ""));
        button.innerHTML = codeIcon("Check");
        button.setAttribute("aria-label", "已复制");
        button.title = "已复制";
      } catch (_) {
        button.innerHTML = codeIcon("CircleAlert");
        button.setAttribute("aria-label", "复制失败，请重试");
        button.title = "复制失败，请重试";
      } finally {
        global.setTimeout(() => { button.innerHTML = codeIcon("Copy"); button.setAttribute("aria-label", copyLabel); button.title = copyLabel; button.disabled = false; }, 1500);
      }
      return;
    }
    // Hash links in Markdown must scroll locally, never replace the Console route.
    const link = event.target.closest?.('.markdown-body a[href^="#"]');
    if (!link) return;
    event.preventDefault();
    const id = link.getAttribute("href").slice(1);
    const target = [...link.closest(".markdown-body").querySelectorAll("[id]")].find(node => node.id === id);
    if (target) {
      for (let parent = target.parentElement; parent; parent = parent.parentElement) {
        if (parent.tagName === "DETAILS") parent.open = true;
      }
      // Scroll only the nearest owned pane. scrollIntoView also moves hidden
      // shell ancestors, clipping the Console header just like message focus.
      for (let pane = target.parentElement; pane && pane !== global.document.body && pane !== global.document.documentElement; pane = pane.parentElement) {
        if (!/(auto|scroll)/.test(global.getComputedStyle(pane).overflowY) || pane.scrollHeight <= pane.clientHeight) continue;
        const top = pane.getBoundingClientRect().top + pane.clientTop;
        const rect = target.getBoundingClientRect();
        if (rect.top < top) pane.scrollTop += rect.top - top;
        else if (rect.bottom > top + pane.clientHeight) pane.scrollTop += Math.min(rect.top - top, rect.bottom - top - pane.clientHeight);
        break;
      }
      target.setAttribute("tabindex", "-1");
      target.focus({ preventScroll: true });
    }
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function normalizeLanguage(value) {
    return String(value || "").trim().split(/\s+/, 1)[0].toLowerCase();
  }

  function init() {
    if (ready) return true;
    if (typeof global.marked === "undefined" || typeof global.DOMPurify === "undefined") {
      console.warn("[deep-agent] Markdown 库未加载，将回退为纯文本");
      return false;
    }

    markdownOptions = {
      gfm: true,
      breaks: false,
      extensions: [{ name: "table", renderer(token) {
        // Marked 12 passes rendered strings to renderer.table; use its token
        // extension to retain the source, including alignment and inline syntax.
        const row = (cells, header) => this.parser.renderer.tablerow(cells.map((cell, i) =>
          this.parser.renderer.tablecell(this.parser.parseInline(cell.tokens), { header, align: token.align[i] })
        ).join(""));
        const table = this.parser.renderer.table(row(token.header, true), token.rows.map(cells => row(cells, false)).join(""));
        return `<div class="markdown-table-block" data-table-source="${escapeHtml(JSON.stringify(token.raw))}">${table}<button type="button" class="markdown-table-copy" aria-label="复制表格 Markdown" title="复制表格 Markdown">${codeIcon("Copy")}</button></div>\n`;
      } }],
      renderer: {
        blockquote(quote) {
          const match = /^<p>\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\](?:\n|<br\s*\/?>|<\/p>)/.exec(quote);
          if (!match) return `<blockquote>\n${quote}</blockquote>\n`;
          const labels = { NOTE: "备注", TIP: "提示", IMPORTANT: "重要", WARNING: "警告", CAUTION: "注意" };
          const body = match[0].endsWith("</p>") ? quote.slice(match[0].length) : `<p>${quote.slice(match[0].length)}`;
          return `<blockquote class="markdown-alert markdown-alert-${match[1].toLowerCase()}"><p class="markdown-alert-title">${labels[match[1]]}</p>${body}</blockquote>\n`;
        },
        table(header, body) {
          return `<div class="markdown-table-scroll" tabindex="0" role="region" aria-label="表格，可横向滚动"><table><thead>${header}</thead>${body ? `<tbody>${body}</tbody>` : ""}</table></div>\n`;
        },
        code(codeText, lang) {
          const text = String(codeText ?? "");
          const language = normalizeLanguage(lang);
          if (language === "mermaid") {
            return `<div class="mermaid-diagram" data-mermaid-state="pending">${codeBlock(escapeHtml(text), language, { source: text })}</div>\n`;
          }

          let highlighted = escapeHtml(text);
          if (typeof global.hljs !== "undefined") {
            if (language && global.hljs.getLanguage(language)) {
              try {
                highlighted = global.hljs.highlight(text, { language, ignoreIllegals: true }).value;
              } catch (_) { /* keep escaped fallback */ }
            }
          }
          return `${codeBlock(highlighted, language, { source: text })}\n`;
        },
      },
    };
    global.marked.use(markdownOptions);

    if (global.markedKatex && global.katex) {
      const options = { throwOnError: false, trust: false, strict: "ignore", maxExpand: 1000, maxSize: 20 };
      mathExtension = global.markedKatex(options);
      const inlineMath = mathExtension.extensions.find(extension => extension.name === "inlineKatex");
      const tokenizeMath = inlineMath.tokenizer;
      inlineMath.tokenizer = function(src, tokens) {
        const token = tokenizeMath.call(this, src, tokens);
        // "$5 and $10" is a pair of prices, not an inline formula.
        if (token && /^\$\d/.test(token.raw) && /\s\$$/.test(token.raw)) return;
        return token;
      };
      // Also accept the TeX delimiters commonly emitted by assistants.
      for (const [name, level, pattern, start] of [
        ["bracketMath", "block", /^\\\[\s*\n?([\s\S]*?)\\\](?:\n|$)/, "\\["],
        ["parenMath", "inline", /^\\\(([^\n]*?)\\\)/, "\\("],
      ]) {
        mathExtension.extensions.push({ name, level,
          start(src) { const index = src.indexOf(start); return index < 0 ? undefined : index; },
          tokenizer(src) { const match = pattern.exec(src); if (match) return { type: name, raw: match[0], text: match[1] }; },
          renderer(token) { return global.katex.renderToString(token.text, { ...options, displayMode: level === "block" }); },
        });
      }
      global.marked.use(mathExtension);
    }
    global.document?.addEventListener("click", handleMarkdownClick);

    global.DOMPurify.addHook("afterSanitizeAttributes", (node) => {
      if (node.tagName === "A" && node.hasAttribute("href") && !node.getAttribute("href").startsWith("#")) {
        node.setAttribute("target", "_blank");
        node.setAttribute("rel", "noopener noreferrer");
      }
    });
    ready = true;
    return true;
  }

  function scopedFootnotes(prefix) {
    const extension = global.markedFootnote({ prefixId: prefix, description: "脚注", refMarkers: true });
    const references = new Map();
    // Use numeric note IDs and separate namespaces. The pinned plugin uses raw
    // labels in attributes and repeats reference IDs; suffixing those labels
    // cannot distinguish a repeated "a" from a literal "a-2" or "ref-a".
    extension.extensions.find(item => item.name === "footnoteRef").renderer = function({ id }) {
      const count = (references.get(id) || 0) + 1;
      references.set(id, count);
      return `<sup><a id="${prefix}ref-${id}-${count}" href="#${prefix}note-${id}" data-footnote-ref aria-describedby="${prefix}label">[${id}]</a></sup>`;
    };
    extension.extensions.find(item => item.name === "footnotes").renderer = function({ items = [] }) {
      if (!items.length) return "";
      const notes = items.map(({ content, refs }) => {
        const id = refs[0].id;
        const body = this.parser.parse(content).trimEnd();
        const links = refs.map((ref, index) => ` <a href="#${prefix}ref-${id}-${index + 1}" data-footnote-backref aria-label="返回引用 ${id}${index ? `（${index + 1}）` : ""}">↩${index ? `<sup>${index + 1}</sup>` : ""}</a>`).join("");
        return `<li id="${prefix}note-${id}">${body.endsWith("</p>") ? body.slice(0, -4) + links + "</p>" : body + links}</li>`;
      }).join("\n");
      return `<section class="footnotes" data-footnotes><h2 id="${prefix}label" class="sr-only">脚注</h2><ol>${notes}</ol></section>`;
    };
    return extension;
  }

  function render(source) {
    const text = String(source ?? "");
    if (!init()) return `<pre class="md-fallback">${escapeHtml(text)}</pre>`;
    // Footnotes are scoped per message, including repeated renders during streaming.
    const parser = global.markedFootnote && text.includes("[^")
      ? new global.marked.Marked(markdownOptions, ...(mathExtension ? [mathExtension] : []), scopedFootnotes(`md-fn-${++renderSequence}-`))
      : global.marked;
    const raw = parser.parse(text);
    return global.DOMPurify.sanitize(raw, {
      USE_PROFILES: { html: true, mathMl: true, svg: true },
      ADD_ATTR: ["target", "rel", "class", "data-lang", "data-mermaid-state"],
    });
  }

  function renderBody(source) {
    return `<div class="markdown-body">${render(source)}</div>`;
  }

  function loadMermaid() {
    if (global.mermaid) return Promise.resolve(global.mermaid);
    if (mermaidLoader) return mermaidLoader;
    mermaidLoader = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = new URL("../vendor/mermaid.min.js", document.baseURI).href;
      script.async = true;
      script.onload = () => global.mermaid ? resolve(global.mermaid) : reject(new Error("Mermaid 未初始化"));
      script.onerror = () => reject(new Error("Mermaid 资源加载失败"));
      document.head.appendChild(script);
    });
    return mermaidLoader;
  }

  function mermaidTheme() {
    return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "neutral";
  }

  async function renderDiagram(node, mermaid) {
    const code = node.querySelector("code");
    if (!code) return;
    const definition = code.textContent || "";
    node.dataset.mermaidState = "rendering";
    try {
      const id = `deepagent-mermaid-${Date.now()}-${diagramSequence += 1}`;
      const result = await mermaid.render(id, definition);
      node.innerHTML = global.DOMPurify.sanitize(result.svg, {
        USE_PROFILES: { svg: true, svgFilters: true },
        ADD_ATTR: ["class", "style", "target"],
      });
      node.dataset.mermaidState = "rendered";
      const source = document.createElement("details");
      source.className = "markdown-diagram-source";
      source.innerHTML = `<summary>Mermaid 源码</summary>${codeBlock(escapeHtml(definition), "mermaid", { source: definition })}`;
      node.appendChild(source);
      result.bindFunctions?.(node);
    } catch (error) {
      node.dataset.mermaidState = "error";
      const message = document.createElement("p");
      message.className = "mermaid-diagram-error";
      message.textContent = "Mermaid 图表语法有误，已保留源代码。";
      node.appendChild(message);
      console.warn("[deep-agent] Mermaid 渲染失败", error);
    }
  }

  async function renderDiagrams(root) {
    if (!root?.querySelectorAll) return;
    const nodes = [...root.querySelectorAll('.mermaid-diagram[data-mermaid-state="pending"]')];
    if (!nodes.length) return;
    try {
      const mermaid = await loadMermaid();
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: "strict",
        theme: mermaidTheme(),
        htmlLabels: false,
        flowchart: { htmlLabels: false },
        maxTextSize: 50000,
      });
      for (const node of nodes) await renderDiagram(node, mermaid);
    } catch (error) {
      for (const node of nodes) {
        node.dataset.mermaidState = "error";
        if (!node.querySelector(".mermaid-diagram-error")) {
          const message = document.createElement("p");
          message.className = "mermaid-diagram-error";
          message.textContent = "Mermaid 资源暂不可用，已保留源代码。";
          node.appendChild(message);
        }
      }
      console.warn("[deep-agent] Mermaid 资源加载失败", error);
    }
  }

  global.DeepAgentMarkdown = Object.freeze({ init, render, renderBody, renderDiagrams, replaceBody, captureCodeState, restoreCodeState, updateCodeBlock, renderCodeBlock(highlighted, language, options) { init(); return codeBlock(highlighted, language, options); } });
})(window);
