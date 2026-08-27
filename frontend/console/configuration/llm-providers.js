const providerAdminState = {
  selectedId: null,
  afterSave: null,
};

const providerCatalogPickerState = {
  provider: null,
  choices: [],
};

function providerTypeByCode(code) {
  return (state.llmProviderTypes || []).find((item) => item.code === code) || null;
}

function providerTypeLabel(provider) {
  return providerTypeByCode(provider.provider_type)?.label || provider.provider_type || "—";
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
  openai: { src: "./assets/provider-logos/openai.svg", tone: "openai" },
  anthropic: { src: "./assets/provider-logos/anthropic.png", tone: "anthropic" },
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

function renderProviderTypeOptions(selectedCode = "") {
  const select = document.getElementById("llmProviderType");
  if (!select) return;
  select.innerHTML = (state.llmProviderTypes || []).map((item) =>
    `<option value="${escapeHtml(item.code)}" ${item.code === selectedCode ? "selected" : ""}>${escapeHtml(item.label)}</option>`,
  ).join("");
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
  input.type = masked && !clearRequested ? "text" : "password";
  input.placeholder = clearRequested ? "保存后清除当前 API Key" : "";
  button.classList.toggle("hidden", !masked);
  button.textContent = clearRequested ? "撤销清除" : "清除";
  button.classList.toggle("is-undo", clearRequested);
}

const llmProvidersAdmin = {
  async refresh({ preserveSelection = true } = {}) {
    const list = document.getElementById("llmProviderList");
    if (!list) return;
    list.innerHTML = `<div class="provider-list-state">正在加载 Provider…</div>`;
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
    } catch (error) {
      list.innerHTML = `<div class="provider-list-state error">${escapeHtml(error.message)}</div>`;
    }
  },

  renderList() {
    const list = document.getElementById("llmProviderList");
    if (!list) return;
    if (!state.llmProviders.length) {
      list.innerHTML = `<div class="provider-list-state">暂无 Provider</div>`;
      return;
    }
    list.innerHTML = state.llmProviders.map((provider) => `
      <button type="button" class="provider-list-item ${provider.id === providerAdminState.selectedId ? "active" : ""}" data-provider-id="${escapeHtml(provider.id)}">
        ${providerBrandMarkup(provider)}
        <span class="provider-list-copy"><strong>${escapeHtml(provider.name)}</strong><small>${escapeHtml(providerTypeLabel(provider))}</small></span>
        <span class="provider-list-signals">
          <span class="provider-key-state ${provider.enabled === false ? "off" : "on"}" title="${provider.enabled === false ? "Provider 已停用" : "Provider 已启用"}"><i data-lucide="power"></i></span>
          <span class="provider-key-state ${provider.api_key_set ? "on" : "off"}" title="${provider.api_key_set ? "API Key 已配置" : "API Key 未配置"}"><i data-lucide="key-round"></i></span>
          <span class="provider-model-count"><i data-lucide="cpu"></i>${Number(provider.model_count ?? providerModels(provider.id).length)}</span>
        </span>
        <i data-lucide="chevron-right"></i>
      </button>
    `).join("");
    list.querySelectorAll("[data-provider-id]").forEach((button) => button.addEventListener("click", () => {
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
      host.innerHTML = `<div class="provider-detail-empty"><i data-lucide="waypoints"></i><p>选择一个 Provider 查看接入配置与关联模型</p></div>`;
      window.lucide?.createIcons();
      return;
    }
    const models = providerModels(provider.id);
    const catalogId = providerCatalogId(provider);
    host.innerHTML = `
      <header class="provider-detail-header">
        <div class="provider-detail-identity">
          ${providerBrandMarkup(provider)}
          <div class="provider-detail-copy">
            <div class="provider-detail-title-line">
              <h3>${escapeHtml(provider.name)}</h3>
              <div class="provider-facts" aria-label="Provider 接入信息">
                <span class="provider-fact" title="地址模式"><i data-lucide="route"></i><span>${provider.use_default_base_url ? "默认地址" : "自定义地址"}</span></span>
                <span class="provider-fact ${provider.api_key_set ? "on" : "off"}" title="API Key"><i data-lucide="key-round"></i><span>${provider.api_key_set ? "Key 已配" : "Key 未配"}</span></span>
                <span class="provider-fact ${provider.enabled === false ? "off" : "on"}" title="Provider 状态"><i data-lucide="power"></i><span>${provider.enabled === false ? "停用" : "启用"}</span></span>
              </div>
            </div>
            <span class="provider-fact provider-fact-url" title="Base URL"><i data-lucide="link-2"></i><code>${escapeHtml(provider.effective_base_url || provider.base_url || "—")}</code></span>
          </div>
        </div>
        <div class="row-actions provider-action-group">
          <button type="button" class="provider-action" data-provider-edit><i data-lucide="pencil"></i><span>编辑</span></button>
          <button type="button" class="provider-action danger" data-provider-delete><i data-lucide="trash-2"></i><span>删除</span></button>
        </div>
      </header>
      <section class="provider-models-section">
        <div class="provider-section-head"><h4>关联模型</h4><div class="row-actions provider-action-group">${catalogId ? `<button type="button" class="provider-action" data-provider-catalog><i data-lucide="library"></i><span>从目录添加</span></button>` : ""}<button type="button" class="provider-action" data-provider-model><i data-lucide="plus"></i><span>新建模型</span></button></div></div>
        <div class="provider-related-models">${models.length ? models.map((model) => `
          <div class="provider-related-model ${model.selectable === false ? "is-disabled" : ""}">
            <button type="button" class="provider-related-model-main" data-edit-model="${escapeHtml(model.id)}">
              <code>${escapeHtml(model.model)}</code>
              <span class="provider-model-facts">${providerModelFacts(model)}</span>
            </button>
            <button type="button" class="model-enabled-switch ${model.enabled === false ? "off" : "on"}" data-toggle-model="${escapeHtml(model.id)}" role="switch" aria-checked="${model.enabled !== false}" title="${model.enabled === false ? "启用模型" : "停用模型"}"><span></span></button>
            <button type="button" class="provider-model-delete" data-delete-model="${escapeHtml(model.id)}" data-model-name="${escapeHtml(model.model)}" title="删除模型" aria-label="删除模型"><i data-lucide="trash-2"></i></button>
          </div>`).join("") : `<div class="provider-model-empty">尚未关联模型</div>`}</div>
      </section>`;
    host.querySelector("[data-provider-edit]")?.addEventListener("click", () => this.openEdit(provider.id));
    host.querySelector("[data-provider-delete]")?.addEventListener("click", () => this.remove(provider));
    host.querySelector("[data-provider-model]")?.addEventListener("click", () => window.llmModelsAdmin?.openCreate({ provider_id: provider.id }, { lockProvider: true, deriveName: true }));
    host.querySelector("[data-provider-catalog]")?.addEventListener("click", (event) => this.initializeFromCatalog(provider, event.currentTarget));
    host.querySelectorAll("[data-edit-model]").forEach((button) => button.addEventListener("click", () => window.llmModelsAdmin?.openEdit(button.dataset.editModel, { lockProvider: true, deriveName: true })));
    host.querySelectorAll("[data-toggle-model]").forEach((button) => button.addEventListener("click", () => this.toggleModel(button.dataset.toggleModel)));
    host.querySelectorAll("[data-delete-model]").forEach((button) => button.addEventListener("click", () => window.llmModelsAdmin?.remove(button.dataset.deleteModel, button.dataset.modelName)));
    window.lucide?.createIcons();
  },

  async initializeFromCatalog(provider, button) {
    const catalogId = providerCatalogId(provider);
    if (!catalogId || !window.DeepAgentProviderCatalog) return;
    button.disabled = true;
    button.classList.add("is-loading");
    try {
      const params = new URLSearchParams({ view: "provider", provider_id: catalogId, offset: "0", limit: "200" });
      const catalog = await apiFetch(`/api/v1/llm-models/catalog?${params}`);
      const catalogModels = (catalog.items || []).flatMap((item) => item.models || []);
      if (!catalogModels.length) {
        showToast("目录中暂无可初始化模型", true);
        return;
      }
      const { choices } = window.DeepAgentProviderCatalog.buildProviderCatalogChoices({
        provider,
        catalogModels,
        existingModels: providerModels(provider.id),
      });
      providerCatalogPickerState.provider = provider;
      providerCatalogPickerState.choices = choices;
      this.renderCatalogPicker();
      document.getElementById("providerCatalogPickerModal")?.showModal();
    } catch (error) {
      showToast(`目录模型加载失败：${error.message}`, true);
    } finally {
      button.disabled = false;
      button.classList.remove("is-loading");
    }
  },

  renderCatalogPicker() {
    const provider = providerCatalogPickerState.provider;
    const choices = providerCatalogPickerState.choices;
    const host = document.getElementById("providerCatalogChoiceList");
    if (!provider || !host) return;
    document.getElementById("providerCatalogPickerTitle").textContent = "选择要添加的模型";
    document.getElementById("providerCatalogPickerProvider").textContent = provider.name;
    host.innerHTML = choices.length ? choices.map((choice) => {
      const context = window.__LlmModelConfigurationUI?.formatTokenCount?.(choice.payload.context_window_tokens) || "—";
      const modalities = (choice.payload.input_modalities || []).join("、") || "text";
      return `<label class="provider-catalog-choice ${choice.added ? "is-added" : ""}">
        <input type="checkbox" name="providerCatalogChoice" value="${escapeHtml(choice.id)}" ${choice.added ? "disabled" : ""} />
        <span class="provider-catalog-choice-copy"><code>${escapeHtml(choice.model)}</code><small>${escapeHtml(modalities)} · ${escapeHtml(context)}</small></span>
        <span class="badge ${choice.added ? "on" : ""}">${choice.added ? "已添加" : "可添加"}</span>
      </label>`;
    }).join("") : `<div class="provider-model-empty">目录中暂无模型</div>`;
    host.querySelectorAll('input[name="providerCatalogChoice"]').forEach((input) => {
      input.addEventListener("change", () => this.updateCatalogPickerSelection());
    });
    AdminFormFeedback.clear(document.getElementById("providerCatalogPickerForm"));
    this.updateCatalogPickerSelection();
  },

  updateCatalogPickerSelection() {
    const inputs = Array.from(document.querySelectorAll('#providerCatalogChoiceList input[name="providerCatalogChoice"]:not(:disabled)'));
    const selected = inputs.filter((input) => input.checked).length;
    const confirm = document.getElementById("confirmProviderCatalogPickerBtn");
    const toggle = document.getElementById("toggleProviderCatalogSelection");
    if (confirm) {
      confirm.disabled = selected === 0;
      confirm.textContent = selected ? `添加所选（${selected}）` : "添加所选";
    }
    if (toggle) {
      toggle.disabled = inputs.length === 0;
      toggle.textContent = inputs.length > 0 && selected === inputs.length ? "取消全选" : "全选可添加";
    }
  },

  toggleCatalogPickerSelection() {
    const inputs = Array.from(document.querySelectorAll('#providerCatalogChoiceList input[name="providerCatalogChoice"]:not(:disabled)'));
    const shouldSelect = inputs.some((input) => !input.checked);
    inputs.forEach((input) => { input.checked = shouldSelect; });
    this.updateCatalogPickerSelection();
  },

  closeCatalogPicker() {
    document.getElementById("providerCatalogPickerModal")?.close();
    providerCatalogPickerState.provider = null;
    providerCatalogPickerState.choices = [];
  },

  async submitCatalogPicker(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const selectedIds = new Set(Array.from(
      form.querySelectorAll('input[name="providerCatalogChoice"]:checked:not(:disabled)'),
      (input) => input.value,
    ));
    const selected = providerCatalogPickerState.choices.filter((choice) => selectedIds.has(choice.id));
    if (!selected.length) return;
    AdminFormFeedback.clear(form);
    AdminFormFeedback.setSubmitting(form, true);
    let created = 0;
    let failed = 0;
    try {
      for (const choice of selected) {
        try {
          await apiFetch("/api/v1/llm-models", { method: "POST", body: JSON.stringify(choice.payload) });
          created += 1;
        } catch {
          failed += 1;
        }
      }
      await this.refresh();
      this.closeCatalogPicker();
      showToast(window.DeepAgentProviderCatalog.providerCatalogSummary({ created, skipped: 0, failed }), failed > 0);
    } catch (error) {
      AdminFormFeedback.show(form, error);
    } finally {
      AdminFormFeedback.setSubmitting(form, false);
    }
  },

  async openCreate({ providerType = "", afterSave = null } = {}) {
    await loadLlmProviderTypes();
    providerAdminState.afterSave = afterSave;
    document.getElementById("llmProviderForm").dataset.providerId = "";
    renderProviderTypeOptions(providerType || state.llmProviderTypes[0]?.code || "");
    document.getElementById("llmProviderModalTitle").textContent = "新建 Provider";
    document.getElementById("llmProviderName").value = "";
    setProviderApiKeyEditor();
    document.getElementById("llmProviderEnabled").checked = true;
    document.getElementById("llmProviderUseDefaultBaseUrl").checked = Boolean(providerTypeByCode(document.getElementById("llmProviderType").value)?.supports_default_base_url);
    document.getElementById("llmProviderBaseUrl").value = "";
    syncProviderBaseUrl();
    document.getElementById("llmProviderModal").showModal();
  },

  async openEdit(providerId) {
    const provider = await apiFetch(`/api/v1/llm-providers/${encodeURIComponent(providerId)}`);
    await loadLlmProviderTypes();
    providerAdminState.afterSave = null;
    document.getElementById("llmProviderForm").dataset.providerId = providerId;
    renderProviderTypeOptions(provider.provider_type);
    document.getElementById("llmProviderModalTitle").textContent = `编辑 Provider · ${provider.name}`;
    document.getElementById("llmProviderName").value = provider.name;
    document.getElementById("llmProviderUseDefaultBaseUrl").checked = Boolean(provider.use_default_base_url);
    document.getElementById("llmProviderBaseUrl").value = provider.base_url || provider.effective_base_url || "";
    setProviderApiKeyEditor({ maskedValue: provider.api_key_masked || "" });
    document.getElementById("llmProviderEnabled").checked = provider.enabled !== false;
    syncProviderBaseUrl();
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
      const payload = {
        name: document.getElementById("llmProviderName").value.trim(),
        provider_type: document.getElementById("llmProviderType").value,
        use_default_base_url: useDefault,
        base_url: document.getElementById("llmProviderBaseUrl").value.trim(),
        enabled: document.getElementById("llmProviderEnabled").checked,
      };
      const apiKeyInput = document.getElementById("llmProviderApiKey");
      const apiKey = apiKeyInput.value.trim();
      const maskedValue = String(apiKeyInput.dataset.maskedValue || "");
      if (apiKey && apiKey !== maskedValue) payload.api_key = apiKey;
      if (apiKeyInput.dataset.clearRequested === "true") {
        payload.clear_api_key = true;
      }
      const saved = await apiFetch(editingId ? `/api/v1/llm-providers/${encodeURIComponent(editingId)}` : "/api/v1/llm-providers", {
        method: editingId ? "PATCH" : "POST",
        body: JSON.stringify(payload),
      });
      document.getElementById("llmProviderModal").close();
      providerAdminState.selectedId = saved.id;
      await this.refresh();
      showToast(editingId ? "Provider 已更新" : "Provider 已创建");
      const afterSave = providerAdminState.afterSave;
      providerAdminState.afterSave = null;
      afterSave?.(saved);
    } catch (error) {
      AdminFormFeedback.show(form, error);
    } finally {
      AdminFormFeedback.setSubmitting(form, false);
    }
  },

  async toggleModel(modelId) {
    const model = (state.llmModels || []).find((item) => item.id === modelId);
    if (!model) return;
    try {
      await apiFetch(`/api/v1/llm-models/${encodeURIComponent(modelId)}`, {
        method: "PATCH",
        body: JSON.stringify({ enabled: model.enabled === false }),
      });
      await this.refresh();
      showToast(model.enabled === false ? "模型已启用" : "模型已停用");
    } catch (error) { showToast(error.message, true); }
  },

  async remove(provider) {
    if (!(await confirmAction({ title: "删除 Provider？", message: `只有未关联模型的 Provider 才能删除。当前对象：${provider.name}`, icon: "trash-2", tone: "danger", confirmLabel: "删除" }))) return;
    try {
      await apiFetch(`/api/v1/llm-providers/${encodeURIComponent(provider.id)}`, { method: "DELETE" });
      providerAdminState.selectedId = null;
      await this.refresh({ preserveSelection: false });
      showToast("Provider 已删除");
    } catch (error) { showToast(error.message, true); }
  },
};

window.llmProvidersAdmin = llmProvidersAdmin;
document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("newLlmProviderBtn")?.addEventListener("click", () => llmProvidersAdmin.openCreate());
  document.getElementById("llmProviderForm")?.addEventListener("submit", (event) => llmProvidersAdmin.save(event));
  document.getElementById("llmProviderType")?.addEventListener("change", () => {
    const type = providerTypeByCode(document.getElementById("llmProviderType").value);
    document.getElementById("llmProviderUseDefaultBaseUrl").checked = Boolean(type?.supports_default_base_url && type?.default_base_url);
    document.getElementById("llmProviderBaseUrl").value = "";
    syncProviderBaseUrl();
  });
  document.getElementById("llmProviderUseDefaultBaseUrl")?.addEventListener("change", syncProviderBaseUrl);
  document.getElementById("llmProviderApiKey")?.addEventListener("focus", (event) => {
    if (event.currentTarget.value === event.currentTarget.dataset.maskedValue) event.currentTarget.select();
  });
  document.getElementById("llmProviderApiKey")?.addEventListener("input", (event) => {
    const input = event.currentTarget;
    if (input.value !== input.dataset.maskedValue) input.type = "password";
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
  document.getElementById("providerCatalogPickerForm")?.addEventListener("submit", (event) => llmProvidersAdmin.submitCatalogPicker(event));
  document.getElementById("toggleProviderCatalogSelection")?.addEventListener("click", () => llmProvidersAdmin.toggleCatalogPickerSelection());
  document.getElementById("closeProviderCatalogPickerModal")?.addEventListener("click", () => llmProvidersAdmin.closeCatalogPicker());
  document.getElementById("cancelProviderCatalogPickerBtn")?.addEventListener("click", () => llmProvidersAdmin.closeCatalogPicker());
});

window.__LlmProviderConfigurationUI = { providerBrand, providerTypeLabel, syncProviderBaseUrl };
