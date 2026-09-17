import { createConversationPresenter } from "./conversation-presenter.js?v=20260912-21";
import { projectConversationItems, sortConversationItems } from "./conversation-items.js?v=20260912-21";
import { applyAssistantPresentationActions } from "./assistant-presentation.js?v=20260912-21";
import { buildTrajectory, createTrajectoryController, redactTrajectoryValue } from "./trajectory.js?v=20260915-scroll-focus";
import { centerInScrollContainer } from "./scroll.js?v=20260915-scroll-focus";
import { renderSessionInspector } from "./session-inspector.js?v=20260917-release01010";
import { channelOriginPresentation, formatSessionIdSubtitle } from "./session-display.js?v=20260915-session-icons";
import { copyTextToClipboard } from "./message-actions.js?v=20260912-21";
import { parseServerTimestamp } from "./time.js?v=20260912-21";
import { bindAttachmentImagePreviews } from "./attachment-preview.js?v=20260912-21";
import { createReadonlySessionShell } from "./session-view-shell.js?v=20260912-21";

const escapeHtml = (value) => String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const icon = (name) => `<i data-lucide="${name}" aria-hidden="true"></i>`;
const statusLabels = { running: "进行中", pending: "待操作", waiting: "待操作", awaiting_input: "待操作", completed: "已完成", stopped: "已停止", interrupted: "已停止", error: "异常", failed: "异常", unknown: "未确认", unconfirmed: "未确认", not_started: "未开始", empty: "未开始" };

/** Locate a saved Conversation item, never guess from visible text or truncated IDs. */
export function findConversationFocus(items = [], events = [], { eventId, runId } = {}) {
  const ordered = sortConversationItems(items);
  if (eventId) return ordered.find((item) => item.item_id === eventId || item.source_event_ids?.includes(eventId)) || null;
  if (!runId) return null;
  const ids = new Set(events.filter((event) => event.run_id === runId).map((event) => event.event_id));
  return ordered.find((item) => item.source_event_ids?.some((id) => ids.has(id))) || null;
}

/** Shared trajectory grouping contains complete message usage, not one count per part. */
export function savedRunFacts(events = [], runId = null) {
  const messages = buildTrajectory({ items: events }).filter((item) => item.kind === "assistant" && (!runId || item.run_id === runId));
  const usage = {};
  for (const item of messages) {
    for (const key of ["input_tokens", "output_tokens", "total_tokens", "cache_read_tokens", "cache_write_tokens"]) {
      const value = item.native?.usage?.[key];
      if (value != null && Number.isFinite(Number(value))) usage[key] = (usage[key] || 0) + Number(value);
    }
  }
  if (usage.total_tokens == null && (usage.input_tokens != null || usage.output_tokens != null)) {
    usage.total_tokens = (usage.input_tokens || 0) + (usage.output_tokens || 0);
  }
  const latest = messages.at(-1)?.native || {};
  return { usage, model_name: latest.model_name || null, model_id: latest.model_id || null, finish_reason: latest.finish_reason || null };
}

/** Metadata is authoritative and available before the lazy trace has loaded. */
export function diagnosticRunPresentation(session = {}, events = [], selectedRunId = null) {
  const runId = selectedRunId || session.run_id || session.runs?.at(-1)?.run_id;
  const storedRun = session.runs?.find((item) => item.run_id === runId) || {};
  const facts = events.length ? savedRunFacts(events, runId) : {};
  return {
    run: { ...facts, ...storedRun, run_id: runId, status: storedRun.status || session.latest_status },
    usage: storedRun.usage || facts.usage || {},
    model: session.model_name ? { name: session.model_name } : null,
    // The last model of the whole session need not be the model of this run.
    runModel: storedRun.model_name || facts.model_name ? { name: storedRun.model_name || facts.model_name } : null,
  };
}

