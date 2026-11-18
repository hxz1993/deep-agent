(() => {
  const root = "/api/v1/agent-service";
  const el = (id) => document.getElementById(id);
  const labels = {
    SUBMITTED: "排队中", WORKING: "进行中", COMPLETED: "已完成", FAILED: "失败",
    CANCELED: "已取消", REJECTED: "已拒绝", INPUT_REQUIRED: "待补材料", AUTH_REQUIRED: "待鉴权",
  };
  let rows = [], agents = [], apps = new Map(), tasks = [];
  let selected = "", selectedTask = "", access = "a2a", a2aCardMode = "general", page = 1;
  let clientConfig = {};
  let draft, detailRequest, taskRequest, timer, searchTimer;
  let directoryGeneration = 0;
  let taskRowsMarkup = "", taskDetailSnapshot = "", shownTask = "";
  const icon = (name) => `<i data-lucide="${name}"></i>`;
  const attr = (value) => escapeHtml(value).replaceAll('"', "&quot;").replaceAll("'", "&#39;");
  const refreshIcons = () => window.lucide?.createIcons();
  const statusLabel = (value) => labels[value?.replace("TASK_STATE_", "")] || "未知";
  const clearText = (id, value) => { el(id).textContent = value; };
  function duration(row) {
    const seconds = row.duration_seconds;
    if (!Number.isFinite(seconds)) return "—";
    const tenths = Math.round(seconds * 10);
    const minutes = Math.floor(tenths / 600);
    const remainder = (tenths % 600) / 10;
    return minutes ? `${minutes}m${remainder ? ` ${remainder}s` : ""}` : `${remainder}s`;
  }

  function copy(value) {
    return navigator.clipboard.writeText(value).then(() => showToast("已复制"), () => showToast("复制失败", true));
  }

  const copyButton = (value, label, variant = "text compact") => `<button type="button" class="btn ${variant}" data-client-copy="${attr(value)}">${label}</button>`;

  function publicationUpdate(row) {
    const operator = row.updated_by_user?.username || row.updated_by || "—";
    const time = formatDate(row.updated_at);
    return `<div class="agent-update-cell">
      <span class="agent-updater" title="更新人：${attr(operator)}">${icon("user-round")}<span class="agent-updater-name">${escapeHtml(operator)}</span></span>
      <span class="agent-update-time" title="${attr(time)}">${escapeHtml(time)}</span>
    </div>`;
  }

  function renderPublications(message = "") {
    const empty = !rows.length && !message;
    el("agentServiceEmpty").hidden = !empty;
    el("agentServiceLayout").hidden = empty;
    const body = el("agentPublicationRows");
    body.innerHTML = rows.length ? rows.map((row) => {
      const name = escapeHtml(row.name);
      const target = agents.find((item) => item.id === row.agent_id)?.name || row.agent_id;
      const scope = row.scope.apps;
      const scopeText = scope.mode === "all" ? "全部应用" :
        scope.items.map((item) => apps.get(item.app_id)?.app_name || item.app_id).join("、");
      return `<tr data-publication="${attr(row.agent_code)}" class="${selected === row.agent_code ? "selected" : ""}">
        <td><div class="agent-service-pub-name"><button type="button" class="model-enabled-switch ${row.enabled ? "on" : "off"}" role="switch" aria-checked="${row.enabled}" aria-label="${attr(row.name)}启用状态" data-service-action="toggle"><span></span></button>
        <button type="button" class="btn text agent-service-select" aria-controls="agentServiceAccess agentTaskList" aria-pressed="${selected === row.agent_code}"><span title="${attr(row.name)}">${name}</span><code title="${attr(row.agent_code)}">${escapeHtml(row.agent_code)}</code></button></div></td>
        <td title="${attr(target)}">${escapeHtml(target)}</td><td title="${attr(scopeText)}">${escapeHtml(scopeText)}</td>
        <td>${publicationUpdate(row)}</td>
        <td><div class="row-actions agent-row-actions"><button type="button" class="row-action icon-only" data-service-action="edit" aria-label="编辑${attr(row.name)}" title="编辑">${icon("pencil")}</button>
        <button type="button" class="row-action icon-only danger" data-service-action="delete" aria-label="删除${attr(row.name)}" title="删除">${icon("trash-2")}</button></div></td></tr>`;
    }).join("") : `<tr><td colspan="5" class="empty-cell">${escapeHtml(message || "暂无发布")}</td></tr>`;
    renderAccess();
  }

  const methods = { a2a: "A2A", mcp: "MCP", http: "HTTP" };
  const methodIcon = (method) => method === "http" ? icon("globe")
    : `<span class="agent-protocol-logo" data-protocol="${method}" aria-hidden="true"></span>`;
  const missingPublicAddress = "请在部署中配置 <code>DEEP_AGENT_AGENT_SERVICE_PUBLIC_BASE_URL</code>，填写客户端可达的服务基址，包含网关路径。";

  function accessToolbar(method, controls = "", publicationName = "") {
    const publication = publicationName
      ? `<span class="agent-service-publication-name" title="当前发布 ${attr(publicationName)}">${escapeHtml(publicationName)}</span>` : "";
    return `<div class="agent-service-card-heading agent-service-card-toolbar">
      <div class="agent-service-access-identity">
        <h4 class="agent-service-access-title" data-service-access="${method}">${methodIcon(method)}<span>${methods[method]} 接入</span></h4>
        ${publication}
      </div>
      ${controls}
    </div>`;
  }

  function renderMcpAccess(card, inGuide = false) {
    const endpoint = clientConfig.mcp_url;
    const publication = rows.find((row) => row.agent_code === selected);
    if (!endpoint) {
      const message = endpoint === null
        ? `当前环境未配置 MCP 接入地址。${missingPublicAddress}`
        : "暂时无法读取 MCP 接入地址，请稍后重试。";
      card.innerHTML = `${inGuide ? "" : accessToolbar("mcp", "", publication?.name)}<p>${message}</p>`;
      return;
    }
    const scope = publication?.scope.apps;
    const target = scope?.mode === "selected" && scope.items.length === 1 ? scope.items[0] : null;
    const config = JSON.stringify({
      mcpServers: {
        "deepagent-agents": {
          type: "streamableHttp",
          url: endpoint,
          headers: {
            app: target?.app_id || "<App ID>",
            space: target?.space_id || "<Space ID>",
            "X-Deepfos-Key": "<API Key>",
          },
        },
      },
    }, null, 2);
    const setupGuide = [
      "在 Agent 客户端添加以下 Streamable HTTP 配置。将 app 填为目标应用 ID，space 填为应用所属空间 ID。",
      config,
      "在客户端设置中填写一种凭据。有权访问该应用的 DeepFOS API Key 填入 X-Deepfos-Key。使用平台 Token 时，将 X-Deepfos-Key 换成 token 或 Authorization: Bearer <Token>。",
      "客户端须能访问配置中的 URL。localhost 和 127.0.0.1 仅供同机客户端使用。",
    ].join("\n\n");
    const guideButton = copyButton(setupGuide, "复制 Agent 接入指引", "compact");
    if (inGuide) {
      card.innerHTML = `<div class="agent-service-guide-heading"><h4>Agent 接入指引</h4>${guideButton}</div>
        <pre class="agent-service-guide-preview">${escapeHtml(setupGuide)}</pre>`;
      return;
    }
    card.innerHTML = `${accessToolbar("mcp", guideButton, publication?.name)}
      <div class="agent-service-card-option agent-service-card-options">
        <section>
          <div class="agent-service-card-heading"><h5>MCP 配置</h5>${copyButton(config, "复制配置")}</div>
          <pre class="agent-service-client-snippet">${escapeHtml(config)}</pre>
          <p class="agent-service-access-help">填写应用 ID、所属空间 ID 和一种凭据。</p>
        </section>
      </div>`;
  }

  function a2aHeaderExample({ appScoped = false, useToken = false } = {}) {
    const context = appScoped ? {} : {
      app: "budget001",
      space: "budget",
      language: "zh-cn",
    };
    const credential = useToken ? { token: "<Token>" } : { "X-Deepfos-Key": "<API Key>" };
    return JSON.stringify({ ...context, "A2A-Version": "1.0", ...credential }, null, 2);
  }

  function renderA2aAccess(card, row) {
    if (!row) {
      card.innerHTML = `${accessToolbar("a2a")}<p>请选择一项发布</p>`;
      return;
    }
    if (!row.card_url) {
      const message = row.enabled ? `当前环境未配置 A2A 接入地址。${missingPublicAddress}` : "该发布已停用。";
      card.innerHTML = `${accessToolbar("a2a", "", row.name)}<p>${message}</p>`;
      return;
    }
    const appScoped = a2aCardMode === "app" && Boolean(row.app_card_url_template);
    const url = appScoped ? row.app_card_url_template : row.card_url;
    const headers = a2aHeaderExample({ appScoped });
    const help = appScoped
      ? "将地址中的 <code>{app_id}</code> 替换为应用 ID。"
      : "在 <code>app</code> 中填写应用 ID。读取 Agent Card 时可省略 <code>A2A-Version</code>。";
    const cardModes = `<div class="console-segmented" role="group" aria-label="Agent Card 地址类型">
          <button type="button" data-a2a-card-mode="general" aria-pressed="${!appScoped}">通用卡片</button>
          <button type="button" data-a2a-card-mode="app" aria-pressed="${appScoped}"${row.app_card_url_template ? "" : " disabled"}>指定应用卡片</button>
        </div>`;
    card.innerHTML = `${accessToolbar("a2a", cardModes, row.name)}
      <div class="agent-service-card-option agent-service-card-options">
        <section>
          <div class="agent-service-card-heading"><h5>Agent Card 地址</h5>${copyButton(url, "复制地址")}</div>
          <div class="agent-service-card-url"><code title="${attr(url)}">${escapeHtml(url)}</code></div>
          <p class="agent-service-access-help">${help}</p>
        </section>
        <section>
          <div class="agent-service-card-heading"><h5>Header 示例</h5>${copyButton(headers, "复制示例")}</div>
          <pre class="agent-service-client-snippet">${escapeHtml(headers)}</pre>
        </section>
      </div>`;
  }

  function renderAccess() {
    const card = el("agentServiceAccess");
    el("agentServiceTabs").querySelectorAll("[data-service-access]").forEach((button) => {
      const active = button.dataset.serviceAccess === access;
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", String(active));
      button.tabIndex = active ? 0 : -1;
      if (active) card.setAttribute("aria-labelledby", button.id);
    });
    if (access === "mcp") renderMcpAccess(card);
    else if (access === "a2a") renderA2aAccess(card, rows.find((item) => item.agent_code === selected));
    else card.textContent = "接入方式待评审，本版本暂未开放。";
    refreshIcons();
  }

  function selectPublication(code) {
    selected = code;
    selectedTask = "";
    shownTask = "";
    page = 1;
    tasks = [];
    taskRequest?.abort();
    detailRequest?.abort();
    el("agentTaskApp").replaceChildren(new Option("全部调用应用", ""));
    renderTasks(0);
    if (code) renderDetailPlaceholder("正在加载近期任务…");
  }

  async function refresh() {
    const generation = ++directoryGeneration;
    try {
      const [publications, agentList, distribution] = await Promise.all([
        apiFetch(`${root}/publications`),
        apiFetch("/api/v1/agents").catch(() => []),
        apiFetch(`${root}/client-config`).catch(() => ({})),
      ]);
      if (generation !== directoryGeneration) return;
      rows = publications;
      agents = agentList;
      const mcpAddressChanged = clientConfig.mcp_url !== distribution.mcp_url;
      clientConfig = distribution;
      if (mcpAddressChanged && el("agentServiceProtocol").open) {
        renderMcpAccess(el("agentServiceMcpGuideSetup"), true);
      }
      el("newAgentPublication").disabled = false;
      if (!rows.some((row) => row.agent_code === selected)) {
        selectPublication(rows[0]?.agent_code || "");
      }
      renderPublications();
      await loadTasks();
    } catch (error) {
      if (generation !== directoryGeneration) return;
      selectPublication("");
      rows = [];
      el("newAgentPublication").disabled = true;
      renderPublications(error.message);
      renderTasks(0);
    }
  }

  async function loadTasks() {
    taskRequest?.abort();
    if (!selected) { tasks = []; renderTasks(0); return; }
    const request = taskRequest = new AbortController();
    const appFilter = el("agentTaskApp");
    const params = new URLSearchParams({ page, q: el("agentTaskSearch").value, state: el("agentTaskState").value });
    if (appFilter.value) params.set("app_id", appFilter.value);
    try {
      const result = await apiFetch(`${root}/publications/${selected}/tasks?${params}`, { signal: request.signal, reportStatus: false });
      if (request.signal.aborted) return;
      const appIds = result.app_ids || [];
      const currentApp = appFilter.value;
      const options = [...new Set([...appIds, ...(result.app_ids && currentApp ? [currentApp] : [])])];
      if (Array.from(appFilter.options).slice(1).map(option => option.value).join(",") !== options.join(",")) {
        appFilter.replaceChildren(new Option("全部调用应用", ""), ...options.map(id => new Option(id, id)));
        appFilter.value = currentApp;
      }
      if (!result.items.length && page > 1) {
        page = Math.max(1, Math.ceil(result.total / 20));
        return loadTasks();
      }
      tasks = result.items;
      renderTasks(result.total);
      void loadDetail();
    } catch (error) {
      if (request.signal.aborted) return;
      tasks = [];
      renderTasks(0, error.message);
    } finally {
      if (taskRequest === request) taskRequest = null;
    }
  }

  function renderTasks(total, message = "") {
    if (!tasks.some((row) => row.id === selectedTask)) selectedTask = "";
    const body = el("agentTaskRows");
    const filtering = el("agentTaskSearch").value.trim() || el("agentTaskState").value || el("agentTaskApp").value;
    const emptyLabel = message || (filtering ? "没有符合条件的任务" : "暂无任务");
    const markup = tasks.length ? tasks.map((row) => `
      <tr data-agent-task="${attr(row.id)}" class="${row.id === selectedTask ? "selected" : ""}">
        <td><button type="button" class="btn text agent-service-select" aria-controls="agentTaskDetail" aria-pressed="${row.id === selectedTask}"><span title="${attr(row.title)}">${escapeHtml(row.title)}</span><code title="${attr(row.id)}">${escapeHtml(row.id.slice(0, 8))}</code></button></td>
        <td><span class="agent-service-status" data-state="${attr(row.status)}">${statusLabel(row.status)}</span></td><td>${Object.hasOwn(methods, row.source) ? methods[row.source] : methods.a2a}</td>
        <td class="agent-task-wide" title="${attr(row.caller_username ? `${row.caller_username} · ${row.caller}` : row.caller)}">${escapeHtml(row.caller_username || row.caller?.slice(0, 8) || "—")}</td>
        <td class="agent-task-wide">${escapeHtml(formatDate(row.created_at))}</td>
        <td class="agent-task-wide">${duration(row)}</td>
        <td>${row.artifact_count ? `<span class="agent-task-file-count" role="img" aria-label="${row.artifact_count} 个文件" title="${row.artifact_count} 个文件"><i data-lucide="file-text" aria-hidden="true"></i>${row.artifact_count}</span>` : "—"}</td>
      </tr>`).join("") :
      `<tr><td colspan="7" class="empty-cell agent-task-wide">${escapeHtml(emptyLabel)}</td>
        <td colspan="4" class="empty-cell agent-task-compact">${escapeHtml(emptyLabel)}</td></tr>`;
    if (taskRowsMarkup !== markup) {
      const focusedId = body.contains(document.activeElement) && document.activeElement.closest("tr")?.dataset.agentTask;
      body.innerHTML = markup;
      taskRowsMarkup = markup;
      if (focusedId) body.querySelector(`[data-agent-task="${CSS.escape(focusedId)}"] button`)?.focus({ preventScroll: true });
      refreshIcons();
    }
    const pageCount = Math.max(1, Math.ceil(total / 20));
    el("prevAgentTasks").disabled = total === 0 || page <= 1;
    el("nextAgentTasks").disabled = page >= pageCount;
    const pageLabel = message ? "加载失败" : `第 ${page} / ${pageCount} 页 · 共 ${total.toLocaleString()} 条任务`;
    if (el("agentTaskPage").textContent !== pageLabel) clearText("agentTaskPage", pageLabel);
    if (!selectedTask) {
      detailRequest?.abort();
      shownTask = "";
      renderDetailPlaceholder(message ? "暂时无法查看任务详情" : tasks.length ? "请选择一条任务查看详情" : "暂无任务可查看");
    }
  }

  function renderDetailPlaceholder(message) {
    const markup = `<div class="agent-service-detail-heading"><h3>任务详情</h3></div>
      <div class="inspector-tab-empty" role="status"><span>${escapeHtml(message)}</span></div>`;
    if (taskDetailSnapshot === markup) return;
    taskDetailSnapshot = markup;
    el("agentTaskDetail").innerHTML = markup;
  }

  function taskIdValue(id, label, url = "") {
    if (!id) return "—";
    const code = `<code title="${attr(id)}">${escapeHtml(id.slice(0, 8))}</code>`;
    const value = url ? `<a class="agent-service-session-link" href="${attr(url)}" data-detail-control="${attr(`open:${label}:${id}`)}" aria-label="${attr(`查看 ${label} ${id.slice(0, 8)}`)}">${code}</a>` : code;
    return `<span class="agent-service-id-value">${value}
      <button type="button" class="console-inline-id-copy" data-task-copy="${attr(id)}" data-copy-label="${attr(label)}" data-detail-control="${attr(`copy:${label}:${id}`)}" title="复制 ${attr(label)}" aria-label="复制 ${attr(label)}">${icon("copy")}</button></span>`;
  }

  function taskDetailFacts(row) {
    const callerName = row.caller_username ? `<strong class="agent-service-fact-name" title="${attr(row.caller_username)}">${escapeHtml(row.caller_username)}</strong>` : "";
    const relatedIds = (label, ids) => ids.length
      ? `<span class="agent-service-related-ids">${label} ${ids.join(" ")}</span>` : "";
    const sessionUrl = row.session_id ? `#/diagnostics/sessions/${encodeURIComponent(row.session_id)}?view=conversation` : "";
    const turns = (row.turns || []).filter((turn) => turn.id).map((turn) =>
      taskIdValue(turn.id, "提问 ID", turn.event_id && sessionUrl ? `${sessionUrl}&event_id=${encodeURIComponent(turn.event_id)}` : ""),
    );
    const sessions = row.session_id ? [taskIdValue(row.session_id, "会话 ID", sessionUrl)] : [];
    const appName = row.app_name || (row.app_id ? "名称未获取" : "未关联应用");
    const spaceName = row.space_name || (row.space_id ? "名称未获取" : "未关联空间");
    const method = Object.hasOwn(methods, row.source) ? row.source : "a2a";
    const scope = `<span class="inspector-scope-value">
      <span class="inspector-scope-names"><span title="${attr(appName)}">${escapeHtml(appName)}</span><span>/</span><span title="${attr(spaceName)}">${escapeHtml(spaceName)}</span></span>
      <span class="inspector-scope-codes"><code>${escapeHtml(row.app_id || "—")}</code><span>/</span><code>${escapeHtml(row.space_id || "—")}</code></span>
    </span>`;
    return `<section class="inspector-section agent-service-task-facts">
      <dl class="agent-service-facts">
        <div><dt>调用方</dt><dd>${callerName}${taskIdValue(row.caller, "User ID")}</dd></div>
        <div><dt>接入方式</dt><dd><span class="agent-service-access-method" data-service-access="${method}">${methodIcon(method)}${methods[method]}</span></dd></div>
        <div><dt>应用 / 空间</dt><dd>${scope}</dd></div>
        <div><dt>提交时间</dt><dd>${escapeHtml(formatDate(row.created_at))}${Number.isFinite(row.duration_seconds) ? `<span class="agent-service-fact-meta">耗时 ${duration(row)}</span>` : ""}</dd></div>
      </dl>
    </section>
    <section class="inspector-section agent-service-task-facts">
      <h4 class="channel-extension-heading">任务与会话</h4>
      <dl class="agent-service-facts">
        <div><dt>Task ID</dt><dd class="agent-service-id-pair">${taskIdValue(row.id, "Task ID")}${relatedIds("提问 ID", turns)}</dd></div>
        <div><dt>Context ID</dt><dd class="agent-service-id-pair">${taskIdValue(row.context_id, "Context ID")}${relatedIds("会话 ID", sessions)}</dd></div>
        <div><dt>会话名称</dt><dd>${escapeHtml(row.session_title || "—")}</dd></div>
      </dl>
    </section>`;
  }

  async function loadDetail() {
    detailRequest?.abort();
    if (!selectedTask) return;
    const request = detailRequest = new AbortController();
    const target = el("agentTaskDetail");
    if (shownTask !== selectedTask) {
      shownTask = selectedTask;
      renderDetailPlaceholder("正在加载任务详情…");
    }
    try {
      const row = await apiFetch(`${root}/publications/${selected}/tasks/${selectedTask}`, { signal: request.signal, reportStatus: false });
      if (request.signal.aborted) return;
      const snapshot = JSON.stringify(row);
      if (taskDetailSnapshot === snapshot) return;
      const focusedControl = target.contains(document.activeElement) ? document.activeElement.dataset.detailControl : "";
      const previousScroll = target.querySelector(".inspector-tab-panel")?.scrollTop || 0;
      target.innerHTML = `<div class="agent-service-detail-heading">
        <div class="agent-service-detail-title"><h3 title="${attr(row.title)}">${escapeHtml(row.title)}</h3>
          <div class="session-subtitle-row"><code class="session-badge" title="${attr(row.id)}">${escapeHtml(row.id.slice(0, 8))}</code>
          <button type="button" class="console-inline-id-copy" data-task-copy="${attr(row.id)}" data-copy-label="Task ID" data-detail-control="copy:task" aria-label="复制 Task ID" title="复制 Task ID">${icon("copy")}</button></div></div>
        <span class="status-badge inspector-tab-status agent-service-detail-status" data-state="${attr(row.status)}">${statusLabel(row.status)}</span></div>
        <div class="inspector-tab-panel">
          ${taskDetailFacts(row)}
          ${row.progress?.length ? `<section class="inspector-section"><h4 class="channel-extension-heading">任务进度</h4><ol class="agent-service-progress">${row.progress.map((step) =>
            `<li><span>${statusLabel(step.status)}</span><time>${escapeHtml(formatDate(step.timestamp))}</time></li>`).join("")}</ol></section>` : ""}
          ${row.message.length ? `<section class="inspector-section"><header><strong>${statusLabel(row.status)}</strong></header><p>${escapeHtml(row.message.join("\n"))}</p></section>` : ""}
          ${row.files.length ? `<section class="inspector-section"><header><strong>交付产物</strong></header><ul>${row.files.map((file) => `<li>${escapeHtml(file.name)}</li>`).join("")}</ul></section>` : ""}
        </div>`;
      taskDetailSnapshot = snapshot;
      target.querySelector(".inspector-tab-panel").scrollTop = previousScroll;
      if (focusedControl) {
        const control = Array.from(target.querySelectorAll("[data-detail-control]"))
          .find((item) => item.dataset.detailControl === focusedControl);
        control?.focus({ preventScroll: true });
      }
      refreshIcons();
    } catch (error) {
      if (!request.signal.aborted) renderDetailPlaceholder(error.message);
    } finally {
      if (detailRequest === request) detailRequest = null;
    }
  }

  function renderScope() {
    const all = draft.mode === "all";
    el("agentPublicationAppScopeToggle").querySelectorAll("[data-service-app-scope]").forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.serviceAppScope === draft.mode));
    });
    el("agentPublicationAllAppsHint").hidden = !all;
    el("agentPublicationAppControls").hidden = all;
    el("agentPublicationAppTags").innerHTML = (all ? [] : draft.items).map((item, index) => {
      const name = apps.get(item.app_id)?.app_name;
      const hasName = name && name !== item.app_id;
      const label = hasName ? `${name} · ${item.app_id}` : item.app_id;
      return `<span class="agent-service-app-tag">
        <span class="agent-service-app-tag-value" title="${attr(label)}">
          ${hasName ? `<span class="agent-service-app-tag-name">${escapeHtml(name)}</span><span aria-hidden="true">·</span>` : ""}
          <span class="agent-service-app-tag-id">${escapeHtml(item.app_id)}</span>
        </span>
        <button type="button" class="icon-btn" data-remove-app="${index}" aria-label="移除 ${attr(label)}">${icon("x")}</button>
      </span>`;
    }).join("");
    refreshIcons();
  }

  function renderPublicationExample(useToken = false) {
    const toggle = el("agentPublicationExampleToggle");
    const mode = useToken ? "平台 Token" : "API Key";
    toggle.dataset.mode = useToken ? "token" : "api-key";
    toggle.setAttribute("aria-label", `当前示例为 ${mode}，切换为${useToken ? " API Key" : "平台 Token"}`);
    clearText("agentPublicationExampleMode", mode);
    clearText("agentPublicationExampleCode", a2aHeaderExample({ useToken }));
  }

  function openEditor(row = null) {
    const form = el("agentPublicationForm");
    form.reset();
    const scope = row?.scope.apps;
    draft = { row, mode: scope?.mode || "all", items: [...(scope?.items || [])] };
    clearText("agentPublicationTitle", row ? "编辑发布" : "发布 Agent");
    form.elements.agent_code.value = row?.agent_code || "";
    form.elements.agent_code.readOnly = Boolean(row);
    form.elements.name.value = row?.name || "";
    form.elements.description.value = row?.description || "";
    const picker = form.elements.agent_id;
    picker.replaceChildren(new Option("请选择", ""));
    agents.forEach((agent) => picker.add(new Option(agent.name, agent.id)));
    picker.value = row?.agent_id || "";
    el("agentPublicationError").hidden = true;
    renderPublicationExample();
    renderScope();
    el("agentPublicationDialog").showModal();
    form.elements[row ? "name" : "agent_code"].focus();
  }

  async function save(event) {
    event.preventDefault();
    if (!draft) return;
    const form = event.currentTarget;
    const submit = form.querySelector('[type="submit"]');
    const values = Object.fromEntries(new FormData(form));
    const { mode } = draft;
    const items = mode === "selected" ? draft.items : [];
    if (mode === "selected" && !items.length) {
      const error = el("agentPublicationError");
      error.textContent = "请至少选择一个应用。";
      error.hidden = false;
      error.scrollIntoView({ block: "nearest" });
      return;
    }
    const scope = { apps: { mode, items } };
    const row = draft.row;
    const body = { ...values, scope };
    if (row) { delete body.agent_code; body.revision = row.revision; }
    submit.disabled = true;
    el("agentPublicationError").hidden = true;
    try {
      const saved = await apiFetch(`${root}/publications${row ? `/${row.agent_code}` : ""}`, {
        method: row ? "PATCH" : "POST", body: JSON.stringify(body),
      });
      el("agentPublicationDialog").close();
      selectPublication(saved.agent_code);
      await refresh();
      showToast("发布配置已保存");
    } catch (error) {
      clearText("agentPublicationError", error.message);
      el("agentPublicationError").hidden = false;
    } finally {
      submit.disabled = false;
    }
  }

  async function publicationAction(event) {
    const tr = event.target.closest("[data-publication]");
    const row = rows.find((item) => item.agent_code === tr?.dataset.publication);
    if (!row) return;
    const button = event.target.closest("[data-service-action]");
    const action = button?.dataset.serviceAction;
    if (action === "edit") { openEditor(row); return; }
    if (action === "delete" && !await confirmAction({
      title: "删除发布？", message: `将删除「${row.name}」并关闭它的接入地址。`, tone: "danger", icon: "trash-2", confirmLabel: "删除",
    })) return;
    if (button) {
      button.disabled = true;
      try {
        if (action === "toggle") await apiFetch(`${root}/publications/${row.agent_code}`, {
          method: "PATCH", body: JSON.stringify({ enabled: !row.enabled, revision: row.revision }),
        });
        if (action === "delete") await apiFetch(`${root}/publications/${row.agent_code}?revision=${row.revision}`, { method: "DELETE" });
        await refresh();
      } catch (error) { showToast(error.message, true); }
      finally { button.disabled = false; }
    } else if (selected !== row.agent_code) {
      selectPublication(row.agent_code);
      renderPublications();
      await loadTasks();
    }
  }

  function bindTabs(tabList, activate) {
    const tabs = [...tabList.querySelectorAll('[role="tab"]')];
    tabs.forEach((tab) => tab.addEventListener("click", () => activate(tab)));
    tabList.addEventListener("keydown", (event) => {
      const index = tabs.indexOf(event.target);
      const next = { Home: 0, End: tabs.length - 1, ArrowLeft: (index + tabs.length - 1) % tabs.length, ArrowRight: (index + 1) % tabs.length }[event.key];
      if (index < 0 || next == null) return;
      event.preventDefault();
      tabs[next].click();
      tabs[next].focus();
    });
  }

  window.agentServiceAdmin = { refresh };
  document.addEventListener("DOMContentLoaded", () => {
    const panel = el("panel-agent-service");
    if (!panel || panel.dataset.bound) return;
    panel.dataset.bound = "true";
    el("newAgentPublication").addEventListener("click", () => openEditor());
    el("agentPublicationRows").addEventListener("click", (event) => void publicationAction(event));
    el("agentPublicationForm").addEventListener("submit", save);
    el("agentPublicationExampleToggle").addEventListener("click", (event) => {
      renderPublicationExample(event.currentTarget.dataset.mode !== "token");
    });
    const guide = el("agentServiceProtocol");
    const guideTabs = el("agentServiceGuideTabs");
    bindTabs(guideTabs, (selectedTab) => {
      guideTabs.querySelectorAll('[role="tab"]').forEach((tab) => {
        const active = tab === selectedTab;
        tab.classList.toggle("active", active);
        tab.setAttribute("aria-selected", String(active));
        tab.tabIndex = active ? 0 : -1;
        const tabPanel = el(tab.getAttribute("aria-controls"));
        tabPanel.hidden = !active;
        if (active) tabPanel.scrollTop = 0;
      });
    });
    el("agentServiceProtocolBtn").addEventListener("click", () => {
      const overview = el("agentServiceOverviewTab");
      renderMcpAccess(el("agentServiceMcpGuideSetup"), true);
      refreshIcons();
      guide.showModal();
      overview.click();
      overview.focus();
    });
    panel.querySelectorAll("[data-service-close]").forEach((button) => button.addEventListener("click", () => button.closest("dialog").close()));
    bindTabs(el("agentServiceTabs"), (tab) => {
      if (access === tab.dataset.serviceAccess) return;
      access = tab.dataset.serviceAccess;
      renderAccess();
      el("agentServiceAccess").scrollTop = 0;
    });
    panel.addEventListener("click", (event) => {
      const copyTarget = event.target.closest("[data-client-copy]");
      if (copyTarget) void copy(copyTarget.dataset.clientCopy);
    });
    el("agentServiceAccess").addEventListener("click", (event) => {
      const toggle = event.target.closest("[data-a2a-card-mode]");
      if (!toggle || toggle.getAttribute("aria-pressed") === "true") return;
      a2aCardMode = toggle.dataset.a2aCardMode;
      renderAccess();
      el("agentServiceAccess").querySelector('[data-a2a-card-mode][aria-pressed="true"]')?.focus();
    });
    panel.querySelectorAll("[data-service-app-scope]").forEach((button) => button.addEventListener("click", () => {
      draft.mode = button.dataset.serviceAppScope;
      el("agentPublicationError").hidden = true;
      renderScope();
    }));
    el("addAgentPublicationApp").addEventListener("click", () => window.DeepAgentAppPicker.open({
      owner: "agent-service", path: `${root}/applications`, title: "添加应用",
      onSelect(app) {
        if (draft.items.some((item) => item.app_id === app.app_id)) return;
        apps.set(app.app_id, app);
        draft.items.push({ app_id: app.app_id, space_id: app.space_id });
        el("agentPublicationError").hidden = true;
        renderScope();
      },
    }));
    el("agentPublicationAppTags").addEventListener("click", (event) => {
      const button = event.target.closest("[data-remove-app]");
      if (!button) return;
      const index = Number(button.dataset.removeApp);
      draft.items.splice(index, 1);
      renderScope();
      const next = el("agentPublicationAppTags").querySelector(`[data-remove-app="${index}"]`) || el("addAgentPublicationApp");
      next.focus();
    });
    el("agentTaskRows").addEventListener("click", (event) => {
      const tr = event.target.closest("[data-agent-task]");
      if (!tr || selectedTask === tr.dataset.agentTask) return;
      selectedTask = tr.dataset.agentTask;
      for (const item of tr.parentElement.children) {
        item.classList.toggle("selected", item === tr);
        item.querySelector("button").setAttribute("aria-pressed", String(item === tr));
      }
      taskRowsMarkup = "";
      void loadDetail();
    });
    el("agentTaskDetail").addEventListener("click", async (event) => {
      const button = event.target.closest("[data-task-copy]");
      if (!button || button.disabled) return;
      const label = button.dataset.copyLabel;
      button.disabled = true;
      try {
        await navigator.clipboard.writeText(button.dataset.taskCopy);
        button.classList.add("copied");
        button.innerHTML = icon("check");
        button.title = `${label} 已复制`;
        button.setAttribute("aria-label", button.title);
        refreshIcons();
        showToast(button.title);
        setTimeout(() => {
          if (!button.isConnected) return;
          button.classList.remove("copied");
          button.innerHTML = icon("copy");
          button.title = `复制 ${label}`;
          button.setAttribute("aria-label", button.title);
          button.disabled = false;
          refreshIcons();
        }, 1500);
      } catch {
        button.disabled = false;
        showToast("复制失败，请手动选择", true);
      }
    });
    for (const id of ["agentTaskState", "agentTaskApp"]) {
      el(id).addEventListener("change", () => { page = 1; void loadTasks(); });
    }
    el("agentTaskSearch").addEventListener("input", () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => { page = 1; void loadTasks(); }, 160);
    });
    el("prevAgentTasks").addEventListener("click", () => { page--; void loadTasks(); });
    el("nextAgentTasks").addEventListener("click", () => { page++; void loadTasks(); });
    const topPane = el("agentServiceTop");
    window.bindConsolePaneResize(el("agentServiceRowSplitter"), topPane.parentElement, "--agent-service-top-height",
      { pane: topPane, sizePrefix: "--agent-service" });
    window.bindConsolePaneResize(el("agentServiceTopSplitter"), topPane, "--agent-publications-width");
    window.bindConsolePaneResize(el("agentTaskSplitter"), el("agentTaskWorkbench"), "--task-list-width");
    clearInterval(timer);
    timer = setInterval(() => {
      if (!taskRequest && !detailRequest && panel.classList.contains("active")
        && document.body.dataset.consolePage === "configuration" && !document.hidden) void loadTasks();
    }, 5000);
  });
})();
