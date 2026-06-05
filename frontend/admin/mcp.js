const mcpAdmin = {
  async refresh() {
    const tbody = document.getElementById("mcpTableBody");
    if (!tbody) return;
    tbody.innerHTML = `<tr><td colspan="6" class="empty-cell">加载中…</td></tr>`;
    try {
      const servers = await apiFetch("/api/v1/mcp");
      showDbBanner(false);
      if (!servers.length) {
        tbody.innerHTML = `<tr><td colspan="6" class="empty-cell">暂无 MCP 服务</td></tr>`;
        return;
      }
      tbody.innerHTML = servers
        .map(
          (s) => `
        <tr>
          <td><strong>${escapeHtml(s.name)}</strong></td>
          <td><span class="badge">${escapeHtml(s.transport)}</span></td>
          <td><span class="badge ${s.enabled ? "on" : "off"}">${s.enabled ? "是" : "否"}</span></td>
          <td>${escapeHtml(s.description || "—")}</td>
          <td>${escapeHtml(formatDate(s.updated_at))}</td>
          <td>
            <div class="row-actions">
              <button type="button" data-edit="${escapeHtml(s.name)}">编辑</button>
              <button type="button" class="danger" data-delete="${escapeHtml(s.name)}">删除</button>
            </div>
          </td>
        </tr>`
        )
        .join("");

      tbody.querySelectorAll("[data-edit]").forEach((btn) => {
        btn.addEventListener("click", () => this.openEdit(btn.dataset.edit));
      });
      tbody.querySelectorAll("[data-delete]").forEach((btn) => {
        btn.addEventListener("click", () => this.remove(btn.dataset.delete));
      });
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="6" class="empty-cell">${escapeHtml(err.message)}</td></tr>`;
    }
  },

  toggleTransportFields() {
    const transport = document.getElementById("mcpTransport").value;
    document.getElementById("mcpUrlFields").classList.toggle("hidden", transport !== "url");
    document.getElementById("mcpStdioFields").classList.toggle("hidden", transport !== "stdio");
  },

  openCreate() {
    state.editingMcp = null;
    document.getElementById("mcpModalTitle").textContent = "新建 MCP";
    document.getElementById("mcpName").disabled = false;
    document.getElementById("mcpName").value = "";
    document.getElementById("mcpTransport").value = "url";
    document.getElementById("mcpDescription").value = "";
    document.getElementById("mcpEnabled").checked = true;
    document.getElementById("mcpUrl").value = "";
    document.getElementById("mcpHeaders").value = "";
    document.getElementById("mcpCommand").value = "";
    document.getElementById("mcpArgs").value = "";
    document.getElementById("mcpEnv").value = "";
    document.getElementById("mcpCwd").value = "";
    this.toggleTransportFields();
    document.getElementById("mcpModal").showModal();
  },

  async openEdit(name) {
    try {
      const server = await apiFetch(`/api/v1/mcp/${encodeURIComponent(name)}`);
      state.editingMcp = name;
      document.getElementById("mcpModalTitle").textContent = `编辑 MCP · ${name}`;
      document.getElementById("mcpName").disabled = true;
      document.getElementById("mcpName").value = server.name;
      document.getElementById("mcpTransport").value = server.transport;
      document.getElementById("mcpDescription").value = server.description || "";
      document.getElementById("mcpEnabled").checked = server.enabled;

      if (server.transport === "url") {
        document.getElementById("mcpUrl").value = server.config.url || "";
        document.getElementById("mcpHeaders").value = server.config.headers
          ? JSON.stringify(server.config.headers, null, 2)
          : "";
      } else {
        document.getElementById("mcpCommand").value = server.config.command || "";
        document.getElementById("mcpArgs").value = JSON.stringify(server.config.args || []);
        document.getElementById("mcpEnv").value = server.config.env
          ? JSON.stringify(server.config.env, null, 2)
          : "";
        document.getElementById("mcpCwd").value = server.config.cwd || "";
      }

      this.toggleTransportFields();
      document.getElementById("mcpModal").showModal();
    } catch (err) {
      showToast(err.message, true);
    }
  },

  buildConfig() {
    const transport = document.getElementById("mcpTransport").value;
    if (transport === "url") {
      const config = { url: document.getElementById("mcpUrl").value.trim() };
      const headers = parseJsonOrEmpty(document.getElementById("mcpHeaders").value);
      if (headers) config.headers = headers;
      return config;
    }
    const config = {
      command: document.getElementById("mcpCommand").value.trim(),
      args: parseArgs(document.getElementById("mcpArgs").value),
    };
    const env = parseJsonOrEmpty(document.getElementById("mcpEnv").value);
    const cwd = document.getElementById("mcpCwd").value.trim();
    if (env) config.env = env;
    if (cwd) config.cwd = cwd;
    return config;
  },

  async save(event) {
    event.preventDefault();
    const transport = document.getElementById("mcpTransport").value;
    let config;
    try {
      config = this.buildConfig();
    } catch (err) {
      showToast(`配置 JSON 无效: ${err.message}`, true);
      return;
    }

    const payload = {
      transport,
      config,
      description: document.getElementById("mcpDescription").value.trim() || null,
      enabled: document.getElementById("mcpEnabled").checked,
    };

    try {
      if (state.editingMcp) {
        await apiFetch(`/api/v1/mcp/${encodeURIComponent(state.editingMcp)}`, {
          method: "PATCH",
          body: JSON.stringify(payload),
        });
        showToast("MCP 已更新，已热重载");
      } else {
        payload.name = document.getElementById("mcpName").value.trim();
        await apiFetch("/api/v1/mcp", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        showToast("MCP 已创建，已热重载");
      }
      document.getElementById("mcpModal").close();
      await this.refresh();
      await loadCapabilities();
    } catch (err) {
      showToast(err.message, true);
    }
  },

  async remove(name) {
    if (!confirm(`确定删除 MCP "${name}"？`)) return;
    try {
      await apiFetch(`/api/v1/mcp/${encodeURIComponent(name)}`, { method: "DELETE" });
      showToast("MCP 已删除，已热重载");
      await this.refresh();
      await loadCapabilities();
    } catch (err) {
      showToast(err.message, true);
    }
  },
};

window.mcpAdmin = mcpAdmin;

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("newMcpBtn")?.addEventListener("click", () => mcpAdmin.openCreate());
  document.getElementById("mcpForm")?.addEventListener("submit", (e) => mcpAdmin.save(e));
  document.getElementById("mcpTransport")?.addEventListener("change", () => mcpAdmin.toggleTransportFields());
  document.getElementById("closeMcpModal")?.addEventListener("click", () => {
    document.getElementById("mcpModal").close();
  });
  document.getElementById("cancelMcpBtn")?.addEventListener("click", () => {
    document.getElementById("mcpModal").close();
  });
});
