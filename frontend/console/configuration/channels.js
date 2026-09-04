const channelAdminState = {
  accounts: [],
  agents: [],
  runtime: null,
  editingId: null,
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
  if (!runtime?.deployed) return { state: "not_deployed", message: runtime?.message || "企微网关未部署" };
  if (!runtime?.reachable) return { state: "unreachable", message: runtime?.message || "企微网关不可达" };
  if (!account.enabled) return { state: "closed", message: "渠道已关闭" };
  return runtime.accounts?.find((item) => item.account_id === account.id)
    || { state: "connecting", message: "等待企微网关加载配置" };
}

function renderChannelAgentOptions(selectedId = "") {
  const select = document.getElementById("channelAgentId");
  if (!select) return;
  select.innerHTML = channelAdminState.agents.length
    ? channelAdminState.agents.map((agent) => `<option value="${escapeHtml(agent.id)}" ${agent.id === selectedId ? "selected" : ""}>${escapeHtml(agent.name)}</option>`).join("")
    : '<option value="">（请先创建 Agent）</option>';
  select.disabled = !channelAdminState.agents.length;
}

const channelsAdmin = {
  async refresh() {
    const tbody = document.getElementById("channelTableBody");
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="6" class="empty-cell">正在加载渠道…</td></tr>';
    try {
      const [accounts, agents, runtime] = await Promise.all([
        apiFetch("/api/v1/channel-accounts"),
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
      tbody.innerHTML = `<tr><td colspan="6" class="empty-cell">${escapeHtml(error.message)}</td></tr>`;
    }
  },

  renderRuntime() {
    const banner = document.getElementById("channelRuntimeBanner");
    const runtime = channelAdminState.runtime;
    if (!banner || !runtime) return;
    banner.dataset.state = runtime.state;
    const stateLabel = CHANNEL_STATE_LABELS[runtime.state] || runtime.state || "未知";
    banner.querySelector("strong").textContent = `企微网关：${stateLabel}`;
    banner.title = runtime.message || "";
  },

  renderTable() {
    const tbody = document.getElementById("channelTableBody");
    if (!tbody) return;
    if (!channelAdminState.accounts.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="empty-cell">暂无渠道配置</td></tr>';
      return;
    }
    tbody.innerHTML = channelAdminState.accounts.map((account) => {
      const runtime = channelRuntimeFor(account);
      return `<tr class="channel-list-row" data-open-channel="${escapeHtml(account.id)}" tabindex="0">
        <td><strong>${escapeHtml(account.name)}</strong></td>
        <td class="channel-type-cell">企微（WS）</td>
        <td>${escapeHtml(account.agent_name || account.agent_id)}</td>
        <td><span class="channel-status" data-state="${escapeHtml(runtime.state)}" title="${escapeHtml(runtime.message || "")}"><span></span>${escapeHtml(CHANNEL_STATE_LABELS[runtime.state] || runtime.state)}</span></td>
        <td>${escapeHtml(formatDate(account.updated_at))}</td>
        <td><div class="row-actions channel-row-actions compact-icon-actions">
          <button type="button" class="model-enabled-switch ${account.enabled ? "on" : "off"}" data-toggle-channel="${escapeHtml(account.id)}" role="switch" aria-checked="${account.enabled}" title="${account.enabled ? "停用渠道" : "启用渠道"}" aria-label="${account.enabled ? "停用渠道" : "启用渠道"}"><span></span></button>
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
    const secret = document.getElementById("channelSecret");
    secret.value = "";
    secret.required = true;
    secret.placeholder = "请输入 Secret";
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
    const secret = document.getElementById("channelSecret");
    secret.value = "";
    secret.required = false;
    secret.placeholder = account.secret_set ? "已配置，留空则保持不变" : "请输入 Secret";
    AdminFormFeedback.clear(document.getElementById("channelAccountForm"));
    document.getElementById("channelAccountModal").showModal();
  },

  async save(event) {
    event.preventDefault();
    const form = event.currentTarget;
    if (!form.reportValidity()) return;
    const editingId = channelAdminState.editingId;
    const payload = {
      name: document.getElementById("channelName").value.trim(),
      bot_id: document.getElementById("channelBotId").value.trim(),
      agent_id: document.getElementById("channelAgentId").value,
      session_user_id: document.getElementById("channelSessionUserId").value.trim(),
    };
    const secret = document.getElementById("channelSecret").value.trim();
    if (secret) payload.secret = secret;
    if (!editingId) payload.enabled = false;
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

window.channelsAdmin = channelsAdmin;

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("newChannelBtn")?.addEventListener("click", () => channelsAdmin.openCreate());
  document.getElementById("refreshChannelsBtn")?.addEventListener("click", () => channelsAdmin.refresh());
  document.getElementById("channelAccountForm")?.addEventListener("submit", (event) => channelsAdmin.save(event));
  document.getElementById("closeChannelAccountModal")?.addEventListener("click", () => document.getElementById("channelAccountModal").close());
  document.getElementById("cancelChannelAccountBtn")?.addEventListener("click", () => document.getElementById("channelAccountModal").close());
});
