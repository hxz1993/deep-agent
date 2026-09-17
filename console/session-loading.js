/** Shared transition for opening saved conversations in either host. */
export function sessionLoadingHtml() {
  return `<div class="session-loading hidden" data-detail-loading role="status" aria-live="polite"><span class="loading-spinner" aria-hidden="true"></span><strong data-detail-loading-label>正在加载会话…</strong><span>内容较多时需要稍候，加载完成后即可操作。</span></div>`;
}

// Give synchronous history rendering a paint boundary, including background tabs.
export function prepareSessionRender() {
  return new Promise(resolve => {
    if (!globalThis.requestAnimationFrame) { resolve(); return; }
    let frame;
    const done = () => { clearTimeout(timer); globalThis.cancelAnimationFrame?.(frame); resolve(); };
    const timer = setTimeout(done, 150);
    frame = globalThis.requestAnimationFrame(() => { frame = globalThis.requestAnimationFrame(done); });
  });
}

/** Both hosts use the same visibility, busy state and interaction lock. */
export function setSessionLoading(root, targets, message = true) {
  const active = message !== false;
  const overlay = root.querySelector("[data-detail-loading]");
  overlay.classList.toggle("hidden", !active);
  overlay.querySelector("[data-detail-loading-label]").textContent = message === true ? "正在加载会话…" : String(message || "");
  root.toggleAttribute("aria-busy", active);
  if (active) root.setAttribute("aria-busy", "true");
  for (const target of targets) target.inert = active;
}
