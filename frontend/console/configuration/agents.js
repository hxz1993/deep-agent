function chatUrlForAgent(id) {
  return window.DeepAgentConsoleAPI.consoleUrl(`?agent_id=${encodeURIComponent(id)}#/workspace`);
}

const CATEGORY_LABELS = {
  builtin: "内置",
  mcp: "MCP",
  ui: "UI 工具",
};

const GROUP_LABELS = {
  builtin: "内置工具",
  mcp: "MCP",
  ui: "UI 工具",
};

const GROUP_ORDER = ["builtin", "mcp", "ui"];
let promptEstimateTimer = null;
let promptEstimateRequestId = 0;
let agentListView = "cards";
let agentEditorDirty = false;
let agentsLoadedOnce = false;

function formatCompactCount(value) {
  const count = Number(value);
  if (!Number.isFinite(count) || count < 0) return "—";
  if (count < 1_000) return String(Math.round(count));
  const divisor = count >= 1_000_000 ? 1_000_000 : 1_000;
  const suffix = count >= 1_000_000 ? "M" : "K";
  const scaled = count / divisor;
  return `${scaled >= 100 ? Math.round(scaled) : scaled.toFixed(1).replace(/\.0$/, "")}${suffix}`;
}

function estimatorSourceLabel(source) {
  return source === "model_tokenizer" ? "模型 Tokenizer" : "保守估算器";
}

function capabilityGroup(toolPackage) {
  return toolPackage.category;
}

function capDisplayName(code, category) {
  if (category === "mcp" && code.startsWith("mcp:")) {
    return code.slice(4);
  }
  if (category === "ui" && code.startsWith("ui:")) {
    return code.slice(3);
  }
  return code;
}

function capKindLabel(cap) {
  return CATEGORY_LABELS[cap.category] || cap.category;
}

function isCapabilitySelected(cap, selectedSet) {
  return selectedSet.has(cap.code);
}

function groupCapabilities(caps) {
  const byGroup = {};
  for (const c of caps) {
    const group = capabilityGroup(c);
    (byGroup[group] ||= []).push(c);
  }
  const ordered = GROUP_ORDER.filter((k) => byGroup[k]?.length);
  for (const k of Object.keys(byGroup)) {
    if (!ordered.includes(k)) ordered.push(k);
  }
  return { byGroup, groups: ordered };
}

function formatCapabilitiesPreview(capabilities) {
  if (!capabilities?.length) return "（无）";
  return capabilities.join(", ");
}

function formatSkillsPreview(skills) {
  if (!skills?.length) return "无 Skill";
  return skills.join(", ");
}

function buildCapabilitySelectionSet(selected) {
  return new Set(selected || []);
}

function nextAgentCopyName(sourceName, existingNames) {
  const base = `${sourceName} 副本`;
  if (!existingNames.has(base)) return base;
  let suffix = 2;
  while (existingNames.has(`${base} ${suffix}`)) suffix += 1;
  return `${base} ${suffix}`;
}

