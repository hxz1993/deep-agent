const WEB_SEARCH_PROVIDERS = {
  deepseek: { label: "DeepSeek", requiresKey: true, llmProviderType: "deepseek", defaultModel: "deepseek-v4-flash", models: ["deepseek-v4-flash", "deepseek-v4-pro", "deepseek-v4-flash-vision-exp"] },
  kimi: { label: "Kimi", requiresKey: true, llmProviderType: "moonshotai", defaultModel: "kimi-k3", models: ["kimi-k3", "kimi-k2.6"] },
  tavily: { label: "Tavily", requiresKey: true },
  exa: { label: "Exa", requiresKey: true },
  you: { label: "You.com", requiresKey: true },
  duckduckgo: { label: "DuckDuckGo", requiresKey: false },
};
let webSearchTestRunId = 0;

function globalSettingSourceLabel(source) {
  return source === "environment" ? "环境配置" : "代码默认值";
}

function globalSettingValue(item) {
  const display = item?.display_value == null ? "—" : String(item.display_value);
  if (
    item?.value === null ||
    item.value === "" ||
    (Array.isArray(item.value) && item.value.length === 0)
  ) {
    return display;
  }
  return item?.unit ? `${display} ${item.unit}` : display;
}

function globalSettingMatches(group, item, query) {
  const normalized = String(query || "").trim().toLocaleLowerCase();
  if (!normalized) return true;
  return [
    group?.label,
    item?.label,
    item?.env_name,
    item?.description,
    item?.display_value,
    globalSettingSourceLabel(item?.source),
  ].some((value) => String(value || "").toLocaleLowerCase().includes(normalized));
}

function globalSettingsTableHtml(groups, query = "") {
  const rows = (Array.isArray(groups) ? groups : []).flatMap((group) => {
    const items = (Array.isArray(group?.items) ? group.items : []).filter((item) =>
      globalSettingMatches(group, item, query),
    );
    if (!items.length) return [];
    return [
      `<tr class="global-settings-group-row"><th colspan="5">${escapeHtml(group?.label || "未命名分组")}</th></tr>`,
      ...items.map(
        (item) => `<tr>
          <td><strong>${escapeHtml(item.label)}</strong></td>
          <td class="global-setting-value">${escapeHtml(globalSettingValue(item))}</td>
          <td><code>${escapeHtml(item.env_name)}</code></td>
          <td><span class="global-setting-source source-${escapeHtml(item.source)}">${escapeHtml(globalSettingSourceLabel(item.source))}</span></td>
          <td class="global-setting-description">${escapeHtml(item.description)}</td>
        </tr>`,
      ),
    ];
  });
  if (!rows.length) return "";
  return `<div class="table-wrap">
    <table class="data-table global-settings-table">
      <thead><tr><th>配置项</th><th>当前值</th><th>环境变量</th><th>来源</th><th>说明</th></tr></thead>
      <tbody>${rows.join("")}</tbody>
    </table>
  </div>`;
}

function renderGlobalSettings(groups, query = "") {
  const target = document.getElementById("globalSettingsGroups");
  if (!target) return;
  if (!Array.isArray(groups) || groups.length === 0) {
    target.innerHTML = `<div class="global-settings-state">暂无可展示的运行设置。</div>`;
    return;
  }
  target.innerHTML = globalSettingsTableHtml(groups, query) || `<div class="global-settings-state">没有匹配的配置项。</div>`;
}

function renderGlobalSettingsError(error) {
  const target = document.getElementById("globalSettingsGroups");
  if (!target) return;
  const message = error instanceof Error ? error.message : String(error);
  target.innerHTML = `<div class="global-settings-state error">
    <span>${escapeHtml(message || "运行设置加载失败")}</span>
    <button class="btn" type="button" data-retry-global-settings>重新加载</button>
  </div>`;
}

