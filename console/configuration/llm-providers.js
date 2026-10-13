const providerAdminState = {
  selectedId: null,
  afterSave: null,
  modelTests: new Map(),
  testBatch: null,
  accessMode: "api",
  flowId: null,
  verified: false,
  existingAuth: false,
};

const providerCatalogPickerState = {
  provider: null,
  choices: [],
  sources: [],
  sourceId: "",
  selectedIds: new Set(),
  createdKeys: new Set(),
  query: "",
  generation: 0,
  controller: null,
  loading: false,
  submitting: false,
};

function providerTypeByCode(code) {
  return (state.llmProviderTypes || []).find((item) => item.code === code) || null;
}

function providerTypeLabel(provider) {
  return providerTypeByCode(provider.provider_type)?.label || (provider.provider_type === "openai_codex" ? "ChatGPT" : provider.provider_type) || "—";
}

function providerModels(providerId) {
  return (state.llmModels || []).filter((model) => model.provider_id === providerId || model.provider?.id === providerId);
}

const PROVIDER_BRANDS = {
  deepseek: { src: "./assets/provider-logos/deepseek.ico", tone: "deepseek" },
  moonshotai: { src: "./assets/provider-logos/kimi.ico", tone: "moonshot" },
  alibaba: { src: "./assets/provider-logos/qwen.png", tone: "alibaba" },
  zai: { src: "./assets/provider-logos/zai.svg", tone: "zai" },
  openrouter: { src: "./assets/provider-logos/openrouter.png", tone: "openrouter" },
  openai: { src: "./assets/provider-logos/openai.svg?v=20260926-centered-2", tone: "openai" },
  openai_codex: { src: "./assets/provider-logos/openai.svg?v=20260926-centered-2", tone: "openai" },
  anthropic: { src: "./assets/provider-logos/anthropic.png", tone: "anthropic" },
  anthropic_compatible: { src: "./assets/provider-logos/anthropic.png", tone: "anthropic" },
  minimax: { src: "./assets/provider-logos/minimax.ico", tone: "minimax" },
  stepfun: { src: "./assets/provider-logos/stepfun.svg", tone: "stepfun" },
  google: { src: "./assets/provider-logos/google-gemini.png", tone: "google" },
  openai_compatible: { src: "./assets/provider-logos/openai-compatible.svg", tone: "compatible" },
};

function providerBrand(provider) {
  return PROVIDER_BRANDS[provider?.provider_type] || PROVIDER_BRANDS.openai_compatible;
}

function providerBrandMarkup(provider) {
  const brand = providerBrand(provider);
  return `<span class="provider-brand-mark ${escapeHtml(brand.tone)}" aria-hidden="true"><img src="${escapeHtml(brand.src)}" alt="" /></span>`;
}

function providerModelFacts(model) {
  const context = window.__LlmModelConfigurationUI?.formatTokenCount?.(model.context_window_tokens) || "—";
  const labels = { text: "文本", image: "图像", audio: "音频", video: "视频" };
  const modalities = (model.input_modalities || []).map((item) => labels[item] || item).join("、") || "未声明";
  return `<span><i data-lucide="image"></i>${escapeHtml(modalities)}</span><span><i data-lucide="scan-line"></i>${escapeHtml(context)}</span>`;
}

function providerCatalogId(provider) {
  return window.DeepAgentProviderCatalog?.catalogProviderIdForType(provider?.provider_type) || null;
}

function isGenericCatalogProvider(provider) {
  return Boolean(window.DeepAgentProviderCatalog?.isGenericCatalogProvider(provider?.provider_type));
}

function catalogChoiceKey(choice) {
  return `${providerCatalogPickerState.sourceId}:${choice.id}`;
}

function renderProviderTypeOptions(selectedCode = "") {
  const select = document.getElementById("llmProviderType");
  if (!select) return;
  const types = (state.llmProviderTypes || []).filter((item) => item.code !== "openai_codex" || selectedCode === "openai_codex");
  const groups = window.DeepAgentProviderCatalog.groupProviderTypes(types);
  select.innerHTML = groups.map((group) => `<optgroup label="${escapeHtml(group.label)}">${group.items.map((item) =>
    `<option value="${escapeHtml(item.code)}" ${item.code === selectedCode ? "selected" : ""}>${escapeHtml(item.label)}</option>`,
  ).join("")}</optgroup>`).join("");
}

function suggestedProviderName(mode = providerAdminState.accessMode) {
  return mode === "subscription"
    ? (document.getElementById("llmProviderSubscriptionType")?.selectedOptions[0]?.textContent.trim() || "GPT")
    : (providerTypeByCode(document.getElementById("llmProviderType")?.value)?.label || "");
}

function syncSuggestedProviderName() {
  if (document.getElementById("llmProviderForm")?.dataset.providerId) return;
  const input = document.getElementById("llmProviderName");
  const previous = input.dataset.autoName || "";
  if (input.value.trim() && input.value !== previous) return;
  const suggested = suggestedProviderName();
  input.value = suggested;
  input.dataset.autoName = suggested;
}

function setProviderAuthState(message, status = "idle") {
  const text = document.getElementById("llmProviderSubscriptionState");
  text.textContent = message;
  text.closest(".provider-auth-status").dataset.state = status;
}

