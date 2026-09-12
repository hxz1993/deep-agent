const MCP_TRANSPORT_LABELS = {
  url: "Streamable HTTP",
  stdio: "STDIO（历史）",
  builtin: "STDIO（Builtin）",
};

function mcpTransportLabel(transport) {
  return MCP_TRANSPORT_LABELS[transport] || transport;
}

const MCP_CONTEXT_LABELS = {
  inherit: "内部继承",
  isolated: "外部固定",
};

function escapeJsonEditorHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function highlightJsonEditorSource(value) {
  const source = String(value ?? "");
  const tokenPattern = /("(?:\\.|[^"\\])*"\s*:)|("(?:\\.|[^"\\])*")|\b(true|false)\b|\b(null)\b|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|([{}[\],:])/g;
  let cursor = 0;
  let html = "";
  for (const match of source.matchAll(tokenPattern)) {
    const index = match.index ?? 0;
    html += escapeJsonEditorHtml(source.slice(cursor, index));
    const token = match[0];
    if (match[1]) {
      const colonIndex = token.lastIndexOf(":");
      const key = token.slice(0, colonIndex).trimEnd();
      const spacing = token.slice(key.length, colonIndex);
      html += `<span class="json-key">${escapeJsonEditorHtml(key)}</span>${escapeJsonEditorHtml(spacing)}:`;
    } else if (match[2]) {
      html += `<span class="json-string">${escapeJsonEditorHtml(token)}</span>`;
    } else if (match[3]) {
      html += `<span class="json-boolean">${escapeJsonEditorHtml(token)}</span>`;
    } else if (match[4]) {
      html += `<span class="json-null">${escapeJsonEditorHtml(token)}</span>`;
    } else if (match[5]) {
      html += `<span class="json-number">${escapeJsonEditorHtml(token)}</span>`;
    } else {
      html += escapeJsonEditorHtml(token);
    }
    cursor = index + token.length;
  }
  html += escapeJsonEditorHtml(source.slice(cursor));
  return `${html}\n`;
}

function inspectionErrorMessage(error) {
  return error?.message || "";
}

function missingInspectionAppHint(server, context) {
  if (!server || server.transport === "stdio") return "";
  const mode = server?.request_context?.mode || server?.config?.request_context?.mode || "inherit";
  if (mode !== "inherit") return "";
  const staticApp = Object.entries(server?.config?.headers || {})
    .find(([name]) => name.toLowerCase() === "app")?.[1];
  if (String(context?.app || staticApp || "").trim()) return "";
  return "当前请求未提供测试 App；请到「对话测试」右上角「调试上下文」配置后，再返回 MCP 刷新。";
}

async function inspectionFailureMessage(server, message) {
  // Only enrich a completed inspection. Context lookup failures preserve the
  // original error without guessing whether the App was configured.
  if (!missingInspectionAppHint(server, {}) || !window.DeepAgentConsoleAPI?.requestContext) return message;
  try {
    const context = await window.DeepAgentConsoleAPI.requestContext();
    const hint = missingInspectionAppHint(server, context);
    return hint ? `${message}。${hint}` : message;
  } catch (_) {
    return message;
  }
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
      counts: { tools: 0, resources: 0, templates: 0 },
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
  const toolCount = Number(info.tools?.count || 0);
  const resourceCount = Number(info.resources?.count || 0);
  const templateCount = Number(info.resources?.template_count || 0);
  const snapshotStatus = info.tools?.snapshot_status || "missing";
  let snapshotLabel = "";
  if (transport === "url") {
    if (snapshotStatus === "stale") snapshotLabel = "配置已变更，需重新检测";
    else if (snapshotStatus !== "ready") snapshotLabel = "缺少工具快照，请检测并更新";
    else if (info.status === "unreachable") snapshotLabel = "当前无法连接，沿用上次快照";
    else snapshotLabel = "工具快照可用";
  }
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
    snapshotLabel ? `快照状态：${snapshotLabel}` : "",
    info.checked_at ? `检测时间：${formatDate(info.checked_at)}` : "",
    info.tools?.snapshot_at ? `快照时间：${formatDate(info.tools.snapshot_at)}` : "",
    info.tools?.snapshot_hash ? `快照：${info.tools.snapshot_hash.slice(0, 12)}` : "",
    Number.isFinite(info.duration_ms) ? `耗时：${info.duration_ms} ms` : "",
    errors.length ? `错误：${errors.join("；")}` : "",
  ].filter(Boolean);
  return {
    text,
    className,
    counts: { tools: toolCount, resources: resourceCount, templates: templateCount },
    title: details.join("\n") || (info.status === "not_applicable" ? "Builtin MCP 不参与主动检测" : "尚未检测"),
  };
}

