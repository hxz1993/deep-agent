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
    const operator = row.updated_by_user?.username || row.updated_by || "—";
    const time = formatDate(row.updated_at);
    return `<div class="agent-update-cell">
      <span class="agent-updater" title="更新人：${attr(operator)}">${icon("user-round")}<span class="agent-updater-name">${escapeHtml(operator)}</span></span>
      <span class="agent-update-time" title="${attr(time)}">${escapeHtml(time)}</span>
    </div>`;
  }

  function renderPublications(message = "") {
    const body = el("agentPublicationRows");
    body.innerHTML = rows.length ? rows.map((row) => {
      const name = escapeHtml(row.name);
      const target = agents.find((item) => item.id === row.agent_id)?.name || row.agent_id;
      const scope = row.scope.apps;
      const scopeText = scope.mode === "all" ? "全部应用" :
        scope.items.map((item) => apps.get(item.app_id)?.app_name || item.app_id).join("、");
      return `<tr data-publication="${attr(row.agent_code)}" class="${selected === row.agent_code ? "selected" : ""}">
        <td><div class="agent-service-pub-name"><button type="button" class="model-enabled-switch ${row.enabled ? "on" : "off"}" role="switch" aria-checked="${row.enabled}" aria-label="${attr(row.name)}启用状态" data-service-action="toggle"><span></span></button>
        <button type="button" class="btn text agent-service-select" aria-pressed="${selected === row.agent_code}"><span title="${attr(row.name)}">${name}</span><code title="${attr(row.agent_code)}">${escapeHtml(row.agent_code)}</code></button></div></td>
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
    const row = rows.find((item) => item.agent_code === selected);
    const publication = row ? `<h4 class="agent-service-current-publication">${icon("bot")}<span>当前发布</span><strong>${escapeHtml(row.name)}</strong></h4>` : "";
    if (access !== "a2a") card.textContent = "接入方式待定。";
    else if (!row) card.textContent = "请选择一项发布";
    else if (!row.card_url) {
      card.innerHTML = `${publication}<p>${row.enabled ? "尚未配置接入地址。" : "该发布已停用。"}</p>`;
    } else {
      const cards = [
        { label: "通用 Agent Card", url: row.card_url, help: "在请求 Header 的 <code>app</code> 中填写应用 ID，并传凭据。" },
        { label: "带应用的 Agent Card", url: row.app_card_url_template, help: "将地址中的 <code>{app_id}</code> 替换为应用 ID，请求只需传凭据。" },
      ];
      card.innerHTML = `${publication}
        <div class="agent-service-card-options">${cards.map(({ label, url, help }, index) => `
          <section class="agent-service-card-option" aria-labelledby="agentServiceCardLabel${index}">
            <div class="agent-service-card-heading"><h5 id="agentServiceCardLabel${index}">${label}</h5>
              <button type="button" class="btn text compact" aria-label="复制${label}地址" data-card-copy="${index}">复制地址</button></div>
            <p class="agent-service-access-help">${help}</p>
            <div class="agent-service-card-url"><code title="${attr(url)}">${escapeHtml(url)}</code></div>
          </section>`).join("")}</div>`;
      card.querySelectorAll("[data-card-copy]").forEach((button) => {
        button.addEventListener("click", () => void copy(cards[Number(button.dataset.cardCopy)].url));
      });
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
      if (!rows.some((row) => row.agent_code === selected)) {
        selectPublication(rows[0]?.agent_code || "");
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

  function taskIdValue(id, label, url = "") {
    if (!id) return "—";
    const code = `<code title="${attr(id)}">${escapeHtml(id.slice(0, 8))}</code>`;
    const value = url ? `<a class="agent-service-session-link" href="${attr(url)}" data-detail-control="${attr(`open:${label}:${id}`)}" aria-label="${attr(`查看 ${label} ${id.slice(0, 8)}`)}">${code}</a>` : code;
    return `<span class="agent-service-id-value">${value}
      <button type="button" class="inspector-copy-btn" data-task-copy="${attr(id)}" data-detail-control="${attr(`copy:${label}:${id}`)}" title="复制 ${attr(label)}" aria-label="复制 ${attr(label)}">${icon("copy")}</button></span>`;
  }

  function taskDetailFacts(row) {
    const named = (name, id, label, url) => {
      const title = name ? `<strong class="agent-service-fact-name" title="${attr(name)}">${escapeHtml(name)}</strong>` : "";
      const separator = title && id && url ? '<span class="agent-service-fact-meta" aria-hidden="true">·</span>' : "";
      return `${title}${separator}${taskIdValue(id, label, url)}`;
    };
    const sessionUrl = row.session_id ? `#/diagnostics/sessions/${encodeURIComponent(row.session_id)}?view=conversation` : "";
    const turns = (row.turns || []).map((turn) =>
      taskIdValue(turn.id, "Turn ID", turn.event_id && sessionUrl ? `${sessionUrl}&event_id=${encodeURIComponent(turn.event_id)}` : ""),
    );
    const appName = row.app_name || (row.app_id ? "名称未获取" : "未关联应用");
    const spaceName = row.space_name || (row.space_id ? "名称未获取" : "未关联空间");
    const scope = `<span class="inspector-scope-value">
      <span class="inspector-scope-names"><span title="${attr(appName)}">${escapeHtml(appName)}</span><span>/</span><span title="${attr(spaceName)}">${escapeHtml(spaceName)}</span></span>
      <span class="inspector-scope-codes"><code>${escapeHtml(row.app_id || "—")}</code><span>/</span><code>${escapeHtml(row.space_id || "—")}</code></span>
    </span>`;
    return `<section class="inspector-section agent-service-task-facts">
      <dl class="agent-service-facts">
        <div><dt>调用方</dt><dd>${named(row.caller_username, row.caller, "User ID")}</dd></div>
        <div><dt>接入方式</dt><dd><span class="agent-service-access-method" data-service-access="a2a">${icon("network")}A2A</span></dd></div>
        <div><dt>应用 / 空间</dt><dd>${scope}</dd></div>
        <div><dt>提交时间</dt><dd>${escapeHtml(formatDate(row.created_at))}${Number.isFinite(row.duration_seconds) ? `<span class="agent-service-fact-meta">耗时 ${duration(row)}</span>` : ""}</dd></div>
      </dl>
    </section>
    <section class="inspector-section agent-service-task-facts">
      <h4 class="channel-extension-heading">会话与轮次</h4>
      <dl class="agent-service-facts">
        <div><dt>提问</dt><dd>${turns.join("") || "—"}</dd></div>
        <div><dt>会话</dt><dd>${named(row.session_title, row.session_id, "会话 ID", sessionUrl)}</dd></div>
        <div><dt>Context ID</dt><dd>${taskIdValue(row.context_id, "Context ID")}</dd></div>
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
      target.innerHTML = `<div class="inspector-tabs agent-service-detail-heading">
        <div class="agent-service-detail-title"><h3 title="${attr(row.title)}">${escapeHtml(row.title)}</h3>
          <div class="session-subtitle-row"><code class="session-badge" title="${attr(row.id)}">${escapeHtml(row.id.slice(0, 8))}</code>
          <button type="button" class="session-copy-btn" data-task-copy="${attr(row.id)}" data-detail-control="copy:task" aria-label="复制任务 ID" title="复制任务 ID">${icon("copy")}</button></div></div>
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
    renderScope();
    el("agentPublicationDialog").showModal();
    form.elements[row ? "name" : "agent_code"].focus();
    if (draft.items.some((app) => !apps.has(app.app_id))) {
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
    el("agentTaskDetail").addEventListener("click", (event) => {
      const button = event.target.closest("[data-task-copy]");
      if (button) void copy(button.dataset.taskCopy);
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