function providerUiState(provider, apiKeySet = false) {
  const definition = WEB_SEARCH_PROVIDERS[provider] || WEB_SEARCH_PROVIDERS.deepseek;
  return {
    provider,
    requiresKey: definition.requiresKey,
    requiresModel: Boolean(definition.defaultModel),
    supportsLlmProvider: Boolean(definition.llmProviderType),
    llmProviderType: definition.llmProviderType || "",
    defaultModel: definition.defaultModel || "",
    models: definition.models || [],
    apiKeySet: Boolean(apiKeySet),
  };
}

function reusableLlmProviders(provider) {
  const providerType = WEB_SEARCH_PROVIDERS[provider]?.llmProviderType;
  if (!providerType) return [];
  return (state.llmProviders || []).filter((item) =>
    item.provider_type === providerType && item.api_key_set,
  );
}

function closeWebSearchMenus(exceptId = "") {
  document.querySelectorAll("#webSearchSettingsDialog .console-select-menu").forEach((menu) => {
    if (menu.id !== exceptId) {
      menu.classList.add("hidden");
      menu.classList.remove("open-up");
    }
  });
  document.querySelectorAll("#webSearchSettingsDialog [aria-expanded]").forEach((trigger) => {
    const controls = trigger.id === "webSearchModelToggle"
      ? "webSearchModelMenu"
      : trigger.id.replace(/Trigger$/, "Menu");
    if (controls !== exceptId) trigger.setAttribute("aria-expanded", "false");
  });
}

function positionWebSearchMenu(trigger, menu) {
  menu.classList.remove("open-up");
  const triggerRect = trigger.getBoundingClientRect();
  const menuHeight = menu.getBoundingClientRect().height;
  const spaceBelow = window.innerHeight - triggerRect.bottom - 8;
  const spaceAbove = triggerRect.top - 8;
  if (menuHeight > spaceBelow && spaceAbove > spaceBelow) menu.classList.add("open-up");
}

function consoleSelectParts(selectId) {
  return {
    select: document.getElementById(selectId),
    trigger: document.getElementById(`${selectId}Trigger`),
    label: document.getElementById(`${selectId}Label`),
    menu: document.getElementById(`${selectId}Menu`),
  };
}

function renderConsoleSelect(selectId, emptyLabel = "请选择") {
  const { select, label, menu } = consoleSelectParts(selectId);
  if (!select || !label || !menu) return;
  const options = Array.from(select.options);
  const selected = options.find((option) => option.value === select.value);
  label.textContent = selected?.textContent || emptyLabel;
  menu.innerHTML = options.length ? options.map((option) => `
    <button class="console-select-option${option.value === select.value ? " selected" : ""}" type="button" role="option" aria-selected="${option.value === select.value}" data-console-select-value="${escapeHtml(option.value)}">
      <span>${escapeHtml(option.textContent)}</span>${option.value === select.value ? '<i data-lucide="check" aria-hidden="true"></i>' : ""}
    </button>`).join("") : `<div class="console-select-empty">${escapeHtml(emptyLabel)}</div>`;
  window.lucide?.createIcons();
}

function setConsoleSelectValue(selectId, value, { emit = false } = {}) {
  const { select } = consoleSelectParts(selectId);
  if (!select) return;
  select.value = String(value || "");
  renderConsoleSelect(selectId);
  if (emit) select.dispatchEvent(new Event("change", { bubbles: true }));
}

function bindConsoleSelect(selectId) {
  const { select, trigger, menu } = consoleSelectParts(selectId);
  if (!select || !trigger || !menu) return;
  trigger.addEventListener("click", () => {
    const opening = menu.classList.contains("hidden");
    closeWebSearchMenus(opening ? menu.id : "");
    menu.classList.toggle("hidden", !opening);
    trigger.setAttribute("aria-expanded", String(opening));
    if (opening) positionWebSearchMenu(trigger, menu);
  });
  menu.addEventListener("click", (event) => {
    const option = event.target.closest("[data-console-select-value]");
    if (!option) return;
    setConsoleSelectValue(selectId, option.dataset.consoleSelectValue, { emit: true });
    closeWebSearchMenus();
  });
  renderConsoleSelect(selectId);
}

