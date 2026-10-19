import { escapeHtml as e, icon, localInput } from "./diagnostics-ui.js?v=20261019-release0138";

export const DIAGNOSTIC_PERIODS = [["24", "最近 1 天"], ["168", "最近 7 天"], ["720", "最近 30 天"], ["custom", "自定义时间"]];

export function diagnosticRangeHelpHtml(f, { formId, busy = false, formatTime = value => String(value || "") } = {}) {
    const custom = f.period === "custom";
    return `<details class="diagnostic-range-settings"><summary aria-label="${custom ? "设置自定义时间" : "查看查询时间范围和时区"}" title="${custom ? "设置自定义时间" : "查看查询时间范围和时区"}">${icon(custom ? "settings" : "info")}</summary><div class="diagnostic-range-popover" role="group" aria-label="${custom ? "自定义时间设置" : "查询时间范围"}"><strong>${custom ? "自定义时间" : "查询时间范围"}</strong>${custom ? `<div class="diagnostics-custom-time"><label>开始时间<input form="${e(formId)}" type="datetime-local" name="start" aria-label="开始时间" value="${localInput(f.start)}" required autocomplete="off"/></label><label>结束时间<input form="${e(formId)}" type="datetime-local" name="end" aria-label="结束时间" value="${localInput(f.end)}" required autocomplete="off"/></label><span class="diagnostic-range-zone">时区 · ${e(f.timezone)}</span><button form="${e(formId)}" class="diagnostics-button primary" type="submit" ${busy ? "disabled" : ""}>应用</button></div>` : `<span class="diagnostic-range-line"><span>开始</span><time>${e(formatTime(f.start))}</time></span><span class="diagnostic-range-line"><span>结束</span><time>${e(formatTime(f.end))}</time></span><span class="diagnostic-range-zone">时区 · ${e(f.timezone)}</span>`}</div></details>`;
  }

export function bindDiagnosticRangeDismiss(root) {
  const click = event => { if (!event.target.closest(".diagnostic-range-settings,[data-value=custom]")) root.querySelectorAll(".diagnostic-range-settings[open]").forEach(node => { node.open = false; }); };
  const key = event => { if (event.key !== "Escape") return; const settings = root.querySelector(".diagnostic-range-settings[open]"); if (settings) { settings.open = false; settings.querySelector("summary")?.focus(); } };
  document.addEventListener("click", click); root.addEventListener("keydown", key);
  return () => { document.removeEventListener("click", click); root.removeEventListener("keydown", key); };
}
