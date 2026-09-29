const WEB_SEARCH_PROVIDERS = {
  deepseek: { label: "DeepSeek", requiresKey: true, llmProviderType: "deepseek", defaultModel: "deepseek-flash", models: ["deepseek-flash", "deepseek-v4-pro"] },
  kimi: { label: "Kimi", requiresKey: true, llmProviderType: "moonshotai" },
  tavily: { label: "Tavily", requiresKey: true },
  exa: { label: "Exa", requiresKey: true },
  you: { label: "You.com", requiresKey: true },
  duckduckgo: { label: "DuckDuckGo", requiresKey: false },
};
let webSearchTestRunId = 0;
let webSearchOperation = null;

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

function globalSettingsVisibleGroups(groups, query = "") {
  return (Array.isArray(groups) ? groups : []).map((group) => ({
    ...group,
    items: (Array.isArray(group?.items) ? group.items : []).filter((item) => globalSettingMatches(group, item, query)),
  })).filter((group) => group.items.length);
}

function globalSettingsSwitchHtml(groups, selectedKey) {
  return groups.map((group) => `<button class="cap-tab${group.key === selectedKey ? " active" : ""}" type="button" data-global-settings-group="${escapeHtml(group.key)}" aria-pressed="${group.key === selectedKey}">${escapeHtml(group.label || "未命名分组")}</button>`).join("");
}

function globalSettingsTableHtml(group) {
  const rows = (Array.isArray(group?.items) ? group.items : []).map(
    (item) => `<tr>
          <td class="global-setting-name"><strong>${escapeHtml(item.label)}</strong><code>${escapeHtml(item.env_name)}</code></td>
          <td class="global-setting-value">${escapeHtml(globalSettingValue(item))}</td>
          <td><span class="global-setting-source source-${escapeHtml(item.source)}">${escapeHtml(globalSettingSourceLabel(item.source))}</span></td>
          <td class="global-setting-description">${escapeHtml(item.description)}</td>
        </tr>`,
  );
  if (!rows.length) return "";
  return `<div class="table-wrap">
    <table class="data-table global-settings-table">
      <thead><tr><th>环境变量</th><th>当前值</th><th>来源</th><th>说明</th></tr></thead>
      <tbody>${rows.join("")}</tbody>
    </table>
  </div>`;
}

function renderGlobalSettings(groups, query = "", selectedKey = "") {
  const target = document.getElementById("globalSettingsGroups");
  const switchTarget = document.getElementById("globalSettingsGroupSwitch");
  if (!target) return;
  if (!Array.isArray(groups) || groups.length === 0) {
    if (switchTarget) switchTarget.innerHTML = "";
    target.innerHTML = `<div class="global-settings-state">暂无可展示的运行设置。</div>`;
    return;
  }
  const visible = globalSettingsVisibleGroups(groups, query);
  const selected = visible.find((group) => group.key === selectedKey) || visible[0];
  if (switchTarget) switchTarget.innerHTML = selected ? globalSettingsSwitchHtml(visible, selected.key) : "";
  target.innerHTML = selected ? globalSettingsTableHtml(selected) : `<div class="global-settings-state">没有匹配的配置项。</div>`;
  return selected?.key || "";
}

function renderGlobalSettingsError(error) {
  const target = document.getElementById("globalSettingsGroups");
  const switchTarget = document.getElementById("globalSettingsGroupSwitch");
  if (!target) return;
  if (switchTarget) switchTarget.innerHTML = "";
  const message = error instanceof Error ? error.message : String(error);
  target.innerHTML = `<div class="global-settings-state error">
    <span>${escapeHtml(message || "运行设置加载失败")}</span>
    <button class="btn" type="button" data-retry-global-settings>重新加载</button>
  </div>`;
}

