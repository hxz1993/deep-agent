function chatUrlForAgent(id) {
  const base = typeof getBaseUrl === "function" ? getBaseUrl() : "/";
  const root = base.replace(/\/$/, "");
  return `${root}/?agent_id=${encodeURIComponent(id)}`;
}

const KIND_LABELS = {
  native: "内置",
  skill: "Skills",
  mcp: "MCP",
  ui: "UI（内置）",
  "ui-client": "UI 工具",
};

const KIND_ORDER = ["native", "ui", "ui-client", "skill", "mcp"];

function capDisplayName(name, kind) {
  if (kind === "mcp" && name.startsWith("mcp:")) {
    return name.slice(4);
  }
  if (kind === "ui-client" && name.startsWith("ui:")) {
    return name.slice(3);
  }
  if (kind === "ui-client" && name.startsWith("ui-builtin:")) {
    return name.slice(11);
  }
  return name;
}

function capKindLabel(cap) {
  if (cap.kind !== "ui-client") return KIND_LABELS[cap.kind] || cap.kind;
  if (cap.source === "builtin") return "内置";
  if (cap.source === "manual") return "MANUAL";
  if (cap.source === "mcp") return "MCP";
  return KIND_LABELS[cap.kind];
}

function isCapabilitySelected(cap, selectedSet) {
  return Boolean(cap.locked) || selectedSet.has(cap.name);
}

function groupCapabilities(caps) {
  const byKind = {};
  for (const c of caps) {
    (byKind[c.kind] ||= []).push(c);
  }
  const ordered = KIND_ORDER.filter((k) => byKind[k]?.length);
  for (const k of Object.keys(byKind)) {
    if (!ordered.includes(k)) ordered.push(k);
  }
  return { byKind, kinds: ordered };
}

function defaultEnabledCapabilityNames(caps) {
  return (caps || []).filter((c) => c.default_enabled && !c.locked).map((c) => c.name);
}

