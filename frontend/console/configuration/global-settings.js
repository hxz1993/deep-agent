const WEB_SEARCH_PROVIDERS = {
  deepseek: { label: "DeepSeek", requiresKey: true, defaultModel: "deepseek-v4-flash", models: ["deepseek-v4-flash", "deepseek-v4-pro", "deepseek-v4-flash-vision-exp"] },
  kimi: { label: "Kimi", requiresKey: true, defaultModel: "kimi-k3", models: ["kimi-k3", "kimi-k2.6"] },
  tavily: { label: "Tavily", requiresKey: true },
  exa: { label: "Exa", requiresKey: true },
  you: { label: "You.com", requiresKey: true },
  duckduckgo: { label: "DuckDuckGo", requiresKey: false },
};

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
    defaultModel: definition.defaultModel || "",
    models: definition.models || [],
    apiKeySet: Boolean(apiKeySet),
  };
}

function buildWebSearchPayload({ mode, provider, model, apiKey }) {
  if (mode !== "provider") return { version: 1, value: { mode: "native" } };
  const state = providerUiState(provider);
  const value = { mode: "provider", provider };
  if (state.requiresModel) value.model = String(model || state.defaultModel).trim();
  const key = String(apiKey || "").trim();
  if (state.requiresKey && key) value.api_key = key;
  return { version: 1, value };
}

function selectedWebSearchMode() {
  return document.querySelector('input[name="webSearchMode"]:checked')?.value || "native";
}

function updateWebSearchMode() {
  const mode = selectedWebSearchMode();
  document.querySelectorAll("[data-web-search-detail]").forEach((element) => {
    element.classList.toggle("hidden", element.dataset.webSearchDetail !== mode);
  });
  document.querySelectorAll(".web-search-mode-card").forEach((element) => {
    element.classList.toggle("active", element.querySelector("input")?.checked);
  });
  if (mode === "provider") updateWebSearchProvider();
}

function updateWebSearchProvider() {
  const provider = document.getElementById("webSearchProvider")?.value || "deepseek";
  const current = globalSettingsAdmin.webSearch?.value || {};
  const state = providerUiState(provider, current.provider === provider && current.api_key_set);
  const modelField = document.querySelector('[data-web-search-field="model"]');
  const keyField = document.querySelector('[data-web-search-field="api-key"]');
  const keylessTest = document.querySelector('[data-web-search-field="keyless-test"]');
  modelField?.classList.toggle("hidden", !state.requiresModel);
  keyField?.classList.toggle("hidden", !state.requiresKey);
  keylessTest?.classList.toggle("hidden", state.requiresKey);

  const modelInput = document.getElementById("webSearchModel");
  const modelOptions = document.getElementById("webSearchModelOptions");
  if (modelInput && state.requiresModel && !modelInput.value.trim()) modelInput.value = state.defaultModel;
  if (modelOptions) modelOptions.innerHTML = state.models.map((model) => `<option value="${escapeHtml(model)}"></option>`).join("");
  const apiKey = document.getElementById("webSearchApiKey");
  const hint = document.getElementById("webSearchApiKeyHint");
  if (apiKey) apiKey.placeholder = state.apiKeySet ? "已配置，留空则保持不变" : "输入 API Key";
  if (hint) hint.textContent = state.apiKeySet ? "API Key 已配置；留空保存会保留原值。" : `输入 ${WEB_SEARCH_PROVIDERS[provider].label} 的 API Key。`;
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
  const modelInput = document.getElementById("webSearchModel");
  if (modelInput) modelInput.value = value.model || providerUiState(provider).defaultModel;
  const apiKey = document.getElementById("webSearchApiKey");
  if (apiKey) apiKey.value = "";
  updateWebSearchMode();
}

function currentWebSearchDraft() {
  return buildWebSearchPayload({
    mode: selectedWebSearchMode(),
    provider: document.getElementById("webSearchProvider")?.value,
    model: document.getElementById("webSearchModel")?.value,
    apiKey: document.getElementById("webSearchApiKey")?.value,
  });
}

function setWebSearchBusy(busy) {
  ["saveWebSearchBtn", "cancelWebSearchBtn", "testWebSearchBtn", "testWebSearchKeylessBtn"].forEach((id) => {
    const button = document.getElementById(id);
    if (button) button.disabled = Boolean(busy);
  });
}

function switchGlobalSettingsView(name) {
  const targetName = name === "runtime" ? "runtime" : "web-search";
  document.querySelectorAll("[data-global-settings-view]").forEach((button) => {
    button.classList.toggle("active", button.dataset.globalSettingsView === targetName);
  });
  document.querySelectorAll("[data-global-settings-panel]").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.globalSettingsPanel === targetName);
  });
}

const globalSettingsAdmin = {
  groups: [],
  query: "",
  webSearch: null,
  async refresh() {
    const runtimeTarget = document.getElementById("globalSettingsGroups");
    if (runtimeTarget) runtimeTarget.innerHTML = `<div class="global-settings-state">正在加载运行设置…</div>`;
    try {
      const [runtime, webSearch] = await Promise.all([
        apiFetch("/api/v1/global-settings"),
        apiFetch("/api/v1/global-settings/web-search"),
      ]);
      this.groups = Array.isArray(runtime?.groups) ? runtime.groups : [];
      this.webSearch = webSearch;
      renderGlobalSettings(this.groups, this.query);
      renderWebSearchSettings(webSearch);
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
      showToast("Web Search 配置已保存");
    } catch (error) {
      showToast(error?.message || "Web Search 配置保存失败", true);
    } finally {
      setWebSearchBusy(false);
    }
  },
  async testWebSearch() {
    setWebSearchBusy(true);
    try {
      const result = await apiFetch("/api/v1/global-settings/web-search/test", {
        method: "POST",
        body: JSON.stringify(currentWebSearchDraft()),
      });
      showToast(result?.message || (result?.ok ? "连接成功" : "连接失败"), !result?.ok);
    } catch (error) {
      showToast(error?.message || "连接测试失败", true);
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
};

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("globalSettingsSearch")?.addEventListener("input", (event) => globalSettingsAdmin.search(event.target.value));
  document.getElementById("globalSettingsGroups")?.addEventListener("click", (event) => {
    if (event.target.closest("[data-retry-global-settings]")) globalSettingsAdmin.refresh();
  });
  document.querySelectorAll("[data-global-settings-view]").forEach((button) => {
    button.addEventListener("click", () => switchGlobalSettingsView(button.dataset.globalSettingsView));
  });
  document.querySelectorAll('input[name="webSearchMode"]').forEach((input) => input.addEventListener("change", updateWebSearchMode));
  document.getElementById("webSearchProvider")?.addEventListener("change", () => {
    const modelInput = document.getElementById("webSearchModel");
    if (modelInput) modelInput.value = "";
    updateWebSearchProvider();
  });
  document.getElementById("webSearchSettingsForm")?.addEventListener("submit", (event) => {
    event.preventDefault();
    globalSettingsAdmin.saveWebSearch();
  });
  document.getElementById("cancelWebSearchBtn")?.addEventListener("click", () => renderWebSearchSettings(globalSettingsAdmin.webSearch));
  document.getElementById("testWebSearchBtn")?.addEventListener("click", () => globalSettingsAdmin.testWebSearch());
  document.getElementById("testWebSearchKeylessBtn")?.addEventListener("click", () => globalSettingsAdmin.testWebSearch());
});
