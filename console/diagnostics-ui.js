export const escapeHtml = value => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
export const icon = name => `<i data-lucide="${escapeHtml(name)}" aria-hidden="true"></i>`;
export const shortId = value => String(value || "").length > 22 ? `${String(value).slice(0, 10)}…${String(value).slice(-8)}` : String(value || "");
export const icons = () => window.lucide?.createIcons();
export function localInput(utc) {
  const date = new Date(utc);
  if (!Number.isFinite(date.getTime())) return "";
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}
export function selectHtml(name, label, value, options) {
  const e = escapeHtml;
  const badge = decoration => decoration ? `<span class="diagnostic-select-badge" title="${e(decoration.label)}" aria-label="${e(decoration.label)}">${icon(decoration.icon)}</span>` : "";
  const selected = options.find(([id]) => String(id) === String(value));
  return `<div class="console-select" data-diagnostic-control="${e(name)}"><button type="button" class="console-select-trigger" aria-label="${e(label)}" aria-haspopup="listbox" aria-expanded="false"><span>${e(selected?.[1] || value || label)}</span>${badge(selected?.[2])}${icon("chevron-down")}</button><div class="console-select-menu hidden" role="listbox" aria-label="${e(label)}">${options.map(([id, text, decoration]) => `<button type="button" role="option" class="console-select-option${String(id) === String(value) ? " selected" : ""}" aria-selected="${String(id) === String(value)}" data-value="${e(id)}">${decoration ? `<span>${e(text)}</span>${badge(decoration)}` : e(text)}</button>`).join("")}</div></div>`;
}

// Delegate from a stable host. Mirrors the Console select's keyboard contract.
export function bindDiagnosticSelects(root, onSelect) {
  const close = () => {
    root.querySelectorAll(".console-select-menu").forEach(node => node.classList.add("hidden"));
    root.querySelectorAll(".console-select-trigger").forEach(node => node.setAttribute("aria-expanded", "false"));
  };
  const open = host => {
    close();
    const trigger = host.querySelector(".console-select-trigger"), menu = host.querySelector(".console-select-menu");
    menu.classList.remove("hidden", "open-up"); trigger.setAttribute("aria-expanded", "true");
    const bounds = trigger.getBoundingClientRect();
    if (menu.getBoundingClientRect().height > window.innerHeight - bounds.bottom - 8 && bounds.top > window.innerHeight - bounds.bottom) menu.classList.add("open-up");
    menu.querySelector('[aria-selected="true"]')?.focus();
  };
  const click = event => {
    const host = event.target.closest("[data-diagnostic-control]");
    if (!host || !root.contains(host)) return;
    if (event.target.closest(".console-select-trigger")) {
      host.querySelector(".console-select-menu").classList.contains("hidden") ? open(host) : close();
    } else {
      const option = event.target.closest("[data-value]");
      if (option) { close(); onSelect(host.dataset.diagnosticControl, option.dataset.value); }
    }
  };
  const key = event => {
    const host = event.target.closest("[data-diagnostic-control]");
    if (!host) return;
    const trigger = host.querySelector(".console-select-trigger"), menu = host.querySelector(".console-select-menu");
    if (event.key === "Escape") { close(); trigger.focus(); return; }
    if (event.key === "Tab") { close(); return; }
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    if (menu.classList.contains("hidden")) return open(host);
    const options = [...menu.querySelectorAll('[role="option"]')];
    const index = options.indexOf(document.activeElement);
    options[event.key === "Home" ? 0 : event.key === "End" ? options.length - 1 : (index + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length]?.focus();
  };
  const outside = event => { if (!event.target.closest("[data-diagnostic-control]")) close(); };
  root.addEventListener("click", click); root.addEventListener("keydown", key);
  document.addEventListener("click", outside); window.addEventListener("resize", close);
  return { close, dispose: () => { close(); root.removeEventListener("click", click); root.removeEventListener("keydown", key); document.removeEventListener("click", outside); window.removeEventListener("resize", close); } };
}

export async function diagnosticRequest(path, signal) {
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  if (signal?.aborted) abort();
  const timeout = setTimeout(abort, 30000);
  try {
    const response = await window.daFetch(window.serviceUrl(path), { signal: controller.signal, includeContext: false, reportStatus: false, cache: "no-store" });
    const body = await response.json().catch(() => null);
    if (!response.ok) {
      if ([401, 403].includes(response.status)) window.dispatchEvent(new CustomEvent("deep-agent-diagnostics-denied", { detail: { status: response.status } }));
      throw Object.assign(new Error(typeof body?.detail === "string" ? body.detail : `HTTP ${response.status}`), { status: response.status });
    }
    return body;
  } finally { clearTimeout(timeout); signal?.removeEventListener("abort", abort); }
}

export function bindDiagnosticResizer(handle, host, property, initial = 360) {
  let drag = null;
  const set = width => { const max = Math.max(240, Math.min(720, host.clientWidth - 260)); const value = Math.max(220, Math.min(max, width)); host.style.setProperty(property, `${value}px`); handle.setAttribute("aria-valuenow", String(value)); };
  const down = event => { if (event.button !== 0) return; drag = { x: event.clientX, width: Number.parseFloat(getComputedStyle(host).getPropertyValue(property)) || initial }; handle.setPointerCapture(event.pointerId); event.preventDefault(); };
  const move = event => { if (drag) set(drag.width + drag.x - event.clientX); };
  const finish = () => { drag = null; };
  const key = event => { if (["ArrowLeft", "ArrowRight", "Home"].includes(event.key)) { event.preventDefault(); set(event.key === "Home" ? initial : (Number.parseFloat(getComputedStyle(host).getPropertyValue(property)) || initial) + (event.key === "ArrowLeft" ? 20 : -20)); } };
  handle.addEventListener("pointerdown", down); handle.addEventListener("pointermove", move); handle.addEventListener("pointerup", finish); handle.addEventListener("pointercancel", finish); handle.addEventListener("lostpointercapture", finish); handle.addEventListener("keydown", key);
  return () => { handle.removeEventListener("pointerdown", down); handle.removeEventListener("pointermove", move); handle.removeEventListener("pointerup", finish); handle.removeEventListener("pointercancel", finish); handle.removeEventListener("lostpointercapture", finish); handle.removeEventListener("keydown", key); };
}
