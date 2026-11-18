const MCP_TRANSPORT_LABELS = {
  url: "Streamable HTTP",
  stdio: "STDIO（历史）",
  builtin: "STDIO（Builtin）",
};

function mcpTransportLabel(transport) {
  return MCP_TRANSPORT_LABELS[transport] || transport;
}

const MCP_CONTEXT_LABELS = {
  inherit: "会话应用",
  bound_app: "绑定应用",
  isolated: "外部固定",
};
const MCP_QUESTION_APP_LABEL = "提问应用";

function escapeJsonEditorHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function renderMcpContext(server) {
  const scope = server.request_context || server.config?.request_context || {};
  const mode = Object.hasOwn(MCP_CONTEXT_LABELS, scope.mode) ? scope.mode : "inherit";
  const appName = scope.target_app_name;
  const appId = scope.target_app_id;
  let target = "";
  let title = [];
  if (mode === "bound_app") {
    const name = appName && appName !== appId
      ? `<span class="mcp-context-app-name">${escapeHtml(appName)}</span>` : "";
    const id = appId ? `<code class="mcp-context-app-id">${escapeHtml(appId)}</code>` : "";
    if (name || id) target = `<span class="mcp-context-target">${name}${id}</span>`;
    title = [appName, appId, scope.target_space_name, scope.target_space_id];
  }
  const tooltip = [...new Set(title)].filter(Boolean).join(" · ");
  return `<span class="mcp-context-summary"${tooltip ? ` title="${escapeJsonEditorHtml(tooltip)}"` : ""}>
    <span class="badge mcp-context-${mode}">${MCP_CONTEXT_LABELS[mode]}</span>
    ${target}
  </span>`;
}

function inspectionErrorMessage(error) {
  return error?.message || "";
}

function needsInspectionApp(server, context) {
  if (server?.transport !== "url") return false;
  const mode = server?.request_context?.mode || server?.config?.request_context?.mode || "inherit";
  return mode === "inherit" && !context?.app;
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

function resourceInspectionWarning(inspection) {
  if (inspection.resources?.status !== "error" && !inspection.resources?.error) return "";
  return "资源清单检查失败，不影响工具调用；资源明细可能是上次检测结果";
}

function hasOnlyResourceWarning(inspection) {
  return inspection.status === "degraded"
    && !inspection.error && !inspection.tools?.error
    && inspection.tools?.status === "supported"
    && inspection.tools?.snapshot_status === "ready"
    && !!resourceInspectionWarning(inspection);
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
    not_applicable: ["正常", "on"],
  };
  const builtinFailure = transport === "builtin" && (info.error || info.tools?.error || info.tools?.status === "error");
  const [text, className] = builtinFailure
    ? ["检测失败", "off"]
    : labels[hasOnlyResourceWarning(info) ? "healthy" : info.status] || labels.unknown;
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
    resourceWarning: resourceInspectionWarning(info),
    title: details.join("\n") || (info.status === "not_applicable" ? "Builtin MCP 不参与主动检测" : "尚未检测"),
  };
}

function renderInspectionCounts(counts = {}, name = "", resourceWarning = "") {
  return [["wrench", "工具", counts.tools], ["file-text", "资源", counts.resources]]
    .map(([iconName, label, count]) => {
      const warning = label === "资源" ? resourceWarning : "";
      const content = `<i data-lucide="${warning ? "circle-alert" : iconName}"></i><strong>${Number(count || 0)}</strong>`;
      return `<button type="button"${warning ? ' class="mcp-resource-warning"' : ""} data-mcp-catalog="${escapeJsonEditorHtml(name)}" data-catalog-kind="${label === "工具" ? "tools" : "resources"}" aria-label="查看${label}明细${warning ? '（资源检查告警）' : ''}" title="${escapeJsonEditorHtml(warning || label)}">${content}</button>`;
    }).join("");
}

