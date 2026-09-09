/**
 * Visual JSON Schema editor for UI tool params (object) and result (scalar | object).
 */
const uiSchemaEditor = (() => {
  const SCALAR_TYPES = ["string", "number", "integer", "boolean", "array"];
  const ALL_TYPES = [...SCALAR_TYPES, "object"];
  let _uid = 0;

  function refreshIcons() {
    window.requestAnimationFrame(() => window.lucide?.createIcons());
  }

  function nextId() {
    _uid += 1;
    return `sf-${_uid}`;
  }

  function escapeAttr(text) {
    return String(text ?? "")
      .replace(/&/g, "&amp;")
      .replace(/"/g, "&quot;")
      .replace(/</g, "&lt;");
  }

  function typeOptions(selected, { allowEmpty = false } = {}) {
    let html = allowEmpty ? `<option value="">—</option>` : "";
    html += ALL_TYPES.map(
      (t) => `<option value="${t}"${t === selected ? " selected" : ""}>${t}</option>`
    ).join("");
    return html;
  }

  function coerceDefault(raw, type) {
    const text = String(raw).trim();
    if (text === "") return undefined;
    if (type === "boolean") return text === "true" || text === "1";
    if (type === "number") return Number(text);
    if (type === "integer") return parseInt(text, 10);
    return text;
  }

  function parseObjectFields(schema) {
    if (!schema || schema.type !== "object") return [];
    const props = schema.properties || {};
    const required = new Set(schema.required || []);
    return Object.entries(props).map(([name, def]) => fieldFromDef(name, def, required.has(name)));
  }

  function fieldFromDef(name, def, required) {
    const type = def?.oneOf?.[0]?.type || def?.type || "string";
    const field = {
      id: nextId(),
      name,
      type,
      description: def?.description || "",
      default:
        def?.default !== undefined && def?.default !== null ? String(def.default) : "",
      required: Boolean(required),
      children: [],
    };
    if (type === "object" && def?.properties) {
      field.children = parseObjectFields(def);
    }
    return field;
  }

  function buildObjectSchema(fields, { includeDefault }) {
    const properties = {};
    const required = [];
    for (const field of fields) {
      const name = field.name.trim();
      if (!name) continue;
      properties[name] = buildFieldDef(field, { includeDefault });
      if (field.required) required.push(name);
    }
    const schema = { type: "object", properties };
    if (required.length) schema.required = required;
    return schema;
  }

  function buildFieldDef(field, { includeDefault }) {
    const def = { type: field.type };
    if (field.description.trim()) def.description = field.description.trim();
    if (includeDefault) {
      const dv = coerceDefault(field.default, field.type);
      if (dv !== undefined) def.default = dv;
    }
    if (field.type === "object") {
      const nested = buildObjectSchema(field.children || [], { includeDefault });
      def.properties = nested.properties;
      if (nested.required?.length) def.required = nested.required;
    }
    if (field.type === "array") {
      def.items = { type: "string" };
    }
    return def;
  }

  function collectFieldsFromParent(fieldsEl) {
    if (!fieldsEl) return [];
    return Array.from(fieldsEl.querySelectorAll(":scope > .schema-field-row")).map((row) => {
      const name = row.querySelector(".schema-name")?.value || "";
      const type = row.querySelector(".schema-type")?.value || "string";
      const description = row.querySelector(".schema-desc")?.value || "";
      const defaultEl = row.querySelector(".schema-default");
      const defaultVal = defaultEl ? defaultEl.value : "";
      const required = row.querySelector(".schema-required")?.value === "yes";
      const nestedFieldsEl = row.querySelector(":scope > .schema-children > .schema-fields");
      const children = nestedFieldsEl ? collectFieldsFromParent(nestedFieldsEl) : [];
      return { id: row.dataset.id, name, type, description, default: defaultVal, required, children };
    });
  }

  function bindNestedAddButton(childrenWrap, depth, { showDefault, readOnly }) {
    const fieldsEl = childrenWrap.querySelector(".schema-fields");
    const addBtn = childrenWrap.querySelector(".schema-add-btn-nested");
    if (!addBtn || !fieldsEl) return;
    addBtn.addEventListener("click", () => {
      appendField(fieldsEl, depth, { showDefault, readOnly });
    });
    addBtn.classList.toggle("hidden", readOnly);
  }

  function renderFieldRow(field, depth, { showDefault, readOnly }) {
    const row = document.createElement("div");
    row.className = "schema-field-row";
    row.dataset.id = field.id;

    const defaultCol = showDefault
      ? `<input type="text" class="schema-default" placeholder="默认值" value="${escapeAttr(field.default)}"${readOnly ? " readonly" : ""} />`
      : "";

    row.innerHTML = `
      <div class="schema-field-cols${showDefault ? "" : " no-default"}">
        <input type="text" class="schema-name" placeholder="列名" value="${escapeAttr(field.name)}"${readOnly ? " readonly" : ""} />
        <select class="schema-type"${readOnly ? " disabled" : ""}>${typeOptions(field.type)}</select>
        <input type="text" class="schema-desc" placeholder="描述" value="${escapeAttr(field.description)}"${readOnly ? " readonly" : ""} />
        ${defaultCol}
        <select class="schema-required"${readOnly ? " disabled" : ""}>
          <option value="no"${field.required ? "" : " selected"}>否</option>
          <option value="yes"${field.required ? " selected" : ""}>是</option>
        </select>
        <button type="button" class="schema-remove icon-btn" title="删除" aria-label="删除字段"${readOnly ? " disabled" : ""}><i data-lucide="trash-2" aria-hidden="true"></i></button>
      </div>
      <div class="schema-children${field.type === "object" ? "" : " hidden"}">
        <div class="schema-fields"></div>
        <button type="button" class="btn schema-add-btn schema-add-btn-nested"${readOnly ? " disabled hidden" : ""}><i data-lucide="plus" aria-hidden="true"></i>添加字段</button>
      </div>
    `;

    const typeSelect = row.querySelector(".schema-type");
    const childrenWrap = row.querySelector(".schema-children");
    const nestedFieldsEl = childrenWrap.querySelector(".schema-fields");

    typeSelect.addEventListener("change", () => {
      const isObject = typeSelect.value === "object";
      childrenWrap.classList.toggle("hidden", !isObject);
    });

    row.querySelector(".schema-remove")?.addEventListener("click", () => row.remove());

    bindNestedAddButton(childrenWrap, depth + 1, { showDefault, readOnly });

    if (field.type === "object" && field.children?.length) {
      for (const child of field.children) {
        nestedFieldsEl.appendChild(renderFieldRow(child, depth + 1, { showDefault, readOnly }));
      }
    }

    return row;
  }

  function appendField(fieldsEl, depth, { showDefault, readOnly }) {
    const field = {
      id: nextId(),
      name: "",
      type: "string",
      description: "",
      default: "",
      required: false,
      children: [],
    };
    fieldsEl.appendChild(renderFieldRow(field, depth, { showDefault, readOnly }));
    refreshIcons();
  }

  function mountFieldsBlock(fieldsEl, fields, depth, { showDefault, readOnly }, addBtn) {
    for (const f of fields) {
      fieldsEl.appendChild(renderFieldRow(f, depth, { showDefault, readOnly }));
    }
    if (addBtn) {
      addBtn.addEventListener("click", () => appendField(fieldsEl, depth, { showDefault, readOnly }));
      addBtn.classList.toggle("hidden", readOnly);
    }
    refreshIcons();
  }

  function renderParamsEditor(container, schema, { readOnly = false } = {}) {
    container.innerHTML = "";
    container.className = "schema-editor schema-editor-params";
    const fields = parseObjectFields(schema);

    const wrap = document.createElement("div");
    wrap.className = "schema-fields-root";
    wrap.innerHTML = `
      <div class="schema-field-header">
        <span>列名</span><span>类型</span><span>描述</span><span>默认值</span><span>必填</span><span></span>
      </div>
      <div class="schema-fields"></div>
      <button type="button" class="btn schema-add-btn"${readOnly ? " disabled hidden" : ""}><i data-lucide="plus" aria-hidden="true"></i>添加字段</button>
    `;
    container.appendChild(wrap);

    const fieldsEl = wrap.querySelector(".schema-fields");
    const addBtn = wrap.querySelector(".schema-add-btn");
    mountFieldsBlock(fieldsEl, fields, 0, { showDefault: true, readOnly }, addBtn);
  }

  function renderResultEditor(container, schema, { readOnly = false } = {}) {
    container.innerHTML = "";
    container.className = "schema-editor schema-editor-result";

    let rootType = "";
    let fields = [];
    if (schema) {
      if (schema.type === "object") {
        rootType = "object";
        fields = parseObjectFields(schema);
      } else if (schema.type) {
        rootType = schema.type;
      }
    }

    const wrap = document.createElement("div");
    wrap.className = "schema-result-root";
    wrap.innerHTML = `
      <div class="schema-result-type-row">
        <span class="schema-result-type-label">返回类型</span>
        <select class="schema-result-type"${readOnly ? " disabled" : ""}>${typeOptions(rootType, { allowEmpty: true })}</select>
      </div>
      <div class="schema-result-object hidden">
        <div class="schema-field-header no-default">
          <span>列名</span><span>类型</span><span>描述</span><span>必填</span><span></span>
        </div>
        <div class="schema-fields"></div>
        <button type="button" class="btn schema-add-btn"${readOnly ? " disabled hidden" : ""}><i data-lucide="plus" aria-hidden="true"></i>添加字段</button>
      </div>
    `;
    container.appendChild(wrap);

    const typeSelect = wrap.querySelector(".schema-result-type");
    const objectPanel = wrap.querySelector(".schema-result-object");
    const fieldsEl = wrap.querySelector(".schema-fields");
    const addBtn = objectPanel.querySelector(".schema-add-btn");

    function syncObjectPanel() {
      const isObject = typeSelect.value === "object";
      objectPanel.classList.toggle("hidden", !isObject);
    }

    typeSelect.addEventListener("change", syncObjectPanel);
    mountFieldsBlock(fieldsEl, fields, 0, { showDefault: false, readOnly }, addBtn);
    syncObjectPanel();
  }

  function getParamsSchema(container) {
    const fieldsEl = container.querySelector(".schema-fields-root > .schema-fields");
    if (!fieldsEl) throw new Error("请配置参数字段");
    const schema = buildObjectSchema(collectFieldsFromParent(fieldsEl), { includeDefault: true });
    if (!Object.keys(schema.properties).length) throw new Error("请至少添加一个参数字段");
    return schema;
  }

  function getResultSchema(container) {
    const type = container.querySelector(".schema-result-type")?.value || "";
    if (!type) return null;
    if (type === "object") {
      const fieldsEl = container.querySelector(".schema-result-object > .schema-fields");
      const schema = buildObjectSchema(collectFieldsFromParent(fieldsEl), { includeDefault: false });
      if (!Object.keys(schema.properties).length) throw new Error("结果类型为 object 时请至少添加一个字段");
      return schema;
    }
    return { type };
  }

  function setReadOnly(container, readOnly) {
    container.querySelectorAll("input, select, button.schema-add-btn, button.schema-add-btn-nested, button.schema-remove").forEach((el) => {
      if (
        el.classList.contains("schema-type") ||
        el.classList.contains("schema-required") ||
        el.classList.contains("schema-result-type")
      ) {
        el.disabled = readOnly;
      } else if (el.tagName === "BUTTON") {
        el.disabled = readOnly;
        if (el.classList.contains("schema-add-btn") || el.classList.contains("schema-add-btn-nested")) {
          el.classList.toggle("hidden", readOnly);
        }
      } else if (el.tagName === "INPUT") {
        el.readOnly = readOnly;
      }
    });
  }

  return {
    renderParamsEditor,
    renderResultEditor,
    getParamsSchema,
    getResultSchema,
    setReadOnly,
  };
})();

window.uiSchemaEditor = uiSchemaEditor;