const agentsAdmin = {
  setListView(view) {
    agentListView = view === "list" ? "list" : "cards";
    document.getElementById("agentsCardGrid")?.classList.toggle("hidden", agentListView !== "cards");
    document.getElementById("agentsListView")?.classList.toggle("hidden", agentListView !== "list");
    document.querySelectorAll("[data-agent-view]").forEach((button) => {
      const active = button.dataset.agentView === agentListView;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });
  },

  filterList() {
    const query = (document.getElementById("agentListSearch")?.value || "").trim().toLowerCase();
    document.querySelectorAll("[data-agent-search]").forEach((item) => {
      item.classList.toggle("agent-filtered", Boolean(query) && !item.dataset.agentSearch.includes(query));
    });
  },

  renderList(agents) {
    const cardGrid = document.getElementById("agentsCardGrid");
    const tbody = document.getElementById("agentsTableBody");
    if (!cardGrid || !tbody) return;
    if (!agents.length) {
      cardGrid.innerHTML = `<div class="agent-list-state"><i data-lucide="bot"></i><strong>暂无 Agent</strong><span>新建一个 Agent 开始配置与测试。</span></div>`;
      tbody.innerHTML = `<tr><td colspan="5" class="empty-cell">暂无 Agent</td></tr>`;
      window.lucide?.createIcons();
      return;
    }

    cardGrid.innerHTML = agents.map((agent, index) => {
      const capabilities = formatCapabilitiesPreview(agent.capabilities);
      const skills = formatSkillsPreview(agent.skills);
      const search = `${agent.name || ""} ${agent.model?.name || ""} ${capabilities} ${skills}`.toLowerCase();
      const tone = ["violet", "blue", "green", "orange"][index % 4];
      return `<article class="agent-card" data-agent-search="${escapeHtml(search)}" data-open-agent="${escapeHtml(agent.id)}" tabindex="0">
        <header class="agent-card-head">
          <span class="agent-card-icon tone-${tone}"><i data-lucide="bot"></i></span>
          <div><strong>${escapeHtml(agent.name)}</strong><span>${escapeHtml(agent.model?.name || "未配置模型")}</span></div>
        </header>
        <div class="agent-card-body">
          <div class="agent-card-fact"><span>能力</span><strong>${escapeHtml(capabilities)}</strong></div>
          <div class="agent-card-fact"><span>Skill</span><strong>${escapeHtml(skills)}</strong></div>
        </div>
        <footer class="agent-card-footer">
          <span><i data-lucide="clock-3"></i>${escapeHtml(formatDate(agent.updated_at))}</span>
          <div class="row-actions agent-row-actions">
            <a class="btn-link row-action icon-only" data-agent-chat href="${chatUrlForAgent(agent.id)}" target="_blank" rel="noopener" title="发起对话" aria-label="发起对话"><i data-lucide="message-square"></i></a>
            <button type="button" class="row-action icon-only" data-copy-agent="${escapeHtml(agent.id)}" title="复制" aria-label="复制"><i data-lucide="copy"></i></button>
            <button type="button" class="danger row-action icon-only" data-delete="${escapeHtml(agent.id)}" data-name="${escapeHtml(agent.name)}" title="删除" aria-label="删除"><i data-lucide="trash-2"></i></button>
          </div>
        </footer>
      </article>`;
    }).join("");

    tbody.innerHTML = agents.map((agent) => {
      const capabilities = formatCapabilitiesPreview(agent.capabilities);
      const skills = formatSkillsPreview(agent.skills);
      const search = `${agent.name || ""} ${agent.model?.name || ""} ${capabilities} ${skills}`.toLowerCase();
      return `<tr class="agent-list-row" data-agent-search="${escapeHtml(search)}" data-open-agent="${escapeHtml(agent.id)}" tabindex="0">
        <td><strong>${escapeHtml(agent.name)}</strong></td>
        <td>${escapeHtml(agent.model?.name || "—")}</td>
        <td><div class="cap-preview">${escapeHtml(capabilities)}</div><div class="skill-preview">Skill：${escapeHtml(skills)}</div></td>
        <td>${escapeHtml(formatDate(agent.updated_at))}</td>
        <td><div class="row-actions agent-row-actions">
          <a class="btn-link row-action icon-only" data-agent-chat href="${chatUrlForAgent(agent.id)}" target="_blank" rel="noopener" title="发起对话" aria-label="发起对话"><i data-lucide="message-square"></i></a>
          <button type="button" class="row-action icon-only" data-copy-agent="${escapeHtml(agent.id)}" title="复制" aria-label="复制"><i data-lucide="copy"></i></button>
          <button type="button" class="danger row-action icon-only" data-delete="${escapeHtml(agent.id)}" data-name="${escapeHtml(agent.name)}" title="删除" aria-label="删除"><i data-lucide="trash-2"></i></button>
        </div></td>
      </tr>`;
    }).join("");

    document.querySelectorAll("[data-open-agent]").forEach((item) => {
      const open = (event) => {
        if (event.target.closest("a, button")) return;
        this.openEdit(item.dataset.openAgent);
      };
      item.addEventListener("click", open);
      item.addEventListener("keydown", (event) => {
        if (event.target.closest("a, button")) return;
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          this.openEdit(item.dataset.openAgent);
        }
      });
    });
    document.querySelectorAll(".agent-row-actions").forEach((actions) => {
      actions.addEventListener("click", (event) => event.stopPropagation());
      actions.addEventListener("keydown", (event) => event.stopPropagation());
    });
    document.querySelectorAll("[data-copy-agent]").forEach((button) => {
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        this.copy(button.dataset.copyAgent, button);
      });
    });
    document.querySelectorAll("[data-delete]").forEach((button) => {
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        this.remove(button.dataset.delete, button.dataset.name);
      });
    });
    this.filterList();
    window.lucide?.createIcons();
  },

  async updatePromptEstimate() {
    const output = document.getElementById("agentPromptEstimate");
    const promptInput = document.getElementById("agentPrompt");
    const modelInput = document.getElementById("agentModelId");
    if (!output || !promptInput || !modelInput) return;

    const requestId = ++promptEstimateRequestId;
    const prompt = promptInput.value.trim();
    const localCharacterCount = Array.from(prompt).length;
    const modelId = modelInput.value.trim();
    if (!modelId) {
      output.textContent = `${formatCompactCount(localCharacterCount)} 字 / — Token`;
      output.title = "请先选择模型配置";
      return;
    }

    output.textContent = `${formatCompactCount(localCharacterCount)} 字 / 估算中…`;
    try {
      const result = await apiFetch("/api/v1/agents/prompt-estimate", {
        method: "POST",
        body: JSON.stringify({ model_id: modelId, prompt }),
      });
      if (requestId !== promptEstimateRequestId) return;
      output.textContent = `${formatCompactCount(result.character_count)} 字 / ${formatCompactCount(result.estimated_tokens)} Token`;
      output.title = `${Number(result.character_count).toLocaleString("zh-CN")} 字 / 约 ${Number(result.estimated_tokens).toLocaleString("zh-CN")} Token · ${estimatorSourceLabel(result.estimator_source)}`;
    } catch (error) {
      if (requestId !== promptEstimateRequestId) return;
      output.textContent = `${formatCompactCount(localCharacterCount)} 字 / — Token`;
      output.title = `Token 估算失败：${error instanceof Error ? error.message : String(error)}`;
    }
  },

  schedulePromptEstimate(immediate = false) {
    clearTimeout(promptEstimateTimer);
    if (immediate) {
      this.updatePromptEstimate();
      return;
    }
    promptEstimateTimer = setTimeout(() => this.updatePromptEstimate(), 300);
  },

  initPromptEstimate() {
    const promptInput = document.getElementById("agentPrompt");
    const modelInput = document.getElementById("agentModelId");
    if (!promptInput || !modelInput || promptInput.dataset.estimateBound) return;
    promptInput.dataset.estimateBound = "1";
    promptInput.addEventListener("input", () => this.schedulePromptEstimate());
    modelInput.addEventListener("change", () => this.schedulePromptEstimate(true));
  },

  async refresh() {
    const tbody = document.getElementById("agentsTableBody");
    const cardGrid = document.getElementById("agentsCardGrid");
    if (!tbody || !cardGrid) return;
    const panel = document.getElementById("panel-agents");
    panel?.setAttribute("aria-busy", "true");
    if (!agentsLoadedOnce) {
      tbody.innerHTML = `<tr><td colspan="5" class="empty-cell">加载中…</td></tr>`;
      cardGrid.innerHTML = `<div class="agent-list-state"><span class="loading-spinner"></span><span>正在加载 Agent…</span></div>`;
    }
    try {
      const agents = await apiFetch("/api/v1/agents");
      showDbBanner(false);
      this.renderList(agents);
      agentsLoadedOnce = true;
      this.setListView(agentListView);
    } catch (err) {
      if (!agentsLoadedOnce) {
        tbody.innerHTML = `<tr><td colspan="5" class="empty-cell">${escapeHtml(err.message)}</td></tr>`;
        cardGrid.innerHTML = `<div class="agent-list-state error"><i data-lucide="circle-alert"></i><strong>Agent 加载失败</strong><span>${escapeHtml(err.message)}</span></div>`;
        window.lucide?.createIcons();
      } else {
        showToast(`Agent 刷新失败：${err.message}`, true);
      }
    } finally {
      panel?.setAttribute("aria-busy", "false");
    }
  },

  openEditor() {
    const editor = document.getElementById("agentModal");
    if (!editor) return;
    this.switchCapTab("builtin");
    if (!editor.open) editor.show();
    document.body.classList.add("agent-editor-open");
    agentEditorDirty = false;
    window.lucide?.createIcons();
  },

  isEditorOpen() {
    return Boolean(document.getElementById("agentModal")?.open);
  },

  async requestCloseEditor(force = false) {
    const editor = document.getElementById("agentModal");
    if (!editor?.open) return true;
    if (
      !force
      && agentEditorDirty
      && !(await confirmAction({
        title: "放弃未保存的修改？",
        message: "当前 Agent 的修改尚未保存，返回列表后这些修改将丢失。",
        icon: "file-warning",
        confirmLabel: "放弃修改",
      }))
    ) return false;
    agentEditorDirty = false;
    editor.close();
    document.body.classList.remove("agent-editor-open");
    return true;
  },

  renderCapCard(cap, selectedSet) {
    const checked = isCapabilitySelected(cap, selectedSet);
    const displayName = cap.label || capDisplayName(cap.code, cap.category);
    const kindLabel = capKindLabel(cap);
    const toolCount = Number(cap.tool_count ?? cap.tools?.length ?? 0);
    const toolsHtml = toolCount
      ? `<details class="cap-card-tools">
          <summary>${toolCount} 个工具</summary>
          <ul>${cap.tools
            .map(
              (t) =>
                `<li><strong>${escapeHtml(t.name)}</strong>${t.description ? ` — ${escapeHtml(t.description)}` : ""}</li>`
            )
            .join("")}</ul>
        </details>`
      : "";

    return `<article class="cap-card kind-${escapeHtml(cap.category)}" data-cap-name="${escapeHtml(cap.code)}">
      <div class="cap-card-header">
        <label class="cap-switch" title="${checked ? "已关联" : "未关联"}">
          <input type="checkbox" name="cap" value="${escapeHtml(cap.code)}" ${checked ? "checked" : ""} />
          <span class="cap-switch-ui"></span>
        </label>
        <div class="cap-card-info">
          <div class="cap-card-title">
            <span class="kind-badge kind-${escapeHtml(cap.category)} source-${escapeHtml(cap.source || "unknown")}">${escapeHtml(kindLabel)}</span>
            <span>${escapeHtml(displayName)}</span>
          </div>
          ${cap.description ? `<p class="cap-card-desc">${escapeHtml(cap.description)}</p>` : ""}
        </div>
      </div>
      ${toolsHtml}
    </article>`;
  },

  renderCapabilityCheckboxes(selected = []) {
    const container = document.getElementById("agentCapGroups");
    if (!container) return;

    const caps = state.capabilities || [];
    const selectedSet = buildCapabilitySelectionSet(selected);
    const { byGroup } = groupCapabilities(caps);

    container.innerHTML = GROUP_ORDER
      .map((group) => {
        const groupCaps = byGroup[group] || [];
        const cards = groupCaps.length
          ? groupCaps.map((c) => this.renderCapCard(c, selectedSet)).join("")
          : `<div class="cap-empty">暂无${escapeHtml(GROUP_LABELS[group] || group)}</div>`;
        const hidden = group !== "builtin" ? "hidden" : "";
        const label = GROUP_LABELS[group] || group;
        return `<div class="cap-panel ${hidden}" data-kind-panel="${escapeHtml(group)}">
          <div class="cap-panel-toolbar">
            <button type="button" class="btn-link cap-select-all" data-kind="${escapeHtml(group)}">全选 ${escapeHtml(label)}</button>
            <button type="button" class="btn-link cap-select-none" data-kind="${escapeHtml(group)}">全不选</button>
          </div>
          <div class="cap-grid" data-kind-grid="${escapeHtml(group)}">${cards}</div>
        </div>`;
      })
      .join("");

    this.updateCapCounter();
  },

  switchCapTab(kind) {
    const resources = document.querySelector(".agent-editor-resources");
    resources?.querySelectorAll(".cap-tab").forEach((tab) => {
      const active = tab.dataset.kind === kind;
      tab.classList.toggle("active", active);
      tab.setAttribute("aria-selected", String(active));
    });
    resources?.querySelectorAll(".cap-panel").forEach((panel) => {
      panel.classList.toggle("hidden", panel.dataset.kindPanel !== kind);
    });
  },

  setCapsInKind(kind, checked) {
    const panel = document.querySelector(`.cap-panel[data-kind-panel="${kind}"]`);
    if (!panel) return;
    panel.querySelectorAll('input[name="cap"]:not(:disabled)').forEach((input) => {
      input.checked = checked;
    });
    this.updateCapCounter();
  },

  updateCapCounter() {
    const configurableChecked = document.querySelectorAll(
      '#agentCapGroups input[name="cap"]:checked:not(:disabled)'
    );
    const selectedSet = new Set(Array.from(configurableChecked).map((b) => b.value));

    document.querySelectorAll("[data-kind-badge]").forEach((badge) => {
      const group = badge.dataset.kindBadge;
      const groupCapabilities = (state.capabilities || []).filter(
        (capability) => capabilityGroup(capability) === group,
      );
      const groupSelected = groupCapabilities.filter((capability) =>
        selectedSet.has(capability.code),
      ).length;
      badge.textContent = `${groupSelected}/${groupCapabilities.length}`;
    });
  },

  getSelectedCapabilities() {
    const boxes = document.querySelectorAll('#agentCapGroups input[name="cap"]:checked:not(:disabled)');
    const selected = Array.from(boxes).map((b) => b.value);
    return selected;
  },

  renderSkillChoices(selected = []) {
    const container = document.getElementById("agentSkillChoices");
    const counter = document.getElementById("agentSkillCounter");
    if (!container || !counter) return;
    const selectedSet = new Set(selected || []);
    const known = new Set((state.skills || []).map((skill) => skill.code));
    const unavailable = Array.from(selectedSet).filter((code) => !known.has(code));
    const skills = [
      ...(state.skills || []),
      ...unavailable.map((code) => ({
        code,
        label: code,
        description: "该 Skill 当前不存在，保存时将保留原引用。",
        source: "unknown",
        missing: true,
        reference_count: 0,
        asset_count: 0,
      })),
    ];
    if (!skills.length) {
      container.innerHTML = `<div class="cap-empty">暂无可配置 Skill</div>`;
      counter.textContent = "0/0";
      return;
    }
    container.innerHTML = skills.map((skill) => {
      const checked = selectedSet.has(skill.code);
      const disabled = skill.missing === true;
      const source = skill.source === "builtin" ? "内置" : skill.source === "upload" ? "自定义" : "不可用";
      return `<label class="agent-skill-card${disabled ? " is-disabled" : ""}">
        <input type="checkbox" name="agentSkill" value="${escapeHtml(skill.code)}" ${checked ? "checked" : ""} ${disabled ? "disabled" : ""} />
        <span class="agent-skill-card-copy">
          <span class="agent-skill-card-title">${escapeHtml(skill.label)} <small>${escapeHtml(source)}</small></span>
          <span class="agent-skill-card-meta">
            <span title="References">${Number(skill.reference_count || 0)} <i data-lucide="book-open"></i></span>
            <span title="Assets">${Number(skill.asset_count || 0)} <i data-lucide="paperclip"></i></span>
            ${disabled ? '<span class="agent-skill-unavailable">不可用</span>' : ""}
          </span>
        </span>
      </label>`;
    }).join("");
    this.updateSkillCounter();
    window.lucide?.createIcons();
  },

  updateSkillCounter() {
    const counter = document.getElementById("agentSkillCounter");
    if (!counter) return;
    const selected = document.querySelectorAll('#agentSkillChoices input[name="agentSkill"]:checked').length;
    const total = document.querySelectorAll('#agentSkillChoices input[name="agentSkill"]').length;
    counter.textContent = `${selected}/${total}`;
  },

  getSelectedSkills() {
    return Array.from(
      document.querySelectorAll('#agentSkillChoices input[name="agentSkill"]:checked'),
    ).map((input) => input.value);
  },

  syncEditorTitle() {
    const title = document.getElementById("agentModalTitle");
    const name = document.getElementById("agentName")?.value.trim();
    if (title) title.textContent = name || (state.editingAgentId ? "Agent" : "新建 Agent");
  },

  openCreate() {
    state.editingAgentId = null;
    document.getElementById("agentModalTitle").textContent = "新建 Agent";
    document.getElementById("agentModalId").textContent = "";
    document.getElementById("agentModalId").classList.add("hidden");
    document.getElementById("agentName").disabled = false;
    document.getElementById("agentName").value = "";
    populateAgentModelSelect(window.DeepAgentModelOptions.firstSelectableModelId(state.llmModels));
    document.getElementById("agentTemperature").value = "";
    document.getElementById("agentPrompt").value =
      "You are a helpful assistant. Use available tools when they help answer accurately.";
    this.renderCapabilityCheckboxes([]);
    this.renderSkillChoices([]);
    document.getElementById("testAgentBtn")?.classList.add("hidden");
    this.openEditor();
    this.schedulePromptEstimate(true);
  },

  async openEdit(agentId) {
    try {
      const agent = await apiFetch(`/api/v1/agents/${encodeURIComponent(agentId)}`);
      state.editingAgentId = agentId;
      document.getElementById("agentModalId").textContent = `Agent ID：${agent.id || agentId}`;
      document.getElementById("agentModalId").classList.remove("hidden");
      document.getElementById("agentName").disabled = false;
      document.getElementById("agentName").value = agent.name;
      this.syncEditorTitle();
      populateAgentModelSelect(agent.model_id);
      document.getElementById("agentTemperature").value =
        agent.temperature == null ? "" : String(agent.temperature);
      document.getElementById("agentPrompt").value = agent.prompt;
      this.renderCapabilityCheckboxes(agent.capabilities);
      this.renderSkillChoices(agent.skills || []);
      document.getElementById("testAgentBtn")?.classList.remove("hidden");
      this.openEditor();
      this.schedulePromptEstimate(true);
    } catch (err) {
      showToast(err.message, true);
    }
  },

  async copy(agentId, button = null) {
    if (button) button.disabled = true;
    try {
      const [agent, agents] = await Promise.all([
        apiFetch(`/api/v1/agents/${encodeURIComponent(agentId)}`),
        apiFetch("/api/v1/agents"),
      ]);
      const existingNames = new Set((agents || []).map((item) => item.name));
      const values = await requestActionFields({
        title: "复制 Agent",
        message: "除 Agent ID 外，模型、Prompt、温度、工具包和 Skill 都会完整复制。",
        icon: "copy",
        confirmLabel: "复制",
        fields: [{
          name: "name",
          label: "Agent 名称",
          value: nextAgentCopyName(agent.name, existingNames),
          required: true,
        }],
      });
      if (!values) return;
      const name = String(values.name || "").trim();
      if (!name) return;
      await apiFetch("/api/v1/agents", {
        method: "POST",
        body: JSON.stringify({
          name,
          model_id: agent.model_id,
          prompt: agent.prompt,
          temperature: agent.temperature,
          capabilities: [...(agent.capabilities || [])],
          skills: [...(agent.skills || [])],
        }),
      });
      showToast("Agent 已复制");
      await this.refresh();
    } catch (err) {
      showToast(err.message, true);
    } finally {
      if (button?.isConnected) button.disabled = false;
    }
  },

  async save(event) {
    event.preventDefault();
    const form = event.currentTarget;
    AdminFormFeedback.clear(form);
    const name = document.getElementById("agentName").value.trim();
    const modelId = document.getElementById("agentModelId").value.trim();
    const prompt = document.getElementById("agentPrompt").value.trim();
    if (!name || !modelId || !prompt) {
      AdminFormFeedback.show(form, "名称、模型配置和 System Prompt 不能为空");
      return;
    }
    AdminFormFeedback.setSubmitting(form, true);
    const capabilities = this.getSelectedCapabilities();
    const payload = {
      model_id: modelId,
      prompt,
      capabilities,
      skills: this.getSelectedSkills(),
    };
    const tempRaw = document.getElementById("agentTemperature").value.trim();
    if (tempRaw) {
      payload.temperature = Number(tempRaw);
    } else if (state.editingAgentId) {
      payload.temperature = null;
    }

    let saved = false;
    try {
      if (state.editingAgentId) {
        payload.name = name;
        await apiFetch(`/api/v1/agents/${encodeURIComponent(state.editingAgentId)}`, {
          method: "PATCH",
          body: JSON.stringify(payload),
        });
        showToast("Agent 已更新");
      } else {
        payload.name = name;
        await apiFetch("/api/v1/agents", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        showToast("Agent 已创建");
      }
      saved = true;
      await this.requestCloseEditor(true);
    } catch (err) {
      AdminFormFeedback.show(form, err);
    } finally {
      AdminFormFeedback.setSubmitting(form, false);
    }
    if (saved) {
      await refreshAfterSuccessfulSave("Agent", () => this.refresh());
    }
  },

  async remove(agentId, name) {
    const msg =
      name === "default"
        ? `确定删除默认 Agent "${name}"？这可能导致部分会话失去关联。`
        : `确定删除 Agent "${name}"？`;
    if (!(await confirmAction({
      title: name === "default" ? "删除默认 Agent？" : "删除 Agent？",
      message: msg,
      icon: "trash-2",
      tone: "danger",
      confirmLabel: "删除",
    }))) return;
    try {
      await apiFetch(`/api/v1/agents/${encodeURIComponent(agentId)}`, { method: "DELETE" });
      showToast("Agent 已删除");
      await refreshAfterSuccessfulMutation("Agent 已删除", () => this.refresh());
    } catch (err) {
      showToast(err.message, true);
    }
  },

  initCapSection() {
    const resources = document.querySelector(".agent-editor-resources");
    if (!resources || resources.dataset.bound) return;
    resources.dataset.bound = "1";

    resources.addEventListener("click", (e) => {
      const tab = e.target.closest(".cap-tab");
      if (tab) {
        this.switchCapTab(tab.dataset.kind);
        return;
      }
      const selectAll = e.target.closest(".cap-select-all");
      if (selectAll) {
        this.setCapsInKind(selectAll.dataset.kind, true);
        return;
      }
      const selectNone = e.target.closest(".cap-select-none");
      if (selectNone) {
        this.setCapsInKind(selectNone.dataset.kind, false);
      }
    });

    resources.addEventListener("change", (e) => {
      if (e.target.matches('input[name="cap"]')) {
        this.updateCapCounter();
      }
      if (e.target.matches('input[name="agentSkill"]')) {
        this.updateSkillCounter();
      }
    });

  },

  initListControls() {
    document.querySelectorAll("[data-agent-view]").forEach((button) => {
      button.addEventListener("click", () => this.setListView(button.dataset.agentView));
    });
    document.getElementById("agentListSearch")?.addEventListener("input", () => this.filterList());
    this.setListView(agentListView);
  },
};

