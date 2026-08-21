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

function capabilityDisclosureLabel(cap) {
  if (cap.disclosure_mode === "conditional") return "有附件时可用";
  if (cap.disclosure_mode === "search") {
    return `自动预选 Top ${cap.preselect_limit || 3} · 搜索 Top ${cap.search_result_limit || 5}`;
  }
  return "始终可用";
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

function defaultEnabledCapabilityNames(caps) {
  return (caps || []).filter((c) => c.default_enabled && c.enabled !== false).map((c) => c.code);
}

function formatCapabilitiesPreview(capabilities) {
  if (capabilities == null) return "（全部默认）";
  if (!capabilities.length) return "（无）";
  return capabilities.join(", ");
}

function formatSkillsPreview(skills) {
  if (!skills?.length) return "无 Skill";
  return skills.join(", ");
}

function buildCapabilitySelectionSet(selected, caps) {
  if (selected === null) {
    return new Set(defaultEnabledCapabilityNames(caps));
  }
  return new Set(selected || []);
}

function selectionMatchesDefaults(selectedSet, caps) {
  const defaults = new Set(defaultEnabledCapabilityNames(caps));
  if (defaults.size !== selectedSet.size) return false;
  for (const name of defaults) {
    if (!selectedSet.has(name)) return false;
  }
  return true;
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
          <i class="agent-card-arrow" data-lucide="arrow-up-right"></i>
        </header>
        <div class="agent-card-body">
          <div class="agent-card-fact"><span>能力</span><strong>${escapeHtml(capabilities)}</strong></div>
          <div class="agent-card-fact"><span>Skill</span><strong>${escapeHtml(skills)}</strong></div>
        </div>
        <footer class="agent-card-footer">
          <span><i data-lucide="clock-3"></i>${escapeHtml(formatDate(agent.updated_at))}</span>
          <div class="row-actions agent-row-actions">
            <a class="btn-link row-action" href="${chatUrlForAgent(agent.id)}" target="_blank" rel="noopener"><i data-lucide="message-square"></i><span>发起对话</span></a>
            <button type="button" class="danger row-action" data-delete="${escapeHtml(agent.id)}" data-name="${escapeHtml(agent.name)}"><i data-lucide="trash-2"></i><span>删除</span></button>
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
          <a class="btn-link row-action" href="${chatUrlForAgent(agent.id)}" target="_blank" rel="noopener"><i data-lucide="message-square"></i><span>发起对话</span></a>
          <button type="button" class="row-action" data-edit="${escapeHtml(agent.id)}"><i data-lucide="pencil"></i><span>编辑</span></button>
          <button type="button" class="danger row-action" data-delete="${escapeHtml(agent.id)}" data-name="${escapeHtml(agent.name)}"><i data-lucide="trash-2"></i><span>删除</span></button>
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
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          this.openEdit(item.dataset.openAgent);
        }
      });
    });
    document.querySelectorAll("[data-edit]").forEach((button) => {
      button.addEventListener("click", () => this.openEdit(button.dataset.edit));
    });
    document.querySelectorAll("[data-delete]").forEach((button) => {
      button.addEventListener("click", () => this.remove(button.dataset.delete, button.dataset.name));
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
    tbody.innerHTML = `<tr><td colspan="5" class="empty-cell">加载中…</td></tr>`;
    cardGrid.innerHTML = `<div class="agent-list-state"><span class="loading-spinner"></span><span>正在加载 Agent…</span></div>`;
    try {
      const agents = await apiFetch("/api/v1/agents");
      showDbBanner(false);
      this.renderList(agents);
      this.setListView(agentListView);
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="5" class="empty-cell">${escapeHtml(err.message)}</td></tr>`;
      cardGrid.innerHTML = `<div class="agent-list-state error"><i data-lucide="circle-alert"></i><strong>Agent 加载失败</strong><span>${escapeHtml(err.message)}</span></div>`;
      window.lucide?.createIcons();
    }
  },

  openEditor() {
    const editor = document.getElementById("agentModal");
    if (!editor) return;
    if (!editor.open) editor.show();
    document.body.classList.add("agent-editor-open");
    agentEditorDirty = false;
    window.lucide?.createIcons();
  },

  isEditorOpen() {
    return Boolean(document.getElementById("agentModal")?.open);
  },

  requestCloseEditor(force = false) {
    const editor = document.getElementById("agentModal");
    if (!editor?.open) return true;
    if (!force && agentEditorDirty && !confirm("当前 Agent 有未保存修改，确定返回列表吗？")) return false;
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
    const searchText = `${displayName} ${cap.description || ""} ${cap.code}`.toLowerCase();
    const disabled = cap.enabled === false;

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

    return `<article class="cap-card kind-${escapeHtml(cap.category)}${disabled ? " is-locked" : ""}" data-cap-name="${escapeHtml(cap.code)}" data-search="${escapeHtml(searchText)}">
      <div class="cap-card-header">
        <label class="cap-switch" title="${disabled ? "工具包已停用" : checked ? "已启用" : "未启用"}">
          <input type="checkbox" name="cap" value="${escapeHtml(cap.code)}" ${checked ? "checked" : ""} ${disabled ? "disabled" : ""} />
          <span class="cap-switch-ui"></span>
        </label>
        <div class="cap-card-info">
          <div class="cap-card-title">
          <span class="kind-badge kind-${escapeHtml(cap.category)} source-${escapeHtml(cap.source || "unknown")}">${escapeHtml(kindLabel)}</span>
            <span>${escapeHtml(displayName)}</span>
            <span class="kind-badge disclosure-badge">${escapeHtml(capabilityDisclosureLabel(cap))}</span>
          </div>
          ${cap.description ? `<p class="cap-card-desc">${escapeHtml(cap.description)}</p>` : ""}
        </div>
      </div>
      ${toolsHtml}
    </article>`;
  },

  countSelectedInGroup(group, selectedSet) {
    const caps = (state.capabilities || []).filter((c) => capabilityGroup(c) === group);
    return caps.filter((c) => isCapabilitySelected(c, selectedSet)).length;
  },

  renderCapabilityCheckboxes(selected = null) {
    const container = document.getElementById("agentCapGroups");
    const tabsEl = document.getElementById("capTabs");
    const searchEl = document.getElementById("capSearch");
    if (!container || !tabsEl) return;

    if (searchEl) searchEl.value = "";

    const caps = state.capabilities || [];
    if (!caps.length) {
      tabsEl.innerHTML = "";
      container.innerHTML = `<div class="cap-empty">无法加载能力列表</div>`;
      this.updateCapCounter();
      return;
    }

    const selectedSet = buildCapabilitySelectionSet(selected, caps);
    const { byGroup, groups } = groupCapabilities(caps);
    const defaultTab = groups.find((group) => group !== "mcp") || groups[0];

    tabsEl.innerHTML = groups
      .map((group) => {
        const total = byGroup[group].length;
        const selectedCount = this.countSelectedInGroup(group, selectedSet);
        const active = group === defaultTab ? "active" : "";
        const label = GROUP_LABELS[group] || group;
        return `<button type="button" class="cap-tab ${active}" data-kind="${escapeHtml(group)}">
          ${escapeHtml(label)}
          <span class="cap-tab-badge" data-kind-badge="${escapeHtml(group)}">${selectedCount}/${total}</span>
        </button>`;
      })
      .join("");

    container.innerHTML = groups
      .map((group) => {
        const cards = byGroup[group].map((c) => this.renderCapCard(c, selectedSet)).join("");
        const hidden = group !== defaultTab ? "hidden" : "";
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

    this.filterCaps();
    this.updateCapCounter();
  },

  switchCapTab(kind) {
    document.querySelectorAll(".cap-tab").forEach((tab) => {
      tab.classList.toggle("active", tab.dataset.kind === kind);
    });
    document.querySelectorAll(".cap-panel").forEach((panel) => {
      panel.classList.toggle("hidden", panel.dataset.kindPanel !== kind);
    });
    const searchEl = document.getElementById("capSearch");
    if (searchEl) searchEl.value = "";
    this.filterCaps();
  },

  setCapsInKind(kind, checked) {
    const panel = document.querySelector(`.cap-panel[data-kind-panel="${kind}"]`);
    if (!panel) return;
    panel.querySelectorAll('input[name="cap"]:not(:disabled)').forEach((input) => {
      input.checked = checked;
    });
    this.updateCapCounter();
  },

  filterCaps() {
    const query = (document.getElementById("capSearch")?.value || "").trim().toLowerCase();
    const activePanel = document.querySelector(".cap-panel:not(.hidden)");
    if (!activePanel) return;

    const cards = activePanel.querySelectorAll(".cap-card");
    let visible = 0;
    cards.forEach((card) => {
      const match = !query || (card.dataset.search || "").includes(query);
      card.classList.toggle("is-filtered-out", !match);
      if (match) visible += 1;
    });

    const grid = activePanel.querySelector(".cap-grid");
    let emptyEl = activePanel.querySelector(".cap-grid-empty");
    if (!emptyEl && grid) {
      emptyEl = document.createElement("div");
      emptyEl.className = "cap-empty cap-grid-empty hidden";
      emptyEl.textContent = "没有匹配的能力";
      grid.after(emptyEl);
    }
    if (emptyEl) {
      emptyEl.classList.toggle("hidden", visible > 0 || cards.length === 0);
    }
  },

  updateCapCounter() {
    const counter = document.getElementById("capCounter");
    if (!counter) return;

    const allBoxes = document.querySelectorAll('#agentCapGroups input[name="cap"]');
    const checked = document.querySelectorAll('#agentCapGroups input[name="cap"]:checked');
    const configurableChecked = document.querySelectorAll(
      '#agentCapGroups input[name="cap"]:checked:not(:disabled)'
    );
    const total = allBoxes.length;
    const selected = checked.length;
    const selectedSet = new Set(Array.from(configurableChecked).map((b) => b.value));

    if (!total) {
      counter.textContent = "";
      return;
    }

    if (selected === 0) {
      counter.textContent = "无能力";
    } else if (selectionMatchesDefaults(selectedSet, state.capabilities || [])) {
      counter.textContent = "全部默认";
    } else {
      counter.textContent = `已选 ${selected} / ${total}`;
    }

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
    const allBoxes = document.querySelectorAll('#agentCapGroups input[name="cap"]:not(:disabled)');
    if (!allBoxes.length) return null;
    if (selected.length === 0) return [];
    const selectedSet = new Set(selected);
    if (selectionMatchesDefaults(selectedSet, state.capabilities || [])) return null;
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
        enabled: false,
        reference_count: 0,
        asset_count: 0,
      })),
    ];
    if (!skills.length) {
      container.innerHTML = `<div class="cap-empty">暂无可配置 Skill</div>`;
      counter.textContent = "未选择";
      return;
    }
    container.innerHTML = skills.map((skill) => {
      const checked = selectedSet.has(skill.code);
      const disabled = skill.enabled === false;
      const source = skill.source === "builtin" ? "内置" : skill.source === "upload" ? "上传" : "不可用";
      return `<label class="agent-skill-card${disabled ? " is-disabled" : ""}">
        <input type="checkbox" name="agentSkill" value="${escapeHtml(skill.code)}" ${checked ? "checked" : ""} ${disabled ? "disabled" : ""} />
        <span class="agent-skill-card-copy">
          <span class="agent-skill-card-title">${escapeHtml(skill.label)} <small>${escapeHtml(source)}</small></span>
          <span>${escapeHtml(skill.description || "暂无说明")}</span>
          <span class="agent-skill-card-meta">${Number(skill.reference_count || 0)} References · ${Number(skill.asset_count || 0)} Assets${disabled ? " · 当前不可用" : ""}</span>
        </span>
      </label>`;
    }).join("");
    this.updateSkillCounter();
  },

  updateSkillCounter() {
    const counter = document.getElementById("agentSkillCounter");
    if (!counter) return;
    const selected = document.querySelectorAll('#agentSkillChoices input[name="agentSkill"]:checked').length;
    counter.textContent = selected ? `已选 ${selected}` : "未选择";
  },

  getSelectedSkills() {
    return Array.from(
      document.querySelectorAll('#agentSkillChoices input[name="agentSkill"]:checked'),
    ).map((input) => input.value);
  },

  openCreate() {
    state.editingAgentId = null;
    document.getElementById("agentModalTitle").textContent = "新建 Agent";
    document.getElementById("agentModalId").textContent = "";
    document.getElementById("agentModalId").classList.add("hidden");
    document.getElementById("agentName").disabled = false;
    document.getElementById("agentName").value = "";
    populateAgentModelSelect(state.llmModels[0]?.id || null);
    document.getElementById("agentTemperature").value = "";
    document.getElementById("agentPrompt").value =
      "You are a helpful assistant. Use available tools when they help answer accurately.";
    this.renderCapabilityCheckboxes(null);
    this.renderSkillChoices([]);
    document.getElementById("testAgentBtn")?.classList.add("hidden");
    this.openEditor();
    this.schedulePromptEstimate(true);
  },

  async openEdit(agentId) {
    try {
      const agent = await apiFetch(`/api/v1/agents/${encodeURIComponent(agentId)}`);
      state.editingAgentId = agentId;
      document.getElementById("agentModalTitle").textContent = `编辑 Agent · ${agent.name}`;
      document.getElementById("agentModalId").textContent = `Agent ID：${agent.id || agentId}`;
      document.getElementById("agentModalId").classList.remove("hidden");
      document.getElementById("agentName").disabled = false;
      document.getElementById("agentName").value = agent.name;
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
      this.requestCloseEditor(true);
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
    if (!confirm(msg)) return;
    try {
      await apiFetch(`/api/v1/agents/${encodeURIComponent(agentId)}`, { method: "DELETE" });
      showToast("Agent 已删除");
      await refreshAfterSuccessfulMutation("Agent 已删除", () => this.refresh());
    } catch (err) {
      showToast(err.message, true);
    }
  },

  initCapSection() {
    const section = document.querySelector(".cap-section");
    if (!section || section.dataset.bound) return;
    section.dataset.bound = "1";

    section.addEventListener("click", (e) => {
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

    section.addEventListener("change", (e) => {
      if (e.target.matches('input[name="cap"]')) {
        this.updateCapCounter();
      }
    });

    document.getElementById("capSearch")?.addEventListener("input", () => this.filterCaps());
    document.getElementById("agentSkillChoices")?.addEventListener("change", () => this.updateSkillCounter());
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

document.addEventListener("DOMContentLoaded", () => {
  agentsAdmin.initCapSection();
  agentsAdmin.initPromptEstimate();
  agentsAdmin.initListControls();
  document.getElementById("newAgentBtn")?.addEventListener("click", () => agentsAdmin.openCreate());
  document.getElementById("agentForm")?.addEventListener("submit", (e) => agentsAdmin.save(e));
  document.getElementById("agentForm")?.addEventListener("input", () => { agentEditorDirty = true; });
  document.getElementById("agentForm")?.addEventListener("change", () => { agentEditorDirty = true; });
  document.getElementById("closeAgentModal")?.addEventListener("click", () => agentsAdmin.requestCloseEditor());
  document.getElementById("testAgentBtn")?.addEventListener("click", () => {
    if (!state.editingAgentId) return;
    if (!agentsAdmin.requestCloseEditor()) return;
    window.DeepAgentConsoleChat?.selectAgent(state.editingAgentId);
    window.DeepAgentConsole?.showPage("workspace");
  });
  agentsAdmin.refresh();
});