function selectedWebSearchCredentialSource() {
  return document.querySelector('input[name="webSearchCredentialSource"]:checked')?.value || "api_key";
}

function setWebSearchCredentialSource(source) {
  const value = source === "llm_provider" ? "llm_provider" : "api_key";
  const input = document.querySelector(`input[name="webSearchCredentialSource"][value="${value}"]`);
  if (input) input.checked = true;
}

function renderWebSearchModelMenu(query = "") {
  const provider = document.getElementById("webSearchProvider")?.value || "deepseek";
  const models = providerUiState(provider).models;
  const normalized = String(query || "").trim().toLowerCase();
  const visible = models.filter((model) => !normalized || model.toLowerCase().includes(normalized));
  const current = document.getElementById("webSearchModel")?.value || "";
  const menu = document.getElementById("webSearchModelMenu");
  if (!menu) return;
  menu.innerHTML = visible.length ? visible.map((model) => `
    <button class="console-select-option${model === current ? " selected" : ""}" type="button" role="option" aria-selected="${model === current}" data-web-search-model="${escapeHtml(model)}">
      <span>${escapeHtml(model)}</span>${model === current ? '<i data-lucide="check" aria-hidden="true"></i>' : ""}
    </button>`).join("") : `<div class="console-select-empty">可直接输入模型名称</div>`;
  window.lucide?.createIcons();
}

