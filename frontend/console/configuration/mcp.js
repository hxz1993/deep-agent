const MCP_TRANSPORT_LABELS = {
  url: "url",
  stdio: "stdio",
  builtin: "builtin",
};

const MCP_CONTEXT_LABELS = {
  inherit: "继承",
  isolated: "独立",
};

function inheritedHeadersTooltip(headers) {
  return headers?.length
    ? `允许继承的 Header：${headers.join("、")}`
    : "允许继承的 Header 由后端白名单控制。";
}

function inspectionErrorMessage(error) {
  return error?.message || "";
}

function inspectionFailureLabel(error) {
  if (!error) return "";
  const stages = {
    initialize: "MCP 初始化失败",
    ping: "MCP Ping 失败",
    "tools/list": "工具清单更新失败",
    "resources/list": "Resource 清单更新失败",
    "resources/templates/list": "Resource Template 清单更新失败",
  };
  const label = stages[error.stage];
  return label
    ? `${label}${error.type ? `（${error.type}）` : ""}`
    : inspectionErrorMessage(error);
}

function inspectionView(inspection, transport) {
  if (transport === "stdio") {
    return {
      text: "已停止支持",
      className: "off",
      summary: "请迁移为 URL MCP",
      title: "历史 stdio MCP 只能查看或删除",
    };
  }
  const info = inspection || { status: transport === "builtin" ? "not_applicable" : "unknown" };
  const labels = {
    healthy: ["正常", "on"],
    degraded: ["部分异常", "warn"],
    unreachable: ["无法连接", "off"],
    unknown: ["待检测", ""],
    stale: ["配置已变更", "warn"],
    not_applicable: ["内置运行时", ""],
  };
  const [text, className] = labels[info.status] || labels.unknown;
  const summary = info.status === "not_applicable"
    ? "运行时按需启动"
    : `${info.tools?.count || 0} 工具 · ${info.resources?.count || 0} 资源 · ${info.resources?.template_count || 0} 模板`;
  const server = [info.server?.name, info.server?.version].filter(Boolean).join(" ");
  const errors = [
    inspectionErrorMessage(info.error),
    inspectionErrorMessage(info.tools?.error),
    inspectionErrorMessage(info.resources?.error),
  ].filter(Boolean);
  const details = [
    server ? `服务：${server}` : "",
    info.server?.protocol_version ? `协议：${info.server.protocol_version}` : "",
    info.server ? `Instructions：${info.server.instructions_available ? "可用" : "无"}` : "",
    info.checked_at ? `检测时间：${formatDate(info.checked_at)}` : "",
    Number.isFinite(info.duration_ms) ? `耗时：${info.duration_ms} ms` : "",
    errors.length ? `错误：${errors.join("；")}` : "",
  ].filter(Boolean);
  return {
    text,
    className,
    summary,
    title: details.join("\n") || (info.status === "not_applicable" ? "Builtin MCP 不参与主动检测" : "尚未检测"),
  };
}

function inspectionToast(result) {
  const inspection = result.inspection || {};
  const changes = result.changes || {};
  if (inspection.status === "unreachable") {
    return {
      message: `检测失败：${inspectionFailureLabel(inspection.error) || "MCP 无法连接"}`,
      error: true,
    };
  }
  if (inspection.status === "degraded") {
    const detail = inspectionFailureLabel(inspection.tools?.error)
      || inspectionFailureLabel(inspection.resources?.error)
      || "部分能力清单更新失败";
    return { message: `连接成功，但${detail}`, error: true };
  }

  const changed = [];
  [
    ["工具", changes.tool_count],
    ["资源", changes.resource_count],
    ["模板", changes.template_count],
  ].forEach(([label, item]) => {
    if (item && item.before !== item.after) changed.push(`${label} ${item.before ?? 0} → ${item.after ?? 0}`);
  });
  if (changed.length) return { message: `检测成功：${changed.join("，")}`, error: false };
  return {
    message: `检测完成：无变化，共 ${inspection.tools?.count || 0} 个工具、${inspection.resources?.count || 0} 个资源、${inspection.resources?.template_count || 0} 个模板`,
    error: false,
  };
}

function savedInspectionToast(server, action) {
  const info = server?.inspection || {};
  if (server?.transport === "builtin") return { message: `MCP 已${action}，已热重载`, error: false };
  if (!server?.enabled) return { message: `MCP 已${action}，当前未启用，尚未检测`, error: false };
  if (info.status === "unreachable") {
    return {
      message: `MCP 已${action}，但检测失败：${inspectionFailureLabel(info.error) || "无法连接"}`,
      error: true,
    };
  }
  if (info.status === "degraded") return { message: `MCP 已${action}，但部分能力更新失败`, error: true };
  return { message: `MCP 已${action}并完成检测`, error: false };
}