function renderMcpCatalog(catalog, kind = "tools", query = "") {
  const label = kind === "resources" ? "资源" : "工具";
  const tools = catalog[kind] || {};
  const all = Array.isArray(tools.definitions) ? tools.definitions : [];
  const term = query.trim().toLowerCase();
  const definitions = all.filter(item => !term || [item.name, item.title, item.description, item.uri, item.mime_type].some(value => String(value || "").toLowerCase().includes(term)));
  const notice = tools.snapshot_status === "stale" ? "配置已变更，以下为上次检测结果。" : "";
  if (!definitions.length) return `<p class="empty-cell">${term && all.length ? "没有匹配的内容" : kind === "resources" && tools.snapshot_status === "missing" ? "尚未取得资源清单，请查看服务检测结果；工具仍可独立使用。" : tools.snapshot_status === "missing" && tools.count !== 0 ? `暂无${label}快照，请先检测此服务。` : `此服务暂无${label}。`}</p>`;
  return `${notice ? `<p class="mcp-catalog-notice">${notice}</p>` : ""}
    <table class="data-table mcp-catalog-table"><thead><tr><th>${label}</th><th>描述</th></tr></thead><tbody>
    ${definitions.map((tool) => `<tr><td><strong>${escapeHtml(tool.name || "")}</strong>${tool.title ? `<div class="muted">${escapeHtml(tool.title)}</div>` : ""}${tool.uri ? `<div class="muted">${escapeHtml(tool.uri)}</div>` : ""}${tool.mime_type ? `<div class="muted">${escapeHtml(tool.mime_type)}</div>` : ""}</td><td>${tool.description?.length > 220 ? `<details><summary>${escapeHtml(tool.description.slice(0, 180))}…</summary><div class="mcp-catalog-description">${escapeHtml(tool.description)}</div></details>` : escapeHtml(tool.description || "—")}</td></tr>`).join("")}
    </tbody></table>`;
}

function inspectionToast(result) {
  const inspection = result.inspection || {};
  const changes = result.changes || {};
  if (inspection.status === "not_applicable") {
    const failure = inspectionFailureLabel(inspection.error)
      || inspectionFailureLabel(inspection.tools?.error);
    if (failure || inspection.error || inspection.tools?.status === "error") return { message: `内置清单刷新失败：${failure || "工具清单更新失败"}`, error: true };
    const resourceWarning = resourceInspectionWarning(inspection);
    if (resourceWarning) return { message: `内置工具清单已刷新；${resourceWarning}`, error: false };
    const changed = [];
    [
      ["工具", changes.tool_count],
      ["资源", changes.resource_count],
    ].forEach(([label, item]) => {
      if (item && item.before !== item.after) changed.push(`${label} ${item.before ?? 0} → ${item.after ?? 0}`);
    });
    return {
      message: changed.length
        ? `内置清单已刷新并保存：${changed.join("，")}`
        : `内置清单已刷新：无变化，共 ${inspection.tools?.count || 0} 个工具、${inspection.resources?.count || 0} 个资源`,
      error: false,
    };
  }
  if (inspection.status === "unreachable") {
    return {
      message: `检测失败：${inspectionFailureLabel(inspection.error) || "MCP 无法连接"}`,
      error: true,
    };
  }
  if ((inspection.status === "healthy" || hasOnlyResourceWarning(inspection)) && resourceInspectionWarning(inspection)) {
    return { message: `工具清单已更新并保存（${inspection.tools?.count || 0} 个）；${resourceInspectionWarning(inspection)}`, error: false };
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
    message: `检测完成并保存 ${savedCount} 个工具快照：无变化，共 ${inspection.resources?.count || 0} 个资源`,
    error: false,
  };
}