function providerUiState(provider, apiKeySet = false) {
  const definition = WEB_SEARCH_PROVIDERS[provider] || {};
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
    const controls = trigger.id.replace(/Trigger$/, "Menu");
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
  const optionLabel = (option) => escapeHtml(option.textContent);
  label.innerHTML = selected ? optionLabel(selected) : escapeHtml(emptyLabel);
  menu.innerHTML = options.length ? options.map((option) => `
    <button class="console-select-option${option.value === select.value ? " selected" : ""}" type="button" role="option" aria-selected="${option.value === select.value}" data-console-select-value="${escapeHtml(option.value)}">
      <span class="web-search-option-label">${optionLabel(option)}</span>${option.value === select.value ? '<i data-lucide="check" aria-hidden="true"></i>' : ""}
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

function renderWebSearchModelMenu(selectedModel) {
  const config = providerUiState("deepseek");
  const select = document.getElementById("webSearchModel");
  if (!select) return;
  const selected = String(selectedModel ?? select.value).trim() || config.defaultModel;
  const models = [...new Set([...config.models, selected])];
  select.innerHTML = models.map((model) => `<option value="${escapeHtml(model)}">${escapeHtml(model)}</option>`).join("");
  select.value = selected;
  renderConsoleSelect("webSearchModel");
}

function renderWebSearchLlmProviderOptions(provider, selectedId = "") {
  const select = document.getElementById("webSearchLlmProvider");
  if (!select) return;
  const providers = reusableLlmProviders(provider);
  const missing = selectedId && !providers.some(item => item.id === selectedId);
  select.innerHTML = `${missing ? `<option value="${escapeHtml(selectedId)}">供应商已不可用，请重新选择</option>` : ""}${providers.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`).join("")}`;
  select.value = selectedId || providers[0]?.id || "";
  renderConsoleSelect("webSearchLlmProvider", "暂无可沿用的供应商");
}

function webSearchSummaryLabel(settings) {
  const value = settings?.value || {};
  if (value.mode !== "provider") return "未配置";
  if (value.configuration_error) return "配置不可用";
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
  if (target) {
    target.textContent = webSearchSummaryLabel(settings);
    target.classList.toggle("unconfigured", settings?.value?.mode !== "provider");
    target.classList.toggle("invalid", Boolean(settings?.value?.configuration_error));
    target.title = settings?.value?.configuration_error || "";
  }
}

function buildWebSearchPayload({ mode, provider, model, apiKey, maskedApiKey, credentialSource, llmProviderId }) {
  if (mode === "unconfigured") return { version: 1, value: { mode: "unconfigured" } };
  if (mode !== "provider" || !WEB_SEARCH_PROVIDERS[provider]) throw new Error("请选择搜索供应商");
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
  if (state.requiresKey && key && key !== maskedApiKey) value.api_key = key;
  return { version: 1, value };
}

function selectedWebSearchCategory() {
  return document.querySelector('input[name="webSearchCategory"]:checked')?.value || "";
}

function selectedWebSearchProvider() {
  const category = selectedWebSearchCategory();
  return category === "third-party" ? document.getElementById("webSearchProvider")?.value || "" : category;
}

function updateWebSearchCategory({ resetCredentialSource = false } = {}) {
  closeWebSearchMenus();
  setWebSearchTestFeedback();
  const category = selectedWebSearchCategory();
  const configured = Boolean(category && category !== "unconfigured");
  document.getElementById("webSearchUnconfigured")?.classList.toggle("hidden", configured);
  document.querySelector('[data-web-search-detail="provider"]')?.classList.toggle("hidden", !configured);
  document.querySelector('[data-web-search-field="provider"]')?.classList.toggle("hidden", category !== "third-party");
  if (configured) updateWebSearchProvider({ resetCredentialSource });
  setWebSearchBusy(Boolean(webSearchOperation));
}

function updateWebSearchProvider({ resetCredentialSource = false } = {}) {
  setWebSearchTestFeedback();
  const provider = selectedWebSearchProvider();
  const current = globalSettingsAdmin.webSearch?.value || {};
  const state = providerUiState(provider);
  const modelField = document.querySelector('[data-web-search-field="model"]');
  const credentialRow = document.getElementById("webSearchCredentialRow");
  const credentialSourceField = document.querySelector('[data-web-search-field="credential-source"]');
  const llmProviderField = document.querySelector('[data-web-search-field="llm-provider"]');
  const keyField = document.querySelector('[data-web-search-field="api-key"]');
  const keylessTest = document.querySelector('[data-web-search-field="keyless-test"]');
  modelField?.classList.toggle("hidden", !state.requiresModel);

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

  if (state.requiresModel) renderWebSearchModelMenu();
  const selectedLlmProviderId = current.provider === provider ? current.llm_provider_id : "";
  renderWebSearchLlmProviderOptions(provider, resetCredentialSource ? selectedLlmProviderId : (document.getElementById("webSearchLlmProvider")?.value || selectedLlmProviderId));
  if (resetCredentialSource) resetWebSearchApiKey(provider);
  window.lucide?.createIcons();
}

function resetWebSearchApiKey(provider) {
  const input = document.getElementById("webSearchApiKey");
  if (!input) return;
  const current = globalSettingsAdmin.webSearch?.value || {};
  const masked = current.mode === "provider" && current.provider === provider && current.credential_source !== "llm_provider"
    ? String(current.api_key_masked || "") : "";
  input.dataset.maskedValue = masked;
  input.value = masked;

  input.placeholder = "输入 API Key";
}

function renderWebSearchSettings(settings) {
  const value = settings?.value || {};
  const provider = value.mode === "provider" && WEB_SEARCH_PROVIDERS[value.provider] ? value.provider : "";
  const category = provider ? (["deepseek", "kimi"].includes(provider) ? provider : "third-party") : "unconfigured";
  document.querySelectorAll('input[name="webSearchCategory"]').forEach(input => { input.checked = input.value === category; });
  const providerInput = document.getElementById("webSearchProvider");
  if (providerInput) providerInput.value = category === "third-party" ? provider : "tavily";
  renderConsoleSelect("webSearchProvider");
  renderWebSearchModelMenu(provider === "deepseek" ? value.model || "" : "");
  setWebSearchCredentialSource(value.credential_source || (value.llm_provider_id ? "llm_provider" : "api_key"));
  renderWebSearchLlmProviderOptions(provider, value.llm_provider_id || "");
  resetWebSearchApiKey(provider);
  renderWebSearchSummary(settings);
  updateWebSearchCategory();
  if (value.configuration_error) setWebSearchTestFeedback("error", value.configuration_error);
}

function currentWebSearchDraft() {
  return buildWebSearchPayload({
    mode: selectedWebSearchCategory() === "unconfigured" ? "unconfigured" : "provider",
    provider: selectedWebSearchProvider(),
    model: document.getElementById("webSearchModel")?.value,
    apiKey: document.getElementById("webSearchApiKey")?.value,
    maskedApiKey: document.getElementById("webSearchApiKey")?.dataset.maskedValue,
    credentialSource: selectedWebSearchCredentialSource(),
    llmProviderId: document.getElementById("webSearchLlmProvider")?.value,
  });
}

function setWebSearchBusy(busy) {
  document.querySelectorAll("#webSearchSettingsForm input, #webSearchSettingsForm select, #webSearchSettingsForm .console-select-trigger, #webSearchSettingsForm .console-select-option").forEach((control) => {
    control.disabled = Boolean(busy);
  });
  ["saveWebSearchBtn", "cancelWebSearchBtn", "testWebSearchBtn", "testWebSearchLlmProviderBtn", "testWebSearchKeylessBtn"].forEach((id) => {
    const button = document.getElementById(id);
    if (button) button.disabled = Boolean(busy) || (id !== "cancelWebSearchBtn" && !selectedWebSearchCategory()) || (id.startsWith("test") && selectedWebSearchCategory() === "unconfigured");
  });
}

function beginWebSearchOperation(kind) {
  if (webSearchOperation) return null;
  const operation = { kind, controller: new AbortController() };
  webSearchOperation = operation;
  closeWebSearchMenus();
  setWebSearchBusy(true);
  return operation;
}

function finishWebSearchOperation(operation) {
  if (webSearchOperation !== operation) return;
  webSearchOperation = null;
  setWebSearchBusy(false);
}

function openWebSearchSettings() {
  renderWebSearchSettings(globalSettingsAdmin.webSearch);
  const dialog = document.getElementById("webSearchSettingsDialog");
  if (dialog && !dialog.open) dialog.showModal();
}

function closeWebSearchSettings() {
  if (webSearchOperation?.kind === "save") return;
  if (webSearchOperation) {
    webSearchOperation.controller.abort();
    finishWebSearchOperation(webSearchOperation);
  }
  closeWebSearchMenus();
  renderWebSearchSettings(globalSettingsAdmin.webSearch);
  const dialog = document.getElementById("webSearchSettingsDialog");
  if (dialog?.open) dialog.close();
}

const globalSettingsAdmin = {
  page: "general",
  selectPage(page, { refresh = true } = {}) {
    if (!["general", "models", "operations"].includes(page)) return;
    this.page = page;
    document.querySelectorAll("[data-settings-page]").forEach(button => {
      const selected = button.dataset.settingsPage === page;
      button.classList.toggle("active", selected);
      if (selected) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
    document.querySelectorAll(".global-settings-page").forEach(section => {
      section.hidden = section.id !== `settings-${page}`;
    });
    if (refresh) void this.refresh();
  },
  groups: [],
  query: "",
  selectedGroup: "",
  webSearch: null,
  async refresh() {
    if (this.page === "models") return window.modelCatalogAdmin?.refresh();
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
      this.selectedGroup = renderGlobalSettings(this.groups, this.query, this.selectedGroup);
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
    this.selectedGroup = renderGlobalSettings(this.groups, this.query, this.selectedGroup);
  },
  selectGroup(key) {
    this.selectedGroup = renderGlobalSettings(this.groups, this.query, key);
  },
  async saveWebSearch() {
    if (!selectedWebSearchCategory()) {
      setWebSearchTestFeedback("error", "请选择搜索供应商");
      return;
    }
    const operation = beginWebSearchOperation("save");
    if (!operation) return;
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
      finishWebSearchOperation(operation);
    }
  },
  async testWebSearch() {
    if (webSearchOperation) return;
    if (!selectedWebSearchCategory()) {
      setWebSearchTestFeedback("error", "请选择搜索供应商");
      return;
    }
    const requestId = ++webSearchTestRunId;
    const operation = beginWebSearchOperation("test");
    setWebSearchTestFeedback("testing");
    try {
      const result = await apiFetch("/api/v1/global-settings/web-search/test", {
        method: "POST",
        signal: operation.controller.signal,
        body: JSON.stringify(currentWebSearchDraft()),
      });
      if (webSearchOperation !== operation || requestId !== webSearchTestRunId) return;
      setWebSearchTestFeedback(
        result?.ok ? "success" : "error",
        result?.message || (result?.ok ? "连接成功" : "连接失败"),
      );
    } catch (error) {
      if (webSearchOperation !== operation || requestId !== webSearchTestRunId) return;
      setWebSearchTestFeedback("error", error?.message || "连接测试失败");
    } finally {
      finishWebSearchOperation(operation);
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
  visibleGroups: globalSettingsVisibleGroups,
  switchHtml: globalSettingsSwitchHtml,
  matches: globalSettingMatches,
  webSearchSummaryLabel,
  webSearchTestFeedbackPresentation,
};

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-settings-page]").forEach(button => {
    button.addEventListener("click", () => globalSettingsAdmin.selectPage(button.dataset.settingsPage));
  });

  document.getElementById("globalSettingsSearch")?.addEventListener("input", (event) => globalSettingsAdmin.search(event.target.value));
  document.getElementById("globalSettingsGroupSwitch")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-global-settings-group]");
    if (button) globalSettingsAdmin.selectGroup(button.dataset.globalSettingsGroup);
  });
  document.getElementById("globalSettingsGroups")?.addEventListener("click", (event) => {
    if (event.target.closest("[data-retry-global-settings]")) globalSettingsAdmin.refresh();
  });
  document.getElementById("openWebSearchSettingsBtn")?.addEventListener("click", openWebSearchSettings);
  document.getElementById("closeWebSearchSettingsBtn")?.addEventListener("click", closeWebSearchSettings);
  bindConsoleSelect("webSearchProvider");
  bindConsoleSelect("webSearchLlmProvider");
  bindConsoleSelect("webSearchModel");
  document.querySelectorAll('input[name="webSearchCategory"]').forEach((input) => input.addEventListener("change", () => {
    const key = document.getElementById("webSearchApiKey");
    if (key) key.value = "";
    updateWebSearchCategory({ resetCredentialSource: true });
  }));
  document.getElementById("webSearchProvider")?.addEventListener("change", () => {
    const key = document.getElementById("webSearchApiKey");
    if (key) key.value = "";
    updateWebSearchProvider({ resetCredentialSource: true });
  });
  document.querySelectorAll('input[name="webSearchCredentialSource"]').forEach((input) => input.addEventListener("change", () => updateWebSearchProvider()));
  document.getElementById("webSearchLlmProvider")?.addEventListener("change", () => setWebSearchTestFeedback());
  document.getElementById("webSearchModel")?.addEventListener("change", () => setWebSearchTestFeedback());
  document.getElementById("webSearchSettingsForm")?.addEventListener("submit", (event) => {
    event.preventDefault();
    globalSettingsAdmin.saveWebSearch();
  });
  document.getElementById("cancelWebSearchBtn")?.addEventListener("click", closeWebSearchSettings);
  document.getElementById("webSearchSettingsDialog")?.addEventListener("cancel", (event) => {
    event.preventDefault();
    closeWebSearchSettings();
  });
  document.getElementById("testWebSearchBtn")?.addEventListener("click", () => globalSettingsAdmin.testWebSearch());
  document.getElementById("testWebSearchLlmProviderBtn")?.addEventListener("click", () => globalSettingsAdmin.testWebSearch());
  document.getElementById("testWebSearchKeylessBtn")?.addEventListener("click", () => globalSettingsAdmin.testWebSearch());
  document.getElementById("webSearchApiKey")?.addEventListener("focus", (event) => {
    const input = event.currentTarget;
    if (input.value === input.dataset.maskedValue) input.select();
  });
  document.getElementById("webSearchApiKey")?.addEventListener("input", (event) => {
    setWebSearchTestFeedback();
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest("#webSearchSettingsDialog .console-select")) closeWebSearchMenus();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeWebSearchMenus();
  });
});
