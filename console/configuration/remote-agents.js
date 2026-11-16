import { attachmentDownloadUrl } from "../attachment-preview.js?v=20261103-view-image1";

(() => {
  const endpoint = "/api/v1/remote-agent-connections";
  const el = (id) => document.getElementById(id);
  const attr = (value) => escapeHtml(value).replaceAll('"', "&quot;").replaceAll("'", "&#39;");
  const icon = (name) => `<i data-lucide="${name}" aria-hidden="true"></i>`;
  const icons = () => window.lucide?.createIcons();
  const drivers = {
    a2a: { label: "A2A", icon: "network", help: "从 Agent Card 发现远端 Agent，并通过 A2A 提交任务。" },
    codex: { label: "Codex", icon: "terminal", help: "使用 Codex CLI 执行任务并续接原有对话。" },
    codebuddy: { label: "CodeBuddy", icon: "bot", help: "使用 CodeBuddy CLI 执行任务并续接原有对话。" },
    mcp: { label: "MCP", icon: "plug", help: "使用已有的 MCP 连接调用 DeepAgent 发布的 Agent。" },
  };
  const labels = {
    submitted: "排队中", working: "进行中", input_required: "待补答", completed: "已完成",
    failed: "失败", canceled: "已取消", unknown: "结果未知",
  };
  const activeStates = new Set(["submitted", "working"]);
  const fieldOptions = {
    execution: { local: "本机", ssh: "SSH" },
    cwd: { default: "默认", custom: "指定" },
    model: { default: "默认", custom: "指定" },
    sandbox: { "read-only": "只读", "workspace-write": "可写" },
  };
  let connections = [];
  let recent = [];
  let selectedRecent;
  let view = "cards";
  let editor;
  let homeRequest;
  let openRequest = 0;
  let pollTimer;
  let binding = false;

  const path = (id) => `${endpoint}/${encodeURIComponent(id)}`;
  const post = (url, body, method = "POST") => apiFetch(url, { method, body: JSON.stringify(body) });
  const protocolOf = (row) => row?.config?.protocol || row?.protocol;

  function driverBadge(protocol) {
    const driver = drivers[protocol];
    return driver ? `<span class="remote-agent-driver">${icon(driver.icon)}${driver.label}</span>` : "—";
  }

  function status(value, label = labels[value]) {
    return `<span class="remote-agent-state" data-state="${attr(value)}">${escapeHtml(label || "未知")}</span>`;
  }

  function inspectionState(inspection) {
    if (!inspection) return status("unchecked", "未检查");
    if (["ok", "healthy", "ready", "success"].includes(inspection.status) || inspection.ok === true) {
      return status("completed", "正常");
    }
    if (inspection.status === "approval_required" || inspection.proposed_interface) return status("input_required", "待确认");
    return status("failed", "异常");
  }

  function renderConnections() {
    const q = el("remoteConnectionSearch").value.trim().toLocaleLowerCase();
    const shown = connections.filter(row => `${row.name} ${row.description || ""} ${drivers[protocolOf(row)]?.label || ""}`.toLocaleLowerCase().includes(q));
    const cards = el("remoteConnectionCardsHost");
    const list = el("remoteConnectionListHost");
    cards.hidden = view !== "cards" || !shown.length;
    list.hidden = view !== "list" || !shown.length;
    const remove = (row) => `<button type="button" class="row-action icon-only danger" data-remote-delete="${attr(row.id)}" title="删除" aria-label="删除 ${attr(row.name)}">${icon("trash-2")}</button>`;
    const open = (row) => `<button type="button" class="btn text remote-agent-open" data-remote-open="${attr(row.id)}" title="${attr(row.name)}">${escapeHtml(row.name)}</button>`;
    cards.innerHTML = shown.map(row => `
      <article class="remote-agent-card" data-remote-card="${attr(row.id)}">
        <header><span class="remote-agent-card-icon">${icon(drivers[protocolOf(row)]?.icon || "bot")}</span>${open(row)}${driverBadge(protocolOf(row))}</header>
        <p>${escapeHtml(row.description || "—")}</p>
        <footer>${inspectionState(row.inspection)}<div class="row-actions agent-row-actions">${remove(row)}</div></footer>
      </article>`).join("");
    el("remoteConnectionRows").innerHTML = shown.map(row => `
      <tr><td>${open(row)}${driverBadge(protocolOf(row))}</td><td>${escapeHtml(row.description || "—")}</td><td>${inspectionState(row.inspection)}</td><td><div class="row-actions agent-row-actions">${remove(row)}</div></td></tr>`).join("");
    el("remoteConnectionNotice").textContent = shown.length ? "" : q ? "没有匹配的远程 Agent" : "暂无远程 Agent";
    icons();
  }

  function recentIsVisible() {
    const panel = el("panel-remote-agents");
    return panel?.classList.contains("active") && !editor && !document.hidden
      && (!document.body.dataset.consolePage || document.body.dataset.consolePage === "configuration");
  }

  function renderRecentFilter() {
    const select = el("remoteCallConnection");
    const previous = select.value;
    const names = new Map(connections.map(row => [row.id, row.name]));
    for (const row of recent) if (!names.has(row.connection_id)) {
      names.set(row.connection_id, row.connection_name || row.name || row.connection_id);
    }
    const options = [new Option("全部远程 Agent", ""), ...Array.from(names, ([id, name]) => new Option(name, id))];
    if (Array.from(select.options).map(o => [o.value, o.text]).toString() === options.map(o => [o.value, o.text]).toString()) return;
    select.replaceChildren(...options);
    select.value = names.has(previous) ? previous : "";
  }

  function renderRecent(message = "") {
    renderRecentFilter();
    const keyword = el("remoteCallSearch").value.trim().toLocaleLowerCase();
    const connectionId = el("remoteCallConnection").value;
    const protocol = el("remoteCallProtocol").value;
    const filterStatus = el("remoteCallStatus").value;
    const shown = recent.filter(row => (!connectionId || connectionId === row.connection_id)
      && (!protocol || protocol === row.protocol)
      && (!filterStatus || (filterStatus === "working" ? activeStates.has(row.status) : filterStatus === row.status))
      && (!keyword || `${row.title || row.task || ""} ${row.id} ${row.remote_task_id || ""}`.toLocaleLowerCase().includes(keyword)));
    const selected = shown.find(row => row.id === selectedRecent) || shown[0];
    selectedRecent = selected?.id;
    const markup = shown.length ? shown.map(row => {
      const name = row.connection_name || connections.find(item => item.id === row.connection_id)?.name || row.name;
      const title = row.title || row.task || row.label || "远程 Agent 调用";
      return `<tr class="${row.id === selectedRecent ? "selected" : ""}">
        <td><button type="button" class="btn text remote-agent-call-name" data-remote-call="${attr(row.id)}" aria-pressed="${row.id === selectedRecent}"><span>${escapeHtml(title)}</span><code>${escapeHtml(String(row.id).slice(0, 8))}</code></button></td>
        <td><div class="remote-agent-call-name"><span>${escapeHtml(name || "—")}</span>${driverBadge(row.protocol)}</div></td>
        <td>${status(row.status)}</td><td><time datetime="${attr(row.created_at)}">${escapeHtml(formatDate(row.created_at))}</time></td></tr>`;
    }).join("") : `<tr><td colspan="4" class="remote-agent-empty">${escapeHtml(message || "暂无调用记录")}</td></tr>`;
    replaceMarkup(el("remoteCallRows"), markup);
    el("remoteCallCount").textContent = `共 ${shown.length} 条`;
    renderRecentDetail(selected);
    icons();
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

  function renderRecentDetail(row) {
    if (!row) {
      replaceMarkup(el("remoteCallDetail"), '<p class="remote-agent-empty">选择调用查看详情</p>');
      return;
    }
    const title = row.title || row.task || row.label || "调用详情";
    const question = row.question || (row.status === "input_required" ? row.text : "");
    const result = row.result || (row.status === "input_required" ? "" : row.text);
    const sessionUrl = row.session_id && window.DeepAgentConsoleAPI?.consoleUrl(
      `#/diagnostics/sessions/${encodeURIComponent(row.session_id)}`,
    );
    const events = (Array.isArray(row.timeline) ? row.timeline : []).map(item => `<li>${status(item.status, item.label)}<time>${escapeHtml(formatDate(item.at || item.created_at))}</time></li>`).join("");
    replaceMarkup(el("remoteCallDetail"), `
      <div class="remote-agent-toolbar remote-agent-detail-heading"><h3>${escapeHtml(title)}</h3>${status(row.status)}</div>
      <dl class="remote-agent-facts">
        <div><dt>接入方式</dt><dd>${driverBadge(row.protocol)}</dd></div>
        ${fact("远程 Agent", row.name)}
        ${fact("提交时间", formatDate(row.created_at))}
        ${fact("调用 ID", row.id)}
      </dl>
      <section class="remote-agent-detail-section"><h4>外部会话</h4><dl class="remote-agent-facts">
        ${fact("任务 ID", row.remote_task_id)}
        ${fact("远端会话", row.remote_context_id || row.remote_session_id || row.thread_id)}
        ${fact("提问 ID", row.question_id)}
      </dl></section>
      ${sessionUrl ? `<section class="remote-agent-detail-section"><h4>本地会话</h4><dl class="remote-agent-facts"><div><dt>会话 ID</dt><dd><a href="${attr(sessionUrl)}">${escapeHtml(row.session_id)}</a></dd></div>${fact("提问 ID", row.turn_id)}</dl></section>` : ""}
      ${events ? `<section class="remote-agent-detail-section"><h4>任务进度</h4><ol>${events}</ol></section>` : ""}
      ${question ? `<section class="remote-agent-detail-section"><h4>待补材料</h4><p>${escapeHtml(question)}</p></section>` : ""}
      ${result ? `<section class="remote-agent-detail-section remote-agent-result"><h4>任务结果</h4><p>${escapeHtml(result)}</p></section>` : ""}
      ${(row.warnings || []).map(warning => `<p class="muted">${escapeHtml(warning)}</p>`).join("")}
      ${renderFiles(row.files)}`);
  }

  function schedulePoll() {
    clearTimeout(pollTimer);
    if (recentIsVisible()) pollTimer = setTimeout(() => void updateRecent(), 4000);
  }

  async function updateRecent() {
    if (!recentIsVisible()) return;
    try {
      const rows = await apiFetch(`${endpoint}/recent`);
      if (!recentIsVisible()) return;
      recent = rows;
      renderRecent();
    } catch (error) {
      if (recentIsVisible()) renderRecent(error.message || "近期调用加载失败");
    } finally {
      schedulePoll();
    }
  }

  async function refresh() {
    if (editor || !el("remoteAgentDirectory")) return;
    homeRequest?.abort();
    const request = new AbortController();
    homeRequest = request;
    el("remoteConnectionNotice").textContent = "正在加载远程 Agent…";
    try {
      const rows = await apiFetch(endpoint, { signal: request.signal });
      if (request.signal.aborted) return;
      connections = rows;
      renderConnections();
      await updateRecent();
    } catch (error) {
      if (!request.signal.aborted) {
        const notice = el("remoteConnectionNotice");
        const retry = document.createElement("button");
        retry.className = "btn text compact";
        retry.type = "button";
        retry.textContent = "重试";
        retry.addEventListener("click", () => void refresh());
        notice.replaceChildren(document.createTextNode(error.message || "加载远程 Agent 失败"), retry);
        schedulePoll();
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

  function openEditor(row = {}, initial = {}) {
    homeRequest?.abort();
    clearTimeout(pollTimer);
    const config = { ...baseConfig(protocolOf(row) || initial.protocol || "a2a"), ...(row.config || initial.config) };
    editor = {
      row, config, formModes: { cwd: config.cwd ? "custom" : "default", model: config.model ? "custom" : "default" },
      dirty: Boolean(initial.config), json: false, advanced: false, busy: false, debugBusy: false,
      headers: Object.entries(config.headers || {}), debug: { files: [], messages: [], invocation: null },
    };
    el("remoteAgentDirectory").hidden = true;
    el("remoteAgentEditor").hidden = false;
    nameInput().value = row.name || initial.name || "";
    descInput().value = row.description || initial.description || "";
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
    editor = null;
    el("remoteAgentEditor").hidden = true;
    el("remoteAgentDirectory").hidden = false;
    nameInput().value = "";
    descInput().value = "";
    el("remoteConfigJson").value = "";
    el("remoteDriverFields").replaceChildren();
    el("remoteDebugPrompt").value = "";
    el("remoteDebugThread").replaceChildren();
    el("remoteDebugFiles").replaceChildren();
    return true;
  }

  function field(label, key, { value, hint, type = "text", required = false, readonly = false } = {}) {
    const identifier = `remote-field-${key}`;
    return `<label class="field"><span class="field-label-row">${label}${hint ? tooltip(identifier, hint) : ""}</span>
      <input type="${type}" id="${identifier}" ${readonly ? "readonly" : `data-config-field="${key}"`} value="${attr(value ?? editor.config[key])}" autocomplete="off" ${required ? "required" : ""}></label>`;
  }

  function tooltip(id, content) {
    return `<span class="field-help" tabindex="0" aria-label="查看字段说明" aria-describedby="${id}-tip">${icon("circle-help")}<span class="field-help-tooltip" id="${id}-tip" role="tooltip">${escapeHtml(content)}</span></span>`;
  }

  function segmented(label, key, current, content = "") {
    return `<div class="remote-agent-option"><div class="remote-agent-option-label"><span>${label}</span>
      <div class="console-segmented" role="group" aria-label="${label}">${Object.entries(fieldOptions[key]).map(([value, label]) => `<button type="button" class="${value === current ? "active" : ""}" data-config-option="${key}" data-config-value="${value}" aria-pressed="${value === current}">${label}</button>`).join("")}</div>
      </div>${content ? `<div class="remote-agent-option-field">${content}</div>` : ""}</div>`;
  }

  function optionField(key, label, placeholder, required = false) {
    return `<input class="console-input" aria-label="${label}" data-config-field="${key}" value="${attr(editor.config[key])}" placeholder="${attr(placeholder)}" autocomplete="off" ${required ? "required" : ""}>`;
  }

  function driverFields() {
    const config = editor.config;
    if (config.protocol === "mcp") {
      return field("MCP 连接", "mcp_server_name", { readonly: true })
        + field("目标发布", "publication_code", { readonly: true });
    }
    if (config.protocol === "a2a") {
      return `${field("Agent Card 地址", "card_url", { type: "url", required: true })}
        <div class="remote-agent-header-fields"><div class="remote-agent-toolbar"><strong>请求 Header</strong>
          <button type="button" class="btn text compact" id="addRemoteHeader">${icon("plus")}添加</button></div>
          ${editor.headers.map(([key, value], index) => `
            <div class="remote-agent-header-row">
              <input class="console-input" data-header-key="${index}" value="${attr(key)}" aria-label="Header 名称" placeholder="Header 名称" autocomplete="off">
              <input class="console-input" type="password" data-header-value="${index}" value="${attr(value)}" aria-label="Header 值" placeholder="${value === null ? "•••••• 保留原值" : "Header 值"}" autocomplete="new-password">
              <div class="row-actions agent-row-actions"><button type="button" class="row-action icon-only" data-header-remove="${index}" aria-label="删除 Header" title="删除 Header">${icon("trash-2")}</button></div>
            </div>`).join("")}</div>`;
    }
    const codex = config.protocol === "codex";
    return `
      ${segmented("执行位置", "execution", config.execution, config.execution === "ssh"
        ? optionField("ssh_host", "SSH 主机别名", "例如 development-server", true)
        : '<span class="muted">在本机执行。</span>')}
      ${segmented("工作目录", "cwd", editor.formModes.cwd, editor.formModes.cwd === "custom"
        ? optionField("cwd", "工作目录", "输入执行环境中的绝对路径", true)
        : '<span class="muted">为任务使用独立工作目录。</span>')}
      ${segmented("模型", "model", editor.formModes.model, editor.formModes.model === "custom"
        ? optionField("model", "模型", "输入 CLI 支持的模型", true)
        : '<span class="muted">使用 CLI 的默认模型。</span>')}
      ${codex ? segmented("工作区权限", "sandbox", config.sandbox) : ""}
      <details class="remote-agent-advanced" ${editor.advanced ? "open" : ""}>
        <summary>高级</summary>
        ${field("启动命令", "command", { hint: `留空使用 ${config.protocol}。也可填写执行环境中的可执行文件绝对路径。` })}
        ${codex ? field("配置档（Profile）", "profile") + field("推理强度", "reasoning_effort", { hint: "常见档位为 minimal、low、medium、high、xhigh。可用值取决于模型。" }) : ""}
      </details>`;
  }

  function renderConfig() {
    const cfg = editor.config;
    const mcp = cfg.protocol === "mcp";
    el("remoteDriverOptions").innerHTML = Object.entries(drivers).filter(([key]) => mcp ? key === "mcp" : key !== "mcp").map(([key, driver]) =>
      `<button type="button" class="btn remote-agent-driver-option ${key === cfg.protocol ? "active" : ""}" data-remote-driver="${key}" aria-pressed="${key === cfg.protocol}" ${mcp ? "disabled" : ""}>${icon(driver.icon)}<span>${driver.label}</span></button>`).join("");
    el("remoteDriverNote").textContent = drivers[cfg.protocol].help;
    const fields = el("remoteDriverFields");
    fields.hidden = editor.json;
    fields.innerHTML = editor.json ? "" : driverFields();
    fields.querySelector("details")?.addEventListener("toggle", (event) => {
      if (editor) editor.advanced = event.target.open;
    });
    el("remoteConfigJsonField").hidden = !editor.json;
    el("toggleRemoteConfigMode").querySelector("span").textContent = editor.json ? "切换为表单" : "切换为 JSON";
    el("toggleRemoteConfigMode").hidden = mcp;
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
    } else if (config.protocol !== "mcp") {
      if (editor.formModes.cwd !== "custom") delete config.cwd;
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
        editor.formModes = { cwd: config.cwd ? "custom" : "default", model: config.model ? "custom" : "default" };
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
    const config = row.config || editor.config;
    const inspection = row.inspection;
    const proposed = inspection?.proposed_interface
      || (inspection?.status === "approval_required" ? inspection.interface : null);
    el("remoteConnectionSummary").innerHTML = `
      <h3>${config.protocol === "a2a" ? "连接概览" : ["codex", "codebuddy"].includes(config.protocol) ? "执行环境" : "连接概览"}</h3>
      <dl class="remote-agent-facts">${fact("接入方式", drivers[config.protocol].label)}
        <div><dt>状态</dt><dd>${inspectionState(inspection)}</dd></div>
        ${fact("远端 Agent", inspection?.agent_name || inspection?.name)}
        ${fact("检查时间", inspection?.checked_at && formatDate(inspection.checked_at))}
        ${fact("已选用", row.agents?.map(agent => agent.name).join("、"))}
        ${fact("Agent Card", config.card_url)}
        ${fact("已批准接口", config.approved_interface?.url || inspection?.approved_interface?.url)}
        ${fact("执行位置", config.execution === "ssh" ? config.ssh_host : config.execution === "local" ? "本机" : null)}
        ${fact("工作目录", config.cwd)}${fact("模型", config.model)}
        ${fact("发布编码", config.publication_code)}
      </dl>
      ${inspection?.message ? `<p class="muted">${escapeHtml(inspection.message)}</p>` : ""}
      ${proposed ? `<p class="remote-agent-proposed"><code>${escapeHtml(proposed.url || proposed)}</code><button type="button" id="approveRemoteInterface" class="btn" ${editor.dirty || editor.busy ? "disabled" : ""}>批准接口</button></p>` : ""}`;
  }

  function renderEditorState() {
    const ready = Boolean(editor.row.id && !editor.dirty && !editor.busy);
    const active = editor.debug.invocation && activeStates.has(editor.debug.invocation.status);
    el("checkRemoteConnection").disabled = !ready;
    el("checkRemoteConnection").textContent = editor.checking ? "正在检查…" : ["a2a", "mcp"].includes(editor.config.protocol) ? "检查连接" : "检查环境";
    el("saveRemoteConnection").disabled = editor.busy;
    el("remoteConnectionForm").inert = editor.busy;
    el("remoteDebugPrompt").disabled = !ready || editor.debugBusy || active;
    el("attachRemoteDebug").disabled = el("remoteDebugPrompt").disabled;
    el("sendRemoteDebug").disabled = el("remoteDebugPrompt").disabled || !el("remoteDebugPrompt").value.trim();
    el("resetRemoteDebug").disabled = !ready || editor.debugBusy;
    el("cancelRemoteDebug").hidden = !editor.debug.invocation || !["submitted", "working", "input_required"].includes(editor.debug.invocation.status);
    el("cancelRemoteDebug").disabled = !ready || editor.debugBusy;
    const invocation = editor.debug.invocation;
    el("refreshRemoteDebug").hidden = !invocation || !(invocation.warnings?.length
      || (invocation.status === "unknown" && invocation.remote_task_id));
    el("refreshRemoteDebug").disabled = !ready || editor.debugBusy || active;
    const notice = el("remoteDebugNotice");
    if (!editor.row.id) notice.textContent = "保存连接后可发送调试消息。";
    else if (editor.dirty) notice.textContent = "保存当前修改后继续调试。";
    else if (!editor.debug.error) notice.textContent = active ? "远端任务正在执行…" : editor.debug.invocation?.status === "input_required" ? "回复下方追问以继续原任务。" : "";
  }

  function renderDebug() {
    const thread = el("remoteDebugThread");
    const state = editor.debug;
    thread.innerHTML = state.messages.map(item => `<article class="remote-agent-message ${item.role}">
      <header><strong>${item.role === "user" ? "你" : escapeHtml(editor.row.name || "远程 Agent")}</strong>${item.status ? status(item.status) : ""}</header>
      ${item.text ? `<p>${escapeHtml(item.text)}</p>` : ""}
      ${(item.warnings || []).map(warning => `<p class="muted">${escapeHtml(warning)}</p>`).join("")}
      ${renderFiles(item.files)}</article>`).join("");
    thread.scrollTop = thread.scrollHeight;
    const files = el("remoteDebugFiles");
    files.hidden = !state.files.length;
    files.innerHTML = state.files.map((file, i) => `<span>${icon("paperclip")}${escapeHtml(file.name)}
      <button type="button" class="btn text compact" data-debug-remove="${i}" aria-label="移除 ${attr(file.name)}" title="移除附件">${icon("x")}</button></span>`).join("");
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
    if (!target || target.busy) return;
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
      editor.formModes = { cwd: row.config.cwd ? "custom" : "default", model: row.config.model ? "custom" : "default" };
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
      const row = await post(`${path(target.row.id)}/check`, approve ? {
        approve_interface: true,
        expected_revision: target.row.revision,
        expected_interface: target.row.inspection?.proposed_interface,
      } : {});
      if (target !== editor) return;
      target.row = row.config ? row : { ...target.row, inspection: row.inspection || row };
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

  async function debugAction(action, manual = false) {
    const target = editor;
    if (!target?.row.id || target.debugBusy || target.dirty || target.busy) return;
    const state = target.debug;
    if (action === "refresh" && !manual && !activeStates.has(state.invocation?.status)) return;
    const input = el("remoteDebugPrompt");
    const message = action === "send" ? input.value.trim() : "";
    const files = action === "send" ? [...state.files] : [];
    if (action === "send" && !message) return;
    if (action === "clear" && !state.invocation) {
      target.debug = { files: [], messages: [], invocation: null };
      input.value = "";
      renderDebug();
      return;
    }
    target.debugBusy = true;
    state.error = false;
    renderEditorState();
    try {
      const row = await post(`${path(target.row.id)}/debug`, {
        action, message, attachments: files.map(({ id, name, mime }) => ({ id, name, mime })), invocation_id: state.invocation?.id,
      });
      if (target !== editor) return;
      if (action === "clear") {
        target.debug = { files: [], messages: [], invocation: null };
        input.value = "";
        return;
      }
      state.invocation = row;
      if (action === "send") {
        state.messages.push({ role: "user", text: message || files.map(file => file.name).join("、") });
        input.value = "";
        state.files = [];
      }
      const latest = { role: "agent", status: row.status, text: row.question || row.text || row.result, files: row.files, warnings: row.warnings };
      if (action !== "send" && state.messages.at(-1)?.role === "agent") state.messages[state.messages.length - 1] = latest;
      else state.messages.push(latest);
      if (activeStates.has(row.status)) {
        setTimeout(() => { if (editor === target && target.debug === state) void debugAction("refresh"); }, 1500);
      }
    } catch (error) {
      if (target !== editor) return;
      state.error = true;
      el("remoteDebugNotice").textContent = error.message;
      if (action === "refresh" && !manual && el("panel-remote-agents").classList.contains("active")) {
        setTimeout(() => { if (editor === target && target.debug === state) void debugAction("refresh"); }, 4000);
      }
    } finally {
      if (target === editor) {
        target.debugBusy = false;
        renderDebug();
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

  async function bindMcp(mcpServerName) {
    if (binding) return;
    binding = true;
    try {
      const publications = await post(`${endpoint}/mcp-publications`, { mcp_server_name: mcpServerName });
      if (!publications.length) { showToast("当前 MCP 连接没有可用的 Agent 发布"); return; }
      const selected = await requestActionFields({
        title: "绑定为远程 Agent", icon: "bot", confirmLabel: "继续",
        fields: [{ name: "code", label: "目标发布", required: true, value: publications[0].agent_code, options: publications.map(item => ({
          value: item.agent_code, label: item.name ? `${item.name} · ${item.agent_code}` : item.agent_code,
        })) }],
      });
      if (!selected) return;
      const pub = publications.find(item => item.agent_code === selected.code);
      if (!pub || !await window.mcpAdmin.requestCloseEditor()) return;
      document.querySelector('[data-tab="remote-agents"]')?.click();
      await window.configurationTabsAdmin?.pending;
      if (!el("panel-remote-agents").classList.contains("active")) return;
      openEditor({}, {
        name: pub.name || pub.agent_code,
        description: pub.description || "",
        protocol: "mcp",
        config: { mcp_server_name: mcpServerName, publication_code: pub.agent_code },
      });
    } catch (error) {
      showToast(error.message, true);
    } finally {
      binding = false;
    }
  }

  function bind() {
    const panel = el("panel-remote-agents");
    if (!panel) return;
    panel.addEventListener("click", async (event) => {
      const button = event.target.closest("button");
      if (!button) {
        const card = event.target.closest("[data-remote-card]");
        if (card) await loadEditor(card.dataset.remoteCard);
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
      else if (data.remoteDelete) await removeConnection(data.remoteDelete);
      else if (data.remoteCall) { selectedRecent = data.remoteCall; renderRecent(); }
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
        editor.formModes = { cwd: "default", model: "default" };
        editor.advanced = false;
        dirty();
        renderConfig();
        renderSummary();
      } else if (data.configOption) {
        if (data.configOption === "cwd" || data.configOption === "model") editor.formModes[data.configOption] = data.configValue;
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
    el("remoteCallSearch").addEventListener("input", () => renderRecent());
    for (const id of ["remoteCallConnection", "remoteCallProtocol", "remoteCallStatus"]) el(id).addEventListener("change", () => renderRecent());
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
    el("remoteDebugForm").addEventListener("submit", (event) => { event.preventDefault(); void debugAction("send"); });
    el("remoteDebugPrompt").addEventListener("input", renderEditorState);
    el("remoteDebugFileInput").addEventListener("change", (event) => {
      const files = Array.from(event.target.files || []);
      event.target.value = "";
      if (files.length) void uploadFiles(files);
    });
    document.addEventListener("visibilitychange", schedulePoll);
  }

  async function catalog() {
    const rows = await apiFetch(endpoint);
    return rows.map(row => ({
      code: `agent_connection:${row.id}`, category: "agent_connection", label: row.name,
      protocol: protocolOf(row), description: row.description, inspection: row.inspection,
    }));
  }

  window.remoteAgentAdmin = { refresh, requestCloseEditor, bindMcp, catalog, driverBadge, inspectionState };
  document.addEventListener("DOMContentLoaded", bind);
})();
