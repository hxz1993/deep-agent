const MCP_TRANSPORT_LABELS = {
  url: "url",
  stdio: "stdio",
  builtin: "builtin",
};

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
          <td><span class="badge">${escapeHtml(MCP_TRANSPORT_LABELS[s.transport] || s.transport)}</span></td>
          <td><span class="badge ${s.enabled ? "on" : "off"}">${s.enabled ? "是" : "否"}</span></td>
          <td>${escapeHtml(s.description || "—")}</td>
          <td>${escapeHtml(formatDate(s.updated_at))}</td>
          <td>
            <div class="row-actions">
              <button type="button" data-refresh-ui="${escapeHtml(s.name)}" title="拉取 ui_tools_list">刷新 UI</button>
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
      tbody.querySelectorAll("[data-refresh-ui]").forEach((btn) => {
        btn.addEventListener("click", () => this.refreshUiTools(btn.dataset.refreshUi));
      });
      tbody.querySelectorAll("[data-delete]").forEach((btn) => {
        btn.addEventListener("click", () => this.remove(btn.dataset.delete));
      });
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="6" class="empty-cell">${escapeHtml(err.message)}</td></tr>`;
    }
  },

  fillUiToolsFields(config) {
    const ui = (config && config.ui_tools) || {};
    document.getElementById("mcpUiToolsEnabled").checked = ui.enabled !== false;
    document.getElementById("mcpUiToolsMode").value = ui.mode || "tool";
    document.getElementById("mcpUiToolsRefresh").value = ui.refresh_seconds || 300;
  },

  readUiToolsConfig() {
    const refresh = parseInt(document.getElementById("mcpUiToolsRefresh").value, 10);
    return {
      enabled: document.getElementById("mcpUiToolsEnabled").checked,
      mode: document.getElementById("mcpUiToolsMode").value,
      refresh_seconds: Number.isFinite(refresh) && refresh >= 60 ? refresh : 300,
    };
  },

  async refreshUiTools(name) {
    try {
      const result = await apiFetch(
        `/api/v1/mcp/${encodeURIComponent(name)}/ui-tools/refresh`,
        { method: "POST" }
      );
      const errNote = result.errors?.length ? `；${result.errors.join("; ")}` : "";
      if (result.discovered === 0 && !result.upserted && !result.removed && !errNote) {
        showToast("内置 MCP 不参与 UI 工具发现");
        return;
      }
      showToast(
        `UI 发现：${result.discovered} 条，写入 ${result.upserted}，移除 ${result.removed}${errNote}`,
        Boolean(result.errors?.length)
      );
      if (window.uiToolsAdmin) await window.uiToolsAdmin.refresh();
      await loadCapabilities();
    } catch (err) {
      showToast(err.message, true);
    }
  },

  setTransportSelectMode(mode) {
    const select = document.getElementById("mcpTransport");
    const builtinOption = select?.querySelector('option[value="builtin"]');
    if (!select || !builtinOption) return;

    if (mode === "create") {
      builtinOption.hidden = true;
      builtinOption.disabled = true;
      select.disabled = false;
      return;
    }
    if (mode === "edit-builtin") {
      builtinOption.hidden = false;
      builtinOption.disabled = false;
      select.disabled = true;
      return;
    }
    builtinOption.hidden = true;
    builtinOption.disabled = true;
    select.disabled = false;
  },

  toggleTransportFields() {
    const transport = document.getElementById("mcpTransport").value;
    document.getElementById("mcpUrlFields").classList.toggle("hidden", transport !== "url");
    document.getElementById("mcpStdioFields").classList.toggle("hidden", transport !== "stdio");
    document.getElementById("mcpBuiltinFields").classList.toggle("hidden", transport !== "builtin");
    document.getElementById("mcpEnvCwdFields").classList.toggle(
      "hidden",
      transport !== "stdio" && transport !== "builtin"
    );
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
    this.fillUiToolsFields({});
    this.setTransportSelectMode("create");
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
        document.getElementById("mcpCommand").value = "";
        document.getElementById("mcpArgs").value = "";
        document.getElementById("mcpEnv").value = "";
        document.getElementById("mcpCwd").value = "";
      } else if (server.transport === "builtin") {
        document.getElementById("mcpUrl").value = "";
        document.getElementById("mcpHeaders").value = "";
        document.getElementById("mcpCommand").value = "";
        document.getElementById("mcpArgs").value = "";
        document.getElementById("mcpEnv").value = server.config.env
          ? JSON.stringify(server.config.env, null, 2)
          : "";
        document.getElementById("mcpCwd").value = server.config.cwd || "";
      } else {
        document.getElementById("mcpUrl").value = "";
        document.getElementById("mcpHeaders").value = "";
        document.getElementById("mcpCommand").value = server.config.command || "";
        document.getElementById("mcpArgs").value = JSON.stringify(server.config.args || []);
        document.getElementById("mcpEnv").value = server.config.env
          ? JSON.stringify(server.config.env, null, 2)
          : "";
        document.getElementById("mcpCwd").value = server.config.cwd || "";
      }

      this.fillUiToolsFields(server.config);
      this.setTransportSelectMode(server.transport === "builtin" ? "edit-builtin" : "edit");
      this.toggleTransportFields();
      document.getElementById("mcpModal").showModal();
    } catch (err) {
      showToast(err.message, true);
    }
  },

  buildConfig() {
    const transport = document.getElementById("mcpTransport").value;
    let config;
    if (transport === "url") {
      config = { url: document.getElementById("mcpUrl").value.trim() };
      const headers = parseJsonOrEmpty(document.getElementById("mcpHeaders").value);
      if (headers) config.headers = headers;
    } else if (transport === "builtin") {
      config = {};
      const env = parseJsonOrEmpty(document.getElementById("mcpEnv").value);
      const cwd = document.getElementById("mcpCwd").value.trim();
      if (env) config.env = env;
      if (cwd) config.cwd = cwd;
    } else {
      config = {
        command: document.getElementById("mcpCommand").value.trim(),
        args: parseArgs(document.getElementById("mcpArgs").value),
      };
      const env = parseJsonOrEmpty(document.getElementById("mcpEnv").value);
      const cwd = document.getElementById("mcpCwd").value.trim();
      if (env) config.env = env;
      if (cwd) config.cwd = cwd;
    }
    config.ui_tools = this.readUiToolsConfig();
    return config;
  },

  async save(event) {
    event.preventDefault();
    const transport = document.getElementById("mcpTransport").value;
    if (!state.editingMcp && transport === "builtin") {
      showToast("builtin 类型 MCP 由仓库内置，不能手动新建", true);
      return;
    }
    let config;
    try {
      config = this.buildConfig();
    } catch (err) {
      showToast(`配置 JSON 无效: ${err.message}`, true);
      return;
    }

    const payload = {
      config,
      description: document.getElementById("mcpDescription").value.trim() || null,
      enabled: document.getElementById("mcpEnabled").checked,
    };
    if (transport !== "builtin") {
      payload.transport = transport;
    }

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
