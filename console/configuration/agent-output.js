/* Agent-wide output draft. This is a classic script because the admin shell is loaded lazily. */
(() => {
  const escapeOutputHtml = value => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
  const parseSchema = (text, allowBoolean = false) => {
    let schema;
    try { schema = JSON.parse(text); } catch (error) { throw new Error(`JSON Schema 无效：${error.message}`); }
    if (allowBoolean && typeof schema === "boolean") return schema;
    if (!schema || typeof schema !== "object" || Array.isArray(schema)) throw new Error("JSON Schema 必须是对象");
    return schema;
  };
  const canUseCards = schema => schema?.type === "object"
    && (!schema.properties || (typeof schema.properties === "object" && !Array.isArray(schema.properties) && Object.values(schema.properties).every(value => typeof value === "boolean" || (value !== null && typeof value === "object" && !Array.isArray(value)))))
    && (!schema.required || (Array.isArray(schema.required) && schema.required.every(value => typeof value === "string")))
    && !["allOf", "anyOf", "oneOf", "$ref", "if", "then", "else"].some(key => key in schema);
  const schemaType = schema => Array.isArray(schema?.type) ? schema.type.join(" | ") : schema?.type || (schema?.$ref ? "$ref" : "—");
  const schemaSummary = schema => schema?.type === "object" ? `${Object.keys(schema.properties || {}).length} 个字段` : schema?.type === "array" ? `${Object.keys(schema.items?.properties || {}).length} 个字段 / 项` : "";

  class OutputDraft {
    constructor(value = {}) {
      this.enabled = value.structured_output_enabled === true;
      const config = value.output_config;
      this.hasDefinition = Boolean(config);
      this.name = config?.name || "";
      this.description = config?.description || "";
      this.schemaText = JSON.stringify(config?.json_schema || { type: "object", properties: {}, additionalProperties: false }, null, 2);
      this.invalidDetails = new Map();
    }
    schema() { return parseSchema(this.schemaText); }
    setSchemaText(text) { this.hasDefinition = true; this.schemaText = text; this.invalidDetails.clear(); }
    mutateSchema(callback) {
      const schema = this.schema();
      callback(schema);
      this.hasDefinition = true;
      this.schemaText = JSON.stringify(schema, null, 2);
    }
    required(key, enabled) {
      this.mutateSchema(schema => {
        const required = new Set(schema.required || []);
        if (enabled) required.add(key); else required.delete(key);
        if (required.size) schema.required = [...required]; else delete schema.required;
      });
    }
    remove(key) {
      this.invalidDetails.delete(key);
      this.mutateSchema(schema => {
        delete schema.properties?.[key];
        if (schema.required) { schema.required = schema.required.filter(item => item !== key); if (!schema.required.length) delete schema.required; }
      });
    }
    detail(key, value) {
      const pending = { ...value };
      try {
        const field = parseSchema(value.text, true);
        this.mutateSchema(schema => {
          schema.properties ||= {};
          if (key !== value.key && Object.hasOwn(schema.properties, value.key)) throw new Error("结构编码已存在");
          const properties = Object.entries(schema.properties).map(([name, child]) => name === key ? [value.key, field] : [name, child]);
          schema.properties = Object.fromEntries(properties);
          if (schema.required) schema.required = schema.required.map(name => name === key ? value.key : name);
        });
        this.invalidDetails.delete(key);
        return value.key;
      } catch (error) {
        this.invalidDetails.set(key, pending);
        throw error;
      }
    }
    payload() {
      if (this.invalidDetails.size) throw new Error("结构详情中仍有无效的 JSON Schema，请修正后保存");
      if (!this.hasDefinition && !this.enabled) return { structured_output_enabled: false };
      if (!/^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(this.name)) {
        const error = new Error(this.name ? "结果名称须为 1–64 位英文、数字和下划线，且以英文字母开头" : "请先填写结果名称，例如 lease_result");
        error.field = "name";
        throw error;
      }
      return { structured_output_enabled: this.enabled, output_config: { name: this.name, description: this.description || null, json_schema: this.schema() } };
    }
  }

  let draft = new OutputDraft();
  let mode = "fields";
  let detailKey = null;
  let newDetail = false;
  const element = id => document.getElementById(id);
  const markDirty = () => element("agentForm")?.dispatchEvent(new Event("input", { bubbles: true }));
  const showError = error => {
    const nameError = error?.field === "name";
    for (const [id, active] of [["agentOutputNameError", nameError], ["agentOutputError", !nameError]]) {
      const target = element(id);
      if (target) { target.textContent = active ? error?.message || error || "" : ""; target.classList.toggle("hidden", !target.textContent); }
    }
    element("agentOutputName")?.setAttribute("aria-invalid", String(nameError));
  };
  const showDetailError = error => {
    const target = element("agentOutputDetailError");
    target.textContent = error?.message || error || "";
    target.classList.toggle("hidden", !target.textContent);
  };
  const syncSchema = () => window.DeepAgentJsonInput?.syncJsonInput(element("agentOutputSchema"), element("agentOutputSchemaHighlight"));
  const syncDetail = () => window.DeepAgentJsonInput?.syncJsonInput(element("agentOutputFieldSchema"), element("agentOutputFieldSchemaHighlight"));
  const notify = () => { markDirty(); showError(""); };

  function renderFields() {
    const target = element("agentOutputFields");
    if (!target) return;
    let schema;
    try { schema = draft.schema(); } catch (error) { showError(error.message); return; }
    const cardsAllowed = canUseCards(schema);
    element("agentOutputFieldsTab").disabled = !cardsAllowed;
    element("agentOutputFieldsTab").title = cardsAllowed ? "字段配置" : "此定义使用原生 JSON Schema 编辑";
    if (!cardsAllowed) mode = "schema";
    element("agentOutputAdd").classList.toggle("hidden", mode !== "fields");
    element("agentOutputFields").classList.toggle("hidden", mode !== "fields");
    element("agentOutputSchemaEditor").classList.toggle("hidden", mode !== "schema");
    element("agentOutputFieldsTab").classList.toggle("active", mode === "fields");
    element("agentOutputSchemaTab").classList.toggle("active", mode === "schema");
    target.innerHTML = Object.entries(schema.properties || {}).map(([key, field]) => `<article class="output-field-card${draft.invalidDetails.has(key) ? " invalid" : ""}">
      <button type="button" class="output-field-body" data-output-edit="${escapeOutputHtml(key)}" aria-label="编辑结构 ${escapeOutputHtml(key)}"><span class="output-field-line"><code>${escapeOutputHtml(key)}</code><span class="output-field-type">${escapeOutputHtml(schemaType(field))}</span><span>${escapeOutputHtml(schemaSummary(field))}</span>${draft.invalidDetails.has(key) ? '<span class="output-field-invalid">定义无效</span>' : ""}</span><span class="output-field-description"><strong>${escapeOutputHtml(field.title || "未命名结构")}</strong><span>${escapeOutputHtml(field.description || "")}</span></span></button>
      <div class="output-field-actions"><label><input type="checkbox" data-output-required="${escapeOutputHtml(key)}" ${(schema.required || []).includes(key) ? "checked" : ""}>必须</label><button type="button" class="icon-action danger" data-output-delete="${escapeOutputHtml(key)}" title="删除结构" aria-label="删除结构 ${escapeOutputHtml(key)}"><i data-lucide="trash-2"></i></button></div></article>`).join("") || '<div class="agent-output-empty"><span>尚未添加结构</span><span>点击 <button type="button" class="btn text compact" data-output-add>添加结构</button> 开始配置</span><span>也可保存空对象定义。</span></div>';
    window.lucide?.createIcons();
  }

  function render() {
    if (!element("agentOutputEnabled")) return;
    element("agentOutputEnabled").checked = draft.enabled;
    element("agentOutputEnabledLabel").textContent = draft.enabled ? "已开启" : "已关闭";
    element("agentOutputSettings").classList.toggle("hidden", !draft.enabled);
    element("agentOutputName").value = draft.name;
    element("agentOutputDescription").value = draft.description;
    element("agentOutputSchema").value = draft.schemaText;
    syncSchema();
    renderFields();
  }

  function closeDetail() {
    detailKey = null;
    newDetail = false;
    showDetailError("");
  }

  function confirmDetail() {
    try {
      const key = element("agentOutputFieldKey").value.trim();
      if (!key) throw new Error("请填写结构编码");
      const field = parseSchema(element("agentOutputFieldSchema").value, true);
      draft.mutateSchema(schema => {
        schema.properties ||= {};
        if ((newDetail || key !== detailKey) && Object.hasOwn(schema.properties, key)) throw new Error("结构编码已存在，请使用其他编码");
        schema.properties = Object.fromEntries(newDetail
          ? [...Object.entries(schema.properties), [key, field]]
          : Object.entries(schema.properties).map(([name, child]) => name === detailKey ? [key, field] : [name, child]));
        if (!newDetail && schema.required) schema.required = schema.required.map(name => name === detailKey ? key : name);
      });
      draft.invalidDetails.delete(detailKey);
      closeDetail();
      element("agentOutputDetail").close();
      notify(); render();
    } catch (error) { showDetailError(error); }
  }

  const controller = {
    reset(value = {}) { draft = new OutputDraft(value); mode = "fields"; closeDetail(); element("agentOutputDetail")?.close(); showError(""); render(); },
    payload() {
      if (detailKey != null) throw new Error("请先确定或取消结构详情中的修改");
      try { return draft.payload(); } catch (error) { showError(error); if (error.field === "name") element("agentOutputName").focus(); throw error; }
    },
    init() {
      if (!element("agentOutputEnabled") || element("agentOutputEnabled").dataset.bound) return;
      element("agentOutputEnabled").dataset.bound = "1";
      element("agentOutputEnabled").addEventListener("change", event => { draft.enabled = event.target.checked; notify(); render(); });
      for (const [id, key] of [["agentOutputName", "name"], ["agentOutputDescription", "description"]]) element(id).addEventListener("input", event => { draft[key] = event.target.value; draft.hasDefinition = true; notify(); });
      element("agentOutputSchema").addEventListener("input", event => { draft.setSchemaText(event.target.value); syncSchema(); notify(); try { element("agentOutputFieldsTab").disabled = !canUseCards(draft.schema()); } catch (error) { showError(error.message); } });
      element("agentOutputSchema").addEventListener("scroll", syncSchema);
      element("agentOutputFieldsTab").addEventListener("click", () => { try { draft.schema(); mode = "fields"; renderFields(); } catch (error) { showError(error.message); } });
      element("agentOutputSchemaTab").addEventListener("click", () => { mode = "schema"; element("agentOutputSchema").value = draft.schemaText; syncSchema(); renderFields(); });
      element("agentOutputCheck").addEventListener("click", () => { try { controller.payload(); showError(""); showToast("定义已检查，完整约束将在保存时由服务端校验"); } catch (error) { showError(error); } });
      element("agentOutputAdd").addEventListener("click", addDetail);
      element("agentOutputFields").addEventListener("click", event => {
        const edit = event.target.closest("[data-output-edit]");
        const remove = event.target.closest("[data-output-delete]");
        if (event.target.closest("[data-output-add]")) addDetail();
        if (edit) openDetail(edit.dataset.outputEdit);
        if (remove) { draft.remove(remove.dataset.outputDelete); notify(); render(); }
      });
      element("agentOutputFields").addEventListener("change", event => { if (event.target.matches("[data-output-required]")) { draft.required(event.target.dataset.outputRequired, event.target.checked); notify(); element("agentOutputSchema").value = draft.schemaText; syncSchema(); } });
      element("agentOutputDetailClose").addEventListener("click", () => element("agentOutputDetail").close());
      element("agentOutputDetailCancel").addEventListener("click", () => element("agentOutputDetail").close());
      element("agentOutputDetailConfirm").addEventListener("click", confirmDetail);
      element("agentOutputDetail").addEventListener("close", closeDetail);
      element("agentOutputFieldSchema").addEventListener("input", () => { syncDetail(); showDetailError(""); });
      element("agentOutputFieldSchema").addEventListener("scroll", syncDetail);
      element("agentOutputFieldKey").addEventListener("input", () => showDetailError(""));
      render();
    },
  };
  function addDetail() {
    let key = "field_1";
    const schema = draft.schema();
    let index = 1;
    while (Object.hasOwn(schema.properties || {}, key)) key = `field_${++index}`;
    openDetail(key, true);
  }
  function openDetail(key, creating = false) {
    detailKey = key;
    newDetail = creating;
    showDetailError("");
    const pending = draft.invalidDetails.get(key);
    element("agentOutputFieldKey").value = pending?.key ?? key;
    element("agentOutputFieldSchema").value = pending?.text ?? JSON.stringify(creating ? { type: "string", title: "新结构" } : draft.schema().properties[key], null, 2);
    syncDetail();
    element("agentOutputDetail").showModal();
  }
  window.DeepAgentOutputEditor = controller;
  window.DeepAgentOutputDraft = { OutputDraft, canUseCards, schemaType, schemaSummary, escapeOutputHtml };
  document.addEventListener("DOMContentLoaded", () => controller.init());
})();
