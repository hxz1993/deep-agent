const WEB_SEARCH_PROVIDERS = {
  deepseek: { label: "DeepSeek", requiresKey: true, llmProviderType: "deepseek" },
  kimi: { label: "Kimi", requiresKey: true, llmProviderType: "moonshotai" },
  tavily: { label: "Tavily", requiresKey: true },
  exa: { label: "Exa", requiresKey: true },
  you: { label: "You.com", requiresKey: true },
  duckduckgo: { label: "DuckDuckGo", requiresKey: false },
};
let webSearchTestRunId = 0;
let webSearchOperation = null;
const globalSettingsLoads = new Map();

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
    supportsLlmProvider: Boolean(definition.llmProviderType),
    llmProviderType: definition.llmProviderType || "",
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

function renderWebSearchLlmProviderOptions(provider, selectedId = "") {
  const select = document.getElementById("webSearchLlmProvider");
  if (!select) return;
  const providers = reusableLlmProviders(provider);
  const missing = selectedId && !providers.some(item => item.id === selectedId);
  select.innerHTML = `${missing ? `<option value="${escapeHtml(selectedId)}">供应商已不可用，请重新选择</option>` : ""}${providers.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`).join("")}`;
  select.value = selectedId || providers[0]?.id || "";
  renderConsoleSelect("webSearchLlmProvider", "暂无可沿用的供应商");
}

function webSearchFetchMode(provider, requestedMode) {
  return ["deepseek", "kimi"].includes(provider) && requestedMode === "follow_search" ? "follow_search" : "system";
}

function updateWebFetchMode() {
  const provider = selectedWebSearchProvider();
  const supportsFollow = ["deepseek", "kimi"].includes(provider);
  document.getElementById("webFetchFollowOption")?.classList.toggle("hidden", !supportsFollow);
  const requested = document.querySelector('input[name="webFetchMode"]:checked')?.value;
  const mode = webSearchFetchMode(provider, requested);
  document.querySelectorAll('input[name="webFetchMode"]').forEach(input => { input.checked = input.value === mode; });
  const description = document.getElementById("webFetchDescription");
  if (description) description.textContent = mode === "follow_search"
    ? `使用 ${WEB_SEARCH_PROVIDERS[provider].label} 抓取网页，沿用搜索凭据。`
    : "由 DeepAgent 直接获取网页，无需供应商 Key。";
}

function webSearchSummaryLabel(settings) {
  const value = settings?.value || {};
  if (value.configuration_error) return "配置不可用";
  const configured = value.mode === "provider";
  const label = configured ? WEB_SEARCH_PROVIDERS[value.provider]?.label || value.provider || "未配置" : "未配置";
  return label;
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

function buildWebSearchPayload({ mode, provider, fetchMode, apiKey, maskedApiKey, credentialSource, llmProviderId }) {
  if (mode === "unconfigured") return { version: 1, value: { mode: "unconfigured", fetch_mode: "system" } };
  if (mode !== "provider" || !WEB_SEARCH_PROVIDERS[provider]) throw new Error("请选择搜索供应商");
  const state = providerUiState(provider);
  const value = { mode: "provider", provider, fetch_mode: webSearchFetchMode(provider, fetchMode) };
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
  updateWebFetchMode();
  setWebSearchBusy(Boolean(webSearchOperation));
}

function updateWebSearchProvider({ resetCredentialSource = false } = {}) {
  setWebSearchTestFeedback();
  const provider = selectedWebSearchProvider();
  const current = globalSettingsAdmin.webSearch?.value || {};
  const state = providerUiState(provider);
  const credentialRow = document.getElementById("webSearchCredentialRow");
  const credentialSourceField = document.querySelector('[data-web-search-field="credential-source"]');
  const llmProviderField = document.querySelector('[data-web-search-field="llm-provider"]');
  const keyField = document.querySelector('[data-web-search-field="api-key"]');
  const keylessTest = document.querySelector('[data-web-search-field="keyless-test"]');

  const reusableProviders = reusableLlmProviders(provider);
  if (resetCredentialSource) {
    setWebSearchCredentialSource(state.supportsLlmProvider && reusableProviders.length ? "llm_provider" : "api_key");
  }
  const credentialSource = selectedWebSearchCredentialSource();
  credentialSourceField?.classList.toggle("hidden", !state.supportsLlmProvider);
  llmProviderField?.classList.toggle("hidden", !state.supportsLlmProvider || credentialSource !== "llm_provider");
  keyField?.classList.toggle("hidden", !state.requiresKey || (state.supportsLlmProvider && credentialSource === "llm_provider"));
  keyField?.querySelector(":scope > span")?.classList.toggle("hidden", state.supportsLlmProvider);
  keylessTest?.classList.toggle("hidden", state.requiresKey);
  credentialRow?.classList.toggle("simple", !state.supportsLlmProvider);

  const selectedLlmProviderId = current.provider === provider ? current.llm_provider_id : "";
  renderWebSearchLlmProviderOptions(provider, resetCredentialSource ? selectedLlmProviderId : (document.getElementById("webSearchLlmProvider")?.value || selectedLlmProviderId));
  if (resetCredentialSource) {
    resetWebSearchApiKey(provider);
    // A newly selected search supplier starts with its Fetch default. Merely
    // opening saved/legacy settings or editing credentials keeps the selection.
    const fetchMode = webSearchFetchMode(provider, "follow_search");
    document.querySelectorAll('input[name="webFetchMode"]').forEach(input => { input.checked = input.value === fetchMode; });
  }
  updateWebFetchMode();
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
  const fetchMode = webSearchFetchMode(provider, value.fetch_mode);
  document.querySelectorAll('input[name="webFetchMode"]').forEach(input => { input.checked = input.value === fetchMode; });
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
    fetchMode: document.querySelector('input[name="webFetchMode"]:checked')?.value,
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

let documentParsingOperation = null;
let documentParsingDrafts = new Map();
let documentParsingSelectedProvider = "";
let documentParsingTestRunId = 0;

function documentParsingProviderMetadata(settings, provider) {
  return (settings?.providers || []).find(item => item.provider === provider);
}

function documentParsingSummaryLabel(settings) {
  const value = settings?.value || {};
  if (value.mode !== "provider") return "未配置";
  if (value.configuration_error) return "配置不可用";
  return documentParsingProviderMetadata(settings, value.provider)?.label || value.provider || "未配置";
}

function documentParsingCapabilitiesLabel(metadata) {
  if (!metadata) return "未能读取供应商支持格式，请重新加载配置。";
  return `支持格式：${(metadata.formats || []).join("、") || "未声明"}。${metadata.limits || ""}`;
}

function buildDocumentParsingPayload({ provider, apiKey, maskedApiKey, apiKeySet = false }) {
  if (provider === "unconfigured") return { version: 1, value: { mode: "unconfigured" } };
  if (!["paddle", "glm"].includes(provider)) throw new Error("请选择文档解析供应商");
  const value = { mode: "provider", provider };
  const key = String(apiKey || "").trim();
  if (key && key !== maskedApiKey) value.api_key = key;
  else if (!apiKeySet) throw new Error("请输入当前供应商的 API Key");
  return { version: 1, value };
}

function renderDocumentParsingSummary(settings) {
  const target = document.getElementById("documentParsingSummaryValue");
  if (!target) return;
  target.textContent = documentParsingSummaryLabel(settings);
  target.classList.toggle("unconfigured", settings?.value?.mode !== "provider");
  target.classList.toggle("invalid", Boolean(settings?.value?.configuration_error));
  target.title = settings?.value?.configuration_error || "";
}

function setDocumentParsingFeedback(status = "", message = "") {
  const presentation = webSearchTestFeedbackPresentation(status, message);
  const target = document.getElementById("documentParsingTestFeedback");
  if (!target) return;
  target.classList.toggle("hidden", !presentation.visible);
  target.dataset.status = presentation.status;
  target.title = presentation.message;
  const text = document.getElementById("documentParsingTestFeedbackText");
  if (text) text.textContent = presentation.message;
}

function documentParsingCurrentKeyDraft() {
  const input = document.getElementById("documentParsingApiKey");
  if (!input || !documentParsingSelectedProvider || documentParsingSelectedProvider === "unconfigured") return;
  documentParsingDrafts.set(documentParsingSelectedProvider, {
    apiKey: input.value,
    maskedApiKey: input.dataset.maskedValue || "",
    apiKeySet: input.dataset.apiKeySet === "true",
  });
}

function selectDocumentParsingProvider(provider) {
  if (provider !== "unconfigured" && !documentParsingProviderMetadata(globalSettingsAdmin.documentParsing, provider)) return;
  documentParsingCurrentKeyDraft();
  documentParsingSelectedProvider = provider;
  const current = globalSettingsAdmin.documentParsing?.value || {};
  const metadata = documentParsingProviderMetadata(globalSettingsAdmin.documentParsing, provider);
  const configured = provider !== "unconfigured";
  document.querySelectorAll('input[name="documentParsingProvider"]').forEach(input => { input.checked = input.value === provider; });
  document.getElementById("documentParsingProviderFields")?.classList.toggle("hidden", !configured);
  document.getElementById("documentParsingUnconfigured")?.classList.toggle("hidden", configured);
  const model = document.getElementById("documentParsingModel");
  if (model) model.textContent = metadata?.model || "—";
  const capabilities = document.getElementById("documentParsingCapabilities");
  if (capabilities) capabilities.textContent = documentParsingCapabilitiesLabel(metadata);
  const input = document.getElementById("documentParsingApiKey");
  if (input) {
    const stored = current.mode === "provider" && current.provider === provider;
    const draft = documentParsingDrafts.get(provider) || {
      apiKey: stored ? current.api_key_masked || "" : "",
      maskedApiKey: stored ? current.api_key_masked || "" : "",
      apiKeySet: stored && Boolean(current.api_key_set),
    };
    input.value = draft.apiKey;
    input.dataset.maskedValue = draft.maskedApiKey;
    input.dataset.apiKeySet = String(draft.apiKeySet);
    const toggle = input.closest(".secret-input-control")?.querySelector('.secret-input-toggle[aria-pressed="true"]');
    toggle?.click();
  }
  setDocumentParsingFeedback();
  setDocumentParsingBusy(Boolean(documentParsingOperation));
}

function currentDocumentParsingDraft() {
  documentParsingCurrentKeyDraft();
  return buildDocumentParsingPayload({ provider: documentParsingSelectedProvider, ...documentParsingDrafts.get(documentParsingSelectedProvider) });
}

function setDocumentParsingBusy(busy) {
  document.querySelectorAll("#documentParsingSettingsForm input, #documentParsingSettingsForm button").forEach(control => { control.disabled = Boolean(busy); });
  const cancelAllowed = documentParsingOperation?.kind !== "save";
  ["closeDocumentParsingSettingsBtn", "cancelDocumentParsingBtn"].forEach(id => {
    const button = document.getElementById(id);
    if (button) button.disabled = busy && !cancelAllowed;
  });
  const test = document.getElementById("testDocumentParsingBtn");
  if (test) test.disabled = busy || documentParsingSelectedProvider === "unconfigured";
  const save = document.getElementById("saveDocumentParsingBtn");
  if (save) save.disabled = busy || !documentParsingSelectedProvider;
}

function clearDocumentParsingDrafts() {
  documentParsingDrafts.clear();
  documentParsingSelectedProvider = "";
  documentParsingTestRunId += 1;
  const input = document.getElementById("documentParsingApiKey");
  if (input) { input.value = ""; input.dataset.maskedValue = ""; input.dataset.apiKeySet = "false"; }
  setDocumentParsingFeedback();
}

function openDocumentParsingSettings() {
  clearDocumentParsingDrafts();
  const settings = globalSettingsAdmin.documentParsing;
  const options = document.getElementById("documentParsingProviderOptions");
  if (options) options.innerHTML = (settings?.providers || []).map(metadata => `<label class="web-search-mode-option"><input type="radio" name="documentParsingProvider" value="${escapeHtml(metadata.provider)}" /><span>${escapeHtml(metadata.label)}</span></label>`).join("");
  const selected = settings?.value?.mode === "provider" && documentParsingProviderMetadata(settings, settings.value.provider)
    ? settings.value.provider
    : settings?.value?.configuration_error ? "unconfigured" : settings?.providers?.[0]?.provider || "unconfigured";
  selectDocumentParsingProvider(selected);
  if (settings?.value?.configuration_error) setDocumentParsingFeedback("error", settings.value.configuration_error);
  const dialog = document.getElementById("documentParsingSettingsDialog");
  if (dialog && !dialog.open) dialog.showModal();
}

function closeDocumentParsingSettings() {
  if (documentParsingOperation?.kind === "save") return;
  documentParsingOperation?.controller.abort();
  documentParsingOperation = null;
  document.getElementById("documentParsingSettingsDialog")?.close();
  clearDocumentParsingDrafts();
  setDocumentParsingBusy(false);
}

async function runDocumentParsingOperation(kind) {
  if (documentParsingOperation) return;
  let payload;
  try { payload = currentDocumentParsingDraft(); }
  catch (error) { setDocumentParsingFeedback("error", error.message); return; }
  const operation = { kind, controller: new AbortController() };
  documentParsingOperation = operation;
  const requestId = ++documentParsingTestRunId;
  setDocumentParsingBusy(true);
  setDocumentParsingFeedback(kind === "test" ? "testing" : "", kind === "test" ? "正在解析一页测试样例…" : "");
  try {
    const saved = await apiFetch(`/api/v1/global-settings/document-parsing${kind === "test" ? "/test" : ""}`, {
      method: kind === "test" ? "POST" : "PUT",
      signal: operation.controller.signal,
      body: JSON.stringify(payload),
    });
    if (documentParsingOperation !== operation || requestId !== documentParsingTestRunId) return;
    if (kind === "test") {
      setDocumentParsingFeedback(saved?.ok ? "success" : "error", saved?.message || (saved?.ok ? "连接成功" : "连接失败"));
    } else {
      globalSettingsAdmin.documentParsing = saved;
      renderDocumentParsingSummary(saved);
      window.dispatchEvent?.(new Event("document-parsing-settings-changed"));
      document.getElementById("documentParsingSettingsDialog")?.close();
      clearDocumentParsingDrafts();
      try {
        globalSettingsAdmin.documentParsing = await apiFetch("/api/v1/global-settings/document-parsing");
        renderDocumentParsingSummary(globalSettingsAdmin.documentParsing);
        showToast("文档解析配置已保存");
      } catch (error) { showToast(`文档解析配置已保存，刷新失败：${error.message}`, true); }
    }
  } catch (error) {
    if (documentParsingOperation !== operation || requestId !== documentParsingTestRunId) return;
    setDocumentParsingFeedback("error", error?.message || "文档解析操作失败");
  } finally {
    if (documentParsingOperation === operation) { documentParsingOperation = null; setDocumentParsingBusy(false); }
  }
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
  documentParsing: null,
  webSearchReady: false,
  documentParsingReady: false,
  refresh() {
    const page = this.page;
    // General's independent reads must not retain each other after one response
    // was discarded on another page. Reuse only the child request still pending.
    if (page === "general") return this.refreshPage(page, {
      reuseCompleted: globalSettingsLoads.has("general:webSearch") || globalSettingsLoads.has("general:documentParsing"),
    });
    if (globalSettingsLoads.has(page)) return globalSettingsLoads.get(page);
    const pending = this.refreshPage(page);
    globalSettingsLoads.set(page, pending);
    const cleanup = () => globalSettingsLoads.delete(page);
    pending.then(cleanup, cleanup);
    return pending;
  },
  async refreshPage(page, { reuseCompleted = false } = {}) {
    if (page === "models") return window.modelCatalogAdmin?.refresh();
    try {
      if (page === "operations") {
        const runtimeTarget = document.getElementById("globalSettingsGroups");
        if (runtimeTarget) runtimeTarget.innerHTML = `<div class="global-settings-state">正在加载运行设置…</div>`;
        const [runtime] = await Promise.all([
          apiFetch("/api/v1/global-settings"),
          window.databaseConnectionAdmin?.refresh(),
        ]);
        this.groups = Array.isArray(runtime?.groups) ? runtime.groups : [];
        this.selectedGroup = renderGlobalSettings(this.groups, this.query, this.selectedGroup);
        return;
      }
      await Promise.all([
        this.refreshGeneralSetting("webSearch", { reuseCompleted }),
        this.refreshGeneralSetting("documentParsing", { reuseCompleted }),
      ]);
    } catch (error) {
      if (page === "operations") renderGlobalSettingsError(error);
      else {
        for (const id of ["webSearchSummaryValue", "documentParsingSummaryValue"]) {
          const summary = document.getElementById(id);
          if (summary) summary.textContent = "配置加载失败，请重新加载";
        }
      }
      showToast(error?.message || "全局配置加载失败", true);
    }
  },
  refreshGeneralSetting(kind, { reuseCompleted = false } = {}) {
    const key = `general:${kind}`;
    if (globalSettingsLoads.has(key)) return globalSettingsLoads.get(key);
    if (reuseCompleted && this[`${kind}Ready`]) return Promise.resolve();
    const pending = this.loadGeneralSetting(kind);
    globalSettingsLoads.set(key, pending);
    const cleanup = () => globalSettingsLoads.delete(key);
    pending.then(cleanup, cleanup);
    return pending;
  },
  async loadGeneralSetting(kind) {
    const search = kind === "webSearch";
    const dialog = document.getElementById(search ? "webSearchSettingsDialog" : "documentParsingSettingsDialog");
    // A modal owns its draft and base config until save/cancel. Summary refreshes
    // must not change either the fields or that base while it is being edited.
    const editing = () => Boolean(dialog?.open || (search ? webSearchOperation : documentParsingOperation));
    if (editing()) return;
    const readyKey = `${kind}Ready`;
    const button = document.getElementById(search ? "openWebSearchSettingsBtn" : "openDocumentParsingSettingsBtn");
    const summary = document.getElementById(search ? "webSearchSummaryValue" : "documentParsingSummaryValue");
    this[readyKey] = false;
    if (button) button.disabled = true;
    if (summary) summary.textContent = "正在加载…";
    const active = () => this.page === "general"
      && (!document.body?.dataset.consolePage || (document.body.dataset.consolePage === "configuration"
        && document.getElementById("panel-global-settings")?.classList.contains("active")))
      && !editing();
    try {
      let settings;
      try {
        settings = await apiFetch(`/api/v1/global-settings/${search ? "web-search" : "document-parsing"}`);
      } catch (error) {
        if (!search || error.status !== 409) throw error;
        settings = { invalid: true };
      }
      if (!active()) return;
      this[kind] = search && settings?.invalid ? null : settings;
      this[readyKey] = search || !settings?.invalid;
      if (button) button.disabled = !this[readyKey];
      // Only a modal open operation initializes editable controls.
      if (search) renderWebSearchSummary(this.webSearch);
      else renderDocumentParsingSummary(this.documentParsing);
      if (settings?.invalid && summary) summary.textContent = search ? "配置不可用，请重新配置" : "配置加载失败，请重新加载";
    } catch (error) {
      if (!active()) return;
      if (summary) summary.textContent = "配置加载失败，请重新加载";
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
      try {
        this.webSearch = await apiFetch("/api/v1/global-settings/web-search");
        renderWebSearchSettings(this.webSearch);
        showToast("网络搜索配置已保存");
      } catch (error) { showToast(`网络搜索配置已保存，刷新失败：${error.message}`, true); }
    } catch (error) {
      showToast(error?.message || "网络搜索配置保存失败", true);
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
  webSearchFetchMode,
  webSearchTestFeedbackPresentation,
  documentParsingSummaryLabel,
  documentParsingCapabilitiesLabel,
  buildDocumentParsingPayload,
  openDocumentParsingSettings,
  closeDocumentParsingSettings,
  selectDocumentParsingProvider,
  currentDocumentParsingDraft,
  runDocumentParsingOperation,
};

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("openDocumentParsingSettingsBtn")?.addEventListener("click", () => {
    if (globalSettingsAdmin.documentParsingReady) openDocumentParsingSettings();
  });
  ["closeDocumentParsingSettingsBtn", "cancelDocumentParsingBtn"].forEach(id => document.getElementById(id)?.addEventListener("click", closeDocumentParsingSettings));
  document.getElementById("documentParsingSettingsForm")?.addEventListener("change", event => {
    if (event.target.name === "documentParsingProvider") selectDocumentParsingProvider(event.target.value);
  });
  document.getElementById("documentParsingSettingsForm")?.addEventListener("submit", event => { event.preventDefault(); void runDocumentParsingOperation("save"); });
  document.getElementById("testDocumentParsingBtn")?.addEventListener("click", () => { void runDocumentParsingOperation("test"); });
  document.getElementById("documentParsingApiKey")?.addEventListener("focus", event => { if (event.currentTarget.value === event.currentTarget.dataset.maskedValue) event.currentTarget.select(); });
  document.getElementById("documentParsingApiKey")?.addEventListener("input", () => setDocumentParsingFeedback());
  document.getElementById("documentParsingSettingsDialog")?.addEventListener("cancel", event => { event.preventDefault(); closeDocumentParsingSettings(); });
  document.getElementById("documentParsingSettingsDialog")?.addEventListener("close", clearDocumentParsingDrafts);

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
  document.getElementById("openWebSearchSettingsBtn")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    if (button.disabled || !globalSettingsAdmin.webSearchReady) return;
    button.disabled = true;
    try {
      const providers = await apiFetch("/api/v1/llm-providers");
      if (!Array.isArray(providers)) throw new Error("Provider 目录响应格式错误");
      state.llmProviders = providers;
      if (globalSettingsAdmin.webSearchReady && document.body.dataset.consolePage === "configuration"
        && document.getElementById("panel-global-settings")?.classList.contains("active")
        && globalSettingsAdmin.page === "general") openWebSearchSettings();
    } catch (error) {
      showToast(error.message || "Provider 目录加载失败", true);
    } finally { button.disabled = !globalSettingsAdmin.webSearchReady; }
  });
  document.getElementById("closeWebSearchSettingsBtn")?.addEventListener("click", closeWebSearchSettings);
  bindConsoleSelect("webSearchProvider");
  bindConsoleSelect("webSearchLlmProvider");
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
  document.querySelectorAll('input[name="webFetchMode"]').forEach(input => input.addEventListener("change", updateWebFetchMode));
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