function savedInspectionToast(server, action) {
  const info = server?.inspection || {};
  if (server?.transport === "builtin") {
    const failure = inspectionFailureLabel(info.error) || inspectionFailureLabel(info.tools?.error);
    if (failure || info.tools?.status === "error") return { message: `MCP 已${action}，但清单刷新失败：${failure || "工具清单更新失败"}`, error: true };
    return { message: `MCP 已${action}，已热重载${resourceInspectionWarning(info) ? `；${resourceInspectionWarning(info)}` : ""}`, error: false };
  }
  if (info.status === "unreachable") {
    return {
      message: `MCP 已${action}，但检测失败：${inspectionFailureLabel(info.error) || "无法连接"}`,
      error: true,
    };
  }
  if ((info.status === "healthy" || hasOnlyResourceWarning(info)) && resourceInspectionWarning(info)) {
    return { message: `MCP 已${action}，工具清单可用；${resourceInspectionWarning(info)}`, error: false };
  }
  if (info.status === "degraded") return { message: `MCP 已${action}，但工具清单更新失败`, error: true };
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
    window.DeepAgentAppPicker.close("mcp");
    editor.close();
    return true;
  },

  async refresh() {
    const tbody = document.getElementById("mcpTableBody");
    if (!tbody) return;
    this.moreMenuDismissController?.abort();
    this.moreMenuDismissController = null;
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
        <tr class="mcp-list-row${s.transport === "stdio" ? " is-readonly" : ""}" ${s.transport === "stdio" ? "" : `data-open-mcp="${escapeHtml(s.name)}" tabindex="0"`}>
          <td><strong>${escapeHtml(s.name)}</strong></td>
          <td><span class="badge mcp-transport-${s.transport === "url" ? "http" : "stdio"}">${escapeHtml(mcpTransportLabel(s.transport))}</span></td>
          <td>${renderMcpContext(s)}</td>
          <td class="mcp-description-cell">
            <div class="mcp-description-text" title="${escapeJsonEditorHtml(s.description || "")}">${escapeHtml(s.description || "")}</div>
          </td><td class="mcp-capabilities-cell">
            <div class="mcp-inspection" title="${escapeJsonEditorHtml(inspection.title)}">
              <span class="badge ${inspection.className}">${escapeHtml(inspection.text)}</span>
              <span class="mcp-inspection-counts">${renderInspectionCounts(inspection.counts, s.name, inspection.resourceWarning)}</span>
            </div>
          </td>
          <td>${escapeHtml(formatDate(s.updated_at))}</td>
          <td class="mcp-actions-cell">
            <div class="row-actions mcp-row-actions">
              ${s.transport === "url" || s.transport === "builtin" ? `<button class="mcp-direct-action" type="button" data-inspect-mcp="${escapeHtml(s.name)}"><i data-lucide="refresh-cw"></i><span>检测</span></button>` : ""}
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
      tbody.querySelectorAll("[data-mcp-catalog]").forEach((btn) => {
        btn.addEventListener("click", () => this.openCatalog(btn.dataset.mcpCatalog, btn.dataset.catalogKind));
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

  catalogController: null,
  async openCatalog(name, kind = "tools") {
    const modal = document.getElementById("mcpCatalogModal");
    const content = document.getElementById("mcpCatalogContent");
    const tabs = document.getElementById("mcpCatalogTabs");
    const search = document.getElementById("mcpCatalogSearch");
    this.catalogController?.abort();
    const controller = new AbortController();
    this.catalogController = controller;
    document.getElementById("mcpCatalogTitle").textContent = `${name} · 工具与资源`;
    this.catalog = null;
    tabs.replaceChildren();
    search.value = "";
    content.innerHTML = '<p class="empty-cell">加载中…</p>';
    if (!modal.open) modal.showModal();
    try {
      const catalog = await apiFetch(`/api/v1/mcp/${encodeURIComponent(name)}/catalog`, { signal: controller.signal });
      if (this.catalogController !== controller || controller.signal.aborted) return;
      this.catalog = catalog;
      this.catalogKind = kind;
      tabs.innerHTML = [["tools", "工具"], ["resources", "资源"]].map(([key, label]) => `<button type="button" role="tab" data-catalog-tab="${key}">${label} <span>${Number(catalog[key]?.count || 0)}</span></button>`).join("");
      tabs.querySelectorAll("[data-catalog-tab]").forEach(button => button.addEventListener("click", () => {
        this.catalogKind = button.dataset.catalogTab;
        search.value = "";
        this.renderCatalogResults();
      }));
      this.renderCatalogResults();
    } catch (error) {
      if (this.catalogController !== controller || controller.signal.aborted) return;
      content.innerHTML = `<p class="empty-cell">${escapeHtml(error.message)}</p>`;
    }
  },

  renderCatalogResults() {
    const content = document.getElementById("mcpCatalogContent");
    document.querySelectorAll("#mcpCatalogTabs [data-catalog-tab]").forEach(button => button.setAttribute("aria-selected", String(button.dataset.catalogTab === this.catalogKind)));
    content.innerHTML = renderMcpCatalog(this.catalog, this.catalogKind, document.getElementById("mcpCatalogSearch").value);
    content.scrollTop = 0;
  },

  readRequestContextMode() {
    return document.querySelector('input[name="mcpRequestContextMode"]:checked')?.value || "isolated";
  },

  fillRequestContextFields(server = null) {
    const scope = server?.request_context || server?.config?.request_context || {};
    const mode = Object.hasOwn(MCP_CONTEXT_LABELS, scope.mode) ? scope.mode : "isolated";
    const input = document.querySelector(`input[name="mcpRequestContextMode"][value="${mode}"]`);
    if (input) input.checked = true;
    window.DeepAgentAppPicker.close("mcp");
    const selected = scope.target_app_id ? {
      app_id: scope.target_app_id, app_name: scope.target_app_name,
      space_id: scope.target_space_id, space_name: scope.target_space_name,
    } : null;
    this.setBoundApp(selected);
    this.updateRequestContextDescription(scope.inherited_headers || state.mcpInheritedHeaders || []);
  },

  updateRequestContextDescription(inheritedHeaders = state.mcpInheritedHeaders || []) {
    const mode = this.readRequestContextMode();
    document.getElementById("mcpFixedHeaders")?.classList.toggle("hidden", mode !== "isolated");
    document.getElementById("mcpBoundAppFields")?.classList.toggle("hidden", mode !== "bound_app");
    if (mode !== "bound_app") window.DeepAgentAppPicker.close("mcp");
    const description = document.getElementById("mcpRequestContextDescription");
    if (!description) return;
    description.classList.toggle("hidden", mode === "isolated");
    const authHeaders = inheritedHeaders.filter(header => !["app", "space"].includes(header.toLowerCase()));
    const allowedHeaders = authHeaders.length ? authHeaders.join("、") : "由后端白名单控制";
    description.innerHTML = mode === "inherit"
      ? `<span><strong>目标应用</strong>　${MCP_QUESTION_APP_LABEL}，空间随会话确定。</span>
        <small>沿用本轮登录身份。允许透传其他请求头：${escapeHtml(allowedHeaders)}</small>`
      : "使用目标应用及其空间，沿用本轮的平台登录身份。调用前检查当前用户对目标的访问权限。";
  },

  setBoundApp(app) {
    this.boundApp = app;
    const value = document.getElementById("mcpBoundAppValue");
    const space = document.getElementById("mcpBoundAppSpace");
    value.classList.toggle("da-select-placeholder", !app);
    value.textContent = app
      ? [...new Set([app.app_name, app.app_id])].filter(Boolean).join(" · ")
      : "选择目标应用";
    space.textContent = app?.space_name || app?.space_id || "";
    space.hidden = !space.textContent;
    document.getElementById("mcpBoundApp").title = [value.textContent, space.textContent].filter(Boolean).join(" · ");
  },

  openBoundAppPicker() {
    window.DeepAgentAppPicker.open({
      owner: "mcp", path: "/api/v1/mcp/bound-apps", title: "选择目标应用",
      selectedAppId: this.boundApp?.app_id,
      onSelect: app => {
        if (this.boundApp?.app_id !== app.app_id || this.boundApp?.space_id !== app.space_id) {
          this.editorDirty = true;
        }
        this.setBoundApp(app);
      },
    });
  },

  syncHeadersHighlight() {
    const input = document.getElementById("mcpHeaders");
    const highlight = document.getElementById("mcpHeadersHighlight");
    if (!input || !highlight) return;
    window.DeepAgentJsonInput.syncJsonInput(input, highlight);
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
    const context = window.DeepAgentConsoleContext;
    if (context && needsInspectionApp(server, context.effectiveContext)) {
      context.open(button, { inspectMcp: name });
      return;
    }
    const label = button?.querySelector("span");
    const originalText = label?.textContent || "检测";
    if (button) {
      button.disabled = true;
      if (label) label.textContent = "检测中…";
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
        if (label) label.textContent = originalText;
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
      if (mode === "bound_app") {
        if (!this.boundApp?.app_id || !this.boundApp.space_id) throw new Error("请选择绑定应用。");
        config.request_context.target_app_id = this.boundApp.app_id;
        config.request_context.target_space_id = this.boundApp.space_id;
      }
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
  renderMcpCatalog,
  renderMcpContext,
  renderInspectionCounts,
  highlightJsonEditorSource: value => window.DeepAgentJsonInput.highlightJsonEditorSource(value),
  inspectionView,
  inspectionToast,
  savedInspectionToast,
  validateMcpConfig,
  canDeleteMcp,
  canRenameMcp,
  renameSuccessMessage,
};

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("mcpBoundApp")?.addEventListener("click", () => mcpAdmin.openBoundAppPicker());
  document.getElementById("mcpModal")?.addEventListener("close", event => {
    if (!event.currentTarget.open) window.DeepAgentAppPicker.close("mcp");
  });
  const catalogModal = document.getElementById("mcpCatalogModal");
  document.getElementById("mcpCatalogSearch")?.addEventListener("input", () => mcpAdmin.renderCatalogResults());
  document.getElementById("closeMcpCatalogModal")?.addEventListener("click", () => catalogModal.close());
  catalogModal?.addEventListener("close", () => {
    mcpAdmin.catalogController?.abort();
    mcpAdmin.catalogController = null;
  });
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