function syncProviderAccessMode(mode, { editing = false } = {}) {
  providerAdminState.accessMode = mode;
  const subscription = mode === "subscription";
  const enabled = (state.llmProviderTypes || []).some((item) => item.code === "openai_codex");
  const modal = document.getElementById("llmProviderModal");
  modal.classList.toggle("provider-create-modal", !editing && enabled);
  modal.classList.toggle("provider-api-only-modal", !enabled && !subscription);
  document.getElementById("llmProviderAccessMode").classList.toggle("hidden", editing || !enabled);
  document.querySelectorAll("#llmProviderAccessMode [data-provider-access]").forEach((button) => {
    const active = button.dataset.providerAccess === mode;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  for (const id of ["llmProviderApiTypeField", "llmProviderApiBaseField", "llmProviderApiKeyField"]) {
    document.getElementById(id).classList.toggle("hidden", subscription);
  }
  document.getElementById("llmProviderSubscriptionTypeField").classList.toggle("hidden", !subscription);
  document.getElementById("llmProviderSubscriptionFields").classList.toggle("hidden", !subscription);
  document.getElementById("llmProviderSubscriptionType").disabled = !subscription || editing;
  document.getElementById("llmProviderType").disabled = subscription || editing;
  document.getElementById("llmProviderBaseUrl").disabled = subscription;
  document.getElementById("llmProviderBaseUrl").required = !subscription;
  document.getElementById("llmProviderApiKey").disabled = subscription;
  const nameInput = document.getElementById("llmProviderName");
  nameInput.placeholder = subscription ? "例如 GPT 本机" : "例如 Kimi";
  nameInput.required = editing;
  if (!editing) syncSuggestedProviderName();
  document.getElementById("llmProviderDisconnectAuthorization").classList.toggle("hidden", !subscription || !editing || !providerAdminState.existingAuth);
  document.getElementById("llmProviderStartAuthorization").disabled = !enabled;
  document.getElementById("llmProviderVerifyAuthorization").disabled = !enabled;
}

async function cancelProviderAuthorization() {
  const flowId = providerAdminState.flowId;
  providerAdminState.flowId = null;
  providerAdminState.verified = false;
  if (!flowId) return;
  const providerId = document.getElementById("llmProviderForm")?.dataset.providerId || null;
  try {
    await apiFetch(`/api/v1/llm-providers/codex/authorization/${encodeURIComponent(flowId)}/cancel`, {
      method: "POST", body: JSON.stringify({ provider_id: providerId }),
    });
  } catch (_) { /* Expired flows are already gone server-side. */ }
}

function syncProviderBaseUrl() {
  const type = providerTypeByCode(document.getElementById("llmProviderType")?.value);
  const toggle = document.getElementById("llmProviderUseDefaultBaseUrl");
  const input = document.getElementById("llmProviderBaseUrl");
  if (!toggle || !input) return;
  const supportsDefault = Boolean(type?.supports_default_base_url && type?.default_base_url);
  const supportsCustom = type?.supports_custom_base_url !== false;
  toggle.disabled = !supportsDefault;
  if (!supportsDefault) toggle.checked = false;
  if (toggle.checked) {
    input.value = type.default_base_url;
    input.readOnly = true;
  } else {
    input.readOnly = !supportsCustom;
    if (!input.value || input.dataset.defaultValue === input.value) input.value = type?.default_base_url || "";
  }
  input.dataset.defaultValue = type?.default_base_url || "";
}

function setProviderApiKeyEditor({ maskedValue = "", clearRequested = false } = {}) {
  const input = document.getElementById("llmProviderApiKey");
  const button = document.getElementById("llmProviderClearApiKey");
  if (!input || !button) return;
  const masked = String(maskedValue || "");
  input.dataset.maskedValue = masked;
  input.dataset.clearRequested = clearRequested ? "true" : "false";
  input.value = clearRequested ? "" : masked;

  input.placeholder = clearRequested ? "保存后清除当前 API Key" : "";
  button.classList.toggle("hidden", !masked);
  button.textContent = clearRequested ? "撤销清除" : "清除";
  button.classList.toggle("is-undo", clearRequested);
}

const llmProvidersAdmin = {
  cancelModelTests() {
    providerAdminState.testBatch?.controller.abort();
    providerAdminState.testBatch = null;
    for (const [id, result] of providerAdminState.modelTests) {
      if (result.status === "pending" || result.status === "testing") providerAdminState.modelTests.delete(id);
    }
    this.updateModelTests();
  },

  updateModelTests() {
    const host = document.getElementById("llmProviderDetail");
    if (!host) return;
    host.querySelectorAll("[data-model-test-result]").forEach((element) => {
      const result = providerAdminState.modelTests.get(element.dataset.modelTestResult);
      const labels = { pending: "等待测试", testing: "测试中…", success: "成功", error: "失败" };
      element.className = `provider-model-test-result ${result?.status || ""}`;
      element.textContent = result ? `${labels[result.status]}${Number.isFinite(result.elapsed_ms) ? ` · ${(result.elapsed_ms / 1000).toFixed(1)}s` : ""}` : "";
      element.title = result?.message || "";
      element.tabIndex = result ? 0 : -1;
      element.setAttribute("aria-label", result ? `${element.textContent}，${result.message || ""}` : "");
    });
    const button = host.querySelector("[data-provider-test-models]");
    if (button) {
      button.disabled = Boolean(providerAdminState.testBatch) || !providerModels(providerAdminState.selectedId).some((model) => model.enabled !== false);
      button.querySelector("span").textContent = providerAdminState.testBatch ? "测试中…" : "测试";
    }
  },

  async testModels(provider) {
    if (providerAdminState.testBatch) return;
    const models = providerModels(provider.id).filter((model) => model.enabled !== false);
    if (!models.length) return;
    const batch = { controller: new AbortController() };
    providerAdminState.testBatch = batch;
    for (const model of providerModels(provider.id)) providerAdminState.modelTests.delete(model.id);
    for (const model of models) providerAdminState.modelTests.set(model.id, { status: "pending" });
    this.updateModelTests();
    try {
      for (const model of models) {
        if (providerAdminState.testBatch !== batch) break;
        // Re-check current state: a model may have been disabled/deleted while a previous test ran.
        if (!providerModels(provider.id).some((item) => item.id === model.id && item.enabled !== false)) {
          providerAdminState.modelTests.delete(model.id);
          continue;
        }
        providerAdminState.modelTests.set(model.id, { status: "testing" });
        this.updateModelTests();
        let result;
        const timeout = setTimeout(() => batch.controller.abort(), 35000);
        try {
          result = await apiFetch(`/api/v1/llm-models/${encodeURIComponent(model.id)}/test?provider_id=${encodeURIComponent(provider.id)}`, {
            method: "POST", signal: batch.controller.signal,
          });
        } catch (error) {
          result = { ok: false, message: batch.controller.signal.aborted ? "请求中断，结果未确认，请重新测试" : error.message };
        } finally {
          clearTimeout(timeout);
        }
        if (providerAdminState.testBatch !== batch) break;
        providerAdminState.modelTests.set(model.id, { ...result, status: result?.ok === true ? "success" : "error" });
        this.updateModelTests();
        if (batch.controller.signal.aborted) break;
      }
    } finally {
      if (providerAdminState.testBatch === batch) {
        this.cancelModelTests();
        this.updateModelTests();
      }
    }
  },

  async refresh({ preserveSelection = true } = {}) {
    this.cancelModelTests();
    providerAdminState.modelTests.clear();
    const list = document.getElementById("llmProviderList");
    if (!list) return;
    list.innerHTML = `<div class="provider-list-state">正在加载供应商…</div>`;
    try {
      const [providers, types] = await Promise.all([
        loadLlmProviders(),
        loadLlmProviderTypes(),
        loadLlmModels(),
      ]);
      state.llmProviderTypes = types;
      if (!preserveSelection || !providers.some((item) => item.id === providerAdminState.selectedId)) {
        providerAdminState.selectedId = providers[0]?.id || null;
      }
      this.renderList();
      this.renderDetail();
      showDbBanner(false);
      return true;
    } catch (error) {
      list.innerHTML = `<div class="provider-list-state error">${escapeHtml(error.message)}</div>`;
      const detail = document.getElementById("llmProviderDetail");
      if (detail) detail.innerHTML = `<div class="provider-detail-empty">列表加载失败，请重新加载</div>`;
      return false;
    }
  },

  renderList() {
    const list = document.getElementById("llmProviderList");
    if (!list) return;
    if (!state.llmProviders.length) {
      list.innerHTML = `<div class="provider-list-state">暂无供应商</div>`;
      return;
    }
    list.innerHTML = state.llmProviders.map((provider) => `
      <button type="button" class="provider-list-item ${provider.id === providerAdminState.selectedId ? "active" : ""}" data-provider-id="${escapeHtml(provider.id)}">
        ${providerBrandMarkup(provider)}
        <span class="provider-list-copy"><strong>${escapeHtml(provider.name)}</strong><small>${escapeHtml(providerTypeLabel(provider))}</small></span>
        <span class="provider-list-signals">
          <span class="provider-key-state ${provider.authenticated ? "on" : "off"}" title="${provider.auth_method === "oauth" ? (provider.authenticated ? "订阅已授权" : "订阅未授权") : (provider.api_key_set ? "API Key 已配置" : "API Key 未配置")}"><i data-lucide="${provider.auth_method === "oauth" ? "user-round-check" : "key-round"}"></i></span>
          <span class="provider-model-count"><i data-lucide="cpu"></i>${Number(provider.model_count ?? providerModels(provider.id).length)}</span>
        </span>
      </button>
    `).join("");
    list.querySelectorAll("[data-provider-id]").forEach((button) => button.addEventListener("click", () => {
      this.cancelModelTests();
      this.closeCatalogPicker({ force: true });
      providerAdminState.selectedId = button.dataset.providerId;
      this.renderList();
      this.renderDetail();
    }));
    window.lucide?.createIcons();
  },

  renderDetail() {
    const host = document.getElementById("llmProviderDetail");
    const provider = state.llmProviders.find((item) => item.id === providerAdminState.selectedId);
    if (!host) return;
    if (!provider) {
      host.innerHTML = `<div class="provider-detail-empty"><i data-lucide="waypoints"></i><p>${state.llmProviders.length ? "选择一个供应商查看接入配置与关联模型" : "暂无供应商，请点击右上角新建供应商"}</p></div>`;
      window.lucide?.createIcons();
      return;
    }
    const models = providerModels(provider.id);
    const catalogAvailable = providerCatalogId(provider) || isGenericCatalogProvider(provider);
    host.innerHTML = `
      <header class="provider-detail-header">
        <div class="provider-detail-identity">
          ${providerBrandMarkup(provider)}
          <div class="provider-detail-copy">
            <div class="provider-detail-title-line">
              <h3>${escapeHtml(provider.name)}</h3>
              <div class="provider-facts" aria-label="供应商接入信息">
                <span class="provider-fact ${provider.authenticated ? "on" : "off"}" title="认证状态"><i data-lucide="${provider.auth_method === "oauth" ? "user-round-check" : "key-round"}"></i><span>${provider.auth_method === "oauth" ? (provider.authenticated ? `订阅已授权 ${escapeHtml(provider.account_hint || "")}` : "订阅未授权") : (provider.api_key_set ? "Key 已配" : "Key 未配")}</span></span>
              </div>
            </div>
            <span class="provider-fact provider-fact-url" title="Base URL"><i data-lucide="link-2"></i><code>${escapeHtml(provider.effective_base_url || provider.base_url || "—")}</code><span class="provider-address-mode">${provider.use_default_base_url ? "默认" : "自定义"}</span></span>
          </div>
        </div>
        <div class="row-actions provider-action-group">
          <button type="button" class="provider-action" data-provider-test-models title="逐个测试已开启的模型，会产生少量模型用量；结果仅当前页面保留"><i data-lucide="plug-zap"></i><span>测试</span></button>
          <button type="button" class="provider-action" data-provider-edit><i data-lucide="pencil"></i><span>编辑</span></button>
          <button type="button" class="provider-action danger" data-provider-delete><i data-lucide="trash-2"></i><span>删除</span></button>
        </div>
      </header>
      <section class="provider-models-section">
        <div class="provider-section-head"><h4>关联模型</h4><div class="row-actions provider-action-group">${catalogAvailable ? `<button type="button" class="provider-action" data-provider-catalog><i data-lucide="library"></i><span>从目录添加</span></button>` : ""}<button type="button" class="provider-action" data-provider-model><i data-lucide="plus"></i><span>新建模型</span></button></div></div>
        <div class="provider-related-models">${models.length ? models.map((model) => `
          <div class="provider-related-model ${model.selectable === false ? "is-disabled" : ""}">
            <button type="button" class="model-enabled-switch ${model.enabled === false ? "off" : "on"}" data-toggle-model="${escapeHtml(model.id)}" role="switch" aria-checked="${model.enabled !== false}" title="${model.enabled === false ? "启用模型" : "停用模型"}" aria-label="${model.enabled === false ? "启用模型" : "停用模型"}"><span></span></button>
            <button type="button" class="provider-related-model-main" data-edit-model="${escapeHtml(model.id)}">
              <code>${escapeHtml(model.model)}</code>
              <span class="provider-model-facts">${providerModelFacts(model)}</span>
            </button>
            <span class="provider-model-test-result" data-model-test-result="${escapeHtml(model.id)}" role="status" aria-live="polite"></span>
            <button type="button" class="provider-model-delete" data-delete-model="${escapeHtml(model.id)}" data-model-name="${escapeHtml(model.model)}" title="删除模型" aria-label="删除模型"><i data-lucide="trash-2"></i></button>
          </div>`).join("") : `<div class="provider-model-empty">尚未关联模型</div>`}</div>
      </section>`;
    host.querySelector("[data-provider-edit]")?.addEventListener("click", () => this.openEdit(provider.id));
    host.querySelector("[data-provider-delete]")?.addEventListener("click", () => this.remove(provider));
    host.querySelector("[data-provider-model]")?.addEventListener("click", () => window.llmModelsAdmin?.openCreate({ provider_id: provider.id }, { lockProvider: true, deriveName: true }));
    host.querySelector("[data-provider-catalog]")?.addEventListener("click", (event) => this.initializeFromCatalog(provider, event.currentTarget));
    host.querySelector("[data-provider-test-models]")?.addEventListener("click", () => this.testModels(provider));
    host.querySelectorAll("[data-edit-model]").forEach((button) => button.addEventListener("click", () => window.llmModelsAdmin?.openEdit(button.dataset.editModel, { lockProvider: true, deriveName: true })));
    host.querySelectorAll("[data-toggle-model]").forEach((button) => button.addEventListener("click", () => window.llmModelsAdmin?.toggleEnabled(button.dataset.toggleModel)));
    host.querySelectorAll("[data-delete-model]").forEach((button) => button.addEventListener("click", () => window.llmModelsAdmin?.remove(button.dataset.deleteModel, button.dataset.modelName)));
    window.lucide?.createIcons();
    this.updateModelTests();
  },

  async initializeFromCatalog(provider) {
    const catalogId = providerCatalogId(provider);
    const generic = isGenericCatalogProvider(provider);
    if ((!catalogId && !generic) || !window.DeepAgentProviderCatalog) return;
    if (providerCatalogPickerState.submitting) return;
    this.closeCatalogPicker();
    const generation = providerCatalogPickerState.generation;
    const controller = new AbortController();
    Object.assign(providerCatalogPickerState, { provider, controller, loading: true });
    document.getElementById("providerCatalogSearch").value = "";
    document.getElementById("providerCatalogPickerModal")?.classList.toggle("is-generic", generic);
    AdminFormFeedback.clear(document.getElementById("providerCatalogPickerForm"));
    this.renderCatalogPicker();
    document.getElementById("providerCatalogPickerModal")?.showModal();
    try {
      const params = new URLSearchParams({ view: "provider", offset: "0", limit: "200" });
      if (!generic) params.set("provider_id", catalogId);
      const catalog = await apiFetch(`/api/v1/llm-models/catalog?${params}`, { signal: controller.signal });
      if (generation !== providerCatalogPickerState.generation) return;
      providerCatalogPickerState.sources = (catalog.items || []).filter((item) => item.provider_id);
      providerCatalogPickerState.loading = false;
      this.selectCatalogSource(generic ? "" : catalogId);
    } catch (error) {
      if (generation !== providerCatalogPickerState.generation || controller.signal.aborted) return;
      providerCatalogPickerState.loading = false;
      this.renderCatalogPicker();
      AdminFormFeedback.show(document.getElementById("providerCatalogPickerForm"), `目录模型加载失败：${error.message}`);
    } finally {
      if (generation === providerCatalogPickerState.generation) providerCatalogPickerState.controller = null;
    }
  },

  selectCatalogSource(sourceId) {
    if (providerCatalogPickerState.submitting) return;
    const source = providerCatalogPickerState.sources.find((item) => item.provider_id === sourceId);
    providerCatalogPickerState.sourceId = source?.provider_id || "";
    providerCatalogPickerState.selectedIds.clear();
    providerCatalogPickerState.query = "";
    document.getElementById("providerCatalogSearch").value = "";
    const { choices } = window.DeepAgentProviderCatalog.buildProviderCatalogChoices({
      provider: providerCatalogPickerState.provider,
      catalogModels: source?.models || [],
      existingModels: providerModels(providerCatalogPickerState.provider?.id),
    });
    providerCatalogPickerState.choices = choices;
    AdminFormFeedback.clear(document.getElementById("providerCatalogPickerForm"));
    this.renderCatalogPicker();
  },

  visibleCatalogChoices() {
    const query = providerCatalogPickerState.query.trim().toLowerCase();
    return providerCatalogPickerState.choices.filter((choice) => !query || `${choice.model} ${choice.label}`.toLowerCase().includes(query));
  },

  renderCatalogPicker() {
    const picker = providerCatalogPickerState;
    const provider = picker.provider;
    const host = document.getElementById("providerCatalogChoiceList");
    if (!provider || !host) return;
    const generic = isGenericCatalogProvider(provider);
    document.getElementById("providerCatalogPickerTitle").textContent = "选择要添加的模型";
    document.getElementById("providerCatalogPickerProvider").textContent = provider.name;
    document.getElementById("providerCatalogSourceField").classList.toggle("hidden", !generic);
    const sourceSelect = document.getElementById("providerCatalogSource");
    sourceSelect.innerHTML = `<option value="">请选择目录供应商</option>${picker.sources.map((source) =>
      `<option value="${escapeHtml(source.provider_id)}" ${source.provider_id === picker.sourceId ? "selected" : ""}>${escapeHtml(source.provider_label || source.provider_id)}</option>`,
    ).join("")}`;
    sourceSelect.disabled = picker.loading || picker.submitting;
    document.getElementById("providerCatalogPickerHint").textContent = generic
      ? "从目录预填模型配置，添加到当前供应商；实际可用模型以接入服务为准。"
      : "勾选模型后添加，已添加项不可重复选择";
    const choices = this.visibleCatalogChoices();
    const labels = { text: "文本", image: "图像", audio: "音频", video: "视频" };
    host.innerHTML = picker.loading ? `<div class="provider-model-empty">正在加载模型目录…</div>` : choices.length ? choices.map((choice) => {
      const created = picker.createdKeys.has(catalogChoiceKey(choice));
      const disabled = choice.added || created || picker.submitting;
      const context = window.__LlmModelConfigurationUI?.formatTokenCount?.(choice.payload.context_window_tokens) || "—";
      const modalities = (choice.payload.input_modalities || []).map((item) => labels[item] || item).join("、") || "文本";
      return `<label class="provider-catalog-choice ${choice.added || created ? "is-added" : ""}">
        <input type="checkbox" name="providerCatalogChoice" value="${escapeHtml(choice.id)}" ${disabled ? "disabled" : ""} ${picker.selectedIds.has(choice.id) ? "checked" : ""} />
        <span class="provider-catalog-choice-copy"><code title="${escapeHtml(choice.model)}">${escapeHtml(choice.model)}</code><small>${escapeHtml(modalities)} · ${escapeHtml(context)}</small>${choice.error ? `<small class="provider-catalog-choice-error">${escapeHtml(choice.error)}</small>` : ""}</span>
        <span class="badge ${choice.added || created ? "on" : ""}">${created ? "本次已添加" : choice.added ? "已添加" : choice.error ? "添加失败" : "可添加"}</span>
      </label>`;
    }).join("") : `<div class="provider-model-empty">${generic && !picker.sourceId ? "请先选择目录供应商" : picker.query ? "未找到匹配模型" : "目录中暂无模型"}</div>`;
    host.querySelectorAll('input[name="providerCatalogChoice"]').forEach((input) => {
      input.addEventListener("change", () => {
        if (input.checked) picker.selectedIds.add(input.value);
        else picker.selectedIds.delete(input.value);
        this.updateCatalogPickerSelection();
      });
    });
    for (const id of ["closeProviderCatalogPickerModal", "cancelProviderCatalogPickerBtn", "providerCatalogSearch"]) {
      document.getElementById(id).disabled = picker.submitting;
    }
    this.updateCatalogPickerSelection();
  },

  updateCatalogPickerSelection() {
    const picker = providerCatalogPickerState;
    const selectable = this.visibleCatalogChoices().filter((choice) => !choice.added && !picker.createdKeys.has(catalogChoiceKey(choice)));
    const selected = picker.selectedIds.size;
    const confirm = document.getElementById("confirmProviderCatalogPickerBtn");
    const toggle = document.getElementById("toggleProviderCatalogSelection");
    if (confirm) {
      confirm.disabled = picker.submitting || selected === 0;
      confirm.textContent = picker.submitting ? "添加中…" : selected ? `添加所选（${selected}）` : "添加所选";
    }
    if (toggle) {
      toggle.disabled = picker.submitting || !selectable.length;
      toggle.textContent = selectable.length && selectable.every((choice) => picker.selectedIds.has(choice.id)) ? "取消全选" : "全选可添加";
    }
  },

  toggleCatalogPickerSelection() {
    if (providerCatalogPickerState.submitting) return;
    const selectable = this.visibleCatalogChoices().filter((choice) => !choice.added && !providerCatalogPickerState.createdKeys.has(catalogChoiceKey(choice)));
    const shouldSelect = selectable.some((choice) => !providerCatalogPickerState.selectedIds.has(choice.id));
    selectable.forEach((choice) => {
      if (shouldSelect) providerCatalogPickerState.selectedIds.add(choice.id);
      else providerCatalogPickerState.selectedIds.delete(choice.id);
    });
    this.renderCatalogPicker();
  },

  closeCatalogPicker({ force = false } = {}) {
    const picker = providerCatalogPickerState;
    if (picker.submitting && !force) return false;
    picker.controller?.abort();
    picker.generation += 1;
    Object.assign(picker, { provider: null, choices: [], sources: [], sourceId: "", query: "", controller: null, loading: false, submitting: false });
    picker.selectedIds.clear();
    picker.createdKeys.clear();
    document.getElementById("providerCatalogPickerModal")?.close();
    return true;
  },

  async submitCatalogPicker(event) {
    event.preventDefault();
    const picker = providerCatalogPickerState;
    if (picker.submitting || !picker.provider) return;
    const form = event.currentTarget;
    const generation = picker.generation;
    const selected = picker.choices.filter((choice) => picker.selectedIds.has(choice.id) && !choice.added && !picker.createdKeys.has(catalogChoiceKey(choice)));
    if (!selected.length) return;
    AdminFormFeedback.clear(form);
    picker.submitting = true;
    this.renderCatalogPicker();
    let created = 0;
    let failed = 0;
    try {
      for (const choice of selected) {
        if (generation !== picker.generation) return;
        try {
          await apiFetch("/api/v1/llm-models", { method: "POST", body: JSON.stringify(choice.payload) });
          if (generation !== picker.generation) return;
          created += 1;
          picker.createdKeys.add(catalogChoiceKey(choice));
          picker.selectedIds.delete(choice.id);
          choice.error = "";
        } catch (error) {
          if (generation !== picker.generation) return;
          failed += 1;
          choice.error = error.message || "请求失败";
        }
        this.renderCatalogPicker();
      }
      const refreshed = created ? await this.refresh() : true;
      if (generation !== picker.generation) return;
      picker.submitting = false;
      const summary = window.DeepAgentProviderCatalog.providerCatalogSummary({ created, skipped: 0, failed });
      if (failed) {
        this.renderCatalogPicker();
        AdminFormFeedback.show(form, `${summary}；失败原因见对应模型${refreshed ? "" : "。列表刷新失败，请重新加载"}`);
      } else {
        this.closeCatalogPicker();
        showToast(`${summary}${refreshed ? "" : "，列表刷新失败，请重新加载"}`, !refreshed);
      }
    } catch (error) {
      if (generation === picker.generation) AdminFormFeedback.show(form, error);
    } finally {
      if (generation === picker.generation) {
        picker.submitting = false;
        this.renderCatalogPicker();
      }
    }
  },

  async openCreate({ providerType = "", afterSave = null } = {}) {
    await loadLlmProviderTypes();
    providerAdminState.afterSave = afterSave;
    providerAdminState.flowId = null;
    providerAdminState.verified = false;
    providerAdminState.existingAuth = false;
    document.getElementById("llmProviderForm").dataset.providerId = "";
    renderProviderTypeOptions(providerType || state.llmProviderTypes[0]?.code || "");
    document.getElementById("llmProviderModalTitle").textContent = "新建供应商";
    document.getElementById("llmProviderName").value = "";
    document.getElementById("llmProviderName").dataset.autoName = "";
    setProviderApiKeyEditor();
    document.getElementById("llmProviderUseDefaultBaseUrl").checked = Boolean(providerTypeByCode(document.getElementById("llmProviderType").value)?.supports_default_base_url);
    document.getElementById("llmProviderBaseUrl").value = "";
    syncProviderBaseUrl();
    document.getElementById("llmProviderCallbackUrl").value = "";
    setProviderAuthState("尚未授权");
    syncProviderAccessMode("api");
    document.getElementById("llmProviderModal").showModal();
  },

  async openEdit(providerId) {
    const provider = await apiFetch(`/api/v1/llm-providers/${encodeURIComponent(providerId)}`);
    await loadLlmProviderTypes();
    providerAdminState.afterSave = null;
    providerAdminState.flowId = null;
    providerAdminState.verified = false;
    providerAdminState.existingAuth = Boolean(provider.authenticated);
    document.getElementById("llmProviderForm").dataset.providerId = providerId;
    renderProviderTypeOptions(provider.provider_type);
    document.getElementById("llmProviderModalTitle").textContent = `编辑供应商 · ${provider.name}`;
    document.getElementById("llmProviderName").value = provider.name;
    document.getElementById("llmProviderName").dataset.autoName = "";
    document.getElementById("llmProviderUseDefaultBaseUrl").checked = Boolean(provider.use_default_base_url);
    document.getElementById("llmProviderBaseUrl").value = provider.base_url || provider.effective_base_url || "";
    setProviderApiKeyEditor({ maskedValue: provider.api_key_masked || "" });
    syncProviderBaseUrl();
    document.getElementById("llmProviderCallbackUrl").value = "";
    setProviderAuthState(provider.authenticated ? `已授权 ${provider.account_hint || ""}` : "尚未授权", provider.authenticated ? "connected" : "idle");
    syncProviderAccessMode(provider.auth_method === "oauth" ? "subscription" : "api", { editing: true });
    if (provider.auth_method === "oauth" && !state.llmProviderTypes.some((item) => item.code === "openai_codex")) {
      setProviderAuthState(`${document.getElementById("llmProviderSubscriptionState").textContent} · 当前环境未开启订阅实验功能`);
    }
    document.getElementById("llmProviderModal").showModal();
  },

  async save(event) {
    event.preventDefault();
    const form = event.currentTarget;
    // Capture the write mode before any await. Modal state may change while a
    // request is in flight; the form itself is the authority for this submit.
    const editingId = String(form.dataset.providerId || "").trim() || null;
    AdminFormFeedback.clear(form);
    AdminFormFeedback.setSubmitting(form, true);
    try {
      const useDefault = document.getElementById("llmProviderUseDefaultBaseUrl").checked;
      const subscription = providerAdminState.accessMode === "subscription";
      const nameInput = document.getElementById("llmProviderName");
      const name = nameInput.value.trim() || (!editingId ? suggestedProviderName() : "");
      nameInput.value = name;
      const payload = subscription ? {
        name,
        ...(!editingId ? { provider_type: document.getElementById("llmProviderSubscriptionType").value, use_default_base_url: true } : {}),
        ...(providerAdminState.flowId && providerAdminState.verified ? { authorization_flow_id: providerAdminState.flowId } : {}),
      } : {
        name,
        provider_type: document.getElementById("llmProviderType").value,
        use_default_base_url: useDefault,
        base_url: document.getElementById("llmProviderBaseUrl").value.trim(),
      };
      if (subscription && !editingId && !providerAdminState.verified) throw new Error("请先完成订阅授权验证");
      if (!subscription) {
        const apiKeyInput = document.getElementById("llmProviderApiKey");
        const apiKey = apiKeyInput.value.trim();
        const maskedValue = String(apiKeyInput.dataset.maskedValue || "");
        if (apiKey && apiKey !== maskedValue) payload.api_key = apiKey;
        if (apiKeyInput.dataset.clearRequested === "true") payload.clear_api_key = true;
      }
      const saved = await apiFetch(editingId ? `/api/v1/llm-providers/${encodeURIComponent(editingId)}` : "/api/v1/llm-providers", {
        method: editingId ? "PATCH" : "POST",
        body: JSON.stringify(payload),
      });
      providerAdminState.flowId = null;
      document.getElementById("llmProviderModal").close();
      providerAdminState.selectedId = saved.id;
      await this.refresh();
      showToast(editingId ? "供应商已更新" : "供应商已创建");
      const afterSave = providerAdminState.afterSave;
      providerAdminState.afterSave = null;
      afterSave?.(saved);
    } catch (error) {
      AdminFormFeedback.show(form, error);
    } finally {
      AdminFormFeedback.setSubmitting(form, false);
    }
  },

  async remove(provider) {
    const modelCount = providerModels(provider.id).length;
    const related = modelCount ? `及其 ${modelCount} 个模型配置` : "";
    if (!(await confirmAction({ title: "删除供应商？", message: `将删除供应商「${provider.name}」${related}，此操作不可撤销。仍被 Agent 或 Web Search 使用时不会删除。`, icon: "trash-2", tone: "danger", confirmLabel: "删除" }))) return;
    try {
      await apiFetch(`/api/v1/llm-providers/${encodeURIComponent(provider.id)}`, { method: "DELETE" });
      providerAdminState.selectedId = null;
      const refreshed = await this.refresh({ preserveSelection: false });
      showToast(refreshed ? "供应商已删除" : "供应商已删除，列表刷新失败，请重新加载", !refreshed);
    } catch (error) { showToast(error.message, true); }
  },
};

window.llmProvidersAdmin = llmProvidersAdmin;
document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("newLlmProviderBtn")?.addEventListener("click", () => llmProvidersAdmin.openCreate());
  document.getElementById("llmProviderForm")?.addEventListener("submit", (event) => llmProvidersAdmin.save(event));
  document.querySelectorAll("#llmProviderAccessMode [data-provider-access]").forEach((button) => button.addEventListener("click", async () => {
    if (button.dataset.providerAccess === providerAdminState.accessMode) return;
    await cancelProviderAuthorization();
    setProviderAuthState("尚未授权");
    syncProviderAccessMode(button.dataset.providerAccess);
  }));
  document.getElementById("llmProviderStartAuthorization")?.addEventListener("click", async () => {
    const form = document.getElementById("llmProviderForm");
    const providerId = form.dataset.providerId || null;
    AdminFormFeedback.clear(form);
    const tab = window.open("about:blank", "_blank");
    try {
      await cancelProviderAuthorization();
      const result = await apiFetch("/api/v1/llm-providers/codex/authorization", {
        method: "POST", body: JSON.stringify({ provider_id: providerId }),
      });
      providerAdminState.flowId = result.flow_id;
      providerAdminState.verified = false;
      setProviderAuthState("授权页面已打开，请回填完整回调地址", "pending");
      if (tab) tab.location.replace(result.authorization_url);
      else showToast("浏览器拦截了新标签，请允许弹窗后重试", true);
    } catch (error) {
      tab?.close();
      AdminFormFeedback.show(form, error);
    }
  });
  document.getElementById("llmProviderVerifyAuthorization")?.addEventListener("click", async () => {
    const form = document.getElementById("llmProviderForm");
    AdminFormFeedback.clear(form);
    try {
      if (!providerAdminState.flowId) throw new Error("请先前往授权");
      const result = await apiFetch(`/api/v1/llm-providers/codex/authorization/${encodeURIComponent(providerAdminState.flowId)}/verify`, {
        method: "POST",
        body: JSON.stringify({ provider_id: form.dataset.providerId || null, callback_url: document.getElementById("llmProviderCallbackUrl").value.trim() }),
      });
      providerAdminState.verified = true;
      document.getElementById("llmProviderCallbackUrl").value = "";
      setProviderAuthState(`验证成功 ${result.account_hint}，点击保存后生效`, "connected");
    } catch (error) { AdminFormFeedback.show(form, error); }
  });
  document.getElementById("llmProviderDisconnectAuthorization")?.addEventListener("click", async () => {
    const providerId = document.getElementById("llmProviderForm").dataset.providerId;
    if (!providerId) return;
    if (!(await confirmAction({ title: "断开订阅授权？", message: "关联模型将暂时不可用，之后可以重新授权。", confirmLabel: "断开" }))) return;
    try {
      await apiFetch(`/api/v1/llm-providers/${encodeURIComponent(providerId)}/disconnect`, { method: "POST" });
      await cancelProviderAuthorization();
      document.getElementById("llmProviderModal").close();
      await llmProvidersAdmin.refresh();
      showToast("订阅授权已断开");
    } catch (error) { AdminFormFeedback.show(document.getElementById("llmProviderForm"), error); }
  });
  document.getElementById("llmProviderType")?.addEventListener("change", () => {
    const type = providerTypeByCode(document.getElementById("llmProviderType").value);
    document.getElementById("llmProviderUseDefaultBaseUrl").checked = Boolean(type?.supports_default_base_url && type?.default_base_url);
    document.getElementById("llmProviderBaseUrl").value = "";
    syncProviderBaseUrl();
    syncSuggestedProviderName();
  });
  document.getElementById("llmProviderSubscriptionType")?.addEventListener("change", syncSuggestedProviderName);
  document.getElementById("llmProviderUseDefaultBaseUrl")?.addEventListener("change", syncProviderBaseUrl);
  document.getElementById("llmProviderApiKey")?.addEventListener("focus", (event) => {
    if (event.currentTarget.value === event.currentTarget.dataset.maskedValue) event.currentTarget.select();
  });
  document.getElementById("llmProviderApiKey")?.addEventListener("input", (event) => {
    const input = event.currentTarget;

    input.dataset.clearRequested = "false";
    const button = document.getElementById("llmProviderClearApiKey");
    if (button) {
      button.textContent = "清除";
      button.classList.remove("is-undo");
    }
  });
  document.getElementById("llmProviderClearApiKey")?.addEventListener("click", () => {
    const input = document.getElementById("llmProviderApiKey");
    const clearRequested = input.dataset.clearRequested !== "true";
    setProviderApiKeyEditor({ maskedValue: input.dataset.maskedValue, clearRequested });
  });
  document.getElementById("closeLlmProviderModal")?.addEventListener("click", () => document.getElementById("llmProviderModal").close());
  document.getElementById("cancelLlmProviderBtn")?.addEventListener("click", () => document.getElementById("llmProviderModal").close());
  document.getElementById("llmProviderModal")?.addEventListener("close", () => { void cancelProviderAuthorization(); });
  document.getElementById("providerCatalogPickerForm")?.addEventListener("submit", (event) => llmProvidersAdmin.submitCatalogPicker(event));
  document.getElementById("toggleProviderCatalogSelection")?.addEventListener("click", () => llmProvidersAdmin.toggleCatalogPickerSelection());
  document.getElementById("providerCatalogSource")?.addEventListener("change", (event) => llmProvidersAdmin.selectCatalogSource(event.currentTarget.value));
  document.getElementById("providerCatalogSearch")?.addEventListener("input", (event) => {
    if (providerCatalogPickerState.submitting) return;
    providerCatalogPickerState.query = event.currentTarget.value;
    llmProvidersAdmin.renderCatalogPicker();
  });
  document.getElementById("closeProviderCatalogPickerModal")?.addEventListener("click", () => llmProvidersAdmin.closeCatalogPicker());
  document.getElementById("cancelProviderCatalogPickerBtn")?.addEventListener("click", () => llmProvidersAdmin.closeCatalogPicker());
  document.getElementById("providerCatalogPickerModal")?.addEventListener("cancel", (event) => {
    event.preventDefault();
    llmProvidersAdmin.closeCatalogPicker();
  });
  document.getElementById("providerCatalogPickerModal")?.addEventListener("close", (event) => {
    if (!event.currentTarget.open && providerCatalogPickerState.provider) llmProvidersAdmin.closeCatalogPicker({ force: true });
  });
});

document.addEventListener("deep-agent-configuration-tab", (event) => {
  if (event.detail?.name !== "llm-providers") {
    llmProvidersAdmin.cancelModelTests();
    llmProvidersAdmin.closeCatalogPicker({ force: true });
  }
});

window.__LlmProviderConfigurationUI = { providerBrand, providerTypeLabel, syncProviderBaseUrl };
