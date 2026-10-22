import { copyTextToClipboard } from "./message-actions.js?v=20260912-21";

const preferences = new Map();
const VIEW_PREFERENCE_KEY = "da_console_output_view_v1";
export function readOutputViewPreference(storage) {
  try { return storage?.getItem(VIEW_PREFERENCE_KEY) === "grouped" ? "grouped" : "full"; }
  catch { return "full"; }
}
export function writeOutputViewPreference(storage, mode) {
  if (!["full", "grouped"].includes(mode)) return;
  try { storage?.setItem(VIEW_PREFERENCE_KEY, mode); } catch { /* Read-only storage does not block rendering. */ }
}
const escape = value => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
export const serializeStructuredOutput = value => JSON.stringify(value, null, 2);
export function structuredOutputSections(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return null;
  return Object.entries(value).map(([name, output]) => ({ name, output, prose: typeof output === "string" }));
}
function jsonMarkup(value) {
  const text = serializeStructuredOutput(value);
  try {
    if (globalThis.hljs?.getLanguage?.("json")) return globalThis.hljs.highlight(text, { language: "json", ignoreIllegals: true }).value;
  } catch { /* Keep original JSON readable when highlighting is unavailable. */ }
  return escape(text);
}
function codeBlock(name, value, expanded) {
  const text = serializeStructuredOutput(value);
  // Keep the native value: generic JSON helpers may parse JSON-looking strings.
  const block = globalThis.DeepAgentMarkdown?.renderCodeBlock?.(jsonMarkup(value), "json", {
    source: text, label: name, wrapped: true, expanded, ariaLabel: `${name} JSON`,
  }) || `<div class="markdown-code-header"><code>${escape(name)}</code></div><pre><code class="hljs language-json">${jsonMarkup(value)}</code></pre>`;
  return `<section class="structured-output-json markdown-body">${block}</section>`;
}
export function renderStructuredOutput({ name, output, mode = "full", failed = false, expanded } = {}) {
  const sections = structuredOutputSections(output);
  const grouped = mode === "grouped" && sections !== null;
  const body = grouped && sections.length ? sections.map(section => section.prose
    ? `<section class="structured-output-text"><details open><summary><code>${escape(section.name)}</code></summary><div>${escape(section.output)}</div></details></section>`
    : codeBlock(section.name, section.output, expanded)).join("") : codeBlock("JSON", output, expanded);
  return `<div class="structured-output"><div class="structured-output-toolbar"><code class="structured-output-title">${escape(name)}</code><div class="console-segmented output-view-switch" role="group" aria-label="输出展示方式"><button type="button" data-output-mode="full" class="${!grouped ? "active" : ""}" aria-pressed="${!grouped}">完整 JSON</button><button type="button" data-output-mode="grouped" class="${grouped ? "active" : ""}" aria-pressed="${grouped}"${sections === null ? ' disabled title="数组及标量使用完整 JSON 展示"' : ""}>按结构分组</button></div><button type="button" class="icon-action" data-output-copy title="复制完整 JSON" aria-label="复制完整 JSON"><i data-lucide="copy"></i></button><button type="button" class="icon-action" data-output-download title="下载完整 JSON" aria-label="下载完整 JSON"><i data-lucide="download"></i></button></div>${failed ? '<p class="structured-output-failed">此输出未被接受，仅供排查。</p>' : ""}<div class="structured-output-body">${body}</div></div>`;
}
export function mountStructuredOutput(host, options = {}, { document = globalThis.document, window = globalThis.window } = {}) {
  const value = JSON.parse(JSON.stringify(options.output));
  const key = String(options.id || options.name || "output");
  let storage;
  try { storage = window?.localStorage; } catch { /* Storage can be denied by the browser. */ }
  let preference = { codeStates: preferences.get(key)?.codeStates || {}, mode: readOutputViewPreference(storage) };
  const capture = () => {
    preference.codeStates[preference.mode] = window?.DeepAgentMarkdown?.captureCodeState?.(host) || [];
    remember();
  };
  const remember = () => {
    preferences.delete(key); preferences.set(key, preference);
    if (preferences.size > 128) preferences.delete(preferences.keys().next().value);
  };
  const render = () => {
    host.innerHTML = renderStructuredOutput({ ...options, output: value, ...preference });
    window?.DeepAgentMarkdown?.restoreCodeState?.(host, preference.codeStates[preference.mode] || []);
    window?.lucide?.createIcons();
  };
  host.onclick = async event => {
    const button = event.target.closest("button");
    if (!button || !host.contains(button)) return;
    if (button.matches?.(".markdown-code-toggle, .markdown-code-wrap")) queueMicrotask(capture);
    if (button.dataset.outputMode) { capture(); preference = { ...preference, mode: button.dataset.outputMode }; writeOutputViewPreference(storage, preference.mode); remember(); render(); }
    if (button.hasAttribute("data-output-copy")) {
      try { await copyTextToClipboard(serializeStructuredOutput(value)); button.title = "已复制完整 JSON"; }
      catch { button.title = "复制失败，请重试"; }
    }
    if (button.hasAttribute("data-output-download")) {
      const url = window.URL.createObjectURL(new window.Blob([serializeStructuredOutput(value)], { type: "application/json;charset=utf-8" }));
      const link = document.createElement("a");
      link.href = url; link.download = `${String(options.name || "output").replace(/[^A-Za-z0-9_-]/g, "_")}.json`;
      document.body.appendChild(link);
      try { link.click(); } finally { link.remove(); window.setTimeout(() => window.URL.revokeObjectURL(url), 0); }
    }
  };
  render();
  return { value, dispose() { capture(); host.onclick = null; } };
}
