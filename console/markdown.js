(function initDeepAgentMarkdown(global) {
  "use strict";

  let ready = false;
  let mermaidLoader = null;
  let diagramSequence = 0;

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

    global.marked.use({
      gfm: true,
      breaks: false,
      renderer: {
        code(codeText, lang) {
          const text = String(codeText ?? "");
          const language = normalizeLanguage(lang);
          if (language === "mermaid") {
            return `<div class="mermaid-diagram" data-mermaid-state="pending"><pre><code class="language-mermaid">${escapeHtml(text)}</code></pre></div>\n`;
          }

          let highlighted = escapeHtml(text);
          if (typeof global.hljs !== "undefined") {
            if (language && global.hljs.getLanguage(language)) {
              try {
                highlighted = global.hljs.highlight(text, { language, ignoreIllegals: true }).value;
              } catch (_) { /* keep escaped fallback */ }
            } else {
              try {
                highlighted = global.hljs.highlightAuto(text).value;
              } catch (_) { /* keep escaped fallback */ }
            }
          }
          const langAttr = language ? ` class="hljs language-${escapeHtml(language)}"` : ' class="hljs"';
          return `<pre><code${langAttr}>${highlighted}</code></pre>\n`;
        },
      },
    });

    global.DOMPurify.addHook("afterSanitizeAttributes", (node) => {
      if (node.tagName === "A" && node.hasAttribute("href")) {
        node.setAttribute("target", "_blank");
        node.setAttribute("rel", "noopener noreferrer");
      }
    });
    ready = true;
    return true;
  }

  function render(source) {
    const text = String(source ?? "");
    if (!init()) return `<pre class="md-fallback">${escapeHtml(text)}</pre>`;
    const raw = global.marked.parse(text);
    return global.DOMPurify.sanitize(raw, {
      USE_PROFILES: { html: true },
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

  global.DeepAgentMarkdown = Object.freeze({ init, render, renderBody, renderDiagrams });
})(window);
