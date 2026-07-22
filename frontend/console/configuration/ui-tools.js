const SOURCE_LABELS = {
  builtin: "内置",
  manual: "MANUAL",
  mcp: "mcp",
};

function uiToolDisplayValue(value) {
  if (value === null || value === undefined || value === "") return "—";
  return String(value);
}

function uiToolJsonValue(value) {
  if (value === null || value === undefined) return "—";
  return JSON.stringify(value, null, 2);
}

const UI_TOOL_TEMPLATES = {
  confirm: {
    label: "确认对话框",
    hint: "二次确认场景：参数含 message（必填）与按钮文案；结果返回 confirmed 布尔值。",
    nameExample: "myapp__confirm_delete",
    description: "Ask the user to confirm or cancel before proceeding.",
    widget: "confirm",
    parameters_json_schema: {
      type: "object",
      properties: {
        message: { type: "string", description: "确认文案" },
        confirm_label: { type: "string", description: "确认按钮", default: "确认" },
        cancel_label: { type: "string", description: "取消按钮", default: "取消" },
      },
      required: ["message"],
    },
    result_schema: {
      type: "object",
      properties: { confirmed: { type: "boolean" } },
      required: ["confirmed"],
    },
    metadata: { template: "confirm" },
  },
  choice: {
    label: "选项列表",
    hint: "单选/多选场景：参数含 prompt、options 数组；结果返回 selected。",
    nameExample: "myapp__pick_plan",
    description: "Present options and wait for the user to select one or more.",
    widget: "choice",
    parameters_json_schema: {
      type: "object",
      properties: {
        prompt: { type: "string", description: "选项说明" },
        options: { type: "array", items: { type: "string" }, description: "可选项" },
        allow_multiple: { type: "boolean", description: "允许多选", default: false },
      },
      required: ["prompt", "options"],
    },
    result_schema: {
      type: "object",
      properties: { selected: { type: "string", description: "选中项" } },
      required: ["selected"],
    },
    metadata: { template: "choice" },
  },
  text: {
    label: "文本输入",
    hint: "收集用户文本：参数含 prompt，可选多行与占位符；结果返回 text。",
    nameExample: "myapp__collect_note",
    description: "Ask the user to enter text in the UI.",
    widget: "text",
    parameters_json_schema: {
      type: "object",
      properties: {
        prompt: { type: "string", description: "输入提示" },
        placeholder: { type: "string", description: "占位符" },
        multiline: { type: "boolean", description: "多行", default: false },
      },
      required: ["prompt"],
    },
    result_schema: {
      type: "object",
      properties: { text: { type: "string" } },
      required: ["text"],
    },
    metadata: { template: "text" },
  },
  date_picker: {
    label: "日期选择",
    hint: "日期选择场景：参数含 prompt 与可选日期范围；结果返回 date 字符串。",
    nameExample: "myapp__pick_delivery_date",
    description: "Ask the user to pick a date in the client UI.",
    widget: "date_picker",
    parameters_json_schema: {
      type: "object",
      properties: {
        prompt: { type: "string", description: "日期说明" },
        min_date: { type: "string", description: "最早日期" },
        max_date: { type: "string", description: "最晚日期" },
      },
      required: ["prompt"],
    },
    result_schema: {
      type: "object",
      properties: { date: { type: "string" } },
      required: ["date"],
    },
    metadata: { template: "date_picker" },
  },
  custom: {
    label: "空白自定义",
    hint: "从空白 schema 开始，自行添加参数字段与结果类型；Widget 可选「自定义」输入客户端组件名。",
    nameExample: "myapp__custom_form",
    description: "Collect structured user input via a custom client-side form.",
    widget: "custom",
    parameters_json_schema: {
      type: "object",
      properties: {},
    },
    result_schema: null,
    metadata: { template: "custom" },
  },
};

const KNOWN_WIDGETS = ["confirm", "choice", "text", "date_picker", "custom"];