function validateMcpConfig(transport, config) {
  if (transport === "url" && !String(config?.url || "").trim()) {
    return "MCP URL 不能为空";
  }
  return null;
}

const mcpAdmin = {
  async refresh() {
    const tbody = document.getElementById("mcpTableBody");
    if (!tbody) return;
    tbody.innerHTML = `<tr><td colspan="7" class="empty-cell">加载中…</td></tr>`;
    try {
      const servers = await apiFetch("/api/v1/mcp");
      showDbBanner(false);
      if (!servers.length) {
        tbody.innerHTML = `<tr><td colspan="7" class="empty-cell">暂无 MCP 服务</td></tr>`;
        return;
      }
      const inherited = servers.find((item) => item.request_context?.inherited_headers?.length)
        ?.request_context.inherited_headers;
      state.mcpInheritedHeaders = inherited || state.mcpInheritedHeaders || [];
      tbody.innerHTML = servers
        .map(
          (s) => {
            const inspection = inspectionView(s.inspection, s.transport);
            return `
        <tr>
          <td><strong>${escapeHtml(s.name)}</strong></td>
          <td><span class="badge">${escapeHtml(MCP_TRANSPORT_LABELS[s.transport] || s.transport)}</span></td>
          <td><span class="badge">${escapeHtml(MCP_CONTEXT_LABELS[s.request_context?.mode] || "继承")}</span></td>
          <td><span class="badge ${s.enabled ? "on" : "off"}">${s.enabled ? "是" : "否"}</span></td>
          <td class="mcp-description-cell">
            <div class="mcp-description-text">${escapeHtml(s.description || "—")}</div>
            <div class="mcp-inspection" title="${escapeHtml(inspection.title)}">
              <span class="badge ${inspection.className}">${escapeHtml(inspection.text)}</span>
              <span class="mcp-inspection-summary">${escapeHtml(inspection.summary)}</span>
            </div>
          </td>
          <td>${escapeHtml(formatDate(s.updated_at))}</td>
          <td class="mcp-actions-cell">
            <div class="row-actions mcp-row-actions">
              ${s.transport === "url" ? `<button type="button" data-inspect-mcp="${escapeHtml(s.name)}">检测并更新</button>` : ""}
              ${s.transport === "stdio" ? "" : `<button type="button" data-edit="${escapeHtml(s.name)}">编辑</button>`}
              <button type="button" class="danger" data-delete="${escapeHtml(s.name)}">删除</button>
            </div>
          </td>
        </tr>`;
          }
        )
        .join("");

      tbody.querySelectorAll("[data-edit]").forEach((btn) => {
        btn.addEventListener("click", () => this.openEdit(btn.dataset.edit));
      });
      tbody.querySelectorAll("[data-inspect-mcp]").forEach((btn) => {
        btn.addEventListener("click", () => this.inspect(btn.dataset.inspectMcp, btn));
      });
      tbody.querySelectorAll("[data-delete]").forEach((btn) => {
        btn.addEventListener("click", () => this.remove(btn.dataset.delete));
      });
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="7" class="empty-cell">${escapeHtml(err.message)}</td></tr>`;
    }
  },

  readRequestContextMode() {
    return document.querySelector('input[name="mcpRequestContextMode"]:checked')?.value || "isolated";
  },

  fillRequestContextFields(server = null) {
    const mode = server?.request_context?.mode || server?.config?.request_context?.mode || "isolated";
    const input = document.querySelector(`input[name="mcpRequestContextMode"][value="${mode}"]`);
    if (input) input.checked = true;
    const inherited = server?.request_context?.inherited_headers || state.mcpInheritedHeaders || [];
    this.updateRequestContextDescription(inherited);
  },

  updateRequestContextDescription(inheritedHeaders = state.mcpInheritedHeaders || []) {
    const mode = this.readRequestContextMode();
    const description = document.getElementById("mcpRequestContextDescription");
    if (description) {
      description.innerHTML = mode === "inherit"
        ? `静态 Header 与当前请求上下文合并，请求中的同名值优先。<span class="context-policy-help" data-context-help role="img" tabindex="0" aria-label="查看允许继承的 Header"></span>`
        : "仅使用该 MCP 自身配置的 Header，不继承当前用户请求。";
    }
    document.querySelectorAll("[data-context-help]").forEach((button) => {
      const tooltip = inheritedHeadersTooltip(inheritedHeaders);
      button.dataset.tooltip = tooltip;
      button.title = tooltip;
    });
  },

  async inspect(name, button) {
    const originalText = button?.textContent || "检测并更新";
    if (button) {
      button.disabled = true;
      button.textContent = "检测中…";
    }
    try {
      const result = await apiFetch(
        `/api/v1/mcp/${encodeURIComponent(name)}/inspect`,
        { method: "POST" }
      );
      const toast = inspectionToast(result);
      showToast(toast.message, toast.error);
      await refreshAfterSuccessfulMutation("MCP 检测已完成", () => this.refresh());
    } catch (err) {
      showToast(err.message, true);
    } finally {
      if (button?.isConnected) {
        button.disabled = false;
        button.textContent = originalText;
      }
    }
  },

  setTransportSelectMode(mode) {
    const select = document.getElementById("mcpTransport");
    const builtinOption = select?.querySelector('option[value="builtin"]');
    const stdioOption = select?.querySelector('option[value="stdio"]');
    if (!select || !builtinOption || !stdioOption) return;

    if (mode === "create") {
      builtinOption.hidden = true;
      builtinOption.disabled = true;
      stdioOption.hidden = true;
      stdioOption.disabled = true;
      select.disabled = false;
      return;
    }
    if (mode === "edit-builtin") {
      builtinOption.hidden = false;
      builtinOption.disabled = false;
      stdioOption.hidden = true;
      stdioOption.disabled = true;
      select.disabled = true;
      return;
    }
    builtinOption.hidden = true;
    builtinOption.disabled = true;
    stdioOption.hidden = true;
    stdioOption.disabled = true;
    select.disabled = false;
  },

  toggleTransportFields() {
    const transport = document.getElementById("mcpTransport").value;
    const urlInput = document.getElementById("mcpUrl");
    if (urlInput) urlInput.required = transport === "url";
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
    this.fillRequestContextFields();
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

      this.fillRequestContextFields(server);
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
      config = {
        url: document.getElementById("mcpUrl").value.trim(),
        request_context: { mode: this.readRequestContextMode() },
      };
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
    return config;
  },

  async save(event) {
    event.preventDefault();
    const form = event.currentTarget;
    AdminFormFeedback.clear(form);
    const transport = document.getElementById("mcpTransport").value;
    if (!state.editingMcp && transport === "builtin") {
      AdminFormFeedback.show(form, "builtin 类型 MCP 由仓库内置，不能手动新建");
      return;
    }
    let config;
    try {
      config = this.buildConfig();
    } catch (err) {
      AdminFormFeedback.show(form, `配置 JSON 无效: ${err.message}`);
      return;
    }
    const validationError = validateMcpConfig(transport, config);
    if (validationError) {
      AdminFormFeedback.show(form, validationError);
      document.getElementById("mcpUrl")?.focus();
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

    const submit = form.querySelector('button[type="submit"]');
    if (submit) {
      submit.dataset.submittingLabel = transport === "url" && payload.enabled
        ? "保存并检测中…"
        : "保存中…";
    }
    AdminFormFeedback.setSubmitting(form, true);
    let saved = false;
    try {
      if (state.editingMcp) {
        const server = await apiFetch(`/api/v1/mcp/${encodeURIComponent(state.editingMcp)}`, {
          method: "PATCH",
          body: JSON.stringify(payload),
        });
        const toast = savedInspectionToast(server, "更新");
        showToast(toast.message, toast.error);
      } else {
        payload.name = document.getElementById("mcpName").value.trim();
        const server = await apiFetch("/api/v1/mcp", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        const toast = savedInspectionToast(server, "创建");
        showToast(toast.message, toast.error);
      }
      saved = true;
      document.getElementById("mcpModal").close();
    } catch (err) {
      AdminFormFeedback.show(form, err);
    } finally {
      AdminFormFeedback.setSubmitting(form, false);
    }
    if (saved) {
      await refreshAfterSuccessfulSave("MCP 配置", async () => {
        await this.refresh();
        await loadCapabilities();
      });
    }
  },

  async remove(name) {
    if (!confirm(`确定删除 MCP "${name}"？`)) return;
    try {
      await apiFetch(`/api/v1/mcp/${encodeURIComponent(name)}`, { method: "DELETE" });
      showToast("MCP 已删除，已热重载");
      await refreshAfterSuccessfulMutation("MCP 已删除", async () => {
        await this.refresh();
        await loadCapabilities();
      });
    } catch (err) {
      showToast(err.message, true);
    }
  },
};

window.mcpAdmin = mcpAdmin;
window.__McpInspectionUI = {
  inspectionView,
  inspectionToast,
  savedInspectionToast,
  validateMcpConfig,
};

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("newMcpBtn")?.addEventListener("click", () => mcpAdmin.openCreate());
  document.getElementById("mcpForm")?.addEventListener("submit", (e) => mcpAdmin.save(e));
  document.getElementById("mcpTransport")?.addEventListener("change", () => mcpAdmin.toggleTransportFields());
  document.querySelectorAll('input[name="mcpRequestContextMode"]').forEach((input) => {
    input.addEventListener("change", () => mcpAdmin.updateRequestContextDescription());
  });
  document.getElementById("closeMcpModal")?.addEventListener("click", () => {
    document.getElementById("mcpModal").close();
  });
  document.getElementById("cancelMcpBtn")?.addEventListener("click", () => {
    document.getElementById("mcpModal").close();
  });
});
