const channelAdminState = {
  accounts: [],
  agents: [],
  runtime: null,
  editingId: null,
  view: "accounts",
  applications: [],
  applicationLoadGeneration: 0,
  saving: false,
};

const CHANNEL_STATE_LABELS = {
  not_deployed: "未部署",
  unreachable: "运行时不可达",
  closed: "已关闭",
  connecting: "连接中",
  connected: "已连接",
  reconnecting: "重连中",
  conflict: "连接冲突",
  failed: "连接失败",
};

function channelRuntimeFor(account) {
  const runtime = channelAdminState.runtime;
  if (!runtime?.deployed) return { state: "not_deployed", message: runtime?.message || "渠道网关未部署" };
  if (!runtime?.reachable) return { state: "unreachable", message: runtime?.message || "渠道网关不可达" };
  if (!account.enabled) return { state: "closed", message: "渠道已关闭" };
  return runtime.accounts?.find((item) => item.account_id === account.id)
    || { state: "connecting", message: "等待渠道网关加载配置" };
}

function renderChannelAgentOptions(selectedId = "") {
  const select = document.getElementById("channelAgentId");
  if (!select) return;
  select.innerHTML = channelAdminState.agents.length
    ? channelAdminState.agents.map((agent) => `<option value="${escapeHtml(agent.id)}" ${agent.id === selectedId ? "selected" : ""}>${escapeHtml(agent.name)}</option>`).join("")
    : '<option value="">（请先创建 Agent）</option>';
  select.disabled = !channelAdminState.agents.length;
}

function setChannelSecretEditor({ maskedValue = "", secretSet = false, required = false } = {}) {
  const input = document.getElementById("channelSecret");
  const masked = String(maskedValue || "");
  input.dataset.maskedValue = masked;
  input.value = masked;
  input.type = masked ? "text" : "password";
  input.required = required;
  input.placeholder = masked || secretSet ? "留空则保持不变" : "请输入 Secret";
}

function channelUserDetailsSettings(settings, enabled, applicationId) {
  return { ...settings, wecom: { ...(settings?.wecom || {}), user_details_enabled: enabled, application_id: applicationId || null } };
}

function setChannelUserDetailsEnabled(enabled) {
  const button = document.getElementById("channelUserDetailsEnabled");
  if (!button) return;
  button.setAttribute("aria-checked", String(enabled));
  button.classList.toggle("on", enabled);
  button.classList.toggle("off", !enabled);
  document.getElementById("channelWecomApplicationField").hidden = !enabled;
  document.getElementById("channelWecomApplicationId").required = enabled;
}

function setChannelDeepfosEnabled(enabled) {
  const button = document.getElementById("channelDeepfosEnabled");
  button.setAttribute("aria-checked", String(enabled));
  button.classList.toggle("on", enabled);
  button.classList.toggle("off", !enabled);
  document.getElementById("channelDeepfosAppField").hidden = !enabled;
  const input = document.getElementById("channelDeepfosAppId");
  input.required = enabled;
  input.disabled = !enabled;
}

async function prepareChannelApplicationEditor(settings = {}) {
  document.getElementById("channelDeepfosAppId").value = settings?.deepfos?.app_id || "";
  setChannelDeepfosEnabled(settings?.deepfos?.enabled === true);
  const select = document.getElementById("channelWecomApplicationId");
  if (!select) return;
  const generation = ++channelAdminState.applicationLoadGeneration;
  const selectedId = settings?.wecom?.application_id || "";
  select.dataset.selectedId = selectedId;
  select.innerHTML = '<option value="" hidden disabled selected>正在加载企微应用…</option>';
  select.disabled = true;
  channelAdminState.applications = [];
  setChannelUserDetailsEnabled(settings?.wecom?.user_details_enabled === true);
  const hint = document.getElementById("channelWecomApplicationHint");
  hint.textContent = "";
  try {
    const items = await apiFetch("/api/v1/wecom-applications");
    if (generation !== channelAdminState.applicationLoadGeneration) return;
    channelAdminState.applications = items;
    select.innerHTML = `<option value="" hidden disabled ${!selectedId ? "selected" : ""}>请选择</option>${selectedId && !items.some(item => item.id === selectedId) ? `<option value="${escapeHtml(selectedId)}" hidden disabled selected>应用已不可用</option>` : ""}${items.map(item => `<option value="${escapeHtml(item.id)}" ${item.id === selectedId ? "selected" : ""}>${escapeHtml(item.name)}</option>`).join("")}`;
    select.disabled = !items.length;
    hint.textContent = !items.length ? "暂无企微应用，请先在渠道 → 企微应用中新建。" : "";
  } catch (error) {
    if (generation !== channelAdminState.applicationLoadGeneration) return;
    hint.textContent = `企微应用读取失败：${error.message}`;
    select.innerHTML = '<option value="" hidden disabled selected>企微应用加载失败</option>';
  }
}

