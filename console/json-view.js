(function initDeepAgentJson(global) {
  "use strict";

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function normalizedJson(value) {
    if (typeof value === "string") {
      const trimmed = value.trim();
      if (/^[\[{]/.test(trimmed)) {
        try { return JSON.parse(trimmed); } catch (_) { /* keep the original string */ }
      }
    }
    return value;
  }

  function stringify(value) {
    try {
      const output = JSON.stringify(normalizedJson(value), null, 2);
      return output === undefined ? "null" : output;
    } catch (_) {
      return JSON.stringify(String(value));
    }
  }

  function highlight(value) {
    const source = stringify(value);
    const tokenPattern = /("(?:\\.|[^"\\])*"\s*:)|("(?:\\.|[^"\\])*")|\b(true|false)\b|\b(null)\b|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g;
    let cursor = 0;
    let html = "";
    for (const match of source.matchAll(tokenPattern)) {
      const index = match.index ?? 0;
      html += escapeHtml(source.slice(cursor, index));
      const token = match[0];
      if (match[1]) {
        const colonIndex = token.lastIndexOf(":");
        const key = token.slice(0, colonIndex).trimEnd();
        const spacing = token.slice(key.length, colonIndex);
        html += `<span class="json-key">${escapeHtml(key)}</span>${escapeHtml(spacing)}:`;
      } else if (match[2]) {
        html += `<span class="json-string">${escapeHtml(token)}</span>`;
      } else if (match[3]) {
        html += `<span class="json-boolean">${escapeHtml(token)}</span>`;
      } else if (match[4]) {
        html += `<span class="json-null">${escapeHtml(token)}</span>`;
      } else {
        html += `<span class="json-number">${escapeHtml(token)}</span>`;
      }
      cursor = index + token.length;
    }
    html += escapeHtml(source.slice(cursor));
    return html;
  }

  function renderBlock(value, { className = "", ariaLabel = "JSON" } = {}) {
    const classes = ["json-view", className].filter(Boolean).join(" ");
    const highlighted = highlight(value);
    if (global.DeepAgentMarkdown?.renderCodeBlock) {
      return `<div class="markdown-body markdown-json-block">${global.DeepAgentMarkdown.renderCodeBlock(highlighted, "json", { source: stringify(value), preClass: classes, ariaLabel, wrapped: true })}</div>`;
    }
    return `<pre class="${escapeHtml(classes)}" aria-label="${escapeHtml(ariaLabel)}"><code>${highlighted}</code></pre>`;
  }

  function updateBlock(pre, value) {
    const code = pre.querySelector("code");
    if (code) code.innerHTML = highlight(value);
    else pre.innerHTML = `<code>${highlight(value)}</code>`;
    global.DeepAgentMarkdown?.updateCodeBlock?.(pre.closest(".markdown-code-block"), stringify(value));
  }

  global.DeepAgentJson = Object.freeze({ stringify, highlight, renderBlock, updateBlock });
})(window);
