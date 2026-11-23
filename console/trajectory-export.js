import { renderIconsWithin } from "./render-scheduler.js?v=20261105-v01510";

// File packaging is independent from the chat transport: aborting an export
// must never abort the active model request or change the saved conversation.
export function trajectoryExportUrl(scope, suffix = "", includeFiles = false) {
  const prefix = scope.readOnly ? "api/v1/diagnostics/sessions/" : "api/v1/sessions/";
  const params = new URLSearchParams();
  if (!scope.readOnly && scope.agentId) params.set("agent_id", scope.agentId);
  if (includeFiles) params.set("include_files", "true");
  const query = params.toString();
  return `${prefix}${encodeURIComponent(scope.sessionId)}/trajectory-export${suffix}${query ? `?${query}` : ""}`;
}

export function fileExportUnavailable(scope) {
  if (!scope.sessionId) return "请先创建会话";
  if (scope.running) return "运行结束或停止后可导出轨迹与文件";
  if (!scope.ready) return "历史轨迹完整读取后可导出文件";
  return "";
}

export function fileExportEstimate(preview) {
  const bytes = preview.known_size_bytes;
  const size = bytes < 1024 ? `${bytes} B` : bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} KB`
    : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return {
    title: preview.file_count > 0 && preview.unknown_size_count === preview.file_count
      ? `预计包含 ${preview.file_count} 个文件，大小未知` : preview.unknown_size_count
      ? `预计包含 ${preview.file_count} 个文件，已知大小约 ${size}`
      : `预计包含 ${preview.file_count} 个文件，约 ${size}`,
    detail: `用户上传 ${preview.upload_count} 个 · 工具产物 ${preview.artifact_count} 个 · 按文件去重`,
    unknown: preview.unknown_size_count ? `另有 ${preview.unknown_size_count} 个文件大小未知，实际下载时核对。` : "",
  };
}

function validatePreview(value, sessionId) {
  const counts = ["file_count", "upload_count", "artifact_count", "known_size_bytes", "unknown_size_count", "event_head_sequence"];
  if (!value || value.session_id !== sessionId || counts.some(key => !Number.isSafeInteger(value[key]) || value[key] < 0)
    || typeof value.snapshot_digest !== "string" || !value.snapshot_digest
    || value.upload_count + value.artifact_count !== value.file_count || value.unknown_size_count > value.file_count
    || typeof value.can_export !== "boolean" || !Number.isSafeInteger(value.limits?.max_trace_bytes) || value.limits.max_trace_bytes <= 0) {
    throw new Error("文件预估信息不完整，请重试。");
  }
  return value;
}

async function responseError(response) {
  let detail;
  try { const body = await response.json(); detail = body?.detail || body?.message; } catch { /* An HTML login page is not an export. */ }
  const error = new Error(typeof detail === "string" ? detail : `导出请求失败（HTTP ${response.status}）`);
  error.status = response.status;
  return error;
}

/** Read a fresh, complete persisted snapshot only when a ZIP is requested. */
export async function readExportHistory(scope, preview, { request, url = value => value, signal } = {}) {
  const prefix = scope.readOnly ? "api/v1/diagnostics/sessions/" : "api/v1/sessions/";
  const items = [];
  let after = 0, historyBytes = 2;
  for (;;) {
    signal?.throwIfAborted();
    const params = new URLSearchParams({ after_sequence: String(after), limit: "500" });
    if (!scope.readOnly && scope.agentId) params.set("agent_id", scope.agentId);
    const response = await request(url(`${prefix}${encodeURIComponent(scope.sessionId)}/events?${params}`), { signal });
    if (!response.ok) throw await responseError(response);
    let page;
    try { page = await response.json(); } catch { throw new Error("完整轨迹未返回有效数据，请检查登录状态后重试。"); }
    if (!Array.isArray(page?.items)) throw new Error("完整轨迹信息不完整，请重试。");
    // Account each page once rather than repeatedly serializing the accumulated
    // history. The final projected trajectory is checked again before POST.
    if (page.items.length) historyBytes += new Blob([JSON.stringify(page.items)]).size - 2 + (items.length ? 1 : 0);
    if (historyBytes > preview.limits.max_trace_bytes) throw new Error("轨迹 JSON 超过导出上限，请改用仅轨迹下载。");
    for (const event of page.items) {
      if (!Number.isSafeInteger(event?.sequence) || event.sequence <= after) throw new Error("轨迹分页顺序异常，请重新预估。");
      after = event.sequence;
      items.push(event);
    }
    if (after > preview.event_head_sequence) break;
    if (page.next_after_sequence == null) {
      if (after === preview.event_head_sequence) return { session_id: scope.sessionId, items };
      break;
    }
    if (!Number.isSafeInteger(page.next_after_sequence) || page.next_after_sequence !== after || !page.items.length) {
      throw new Error("轨迹分页未完整读取，请重试。");
    }
  }
  const error = new Error("会话轨迹已变化，请重新预估后下载。");
  error.status = 409;
  throw error;
}

export function createTrajectoryFileExport({ getScope, getTrajectory, prepareTrajectory = null, request, url = value => value,
  view, saveBlob, notify = () => {} } = {}) {
  let generation = 0, active = false, disposed = false, operation = null, preview = null, prepared = null, owner = "";
  const key = scope => `${scope.readOnly ? "diagnostic" : "ordinary"}:${scope.agentId || ""}:${scope.sessionId || ""}`;
  const current = () => !disposed && active && owner === key(getScope()) && !fileExportUnavailable(getScope());
  function abort() { operation?.abort(); operation = null; }
  function close() {
    if (!active && !operation) return;
    active = false; generation++; abort(); preview = null; prepared = null; owner = "";
    view.close();
  }
  function sync() {
    if (active && !current()) close();
    view.availability?.(fileExportUnavailable(getScope()));
  }
  async function run(kind, callback) {
    if (!current() || operation) return;
    const controller = new AbortController(); operation = controller;
    const turn = generation;
    const valid = () => current() && turn === generation && operation === controller && !controller.signal.aborted;
    const timer = setTimeout(() => controller.abort(new Error("timeout")), kind === "preview" ? 15000 : 150000);
    try { await callback(controller.signal, valid); }
    catch (error) {
      if (!current() || turn !== generation || operation !== controller) return;
      preview = null; prepared = null;
      view.error(controller.signal.aborted ? "导出请求超时，请重试。" : error.message || "导出失败，请重试。", error.status === 409);
    } finally { clearTimeout(timer); if (operation === controller) operation = null; }
  }
  async function estimate() {
    if (!current() || operation) return;
    preview = null; prepared = null; view.loading();
    return run("preview", async (signal, valid) => {
      const scope = getScope();
      const response = await request(url(trajectoryExportUrl(scope, "/preview")), { signal });
      if (!response.ok) throw await responseError(response);
      let body;
      try { body = await response.json(); } catch { throw new Error("文件预估未返回有效数据，请检查登录状态后重试。"); }
      const data = validatePreview(body, scope.sessionId);
      if (!valid()) return;
      // Capture the digest before reading events, so a concurrent change is
      // rejected by the server instead of mixing trajectory and file snapshots.
      if (data.can_export && prepareTrajectory) {
        view.reading?.();
        const complete = await prepareTrajectory(signal, data, scope);
        if (!valid()) return;
        prepared = complete;
      }
      preview = data; view.estimate(data);
    });
  }
  return {
    async open() {
      if (disposed || active) return;
      const reason = fileExportUnavailable(getScope());
      if (reason) { notify(reason, true); return; }
      owner = key(getScope()); active = true; generation++; view.open();
      return estimate();
    },
    retry: estimate,
    close, sync,
    async download() {
      if (!current() || operation || !preview?.can_export) return;
      const snapshot = preview;
      let body, traceText;
      try {
        const trajectory = prepareTrajectory ? prepared : getTrajectory(); traceText = JSON.stringify(trajectory);
        if (!trajectory) throw new Error("missing trajectory");
        body = JSON.stringify({ trajectory, event_head_sequence: snapshot.event_head_sequence, snapshot_digest: snapshot.snapshot_digest });
      }
      catch { view.error("轨迹暂时无法序列化，请改用仅轨迹下载或刷新后重试。"); preview = null; return; }
      if (new Blob([traceText]).size > snapshot.limits.max_trace_bytes) { view.error("轨迹 JSON 超过导出上限，请改用仅轨迹下载。"); preview = null; return; }
      view.packing();
      return run("download", async (signal, valid) => {
        const scope = getScope();
        const response = await request(url(trajectoryExportUrl(scope, "", true)), {
          method: "POST", headers: { "Content-Type": "application/json" }, body, signal,
        });
        if (!response.ok) throw await responseError(response);
        if (response.headers.get("content-type")?.split(";")[0].trim() !== "application/zip") throw new Error("下载内容不是 ZIP，请检查登录状态后重试。");
        const status = response.headers.get("X-Deep-Agent-Export-Status");
        const count = name => { const value = response.headers.get(name); return /^\d+$/.test(value || "") ? Number(value) : NaN; };
        const included = count("X-Deep-Agent-Export-Included-Files");
        const skipped = count("X-Deep-Agent-Export-Skipped-Files");
        if (!["complete", "partial"].includes(status) || !Number.isSafeInteger(included) || included < 0
          || !Number.isSafeInteger(skipped) || skipped < 0 || included + skipped !== snapshot.file_count
          || (status === "complete" && skipped !== 0)) throw new Error("导出结果不完整，请重试。");
        const blob = await response.blob();
        if (!valid()) return;
        if (!blob.size) throw new Error("导出文件为空，请重试。");
        saveBlob(blob, `deepagent-trajectory-${String(scope.sessionId).slice(0, 12)}.zip`);
        close();
        notify(status === "partial" ? `已下载，${skipped} 个文件未包含，请查看包内清单。` : `已下载轨迹与 ${included} 个文件。`, status === "partial");
      });
    },
    destroy() { close(); disposed = true; generation++; },
  };
}

function downloadBlob(blob, filename) {
  const href = URL.createObjectURL(blob), link = document.createElement("a");
  link.href = href; link.download = filename; document.body.appendChild(link);
  try { link.click(); } finally { link.remove(); setTimeout(() => URL.revokeObjectURL(href), 1000); }
}

let exportViewId = 0;
export function mountTrajectoryExport({ button, getScope, getTrajectory, exportJson } = {}) {
  const doc = button?.ownerDocument || globalThis.document;
  // Presentation tests may deliberately provide only the trajectory rows.
  if (!button || !doc?.body?.appendChild) return null;
  const menu = doc.createElement("div"), dialog = doc.createElement("dialog");
  menu.id = `trajectory-export-menu-${++exportViewId}`; menu.className = "trajectory-export-menu"; menu.hidden = true;
  menu.setAttribute("role", "menu"); menu.setAttribute("aria-label", "选择导出内容");
  menu.innerHTML = `<button type="button" role="menuitem" data-export-json><i data-lucide="file-json-2"></i><span>仅轨迹</span><small>JSON</small></button><button type="button" role="menuitem" data-export-files><i data-lucide="archive"></i><span>轨迹与文件</span><small>ZIP</small></button>`;
  dialog.className = "modal trajectory-export-dialog";
  dialog.setAttribute("aria-labelledby", `${menu.id}-title`);
  dialog.innerHTML = `<header class="modal-header"><h3 id="${menu.id}-title">导出轨迹与文件</h3><button type="button" class="icon-action" data-export-close aria-label="关闭"><i data-lucide="x"></i></button></header><div class="modal-body"><p class="trajectory-export-progress" role="status" data-export-progress></p><div data-export-estimate hidden><p class="trajectory-export-total" data-export-total></p><p class="trajectory-export-detail" data-export-detail></p><p class="trajectory-export-unknown" data-export-unknown hidden></p></div><p class="trajectory-export-description">将完整轨迹与可下载的会话文件打包为 ZIP。<br>无法获取的文件会在包内清单中注明。</p><p class="trajectory-export-note">大小按原始文件估算，实际 ZIP 大小可能不同。</p><div class="trajectory-export-error" role="alert" data-export-error hidden></div><button class="btn text" type="button" data-export-retry hidden>重新预估</button></div><footer class="modal-footer"><button class="btn" type="button" data-export-cancel>取消</button><button class="btn primary" type="button" data-export-download disabled><i data-lucide="download"></i><span>下载 ZIP</span></button></footer>`;
  doc.body.appendChild(menu); doc.body.appendChild(dialog);
  button.setAttribute("aria-haspopup", "menu"); button.setAttribute("aria-expanded", "false"); button.setAttribute("aria-controls", menu.id);
  const chevron = doc.createElement("i"); chevron.dataset.lucide = "chevron-down"; chevron.className = "trajectory-export-chevron"; button.appendChild(chevron);
  const json = menu.querySelector("[data-export-json]"), files = menu.querySelector("[data-export-files]");
  const find = selector => dialog.querySelector(selector), download = find("[data-export-download]"), retry = find("[data-export-retry]");
  const binders = [];
  const listen = (node, event, handler) => { node.addEventListener(event, handler); binders.push(() => node.removeEventListener(event, handler)); };
  function closeMenu(focus = false) { menu.hidden = true; button.setAttribute("aria-expanded", "false"); if (focus) button.focus(); }
  function availability(reason) { files.disabled = Boolean(reason); files.title = reason; }
  function clearError() { find("[data-export-error]").hidden = true; retry.hidden = true; }
  const view = {
    availability,
    open() { closeMenu(); dialog.showModal(); },
    close() { if (dialog.open) dialog.close(); },
    loading() { clearError(); find("[data-export-estimate]").hidden = true; const status = find("[data-export-progress]"); status.hidden = false; status.textContent = "正在统计会话文件…"; download.disabled = true; download.querySelector("span").textContent = "下载 ZIP"; },
    reading() { find("[data-export-progress]").textContent = "正在读取完整轨迹…"; },
    estimate(value) {
      clearError(); find("[data-export-progress]").hidden = true; find("[data-export-estimate]").hidden = false;
      const label = fileExportEstimate(value); find("[data-export-total]").textContent = label.title; find("[data-export-detail]").textContent = label.detail;
      const unknown = find("[data-export-unknown]"); unknown.hidden = !label.unknown; unknown.textContent = label.unknown;
      download.disabled = !value.can_export;
      if (!value.can_export) { const error = find("[data-export-error]"); error.hidden = false; error.textContent = value.message || "文件超过导出上限，请改用仅轨迹下载。"; }
    },
    packing() { clearError(); const status = find("[data-export-progress]"); status.hidden = false; status.textContent = "正在打包，可取消本次导出…"; download.disabled = true; download.querySelector("span").textContent = "正在打包…"; },
    error(message) { find("[data-export-progress]").hidden = true; const error = find("[data-export-error]"); error.hidden = false; error.textContent = message; retry.hidden = false; download.disabled = true; download.querySelector("span").textContent = "下载 ZIP"; },
  };
  const controller = createTrajectoryFileExport({ getScope, getTrajectory,
    prepareTrajectory: async (signal, preview, scope) => getTrajectory(await readExportHistory(scope, preview, {
      request: (...args) => window.daFetch(...args), url: value => window.serviceUrl(value), signal,
    })),
    request: (...args) => window.daFetch(...args), url: value => window.serviceUrl(value), view,
    saveBlob: downloadBlob, notify: (message, error) => window.DeepAgentConsole?.toast(message, error),
  });
  function openMenu() {
    controller.sync(); if (button.disabled) return;
    const rect = button.getBoundingClientRect(); menu.hidden = false; button.setAttribute("aria-expanded", "true");
    menu.style.left = `${Math.max(8, Math.min(rect.right - 176, window.innerWidth - 184))}px`;
    menu.style.top = `${Math.max(8, Math.min(rect.bottom + 6, window.innerHeight - menu.offsetHeight - 8))}px`;
  }
  listen(button, "click", () => menu.hidden ? openMenu() : closeMenu());
  listen(button, "keydown", event => { if (event.key === "ArrowDown") { event.preventDefault(); openMenu(); json.focus(); } });
  listen(json, "click", () => { closeMenu(true); exportJson(); });
  listen(files, "click", () => { closeMenu(); void controller.open(); });
  listen(menu, "keydown", event => {
    if (event.key === "Escape") { event.preventDefault(); closeMenu(true); }
    if (["ArrowDown", "ArrowUp"].includes(event.key)) { event.preventDefault(); (doc.activeElement === json && !files.disabled ? files : json).focus(); }
    if (event.key === "Tab") closeMenu();
  });
  listen(doc, "pointerdown", event => { if (!menu.contains(event.target) && !button.contains(event.target)) closeMenu(); });
  listen(window, "resize", () => closeMenu()); listen(window, "scroll", () => closeMenu());
  listen(window, "hashchange", () => { closeMenu(); controller.close(); });
  listen(window, "pagehide", () => controller.close());
  listen(dialog, "close", () => { controller.close(); button.focus(); });
  listen(find("[data-export-close]"), "click", () => controller.close()); listen(find("[data-export-cancel]"), "click", () => controller.close());
  listen(download, "click", () => void controller.download()); listen(retry, "click", () => void controller.retry());
  renderIconsWithin(menu, window.lucide); renderIconsWithin(dialog, window.lucide); renderIconsWithin(button, window.lucide);
  controller.sync();
  return { sync: controller.sync, close() { closeMenu(); controller.close(); }, destroy() { controller.destroy(); binders.splice(0).forEach(remove => remove()); menu.remove(); dialog.remove(); button.removeAttribute("aria-controls"); button.removeAttribute("aria-haspopup"); button.removeAttribute("aria-expanded"); button.querySelector(".trajectory-export-chevron")?.remove(); } };
}