function renderInspectionCounts(counts = {}) {
  return [
    ["wrench", "工具", counts.tools],
    ["file-text", "资源", counts.resources],
    ["file-text", "模板", counts.templates],
  ].map(([iconName, label, count]) => `<span title="${label}"><i data-lucide="${iconName}"></i><strong>${Number(count || 0)}</strong></span>`).join("");
}

function inspectionToast(result) {
  const inspection = result.inspection || {};
  const changes = result.changes || {};
  if (inspection.status === "not_applicable") {
    const failure = inspectionErrorMessage(inspection.error)
      || inspectionFailureLabel(inspection.tools?.error)
      || inspectionFailureLabel(inspection.resources?.error);
    if (failure) return { message: `内置清单刷新失败：${failure}`, error: true };
    const changed = [];
    [
      ["工具", changes.tool_count],
      ["资源", changes.resource_count],
      ["模板", changes.template_count],
    ].forEach(([label, item]) => {
      if (item && item.before !== item.after) changed.push(`${label} ${item.before ?? 0} → ${item.after ?? 0}`);
    });
    return {
      message: changed.length
        ? `内置清单已刷新并保存：${changed.join("，")}`
        : `内置清单已刷新：无变化，共 ${inspection.tools?.count || 0} 个工具、${inspection.resources?.count || 0} 个资源、${inspection.resources?.template_count || 0} 个模板`,
      error: false,
    };
  }
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
  const savedCount = inspection.tools?.count || 0;
  if (changed.length) {
    return {
      message: `检测成功并保存 ${savedCount} 个工具快照：${changed.join("，")}`,
      error: false,
    };
  }
  return {
    message: `检测完成并保存 ${savedCount} 个工具快照：无变化，共 ${inspection.resources?.count || 0} 个资源、${inspection.resources?.template_count || 0} 个模板`,
    error: false,
  };
}