const uiToolsAdmin = {
  editingName: null,
  editingSource: null,
  _schemaReadOnly: false,

  paramsEl() {
    return document.getElementById("uiToolParamsEditor");
  },

  resultEl() {
    return document.getElementById("uiToolResultEditor");
  },

  initTemplates() {
    const select = document.getElementById("uiToolTemplate");
    if (!select) return;
    select.innerHTML = Object.entries(UI_TOOL_TEMPLATES)
      .map(([key, tpl]) => `<option value="${key}">${escapeHtml(tpl.label)}</option>`)
      .join("");
    select.addEventListener("change", () => this.applyTemplate(select.value, { fillName: true }));
  },

  initWidgetPicker() {
    const select = document.getElementById("uiToolWidget");
    const customInput = document.getElementById("uiToolWidgetCustom");
    if (!select || !customInput) return;
    select.addEventListener("change", () => {
      customInput.classList.toggle("hidden", select.value !== "custom");
      if (select.value !== "custom") customInput.value = "";
    });
  },

  setWidgetValue(widget) {
    const select = document.getElementById("uiToolWidget");
    const customInput = document.getElementById("uiToolWidgetCustom");
    if (!select || !customInput) return;
    if (!widget) {
      select.value = "";
      customInput.value = "";
      customInput.classList.add("hidden");
      return;
    }
    if (KNOWN_WIDGETS.includes(widget)) {
      select.value = widget;
      customInput.value = "";
      customInput.classList.toggle("hidden", widget !== "custom");
    } else {
      select.value = "custom";
      customInput.value = widget;
      customInput.classList.remove("hidden");
    }
  },

  getWidgetValue() {
    const select = document.getElementById("uiToolWidget");
    const customInput = document.getElementById("uiToolWidgetCustom");
    if (!select) return null;
    if (select.value === "custom") {
      const custom = customInput?.value.trim() || "";
      return custom || null;
    }
    return select.value || null;
  },

  loadSchemas(parametersSchema, resultSchema, { readOnly = false } = {}) {
    uiSchemaEditor.renderParamsEditor(this.paramsEl(), parametersSchema || { type: "object", properties: {} }, {
      readOnly,
    });
    uiSchemaEditor.renderResultEditor(this.resultEl(), resultSchema, { readOnly });
    this._schemaReadOnly = readOnly;
  },

  setSchemaReadOnly(readOnly) {
    this._schemaReadOnly = readOnly;
    uiSchemaEditor.setReadOnly(this.paramsEl(), readOnly);
    uiSchemaEditor.setReadOnly(this.resultEl(), readOnly);
  },

  applyTemplate(key, { fillName = false } = {}) {
    const tpl = UI_TOOL_TEMPLATES[key];
    if (!tpl) return;
    const descEl = document.getElementById("uiToolTemplateDesc");
    if (descEl) descEl.textContent = tpl.hint || "";
    document.getElementById("uiToolDescription").value = tpl.description;
    this.setWidgetValue(tpl.widget);
    this.loadSchemas(tpl.parameters_json_schema, tpl.result_schema, { readOnly: this._schemaReadOnly });
    document.getElementById("uiToolMetadata").value = tpl.metadata
      ? JSON.stringify(tpl.metadata, null, 2)
      : "";
    if (fillName) {
      const nameInput = document.getElementById("uiToolName");
      if (!nameInput.value.trim()) {
        nameInput.value = tpl.nameExample;
      }
    }
  },

  buildQuery() {
    const params = new URLSearchParams();
    const source = document.getElementById("uiToolFilterSource")?.value;
    const mcp = document.getElementById("uiToolFilterMcp")?.value.trim();
    const enabled = document.getElementById("uiToolFilterEnabled")?.value;
    if (source) params.set("source", source);
    if (mcp) params.set("mcp_server", mcp);
    if (enabled) params.set("enabled", enabled);
    const qs = params.toString();
    return qs ? `?${qs}` : "";
  },

  async refresh() {
    const tbody = document.getElementById("uiToolsTableBody");
    if (!tbody) return;
    tbody.innerHTML = `<tr><td colspan="8" class="empty-cell">加载中…</td></tr>`;
    try {
      const data = await apiFetch(`/api/v1/ui-tools${this.buildQuery()}`);
      showDbBanner(false);
      const items = data.items || [];
      if (!items.length) {
        tbody.innerHTML = `<tr><td colspan="8" class="empty-cell">暂无 UI 工具</td></tr>`;
        return;
      }
      tbody.innerHTML = items
        .map((tool) => {
          const shadow = tool.shadowed_by
            ? `<span class="badge off" title="被 ${escapeHtml(tool.shadowed_by)} 覆盖">shadowed</span>`
            : "";
          return `
        <tr>
          <td><strong>${escapeHtml(tool.name)}</strong> ${shadow}</td>
          <td><span class="badge source-${escapeHtml(tool.source)}">${escapeHtml(SOURCE_LABELS[tool.source] || tool.source)}</span></td>
          <td>${escapeHtml(tool.widget || "—")}</td>
          <td><span class="badge ${tool.enabled ? "on" : "off"}">${tool.enabled ? "是" : "否"}</span></td>
          <td>${escapeHtml(tool.mcp_server_name || "—")}</td>
          <td class="desc-cell" title="${escapeHtml(tool.description || "—")}">${escapeHtml(tool.description || "—")}</td>
          <td>${escapeHtml(formatDate(tool.updated_at))}</td>
          <td>
            <div class="row-actions">
              ${tool.source === "builtin"
                ? `<button type="button" data-view="${escapeHtml(tool.name)}">查看</button>`
                : `<button type="button" data-edit="${escapeHtml(tool.name)}">编辑</button>
                   <button type="button" class="danger" data-delete="${escapeHtml(tool.name)}" data-source="${escapeHtml(tool.source)}">删除</button>`}
            </div>
          </td>
        </tr>`;
        })
        .join("");

      tbody.querySelectorAll("[data-view]").forEach((btn) => {
        btn.addEventListener("click", () => this.openDetails(btn.dataset.view));
      });
      tbody.querySelectorAll("[data-edit]").forEach((btn) => {
        btn.addEventListener("click", () => this.openEdit(btn.dataset.edit));
      });
      tbody.querySelectorAll("[data-delete]").forEach((btn) => {
        btn.addEventListener("click", () => this.remove(btn.dataset.delete, btn.dataset.source));
      });
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="8" class="empty-cell">${escapeHtml(err.message)}</td></tr>`;
    }
  },

  async openDetails(name) {
    try {
      const tool = await apiFetch(`/api/v1/ui-tools/${encodeURIComponent(name)}`);
      const fields = [
        ["名称", tool.name],
        ["来源", SOURCE_LABELS[tool.source] || tool.source],
        ["Widget", tool.widget],
        ["启用", tool.enabled ? "是" : "否"],
        ["覆盖同名定义", tool.override ? "是" : "否"],
        ["MCP 服务", tool.mcp_server_name],
        ["描述", tool.description],
        ["参数 Schema", uiToolJsonValue(tool.parameters_json_schema), "code"],
        ["结果 Schema", uiToolJsonValue(tool.result_schema), "code"],
        ["Metadata", uiToolJsonValue(tool.metadata), "code"],
        ["发现时间", formatDate(tool.discovered_at)],
        ["创建时间", formatDate(tool.created_at)],
        ["更新时间", formatDate(tool.updated_at)],
      ];
      document.getElementById("uiToolDetailTitle").textContent = `查看 · ${tool.name}`;
      document.getElementById("uiToolDetailGrid").innerHTML = fields
        .map(([label, value, kind]) => `
          <dt>${escapeHtml(label)}</dt>
          <dd class="${kind === "code" ? "ui-tool-detail-code" : ""}">${escapeHtml(uiToolDisplayValue(value))}</dd>
        `)
        .join("");
      document.getElementById("uiToolDetailModal").showModal();
    } catch (err) {
      showToast(err.message, true);
    }
  },

  setFormMode(mode) {
    const isCreate = mode === "create";
    const isMcp = mode === "mcp";
    document.getElementById("uiToolTemplateSection").classList.toggle("hidden", !isCreate);
    document.getElementById("uiToolName").disabled = !isCreate;
    document.getElementById("uiToolDescription").readOnly = isMcp;
    document.getElementById("uiToolWidget").disabled = isMcp;
    document.getElementById("uiToolWidgetCustom").readOnly = isMcp;
    document.getElementById("uiToolMetadata").readOnly = isMcp;
    document.getElementById("uiToolMcpHint").classList.toggle("hidden", !isMcp);
    document.getElementById("uiToolForceWrap").classList.toggle("hidden", !isMcp);
    if (!isMcp) {
      document.getElementById("uiToolForceEdit").checked = false;
      this.setSchemaReadOnly(false);
    } else {
      this.setSchemaReadOnly(true);
    }
  },

  onForceEditToggle() {
    const force = document.getElementById("uiToolForceEdit").checked;
    const readOnly = this.editingSource === "mcp" && !force;
    document.getElementById("uiToolDescription").readOnly = readOnly;
    document.getElementById("uiToolWidget").disabled = readOnly;
    document.getElementById("uiToolWidgetCustom").readOnly = readOnly;
    document.getElementById("uiToolMetadata").readOnly = readOnly;
    this.setSchemaReadOnly(readOnly);
  },

  openCreate() {
    this.editingName = null;
    this.editingSource = "manual";
    document.getElementById("uiToolModalTitle").textContent = "手工注册 UI 工具";
    document.getElementById("uiToolName").value = "";
    document.getElementById("uiToolEnabled").checked = true;
    document.getElementById("uiToolOverride").checked = false;
    document.getElementById("uiToolTemplate").value = "confirm";
    this.setFormMode("create");
    this.applyTemplate("confirm", { fillName: true });
    document.getElementById("uiToolModal").showModal();
  },

  async openEdit(name) {
    try {
      const tool = await apiFetch(`/api/v1/ui-tools/${encodeURIComponent(name)}`);
      this.editingName = name;
      this.editingSource = tool.source;
      document.getElementById("uiToolModalTitle").textContent = `编辑 · ${name}`;
      document.getElementById("uiToolName").value = tool.name;
      document.getElementById("uiToolDescription").value = tool.description || "";
      this.setWidgetValue(tool.widget || "");
      document.getElementById("uiToolMetadata").value = tool.metadata
        ? JSON.stringify(tool.metadata, null, 2)
        : "";
      document.getElementById("uiToolEnabled").checked = tool.enabled;
      document.getElementById("uiToolOverride").checked = tool.override;
      const isMcp = tool.source === "mcp";
      this.setFormMode(isMcp ? "mcp" : "edit");
      this.loadSchemas(tool.parameters_json_schema, tool.result_schema, { readOnly: isMcp });
      document.getElementById("uiToolModal").showModal();
    } catch (err) {
      showToast(err.message, true);
    }
  },

  async save(event) {
    event.preventDefault();
    const form = event.currentTarget;
    AdminFormFeedback.clear(form);
    let parameters_json_schema;
    let result_schema;
    let metadata;
    try {
      parameters_json_schema = uiSchemaEditor.getParamsSchema(this.paramsEl());
      result_schema = uiSchemaEditor.getResultSchema(this.resultEl());
      metadata = parseJsonOrEmpty(document.getElementById("uiToolMetadata").value);
    } catch (err) {
      AdminFormFeedback.show(form, err);
      return;
    }

    const widget = this.getWidgetValue();
    const enabled = document.getElementById("uiToolEnabled").checked;
    const override = document.getElementById("uiToolOverride").checked;
    const description = document.getElementById("uiToolDescription").value.trim();

    AdminFormFeedback.setSubmitting(form, true);
    try {
      if (this.editingName) {
        const force = document.getElementById("uiToolForceEdit").checked;
        const qs = force ? "?force=true" : "";
        await apiFetch(`/api/v1/ui-tools/${encodeURIComponent(this.editingName)}${qs}`, {
          method: "PATCH",
          body: JSON.stringify({
            description,
            parameters_json_schema,
            widget,
            result_schema: result_schema ?? null,
            metadata: metadata ?? null,
            enabled,
            override,
          }),
        });
        showToast("已保存");
      } else {
        await apiFetch("/api/v1/ui-tools", {
          method: "POST",
          body: JSON.stringify({
            name: document.getElementById("uiToolName").value.trim(),
            description,
            parameters_json_schema,
            widget,
            result_schema: result_schema ?? null,
            metadata: metadata ?? null,
            enabled,
            override,
          }),
        });
        showToast("已注册");
      }
      document.getElementById("uiToolModal").close();
      await this.refresh();
      await loadCapabilities();
    } catch (err) {
      AdminFormFeedback.show(form, err);
    } finally {
      AdminFormFeedback.setSubmitting(form, false);
    }
  },

  async remove(name, source) {
    if (source === "builtin") {
      showToast("内置工具不可删除", true);
      return;
    }
    if (!confirm(`确定删除 UI 工具 "${name}"？`)) return;
    try {
      await apiFetch(`/api/v1/ui-tools/${encodeURIComponent(name)}`, { method: "DELETE" });
      showToast("已删除");
      await this.refresh();
      await loadCapabilities();
    } catch (err) {
      showToast(err.message, true);
    }
  },
};

window.uiToolsAdmin = uiToolsAdmin;

document.addEventListener("DOMContentLoaded", () => {
  uiToolsAdmin.initTemplates();
  uiToolsAdmin.initWidgetPicker();
  document.getElementById("newUiToolBtn")?.addEventListener("click", () => uiToolsAdmin.openCreate());
  document.getElementById("uiToolForm")?.addEventListener("submit", (e) => uiToolsAdmin.save(e));
  document.getElementById("uiToolFilterApply")?.addEventListener("click", () => uiToolsAdmin.refresh());
  document.getElementById("uiToolForceEdit")?.addEventListener("change", () => uiToolsAdmin.onForceEditToggle());
  document.getElementById("closeUiToolModal")?.addEventListener("click", () => {
    document.getElementById("uiToolModal").close();
  });
  document.getElementById("cancelUiToolBtn")?.addEventListener("click", () => {
    document.getElementById("uiToolModal").close();
  });
  document.getElementById("closeUiToolDetailModal")?.addEventListener("click", () => {
    document.getElementById("uiToolDetailModal").close();
  });
  document.getElementById("dismissUiToolDetailModal")?.addEventListener("click", () => {
    document.getElementById("uiToolDetailModal").close();
  });
});