function contextReferenceContent(reference) {
  if (!reference) return `<div class="inspector-tab-empty">${icon("braces")}<strong>尚未选择上下文引用</strong><span>点击对话中引用旁的信息图标查看已保存内容。</span></div>`;
  const safe = redactTrajectoryValue(reference);
  const json = globalThis.DeepAgentJson?.renderBlock(safe, { className: "inspector-json", ariaLabel: "上下文引用 JSON" })
    || `<pre class="inspector-json">${escapeHtml(JSON.stringify(safe, null, 2))}</pre>`;
  return `<section class="inspector-section context-reference-inspector"><header><strong>${escapeHtml(reference.label || reference.type || "上下文引用")}</strong></header>${json}</section>`;
}

/**
 * No request, execution controller, widget or observer is initialized here.
 * The diagnostics route supplies administrator-read data and owns all I/O.
 */
export function createReadonlySessionView({ root, onBack = () => {}, onRefresh = () => {}, onViewChange = () => {} } = {}) {
  if (!root) throw new TypeError("A diagnostics detail host is required");
  root.classList.add("diagnostic-session-detail");
  const chat = createReadonlySessionShell();
  const header = chat.querySelector(".chat-header");
  header.classList.add("diagnostic-detail-header");
  root.innerHTML = `<p class="diagnostic-detail-notice hidden" data-detail-notice role="status"></p>
  <div class="diagnostic-detail-grid">
    <div class="workspace-resizer workspace-resizer-inspector diagnostic-detail-resizer" role="separator" aria-label="调整会话详情宽度" aria-orientation="vertical" aria-valuemin="260" aria-valuemax="720" aria-valuenow="320" tabindex="0"></div>
    <aside class="inspector-pane" data-session-role="inspectorPane"><div class="inspector-content" data-session-role="inspectorContent"></div></aside>
    <div class="diagnostic-detail-loading hidden" data-detail-loading role="status" aria-live="polite"><span class="loading-spinner" aria-hidden="true"></span><strong data-detail-loading-label>正在加载会话…</strong><span>内容较多时需要稍候，加载完成后即可操作。</span></div>
  </div>
  <dialog class="attachment-image-dialog" data-detail-image-dialog><header class="attachment-image-dialog-header"><strong data-detail-image-title></strong><button type="button" class="icon-btn" data-detail-image-close aria-label="关闭图片预览">${icon("x")}</button></header><div class="attachment-image-dialog-content"><img data-detail-image alt="附件预览" referrerpolicy="no-referrer"><p class="hidden" data-detail-image-error>图片无法加载，可能无权限或链接已失效。</p></div><footer><a data-detail-image-open target="_blank" rel="noopener noreferrer" referrerpolicy="no-referrer">打开原图</a></footer></dialog>`;
  root.prepend(header);
  root.querySelector(".diagnostic-detail-grid").prepend(chat);
  const eventNotice = document.createElement("p");
  eventNotice.className = "diagnostic-detail-events-notice hidden";
  eventNotice.dataset.detailEventsNotice = "";
  eventNotice.setAttribute("role", "status");
  chat.querySelector(".trajectory-toolbar").after(eventNotice);
  const find = (selector) => root.querySelector(selector);
  const role = (name) => find(`[data-session-role="${name}"]`);
  const messages = role("messages");
  const state = { sessionId: null, conversationId: null };
  let data = { session: {}, history: { items: [] }, events: [], users: {} };
  let presenters = [];
  let renderedRows = [];
  let selectedReference = null;
  let inspectorTab = "session";
  let selectedRunId = null;
  let currentView = "conversation";
  let disposed = false;
  let lastFocusKey = null;
  let loadingSnapshot = null;
  let pageLoading = false;
  const disabledBeforeLoading = new Map();
  const listeners = [];
  const listen = (element, name, handler, options) => { element.addEventListener(name, handler, options); listeners.push(() => element.removeEventListener(name, handler, options)); };
  const refreshIcons = () => globalThis.lucide?.createIcons();
  const toast = (message, error = false) => globalThis.DeepAgentConsole?.toast?.(message, error);

  function showNotice(message = "") {
    const host = find("[data-detail-notice]");
    host.textContent = message;
    host.classList.toggle("hidden", !message);
  }

  function renderInspector() {
    if (disposed || currentView === "trajectory") return;
    const session = data.session;
    const { run, usage, model, runModel } = diagnosticRunPresentation(session, data.events, selectedRunId);
    const contextStatus = { ...(session.context_status || {}) };
    if (session.context_error) contextStatus.unavailable_reason = typeof session.context_error === "string" ? session.context_error : session.context_error.message || "当前配置无法估算";
    const status = session.latest_status?.status || session.latest_status || "unknown";
    const userId = session.user_id;
    role("inspectorContent").innerHTML = renderSessionInspector({
      diagnostic: true,
      state: { sessionId: session.session_id, agentId: session.agent_id, conversationId: session.conversation_id, modelMessageCount: data.history.items?.filter((item) => ["user_message", "assistant_message"].includes(item.kind)).length },
      activeSession: { appId: session.app_id, spaceId: session.space_id, turnCount: session.turn_count, messageCount: session.message_count, attachmentCount: session.attachment_count },
      model, runModel,
      run, usage, contextStatus, agentName: session.agent_name || session.agent_id || "未记录",
      context: { app: session.app_id, space: session.space_id, language: session.language }, identity: { user: userId || "", profile: data.users[userId] || {} },
      channelOrigin: channelOriginPresentation(session.channel), activeTab: inspectorTab,
      contextContent: contextReferenceContent(selectedReference?.reference),
      statusText: statusLabels[status] || "未确认", statusClass: status === "running" ? "running" : ["pending", "waiting", "awaiting_input"].includes(status) ? "waiting" : "idle",
    });
    refreshIcons();
  }

  const conversation = createConversationPresenter({ messages, readOnly: true, userLabel: "用户", showConsoleToast: toast,
    onContextReference(reference, branchPoint) {
      const same = selectedReference?.branchPoint === branchPoint && selectedReference?.reference === reference;
      selectedReference = same ? null : { reference, branchPoint };
      inspectorTab = "context"; setInspectorVisible(true); renderInspector();
      messages.querySelectorAll("[data-context-ref-id]").forEach((button) => button.setAttribute("aria-pressed", String(Boolean(selectedReference) && button.dataset.contextRefId === [branchPoint ?? "live", reference.key || "", reference.type || ""].join(":"))));
    },
    isContextReferenceSelected: (reference, branchPoint) => selectedReference?.reference === reference && selectedReference?.branchPoint === branchPoint,
  });
  const trajectory = createTrajectoryController({ state, host: root, readOnly: true,
    onViewChange(view) { currentView = view; if (view === "conversation") renderInspector(); onViewChange(view); },
    onInspector() { currentView = "conversation"; renderInspector(); },
    onOpenInspector() { currentView = "trajectory"; setInspectorVisible(true); },
    onSelectEvent(event) { if (currentView === "trajectory" && event?.run_id) selectedRunId = event.run_id; },
  });

  function setInspectorVisible(visible) {
    find(".diagnostic-detail-grid").classList.toggle("inspector-hidden", !visible);
    const button = find("[data-detail-inspector]");
    button.setAttribute("aria-pressed", String(visible));
    button.classList.toggle("active", visible);
    button.title = visible ? "隐藏详情" : "显示详情";
  }

  function renderConversation() {
    presenters.forEach((presenter) => presenter.dispose()); presenters = []; renderedRows = [];
    messages.replaceChildren();
    let day = "";
    const nodes = projectConversationItems(data.history.items || []);
    for (const node of nodes) {
      const date = parseServerTimestamp(node.createdAt);
      const key = date?.toLocaleDateString();
      if (key && key !== day) {
        const separator = document.createElement("div"); separator.className = "date-separator";
        separator.textContent = date.toLocaleDateString("zh-CN", { month: "long", day: "numeric" }); messages.append(separator); day = key;
      }
      let row;
      if (node.type === "user") {
        row = conversation.addMessage("user", node.text, node.createdAt, node.order?.sequence, node.attachments, node.contextRefs);
      } else {
        // Message event timestamps are not an exact execution duration.
        const presenter = conversation.createAssistantMessage(node.createdAt, node.order?.sequence, null, { allowDeferred: false, autoAttachDeferred: false });
        applyAssistantPresentationActions(presenter, node.actions); presenters.push(presenter); row = presenter.row;
      }
      row.dataset.conversationItemId = node.itemId;
      renderedRows.push({ node, row });
    }
    if (!nodes.length) messages.innerHTML = `<div class="empty-state">${icon("messages-square")}<h2>暂无已保存的对话</h2><p>此会话还没有可展示的输入或回复。</p></div>`;
    refreshIcons();
  }

  function markMessage(row, selected) {
    row.classList.toggle("diagnostic-message-focus", selected);
    row.querySelector(".diagnostic-message-marker")?.remove();
    if (selected) {
      const marker = document.createElement("span");
      marker.className = "diagnostic-message-marker";
      marker.textContent = "定位消息";
      row.querySelector(".bubble-wrap").prepend(marker);
    }
  }

  function focus(target = {}) {
    if (disposed) return false;
    const view = target.view || (target.eventId || target.runId ? "conversation" : currentView);
    currentView = view === "trajectory" ? "trajectory" : "conversation";
    trajectory.selectView(currentView, { notify: false });
    renderedRows.forEach(({ row }) => markMessage(row, false));
    if (!target.eventId && !target.runId) return true;
    if (currentView === "trajectory") {
      const found = trajectory.focus(target);
      if (!found) showNotice(target.eventId ? "未找到对应事件；当前展示此会话已保存的轨迹。" : "未找到该轮对应记录；当前展示此会话已保存的轨迹。");
      return found;
    }
    const item = findConversationFocus(data.history.items, data.events, target);
    if (!item) { showNotice(target.eventId ? "未找到对应消息；当前展示此会话已保存的对话。" : "未找到该轮对应记录；当前展示此会话已保存的对话。"); return false; }
    const targetSequence = Number(item.order?.sequence || 0);
    const found = [...renderedRows].reverse().find(({ node }) => Number(node.order?.sequence || 0) <= targetSequence);
    if (!found) return false;
    selectedRunId = target.runId || data.events.find((event) => item.source_event_ids?.includes(event.event_id))?.run_id
      || data.session.runs?.find((run) => run.event_ids?.some((id) => item.source_event_ids?.includes(id)))?.run_id || selectedRunId;
    markMessage(found.row, true);
    if (item.kind === "tool" || item.phase === "process") {
      found.row.querySelector(".assistant-process")?.classList.add("open");
      found.row.querySelector(".assistant-process-summary")?.setAttribute("aria-expanded", "true");
    }
    centerInScrollContainer(messages, found.row); renderInspector();
    return true;
  }

  function snapshot() {
    return { sessionId: state.sessionId, messagesScrollTop: messages.scrollTop, inspectorScrollTop: role("inspectorContent").scrollTop, inspectorTab, selectedRunId, selectedReference, lastFocusKey, inspectorVisible: !find(".diagnostic-detail-grid").classList.contains("inspector-hidden"), inspectorWidth: find(".diagnostic-detail-grid").style.getPropertyValue("--diagnostic-inspector-width"), trajectory: trajectory.snapshot(), view: currentView,
      focusedItemId: renderedRows.find(({ row }) => row.classList.contains("diagnostic-message-focus"))?.node.itemId || null,
      disclosures: renderedRows.map(({ node, row }) => ({ itemId: node.itemId, blocks: [...row.querySelectorAll(".assistant-process, .assistant-thinking, .tool-block")].map((block) => ({ open: block.classList.contains("open"), touched: Boolean(block.dataset.disclosureTouched) })) })),
    };
  }

  function restore(value) {
    if (!value || value.sessionId !== state.sessionId || disposed) return;
    inspectorTab = ["session", "run", "channel", "context"].includes(value.inspectorTab) ? value.inspectorTab : "session";
    selectedRunId = value.selectedRunId || null;
    selectedReference = value.selectedReference || null;
    lastFocusKey = value.lastFocusKey || null;
    currentView = value.view === "trajectory" ? "trajectory" : "conversation";
    trajectory.restore({ ...value.trajectory, view: currentView });
    // Background metadata/context reads rebuild rows but must preserve the
    // inspected message marker without scrolling the reader a second time.
    renderedRows.forEach(({ node, row }) => markMessage(row, Boolean(value.focusedItemId) && node.itemId === value.focusedItemId));
    for (const disclosure of value.disclosures || []) {
      const row = renderedRows.find((entry) => entry.node.itemId === disclosure.itemId)?.row;
      row?.querySelectorAll(".assistant-process, .assistant-thinking, .tool-block").forEach((block, index) => {
        const prior = disclosure.blocks[index]; if (!prior) return;
        block.classList.toggle("open", prior.open); block.querySelector("[aria-expanded]")?.setAttribute("aria-expanded", String(prior.open));
        if (prior.touched) block.dataset.disclosureTouched = "1";
      });
    }
    setInspectorVisible(value.inspectorVisible !== false);
    if (value.inspectorWidth) find(".diagnostic-detail-grid").style.setProperty("--diagnostic-inspector-width", value.inspectorWidth);
    messages.scrollTop = Number(value.messagesScrollTop) || 0;
    role("inspectorContent").scrollTop = Number(value.inspectorScrollTop) || 0;
  }

  function load(next = {}) {
    if (disposed) return;
    const sameSession = state.sessionId && state.sessionId === next.session?.session_id;
    const historyChanged = !sameSession || data.history !== next.history;
    const nextEvents = Array.isArray(next.events) ? next.events : next.events?.items || [];
    const eventsChanged = !sameSession || data.events !== nextEvents;
    const previous = sameSession ? (loadingSnapshot || snapshot()) : null;
    loadingSnapshot = next.history == null ? previous : null;
    data = { session: next.session || {}, history: next.history || { items: [] }, events: nextEvents, users: next.users || next.session?.users || {} };
    state.sessionId = data.session.session_id; state.conversationId = data.session.conversation_id;
    if (!sameSession) { inspectorTab = "session"; selectedRunId = null; selectedReference = null; lastFocusKey = null; }
    role("chatTitle").textContent = data.session.title || data.session.name || "未命名会话";
    role("sessionBadge").textContent = formatSessionIdSubtitle(state.sessionId);
    find("[data-detail-refresh]").title = ["running", "pending", "waiting", "awaiting_input"].includes(data.session.latest_status) ? "仅展示已保存内容，点击刷新更新" : "刷新会话";
    showNotice();
    // Context/profile refreshes reuse the same history: keep existing message DOM
    // and disclosure state instead of parsing every long reply again.
    if (historyChanged) renderConversation();
    if (eventsChanged) trajectory.setHistory({ session_id: state.sessionId, items: data.events }, { preserveView: Boolean(previous) });
    trajectory.setExportReady(next.eventsComplete === true && !next.eventsError);
    if (previous) restore(previous); else { currentView = "conversation"; trajectory.selectView("conversation", { notify: false }); renderInspector(); }
    if (next.history == null && !next.historyError) messages.innerHTML = `<div class="diagnostic-detail-error" role="status"><p>正在读取已保存对话…</p></div>`;
    if (next.focus) {
      const target = next.focus;
      const key = JSON.stringify([target.view || "conversation", target.eventId || "", target.runId || ""]);
      const needsEvents = target.view === "trajectory" || (target.runId && !target.eventId);
      const ready = next.history != null && (!needsEvents || next.eventsComplete !== false);
      // Context estimates and refreshes must not move an already inspected view.
      if (key !== lastFocusKey) {
        focus(ready ? target : { view: target.view });
        if (ready) lastFocusKey = key;
      }
    }
    if (next.historyError) setError(next.historyError, { section: "history" });
    if (next.eventsError) setError(next.eventsError, { section: "events" });
    else if (next.eventsComplete === false) setLoading(`已读取 ${data.events.length} 条轨迹，继续加载中…`, { section: "events" });
    else { find("[data-detail-events-notice]").classList.add("hidden"); }
  }

  function setLoading(message = "加载会话…", { section = "page" } = {}) {
    if (disposed) return;
    if (message && typeof message === "object") {
      if (message.events) setLoading(message.events, { section: "events" });
      return;
    }
    if (section === "events") { const host = find("[data-detail-events-notice]"); host.textContent = String(message); host.classList.remove("hidden"); return; }
    pageLoading = message !== false;
    if (pageLoading && !state.sessionId) role("chatTitle").textContent = "加载会话…";
    root.toggleAttribute("aria-busy", pageLoading);
    if (pageLoading) root.setAttribute("aria-busy", "true");
    find("[data-detail-loading]").classList.toggle("hidden", !pageLoading);
    find("[data-detail-loading-label]").textContent = message === true ? "正在加载会话…" : String(message || "");
    for (const element of find(".diagnostic-detail-grid").children) {
      if (!element.hasAttribute("data-detail-loading")) element.inert = pageLoading;
    }
    for (const button of header.querySelectorAll("button:not([data-detail-back])")) {
      if (pageLoading && !disabledBeforeLoading.has(button)) disabledBeforeLoading.set(button, button.disabled);
      button.disabled = pageLoading || Boolean(disabledBeforeLoading.get(button));
    }
    if (!pageLoading) disabledBeforeLoading.clear();
  }

  function setError(error, { section = "page" } = {}) {
    if (disposed) return;
    if (error && typeof error === "object" && (error.history || error.events || error.context)) {
      if (error.history) setError(error.history, { section: "history" });
      if (error.events) setError(error.events, { section: "events" });
      if (error.context) { data.session.context_error = error.context; renderInspector(); }
      return;
    }
    if (section === "page") setLoading(false);
    const message = typeof error === "string" ? error : error?.message || "读取失败，请重试。";
    if (section === "history") {
      messages.innerHTML = `<div class="diagnostic-detail-error" role="alert">${icon("circle-alert")}<strong>对话读取失败</strong><p>${escapeHtml(message)}</p><span>可切换轨迹继续排查；不会把读取失败显示为空会话。</span></div>`;
    } else if (section === "events") { const host = find("[data-detail-events-notice]"); host.textContent = `轨迹未读完：${message}`; host.classList.remove("hidden"); }
    else showNotice(message);
  }

  // Allow the transition to paint before synchronous Markdown/trajectory work.
  // The timeout also releases background tabs where animation frames are paused.
  function prepareRender() {
    return new Promise(resolve => {
      if (disposed || !globalThis.requestAnimationFrame) { resolve(); return; }
      let frame;
      const done = () => { clearTimeout(timer); globalThis.cancelAnimationFrame?.(frame); resolve(); };
      const timer = setTimeout(done, 150);
      frame = globalThis.requestAnimationFrame(() => { frame = globalThis.requestAnimationFrame(done); });
    });
  }

  listen(root, "click", (event) => {
    if (pageLoading && !event.target.closest("[data-detail-back]")) {
      event.preventDefault(); event.stopImmediatePropagation();
    }
  }, true);
  listen(root, "click", async (event) => {
    const button = event.target.closest("button"); if (!button) return;
    if (button.hasAttribute("data-detail-back")) onBack();
    else if (button.hasAttribute("data-detail-refresh")) onRefresh();
    else if (button.hasAttribute("data-detail-inspector")) setInspectorVisible(button.getAttribute("aria-pressed") !== "true");
    else if (button.dataset.inspectorTab) { inspectorTab = button.dataset.inspectorTab; renderInspector(); }
    else if (button.hasAttribute("data-detail-copy-session") || button.dataset.inspectorCopy || (button.dataset.copyFileId && currentView !== "trajectory")) {
      event.stopPropagation();
      try { await copyTextToClipboard(button.dataset.inspectorCopy || button.dataset.copyFileId || state.sessionId); if (!disposed) toast("已复制"); }
      catch { if (!disposed) toast("复制失败，请手动选择复制", true); }
    } else if (button.hasAttribute("data-detail-image-close")) closeImage();
  });

  const imageDialog = find("[data-detail-image-dialog]");
  function closeImage() { imageDialog.close(); find("[data-detail-image]").removeAttribute("src"); }
  const disposeImagePreviews = bindAttachmentImagePreviews({ root, openImage(image) {
    if (!image?.previewUrl || disposed) return;
    find("[data-detail-image]").src = image.previewUrl; find("[data-detail-image-title]").textContent = image.name || "图片附件";
    find("[data-detail-image-open]").href = image.previewUrl; find("[data-detail-image-error]").classList.add("hidden"); imageDialog.showModal();
  } });
  listen(find("[data-detail-image]"), "error", () => find("[data-detail-image-error]").classList.remove("hidden"));
  listen(imageDialog, "close", () => find("[data-detail-image]").removeAttribute("src"));
  listen(imageDialog, "click", (event) => { if (event.target === imageDialog) closeImage(); });
  listen(root, "error", (event) => event.target.closest?.(".web-image-preview")?.classList.add("load-failed"), true);

  const grid = find(".diagnostic-detail-grid");
  const resizer = find(".diagnostic-detail-resizer");
  let drag = null;
  const setWidth = (value) => {
    const width = Math.max(260, Math.min(720, grid.clientWidth - 280, value));
    grid.style.setProperty("--diagnostic-inspector-width", `${width}px`); resizer.setAttribute("aria-valuenow", String(Math.round(width)));
  };
  listen(resizer, "pointerdown", (event) => { if (event.button !== 0) return; event.preventDefault(); drag = { id: event.pointerId, x: event.clientX, width: role("inspectorPane").getBoundingClientRect().width }; resizer.setPointerCapture(event.pointerId); });
  listen(resizer, "pointermove", (event) => { if (drag && drag.id === event.pointerId) setWidth(drag.width + drag.x - event.clientX); });
  const endDrag = () => { drag = null; };
  listen(resizer, "pointerup", endDrag); listen(resizer, "pointercancel", endDrag);
  listen(resizer, "keydown", (event) => { if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return; event.preventDefault(); setWidth(role("inspectorPane").getBoundingClientRect().width + (event.key === "ArrowLeft" ? 20 : -20)); });

  function destroy() {
    if (disposed) return; disposed = true;
    presenters.forEach((presenter) => presenter.dispose()); presenters = []; renderedRows = [];
    trajectory.destroy(); disposeImagePreviews(); listeners.splice(0).forEach((remove) => remove());
    if (imageDialog.open) imageDialog.close();
    find("[data-detail-image]").removeAttribute("src");
    data = { session: {}, history: { items: [] }, events: [], users: {} }; selectedReference = null;
    root.removeAttribute("aria-busy"); disabledBeforeLoading.clear();
    root.replaceChildren(); root.classList.remove("diagnostic-session-detail");
  }
  renderInspector(); refreshIcons();
  return { load, focus, snapshot, restore, setLoading, prepareRender, setError, destroy };
}