function formatCapabilitiesPreview(capabilities) {
  if (capabilities == null) return "（全部默认）";
  if (!capabilities.length) return "（无）";
  return capabilities.join(", ");
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
  async refresh() {
    const tbody = document.getElementById("agentsTableBody");
    if (!tbody) return;
    tbody.innerHTML = `<tr><td colspan="5" class="empty-cell">加载中…</td></tr>`;
    try {
      const agents = await apiFetch("/api/v1/agents");
      showDbBanner(false);
      if (!agents.length) {
        tbody.innerHTML = `<tr><td colspan="5" class="empty-cell">暂无 Agent</td></tr>`;
        return;
      }
      tbody.innerHTML = agents
        .map(
          (a) => `
        <tr>
          <td><strong>${escapeHtml(a.name)}</strong></td>
          <td>${escapeHtml(a.model?.name || "—")}</td>
          <td><div class="cap-preview">${escapeHtml(formatCapabilitiesPreview(a.capabilities))}</div></td>
          <td>${escapeHtml(formatDate(a.updated_at))}</td>
          <td>
            <div class="row-actions">
              <a class="btn-link" href="${chatUrlForAgent(a.id)}" target="_blank" rel="noopener">发起对话</a>
              <button type="button" data-edit="${escapeHtml(a.id)}">编辑</button>
              <button type="button" class="danger" data-delete="${escapeHtml(a.id)}" data-name="${escapeHtml(a.name)}">删除</button>
            </div>
          </td>
        </tr>`
        )
        .join("");

      tbody.querySelectorAll("[data-edit]").forEach((btn) => {
        btn.addEventListener("click", () => this.openEdit(btn.dataset.edit));
      });
      tbody.querySelectorAll("[data-delete]").forEach((btn) => {
        btn.addEventListener("click", () => this.remove(btn.dataset.delete, btn.dataset.name));
      });
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="5" class="empty-cell">${escapeHtml(err.message)}</td></tr>`;
    }
  },

  renderCapCard(cap, selectedSet) {
    const checked = isCapabilitySelected(cap, selectedSet);
    const displayName = capDisplayName(cap.name, cap.kind);
    const kindLabel = capKindLabel(cap);
    const toolCount = cap.tools?.length || 0;
    const searchText = `${displayName} ${cap.description || ""} ${cap.name}`.toLowerCase();
    const locked = Boolean(cap.locked);

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

    return `<article class="cap-card kind-${escapeHtml(cap.kind)}${locked ? " is-locked" : ""}" data-cap-name="${escapeHtml(cap.name)}" data-search="${escapeHtml(searchText)}">
      <div class="cap-card-header">
        <label class="cap-switch" title="${locked ? "默认启用，不可关闭" : checked ? "已启用" : "未启用"}">
          <input type="checkbox" name="cap" value="${escapeHtml(cap.name)}" ${checked ? "checked" : ""} ${locked ? "disabled data-locked=\"true\"" : ""} />
          <span class="cap-switch-ui"></span>
        </label>
        <div class="cap-card-info">
          <div class="cap-card-title">
          <span class="kind-badge kind-${escapeHtml(cap.kind)} source-${escapeHtml(cap.source || "unknown")}">${escapeHtml(kindLabel)}</span>
            <span>${escapeHtml(displayName)}</span>
          </div>
          ${cap.description ? `<p class="cap-card-desc">${escapeHtml(cap.description)}</p>` : ""}
        </div>
      </div>
      ${toolsHtml}
    </article>`;
  },

  countSelectedInKind(kind, selectedSet) {
    const caps = (state.capabilities || []).filter((c) => c.kind === kind);
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
    const { byKind, kinds } = groupCapabilities(caps);
    const defaultTab = kinds.find((k) => k !== "mcp") || kinds[0];

    tabsEl.innerHTML = kinds
      .map((kind) => {
        const total = byKind[kind].length;
        const selectedCount = this.countSelectedInKind(kind, selectedSet);
        const active = kind === defaultTab ? "active" : "";
        const label = KIND_LABELS[kind] || kind;
        return `<button type="button" class="cap-tab ${active}" data-kind="${escapeHtml(kind)}">
          ${escapeHtml(label)}
          <span class="cap-tab-badge" data-kind-badge="${escapeHtml(kind)}">${selectedCount}/${total}</span>
        </button>`;
      })
      .join("");

    container.innerHTML = kinds
      .map((kind) => {
        const cards = byKind[kind].map((c) => this.renderCapCard(c, selectedSet)).join("");
        const hidden = kind !== defaultTab ? "hidden" : "";
        const label = KIND_LABELS[kind] || kind;
        return `<div class="cap-panel ${hidden}" data-kind-panel="${escapeHtml(kind)}">
          <div class="cap-panel-toolbar">
            <button type="button" class="btn-link cap-select-all" data-kind="${escapeHtml(kind)}">全选 ${escapeHtml(label)}</button>
            <button type="button" class="btn-link cap-select-none" data-kind="${escapeHtml(kind)}">全不选</button>
          </div>
          <div class="cap-grid" data-kind-grid="${escapeHtml(kind)}">${cards}</div>
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
      const kind = badge.dataset.kindBadge;
      const kindTotal = (state.capabilities || []).filter((c) => c.kind === kind).length;
      const kindSelected = (state.capabilities || []).filter((c) => c.kind === kind && selectedSet.has(c.name)).length;
      badge.textContent = `${kindSelected}/${kindTotal}`;
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
    document.getElementById("agentModal").showModal();
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
      document.getElementById("agentModal").showModal();
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
      document.getElementById("agentModal").close();
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
  },
};

window.agentsAdmin = agentsAdmin;

document.addEventListener("DOMContentLoaded", () => {
  agentsAdmin.initCapSection();
  document.getElementById("newAgentBtn")?.addEventListener("click", () => agentsAdmin.openCreate());
  document.getElementById("agentForm")?.addEventListener("submit", (e) => agentsAdmin.save(e));
  document.getElementById("closeAgentModal")?.addEventListener("click", () => {
    document.getElementById("agentModal").close();
  });
  document.getElementById("cancelAgentBtn")?.addEventListener("click", () => {
    document.getElementById("agentModal").close();
  });
  agentsAdmin.refresh();
});
