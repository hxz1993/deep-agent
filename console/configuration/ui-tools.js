function parseUiToolSchema(value, { label, required = false, requireObject = false } = {}) {
  const text = String(value || "").trim();
  if (!text) {
    if (required) throw new Error(`${label || "Schema"}不能为空`);
    return null;
  }
  const parser = window.DeepAgentSchemaText?.parseSchemaText;
  if (!parser) throw new Error("JSON Schema 校验器未加载");
  const schema = parser(text);
  if (requireObject && schema.type !== "object") {
    throw new Error(`${label || "Schema"}顶层 type 必须是 object`);
  }
  return schema;
}

const UI_TOOL_TEMPLATES = {
  confirm: {
    label: "确认对话框",
    hint: "通用继续/取消场景：参数含 title、message（必填）与按钮文案；结果返回 confirmed 布尔值。",
    nameExample: "myapp__confirm_delete",
    description: "向用户展示通用确认界面并等待用户选择继续或取消。",
    widget: "confirm",
    parameters_json_schema: {
      type: "object",
      properties: {
        title: { type: "string", description: "可选标题" },
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
    hint: "通用单选/多选场景：始终提供自由输入兜底；结果返回 selected 数组和 custom_input。",
    nameExample: "myapp__pick_plan",
    description: "向用户展示通用候选列表，支持单选、多选以及候选外自由输入。",
    widget: "choice",
    parameters_json_schema: {
      type: "object",
      properties: {
        title: { type: "string", description: "可选标题" },
        prompt: { type: "string", description: "选项说明" },
        options: {
          type: "array",
          minItems: 1,
          items: {
            oneOf: [
              { type: "string" },
              {
                type: "object",
                properties: {
                  value: { type: "string" },
                  label: { type: "string" },
                  description: { type: "string" },
                },
                required: ["value"],
                additionalProperties: false,
              },
            ],
          },
          description: "可选项",
        },
        allow_multiple: { type: "boolean", description: "允许多选", default: false },
        custom_label: { type: "string", description: "自由输入选项文案", default: "其他（自行输入）" },
        custom_placeholder: { type: "string", description: "自由输入提示", default: "请输入其他内容" },
        submit_label: { type: "string", description: "提交按钮文案", default: "提交" },
      },
      required: ["prompt", "options"],
    },
    result_schema: {
      type: "object",
      properties: {
        selected: {
          type: "array",
          items: { type: "string" },
          description: "选中的候选值",
        },
        custom_input: {
          type: ["string", "null"],
          description: "候选外自由输入",
        },
      },
      required: ["selected", "custom_input"],
      additionalProperties: false,
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
  editingPackageCode: null,
  editingToolPackageCode: null,
  selectedPackageCode: null,
  packages: [],
  formMode: null,
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
    const params = this.paramsEl();
    const result = this.resultEl();
    if (params) {
      params.value = JSON.stringify(parametersSchema || { type: "object", properties: {} }, null, 2);
      params.readOnly = readOnly;
    }
    if (result) {
      result.value = resultSchema ? JSON.stringify(resultSchema, null, 2) : "";
      result.readOnly = readOnly;
    }
    this._schemaReadOnly = readOnly;
  },

  setSchemaReadOnly(readOnly) {
    this._schemaReadOnly = readOnly;
    if (this.paramsEl()) this.paramsEl().readOnly = readOnly;
    if (this.resultEl()) this.resultEl().readOnly = readOnly;
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

  manualPackages() {
    return this.packages.filter((item) => item.source === "manual");
  },

  populatePackageSelect(selectedCode = null) {
    const input = document.getElementById("uiToolPackageCode");
    const owningPackage = this.packages.find(
      (item) => item.code === selectedCode && item.source === "manual",
    );
    this.editingToolPackageCode = owningPackage?.code || null;
    if (input) input.value = this.editingToolPackageCode || "";
  },

  renderPackages() {
    const tree = document.getElementById("uiToolPackageTree");
    if (!tree) return;
    if (!this.packages.length) {
      tree.innerHTML = `<div class="ui-tool-tree-state">暂无 UI 工具包</div>`;
      return;
    }
    const groups = [
      ["自定义", this.packages.filter((item) => item.source === "manual")],
      ["内置", this.packages.filter((item) => item.source === "builtin")],
    ];
    tree.innerHTML = groups
      .map(([label, items]) => `
        <section class="ui-tool-tree-group">
          <div class="ui-tool-tree-group-title"><span>${escapeHtml(label)}</span><small>${items.length}</small></div>
          ${items.length ? items.map((item) => `
            <button type="button" class="ui-tool-tree-node ${item.code === this.selectedPackageCode ? "is-selected" : ""}" data-select-package="${escapeHtml(item.code)}">
              <span class="ui-tool-tree-node-head">
                <span class="ui-tool-tree-icon"><i data-lucide="${item.source === "builtin" ? "panel-top" : "blocks"}"></i></span>
                <span class="ui-tool-tree-node-main">
                  <strong>${escapeHtml(item.label)}</strong>
                  <code>${escapeHtml(item.code)}</code>
                </span>
                <span class="ui-tool-tree-node-meta">
                  <span class="ui-tool-tree-count" title="工具数量"><strong>${escapeHtml(String(item.tool_count || 0))}</strong><i data-lucide="wrench"></i></span>
                </span>
              </span>
            </button>`).join("") : `<div class="configuration-group-empty">${label === "自定义" ? "暂无自定义工具包，请先新建" : "暂无内置工具包"}</div>`}
        </section>`)
      .join("");
    tree.querySelectorAll("[data-select-package]").forEach((node) => {
      node.addEventListener("click", () => this.selectPackage(node.dataset.selectPackage));
    });
    window.lucide?.createIcons();
  },

  selectPackage(code) {
    if (!this.packages.some((item) => item.code === code)) return;
    this.selectedPackageCode = code;
    this.renderSelectedPackage();
    this.renderPackages();
    this.renderTools(this._lastTools || []);
  },

  renderSelectedPackage() {
    const selected = this.packages.find((item) => item.code === this.selectedPackageCode);
    const label = document.getElementById("selectedUiToolPackageLabel");
    const code = document.getElementById("selectedUiToolPackageCode");
    const description = document.getElementById("selectedUiToolPackageDescription");
    const meta = document.getElementById("selectedUiToolPackageMeta");
    const editButton = document.getElementById("editUiToolPackageBtn");
    const deleteButton = document.getElementById("deleteUiToolPackageBtn");
    const newToolButton = document.getElementById("newUiToolBtn");
    if (!selected) {
      label.textContent = "请选择工具包";
      code.textContent = "";
      description.textContent = "从左侧选择工具包后查看工具。";
      meta.innerHTML = "";
      editButton.classList.add("hidden");
      deleteButton.classList.add("hidden");
      newToolButton.disabled = true;
      return;
    }
    label.textContent = selected.label;
    code.textContent = selected.code;
    description.textContent = selected.description || "暂无说明";
    meta.innerHTML = "";
    const manual = selected.source === "manual";
    editButton.classList.toggle("hidden", !manual);
    deleteButton.classList.toggle("hidden", !manual);
    newToolButton.disabled = !manual;
  },

  openPackageCreate() {
    this.editingPackageCode = null;
    const form = document.getElementById("uiToolPackageForm");
    AdminFormFeedback.clear(form);
    document.getElementById("uiToolPackageModalTitle").textContent = "新建 UI 工具包";
    document.getElementById("uiToolPackageEditCode").value = "";
    document.getElementById("uiToolPackageEditCode").disabled = false;
    document.getElementById("uiToolPackageLabel").value = "";
    document.getElementById("uiToolPackageDescription").value = "";
    document.getElementById("uiToolPackageModal").showModal();
  },

  openPackageEdit(code) {
    const item = this.packages.find((candidate) => candidate.code === code);
    if (!item || item.source !== "manual") return;
    this.editingPackageCode = code;
    const form = document.getElementById("uiToolPackageForm");
    AdminFormFeedback.clear(form);
    document.getElementById("uiToolPackageModalTitle").textContent = `编辑工具包 · ${item.label}`;
    document.getElementById("uiToolPackageEditCode").value = item.code;
    document.getElementById("uiToolPackageEditCode").disabled = true;
    document.getElementById("uiToolPackageLabel").value = item.label;
    document.getElementById("uiToolPackageDescription").value = item.description || "";
    document.getElementById("uiToolPackageModal").showModal();
  },

  async savePackage(event) {
    event.preventDefault();
    const form = event.currentTarget;
    AdminFormFeedback.clear(form);
    const payload = {
      label: document.getElementById("uiToolPackageLabel").value.trim(),
      description: document.getElementById("uiToolPackageDescription").value.trim() || null,
    };
    if (!payload.label) {
      AdminFormFeedback.show(form, "显示名称不能为空");
      return;
    }
    AdminFormFeedback.setSubmitting(form, true);
    let saved = false;
    let savedCode = this.editingPackageCode;
    try {
      if (this.editingPackageCode) {
        await apiFetch(`/api/v1/ui-tool-packages/${encodeURIComponent(this.editingPackageCode)}`, {
          method: "PATCH",
          body: JSON.stringify(payload),
        });
      } else {
        payload.code = document.getElementById("uiToolPackageEditCode").value.trim();
        savedCode = payload.code;
        await apiFetch("/api/v1/ui-tool-packages", {
          method: "POST",
          body: JSON.stringify(payload),
        });
      }
      saved = true;
      document.getElementById("uiToolPackageModal").close();
      showToast("UI 工具包已保存");
    } catch (error) {
      AdminFormFeedback.show(form, error);
    } finally {
      AdminFormFeedback.setSubmitting(form, false);
    }
    if (saved) {
      this.selectedPackageCode = savedCode;
      await refreshAfterSuccessfulSave("UI 工具包", () => this.refresh());
    }
  },

  async removePackage(code) {
    if (!(await confirmAction({
      title: "删除 UI 工具包？",
      message: `工具包包含工具或被 Agent 引用时不能删除，请先移除相关工具和引用。当前对象：${code}`,
      icon: "trash-2",
      tone: "danger",
      confirmLabel: "删除",
    }))) return;
    try {
      await apiFetch(`/api/v1/ui-tool-packages/${encodeURIComponent(code)}`, { method: "DELETE" });
      if (this.selectedPackageCode === code) this.selectedPackageCode = null;
      showToast("UI 工具包已删除");
      await refreshAfterSuccessfulMutation("UI 工具包已删除", async () => {
        await this.refresh();
        await loadCapabilities();
      });
    } catch (error) {
      showToast(error.message, true);
    }
  },

  renderTools(items) {
    const tbody = document.getElementById("uiToolsTableBody");
    if (!tbody) return;
    if (!this.selectedPackageCode) {
      tbody.innerHTML = `<tr><td colspan="5" class="empty-cell">请选择左侧工具包</td></tr>`;
      return;
    }
    const packageTools = items.filter((item) => item.package_code === this.selectedPackageCode);
    if (!packageTools.length) {
      tbody.innerHTML = `<tr><td colspan="5" class="empty-cell">当前工具包暂无 UI 工具</td></tr>`;
      return;
    }
    tbody.innerHTML = packageTools
      .map((tool) => `
      <tr>
        <td><strong>${escapeHtml(tool.name)}</strong></td>
        <td>${escapeHtml(tool.widget || "—")}</td>
        <td class="desc-cell" title="${escapeHtml(tool.description || "—")}">${escapeHtml(tool.description || "—")}</td>
        <td>${escapeHtml(formatDate(tool.updated_at))}</td>
        <td><div class="row-actions ui-tool-row-actions compact-icon-actions">
          ${tool.source === "builtin"
            ? `<button type="button" class="icon-btn" data-view="${escapeHtml(tool.name)}" title="查看" aria-label="查看"><i data-lucide="eye"></i></button>`
            : `<button type="button" class="icon-btn" data-edit="${escapeHtml(tool.name)}" title="编辑" aria-label="编辑"><i data-lucide="pencil"></i></button><button type="button" class="icon-btn danger" data-delete="${escapeHtml(tool.name)}" data-source="${escapeHtml(tool.source)}" title="删除" aria-label="删除"><i data-lucide="trash-2"></i></button>`}
        </div></td>
      </tr>`)
      .join("");
    tbody.querySelectorAll("[data-view]").forEach((btn) => btn.addEventListener("click", () => this.openDetails(btn.dataset.view)));
    tbody.querySelectorAll("[data-edit]").forEach((btn) => btn.addEventListener("click", () => this.openEdit(btn.dataset.edit)));
    tbody.querySelectorAll("[data-delete]").forEach((btn) => btn.addEventListener("click", () => this.remove(btn.dataset.delete, btn.dataset.source)));
    window.lucide?.createIcons();
  },

  async refresh() {
    const tbody = document.getElementById("uiToolsTableBody");
    const tree = document.getElementById("uiToolPackageTree");
    if (!tbody || !tree) return;
    tbody.innerHTML = `<tr><td colspan="5" class="empty-cell">加载中…</td></tr>`;
    tree.innerHTML = `<div class="ui-tool-tree-state">加载中…</div>`;
    try {
      const [packageData, data] = await Promise.all([
        apiFetch("/api/v1/ui-tool-packages"),
        apiFetch("/api/v1/ui-tools"),
      ]);
      showDbBanner(false);
      this.packages = packageData.items || [];
      const items = data.items || [];
      this._lastTools = items;
      if (this.selectedPackageCode && !this.packages.some((item) => item.code === this.selectedPackageCode)) this.selectedPackageCode = null;
      if (!this.selectedPackageCode && this.packages.length) {
        this.selectedPackageCode = this.packages.find((item) => item.source === "manual")?.code
          || this.packages.find((item) => item.source === "builtin")?.code
          || null;
      }
      this.renderPackages();
      this.renderSelectedPackage();
      this.renderTools(items);
      this.populatePackageSelect(this.selectedPackageCode);
    } catch (err) {
      tree.innerHTML = `<div class="ui-tool-tree-state">${escapeHtml(err.message)}<br /><button type="button" class="btn" data-ui-tools-retry>重新加载</button></div>`;
      tbody.innerHTML = `<tr><td colspan="5" class="empty-cell">加载失败</td></tr>`;
      tree.querySelector("[data-ui-tools-retry]")?.addEventListener("click", () => this.refresh());
    }
  },

  async openDetails(name) {
    try {
      const tool = await apiFetch(`/api/v1/ui-tools/${encodeURIComponent(name)}`);
      this.editingName = null;
      this.editingSource = tool.source;
      this.editingToolPackageCode = tool.package_code;
      document.getElementById("uiToolModalTitle").textContent = `查看 · ${tool.name}`;
      document.getElementById("uiToolName").value = tool.name;
      document.getElementById("uiToolDescription").value = tool.description || "";
      this.setWidgetValue(tool.widget || "");
      document.getElementById("uiToolMetadata").value = tool.metadata
        ? JSON.stringify(tool.metadata, null, 2)
        : "";
      this.setFormMode("readonly");
      this.loadSchemas(tool.parameters_json_schema, tool.result_schema, { readOnly: true });
      document.getElementById("uiToolModal").showModal();
    } catch (err) {
      showToast(err.message, true);
    }
  },

  setFormMode(mode) {
    const isCreate = mode === "create";
    const isReadOnly = mode === "readonly";
    this.formMode = mode;
    document.getElementById("uiToolTemplateSection").classList.toggle("hidden", !isCreate);
    document.getElementById("uiToolName").disabled = !isCreate;
    document.getElementById("uiToolDescription").readOnly = isReadOnly;
    document.getElementById("uiToolWidget").disabled = isReadOnly;
    document.getElementById("uiToolWidgetCustom").readOnly = isReadOnly;
    document.getElementById("uiToolMetadata").readOnly = isReadOnly;
    document.getElementById("saveUiToolBtn").classList.toggle("hidden", isReadOnly);
    document.getElementById("cancelUiToolBtn").textContent = isReadOnly ? "关闭" : "取消";
    this.setSchemaReadOnly(isReadOnly);
  },

  openCreate() {
    const selected = this.packages.find((item) => item.code === this.selectedPackageCode);
    if (!selected || selected.source !== "manual") {
      showToast("请先在左侧选择一个自定义 UI 工具包", true);
      return;
    }
    this.editingName = null;
    this.editingSource = "manual";
    this.editingToolPackageCode = selected.code;
    document.getElementById("uiToolModalTitle").textContent = "新建自定义 UI 工具";
    document.getElementById("uiToolName").value = "";
    document.getElementById("uiToolTemplate").value = "confirm";
    this.setFormMode("create");
    this.populatePackageSelect(selected.code);
    this.applyTemplate("confirm", { fillName: true });
    document.getElementById("uiToolModal").showModal();
  },

  async openEdit(name) {
    try {
      const tool = await apiFetch(`/api/v1/ui-tools/${encodeURIComponent(name)}`);
      this.editingName = name;
      this.editingSource = tool.source;
      this.editingToolPackageCode = tool.package_code;
      document.getElementById("uiToolModalTitle").textContent = `编辑 · ${name}`;
      document.getElementById("uiToolName").value = tool.name;
      document.getElementById("uiToolDescription").value = tool.description || "";
      this.setWidgetValue(tool.widget || "");
      document.getElementById("uiToolMetadata").value = tool.metadata
        ? JSON.stringify(tool.metadata, null, 2)
        : "";
      this.populatePackageSelect(tool.package_code);
      this.setFormMode("edit");
      this.loadSchemas(tool.parameters_json_schema, tool.result_schema);
      document.getElementById("uiToolModal").showModal();
    } catch (err) {
      showToast(err.message, true);
    }
  },

  async save(event) {
    event.preventDefault();
    if (this.formMode === "readonly") return;
    const form = event.currentTarget;
    AdminFormFeedback.clear(form);
    let parameters_json_schema;
    let result_schema;
    let metadata;
    try {
      parameters_json_schema = parseUiToolSchema(this.paramsEl()?.value, {
        label: "参数 Schema",
        required: true,
        requireObject: true,
      });
      result_schema = parseUiToolSchema(this.resultEl()?.value, { label: "结果 Schema" });
      metadata = parseJsonOrEmpty(document.getElementById("uiToolMetadata").value);
    } catch (err) {
      AdminFormFeedback.show(form, err);
      return;
    }

    const widget = this.getWidgetValue();
    const description = document.getElementById("uiToolDescription").value.trim();
    const package_code = this.editingToolPackageCode
      || document.getElementById("uiToolPackageCode")?.value
      || "";
    const owningPackage = this.packages.find(
      (item) => item.code === package_code && item.source === "manual",
    );
    if (!owningPackage) {
      AdminFormFeedback.show(form, "UI 工具必须归属于创建它的自定义工具包");
      return;
    }
    if (!description) {
      AdminFormFeedback.show(form, "描述不能为空");
      return;
    }

    AdminFormFeedback.setSubmitting(form, true);
    let saved = false;
    try {
      if (this.editingName) {
        await apiFetch(`/api/v1/ui-tools/${encodeURIComponent(this.editingName)}`, {
          method: "PATCH",
          body: JSON.stringify({
            package_code,
            description,
            parameters_json_schema,
            widget,
            result_schema: result_schema ?? null,
            metadata: metadata ?? null,
          }),
        });
        showToast("已保存");
      } else {
        await apiFetch("/api/v1/ui-tools", {
          method: "POST",
          body: JSON.stringify({
            name: document.getElementById("uiToolName").value.trim(),
            package_code,
            description,
            parameters_json_schema,
            widget,
            result_schema: result_schema ?? null,
            metadata: metadata ?? null,
          }),
        });
        showToast("已注册");
      }
      saved = true;
      document.getElementById("uiToolModal").close();
    } catch (err) {
      AdminFormFeedback.show(form, err);
    } finally {
      AdminFormFeedback.setSubmitting(form, false);
    }
    if (saved) {
      await refreshAfterSuccessfulSave("UI 工具配置", async () => {
        await this.refresh();
        await loadCapabilities();
      });
    }
  },

  async remove(name, source) {
    if (source === "builtin") {
      showToast("内置工具不可删除", true);
      return;
    }
    if (!(await confirmAction({
      title: "删除 UI 工具？",
      message: `删除后，Agent 将无法再发起该页面交互。当前对象：${name}`,
      icon: "trash-2",
      tone: "danger",
      confirmLabel: "删除",
    }))) return;
    try {
      await apiFetch(`/api/v1/ui-tools/${encodeURIComponent(name)}`, { method: "DELETE" });
      showToast("已删除");
      await refreshAfterSuccessfulMutation("UI 工具已删除", async () => {
        await this.refresh();
        await loadCapabilities();
      });
    } catch (err) {
      showToast(err.message, true);
    }
  },
};

window.uiToolsAdmin = uiToolsAdmin;

document.addEventListener("DOMContentLoaded", () => {
  uiToolsAdmin.initTemplates();
  uiToolsAdmin.initWidgetPicker();
  document.getElementById("newUiToolPackageBtn")?.addEventListener("click", () => uiToolsAdmin.openPackageCreate());
  document.getElementById("newUiToolBtn")?.addEventListener("click", () => uiToolsAdmin.openCreate());
  document.getElementById("editUiToolPackageBtn")?.addEventListener("click", () => uiToolsAdmin.openPackageEdit(uiToolsAdmin.selectedPackageCode));
  document.getElementById("deleteUiToolPackageBtn")?.addEventListener("click", () => uiToolsAdmin.removePackage(uiToolsAdmin.selectedPackageCode));
  document.getElementById("uiToolPackageForm")?.addEventListener("submit", (e) => uiToolsAdmin.savePackage(e));
  document.getElementById("uiToolForm")?.addEventListener("submit", (e) => uiToolsAdmin.save(e));
  document.getElementById("closeUiToolModal")?.addEventListener("click", () => {
    document.getElementById("uiToolModal").close();
  });
  document.getElementById("closeUiToolPackageModal")?.addEventListener("click", () => {
    document.getElementById("uiToolPackageModal").close();
  });
  document.getElementById("cancelUiToolPackageBtn")?.addEventListener("click", () => {
    document.getElementById("uiToolPackageModal").close();
  });
  document.getElementById("cancelUiToolBtn")?.addEventListener("click", () => {
    document.getElementById("uiToolModal").close();
  });
});
