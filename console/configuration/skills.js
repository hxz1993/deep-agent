const skillsAdmin = {
  selectedCode: null,
  view: "list",
  listLoaded: false,
  listError: null,
  agentFilter: "all",
  query: "",
  agentReferences: [],
  referencesState: "loading",
  referencesError: "",
  referencesDirty: true,
  referenceIndex: new Map(),
  readRequests: new Map(),
  listScroll: 0,
  lastSkillCode: null,
  scopeKey: null,
  selectedResourcePath: null,
  details: new Map(),
  uploadMode: "install",
  moreMenuDismissController: null,
  installQueue: [],
  installing: false,

  renderInstallQueue() {
    const states = {
      queued: { label: "待安装", icon: "clock-3" },
      uploading: { label: "安装中", icon: "loader-circle" },
      done: { label: "已安装", icon: "circle-check" },
      error: { label: "未安装", icon: "circle-x" },
      unknown: { label: "待确认", icon: "circle-help" },
    };
    document.getElementById("skillInstallResults").innerHTML = this.installQueue.map((item) => {
      const state = states[item.status] || states.unknown;
      const detail = ["done", "error", "unknown"].includes(item.status)
        ? `<small class="skill-install-detail">${escapeHtml(item.message)}</small>` : "";
      return `<li class="skill-install-card" data-status="${item.status}">
        <span class="skill-install-file-icon" aria-hidden="true"><i data-lucide="file-archive"></i></span>
        <div class="skill-install-file"><strong title="${escapeHtml(item.file.name)}">${escapeHtml(item.file.name)}</strong><span class="skill-install-file-meta">${this.formatBytes(item.file.size)}</span>${detail}</div>
        <span class="skill-install-status"><i data-lucide="${state.icon}" aria-hidden="true"></i>${state.label}</span>
      </li>`;
    }).join("");
    window.lucide?.createIcons();
    for (const id of ["chooseSkillPackagesBtn", "closeSkillInstallBtn", "dismissSkillInstallBtn"]) {
      document.getElementById(id).disabled = this.installing;
    }
    document.getElementById("startSkillInstallBtn").disabled = this.installing || !this.installQueue.some((item) => item.status === "queued");
    document.getElementById("startSkillInstallBtn").textContent = this.installing ? "正在安装…" : "开始安装";
  },

  openInstallDialog() {
    if (this.installing) return;
    this.installQueue = [];
    document.getElementById("skillInstallFiles").value = "";
    document.getElementById("skillInstallMessage").textContent = "";
    this.renderInstallQueue();
    document.getElementById("skillInstallDialog").showModal();
  },

  selectInstallFiles(files) {
    if (this.installing) return;
    this.installQueue = Array.from(files || [], (file) => {
      const error = !/\.zip$/i.test(file.name) ? "请选择 ZIP 文件" : file.size > 10 * 1024 * 1024 ? "文件超过 10 MiB" : "";
      return { file, status: error ? "error" : "queued", message: error || "等待安装" };
    });
    document.getElementById("skillInstallMessage").textContent = `已选择 ${this.installQueue.length} 个包 · 各包独立安装，失败不影响已成功的包。`;
    this.renderInstallQueue();
  },

  async installSelected() {
    if (this.installing || !this.installQueue.some((item) => item.status === "queued")) return;
    this.installing = true;
    this.renderInstallQueue();
    let installed = 0;
    let shouldRefresh = false;
    try {
      for (const item of this.installQueue) {
        if (item.status !== "queued") continue;
        item.status = "uploading";
        item.message = "正在上传安装…";
        this.renderInstallQueue();
        const form = new FormData();
        form.append("file", item.file);
        let stop = false;
        let rejected = false;
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 120000);
        try {
          const response = await daFetch(serviceUrl("/api/v1/skills"), { method: "POST", body: form, signal: controller.signal });
          if (!response.ok) {
            // A gateway/server failure may happen after the package was committed.
            rejected = response.status >= 400 && response.status < 500 && response.status !== 408;
            let detail = `安装失败（HTTP ${response.status}）`;
            try {
              const body = await response.json();
              detail = window.AdminFormFeedback?.formatDetail(body.detail || body.message) || detail;
            } catch { /* Retain HTTP status when no JSON is available. */ }
            stop = response.status === 401 || response.status === 403;
            if (response.status === 409) detail = "编码已存在，未覆盖；请到对应 Skill 详情上传替换。";
            throw new Error(detail);
          }
          const result = await response.json();
          item.status = "done";
          item.message = `已安装：${result.skill.code}`;
          installed += 1;
          this.selectedCode = result.skill.code;
        } catch (error) {
          item.status = rejected ? "error" : "unknown";
          item.message = rejected ? error.message : "未确认安装结果，请刷新列表核实后再操作；不会自动重试。";
          if (controller.signal.aborted) stop = true;
        } finally {
          clearTimeout(timeout);
        }
        this.renderInstallQueue();
        if (stop) {
          for (const pending of this.installQueue) {
            if (pending.status === "queued") Object.assign(pending, { status: "error", message: controller.signal.aborted ? "前一包请求超时，未上传。" : "权限不可用，未上传。" });
          }
          break;
        }
      }
      const unknown = this.installQueue.filter((item) => item.status === "unknown").length;
      document.getElementById("skillInstallMessage").textContent = `安装结束：成功 ${installed} 个，未安装 ${this.installQueue.length - installed - unknown} 个${unknown ? `，结果待确认 ${unknown} 个` : ""}。`;
      shouldRefresh = Boolean(installed || unknown);
    } finally {
      this.installing = false;
      document.getElementById("skillInstallFiles").value = "";
      this.renderInstallQueue();
    }
    // Refreshing the background list must not keep a finished install modal locked.
    if (shouldRefresh) await this.refresh();
  },

  requestScope() {
    return JSON.stringify([window.defaultApiBaseUrl?.() || "", window.DeepAgentConsoleAPI?.loadContext?.()?.app || "", window.DeepAgentConsoleContext?.identity?.user || ""]);
  },

  startRead(kind) {
    this.cancelReads([kind]);
    const controller = new AbortController();
    const scope = this.requestScope();
    const request = { controller, scope };
    const timeout = setTimeout(() => controller.abort(), 15000);
    request.current = () => this.readRequests.get(kind) === request && scope === this.requestScope();
    request.finish = () => {
      clearTimeout(timeout);
      if (this.readRequests.get(kind) === request) this.readRequests.delete(kind);
    };
    this.readRequests.set(kind, request);
    return request;
  },

  cancelReads(kinds = [...this.readRequests.keys()]) {
    for (const kind of kinds) {
      const request = this.readRequests.get(kind);
      this.readRequests.delete(kind);
      request?.controller.abort();
      request?.finish();
    }
  },

  isActive() {
    return Boolean(document.querySelector('#page-configuration.active #panel-skills.active'));
  },

  setView(view) {
    this.view = view;
    document.getElementById("skillListView")?.classList.toggle("hidden", view !== "list");
    document.getElementById("skillPageToolbar")?.classList.toggle("hidden", view !== "list");
    document.getElementById("skillDetailPane")?.classList.toggle("hidden", view !== "detail");
    this.closeReferences();
    this.moreMenuDismissController?.abort();
    this.moreMenuDismissController = null;
  },

  deactivate() {
    this.cancelReads();
    this.selectedCode = null;
    this.selectedResourcePath = null;
    this.setView("list");
  },

  buildReferenceIndex() {
    const known = new Set(state.skills.map(skill => skill.code));
    this.referenceIndex = new Map();
    for (const agent of this.agentReferences) {
      for (const code of new Set(agent.skills)) {
        if (!known.has(code)) continue;
        if (!this.referenceIndex.has(code)) this.referenceIndex.set(code, []);
        const refs = this.referenceIndex.get(code);
        if (!refs.some(ref => ref.id === agent.id)) refs.push(agent);
      }
    }
  },

  filterableAgents() {
    const ids = new Set([...this.referenceIndex.values()].flat().map(agent => agent.id));
    return this.agentReferences.filter(agent => ids.has(agent.id));
  },

  visibleSkills() {
    const query = this.query.trim().toLocaleLowerCase();
    return state.skills.filter(skill => {
      if (this.referencesState === "ready") {
        const refs = this.referenceIndex.get(skill.code) || [];
        if (this.agentFilter === "unlinked" && refs.length) return false;
        if (!["all", "unlinked"].includes(this.agentFilter) && !refs.some(agent => agent.id === this.agentFilter)) return false;
      }
      return `${skill.label} ${skill.code} ${skill.description || ""}`.toLocaleLowerCase().includes(query);
    }).sort((left, right) => Number(left.source === "builtin") - Number(right.source === "builtin"));
  },

  async refreshReferences() {
    const request = this.startRead("references");
    this.referencesState = "loading";
    this.referencesError = "";
    if (this.view === "list" && this.listLoaded) this.renderList();
    try {
      const agents = await apiFetch("/api/v1/skills/references/agents", { signal: request.controller.signal });
      if (!request.current()) return;
      if (!Array.isArray(agents) || agents.some(agent => !agent.id || typeof agent.name !== "string" || !Array.isArray(agent.skills))) throw new Error("Agent 引用响应格式错误");
      this.agentReferences = agents;
      this.referencesState = "ready";
      this.referencesDirty = false;
      this.buildReferenceIndex();
      if (!["all", "unlinked"].includes(this.agentFilter) && !this.filterableAgents().some(agent => agent.id === this.agentFilter)) this.agentFilter = "all";
    } catch (error) {
      if (!request.current()) return;
      this.referencesState = "error";
      this.agentFilter = "all";
      this.referencesError = request.controller.signal.aborted ? "Agent 引用读取超时" : error.message;
      this.referencesDirty = true;
    } finally {
      if (request.current() && this.view === "list" && this.listLoaded) this.renderList();
      request.finish();
    }
  },

  async refresh() {
    const pane = document.getElementById("skillListPane");
    if (!pane) return;
    const scope = this.requestScope();
    if (this.scopeKey !== null && this.scopeKey !== scope) {
      this.deactivate();
      this.agentFilter = "all";
      this.query = "";
      this.details.clear();
      this.agentReferences = [];
      state.skills = [];
    }
    this.scopeKey = scope;
    const detailAtStart = this.view === "detail" ? this.selectedCode : null;
    const request = this.startRead("list");
    this.listScroll = pane.querySelector(".skill-table-wrap")?.scrollTop ?? this.listScroll;
    this.listLoaded = false;
    this.listError = null;
    this.renderList();
    const references = this.refreshReferences();
    try {
      const skills = await apiFetch("/api/v1/skills", { signal: request.controller.signal });
      if (!request.current()) return;
      if (!Array.isArray(skills)) throw new Error("Skill 列表响应格式错误");
      state.skills = skills;
      this.listLoaded = true;
      this.buildReferenceIndex();
      showDbBanner(false);
      this.renderList();
      await references;
      if (!request.current()) return;
      this.renderList();
      if (this.view === "detail" && this.selectedCode) {
        if (!skills.some(skill => skill.code === this.selectedCode)) this.backToList();
        else if (this.selectedCode === detailAtStart) await this.select(this.selectedCode);
      }
    } catch (error) {
      if (!request.current()) return;
      this.listError = request.controller.signal.aborted ? "Skill 列表读取超时" : error.message;
      if ([401, 403].includes(error.status)) {
        this.cancelReads(["references", "detail", "resource"]);
        state.skills = [];
        this.agentReferences = [];
        this.referenceIndex.clear();
        this.details.clear();
      }
      this.renderList();
      if (this.view === "detail") this.renderEmptyDetail("Skill 列表加载失败");
    } finally {
      request.finish();
    }
  },

  renderList({ scrollTop = null } = {}) {
    const pane = document.getElementById("skillListPane");
    if (!pane) return;
    this.closeReferences();
    if (!this.listLoaded) {
      pane.innerHTML = this.listError
        ? `<div class="skill-list-state is-error" role="alert"><p>${escapeHtml(this.listError)}</p><button class="btn" type="button" data-skill-retry>重试</button></div>`
        : `<div class="skill-list-state"><span class="loading-spinner"></span>正在加载 Skill…</div>`;
      pane.querySelector("[data-skill-retry]")?.addEventListener("click", () => this.refresh());
      return;
    }
    const search = document.getElementById("skillSearch");
    const focused = document.activeElement === search;
    const caret = focused ? search.selectionStart : null;
    const previousScroll = scrollTop ?? pane.querySelector(".skill-table-wrap")?.scrollTop ?? this.listScroll;
    const agents = this.filterableAgents();
    const rows = this.visibleSkills();
    pane.innerHTML = `<div class="skill-list-toolbar">
      <select id="skillAgentFilter" aria-label="筛选 Agent"${this.referencesState !== "ready" ? " disabled" : ""}>
        ${[["all", "全部 Skill", "library"], ["unlinked", "未被引用", "unlink"], ...agents.map(agent => [agent.id, agent.name, "bot"])].map(([id, name, icon], index) => `<option value="${escapeHtml(id)}" data-icon="${icon}"${index === 2 ? ' data-separator-before="true"' : ""}${id === (this.referencesState === "ready" ? this.agentFilter : "all") ? " selected" : ""}>${escapeHtml(name)}</option>`).join("")}
      </select>
      <label class="console-search"><i data-lucide="search"></i><input id="skillSearch" type="search" aria-label="搜索 Skill" placeholder="搜索 Skill" value="${escapeHtml(this.query)}"></label>
      <span class="skill-table-total">${rows.length} 个 Skill</span>
    </div>
    ${this.referencesState === "error" ? `<div class="skill-reference-error" role="status"><span>${escapeHtml(this.referencesError)}</span><button class="btn btn-sm" type="button" data-retry-references>重试引用查询</button></div>` : ""}
    <div class="skill-table-wrap"><table class="data-table skill-table"><thead><tr><th>Skill</th><th class="skill-version-column">版本</th><th class="skill-agent-column">引用 Agent</th><th class="skill-status-column">状态</th><th class="skill-resource-column">资源</th><th class="skill-updated-column">更新时间</th><th class="skill-open-column"></th></tr></thead>
    <tbody>${rows.length ? rows.map(skill => {
      const runtime = this.runtimeLabel(skill);
      const status = ["ready", "syncing", "missing", "error"].includes(skill.runtime_status) ? skill.runtime_status : "missing";
      const label = { ready: "已就绪", syncing: "同步中", missing: "未加载", error: "加载失败" }[status];
      const count = (icon, value, title) => `<span title="${title}"><i data-lucide="${icon}"></i>${Number(value || 0)}</span>`;
      return `<tr data-skill-row="${escapeHtml(skill.code)}" tabindex="0" aria-label="查看 ${escapeHtml(skill.label)}">
        <td><div class="skill-table-name"><button class="skill-name-button" type="button" data-open-skill="${escapeHtml(skill.code)}">${escapeHtml(skill.label)}</button>${skill.source === "builtin" ? '<span class="skill-source-label">内置</span>' : ""}</div><code class="skill-table-code">${escapeHtml(skill.code)}</code></td>
        <td class="skill-version-column">${escapeHtml(skill.version || "未声明")}</td><td>${this.renderReferences(skill)}</td>
        <td><span class="skill-status-tag runtime-${status}" title="${escapeHtml(runtime.label + '：' + runtime.detail)}">${label}</span></td>
        <td><div class="skill-resource-count">${count("book-open-text", skill.reference_count, "References")}${count("paperclip", skill.asset_count, "Assets")}${count("terminal-square", skill.script_count, "脚本")}</div></td>
        <td class="skill-updated-column">${escapeHtml(formatDate(skill.updated_at))}</td><td><button class="skill-open-button" type="button" data-open-skill="${escapeHtml(skill.code)}" aria-label="查看 ${escapeHtml(skill.label)} 详情"><i data-lucide="chevron-right"></i></button></td></tr>`;
    }).join("") : `<tr><td colspan="7" class="skill-table-empty">${state.skills.length ? "没有匹配的 Skill" : "暂无 Skill，可上传 ZIP 安装。"}</td></tr>`}</tbody></table></div>`;
    pane.querySelector("#skillAgentFilter").addEventListener("change", event => this.filterByAgent(event.target.value));
    const searchInput = pane.querySelector("#skillSearch");
    let composing = false;
    const searchSkills = event => {
      this.query = event.target.value;
      if (composing || event.isComposing) return;
      this.listScroll = 0;
      this.renderList({ scrollTop: 0 });
    };
    searchInput.addEventListener("compositionstart", () => { composing = true; });
    searchInput.addEventListener("compositionend", event => { composing = false; searchSkills(event); });
    searchInput.addEventListener("input", searchSkills);
    pane.querySelector("[data-retry-references]")?.addEventListener("click", () => this.refreshReferences());
    pane.querySelectorAll("[data-skill-row]").forEach(row => {
      row.addEventListener("click", event => { if (!event.target.closest("button")) this.openDetail(row.dataset.skillRow); });
      row.addEventListener("keydown", event => { if (event.target === row && ["Enter", " "].includes(event.key)) { event.preventDefault(); this.openDetail(row.dataset.skillRow); } });
    });
    pane.querySelectorAll("[data-open-skill]").forEach(button => button.addEventListener("click", () => this.openDetail(button.dataset.openSkill)));
    pane.querySelectorAll("[data-skill-refs]").forEach(button => button.addEventListener("click", event => {
      event.stopPropagation();
      this.showReferences(button.dataset.skillRefs, button);
    }));
    pane.querySelector(".skill-table-wrap").scrollTop = previousScroll;
    if (focused) { const next = pane.querySelector("#skillSearch"); next.focus(); if (caret !== null) next.setSelectionRange(caret, caret); }
    window.lucide?.createIcons();
  },

  orderedReferences(code) {
    const refs = this.referenceIndex.get(code) || [];
    return [...refs.filter(agent => agent.id === this.agentFilter), ...refs.filter(agent => agent.id !== this.agentFilter)];
  },

  renderReferences(skill) {
    if (this.referencesState !== "ready") return `<span class="muted">${this.referencesState === "error" ? "获取失败" : "读取中…"}</span>`;
    const refs = this.orderedReferences(skill.code);
    if (!refs.length) return `<span class="skill-unreferenced"><i data-lucide="unlink" aria-hidden="true"></i>未被引用</span>`;
    return `<div class="skill-ref-chips"><span class="skill-ref-chip" title="${escapeHtml(refs[0].name + ' · ' + refs[0].id)}"><i data-lucide="bot"></i><span>${escapeHtml(refs[0].name)}</span></span>${refs.length > 1 ? `<button type="button" class="skill-ref-more" data-skill-refs="${escapeHtml(skill.code)}" aria-label="查看 ${escapeHtml(skill.label)} 的其他 ${refs.length - 1} 个引用 Agent" aria-expanded="false" aria-controls="skillAgentRefsPopover">+${refs.length - 1}</button>` : ""}</div>`;
  },

  filterByAgent(id) {
    this.agentFilter = id;
    this.listScroll = 0;
    this.closeReferences();
    this.renderList({ scrollTop: 0 });
  },

  closeReferences() {
    const popover = document.getElementById("skillAgentRefsPopover");
    if (popover?.matches(":popover-open")) popover.hidePopover();
    document.querySelectorAll('[data-skill-refs][aria-expanded="true"]').forEach(button => button.setAttribute("aria-expanded", "false"));
  },

  showReferences(code, anchor) {
    const popover = document.getElementById("skillAgentRefsPopover");
    if (!popover) return;
    const repeated = popover.matches(":popover-open") && popover.dataset.code === code;
    this.closeReferences();
    if (repeated) return;
    popover.dataset.code = code;
    popover.innerHTML = this.orderedReferences(code).slice(1).map(agent => `<div class="skill-agent-popover-item" title="${escapeHtml(agent.id)}"><i data-lucide="bot"></i><span>${escapeHtml(agent.name)}</span></div>`).join("");
    popover.showPopover();
    anchor.setAttribute("aria-expanded", "true");
    const rect = anchor.getBoundingClientRect(), box = popover.getBoundingClientRect();
    popover.style.left = Math.max(8, Math.min(rect.left, window.innerWidth - box.width - 8)) + "px";
    popover.style.top = (rect.bottom + 6 + box.height <= window.innerHeight - 8 ? rect.bottom + 6 : Math.max(8, rect.top - box.height - 6)) + "px";
    window.lucide?.createIcons();
  },

  openDetail(code) {
    this.listScroll = document.querySelector("#skillListPane .skill-table-wrap")?.scrollTop || 0;
    this.lastSkillCode = code;
    return this.select(code);
  },

  backToList() {
    this.cancelReads(["detail", "resource"]);
    this.selectedCode = null;
    this.setView("list");
    this.renderList({ scrollTop: this.listScroll });
    const row = [...document.querySelectorAll("#skillListPane [data-skill-row]")].find(row => row.dataset.skillRow === this.lastSkillCode);
    row?.focus({ preventScroll: true });
  },

  renderEmptyDetail(message = "选择一个 Skill 查看说明和资源") {
    const detail = document.getElementById("skillDetailPane");
    if (!detail) return;
    detail.innerHTML = `<header class="skill-detail-header"><button class="agent-editor-back" type="button" data-skill-back><i data-lucide="arrow-left"></i>返回</button></header><div class="skill-detail-empty"><p>${escapeHtml(message)}</p></div>`;
    detail.querySelector("[data-skill-back]").addEventListener("click", () => this.backToList());
    window.lucide?.createIcons();
  },

  async select(code) {
    if (!code) return;
    this.cancelReads(["resource"]);
    if (code !== this.selectedCode) this.selectedResourcePath = null;
    this.selectedCode = code;
    this.setView("detail");
    const detail = document.getElementById("skillDetailPane");
    if (!detail) return;
    const request = this.startRead("detail");
    this.renderEmptyDetail("正在加载详情…");
    try {
      const skill = await apiFetch(`/api/v1/skills/${encodeURIComponent(code)}`, { signal: request.controller.signal });
      if (!request.current() || this.view !== "detail" || this.selectedCode !== code) return;
      this.details.set(code, skill);
      this.renderDetail(skill);
    } catch (error) {
      if (!request.current() || this.view !== "detail" || this.selectedCode !== code) return;
      this.renderEmptyDetail(request.controller.signal.aborted ? "Skill 详情读取超时" : error.message);
      const retry = document.createElement("button");
      retry.className = "btn"; retry.type = "button"; retry.textContent = "重试";
      retry.addEventListener("click", () => this.select(code));
      detail.querySelector(".skill-detail-empty").appendChild(retry);
    } finally { request.finish(); }
  },

  renderDetail(skill) {
    const detail = document.getElementById("skillDetailPane");
    if (!detail) return;
    this.moreMenuDismissController?.abort();
    this.moreMenuDismissController = null;
    const isBuiltin = skill.source === "builtin";
    const runtime = this.runtimeLabel(skill);
    const resources = skill.resources || [];
    const canPreviewResource = (resource) =>
      ["text", "docx", "xlsx"].includes(resource.read_format) && resource.category !== "script";
    const selectedResource = resources.find((resource) => resource.path === this.selectedResourcePath && canPreviewResource(resource));
    const activeResourcePath = this.selectedResourcePath === "SKILL.md"
      ? "SKILL.md"
      : selectedResource?.path || "SKILL.md";
    this.selectedResourcePath = activeResourcePath;
    const skillDefinitionRow = `<li class="skill-resource-row${activeResourcePath === "SKILL.md" ? " active" : ""}" data-read-resource="SKILL.md" data-resource-path="SKILL.md" role="button" tabindex="0">
      <span class="skill-resource-main"><i data-lucide="file-badge"></i><span><code>SKILL.md</code><small>Skill 定义 · 完整正文</small></span></span>
      <i class="skill-resource-open" data-lucide="chevron-right"></i>
    </li>`;
    const resourceRows = resources.length
      ? resources.map((resource) => {
          const isScript = resource.category === "script";
          const canRead = canPreviewResource(resource);
          const category = resource.category === "reference" ? "Reference" : resource.category === "asset" ? "Asset" : "Script";
          const readLabels = {
            text: "文本可读",
            docx: "Word 可读",
            xlsx: "Excel 可读",
          };
          const unreadableStatus = resource.path?.toLowerCase().endsWith(".pptx")
              ? "PPTX 暂不可读"
              : "当前暂不可读";
          return `<li class="skill-resource-row${isScript ? " is-script" : ""}${resource.path === activeResourcePath ? " active" : ""}" ${canRead ? `data-read-resource="${escapeHtml(resource.path)}" role="button" tabindex="0"` : ""} data-resource-path="${escapeHtml(resource.path)}">
            <span class="skill-resource-main"><i data-lucide="${isScript ? "file-code-2" : resource.category === "reference" ? "file-text" : "file"}"></i><span><code>${escapeHtml(resource.path)}</code><small>${category} · ${escapeHtml(resource.mime)} · ${this.formatBytes(resource.size)}${canRead ? ` · ${escapeHtml(readLabels[resource.read_format] || "可读")}` : ""}</small></span></span>
            ${canRead ? `<i class="skill-resource-open" data-lucide="chevron-right"></i>` : isScript ? `<span class="skill-resource-lock">脚本不披露源码</span>` : `<span class="skill-resource-lock">${escapeHtml(unreadableStatus)}</span>`}
          </li>`;
        }).join("")
      : `<li class="skill-resource-empty">该 Skill 没有附加资源</li>`;
    detail.innerHTML = `
      <header class="skill-detail-header">
        <button class="agent-editor-back" type="button" data-skill-back><i data-lucide="arrow-left"></i><span>返回</span></button>
        <div class="skill-detail-identity">
          <div class="skill-detail-title"><h3>${escapeHtml(skill.label)}</h3><span class="skill-source-label">${isBuiltin ? "内置" : "自定义"}</span><span class="skill-runtime-marker runtime-${escapeHtml(skill.runtime_status || "missing")}" title="${escapeHtml(`${runtime.label}：${runtime.detail}`)}" aria-label="${escapeHtml(runtime.label)}"><span class="skill-runtime-dot"></span></span></div>
          <code>${escapeHtml(skill.code)}</code>
        </div>
        <div class="row-actions skill-detail-actions">
          ${isBuiltin ? "" : `
            <button class="btn skill-direct-action" type="button" data-skill-action="edit"><i data-lucide="pencil-line"></i><span>编辑</span></button>
            <button class="btn skill-direct-action" type="button" data-skill-action="replace"><i data-lucide="upload"></i><span>上传</span></button>
            <button class="btn skill-direct-action" type="button" data-skill-action="download" title="下载 Skill 包" aria-label="下载 Skill 包"><i data-lucide="download"></i><span>下载</span></button>
            <div class="skill-more-actions">
              <button class="skill-more-trigger" type="button" data-skill-more-trigger aria-label="更多 Skill 操作" title="更多操作" aria-haspopup="menu" aria-expanded="false"><i data-lucide="ellipsis-vertical"></i></button>
              <div class="skill-more-menu" data-skill-more-menu role="menu" hidden>
                <button type="button" role="menuitem" data-skill-action="reload"><i data-lucide="refresh-cw"></i><span>重新加载</span></button>
                <button class="danger" type="button" role="menuitem" data-skill-action="delete"><i data-lucide="trash-2"></i><span>删除</span></button>
              </div>
            </div>`}
        </div>
      </header>
      <div class="skill-detail-layout">
        <div class="skill-detail-main">
          <section class="skill-detail-summary">
            <dl class="skill-facts">
              <div><i data-lucide="tag"></i><span><dt>版本</dt><dd>${escapeHtml(skill.version || "未声明")}</dd></span></div>
              <div><i data-lucide="braces"></i><span><dt>包格式</dt><dd>${escapeHtml(skill.schema_version)}</dd></span></div>
              <div><i data-lucide="book-open-text"></i><span><dt>References</dt><dd>${Number(skill.reference_count || 0)}</dd></span></div>
              <div><i data-lucide="paperclip"></i><span><dt>Assets</dt><dd>${Number(skill.asset_count || 0)}</dd></span></div>
              <div><i data-lucide="terminal-square"></i><span><dt>脚本</dt><dd>${Number(skill.script_count || 0)}</dd></span></div>
              <div><i data-lucide="clock-3"></i><span><dt>更新时间</dt><dd>${escapeHtml(formatDate(skill.updated_at))}</dd></span></div>
            </dl>
          </section>
          ${skill.runtime_status === "error" || skill.runtime_status === "missing" ? `<div class="skill-security-note"><i data-lucide="circle-alert"></i><span>${escapeHtml(runtime.detail)}</span></div>` : ""}
          <section class="skill-detail-section skill-resource-directory">
            <div class="skill-section-heading"><div><h4>资源文件</h4><p>SKILL.md、References 与 Assets</p></div><span>${resources.length + 1} 项</span></div>
            <ul class="skill-resource-list">${skillDefinitionRow}${resourceRows}</ul>
          </section>
        </div>
        <aside class="skill-detail-lane skill-preview-lane">
          <section class="skill-resource-preview" id="skillResourcePreview">
            <div class="skill-section-heading"><div><h4>文件预览</h4><p>选择可读资源后在此直接查看</p></div></div>
            <div class="skill-preview-empty">正在加载预览…</div>
          </section>
        </aside>
      </div>`;
    detail.querySelector("[data-skill-back]").addEventListener("click", () => this.backToList());
    const moreTrigger = detail.querySelector("[data-skill-more-trigger]");
    const moreMenu = detail.querySelector("[data-skill-more-menu]");
    const closeMoreMenu = () => {
      if (!moreTrigger || !moreMenu) return;
      moreMenu.hidden = true;
      moreTrigger.setAttribute("aria-expanded", "false");
      this.moreMenuDismissController?.abort();
      this.moreMenuDismissController = null;
    };
    moreTrigger?.addEventListener("click", (event) => {
      event.stopPropagation();
      const willOpen = moreMenu.hidden;
      if (!willOpen) {
        closeMoreMenu();
        return;
      }
      moreMenu.hidden = false;
      moreTrigger.setAttribute("aria-expanded", "true");
      this.moreMenuDismissController?.abort();
      this.moreMenuDismissController = new AbortController();
      const { signal } = this.moreMenuDismissController;
      document.addEventListener("click", (outsideEvent) => {
        if (!outsideEvent.target.closest(".skill-more-actions")) closeMoreMenu();
      }, { signal });
      document.addEventListener("keydown", (keyEvent) => {
        if (keyEvent.key !== "Escape") return;
        closeMoreMenu();
        moreTrigger.focus();
      }, { signal });
    });
    detail.querySelectorAll("[data-skill-action]").forEach((button) => {
      button.addEventListener("click", () => {
        closeMoreMenu();
        this.handleAction(button.dataset.skillAction, skill);
      });
    });
    detail.querySelectorAll("[data-read-resource]").forEach((button) => {
      button.addEventListener("click", () => this.readResource(skill.code, button.dataset.readResource));
      button.addEventListener("keydown", (event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        this.readResource(skill.code, button.dataset.readResource);
      });
    });
    window.lucide?.createIcons();
    if (activeResourcePath) {
      this.readResource(skill.code, activeResourcePath);
    }
  },

  formatBytes(value) {
    const bytes = Number(value || 0);
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MiB`;
  },

  previewStats(content) {
    const text = String(content || "");
    const characters = Array.from(text).length;
    let tokenWeight = 0;
    for (const character of text) {
      if (/\s/u.test(character)) tokenWeight += 0.05;
      else if (/\p{Script=Han}/u.test(character)) tokenWeight += 1;
      else tokenWeight += 0.25;
    }
    return {
      characters,
      tokens: Math.max(1, Math.ceil(tokenWeight)),
    };
  },

  previewHeading(path, { format = "", lineCount = null, content = "" } = {}) {
    const stats = this.previewStats(content);
    const firstLine = [
      format ? String(format).toUpperCase() : "",
      lineCount == null ? "" : `${Number(lineCount).toLocaleString("zh-CN")} 行`,
      content ? `${stats.characters.toLocaleString("zh-CN")} 字` : "",
    ].filter(Boolean).join(" · ");
    return `<div class="skill-section-heading skill-preview-heading">
      <div><h4>文件预览</h4><p>${escapeHtml(path)}</p></div>
      ${firstLine ? `<div class="skill-preview-stats"><span>${escapeHtml(firstLine)}</span><span>约 ${stats.tokens.toLocaleString("zh-CN")} Token</span></div>` : ""}
    </div>`;
  },

  runtimeLabel(skill) {
    const labels = {
      ready: { label: "运行文件已就绪", detail: "当前 Pod 已加载该 Skill 的运行文件。" },
      missing: { label: "运行文件未加载", detail: "当前 Pod 缺少运行文件，请使用“重新加载”。" },
      syncing: { label: "运行文件同步中", detail: "正在同步 Skill 运行文件。" },
      error: { label: "运行文件同步失败", detail: `运行文件不可用（${skill.runtime_error_code || "unknown"}），普通对话和其他 Skill 不受影响。` },
    };
    return labels[skill.runtime_status] || labels.missing;
  },

  renderMetadataCard(entries) {
    if (!entries?.length) return "";
    return `<section class="skill-metadata-card" aria-label="Skill 元数据">
      <dl class="skill-metadata-grid">${entries.map(({ key, value }) => `<div><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("")}</dl>
    </section>`;
  },

  async readResource(code, path) {
    const preview = document.getElementById("skillResourcePreview");
    if (!preview || this.view !== "detail" || this.selectedCode !== code) return;
    const request = this.startRead("resource");
    this.selectedResourcePath = path;
    document.querySelectorAll("#skillDetailPane [data-resource-path]").forEach((row) => {
      row.classList.toggle("active", row.dataset.resourcePath === path);
    });
    preview.innerHTML = `${this.previewHeading(path)}<div class="skill-preview-loading"><span class="loading-spinner"></span>正在读取资源…</div>`;
    window.lucide?.createIcons();
    try {
      const page = await apiFetch(`/api/v1/skills/${encodeURIComponent(code)}/resources/${path.split("/").map(encodeURIComponent).join("/")}`, { signal: request.controller.signal });
      if (!request.current() || this.view !== "detail" || this.selectedCode !== code || this.selectedResourcePath !== path) return;
      const isMarkdown = /\.md$/i.test(path) || /markdown/i.test(String(page.mime_type || page.mime || ""));
      const isJson = /\.json$/i.test(path) || /json/i.test(String(page.mime_type || page.mime || ""));
      const markdownDocument = isMarkdown
        ? window.DeepAgentSkillFrontmatter?.parseSkillFrontmatter(page.content)
        : null;
      const content = isMarkdown
        ? `<div class="skill-preview-markdown">${this.renderMetadataCard(markdownDocument?.entries)}${window.DeepAgentMarkdown?.renderBody(markdownDocument?.body ?? page.content) ?? `<pre>${escapeHtml(markdownDocument?.body ?? page.content)}</pre>`}</div>`
        : isJson
          ? (window.DeepAgentJson?.renderBlock(page.content, { className: "skill-preview-json", ariaLabel: `${path} JSON` }) || `<pre>${escapeHtml(page.content)}</pre>`)
        : `<pre>${escapeHtml(page.content)}</pre>`;
      preview.innerHTML = `${this.previewHeading(path, { format: page.format, lineCount: page.returned_count, content: page.content })}${content}${page.has_more ? `<p class="skill-preview-more">内容较长，可由 Agent 使用 offset=${escapeHtml(page.next_offset)}、char_offset=${escapeHtml(page.next_char_offset ?? 0)} 继续分页读取。</p>` : ""}`;
      window.lucide?.createIcons();
      if (isMarkdown) void window.DeepAgentMarkdown?.renderDiagrams(preview);
    } catch (error) {
      if (!request.current() || this.view !== "detail" || this.selectedCode !== code || this.selectedResourcePath !== path) return;
      preview.innerHTML = `${this.previewHeading(path)}<div class="skill-preview-empty is-error">${escapeHtml(request.controller.signal.aborted ? "资源读取超时" : error.message)}</div>`;
      window.lucide?.createIcons();
    } finally { request.finish(); }
  },

  downloadFilename(response, fallbackName) {
    const disposition = response.headers.get("Content-Disposition") || "";
    const encoded = /filename\*=UTF-8''([^;]+)/i.exec(disposition);
    const plain = /filename=(?:"([^"]+)"|([^;]+))/i.exec(disposition);
    try {
      const name = (encoded ? decodeURIComponent(encoded[1]) : plain?.[1] || plain?.[2] || "").trim();
      if (name.endsWith(".zip") && !/[\\/\x00-\x1f\x7f]/.test(name)) return name;
    } catch { /* Malformed header: retain the safe fallback. */ }
    return fallbackName;
  },

  async download(path, fallbackName) {
    const response = await daFetch(serviceUrl(path));
    if (!response.ok) {
      let detail = `导出 Skill 失败（HTTP ${response.status}）`;
      try {
        const body = await response.json();
        detail = window.AdminFormFeedback?.formatDetail(body.detail || body.message) || detail;
      } catch { /* ignore */ }
      throw new Error(detail);
    }
    const blobUrl = URL.createObjectURL(await response.blob());
    try {
      const anchor = document.createElement("a");
      anchor.href = blobUrl;
      anchor.download = this.downloadFilename(response, fallbackName);
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
    } finally {
      URL.revokeObjectURL(blobUrl);
    }
  },

  async handleAction(action, skill) {
    if (action === "replace") {
      this.uploadMode = "replace";
      document.getElementById("skillPackageInput")?.click();
      return;
    }
    if (action === "download") {
      try {
        await this.download(
          `/api/v1/skills/${encodeURIComponent(skill.code)}/package`,
          `${skill.code}.zip`,
        );
      } catch (error) {
        showToast(error.message, true);
      }
      return;
    }
    if (action === "reload") {
      try {
        await apiFetch(`/api/v1/skills/${encodeURIComponent(skill.code)}/reload`, { method: "POST" });
        showToast("Skill 运行文件已重新加载");
        await this.refresh();
      } catch (error) {
        showToast(error.message, true);
      }
      return;
    }
    if (action === "edit") {
      const values = await requestActionFields({
        title: "编辑 Skill 信息",
        message: skill.code,
        variant: "form",
        confirmLabel: "保存",
        fields: [
          { name: "label", label: "显示名称", value: skill.label, required: true },
          { name: "description", label: "描述", value: skill.description, multiline: true },
        ],
      });
      if (!values) return;
      await this.patch(skill.code, values, "Skill 信息已更新");
      return;
    }
    if (action === "delete") {
      if (!(await confirmAction({
        title: "删除 Skill？",
        message: `平台原始 ZIP 也会尝试清理。当前对象：${skill.label}`,
        icon: "trash-2",
        tone: "danger",
        confirmLabel: "删除",
      }))) return;
      try {
        await apiFetch(`/api/v1/skills/${encodeURIComponent(skill.code)}`, { method: "DELETE" });
        if (this.view === "detail" && this.selectedCode === skill.code) this.backToList();
        showToast("Skill 已删除");
        await this.refresh();
      } catch (error) {
        showToast(error.message, true);
      }
    }
  },

  async patch(code, payload, message) {
    try {
      await apiFetch(`/api/v1/skills/${encodeURIComponent(code)}`, { method: "PATCH", body: JSON.stringify(payload) });
      showToast(message);
      await this.refresh();
    } catch (error) {
      showToast(error.message, true);
    }
  },

  async upload(file) {
    if (!file) return;
    const replacing = this.uploadMode === "replace" && this.selectedCode;
    const targetCode = this.selectedCode;
    const path = replacing
      ? `/api/v1/skills/${encodeURIComponent(this.selectedCode)}/package`
      : "/api/v1/skills";
    const method = replacing ? "PUT" : "POST";
    const form = new FormData();
    form.append("file", file);
    try {
      const response = await daFetch(serviceUrl(path), { method, body: form });
      if (!response.ok) {
        let detail = response.statusText;
        try {
          const body = await response.json();
          detail = body.detail || body.message || detail;
        } catch { /* ignore */ }
        throw new Error(window.AdminFormFeedback?.formatDetail(detail) || String(detail));
      }
      const result = await response.json();
      if (replacing && this.selectedCode === targetCode && this.view === "detail") this.selectedCode = result.skill.code;
      showToast(result.status === "noop" ? "Skill 包内容没有变化" : result.status === "repaired" ? "Skill 原包与运行文件已恢复" : replacing ? "Skill 包已替换" : "Skill 已安装");
      await this.refresh();
    } catch (error) {
      showToast(error.message, true);
    } finally {
      this.uploadMode = "install";
      const input = document.getElementById("skillPackageInput");
      if (input) input.value = "";
    }
  },

  init() {
    document.addEventListener("deep-agent-configuration-tab", event => { if (event.detail?.name !== "skills") this.deactivate(); });
    window.addEventListener("pagehide", () => this.deactivate());
    window.addEventListener("resize", () => this.closeReferences());
    document.getElementById("skillAgentRefsPopover")?.addEventListener("toggle", event => {
      if (event.newState === "closed") document.querySelectorAll('[data-skill-refs][aria-expanded="true"]').forEach(button => button.setAttribute("aria-expanded", "false"));
    });
    window.DeepAgentConsoleDirectory?.subscribe(resources => {
      if (!resources.includes("agents")) return;
      this.referencesDirty = true;
      if (this.isActive()) void this.refreshReferences();
    });
    document.getElementById("refreshSkillsBtn")?.addEventListener("click", () => this.refresh());
    document.getElementById("exportAllSkillsBtn")?.addEventListener("click", async () => {
      try {
        await this.download("/api/v1/skills/export", "deepagent-skills-export.zip");
      } catch (error) {
        showToast(error.message, true);
      }
    });
    document.getElementById("installSkillBtn")?.addEventListener("click", () => {
      this.openInstallDialog();
    });
    document.getElementById("chooseSkillPackagesBtn")?.addEventListener("click", () => document.getElementById("skillInstallFiles").click());
    document.getElementById("skillInstallFiles")?.addEventListener("change", (event) => this.selectInstallFiles(event.target.files));
    document.getElementById("startSkillInstallBtn")?.addEventListener("click", () => this.installSelected());
    for (const id of ["closeSkillInstallBtn", "dismissSkillInstallBtn"]) {
      document.getElementById(id)?.addEventListener("click", () => {
        if (!this.installing) document.getElementById("skillInstallDialog").close();
      });
    }
    document.getElementById("skillInstallDialog")?.addEventListener("cancel", (event) => {
      if (this.installing) event.preventDefault();
    });
    document.getElementById("skillPackageInput")?.addEventListener("change", (event) => this.upload(event.target.files?.[0]));
  },
};

window.skillsAdmin = skillsAdmin;

document.addEventListener("DOMContentLoaded", () => {
  skillsAdmin.init();
});