const channelsAdmin = {
  selectView(view) {
    if (!["accounts", "applications"].includes(view)) return;
    if (view === "accounts" && window.wecomApplicationsAdmin?.deactivate() === false) return;
    channelAdminState.view = view;
    this.renderView();
    return view === "applications" ? window.wecomApplicationsAdmin?.refresh() : undefined;
  },
  renderView() {
    const applications = channelAdminState.view === "applications";
    document.querySelectorAll("[data-channel-view]").forEach(button => {
      const active = button.dataset.channelView === channelAdminState.view;
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", String(active));
      button.tabIndex = active ? 0 : -1;
    });
    const visibility = { channelAccountsView: applications, channelAccountActions: applications, channelRuntimeBanner: applications, wecomApplicationsView: !applications, newWecomApplicationBtn: !applications };
    Object.entries(visibility).forEach(([id, hidden]) => { const node = document.getElementById(id); if (node) node.hidden = hidden; });
  },
  async refresh() {
    if (channelAdminState.view === "applications") return window.wecomApplicationsAdmin?.refresh();
    const tbody = document.getElementById("channelTableBody");
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="7" class="empty-cell">正在加载渠道…</td></tr>';
    try {
      const [accounts, agents, runtime] = await Promise.all([
        apiFetch("/api/v1/channel-accounts").then((accounts) => {
          // Association display does not depend on Gateway deployment/health.
          window.agentsAdmin?.setChannelAccounts(accounts);
          return accounts;
        }),
        apiFetch("/api/v1/agents"),
        apiFetch("/api/v1/channel-runtime"),
      ]);
      channelAdminState.accounts = accounts;
      channelAdminState.agents = agents;
      channelAdminState.runtime = runtime;
      this.renderRuntime();
      this.renderTable();
      showDbBanner(false);
    } catch (error) {
      tbody.innerHTML = `<tr><td colspan="7" class="empty-cell">${escapeHtml(error.message)}</td></tr>`;
    }
  },

  renderRuntime() {
    const banner = document.getElementById("channelRuntimeBanner");
    const runtime = channelAdminState.runtime;
    if (!banner || !runtime) return;
    const state = !runtime.deployed ? "not_deployed" : runtime.reachable ? "connected" : "unreachable";
    banner.dataset.state = state;
    const stateLabel = { not_deployed: "未部署", connected: "运行中", unreachable: "不可达" }[state];
    banner.querySelector("strong").textContent = `网关：${stateLabel}`;
    banner.title = runtime.message || "";
  },

  async openCommands() {
    const dialog = document.getElementById("channelCommandsModal");
    const content = document.getElementById("channelCommandsContent");
    const retry = document.getElementById("retryChannelCommandsBtn");
    if (!dialog || !content || !retry) return;
    if (!dialog.open) dialog.showModal();
    const requestId = (this.commandsRequestId || 0) + 1;
    this.commandsRequestId = requestId;
    content.innerHTML = '<p class="field-hint" role="status">正在加载快捷命令…</p>';
    retry.hidden = true;
    try {
      const catalog = await apiFetch("/api/v1/channel-commands");
      if (this.commandsRequestId !== requestId) return;
      if (!Array.isArray(catalog?.commands) || !Array.isArray(catalog?.notes)) {
        throw new Error("快捷命令说明格式不正确，请重试。");
      }
      content.innerHTML = `<dl class="channel-command-list">${catalog.commands.map((item) =>
        `<div><dt><code>${escapeHtml(item.command)}</code></dt><dd>${escapeHtml(item.description)}</dd></div>`,
      ).join("")}</dl><ul class="channel-command-notes">${catalog.notes.map((note) => `<li>${escapeHtml(note)}</li>`).join("")}</ul>`;
    } catch (error) {
      if (this.commandsRequestId !== requestId) return;
      content.innerHTML = `<p class="channel-command-error" role="alert">${escapeHtml(error.message)}</p>`;
      retry.hidden = false;
    }
  },

  renderTable() {
    const tbody = document.getElementById("channelTableBody");
    if (!tbody) return;
    if (!channelAdminState.accounts.length) {
      tbody.innerHTML = '<tr><td colspan="7" class="empty-cell">暂无渠道配置</td></tr>';
      return;
    }
    tbody.innerHTML = channelAdminState.accounts.map((account) => {
      const runtime = channelRuntimeFor(account);
      return `<tr class="channel-list-row" data-open-channel="${escapeHtml(account.id)}" tabindex="0">
        <td class="channel-enabled-cell"><button type="button" class="model-enabled-switch ${account.enabled ? "on" : "off"}" data-toggle-channel="${escapeHtml(account.id)}" role="switch" aria-checked="${account.enabled}" title="${account.enabled ? "停用渠道" : "启用渠道"}" aria-label="${account.enabled ? "停用渠道" : "启用渠道"}"><span></span></button></td>
        <td><strong>${escapeHtml(account.name)}</strong></td>
        <td class="channel-type-cell"><span class="channel-type-label"><img src="./assets/channel-logos/wecom.png" width="18" height="18" alt="" />企微（WS）</span></td>
        <td>${escapeHtml(account.agent_name || account.agent_id)}</td>
        <td><span class="channel-status" data-state="${escapeHtml(runtime.state)}" title="${escapeHtml(runtime.message || "")}"><span></span>${escapeHtml(CHANNEL_STATE_LABELS[runtime.state] || runtime.state)}</span></td>
        <td>${escapeHtml(formatDate(account.updated_at))}</td>
        <td><div class="row-actions channel-row-actions compact-icon-actions">
          <button class="icon-btn" type="button" data-channel-test="${escapeHtml(account.id)}" title="测试连接" aria-label="测试连接"><i data-lucide="plug-zap"></i></button>
          <button class="icon-btn danger" type="button" data-channel-delete="${escapeHtml(account.id)}" title="删除渠道" aria-label="删除渠道"><i data-lucide="trash-2"></i></button>
        </div></td>
      </tr>`;
    }).join("");
    tbody.querySelectorAll("[data-open-channel]").forEach((row) => {
      const open = (event) => {
        if (event.target.closest("a, button")) return;
        this.openEdit(row.dataset.openChannel);
      };
      row.addEventListener("click", open);
      row.addEventListener("keydown", (event) => {
        if (event.target.closest("a, button")) return;
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          this.openEdit(row.dataset.openChannel);
        }
      });
    });
    tbody.querySelectorAll(".channel-row-actions").forEach((actions) => {
      actions.addEventListener("click", (event) => event.stopPropagation());
      actions.addEventListener("keydown", (event) => event.stopPropagation());
    });
    tbody.querySelectorAll("[data-channel-test]").forEach((button) => button.addEventListener("click", (event) => {
      event.stopPropagation();
      this.test(button.dataset.channelTest, button);
    }));
    tbody.querySelectorAll("[data-toggle-channel]").forEach((button) => button.addEventListener("click", (event) => {
      event.stopPropagation();
      this.toggleEnabled(button.dataset.toggleChannel, button);
    }));
    tbody.querySelectorAll("[data-channel-delete]").forEach((button) => button.addEventListener("click", (event) => {
      event.stopPropagation();
      this.remove(button.dataset.channelDelete);
    }));
    window.lucide?.createIcons();
  },

  openCreate() {
    channelAdminState.editingId = null;
    document.getElementById("channelAccountModalTitle").textContent = "新建企微渠道";
    document.getElementById("channelName").value = "";
    renderChannelAgentOptions(channelAdminState.agents[0]?.id || "");
    document.getElementById("channelBotId").value = "";
    document.getElementById("channelSessionUserId").value = "";
    setChannelSecretEditor({ required: true });
    void prepareChannelApplicationEditor();
    AdminFormFeedback.clear(document.getElementById("channelAccountForm"));
    document.getElementById("channelAccountModal").showModal();
  },

  openEdit(accountId) {
    const account = channelAdminState.accounts.find((item) => item.id === accountId);
    if (!account) return;
    channelAdminState.editingId = accountId;
    document.getElementById("channelAccountModalTitle").textContent = account.name;
    document.getElementById("channelName").value = account.name;
    renderChannelAgentOptions(account.agent_id);
    document.getElementById("channelBotId").value = account.bot_id;
    document.getElementById("channelSessionUserId").value = account.session_user_id;
    setChannelSecretEditor({ maskedValue: account.secret_masked, secretSet: account.secret_set });
    void prepareChannelApplicationEditor(account.settings);
    AdminFormFeedback.clear(document.getElementById("channelAccountForm"));
    document.getElementById("channelAccountModal").showModal();
  },

  async save(event) {
    event.preventDefault();
    const form = event.currentTarget;
    if (channelAdminState.saving || !form.reportValidity()) return;
    const editingId = channelAdminState.editingId;
    const account = channelAdminState.accounts.find(item => item.id === editingId);
    const enabled = document.getElementById("channelUserDetailsEnabled").getAttribute("aria-checked") === "true";
    const applicationSelect = document.getElementById("channelWecomApplicationId");
    const applicationId = applicationSelect.disabled ? applicationSelect.dataset.selectedId : applicationSelect.value;
    if (enabled && !channelAdminState.applications.some(item => item.id === applicationId)) {
      AdminFormFeedback.show(form, "请选择有效的企微应用；没有应用时，请先创建。");
      return;
    }
    const payload = {
      settings: {
        ...channelUserDetailsSettings(account?.settings || {}, enabled, applicationId),
        deepfos: {
          ...(account?.settings?.deepfos || {}),
          enabled: document.getElementById("channelDeepfosEnabled").getAttribute("aria-checked") === "true",
          app_id: document.getElementById("channelDeepfosAppId").value.trim() || null,
        },
      },
      name: document.getElementById("channelName").value.trim(),
      bot_id: document.getElementById("channelBotId").value.trim(),
      agent_id: document.getElementById("channelAgentId").value,
      session_user_id: document.getElementById("channelSessionUserId").value.trim(),
    };
    const secretInput = document.getElementById("channelSecret");
    const secret = secretInput.value.trim();
    if (secret && secret !== secretInput.dataset.maskedValue) payload.secret = secret;
    if (!editingId) payload.enabled = false;
    channelAdminState.saving = true;
    form.querySelectorAll("button").forEach(button => { button.disabled = true; });
    try {
      await apiFetch(editingId ? `/api/v1/channel-accounts/${editingId}` : "/api/v1/channel-accounts", {
        method: editingId ? "PUT" : "POST",
        body: JSON.stringify(payload),
      });
      document.getElementById("channelAccountModal").close();
      showToast(editingId ? "渠道已更新" : "渠道已创建");
      await this.refresh();
    } catch (error) {
      AdminFormFeedback.show(form, error.message);
    } finally {
      channelAdminState.saving = false;
      form.querySelectorAll("button").forEach(button => { button.disabled = false; });
    }
  },

  async test(accountId, button) {
    button.disabled = true;
    try {
      const result = await apiFetch(`/api/v1/channel-accounts/${accountId}/test-connection`, { method: "POST" });
      showToast(result.warning ? `${result.message}；${result.warning}` : result.message, !result.success);
      await this.refresh();
    } catch (error) {
      showToast(error.message, true);
    } finally {
      button.disabled = false;
    }
  },

  async toggleEnabled(accountId, button) {
    const account = channelAdminState.accounts.find((item) => item.id === accountId);
    if (!account) return;
    button.disabled = true;
    try {
      await apiFetch(`/api/v1/channel-accounts/${accountId}`, {
        method: "PUT",
        body: JSON.stringify({ enabled: !account.enabled }),
      });
      showToast(account.enabled ? "渠道已停用" : "渠道已启用");
      await this.refresh();
    } catch (error) {
      showToast(error.message, true);
      button.disabled = false;
    }
  },

  async remove(accountId) {
    const account = channelAdminState.accounts.find((item) => item.id === accountId);
    if (!account) return;
    if (!(await confirmAction({
      title: "删除渠道配置？",
      message: `只有尚未产生会话和运行记录的渠道可以删除。当前对象：${account.name}`,
      icon: "trash-2",
      tone: "danger",
      confirmLabel: "删除",
    }))) return;
    try {
      await apiFetch(`/api/v1/channel-accounts/${accountId}`, { method: "DELETE" });
      showToast("渠道已删除");
      await this.refresh();
    } catch (error) {
      showToast(error.message, true);
    }
  },
};

