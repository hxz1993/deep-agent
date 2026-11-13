(() => {
  const root = "/api/v1/agent-service";
  const el = (id) => document.getElementById(id);
  const labels = {
    SUBMITTED: "排队中", WORKING: "进行中", COMPLETED: "已完成", FAILED: "失败",
    CANCELED: "已取消", REJECTED: "已拒绝", INPUT_REQUIRED: "待补材料", AUTH_REQUIRED: "待鉴权",
  };
  let rows = [], agents = [], apps = new Map(), tasks = [];
  let selected = "", selectedTask = "", access = "a2a", page = 1;
  let draft, detailRequest, taskRequest, timer, searchTimer;
  let taskRowsMarkup = "", taskDetailSnapshot = "", shownTask = "";
  const relativeTime = new Intl.RelativeTimeFormat("zh-CN", { numeric: "auto" });
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

  function publicationUpdate(row) {
    const date = window.DeepAgentTime.parseServerTimestamp(row.updated_at);
    const elapsed = date ? Math.max(0, (Date.now() - date.getTime()) / 1000) : 0;
    const time = !date ? "—" : elapsed < 60 ? "刚刚" : elapsed < 3600
      ? relativeTime.format(-Math.floor(elapsed / 60), "minute")
      : elapsed < 86400 ? relativeTime.format(-Math.floor(elapsed / 3600), "hour")
      : relativeTime.format(-Math.floor(elapsed / 86400), "day");
    const operator = row.updated_by_user?.username || row.updated_by;
    return `<div class="agent-service-update">${operator ? `<span title="更新人：${attr(operator)}">${escapeHtml(operator)}</span>` : ""}
      ${date ? `<time datetime="${date.toISOString()}" title="${attr(formatDate(row.updated_at))}">${escapeHtml(time)}</time>` : '<span>—</span>'}</div>`;
  }

  function renderPublications(message = "") {
    const body = el("agentPublicationRows");
    body.innerHTML = rows.length ? rows.map((row) => {
      const name = escapeHtml(row.name);
      const target = agents.find((item) => item.id === row.agent_id)?.name || row.agent_id;
      const scope = row.scope.apps;
      const names = scope.items.map((item) => apps.get(item.app_id)?.app_name || item.app_id).join("、");
      const scopeText = { all: "全部应用", fixed: `固定 · ${names}`, selected: names }[scope.mode];
      return `<tr data-publication="${attr(row.code)}" class="${selected === row.code ? "selected" : ""}">
        <td><div class="agent-service-pub-name"><button type="button" class="model-enabled-switch ${row.enabled ? "on" : "off"}" role="switch" aria-checked="${row.enabled}" aria-label="${attr(row.name)}启用状态" data-service-action="toggle"><span></span></button>
        <button type="button" class="btn text agent-service-select" aria-pressed="${selected === row.code}"><span title="${attr(row.name)}">${name}</span><code title="${attr(row.code)}">${escapeHtml(row.code)}</code></button></div></td>
        <td title="${attr(target)}">${escapeHtml(target)}</td><td title="${attr(scopeText)}">${escapeHtml(scopeText)}</td>
        <td>${publicationUpdate(row)}</td>
        <td><div class="row-actions agent-row-actions"><button type="button" class="row-action icon-only" data-service-action="edit" aria-label="编辑${attr(row.name)}" title="编辑">${icon("pencil")}</button>
        <button type="button" class="row-action icon-only danger" data-service-action="delete" aria-label="删除${attr(row.name)}" title="删除">${icon("trash-2")}</button></div></td></tr>`;
    }).join("") : `<tr><td colspan="5" class="empty-cell">${escapeHtml(message || "暂无发布")}</td></tr>`;
    renderAccess();
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
    const row = rows.find((item) => item.code === selected);
    if (access !== "a2a") card.textContent = "接入方式待定。";
    else if (!row) card.textContent = "请选择一项发布";
    else if (!row.card_url) {
      card.innerHTML = `<h4>${escapeHtml(row.name)}</h4><p>${row.enabled ? "尚未配置接入地址。" : "该发布已停用。"}</p>`;
    } else {
      const instruction = row.scope.apps.mode === "fixed"
        ? "在 A2A 客户端填写下方地址及平台 Token 或 API Key。"
        : "在 A2A 客户端填写下方地址及凭据，并通过 app Header 指定应用。";
      card.innerHTML = `<h4>${escapeHtml(row.name)}</h4><p class="agent-service-access-subtitle">${instruction}</p>
        <div class="agent-service-card-heading"><span id="agentServiceCardUrlLabel">Agent Card URL</span><button type="button" class="btn text compact" aria-label="复制 Agent Card 地址">复制地址</button></div>
        <div class="agent-service-card-url"><code aria-labelledby="agentServiceCardUrlLabel" title="${attr(row.card_url)}">${escapeHtml(row.card_url)}</code></div>`;
      card.querySelector("button").addEventListener("click", () => void copy(row.card_url));
    }
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
    try {
      const [publications, agentList] = await Promise.all([
        apiFetch(`${root}/publications`),
        apiFetch("/api/v1/agents").catch(() => []),
      ]);
      rows = publications;
      agents = agentList;
      el("newAgentPublication").disabled = false;
      if (!rows.some((row) => row.code === selected)) {
        selectPublication(rows[0]?.code || "");
      }
      renderPublications();
      await loadTasks();
    } catch (error) {
      rows = [];
      tasks = [];
      selectedTask = "";
      el("newAgentPublication").disabled = true;
      renderPublications(error.message);
      renderTasks(0);
    }
  }

  async function loadTasks() {
    taskRequest?.abort();
    const request = taskRequest = new AbortController();
    if (!selected) { tasks = []; renderTasks(0); return; }
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
      if (!tasks.some((row) => row.id === selectedTask)) selectedTask = tasks[0]?.id || "";
      renderTasks(result.total);
      void loadDetail();
    } catch (error) {
      if (request.signal.aborted) return;
      tasks = [];
      renderTasks(0, error.message);
    }
  }

  function renderTasks(total, message = "") {
    const body = el("agentTaskRows");
    const filtering = el("agentTaskSearch").value.trim() || el("agentTaskState").value || el("agentTaskApp").value;
    const emptyLabel = message || (filtering ? "没有符合条件的任务" : "暂无任务");
    const markup = tasks.length ? tasks.map((row) => `
      <tr data-agent-task="${attr(row.id)}" class="${row.id === selectedTask ? "selected" : ""}">
        <td><button type="button" class="btn text agent-service-select" aria-controls="agentTaskDetail" aria-pressed="${row.id === selectedTask}"><span title="${attr(row.title)}">${escapeHtml(row.title)}</span><code title="${attr(row.id)}">${escapeHtml(row.id.slice(0, 8))}</code></button></td>
        <td><span class="agent-service-status" data-state="${attr(row.status)}">${statusLabel(row.status)}</span></td><td>A2A</td>
        <td class="agent-task-wide" title="${attr(row.caller)}">${escapeHtml(row.caller)}</td>
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
    const pageCount = Math.ceil(total / 20);
    const previous = el("prevAgentTasks");
    const next = el("nextAgentTasks");
    previous.hidden = next.hidden = pageCount <= 1;
    previous.disabled = page <= 1;
    next.disabled = page >= pageCount;
    const totalLabel = `共 ${total.toLocaleString()} 条任务`;
    const pageLabel = pageCount > 1 ? `第 ${page} / ${pageCount} 页 · ${totalLabel}` : totalLabel;
    if (el("agentTaskPage").textContent !== pageLabel) clearText("agentTaskPage", pageLabel);
    if (!tasks.length) {
      detailRequest?.abort();
      shownTask = "";
      renderDetailPlaceholder("暂无任务可查看");
    }
  }

  function renderDetailPlaceholder(message) {
    taskDetailSnapshot = "";
    el("agentTaskDetail").innerHTML = `<div class="inspector-tabs agent-service-detail-heading"><h3>任务详情</h3></div>
      <p class="agent-service-empty" role="status">${escapeHtml(message)}</p>`;
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
      const hadCopyFocus = target.contains(document.activeElement);
      const previousScroll = target.querySelector(".inspector-tab-panel")?.scrollTop || 0;
      const facts = [
        ["接入方式", "A2A"], ["调用方", row.caller], ["应用 / 空间", `${row.app_id} / ${row.space_id}`],
        ["提交时间", formatDate(row.created_at)], ["耗时", duration(row)],
        ["Context ID", row.context_id],
      ];
      target.innerHTML = `<div class="inspector-tabs agent-service-detail-heading">
        <div class="agent-service-detail-title"><h3 title="${attr(row.title)}">${escapeHtml(row.title)}</h3>
          <div class="session-subtitle-row"><code class="session-badge" title="${attr(row.id)}">${escapeHtml(row.id.slice(0, 8))}</code>
          <button type="button" class="session-copy-btn" aria-label="复制任务 ID" title="复制任务 ID">${icon("copy")}</button></div></div>
        <span class="status-badge inspector-tab-status agent-service-detail-status" data-state="${attr(row.status)}">${statusLabel(row.status)}</span></div>
        <div class="inspector-tab-panel">
          <section class="inspector-section"><div class="inspector-facts">${facts.map(([name, value]) =>
            `<div><span>${name}</span><strong>${escapeHtml(value || "—")}</strong></div>`).join("")}</div></section>
          ${row.progress?.length ? `<section class="inspector-section"><header><strong>任务进度</strong></header><ol class="agent-service-progress">${row.progress.map((step) =>
            `<li><span>${statusLabel(step.status)}</span><time>${escapeHtml(formatDate(step.timestamp))}</time></li>`).join("")}</ol></section>` : ""}
          ${row.message.length ? `<section class="inspector-section"><header><strong>${statusLabel(row.status)}</strong></header><p>${escapeHtml(row.message.join("\n"))}</p></section>` : ""}
          ${row.files.length ? `<section class="inspector-section"><header><strong>交付产物</strong></header><ul>${row.files.map((file) => `<li>${escapeHtml(file.name)}</li>`).join("")}</ul></section>` : ""}
        </div>`;
      taskDetailSnapshot = snapshot;
      target.querySelector("button").addEventListener("click", () => void copy(row.id));
      target.querySelector(".inspector-tab-panel").scrollTop = previousScroll;
      if (hadCopyFocus) target.querySelector("button").focus({ preventScroll: true });
      refreshIcons();
    } catch (error) {
      if (!request.signal.aborted) renderDetailPlaceholder(error.message);
    }
  }

  function renderScope() {
    const fixed = draft.source === "fixed";
    const all = draft.callerScope === "all";
    const fixedApp = draft.fixedApp;
    const app = fixedApp && (apps.get(fixedApp.app_id) || fixedApp);
    document.querySelectorAll("[data-service-app-source]").forEach((button) =>
      button.setAttribute("aria-pressed", String(button.dataset.serviceAppSource === draft.source)));
    el("pickAgentPublicationFixedApp").hidden = !fixed;
    const appValue = el("agentPublicationFixedAppValue");
    appValue.classList.toggle("da-select-placeholder", !app);
    appValue.textContent = app ? [...new Set([app.app_name, app.app_id])].filter(Boolean).join(" · ") : "选择应用";
    const space = el("agentPublicationFixedAppSpace");
    space.textContent = app?.space_name || app?.space_id || "";
    space.hidden = !space.textContent;
    const appHeader = {
      fixed: "无需发送 <code>app</code> 和 <code>space</code>，由发布配置确定。",
      all: "发送 <code>app: &lt;App ID&gt;</code>，调用方须有该应用的访问权限。<code>space</code> 由服务端确定。",
      selected: "发送 <code>app: &lt;App ID&gt;</code>，应用须在允许范围内。<code>space</code> 由服务端确定。",
    };
    el("agentPublicationAuthContext").innerHTML = appHeader[fixed ? "fixed" : draft.callerScope];
    el("agentPublicationAppControls").hidden = fixed;
    el("agentPublicationAllApps").hidden = !all;
    el("addAgentPublicationApp").hidden = all;
    const scopeToggle = el("toggleAgentPublicationAppScope");
    scopeToggle.title = all ? "切换为指定应用" : "切换为全部应用";
    scopeToggle.setAttribute("aria-label", scopeToggle.title);
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

  function openEditor(row = null) {
    const form = el("agentPublicationForm");
    form.reset();
    const scope = row?.scope.apps;
    const fixed = scope?.mode === "fixed";
    draft = {
      row, source: fixed ? "fixed" : "caller",
      callerScope: scope?.mode === "selected" ? "selected" : "all",
      fixedApp: fixed ? scope.items[0] : null, items: fixed ? [] : [...(scope?.items || [])],
    };
    clearText("agentPublicationTitle", row ? "编辑发布" : "发布 Agent");
    form.elements.code.value = row?.code || "";
    form.elements.code.readOnly = Boolean(row);
    form.elements.name.value = row?.name || "";
    form.elements.description.value = row?.description || "";
    const picker = form.elements.agent_id;
    picker.replaceChildren(new Option("请选择", ""));
    agents.forEach((agent) => picker.add(new Option(agent.name, agent.id)));
    picker.value = row?.agent_id || "";
    el("agentPublicationError").hidden = true;
    renderScope();
    el("agentPublicationDialog").showModal();
    form.elements[row ? "name" : "code"].focus();
    if (scope?.items.some((app) => !apps.has(app.app_id))) {
      const currentDraft = draft;
      void window.DeepAgentAppPicker.getApps(`${root}/applications`).then((directory) => {
        directory.forEach((app) => apps.set(app.app_id, app));
        if (draft === currentDraft && el("agentPublicationDialog").open) renderScope();
      }).catch(() => {});
    }
  }

  async function save(event) {
    event.preventDefault();
    if (!draft) return;
    const form = event.currentTarget;
    const submit = form.querySelector('[type="submit"]');
    const values = Object.fromEntries(new FormData(form));
    const fixed = draft.source === "fixed";
    const mode = fixed ? "fixed" : draft.callerScope;
    const items = { fixed: draft.fixedApp ? [draft.fixedApp] : [], selected: draft.items, all: [] }[mode];
    if (mode !== "all" && !items.length) {
      const error = el("agentPublicationError");
      error.textContent = fixed ? "请选择固定应用。" : "请至少选择一个应用。";
      error.hidden = false;
      error.scrollIntoView({ block: "nearest" });
      return;
    }
    const scope = { apps: { mode, items } };
    const row = draft.row;
    const body = { ...values, scope };
    if (row) { delete body.code; body.revision = row.revision; }
    submit.disabled = true;
    el("agentPublicationError").hidden = true;
    try {
      const saved = await apiFetch(`${root}/publications${row ? `/${row.code}` : ""}`, {
        method: row ? "PATCH" : "POST", body: JSON.stringify(body),
      });
      el("agentPublicationDialog").close();
      selectPublication(saved.code);
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
    const row = rows.find((item) => item.code === tr?.dataset.publication);
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
        if (action === "toggle") await apiFetch(`${root}/publications/${row.code}`, {
          method: "PATCH", body: JSON.stringify({ enabled: !row.enabled, revision: row.revision }),
        });
        if (action === "delete") await apiFetch(`${root}/publications/${row.code}?revision=${row.revision}`, { method: "DELETE" });
        await refresh();
      } catch (error) { showToast(error.message, true); }
      finally { button.disabled = false; }
    } else if (selected !== row.code) {
      selectPublication(row.code);
      renderPublications();
      await loadTasks();
    }
  }

  function bindResize(split, layout, property) {
    const minimum = Number(split.getAttribute("aria-valuemin"));
    const maximum = Number(split.getAttribute("aria-valuemax"));
    const initial = Number(split.getAttribute("aria-valuenow"));
    const apply = (value) => {
      const width = Math.round(Math.max(minimum, Math.min(maximum, value)));
      layout.style.setProperty(property, `${width}%`);
      split.setAttribute("aria-valuenow", width);
    };
    split.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const current = Number(split.getAttribute("aria-valuenow"));
      apply({ Home: minimum, End: maximum, ArrowLeft: current - 2, ArrowRight: current + 2 }[event.key]);
    });
    split.addEventListener("dblclick", () => apply(initial));
    split.addEventListener("pointerdown", (event) => {
      if (!event.isPrimary || event.button !== 0) return;
      event.preventDefault();
      const startWidth = Number(split.getAttribute("aria-valuenow"));
      const move = (pointer) => {
        if (pointer.pointerId === event.pointerId) {
          apply(startWidth + (pointer.clientX - event.clientX) * 100 / layout.clientWidth);
        }
      };
      split.setPointerCapture(event.pointerId);
      split.classList.add("dragging");
      document.body.classList.add("workspace-pane-resizing");
      split.addEventListener("pointermove", move);
      split.addEventListener("lostpointercapture", () => {
        split.removeEventListener("pointermove", move);
        split.classList.remove("dragging");
        document.body.classList.remove("workspace-pane-resizing");
      }, { once: true });
    });
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
    const guide = el("agentServiceProtocol");
    const guideTabs = el("agentServiceGuideTabs");
    bindTabs(guideTabs, (selectedTab) => {
      guideTabs.querySelectorAll('[role="tab"]').forEach((tab) => {
        const active = tab === selectedTab;
        tab.classList.toggle("active", active);
        tab.setAttribute("aria-selected", String(active));
        tab.tabIndex = active ? 0 : -1;
        el(tab.getAttribute("aria-controls")).hidden = !active;
      });
    });
    el("agentServiceProtocolBtn").addEventListener("click", () => {
      const methods = el("agentServiceMethodsTab");
      methods.click();
      guide.showModal();
      el("agentServiceMethodsPanel").scrollTop = 0;
      el("agentServiceTestPanel").scrollTop = 0;
      methods.focus();
    });
    panel.querySelectorAll("[data-service-close]").forEach((button) => button.addEventListener("click", () => button.closest("dialog").close()));
    bindTabs(el("agentServiceTabs"), (tab) => {
      access = tab.dataset.serviceAccess;
      renderAccess();
    });
    panel.querySelectorAll("[data-service-app-source]").forEach((button) => button.addEventListener("click", () => {
      draft.source = button.dataset.serviceAppSource;
      el("agentPublicationError").hidden = true;
      renderScope();
    }));
    el("toggleAgentPublicationAppScope").addEventListener("click", () => {
      draft.callerScope = draft.callerScope === "all" ? "selected" : "all";
      el("agentPublicationError").hidden = true;
      renderScope();
    });
    el("pickAgentPublicationFixedApp").addEventListener("click", () => window.DeepAgentAppPicker.open({
      owner: "agent-service", path: `${root}/applications`, title: "选择固定应用",
      selectedAppId: draft.fixedApp?.app_id,
      onSelect(app) {
        apps.set(app.app_id, app);
        draft.fixedApp = { app_id: app.app_id, space_id: app.space_id };
        el("agentPublicationError").hidden = true;
        renderScope();
      },
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
    for (const id of ["agentTaskState", "agentTaskApp"]) {
      el(id).addEventListener("change", () => { page = 1; void loadTasks(); });
    }
    el("agentTaskSearch").addEventListener("input", () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => { page = 1; void loadTasks(); }, 160);
    });
    el("prevAgentTasks").addEventListener("click", () => { page--; void loadTasks(); });
    el("nextAgentTasks").addEventListener("click", () => { page++; void loadTasks(); });
    bindResize(el("agentServiceTopSplitter"), el("agentServiceTop"), "--agent-publications-width");
    bindResize(el("agentTaskSplitter"), el("agentTaskWorkbench"), "--task-list-width");
    clearInterval(timer);
    timer = setInterval(() => {
      if (panel.classList.contains("active") && document.body.dataset.consolePage === "configuration" && !document.hidden) void loadTasks();
    }, 5000);
  });
})();