function renderWebSearchLlmProviderOptions(provider, selectedId = "") {
  const select = document.getElementById("webSearchLlmProvider");
  if (!select) return;
  const providers = reusableLlmProviders(provider);
  select.innerHTML = providers.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`).join("");
  select.value = providers.some((item) => item.id === selectedId) ? selectedId : (providers[0]?.id || "");
  renderConsoleSelect("webSearchLlmProvider", "暂无可沿用的供应商");
}

function webSearchSummaryLabel(settings) {
  const value = settings?.value || { mode: "native" };
  if (value.mode !== "provider") return "会话原生搜索";
  return WEB_SEARCH_PROVIDERS[value.provider]?.label || value.provider || "未配置";
}

function webSearchTestFeedbackPresentation(status, message = "") {
  const normalized = ["testing", "success", "error"].includes(status) ? status : "";
  const defaults = {
    testing: "正在测试连接…",
    success: "连接成功",
    error: "连接失败",
  };
  return {
    visible: Boolean(normalized),
    status: normalized,
    message: String(message || defaults[normalized] || "").trim(),
  };
}

function setWebSearchTestFeedback(status = "", message = "") {
  const target = document.getElementById("webSearchTestFeedback");
  const text = document.getElementById("webSearchTestFeedbackText");
  if (!target || !text) return;
  const presentation = webSearchTestFeedbackPresentation(status, message);
  if (!presentation.visible) webSearchTestRunId += 1;
  target.classList.toggle("hidden", !presentation.visible);
  target.dataset.status = presentation.status;
  target.title = presentation.message;
  text.textContent = presentation.message;
}

function renderWebSearchSummary(settings) {
  const target = document.getElementById("webSearchSummaryValue");
  if (target) target.textContent = webSearchSummaryLabel(settings);
}

function buildWebSearchPayload({ mode, provider, model, apiKey, credentialSource, llmProviderId }) {
  if (mode !== "provider") return { version: 1, value: { mode: "native" } };
  const state = providerUiState(provider);
  const value = { mode: "provider", provider };
  if (state.requiresModel) value.model = String(model || state.defaultModel).trim();
  if (state.supportsLlmProvider) {
    value.credential_source = credentialSource === "llm_provider" ? "llm_provider" : "api_key";
    if (value.credential_source === "llm_provider") {
      value.llm_provider_id = String(llmProviderId || "").trim();
      return { version: 1, value };
    }
  }
  const key = String(apiKey || "").trim();
  if (state.requiresKey && key) value.api_key = key;
  return { version: 1, value };
}

function selectedWebSearchMode() {
  return document.querySelector('input[name="webSearchMode"]:checked')?.value || "native";
}

function updateWebSearchMode() {
  setWebSearchTestFeedback();
  const mode = selectedWebSearchMode();
  document.querySelectorAll("[data-web-search-detail]").forEach((element) => {
    element.classList.toggle("hidden", element.dataset.webSearchDetail !== mode);
  });
  document.querySelectorAll(".web-search-mode-card").forEach((element) => {
    element.classList.toggle("active", element.querySelector("input")?.checked);
  });
  if (mode === "provider") updateWebSearchProvider();
}

function updateWebSearchProvider({ resetCredentialSource = false } = {}) {
  setWebSearchTestFeedback();
  const provider = document.getElementById("webSearchProvider")?.value || "deepseek";
  const current = globalSettingsAdmin.webSearch?.value || {};
  const currentUsesDirectKey = current.provider === provider && current.credential_source !== "llm_provider";
  const state = providerUiState(provider, currentUsesDirectKey && current.api_key_set);
  const modelField = document.querySelector('[data-web-search-field="model"]');
  const providerGrid = document.getElementById("webSearchProviderGrid");
  const credentialRow = document.getElementById("webSearchCredentialRow");
  const credentialSourceField = document.querySelector('[data-web-search-field="credential-source"]');
  const llmProviderField = document.querySelector('[data-web-search-field="llm-provider"]');
  const keyField = document.querySelector('[data-web-search-field="api-key"]');
  const keylessTest = document.querySelector('[data-web-search-field="keyless-test"]');
  modelField?.classList.toggle("hidden", !state.requiresModel);
  providerGrid?.classList.toggle("single", !state.requiresModel);

  const reusableProviders = reusableLlmProviders(provider);
  if (resetCredentialSource) {
    setWebSearchCredentialSource(state.supportsLlmProvider && reusableProviders.length ? "llm_provider" : "api_key");
  }
  const credentialSource = selectedWebSearchCredentialSource();
  credentialSourceField?.classList.toggle("hidden", !state.supportsLlmProvider);
  llmProviderField?.classList.toggle("hidden", !state.supportsLlmProvider || credentialSource !== "llm_provider");
  keyField?.classList.toggle("hidden", !state.requiresKey || (state.supportsLlmProvider && credentialSource === "llm_provider"));
  keylessTest?.classList.toggle("hidden", state.requiresKey);
  credentialRow?.classList.toggle("simple", !state.supportsLlmProvider);

  const modelInput = document.getElementById("webSearchModel");
  if (modelInput && state.requiresModel && !modelInput.value.trim()) modelInput.value = state.defaultModel;
  renderWebSearchModelMenu();
  const selectedLlmProviderId = current.provider === provider ? current.llm_provider_id : "";
  renderWebSearchLlmProviderOptions(provider, document.getElementById("webSearchLlmProvider")?.value || selectedLlmProviderId);
  const apiKey = document.getElementById("webSearchApiKey");
  if (apiKey) apiKey.placeholder = state.apiKeySet ? "已配置，留空则保持不变" : "输入 API Key";
  window.lucide?.createIcons();
}

function renderWebSearchSettings(settings) {
  const value = settings?.value || { mode: "native" };
  const mode = value.mode === "provider" ? "provider" : "native";
  const modeInput = document.querySelector(`input[name="webSearchMode"][value="${mode}"]`);
  if (modeInput) modeInput.checked = true;
  const provider = value.provider && WEB_SEARCH_PROVIDERS[value.provider] ? value.provider : "deepseek";
  const providerInput = document.getElementById("webSearchProvider");
  if (providerInput) providerInput.value = provider;
  renderConsoleSelect("webSearchProvider");
  const modelInput = document.getElementById("webSearchModel");
  if (modelInput) modelInput.value = value.model || providerUiState(provider).defaultModel;
  setWebSearchCredentialSource(value.credential_source || (value.llm_provider_id ? "llm_provider" : "api_key"));
  renderWebSearchLlmProviderOptions(provider, value.llm_provider_id || "");
  const apiKey = document.getElementById("webSearchApiKey");
  if (apiKey) apiKey.value = "";
  renderWebSearchSummary(settings);
  updateWebSearchMode();
}

function currentWebSearchDraft() {
  return buildWebSearchPayload({
    mode: selectedWebSearchMode(),
    provider: document.getElementById("webSearchProvider")?.value,
    model: document.getElementById("webSearchModel")?.value,
    apiKey: document.getElementById("webSearchApiKey")?.value,
    credentialSource: selectedWebSearchCredentialSource(),
    llmProviderId: document.getElementById("webSearchLlmProvider")?.value,
  });
}

function setWebSearchBusy(busy) {
  ["saveWebSearchBtn", "cancelWebSearchBtn", "testWebSearchBtn", "testWebSearchLlmProviderBtn", "testWebSearchKeylessBtn"].forEach((id) => {
    const button = document.getElementById(id);
    if (button) button.disabled = Boolean(busy);
  });
}

function openWebSearchSettings() {
  renderWebSearchSettings(globalSettingsAdmin.webSearch);
  const dialog = document.getElementById("webSearchSettingsDialog");
  if (dialog && !dialog.open) dialog.showModal();
}

function closeWebSearchSettings() {
  closeWebSearchMenus();
  renderWebSearchSettings(globalSettingsAdmin.webSearch);
  const dialog = document.getElementById("webSearchSettingsDialog");
  if (dialog?.open) dialog.close();
}

const globalSettingsAdmin = {
  groups: [],
  query: "",
  webSearch: null,
  async refresh() {
    void window.databaseConnectionAdmin?.refresh();
    const runtimeTarget = document.getElementById("globalSettingsGroups");
    if (runtimeTarget) runtimeTarget.innerHTML = `<div class="global-settings-state">正在加载运行设置…</div>`;
    try {
      const [runtime, webSearch] = await Promise.all([
        apiFetch("/api/v1/global-settings"),
        apiFetch("/api/v1/global-settings/web-search").catch((error) => {
          if (error.status !== 409) throw error;
          return { invalid: true };
        }),
        loadLlmProviders(),
      ]);
      this.groups = Array.isArray(runtime?.groups) ? runtime.groups : [];
      this.webSearch = webSearch?.invalid ? null : webSearch;
      renderGlobalSettings(this.groups, this.query);
      renderWebSearchSettings(this.webSearch);
      if (webSearch?.invalid) {
        const summary = document.getElementById("webSearchSummaryValue");
        if (summary) summary.textContent = "配置不可用，请重新配置";
      }
    } catch (error) {
      renderGlobalSettingsError(error);
      showToast(error?.message || "全局配置加载失败", true);
    }
  },
  search(query) {
    this.query = String(query || "");
    renderGlobalSettings(this.groups, this.query);
  },
  async saveWebSearch() {
    setWebSearchBusy(true);
    try {
      const saved = await apiFetch("/api/v1/global-settings/web-search", {
        method: "PUT",
        body: JSON.stringify(currentWebSearchDraft()),
      });
      this.webSearch = saved;
      renderWebSearchSettings(saved);
      document.getElementById("webSearchSettingsDialog")?.close();
      showToast("Web Search 配置已保存");
    } catch (error) {
      showToast(error?.message || "Web Search 配置保存失败", true);
    } finally {
      setWebSearchBusy(false);
    }
  },
  async testWebSearch() {
    const requestId = ++webSearchTestRunId;
    setWebSearchBusy(true);
    setWebSearchTestFeedback("testing");
    try {
      const result = await apiFetch("/api/v1/global-settings/web-search/test", {
        method: "POST",
        body: JSON.stringify(currentWebSearchDraft()),
      });
      if (requestId !== webSearchTestRunId) return;
      setWebSearchTestFeedback(
        result?.ok ? "success" : "error",
        result?.message || (result?.ok ? "连接成功" : "连接失败"),
      );
    } catch (error) {
      if (requestId !== webSearchTestRunId) return;
      setWebSearchTestFeedback("error", error?.message || "连接测试失败");
    } finally {
      setWebSearchBusy(false);
    }
  },
};

window.globalSettingsAdmin = globalSettingsAdmin;
window.__GlobalSettingsUI = {
  providers: WEB_SEARCH_PROVIDERS,
  providerState: providerUiState,
  buildPayload: buildWebSearchPayload,
  sourceLabel: globalSettingSourceLabel,
  settingValue: globalSettingValue,
  tableHtml: globalSettingsTableHtml,
  matches: globalSettingMatches,
  webSearchSummaryLabel,
  webSearchTestFeedbackPresentation,
};

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("globalSettingsSearch")?.addEventListener("input", (event) => globalSettingsAdmin.search(event.target.value));
  document.getElementById("globalSettingsGroups")?.addEventListener("click", (event) => {
    if (event.target.closest("[data-retry-global-settings]")) globalSettingsAdmin.refresh();
  });
  document.getElementById("openWebSearchSettingsBtn")?.addEventListener("click", openWebSearchSettings);
  document.getElementById("closeWebSearchSettingsBtn")?.addEventListener("click", closeWebSearchSettings);
  bindConsoleSelect("webSearchProvider");
  bindConsoleSelect("webSearchLlmProvider");
  document.querySelectorAll('input[name="webSearchMode"]').forEach((input) => input.addEventListener("change", updateWebSearchMode));
  document.getElementById("webSearchProvider")?.addEventListener("change", () => {
    const modelInput = document.getElementById("webSearchModel");
    if (modelInput) modelInput.value = "";
    updateWebSearchProvider({ resetCredentialSource: true });
  });
  document.querySelectorAll('input[name="webSearchCredentialSource"]').forEach((input) => input.addEventListener("change", () => updateWebSearchProvider()));
  document.getElementById("webSearchLlmProvider")?.addEventListener("change", () => setWebSearchTestFeedback());
  document.getElementById("webSearchModelToggle")?.addEventListener("click", () => {
    const menu = document.getElementById("webSearchModelMenu");
    const trigger = document.getElementById("webSearchModelToggle");
    if (!menu || !trigger) return;
    const opening = menu.classList.contains("hidden");
    renderWebSearchModelMenu(document.getElementById("webSearchModel")?.value);
    closeWebSearchMenus(opening ? menu.id : "");
    menu.classList.toggle("hidden", !opening);
    trigger.setAttribute("aria-expanded", String(opening));
    if (opening) positionWebSearchMenu(trigger, menu);
  });
  document.getElementById("webSearchModel")?.addEventListener("input", (event) => {
    setWebSearchTestFeedback();
    renderWebSearchModelMenu(event.target.value);
  });
  document.getElementById("webSearchModelMenu")?.addEventListener("click", (event) => {
    const option = event.target.closest("[data-web-search-model]");
    if (!option) return;
    const input = document.getElementById("webSearchModel");
    if (input) input.value = option.dataset.webSearchModel;
    renderWebSearchModelMenu();
    closeWebSearchMenus();
  });
  document.getElementById("webSearchSettingsForm")?.addEventListener("submit", (event) => {
    event.preventDefault();
    globalSettingsAdmin.saveWebSearch();
  });
  document.getElementById("cancelWebSearchBtn")?.addEventListener("click", closeWebSearchSettings);
  document.getElementById("webSearchSettingsDialog")?.addEventListener("cancel", () => renderWebSearchSettings(globalSettingsAdmin.webSearch));
  document.getElementById("testWebSearchBtn")?.addEventListener("click", () => globalSettingsAdmin.testWebSearch());
  document.getElementById("testWebSearchLlmProviderBtn")?.addEventListener("click", () => globalSettingsAdmin.testWebSearch());
  document.getElementById("testWebSearchKeylessBtn")?.addEventListener("click", () => globalSettingsAdmin.testWebSearch());
  document.getElementById("webSearchApiKey")?.addEventListener("input", () => setWebSearchTestFeedback());
  document.addEventListener("click", (event) => {
    if (!event.target.closest("#webSearchSettingsDialog .console-select, #webSearchSettingsDialog .console-combobox")) closeWebSearchMenus();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeWebSearchMenus();
  });
});