window.agentsAdmin = agentsAdmin;
window.__AgentAdminUI = { nextAgentCopyName };

document.addEventListener("DOMContentLoaded", () => {
  agentsAdmin.initCapSection();
  agentsAdmin.initPromptEstimate();
  agentsAdmin.initListControls();
  document.getElementById("newAgentBtn")?.addEventListener("click", () => agentsAdmin.openCreate());
  document.getElementById("agentForm")?.addEventListener("submit", (e) => agentsAdmin.save(e));
  document.getElementById("agentForm")?.addEventListener("input", () => { agentEditorDirty = true; });
  document.getElementById("agentForm")?.addEventListener("change", () => { agentEditorDirty = true; });
  document.getElementById("agentName")?.addEventListener("input", () => agentsAdmin.syncEditorTitle());
  document.getElementById("closeAgentModal")?.addEventListener("click", () => agentsAdmin.requestCloseEditor());
  document.getElementById("testAgentBtn")?.addEventListener("click", async () => {
    if (!state.editingAgentId) return;
    if (!(await agentsAdmin.requestCloseEditor())) return;
    window.DeepAgentConsoleChat?.selectAgent(state.editingAgentId);
    window.DeepAgentConsole?.showPage("workspace");
  });
  agentsAdmin.refresh();
});
