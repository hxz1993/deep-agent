import { attachmentDownloadUrl } from "../attachment-preview.js?v=20261103-view-image1";
import { bindComposerAttachments, bindComposerInput, resizeComposerInput } from "../composer-input.js?v=20261117";

(() => {
  const endpoint = "/api/v1/remote-agent-connections";
  const el = (id) => document.getElementById(id);
  const attr = (value) => escapeHtml(value).replaceAll('"', "&quot;").replaceAll("'", "&#39;");
  const icon = (name) => `<i data-lucide="${name}" aria-hidden="true"></i>`;
  const icons = () => window.lucide?.createIcons();
  const drivers = {
    a2a: { label: "A2A", logo: "a2a.svg", transport: "A2A · HTTP", help: "读取 Agent Card 获取远端接口，通过 A2A 发送消息并查询任务状态。" },
    codex: { label: "Codex", badge: "Codex Exec", logo: "codex.png", transport: "单次命令 · JSONL", command: "codex exec --json", help: "以非交互模式执行任务，运行事件逐行返回，可跟踪进度和结果。" },
    codebuddy: { label: "CodeBuddy", badge: "CodeBuddy -p", logo: "codebuddy.svg", transport: "单次命令 · JSONL", command: "codebuddy -p --output-format stream-json", help: "以非交互模式执行任务，逐行返回运行事件和结果。" },
  };
  const labels = {
    submitted: "排队中", working: "进行中", input_required: "待补答", completed: "已完成",
    failed: "失败", canceled: "已取消", unknown: "结果未知",
  };
  const activeStates = new Set(["submitted", "working"]);
  const checkingConnections = new Set();
  const recentPageSize = 20;
  const fieldOptions = {
    execution: { local: "本机", ssh: "SSH" },
    workspace_root: { default: "默认", custom: "指定" },
    model: { default: "默认", custom: "指定" },
    sandbox: { "read-only": "只读", "workspace-write": "可写" },
  };
  let connections = [];
  let recent = [];
  let recentPage = 1;
  let recentPending = true;
  let recentError = "";
  let connectionPending = true;
  let connectionError = "";
  let selectedRecent;
  let view = "cards";
  let editor;
  let homeRequest;
  let recentRequest;
  let recentSession;
  let openRequest = 0;
  let pollTimer;
  let debugPollTimer;
  let defaultWorkdirsRequest;

  const path = (id) => `${endpoint}/${encodeURIComponent(id)}`;
  const post = (url, body, method = "POST") => apiFetch(url, { method, body: JSON.stringify(body) });
  const protocolOf = (row) => row?.config?.protocol || row?.protocol;
  const driverLogoUrl = (protocol) => {
    const logo = drivers[protocol]?.logo;
    return logo ? new URL(`../assets/remote-agents/${logo}`, import.meta.url).href : "";
  };
  const driverLogo = (protocol) => {
    const source = driverLogoUrl(protocol);
    return source ? `<img class="remote-agent-driver-logo" src="${attr(source)}" alt="" width="16" height="16">` : icon("bot");
  };

  function driverBadge(protocol, showLogo = true) {
    const driver = drivers[protocol];
    return driver ? `<span class="remote-agent-driver">${showLogo ? driverLogo(protocol) : ""}${driver.badge || driver.label}</span>` : "—";
  }

  function listDriver(protocol) {
    if (!drivers[protocol]) return "—";
    return `<span class="remote-agent-list-driver">${driverLogo(protocol)}${driverBadge(protocol, false)}</span>`;
  }

  function status(value, label = labels[value], dotOnly = false) {
    const name = label || "未知";
    const state = attr(value);
    if (dotOnly) {
      return `<span class="remote-agent-state" data-state="${state}" role="img" aria-label="${attr(name)}" title="${attr(name)}"></span>`;
    }
    return `<span class="remote-agent-state" data-state="${state}">${escapeHtml(name)}</span>`;
  }

  function inspectionState(inspection, dotOnly = false) {
    if (!inspection) return status("unchecked", "未检查", dotOnly);
    if (["ok", "healthy", "ready", "success"].includes(inspection.status) || inspection.ok === true) {
      return status("completed", "正常", dotOnly);
    }
    if (inspection.status === "approval_required" || inspection.proposed_interface) return status("input_required", "待确认", dotOnly);
    return status("failed", "异常", dotOnly);
  }

  function updater(row) {
    const name = row.updated_by_username || row.updated_by || "未记录";
    return `<span class="agent-updater" title="更新人：${attr(name)}">${icon("user-round")}<span class="agent-updater-name">${escapeHtml(name)}</span></span>`;
  }

  function updatedTime(row) {
    const time = escapeHtml(formatDate(row.updated_at));
    return row.updated_at ? `<time class="agent-update-time" datetime="${attr(row.updated_at)}">${time}</time>` : time;
  }

  function renderConnections() {
    const q = el("remoteConnectionSearch").value.trim().toLocaleLowerCase();
    const shown = connectionPending || connectionError ? [] : connections.filter(row =>
      `${row.name} ${row.description || ""} ${drivers[protocolOf(row)]?.label || ""}`.toLocaleLowerCase().includes(q));
    const cards = el("remoteConnectionCardsHost");
    const list = el("remoteConnectionListHost");
    cards.hidden = view !== "cards" || !shown.length;
    list.hidden = view !== "list" || !shown.length;
    const remove = (row) => `<button type="button" class="row-action icon-only danger" data-remote-delete="${attr(row.id)}" title="删除" aria-label="删除 ${attr(row.name)}">${icon("trash-2")}</button>`;
    const open = (row) => `<button type="button" class="remote-agent-open" data-remote-open="${attr(row.id)}" title="${attr(row.name)}">${escapeHtml(row.name)}</button>`;
    cards.innerHTML = shown.map(row => {
      const checking = checkingConnections.has(row.id);
      const checkLabel = checking ? "正在检查" : protocolOf(row) === "a2a" ? "检查连接" : "检查环境";
      return `
      <article class="remote-agent-card" data-remote-card="${attr(row.id)}">
        <header>
          <span class="remote-agent-card-icon" aria-hidden="true">${driverLogo(protocolOf(row))}</span>
          <div class="remote-agent-card-identity">${open(row)}${driverBadge(protocolOf(row), false)}</div>
          ${checking ? status("working", "检查中") : inspectionState(row.inspection)}
        </header>
        <p>${escapeHtml(row.description || "—")}</p>
        <footer class="agent-card-footer">
          <span class="agent-card-update">${updater(row)}${updatedTime(row)}</span>
          <div class="row-actions agent-row-actions">
            <button type="button" class="row-action icon-only" data-remote-check="${attr(row.id)}" title="${checkLabel}" aria-label="${checkLabel} ${attr(row.name)}"${checking ? " disabled" : ""}>${icon(checking ? "loader-circle" : "activity")}</button>
            ${remove(row)}
          </div>
        </footer>
      </article>`;
    }).join("");
    el("remoteConnectionRows").innerHTML = shown.map(row => `
      <tr data-remote-connection-row="${attr(row.id)}"><td>${open(row)}</td><td>${listDriver(protocolOf(row))}</td>
        <td title="${attr(row.description || "")}">${escapeHtml(row.description || "—")}</td>
        <td>${inspectionState(row.inspection)}</td><td>${updater(row)}</td><td>${updatedTime(row)}</td>
        <td><div class="row-actions agent-row-actions">${remove(row)}</div></td></tr>`).join("");
    const message = connectionError || (connectionPending ? "正在加载远程 Agent…"
      : q ? "没有匹配的远程 Agent" : "暂无远程 Agent");
    const notice = el("remoteConnectionNotice");
    const empty = !connectionPending && !connectionError && !connections.length;
    notice.classList.toggle("agent-list-state", empty);
    replaceMarkup(notice, empty ? `${icon("bot")}<strong>暂无远程 Agent</strong><span>点击「添加远程 Agent」配置一个连接。</span>` : shown.length ? "" :
      `${icon(connectionError ? "triangle-alert" : connectionPending ? "loader-circle" : q ? "search" : "bot")}
      <span>${escapeHtml(message)}</span>${connectionError ? '<button class="btn text compact" id="retryRemoteDirectory" type="button">重试</button>' : ""}`);
    icons();
  }

  function recentIsVisible() {
    return !editor && panelIsVisible();
  }

  function panelIsVisible() {
    return el("panel-remote-agents")?.classList.contains("active") && !document.hidden
      && (!document.body.dataset.consolePage || document.body.dataset.consolePage === "configuration");
  }

  function renderRecentFilter() {
    const select = el("remoteCallConnection");
    const previous = select.value;
    const agents = new Map(connections.map(row => [row.id, { name: row.name, protocol: protocolOf(row) }]));
    for (const row of recent) if (!agents.has(row.connection_id)) {
      agents.set(row.connection_id, { name: row.connection_name || row.name || row.connection_id, protocol: row.protocol });
    }
    const options = [new Option("全部远程 Agent", ""), ...Array.from(agents, ([id, { name, protocol }]) => {
      const option = new Option(name, id);
      const source = driverLogoUrl(protocol);
      if (source) option.dataset.iconSrc = source;
      return option;
    })];
    if (select.options.length === options.length && options.every((option, index) => {
      const current = select.options[index];
      return current.value === option.value && current.text === option.text && current.dataset.iconSrc === option.dataset.iconSrc;
    })) return;
    select.replaceChildren(...options);
    select.value = agents.has(previous) ? previous : "";
  }

  function recentConnectionName(row, connection = connections.find(item => item.id === row.connection_id)) {
    return connection?.name || row.connection_name || row.name;
  }

  function renderRecent() {
    renderRecentFilter();
    const keyword = el("remoteCallSearch").value.trim().toLocaleLowerCase();
    const connectionId = el("remoteCallConnection").value;
    const filterStatus = el("remoteCallStatus").value;
    const shown = recent.filter(row => (!connectionId || connectionId === row.connection_id)
      && (!filterStatus || filterStatus === row.status)
      && (!keyword || `${row.title || row.task || ""} ${row.id} ${row.remote_task_id || ""} ${formatExternalId(row.remote_task_id || "")}`.toLocaleLowerCase().includes(keyword)));
    const pageCount = Math.max(1, Math.ceil(shown.length / recentPageSize));
    recentPage = Math.min(recentPage, pageCount);
    const rows = shown.slice((recentPage - 1) * recentPageSize, recentPage * recentPageSize);
    const selected = rows.find(row => row.id === selectedRecent);
    selectedRecent = selected?.id;
    const emptyLabel = recentError || (recentPending ? "正在加载近期调用…"
      : keyword || connectionId || filterStatus ? "没有符合条件的调用" : "暂无调用记录");
    const markup = rows.length ? rows.map(row => {
      const name = recentConnectionName(row);
      const title = row.title || row.task || row.label || "远程 Agent 调用";
      return `<tr data-remote-call-row="${attr(row.id)}" class="${row.id === selectedRecent ? "selected" : ""}">
        <td><button type="button" class="btn text agent-service-select" data-remote-call="${attr(row.id)}" aria-controls="remoteCallDetail" aria-pressed="${row.id === selectedRecent}"><span title="${attr(title)}">${escapeHtml(title)}</span><code title="${attr(row.id)}">${escapeHtml(String(row.id).slice(0, 8))}</code></button></td>
        <td><div class="remote-agent-call-name"><span title="${attr(name || "—")}">${escapeHtml(name || "—")}</span>${driverBadge(row.protocol)}</div></td>
        <td>${status(row.status)}</td><td class="agent-task-wide"><time datetime="${attr(row.created_at)}">${escapeHtml(formatDate(row.created_at))}</time></td>
        <td>${row.files?.length ? `<span class="agent-task-file-count" aria-label="${row.files.length} 个文件">${icon("file-text")}${row.files.length}</span>` : "—"}</td></tr>`;
    }).join("") : `<tr><td colspan="5" class="empty-cell agent-task-wide">${escapeHtml(emptyLabel)}</td>
      <td colspan="4" class="empty-cell agent-task-compact">${escapeHtml(emptyLabel)}</td></tr>`;
    const body = el("remoteCallRows");
    const focused = body.contains(document.activeElement) && document.activeElement.dataset.remoteCall;
    replaceMarkup(body, markup);
    if (focused && !body.contains(document.activeElement)) {
      const button = Array.from(body.querySelectorAll("[data-remote-call]")).find(node => node.dataset.remoteCall === focused);
      button?.focus({ preventScroll: true });
    }
    el("prevRemoteCalls").disabled = recentPage === 1;
    el("nextRemoteCalls").disabled = recentPage >= pageCount;
    const pageLabel = recentError ? "加载失败" : recentPending ? "正在加载…"
      : `第 ${recentPage} / ${pageCount} 页 · 共 ${shown.length.toLocaleString()} 条调用`;
    if (el("remoteCallCount").textContent !== pageLabel) el("remoteCallCount").textContent = pageLabel;
    void loadRecentSession(selected?.session_id);
    renderRecentDetail(selected, rows.length);
    icons();
  }

  async function loadRecentSession(sessionId) {
    if (recentSession?.id === sessionId) return;
    recentSession?.request.abort();
    if (!sessionId) { recentSession = undefined; return; }
    const request = new AbortController();
    const selected = recentSession = { id: sessionId, request, data: null };
    try {
      const session = await apiFetch(`/api/v1/sessions/${encodeURIComponent(sessionId)}`, {
        signal: request.signal, includeContext: false, reportStatus: false,
      });
      if (request.signal.aborted) return;
      selected.data = session;
      if (recentIsVisible()) renderRecent();
    } catch {
      // Session information is supplemental to the invocation and may be unavailable.
    }
  }

  function setRecentPage(page) {
    recentPage = page;
    renderRecent();
    el("remoteCallTable").scrollTop = 0;
  }

  function replaceMarkup(host, markup) {
    if (host.remoteAgentMarkup === markup) return;
    host.remoteAgentMarkup = markup;
    host.innerHTML = markup;
  }

  function fact(label, value) {
    if (value == null || value === "") return "";
    return `<div><dt>${label}</dt><dd>${escapeHtml(value)}</dd></div>`;
  }

  function formatExternalId(id) {
    return id.replace(/^([0-9a-f]{8})([0-9a-f]{4})([0-9a-f]{4})([0-9a-f]{4})([0-9a-f]{12})$/i, "$1-$2-$3-$4-$5");
  }

  function callId(id, label, { url = "", external = false } = {}) {
    if (!id) return "—";
    const display = external ? formatExternalId(id) : id.slice(0, 8);
    const value = `<code title="${attr(id)}">${escapeHtml(display)}</code>`;
    return `<span class="agent-service-id-value">${url
      ? `<a class="agent-service-session-link" href="${attr(url)}" aria-label="查看 ${label} ${attr(display)}">${value}</a>` : value}
      <button type="button" class="console-inline-id-copy" data-remote-copy="${attr(id)}" data-copy-label="${label}" aria-label="复制 ${label}" title="复制 ${label}">${icon("copy")}</button></span>`;
  }

  function idFact(label, id, options) {
    return id ? `<div><dt>${label}</dt><dd>${callId(id, label, options)}</dd></div>` : "";
  }

  function renderScope(session) {
    const app = session?.app_id || "";
    const space = session?.space_id || "";
    if (!app && !space) return "";
    const appName = app ? session.app_name || "名称未获取" : "";
    const spaceName = space ? session.space_name || "名称未获取" : "";
    return `<span class="inspector-scope-value">
      <span class="inspector-scope-names"><span title="${attr(appName)}">${escapeHtml(appName)}</span><span>/</span><span title="${attr(spaceName)}">${escapeHtml(spaceName)}</span></span>
      <span class="inspector-scope-codes"><code>${escapeHtml(app)}</code><span>/</span><code>${escapeHtml(space)}</code></span>
    </span>`;
  }

  function renderFiles(files) {
    if (!Array.isArray(files) || !files.length) return "";
    return `<div class="remote-agent-output-files">${files.map(file => {
      const id = file.file_id || file.id;
      if (!id) return "";
      const name = file.file_name || file.name || id;
      const url = attachmentDownloadUrl({ id, name, mime: file.mime_type });
      return `<div class="remote-agent-output-file"><span>${icon("file-output")}${escapeHtml(name)}</span>${url
        ? `<a class="btn text compact" href="${attr(url)}" download title="下载文件" aria-label="下载 ${attr(name)}">${icon("download")}</a>` : ""}</div>`;
    }).join("")}</div>`;
  }

  function renderRecentDetail(row, visibleCount) {
    const detail = el("remoteCallDetail");
    if (!row) {
      const message = recentError ? "暂时无法查看调用详情" : recentPending ? "正在加载…"
        : visibleCount ? "请选择一条调用查看详情" : "暂无调用可查看";
      delete detail.dataset.invocation;
      replaceMarkup(detail, `<div class="agent-service-detail-heading agent-service-detail-placeholder"><h3>调用详情</h3></div>
        <div class="inspector-tab-empty" role="status"><span>${message}</span></div>`);
      return;
    }
    const title = row.title || row.task || row.label || "调用详情";
    const connection = connections.find(item => item.id === row.connection_id);
    const localAgent = connection?.agents?.find(agent => agent.id === row.agent_id);
    const question = row.question || (row.status === "input_required" ? row.text : "");
    const result = row.result || (row.status === "input_required" ? "" : row.text);
    const sessionUrl = row.session_id && window.DeepAgentConsoleAPI?.consoleUrl(
      `#/diagnostics/sessions/${encodeURIComponent(row.session_id)}?view=conversation`,
    );
    const turnUrl = sessionUrl && row.turn_id ? `${sessionUrl}&turn_id=${encodeURIComponent(row.turn_id)}` : "";
    const session = recentSession?.data;
    const events = (Array.isArray(row.timeline) ? row.timeline : []).map(item => `<li>${status(item.status, item.label)}<time>${escapeHtml(formatDate(item.at || item.created_at))}</time></li>`).join("");
    const external = { external: true };
    const remoteSession = [
      idFact("任务 ID", row.remote_task_id, external),
      idFact(row.protocol === "codex" ? "Thread ID" : row.protocol === "codebuddy" ? "Session ID" : "Context ID", row.remote_context_id || row.remote_session_id || row.thread_id, external),
      idFact("提问 ID", row.question_id, external),
    ].join("");
    const previousScroll = detail.querySelector(".inspector-tab-panel")?.scrollTop || 0;
    const sameCall = detail.dataset.invocation === row.id;
    replaceMarkup(detail, `
      <div class="agent-service-detail-heading">
        <div class="agent-service-detail-title">
          <h3 title="${attr(title)}">${escapeHtml(title)}</h3>
          <div class="session-subtitle-row">${callId(row.id, "调用 ID")}</div>
        </div>
        <span class="inspector-tab-status">${status(row.status)}</span>
      </div>
      <div class="inspector-tab-panel">
      <dl class="remote-agent-facts">
        <div><dt>接入方式</dt><dd>${driverBadge(row.protocol)}</dd></div>
        ${fact("远程 Agent", recentConnectionName(row, connection))}
        ${fact("本地 Agent", localAgent?.name || row.agent_id)}
        ${fact("提交时间", formatDate(row.created_at))}
        ${idFact("调用 ID", row.id)}
      </dl>
      ${row.session_id || row.turn_id ? `<section class="remote-agent-detail-section"><h4>DeepAgent 会话</h4><dl class="remote-agent-facts">
        ${idFact("提问 ID", row.turn_id, { url: turnUrl })}
        ${idFact("会话 ID", row.session_id, { url: sessionUrl })}
        <div><dt>会话名称</dt><dd>${escapeHtml(session?.title || "")}</dd></div>
        <div><dt>应用 / 空间</dt><dd>${renderScope(session)}</dd></div>
      </dl></section>` : ""}
      ${remoteSession ? `<section class="remote-agent-detail-section"><h4>远程会话</h4><dl class="remote-agent-facts">${remoteSession}</dl></section>` : ""}
      ${events ? `<section class="remote-agent-detail-section"><h4>任务进度</h4><ol>${events}</ol></section>` : ""}
      ${question ? `<section class="remote-agent-detail-section"><h4>待补材料</h4><p>${escapeHtml(question)}</p>${sessionUrl ? '<p class="muted">请在原 Agent 会话补答。</p>' : ""}</section>` : ""}
      ${result ? `<section class="remote-agent-detail-section remote-agent-result"><h4>任务结果</h4><p>${escapeHtml(result)}</p></section>` : ""}
      ${(row.warnings || []).map(warning => `<p class="muted">${escapeHtml(warning)}</p>`).join("")}
      ${renderFiles(row.files)}</div>`);
    detail.dataset.invocation = row.id;
    if (sameCall) detail.querySelector(".inspector-tab-panel").scrollTop = previousScroll;
  }

  function schedulePoll() {
    clearTimeout(pollTimer);
    if (recentIsVisible()) pollTimer = setTimeout(() => void updateRecent(), 4000);
  }

  async function updateRecent() {
    if (!recentIsVisible()) return;
    recentRequest?.abort();
    const request = new AbortController();
    recentRequest = request;
    try {
      const rows = await apiFetch(`${endpoint}/recent`, { signal: request.signal, reportStatus: false });
      if (request.signal.aborted || !recentIsVisible()) return;
      recent = rows.filter(row => Object.hasOwn(drivers, row.protocol));
      recentPending = false;
      recentError = "";
      renderRecent();
    } catch (error) {
      if (!request.signal.aborted && recentIsVisible()) {
        recent = [];
        recentPending = false;
        recentError = error.status === 404 ? "当前服务未提供近期调用接口" : "近期调用加载失败，请稍后重试";
        renderRecent();
      }
    } finally {
      if (recentRequest === request) schedulePoll();
    }
  }

  async function refresh() {
    if (editor || !el("remoteAgentDirectory")) return;
    homeRequest?.abort();
    const request = new AbortController();
    homeRequest = request;
    connectionPending = true;
    connectionError = "";
    renderConnections();
    renderRecent();
    void updateRecent();
    try {
      const rows = await apiFetch(endpoint, { signal: request.signal });
      if (request.signal.aborted) return;
      connections = rows.filter(row => Object.hasOwn(drivers, protocolOf(row)));
      connectionPending = false;
      renderConnections();
      renderRecent();
    } catch (error) {
      if (!request.signal.aborted) {
        connectionPending = false;
        connectionError = error.status === 404 ? "当前服务未提供远程 Agent 接口" : "加载远程 Agent 失败";
        renderConnections();
      }
    }
  }

  function baseConfig(protocol) {
    const config = { version: 1, protocol };
    if (protocol === "a2a") return { ...config, card_url: "", headers: {} };
    if (protocol === "codex") return { ...config, execution: "local", sandbox: "read-only" };
    if (protocol === "codebuddy") return { ...config, execution: "local" };
    return config;
  }

  function nameInput() { return el("remoteConnectionForm").elements.namedItem("name"); }
  function descInput() { return el("remoteConnectionForm").elements.namedItem("description"); }

  function openEditor(row = {}) {
    homeRequest?.abort();
    recentRequest?.abort();
    clearTimeout(pollTimer);
    clearTimeout(debugPollTimer);
    const config = { ...baseConfig(protocolOf(row) || "a2a"), ...row.config };
    editor = {
      row, config, formModes: { workspace_root: config.workspace_root ? "custom" : "default", model: config.model ? "custom" : "default" },
      dirty: false, json: false, advanced: false, busy: false, debugBusy: false,
      headers: Object.entries(config.headers || {}), debug: { files: [], messages: [], invocation: null },
    };
    el("remoteAgentDirectory").hidden = true;
    el("remoteAgentEditor").hidden = false;
    nameInput().value = row.name || "";
    descInput().value = row.description || "";
    el("remoteEditorTitle").textContent = row.id ? `编辑远程 Agent · ${row.name}` : "添加远程 Agent";
    el("remoteConfigJson").value = "";
    el("remoteDebugPrompt").value = "";
    el("remoteDebugFileInput").value = "";
    el("remoteConfigStatus").textContent = "";
    el("remoteDebugNotice").textContent = "";
    renderEditor();
    nameInput().focus();
  }

  async function loadEditor(id) {
    const generation = ++openRequest;
    try {
      const row = await apiFetch(path(id));
      if (generation === openRequest && recentIsVisible()) openEditor(row);
    } catch (error) {
      showToast(error.message, true);
    }
  }

  async function requestCloseEditor() {
    if (!editor) return true;
    if (editor.busy) return false;
    if (editor.dirty && !(await confirmAction({
      title: "放弃未保存的修改？", message: "当前远程 Agent 的修改尚未保存。",
      icon: "file-warning", confirmLabel: "放弃修改",
    }))) return false;
    clearTimeout(debugPollTimer);
    editor = null;
    el("remoteAgentEditor").hidden = true;
    el("remoteAgentDirectory").hidden = false;
    nameInput().value = "";
    descInput().value = "";
    el("remoteConfigJson").value = "";
    el("remoteDriverFields").replaceChildren();
    el("remoteDebugPrompt").value = "";
    replaceMarkup(el("remoteDebugThread"), "");
    el("remoteDebugFiles").replaceChildren();
    return true;
  }

  function field(label, key, { value, hint, placeholder, type = "text", required = false } = {}) {
    const identifier = `remote-field-${key}`;
    return `<label class="field"><span class="field-label-row">${label}${hint ? tooltip(identifier, hint) : ""}</span>
      <input type="${type}" id="${identifier}" data-config-field="${key}" value="${attr(value ?? editor.config[key])}" ${placeholder ? `placeholder="${attr(placeholder)}"` : ""} autocomplete="off" ${required ? "required" : ""}></label>`;
  }

  function tooltip(id, content) {
    return `<span class="field-help" tabindex="0" aria-label="查看字段说明" aria-describedby="${id}-tip">${icon("circle-help")}<span class="field-help-tooltip" id="${id}-tip" role="tooltip">${escapeHtml(content)}</span></span>`;
  }

  function segmented(label, key, current, content = "", hint = "") {
    return `<div class="remote-agent-option"><div class="remote-agent-option-label"><span class="field-label-row">${label}${hint ? tooltip(`remote-option-${key}`, hint) : ""}</span>
      <div class="console-segmented" role="group" aria-label="${label}">${Object.entries(fieldOptions[key]).map(([value, label]) => `<button type="button" class="${value === current ? "active" : ""}" data-config-option="${key}" data-config-value="${value}" aria-pressed="${value === current}">${label}</button>`).join("")}</div>
      </div>${content ? `<div class="remote-agent-option-field">${content}</div>` : ""}</div>`;
  }

  function optionField(key, label, placeholder, required = false) {
    return `<input class="console-input" aria-label="${label}" data-config-field="${key}" value="${attr(editor.config[key])}" placeholder="${attr(placeholder)}" autocomplete="off" ${required ? "required" : ""}>`;
  }

  function driverFields() {
    const config = editor.config;
    if (config.protocol === "a2a") {
      return `${field("Agent Card 地址", "card_url", { type: "url", required: true, placeholder: "https://example.com/.well-known/agent-card.json" })}
        <div class="remote-agent-header-fields"><h3>请求头</h3>
          ${editor.headers.map(([key, value], index) => `
            <div class="remote-agent-header-row">
              <input class="console-input" data-header-key="${index}" value="${attr(key)}" aria-label="Header 名称" placeholder="Header 名称" autocomplete="off">
              <input class="console-input" type="text" data-header-value="${index}" value="${attr(value)}" aria-label="Header 值" placeholder="${value === null ? "留空保留原值" : "Header 值"}" autocomplete="off" autocapitalize="off" spellcheck="false">
              <div class="row-actions agent-row-actions"><button type="button" class="row-action icon-only" data-header-remove="${index}" aria-label="删除 Header" title="删除 Header">${icon("trash-2")}</button></div>
            </div>`).join("")}
          <button type="button" class="btn" id="addRemoteHeader">${icon("plus")}添加请求头</button></div>`;
    }
    const codex = config.protocol === "codex";
    const modelExample = codex ? "gpt-5.4" : "gpt-5";
    return `
      ${segmented("执行位置", "execution", config.execution, config.execution === "ssh"
        ? optionField("ssh_host", "SSH 主机别名", "SSH 主机名，如 build-runner", true)
        : '<span class="remote-agent-option-hint">在本机运行 CLI。</span>')}
      ${segmented("工作根目录", "workspace_root", editor.formModes.workspace_root, editor.formModes.workspace_root === "custom"
        ? optionField("workspace_root", "工作根目录", "输入执行机器上的绝对路径", true)
        : '<span class="remote-agent-option-hint remote-agent-root-hint"><code id="remoteDefaultWorkdir">~/.deep-agent/remote-agent-workspaces</code></span>',
        ["每次独立派单创建 <工作根目录>/<任务 ID>/work，补答和续跑沿用。",
          "本轮选了附件才创建 work/inputs/<轮次 ID>/。",
          "work/outputs/<轮次 ID>/ 存放本轮生成的产物，完成后回收到 FileStore。",
          "使用 SSH 时，路径在远端执行机器上解析。"].join("\n"))}
      ${segmented("模型", "model", editor.formModes.model, editor.formModes.model === "custom"
        ? optionField("model", "模型", `例如 ${modelExample}`, true)
        : `<span class="remote-agent-option-hint">沿用 ${drivers[config.protocol].label} 的默认模型。</span>`, `指定时填写执行账号可用的模型名，例如 ${modelExample}。`)}
      ${codex ? segmented("工作区权限", "sandbox", config.sandbox) : ""}
      <details class="remote-agent-advanced" ${editor.advanced ? "open" : ""}>
        <summary>高级${icon("chevron-right")}</summary>
        <div class="remote-agent-advanced-fields">
          ${field("启动命令", "command", { placeholder: `默认 ${config.protocol}，支持绝对路径`, hint: "只填命令名或执行环境中的可执行文件绝对路径。" })}
          ${codex ? field("配置档（Profile）", "profile", { placeholder: "沿用默认配置，如 review", hint: "填写执行账号在 Codex 中已定义的配置档名称。配置档可为不同工作场景预设模型等选项；留空使用 CLI 默认配置。" })
            + field("推理强度", "reasoning_effort", { placeholder: "沿用默认档位，如 medium", hint: "常见档位为 minimal、low、medium、high、xhigh。可用值取决于模型。" }) : ""}
        </div>
      </details>`;
  }

  function showDefaultWorkdir(element, execution) {
    defaultWorkdirsRequest ??= apiFetch(`${endpoint}/workspace-defaults`).catch(() => {
      defaultWorkdirsRequest = null;
      return {};
    });
    void defaultWorkdirsRequest.then((paths) => {
      if (element.isConnected && paths[execution]) element.textContent = paths[execution];
    });
  }

  function renderConfig() {
    const cfg = editor.config;
    const cli = cfg.protocol === "codex" || cfg.protocol === "codebuddy";
    el("remoteEditorDriver").innerHTML = driverBadge(cfg.protocol);
    el("remoteDriverOptions").innerHTML = Object.entries(drivers).map(([key, driver]) =>
      `<button type="button" class="btn remote-agent-driver-option" data-remote-driver="${key}" aria-pressed="${key === cfg.protocol}">${driverLogo(key)}<strong>${driver.label}</strong></button>`).join("");
    const driver = drivers[cfg.protocol];
    el("remoteDriverNote").innerHTML = `${driver.command ? `运行 <code>${driver.command}</code>，` : ""}${driver.help}`;
    el("remoteDebugHeading").textContent = cli ? "任务调试" : "对话调试";
    el("resetRemoteDebug").textContent = cli ? "清空调试" : "新对话";
    el("remoteDebugPrompt").placeholder = "给 Agent 发送消息…（Enter 发送，Shift+Enter 换行）";
    const fields = el("remoteDriverFields");
    fields.disabled = editor.json;
    fields.setAttribute("aria-hidden", String(editor.json));
    fields.innerHTML = driverFields();
    const workdir = el("remoteDefaultWorkdir");
    if (workdir && !editor.json) showDefaultWorkdir(workdir, cfg.execution);
    fields.querySelector("details")?.addEventListener("toggle", (event) => {
      if (editor) editor.advanced = event.target.open;
    });
    el("remoteConfigJson").hidden = !editor.json;
    el("toggleRemoteConfigMode").querySelector("span").textContent = editor.json ? "切换为表单" : "切换为 JSON";
    icons();
  }

  function configFromForm() {
    const config = { ...editor.config };
    if (config.protocol === "a2a") {
      delete config.approved_interface;
      const headers = editor.headers.filter(([key, value]) => key.trim() || value);
      if (headers.some(([key]) => !key.trim())) throw new Error("请输入 Header 名称");
      const names = headers.map(([key]) => key.trim().toLowerCase());
      if (new Set(names).size !== names.length) throw new Error("Header 名称不能重复");
      config.headers = Object.fromEntries(headers.map(([key, value]) => [key.trim(), value]));
    } else {
      if (editor.formModes.workspace_root !== "custom") delete config.workspace_root;
      if (editor.formModes.model !== "custom") delete config.model;
      if (config.execution !== "ssh") delete config.ssh_host;
      for (const key of ["command", "profile", "reasoning_effort"]) {
        if (!config[key]?.trim()) delete config[key];
      }
    }
    return config;
  }

  function configFromJson() {
    let config;
    try { config = JSON.parse(el("remoteConfigJson").value); } catch { throw new Error("接入配置必须是有效的 JSON"); }
    if (!config || Array.isArray(config) || typeof config !== "object"
      || config.version !== 1 || config.protocol !== editor.config.protocol) {
      throw new Error("请保留当前接入方式和格式版本");
    }
    if (config.protocol === "a2a") {
      delete config.approved_interface;
      if (config.headers && !Array.isArray(config.headers) && typeof config.headers === "object") {
        const replacements = new Map(editor.headers.map(([key, value]) => [key.trim().toLowerCase(), value]));
        config.headers = Object.fromEntries(Object.entries(config.headers).map(([key, value]) => [
          key, value === null && replacements.has(key.toLowerCase()) ? replacements.get(key.toLowerCase()) : value,
        ]));
      }
    }
    return config;
  }

  function toggleConfigMode() {
    try {
      if (editor.json) {
        const config = configFromJson();
        if (config.headers && (Array.isArray(config.headers) || typeof config.headers !== "object")) throw new Error("请求 Header 必须是 JSON 对象");
        editor.config = config;
        editor.formModes = { workspace_root: config.workspace_root ? "custom" : "default", model: config.model ? "custom" : "default" };
        editor.headers = Object.entries(config.headers || {});
      } else {
        const config = configFromForm();
        if (config.headers) config.headers = Object.fromEntries(Object.keys(config.headers).map(key => [key, null]));
        el("remoteConfigJson").value = JSON.stringify(config, null, 2);
      }
      editor.json = !editor.json;
      if (!editor.json) el("remoteConfigJson").value = "";
      el("remoteConfigStatus").textContent = "";
      renderConfig();
    } catch (error) {
      el("remoteConfigStatus").textContent = error.message;
    }
  }

  function renderSummary() {
    const row = editor.row;
    const savedProtocol = row.config?.protocol === editor.config.protocol;
    const cli = ["codex", "codebuddy"].includes(editor.config.protocol);
    const heading = `<h3>${cli ? "执行环境" : "连接概览"}</h3>`;
    const summary = el("remoteConnectionSummary");
    if (!savedProtocol) {
      summary.innerHTML = `${heading}<p class="muted">保存后显示概要。</p>`;
      return;
    }
    const config = row.config;
    const inspection = row.inspection;
    const proposed = inspection?.proposed_interface
      || (inspection?.status === "approval_required" ? inspection.interface : null);
    summary.innerHTML = `${heading}
      <dl class="remote-agent-facts">${fact("接入方式", drivers[config.protocol].transport || drivers[config.protocol].label)}
        <div><dt>状态</dt><dd>${inspectionState(inspection)}</dd></div>
        ${fact("远端 Agent", inspection?.agent_name || inspection?.name)}
        ${fact("检查时间", inspection?.checked_at && formatDate(inspection.checked_at))}
        ${fact("已选用", row.agents?.map(agent => agent.name).join("、"))}
        ${fact("Agent Card", config.card_url)}
        ${fact("已批准接口", config.approved_interface?.url || inspection?.approved_interface?.url)}
        ${fact("执行位置", config.execution === "ssh" ? config.ssh_host : config.execution === "local" ? "本机" : null)}
        ${fact("工作根目录", config.workspace_root || inspection?.workspace_root)}${fact("模型", config.model)}
      </dl>
      ${inspection?.message ? `<p class="muted">${escapeHtml(inspection.message)}</p>` : ""}
      ${proposed ? `<p class="remote-agent-proposed"><code>${escapeHtml(proposed.url || proposed)}</code><button type="button" id="approveRemoteInterface" class="btn" ${editor.dirty || editor.busy ? "disabled" : ""}>批准接口</button></p>` : ""}`;
  }

  function renderEditorState() {
    const ready = Boolean(editor.row.id && !editor.dirty && !editor.busy);
    const active = editor.debug.invocation && activeStates.has(editor.debug.invocation.status);
    el("checkRemoteConnection").disabled = !ready;
    el("checkRemoteConnection").textContent = editor.checking ? "正在检查…" : editor.config.protocol === "a2a" ? "检查连接" : "检查环境";
    el("saveRemoteConnection").disabled = editor.busy || editor.debugBusy;
    el("remoteConnectionForm").inert = editor.busy;
    el("remoteDebugPrompt").disabled = !ready || editor.debugBusy || active;
    el("attachRemoteDebug").disabled = el("remoteDebugPrompt").disabled;
    el("sendRemoteDebug").disabled = el("remoteDebugPrompt").disabled || (!el("remoteDebugPrompt").value.trim() && !editor.debug.files.length);
    el("resetRemoteDebug").disabled = !ready || editor.debugBusy;
    el("cancelRemoteDebug").hidden = !editor.debug.invocation || !["submitted", "working", "input_required"].includes(editor.debug.invocation.status);
    el("cancelRemoteDebug").disabled = !ready || editor.debugBusy;
    const invocation = editor.debug.invocation;
    el("refreshRemoteDebug").hidden = !invocation || !(invocation.warnings?.length
      || (invocation.status === "unknown" && invocation.remote_task_id));
    el("refreshRemoteDebug").disabled = !ready || editor.debugBusy || active;
    const notice = el("remoteDebugNotice");
    el("remoteDebugPanel").toggleAttribute("data-unsaved", !editor.row.id);
    if (!editor.row.id) {
      if (!notice.firstElementChild) {
        notice.innerHTML = `${icon("triangle-alert")}<span>保存后可调试</span>`;
        icons();
      }
    }
    else if (editor.dirty) notice.textContent = "保存当前修改后继续调试。";
    else if (!editor.debug.error) notice.textContent = active ? "远端任务正在执行…" : editor.debug.invocation?.status === "input_required" ? "回复下方追问以继续原任务。" : "";
  }

  function renderDebug() {
    const thread = el("remoteDebugThread");
    const state = editor.debug;
    const markup = state.messages.map(item => {
      if (item.role === "user") {
        const attachments = item.attachments?.length
          ? `<ul class="msg-attachments remote-agent-message-attachments" aria-label="已发送附件">${item.attachments.map(name => `<li>${icon("paperclip")}${escapeHtml(name)}</li>`).join("")}</ul>` : "";
        return `<article class="msg-row user"><div class="bubble-wrap"><div class="bubble">${attachments}${item.text ? `<div class="bubble-text">${escapeHtml(item.text)}</div>` : ""}</div></div></article>`;
      }
      return `<article class="msg-row assistant assistant-answer"><div class="bubble-wrap assistant-message">
        ${item.text ? `<div class="assistant-final" data-remote-answer>${escapeHtml(item.text)}</div>` : ""}
        ${renderFiles(item.files)}
        ${(item.warnings || []).map(warning => `<div class="muted">${escapeHtml(warning)}</div>`).join("")}
        ${item.status && item.status !== "completed" ? status(item.status) : ""}
      </div></article>`;
    }).join("");
    if (thread.remoteAgentMarkup !== markup) {
      const markdown = window.DeepAgentMarkdown;
      const codeState = markdown?.captureCodeState(thread);
      replaceMarkup(thread, markup);
      thread.querySelectorAll("[data-remote-answer]").forEach(body => markdown?.replaceBody(body, body.textContent));
      markdown?.restoreCodeState(thread, codeState);
      void markdown?.renderDiagrams(thread);
      thread.scrollTop = thread.scrollHeight;
    }
    const files = el("remoteDebugFiles");
    files.hidden = !state.files.length;
    files.innerHTML = state.files.map((file, i) => `<div class="pending-attachment"><span class="pending-attachment-name">${escapeHtml(file.name)}</span>
      <button type="button" class="pending-attachment-remove" data-debug-remove="${i}" aria-label="移除 ${attr(file.name)}" title="移除附件">${icon("x")}</button></div>`).join("");
    resizeComposerInput(el("remoteDebugPrompt"));
    renderEditorState();
    icons();
  }

  function renderEditor() {
    renderConfig();
    renderSummary();
    renderDebug();
  }

  function dirty() {
    editor.dirty = true;
    renderEditorState();
    const approve = el("approveRemoteInterface");
    if (approve) approve.disabled = true;
  }

  async function save(event) {
    event.preventDefault();
    const target = editor;
    if (!target || target.busy || target.debugBusy) return;
    const statusNode = el("remoteConfigStatus");
    let config;
    try {
      config = editor.json ? configFromJson() : configFromForm();
    } catch (error) {
      statusNode.textContent = error.message;
      return;
    }
    target.busy = true;
    statusNode.textContent = "";
    renderEditorState();
    try {
      const { id, revision } = target.row;
      const row = await post(id ? path(id) : endpoint, {
        name: nameInput().value.trim(), description: descInput().value.trim(), config, ...(id ? { revision } : {}),
      }, id ? "PUT" : "POST");
      if (target !== editor) return;
      editor.row = row;
      editor.config = row.config;
      editor.headers = Object.entries(row.config.headers || {});
      editor.dirty = false;
      editor.json = false;
      editor.formModes = { workspace_root: row.config.workspace_root ? "custom" : "default", model: row.config.model ? "custom" : "default" };
      editor.debug.error = false;
      if (revision !== row.revision) editor.debug = { files: [], messages: [], invocation: null };
      el("remoteConfigJson").value = "";
      el("remoteEditorTitle").textContent = `编辑远程 Agent · ${row.name}`;
      showToast("远程 Agent 已保存");
      renderEditor();
    } catch (error) {
      statusNode.textContent = error.status === 409 ? "连接已被更新。返回列表重开后可查看最新配置。" : error.message;
    } finally {
      if (editor === target) {
        target.busy = false;
        renderEditorState();
        renderSummary();
        icons();
      }
    }
  }

  async function inspectConnection(target, approve = false) {
    const result = await post(`${path(target.id)}/check`, approve ? {
      approve_interface: true,
      expected_revision: target.revision,
      expected_interface: target.inspection?.proposed_interface,
    } : {});
    return result.config ? result : { ...target, inspection: result.inspection || result };
  }

  async function check(approve = false) {
    const target = editor;
    if (!target?.row.id || target.busy || target.dirty) return;
    if (approve && !(await confirmAction({
      title: "批准此 A2A 接口？", message: "发送任务时，此接口会收到为连接配置的请求 Header。",
      icon: "shield-check", confirmLabel: "批准接口",
    }))) return;
    if (target !== editor || target.dirty || target.busy) return;
    target.busy = true;
    target.checking = true;
    const notice = el("remoteConfigStatus");
    notice.textContent = "";
    renderEditorState();
    renderSummary();
    try {
      const row = await inspectConnection(target.row, approve);
      if (target !== editor) return;
      target.row = row;
      target.config = target.row.config;
    } catch (error) {
      notice.textContent = error.message;
    } finally {
      if (target === editor) {
        target.busy = false;
        target.checking = false;
        renderEditorState();
        renderSummary();
        icons();
      }
    }
  }

  async function checkFromCard(button) {
    const id = button.dataset.remoteCheck;
    const row = connections.find(item => item.id === id);
    if (!row || checkingConnections.has(id)) return;
    const wasFocused = document.activeElement === button;
    checkingConnections.add(id);
    renderConnections();
    try {
      const checked = await inspectConnection(row);
      connections = connections.map(item => item.id === id && item.revision <= checked.revision ? checked : item);
      const inspection = checked.inspection;
      if (inspection.status === "failed") showToast(inspection.message || "检查失败", true);
      else if (inspection.status === "approval_required") showToast("检查完成，请在详情中批准 A2A 接口");
      else showToast("检查完成");
    } catch (error) {
      showToast(error.message, true);
    } finally {
      checkingConnections.delete(id);
      renderConnections();
      if (wasFocused && document.activeElement === document.body && recentIsVisible() && view === "cards") {
        el("remoteConnectionCardsHost").querySelector(`[data-remote-check="${CSS.escape(id)}"]`)?.focus({ preventScroll: true });
      }
    }
  }

  function scheduleDebugPoll(delay = 1500) {
    clearTimeout(debugPollTimer);
    if (panelIsVisible() && activeStates.has(editor?.debug.invocation?.status)) {
      debugPollTimer = setTimeout(() => void debugAction("refresh"), delay);
    }
  }

  async function debugAction(action, manual = false) {
    const target = editor;
    if (!target?.row.id) return;
    const state = target.debug;
    const auto = action === "refresh" && !manual;
    if (auto) {
      if (!panelIsVisible() || !activeStates.has(state.invocation?.status)) return;
      if (target.debugBusy || target.busy) { scheduleDebugPoll(); return; }
    } else if (target.debugBusy || target.dirty || target.busy) return;
    const input = el("remoteDebugPrompt");
    const message = action === "send" ? input.value.trim() : "";
    const files = action === "send" ? [...state.files] : [];
    if (action === "send" && !message && !files.length) return;
    const clearLocal = () => {
      target.debug = { files: [], messages: [], invocation: null };
      input.value = "";
    };
    if (action === "clear" && !state.invocation) {
      clearLocal();
      renderDebug();
      return;
    }
    const questionId = action === "send" && state.invocation?.status === "input_required" ? state.invocation.question_id : null;
    let pollDelay = 1500;
    target.debugBusy = true;
    state.error = false;
    renderEditorState();
    try {
      const row = await post(`${path(target.row.id)}/debug`, {
        action, message, attachments: files.map(({ id, name, mime }) => ({ id, name, mime })), invocation_id: state.invocation?.id,
        ...(questionId ? { question_id: questionId } : {}),
      });
      if (target !== editor) return;
      if (action === "clear") {
        clearLocal();
        return;
      }
      state.invocation = row;
      if (action === "send") {
        state.messages.push({ role: "user", text: message, attachments: files.map(file => file.name) });
        input.value = "";
        state.files = [];
      }
      const latest = { role: "agent", status: row.status, text: row.question || row.text || row.result, files: row.files, warnings: row.warnings };
      if (action !== "send" && state.messages.at(-1)?.role === "agent") state.messages[state.messages.length - 1] = latest;
      else state.messages.push(latest);
    } catch (error) {
      if (target !== editor) return;
      if (action === "clear" && error.status === 404) { clearLocal(); return; }
      if (action === "refresh" && error.status === 404) {
        state.invocation = null;
        if (state.messages.at(-1)?.role === "agent") state.messages.at(-1).status = "unknown";
      }
      state.error = true;
      el("remoteDebugNotice").textContent = error.message;
      if (auto) pollDelay = 4000;
    } finally {
      if (target === editor) {
        target.debugBusy = false;
        renderDebug();
        scheduleDebugPoll(pollDelay);
      }
    }
  }

  async function uploadFiles(files) {
    const target = editor;
    if (!target || el("attachRemoteDebug").disabled) return;
    target.debugBusy = true;
    target.debug.error = false;
    renderEditorState();
    try {
      const limits = await apiFetch("/api/v1/uploads/capabilities");
      for (const file of files) {
        if (editor !== target) return;
        if (target.debug.files.length >= limits.max_count) throw new Error(`单次最多 ${limits.max_count} 个附件。`);
        if (file.size > limits.max_size_mb * 1024 * 1024) throw new Error(`单个附件不能超过 ${limits.max_size_mb} MB。`);
        const body = new FormData();
        body.append("file", file, file.name);
        const response = await daFetch(serviceUrl("api/v1/uploads"), { method: "POST", body });
        if (!response.ok) {
          const error = await response.json().catch(() => ({}));
          throw new Error(typeof error.detail === "string" ? error.detail : `上传失败（HTTP ${response.status}）`);
        }
        target.debug.files.push(await response.json());
      }
    } catch (error) {
      if (editor === target) {
        target.debug.error = true;
        el("remoteDebugNotice").textContent = error.message;
      }
    } finally {
      if (editor === target) {
        target.debugBusy = false;
        renderDebug();
      }
    }
  }

  async function removeConnection(id) {
    const row = connections.find(item => item.id === id);
    if (!row || !(await confirmAction({
      title: "删除远程 Agent？", message: `将删除「${row.name}」的连接配置。`,
      icon: "trash-2", tone: "danger", confirmLabel: "删除",
    }))) return;
    try {
      await apiFetch(`${path(id)}?revision=${encodeURIComponent(row.revision)}`, { method: "DELETE" });
      showToast("远程 Agent 已删除");
      await refresh();
    } catch (error) {
      const refs = error.detail?.agents || error.detail?.references;
      const names = Array.isArray(refs) ? refs.map(item => item.name || item.id || item).join("、") : "";
      showToast(names ? `请先在这些 Agent 中解除关联：${names}` : error.message, true);
    }
  }

  function bind() {
    const panel = el("panel-remote-agents");
    if (!panel) return;
    panel.addEventListener("click", async (event) => {
      const button = event.target.closest("button");
      if (!button) {
        const call = event.target.closest("[data-remote-call-row]");
        if (call) { selectedRecent = call.dataset.remoteCallRow; renderRecent(); return; }
        const connection = event.target.closest("[data-remote-card], [data-remote-connection-row]");
        if (connection) await loadEditor(connection.dataset.remoteCard || connection.dataset.remoteConnectionRow);
        return;
      }
      const data = button.dataset;
      if (data.remoteView) {
        view = data.remoteView;
        panel.querySelectorAll("[data-remote-view]").forEach(item => {
          const selected = item.dataset.remoteView === view;
          item.classList.toggle("active", selected);
          item.setAttribute("aria-pressed", String(selected));
        });
        renderConnections();
      } else if (data.remoteOpen) await loadEditor(data.remoteOpen);
      else if (data.remoteCheck) await checkFromCard(button);
      else if (data.remoteDelete) await removeConnection(data.remoteDelete);
      else if (data.remoteCall) { selectedRecent = data.remoteCall; renderRecent(); }
      else if (data.remoteCopy) {
        try {
          await navigator.clipboard.writeText(data.remoteCopy);
          showToast(`${data.copyLabel} 已复制`);
        } catch {
          showToast("复制失败", true);
        }
      }
      else if (button.id === "retryRemoteDirectory") await refresh();
      else if (button.id === "prevRemoteCalls") setRecentPage(recentPage - 1);
      else if (button.id === "nextRemoteCalls") setRecentPage(recentPage + 1);
      else if (button.id === "addRemoteConnection") openEditor();
      else if (button.id === "closeRemoteEditor") { if (await requestCloseEditor()) await refresh(); }
      else if (button.id === "checkRemoteConnection") await check();
      else if (button.id === "approveRemoteInterface") await check(true);
      else if (button.id === "toggleRemoteConfigMode") toggleConfigMode();
      else if (button.id === "addRemoteHeader") {
        editor.headers.push(["", ""]);
        dirty();
        renderConfig();
        el("remoteDriverFields").querySelector(`[data-header-key="${editor.headers.length - 1}"]`)?.focus();
      } else if (data.headerRemove != null) {
        editor.headers.splice(Number(data.headerRemove), 1); dirty(); renderConfig();
      } else if (data.remoteDriver && data.remoteDriver !== editor.config.protocol) {
        if (editor.json) toggleConfigMode();
        if (editor.json) return;
        editor.config = baseConfig(data.remoteDriver);
        editor.headers = [];
        editor.formModes = { workspace_root: "default", model: "default" };
        editor.advanced = false;
        dirty();
        renderConfig();
        renderSummary();
        el("remoteDriverOptions").querySelector(`[data-remote-driver="${data.remoteDriver}"]`)?.focus();
      } else if (data.configOption) {
        if (data.configOption === "workspace_root" || data.configOption === "model") editor.formModes[data.configOption] = data.configValue;
        else editor.config[data.configOption] = data.configValue;
        dirty();
        renderConfig();
        el("remoteDriverFields").querySelector(`[data-config-option="${data.configOption}"][data-config-value="${data.configValue}"]`)?.focus();
      } else if (button.id === "attachRemoteDebug") el("remoteDebugFileInput").click();
      else if (button.id === "resetRemoteDebug") await debugAction("clear");
      else if (button.id === "cancelRemoteDebug") await debugAction("cancel");
      else if (button.id === "refreshRemoteDebug") await debugAction("refresh", true);
      else if (data.debugRemove != null) { editor.debug.files.splice(Number(data.debugRemove), 1); renderDebug(); }
    });
    el("remoteConnectionSearch").addEventListener("input", renderConnections);
    el("remoteCallSearch").addEventListener("input", () => setRecentPage(1));
    for (const id of ["remoteCallConnection", "remoteCallStatus"]) el(id).addEventListener("change", () => setRecentPage(1));
    window.bindConsolePaneResize(el("remoteDirectorySplitter"), el("remoteDirectoryLayout"),
      "--remote-agent-top-height", { pane: el("remoteConnectionPane"), sizePrefix: "--remote-agent" });
    window.bindConsolePaneResize(el("remoteCallSplitter"), el("remoteCallWorkbench"), "--task-list-width");
    const editorLayout = el("remoteAgentEditorLayout");
    const configPane = el("remoteConnectionForm");
    window.bindConsolePaneResize(el("remoteConfigSplitter"), editorLayout, "--remote-config-width", {
      pane: configPane,
      widthBounds() {
        const style = getComputedStyle(editorLayout);
        const minimum = parseFloat(style.getPropertyValue("--remote-config-min"));
        const limit = parseFloat(style.getPropertyValue("--remote-config-max"));
        const debugMinimum = parseFloat(style.getPropertyValue("--remote-debug-min"));
        const available = configPane.offsetWidth + el("remoteDebugPanel").offsetWidth - debugMinimum;
        return [minimum, Math.max(minimum, Math.min(limit, available))];
      },
    });
    el("remoteConnectionForm").addEventListener("input", (event) => {
      if (!editor) return;
      const { configField, headerKey, headerValue } = event.target.dataset;
      if (configField) editor.config[configField] = event.target.value;
      if (headerKey != null) editor.headers[Number(headerKey)][0] = event.target.value;
      if (headerValue != null) {
        const index = Number(headerValue);
        const key = editor.headers[index][0];
        const alreadySaved = Object.keys(editor.row.config?.headers || {}).some(name => name.toLowerCase() === key.trim().toLowerCase());
        editor.headers[index][1] = event.target.value || (alreadySaved ? null : "");
      }
      dirty();
    });
    el("remoteConnectionForm").addEventListener("submit", save);
    const debugForm = el("remoteDebugForm");
    const debugInput = el("remoteDebugPrompt");
    debugForm.addEventListener("submit", (event) => { event.preventDefault(); void debugAction("send"); });
    bindComposerInput(debugInput, () => {
      if (!el("sendRemoteDebug").disabled) debugForm.requestSubmit();
    });
    bindComposerAttachments(debugInput, debugForm, el("remoteDebugDropOverlay"), uploadFiles);
    debugInput.addEventListener("input", renderEditorState);
    el("remoteDebugFileInput").addEventListener("change", (event) => {
      const files = Array.from(event.target.files || []);
      event.target.value = "";
      if (files.length) void uploadFiles(files);
    });
    document.addEventListener("visibilitychange", () => { schedulePoll(); scheduleDebugPoll(); });
  }

  async function catalog() {
    const rows = await apiFetch(endpoint);
    return rows.filter(row => Object.hasOwn(drivers, protocolOf(row))).map(row => ({
      code: `agent_connection:${row.id}`, category: "agent_connection", label: row.name,
      protocol: protocolOf(row), description: row.description, inspection: row.inspection,
    }));
  }

  window.remoteAgentAdmin = { refresh, requestCloseEditor, catalog, driverBadge, inspectionState };
  document.addEventListener("DOMContentLoaded", bind);
})();