function bindChannelCreateMenu() {
  const trigger = document.getElementById("newChannelBtn");
  const menu = document.getElementById("newChannelMenu");
  const option = document.getElementById("createWecomChannelBtn");
  if (!trigger || !menu || !option) return;
  const close = (restoreFocus = false) => {
    menu.hidden = true;
    trigger.setAttribute("aria-expanded", "false");
    if (restoreFocus) trigger.focus();
  };
  const open = (focusOption = false) => {
    menu.hidden = false;
    trigger.setAttribute("aria-expanded", "true");
    if (focusOption) option.focus();
  };
  trigger.addEventListener("click", () => menu.hidden ? open() : close());
  trigger.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    open(true);
  });
  option.addEventListener("click", () => {
    close();
    channelsAdmin.openCreate();
  });
  menu.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp" || event.key === "Home" || event.key === "End") {
      event.preventDefault();
      option.focus();
    }
  });
  document.addEventListener("keydown", (event) => {
    if (menu.hidden) return;
    if (event.key === "Escape") { event.preventDefault(); close(true); }
    if (event.key === "Tab") close();
  });
  document.addEventListener("click", (event) => {
    if (!menu.hidden && !trigger.contains(event.target) && !menu.contains(event.target)) close();
  });
  document.addEventListener("focusin", (event) => {
    if (!menu.hidden && !trigger.contains(event.target) && !menu.contains(event.target)) close();
  });
}

