import { sessionLoadingHtml, prepareSessionRender, setSessionLoading } from "./session-loading.js?v=20260918-release01012";
import { savedRunPresentation, sessionStatusLabels as statusLabels, renderContextReferenceContent as contextReferenceContent } from "./session-presentation.js?v=20260923-usage-tab-2";
import { createConversationPresenter } from "./conversation-presenter.js?v=20261002-console-audit";
import { projectConversationItems, sortConversationItems } from "./conversation-items.js?v=20261002-console-audit";
import { applyAssistantPresentationActions } from "./assistant-presentation.js?v=20261002-console-audit";
import { createTrajectoryController } from "./trajectory.js?v=20260924-ptc-execution-6";
import { centerInScrollContainer } from "./scroll.js?v=20260915-scroll-focus";
import { renderSessionInspector } from "./session-inspector.js?v=20260929-release0124";
import { channelOriginPresentation, formatSessionIdSubtitle } from "./session-display.js?v=20260929-release0124";
import { copyTextToClipboard } from "./message-actions.js?v=20260912-21";
import { parseServerTimestamp } from "./time.js?v=20260912-21";
import { bindAttachmentImagePreviews, attachmentDownloadUrl } from "./attachment-preview.js?v=20260922-trajectory-8";
import { createReadonlySessionShell } from "./session-view-shell.js?v=20260912-21";

const escapeHtml = (value) => String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const icon = (name) => `<i data-lucide="${name}" aria-hidden="true"></i>`;

/** Locate a saved Conversation item, never guess from visible text or truncated IDs. */
export function findConversationFocus(items = [], events = [], { eventId, runId } = {}) {
  const ordered = sortConversationItems(items);
  if (eventId) return ordered.find((item) => item.item_id === eventId || item.source_event_ids?.includes(eventId)) || null;
  if (!runId) return null;
  const ids = new Set(events.filter((event) => event.run_id === runId).map((event) => event.event_id));
  return ordered.find((item) => item.source_event_ids?.some((id) => ids.has(id))) || null;
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
    ${sessionLoadingHtml()}
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
  let copyFeedbackTimer = null;
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
    const { run, usage, model, runModel } = savedRunPresentation(session, data.events, selectedRunId);
    const contextStatus = { ...(session.context_status || {}) };
    if (session.context_error) contextStatus.unavailable_reason = typeof session.context_error === "string" ? session.context_error : session.context_error.message || "当前配置无法估算";
    const status = session.latest_status?.status || session.latest_status || "unknown";
    const userId = session.user_id;
    role("inspectorContent").innerHTML = renderSessionInspector({
      state: { sessionId: session.session_id, agentId: session.agent_id, conversationId: session.conversation_id, modelMessageCount: data.history.items?.filter((item) => ["user_message", "assistant_message"].includes(item.kind)).length },
      activeSession: { appId: session.app_id, spaceId: session.space_id, turnCount: session.turn_count, messageCount: session.message_count, attachmentCount: session.attachment_count },
      model, runModel,
      modelProvider: session.provider_name ? { name: session.provider_name } : null,
      run, usage, contextStatus, agentName: session.agent_name || session.agent_id || "未记录",
      sessionCache: session.cache_usage || {}, runCache: run.cache_usage || {},
      context: { app: session.app_id, space: session.space_id, language: session.language }, identity: { user: userId || "", profile: data.users[userId] || {} },
      channelOrigin: channelOriginPresentation(session), activeTab: inspectorTab,
      contextContent: contextReferenceContent(selectedReference?.reference),
      statusText: statusLabels[status] || "未确认", statusClass: status === "running" ? "running" : ["pending", "waiting", "awaiting_input"].includes(status) ? "waiting" : "idle",
    });
    refreshIcons();
  }

  const attachmentUrl = (attachment) => attachmentDownloadUrl(attachment, undefined, { diagnosticSessionId: state.sessionId });
  const conversation = createConversationPresenter({ messages, readOnly: true, attachmentUrl, userLabel: "用户", showConsoleToast: toast,
    onContextReference(reference, branchPoint) {
      const same = selectedReference?.branchPoint === branchPoint && selectedReference?.reference === reference;
      selectedReference = same ? null : { reference, branchPoint };
      inspectorTab = "context"; setInspectorVisible(true); renderInspector();
      messages.querySelectorAll("[data-context-ref-id]").forEach((button) => button.setAttribute("aria-pressed", String(Boolean(selectedReference) && button.dataset.contextRefId === [branchPoint ?? "live", reference.key || "", reference.type || ""].join(":"))));
    },
    isContextReferenceSelected: (reference, branchPoint) => selectedReference?.reference === reference && selectedReference?.branchPoint === branchPoint,
  });
  const trajectory = createTrajectoryController({ state, host: root, readOnly: true, attachmentUrl,
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
      marker.innerHTML = `${icon("crosshair")}<span>定位消息</span>`;
      if (row.classList.contains("user")) row.append(marker);
      else row.querySelector(".bubble-wrap").prepend(marker);
      refreshIcons();
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
    const targets = [...find(".diagnostic-detail-grid").children].filter(element => !element.hasAttribute("data-detail-loading"));
    setSessionLoading(root, targets, message);
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

  const prepareRender = prepareSessionRender;

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
      try {
        await copyTextToClipboard(button.dataset.inspectorCopy || button.dataset.copyFileId || state.sessionId);
        if (disposed) return;
        toast("已复制");
        if (button.hasAttribute("data-detail-copy-session")) {
          button.classList.add("copied");
          button.title = "会话 ID 已复制";
          button.setAttribute("aria-label", "会话 ID 已复制");
          button.innerHTML = icon("check");
          refreshIcons();
          clearTimeout(copyFeedbackTimer);
          copyFeedbackTimer = setTimeout(() => {
            if (disposed || !button.isConnected) return;
            button.classList.remove("copied");
            button.title = "复制会话 ID";
            button.setAttribute("aria-label", "复制会话 ID");
            button.innerHTML = icon("copy");
            refreshIcons();
          }, 1500);
        }
      }
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
    clearTimeout(copyFeedbackTimer);
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