function savedInspectionToast(server, action) {
  const info = server?.inspection || {};
  if (server?.transport === "builtin") return { message: `MCP 已${action}，已热重载`, error: false };
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

function canDeleteMcp(transport) {
  return transport !== "builtin";
}

function canRenameMcp(transport) {
  return transport === "url";
}

function renameSuccessMessage(updatedAgents) {
  const count = Array.isArray(updatedAgents) ? updatedAgents.length : 0;
  return `MCP 已重命名，已同步更新 ${count} 个 Agent`;
}

const mcpAdmin = {
  moreMenuDismissController: null,
  editorDirty: false,
  updateEditorMeta() {
    const transport = document.getElementById("mcpTransport")?.value || "url";
    const meta = document.getElementById("mcpEditorMeta");
    if (meta) meta.textContent = `MCP 配置 · ${mcpTransportLabel(transport)}`;
  },

  isEditorOpen() {
    return Boolean(document.getElementById("mcpModal")?.open);
  },

  async requestCloseEditor(force = false) {
    const editor = document.getElementById("mcpModal");
    if (!editor?.open) return true;
    if (
      !force
      && this.editorDirty
      && !(await confirmAction({
        title: "放弃未保存的修改？",
        message: "当前 MCP 的修改尚未保存，离开后这些修改将丢失。",
        icon: "file-warning",
        confirmLabel: "放弃修改",
      }))
    ) return false;
    this.editorDirty = false;
    state.editingMcp = null;
    editor.close();
    return true;
  },

  async refresh() {
    const tbody = document.getElementById("mcpTableBody");
    if (!tbody) return;
    this.moreMenuDismissController?.abort();
    this.moreMenuDismissController = null;
    tbody.innerHTML = `<tr><td colspan="6" class="empty-cell">加载中…</td></tr>`;
    try {
      const servers = await apiFetch("/api/v1/mcp");
      showDbBanner(false);
      if (!servers.length) {
        tbody.innerHTML = `<tr><td colspan="6" class="empty-cell">暂无 MCP 服务</td></tr>`;
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
        <tr class="mcp-list-row${s.transport === "stdio" ? " is-readonly" : ""}" ${s.transport === "stdio" ? "" : `data-open-mcp="${escapeHtml(s.name)}" tabindex="0"`}>
          <td><strong>${escapeHtml(s.name)}</strong></td>
          <td><span class="badge">${escapeHtml(mcpTransportLabel(s.transport))}</span></td>
          <td><span class="badge">${escapeHtml(MCP_CONTEXT_LABELS[s.request_context?.mode] || "内部继承")}</span></td>
          <td class="mcp-description-cell">
            <div class="mcp-description-text">${escapeHtml(s.description || "—")}</div>
            <div class="mcp-inspection" title="${escapeHtml(inspection.title)}">
              <span class="badge ${inspection.className}">${escapeHtml(inspection.text)}</span>
              <span class="mcp-inspection-counts">${renderInspectionCounts(inspection.counts)}</span>
            </div>
          </td>
          <td>${escapeHtml(formatDate(s.updated_at))}</td>
          <td class="mcp-actions-cell">
            <div class="row-actions mcp-row-actions">
              ${s.transport === "url" || s.transport === "builtin" ? `<button class="mcp-direct-action" type="button" data-inspect-mcp="${escapeHtml(s.name)}"><i data-lucide="refresh-cw"></i><span>刷新</span></button>` : ""}
              ${canRenameMcp(s.transport) || canDeleteMcp(s.transport) ? `<div class="skill-more-actions mcp-more-actions">
                <button class="skill-more-trigger" type="button" data-mcp-more-trigger aria-label="更多 MCP 操作" title="更多操作" aria-haspopup="menu" aria-expanded="false"><i data-lucide="ellipsis-vertical"></i></button>
                <div class="skill-more-menu" data-mcp-more-menu role="menu" hidden>
                  ${canRenameMcp(s.transport) ? `<button type="button" role="menuitem" data-rename-mcp="${escapeHtml(s.name)}"><i data-lucide="text-cursor-input"></i><span>重命名</span></button>` : ""}
                  ${canDeleteMcp(s.transport) ? `<button type="button" role="menuitem" class="danger" data-delete="${escapeHtml(s.name)}"><i data-lucide="trash-2"></i><span>删除</span></button>` : ""}
                </div>
              </div>` : ""}
            </div>
          </td>
        </tr>`;
          }
        )
        .join("");

      tbody.querySelectorAll("[data-open-mcp]").forEach((row) => {
        const open = (event) => {
          if (event.target.closest("a, button, [role=menu]")) return;
          this.openEdit(row.dataset.openMcp);
        };
        row.addEventListener("click", open);
        row.addEventListener("keydown", (event) => {
          if (event.key !== "Enter" && event.key !== " ") return;
          if (event.target.closest("a, button, [role=menu]")) return;
          event.preventDefault();
          this.openEdit(row.dataset.openMcp);
        });
      });
      tbody.querySelectorAll(".mcp-row-actions").forEach((actions) => {
        actions.addEventListener("click", (event) => event.stopPropagation());
        actions.addEventListener("keydown", (event) => event.stopPropagation());
      });
      tbody.querySelectorAll("[data-inspect-mcp]").forEach((btn) => {
        btn.addEventListener("click", (event) => {
          event.stopPropagation();
          this.inspect(btn.dataset.inspectMcp, btn, servers.find((server) => server.name === btn.dataset.inspectMcp));
        });
      });
      tbody.querySelectorAll("[data-rename-mcp]").forEach((btn) => {
        btn.addEventListener("click", (event) => {
          event.stopPropagation();
          this.openRename(btn.dataset.renameMcp);
        });
      });
      tbody.querySelectorAll("[data-delete]").forEach((btn) => {
        btn.addEventListener("click", (event) => {
          event.stopPropagation();
          this.remove(btn.dataset.delete);
        });
      });
      tbody.querySelectorAll("[data-mcp-more-trigger]").forEach((trigger) => {
        const menu = trigger.parentElement?.querySelector("[data-mcp-more-menu]");
        if (!menu) return;
        const closeMenu = () => {
          menu.hidden = true;
          trigger.setAttribute("aria-expanded", "false");
          this.moreMenuDismissController?.abort();
          this.moreMenuDismissController = null;
        };
        trigger.addEventListener("click", (event) => {
          event.stopPropagation();
          const willOpen = menu.hidden;
          tbody.querySelectorAll("[data-mcp-more-menu]").forEach((otherMenu) => { otherMenu.hidden = true; });
          tbody.querySelectorAll("[data-mcp-more-trigger]").forEach((otherTrigger) => { otherTrigger.setAttribute("aria-expanded", "false"); });
          this.moreMenuDismissController?.abort();
          this.moreMenuDismissController = null;
          if (!willOpen) return;
          menu.hidden = false;
          trigger.setAttribute("aria-expanded", "true");
          this.moreMenuDismissController = new AbortController();
          const { signal } = this.moreMenuDismissController;
          document.addEventListener("click", (outsideEvent) => {
            if (!outsideEvent.target.closest(".mcp-more-actions")) closeMenu();
          }, { signal });
          document.addEventListener("keydown", (keyEvent) => {
            if (keyEvent.key !== "Escape") return;
            closeMenu();
            trigger.focus();
          }, { signal });
        });
        menu.querySelectorAll("button").forEach((button) => {
          button.addEventListener("click", closeMenu);
        });
      });
      window.lucide?.createIcons();
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
    document.getElementById("mcpFixedHeaders")?.classList.toggle("hidden", mode === "inherit");
    const description = document.getElementById("mcpRequestContextDescription");
    if (description) {
      description.classList.toggle("hidden", mode !== "inherit");
      const allowedHeaders = inheritedHeaders?.length
        ? inheritedHeaders.join("、")
        : "由后端白名单控制";
      description.innerHTML = mode === "inherit"
        ? `<span>DeepAgent 会将当前请求中的允许 Header 直接透传给 MCP。</span><small>允许透传：${escapeHtml(allowedHeaders)}</small>`
        : "";
    }
  },

  syncHeadersHighlight() {
    const input = document.getElementById("mcpHeaders");
    const highlight = document.getElementById("mcpHeadersHighlight");
    if (!input || !highlight) return;
    highlight.innerHTML = highlightJsonEditorSource(input.value);
    const pre = highlight.parentElement;
    if (pre) {
      pre.scrollTop = input.scrollTop;
      pre.scrollLeft = input.scrollLeft;
    }
  },

  formatHeadersInput() {
    const input = document.getElementById("mcpHeaders");
    if (!input || !input.value.trim()) {
      this.syncHeadersHighlight();
      return null;
    }
    const parsed = JSON.parse(input.value);
    input.value = JSON.stringify(parsed, null, 2);
    this.syncHeadersHighlight();
    return parsed;
  },

  async inspect(name, button, server) {
    const originalText = button?.textContent || "刷新";
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
      const message = toast.error ? await inspectionFailureMessage(server, toast.message) : toast.message;
      showToast(message, toast.error);
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
    this.updateEditorMeta();
  },

  openCreate() {
    state.editingMcp = null;
    document.getElementById("mcpModalTitle").textContent = "新建 MCP";
    document.getElementById("mcpEditorMeta").textContent = `MCP 配置 · ${mcpTransportLabel("url")}`;
    const nameInput = document.getElementById("mcpName");
    nameInput.readOnly = false;
    nameInput.classList.remove("mcp-name-readonly");
    nameInput.value = "";
    document.getElementById("mcpNameReadOnlyHint")?.classList.add("hidden");
    document.getElementById("mcpTransport").value = "url";
    document.getElementById("mcpDescription").value = "";
    document.getElementById("mcpUrl").value = "";
    document.getElementById("mcpHeaders").value = "";
    document.getElementById("mcpCommand").value = "";
    document.getElementById("mcpArgs").value = "";
    document.getElementById("mcpEnv").value = "";
    document.getElementById("mcpCwd").value = "";
    this.fillRequestContextFields();
    this.syncHeadersHighlight();
    this.setTransportSelectMode("create");
    this.toggleTransportFields();
    this.editorDirty = false;
    document.getElementById("mcpModal").show();
  },

  async openEdit(name) {
    try {
      const server = await apiFetch(`/api/v1/mcp/${encodeURIComponent(name)}`);
      state.editingMcp = name;
      document.getElementById("mcpModalTitle").textContent = name;
      document.getElementById("mcpEditorMeta").textContent = `MCP 配置 · ${mcpTransportLabel(server.transport)}`;
      const nameInput = document.getElementById("mcpName");
      nameInput.readOnly = true;
      nameInput.classList.add("mcp-name-readonly");
      nameInput.value = server.name;
      document.getElementById("mcpNameReadOnlyHint")?.classList.remove("hidden");
      document.getElementById("mcpTransport").value = server.transport;
      document.getElementById("mcpDescription").value = server.description || "";

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
      this.syncHeadersHighlight();
      this.setTransportSelectMode(server.transport === "builtin" ? "edit-builtin" : "edit");
      this.toggleTransportFields();
      this.editorDirty = false;
      document.getElementById("mcpModal").show();
    } catch (err) {
      showToast(err.message, true);
    }
  },

  openRename(name) {
    state.renamingMcp = name;
    document.getElementById("mcpRenameCurrentName").value = name;
    document.getElementById("mcpRenameNewName").value = name;
    const form = document.getElementById("mcpRenameForm");
    AdminFormFeedback.clear(form);
    document.getElementById("mcpRenameModal").showModal();
    requestAnimationFrame(() => {
      const input = document.getElementById("mcpRenameNewName");
      input.focus();
      input.select();
    });
  },

  async rename(event) {
    event.preventDefault();
    const form = event.currentTarget;
    AdminFormFeedback.clear(form);
    const oldName = state.renamingMcp;
    const newName = document.getElementById("mcpRenameNewName").value.trim();
    if (!oldName) {
      AdminFormFeedback.show(form, "缺少待重命名的 MCP");
      return;
    }

    AdminFormFeedback.setSubmitting(form, true);
    let result = null;
    try {
      result = await apiFetch(`/api/v1/mcp/${encodeURIComponent(oldName)}/rename`, {
        method: "POST",
        body: JSON.stringify({ new_name: newName }),
      });
      document.getElementById("mcpRenameModal").close();
      state.renamingMcp = null;
      showToast(renameSuccessMessage(result.updated_agents));
    } catch (err) {
      AdminFormFeedback.show(form, err);
    } finally {
      AdminFormFeedback.setSubmitting(form, false);
    }
    if (result) {
      await refreshAfterSuccessfulMutation("MCP 已重命名", async () => {
        await this.refresh();
        await loadCapabilities();
      });
    }
  },

  buildConfig() {
    const transport = document.getElementById("mcpTransport").value;
    let config;
    if (transport === "url") {
      const mode = this.readRequestContextMode();
      config = {
        url: document.getElementById("mcpUrl").value.trim(),
        request_context: { mode },
      };
      if (mode === "isolated") {
        const headers = parseJsonOrEmpty(document.getElementById("mcpHeaders").value);
        if (headers) {
          document.getElementById("mcpHeaders").value = JSON.stringify(headers, null, 2);
          this.syncHeadersHighlight();
          config.headers = headers;
        }
      }
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
    };
    if (transport !== "builtin") {
      payload.transport = transport;
    }

    const submit = form.querySelector('button[type="submit"]');
    if (submit) {
      submit.dataset.submittingLabel = transport === "url"
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
      await this.requestCloseEditor(true);
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
    if (!(await confirmAction({
      title: "删除 MCP？",
      message: `删除后，已关联该 MCP 的 Agent 将无法继续使用它。当前对象：${name}`,
      icon: "trash-2",
      tone: "danger",
      confirmLabel: "删除",
    }))) return;
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
  missingInspectionAppHint,
  inspectionFailureMessage,
  highlightJsonEditorSource,
  inspectionView,
  inspectionToast,
  savedInspectionToast,
  validateMcpConfig,
  canDeleteMcp,
  canRenameMcp,
  renameSuccessMessage,
};

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("newMcpBtn")?.addEventListener("click", () => mcpAdmin.openCreate());
  document.getElementById("mcpForm")?.addEventListener("submit", (e) => mcpAdmin.save(e));
  document.getElementById("mcpForm")?.addEventListener("input", () => { mcpAdmin.editorDirty = true; });
  document.getElementById("mcpForm")?.addEventListener("change", () => { mcpAdmin.editorDirty = true; });
  document.getElementById("mcpRenameForm")?.addEventListener("submit", (e) => mcpAdmin.rename(e));
  document.getElementById("mcpTransport")?.addEventListener("change", () => mcpAdmin.toggleTransportFields());
  document.getElementById("mcpHeaders")?.addEventListener("input", () => mcpAdmin.syncHeadersHighlight());
  document.getElementById("mcpHeaders")?.addEventListener("scroll", () => mcpAdmin.syncHeadersHighlight());
  document.getElementById("mcpHeaders")?.addEventListener("blur", () => {
    try { mcpAdmin.formatHeadersInput(); } catch (_) { mcpAdmin.syncHeadersHighlight(); }
  });
  document.querySelectorAll('input[name="mcpRequestContextMode"]').forEach((input) => {
    input.addEventListener("change", () => mcpAdmin.updateRequestContextDescription());
  });
  document.getElementById("closeMcpModal")?.addEventListener("click", () => mcpAdmin.requestCloseEditor());
  document.getElementById("closeMcpRenameModal")?.addEventListener("click", () => {
    state.renamingMcp = null;
    document.getElementById("mcpRenameModal").close();
  });
  document.getElementById("cancelMcpRenameBtn")?.addEventListener("click", () => {
    state.renamingMcp = null;
    document.getElementById("mcpRenameModal").close();
  });
});