window.channelsAdmin = channelsAdmin;

document.addEventListener("DOMContentLoaded", () => {
  bindChannelCreateMenu();
  const viewButtons = Array.from(document.querySelectorAll("[data-channel-view]"));
  viewButtons.forEach((button, index) => {
    button.addEventListener("click", () => channelsAdmin.selectView(button.dataset.channelView));
    button.addEventListener("keydown", event => {
      const next = event.key === "Home" ? 0 : event.key === "End" ? viewButtons.length - 1 : ["ArrowLeft", "ArrowRight"].includes(event.key) ? (index + 1) % viewButtons.length : -1;
      if (next < 0) return;
      event.preventDefault(); channelsAdmin.selectView(viewButtons[next].dataset.channelView); viewButtons[next].focus();
    });
  });
  document.getElementById("channelDeepfosEnabled")?.addEventListener("click", event => setChannelDeepfosEnabled(event.currentTarget.getAttribute("aria-checked") !== "true"));
  document.getElementById("channelUserDetailsEnabled")?.addEventListener("click", event => setChannelUserDetailsEnabled(event.currentTarget.getAttribute("aria-checked") !== "true"));
  document.getElementById("clearChannelWecomApplicationBtn")?.addEventListener("click", () => {
    const select = document.getElementById("channelWecomApplicationId");
    select.value = ""; select.dataset.selectedId = "";
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  document.getElementById("refreshChannelsBtn")?.addEventListener("click", () => channelsAdmin.refresh());
  document.getElementById("channelCommandsBtn")?.addEventListener("click", () => channelsAdmin.openCommands());
  document.getElementById("retryChannelCommandsBtn")?.addEventListener("click", () => channelsAdmin.openCommands());
  document.getElementById("closeChannelCommandsModal")?.addEventListener("click", () => document.getElementById("channelCommandsModal").close());
  document.getElementById("dismissChannelCommandsBtn")?.addEventListener("click", () => document.getElementById("channelCommandsModal").close());
  document.getElementById("channelAccountForm")?.addEventListener("submit", (event) => channelsAdmin.save(event));
  document.getElementById("channelSecret")?.addEventListener("focus", (event) => {
    const input = event.currentTarget;
    if (input.value === input.dataset.maskedValue) input.select();
  });
  document.getElementById("channelSecret")?.addEventListener("input", (event) => {
    const input = event.currentTarget;
    input.type = input.value && input.value === input.dataset.maskedValue ? "text" : "password";
  });
  const closeAccount = () => { if (!channelAdminState.saving) document.getElementById("channelAccountModal").close(); };
  document.getElementById("closeChannelAccountModal")?.addEventListener("click", closeAccount);
  document.getElementById("cancelChannelAccountBtn")?.addEventListener("click", closeAccount);
  document.getElementById("channelAccountModal")?.addEventListener("cancel", event => { event.preventDefault(); closeAccount(); });
  document.getElementById("channelAccountModal")?.addEventListener("close", () => { channelAdminState.applicationLoadGeneration++; document.getElementById("channelSecret").value = ""; });
});
