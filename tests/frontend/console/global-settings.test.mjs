import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(
  new URL(
    "../../../frontend/console/configuration/global-settings.js",
    import.meta.url,
  ),
  "utf8",
);
const indexHtml = await readFile(
  new URL("../../../frontend/console/index.html", import.meta.url),
  "utf8",
);
const templateHtml = await readFile(
  new URL(
    "../../../frontend/console/configuration-template.html",
    import.meta.url,
  ),
  "utf8",
);
const configurationCss = await readFile(
  new URL(
    "../../../frontend/console/configuration/configuration.css",
    import.meta.url,
  ),
  "utf8",
);

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function loadGlobalSettingsUi() {
  const window = {};
  const document = {
    addEventListener() {},
    getElementById() {
      return null;
    },
  };
  vm.runInContext(
    source,
    vm.createContext({
      apiFetch: async () => ({ groups: [] }),
      document,
      escapeHtml,
      window,
    }),
  );
  return window.__GlobalSettingsUI;
}

test("Agent 是配置中心默认页且全局配置位于系统设置分组", () => {
  assert.match(
    indexHtml,
    /class="tab resource-btn active" data-tab="agents"/,
  );
  assert.match(templateHtml, /class="panel active" id="panel-agents"/);
  assert.doesNotMatch(
    indexHtml,
    /class="tab resource-btn active" data-tab="global-settings"/,
  );
  assert.doesNotMatch(
    templateHtml,
    /class="panel active global-settings-panel" id="panel-global-settings"/,
  );
  assert.doesNotMatch(templateHtml, /data-global-settings-view|global-settings-nav/);
  assert.match(templateHtml, /class="global-settings-stack"/);
  assert.match(
    templateHtml,
    /id="globalSettingsWebSearch"[\s\S]*?data-lucide="globe-2"/,
  );
  assert.match(templateHtml, /id="globalSettingsRuntime"/);
  assert.ok(
    templateHtml.indexOf('id="globalSettingsWebSearch"') <
      templateHtml.indexOf('id="globalSettingsRuntime"'),
  );
  assert.match(templateHtml, /管理 DeepAgent 的系统级业务配置和运行设置/);
  assert.doesNotMatch(templateHtml, /当前页面只读|只读标签/);
  assert.doesNotMatch(templateHtml, /agentMaxRequests|max_requests|最大请求次数（可选）/);
  assert.doesNotMatch(indexHtml, /data-tab="auxiliary-models"|辅助模型|辅助工具/);
  assert.ok(
    indexHtml.indexOf("系统设置") <
      indexHtml.indexOf('data-tab="global-settings"'),
  );
  assert.ok(
    indexHtml.indexOf('data-tab="global-settings"') <
      indexHtml.indexOf('data-tab="database-connection"'),
  );
});

test("Web Search 配置采用两个显式模式并列出原生 Provider", () => {
  assert.match(templateHtml, /value="native" checked/);
  assert.match(templateHtml, /value="provider"/);
  assert.match(templateHtml, /DeepSeek、OpenAI、Anthropic Claude、Google Gemini 和 OpenRouter/);
  assert.doesNotMatch(templateHtml, /Kimi 请选择|当前模型搜索|无需额外配置/);
  assert.doesNotMatch(templateHtml, /Web Fetch/);
  assert.match(templateHtml, /id="testWebSearchBtn"/);
  assert.match(templateHtml, /id="saveWebSearchBtn"/);
  assert.match(templateHtml, /id="openWebSearchSettingsBtn"/);
  assert.match(
    templateHtml,
    /<dialog[^>]*id="webSearchSettingsDialog"[\s\S]*?<form[^>]*id="webSearchSettingsForm"/,
  );
  assert.match(
    templateHtml,
    /<\/section>\s*<\/div>\s*<dialog[^>]*id="webSearchSettingsDialog"/,
    "Web Search 弹窗应位于 admin-app 外并由配置加载器挂载到 body",
  );
  assert.match(source, /openWebSearchSettingsBtn[\s\S]*openWebSearchSettings/);
  assert.match(source, /webSearchSettingsDialog[\s\S]*showModal\(\)/);
  assert.match(source, /this\.webSearch = saved;[\s\S]*webSearchSettingsDialog"\)\?\.close\(\)/);
  assert.match(templateHtml, /class="console-select-trigger" id="webSearchProviderTrigger"/);
  assert.match(templateHtml, /id="webSearchModelToggle"/);
  assert.match(templateHtml, /name="webSearchCredentialSource" value="llm_provider"/);
  assert.match(templateHtml, /name="webSearchCredentialSource" value="api_key"/);
  assert.match(templateHtml, /id="webSearchLlmProvider"/);
  assert.match(templateHtml, /<legend>搜索模式<\/legend>/);
  assert.doesNotMatch(templateHtml, /id="webSearchApiKeyHint"|输入 DeepSeek 的 API Key/);
  assert.doesNotMatch(templateHtml, /class="web-search-mode-detail/);
  assert.match(templateHtml, /web-search-mode-dot/);
  assert.match(templateHtml, /<strong>会话原生搜索<\/strong><small>使用当前会话模型的原生搜索能力。<\/small>/);
  assert.match(templateHtml, /<small>使用固定搜索服务。<\/small>/);
  assert.match(templateHtml, /沿用 Provider/);
  assert.match(templateHtml, /单独 API Key/);
  assert.match(templateHtml, /class="web-search-credential-source"[\s\S]*?<strong>凭据<\/strong>/);
  assert.doesNotMatch(templateHtml, /<span>会话 Provider<\/span>/);
  assert.match(configurationCss, /\.web-search-mode-card\s*\{[\s\S]*?min-height: 64px/);
  assert.match(configurationCss, /\.web-search-provider-grid\s*\{[\s\S]*?grid-template-columns: repeat\(2,/);
  assert.match(configurationCss, /\.web-search-credential-row\s*\{[\s\S]*?grid-template-columns: minmax\(0, 1fr\)/);
  assert.match(configurationCss, /\.web-search-credential-source\s*\{[\s\S]*?display: flex;[\s\S]*?justify-content: space-between/);
  assert.match(configurationCss, /\.web-search-credential-options\s*\{[\s\S]*?width: max-content;[\s\S]*?grid-template-columns: repeat\(2, max-content\)/);
  assert.match(configurationCss, /dialog\.web-search-settings-dialog\.console-detail-dialog\.modal\s*\{[\s\S]*?width: min\(520px, calc\(100vw - 28px\)\);[\s\S]*?height: min\(460px, calc\(100vh - 28px\)\)/);
  assert.match(configurationCss, /\.web-search-provider-grid > \.field\s*\{[\s\S]*?display: grid;[\s\S]*?grid-template-rows: 18px 36px/);
  assert.match(configurationCss, /\.console-select-trigger\s*\{[\s\S]*?border-radius: 8px/);
  assert.match(configurationCss, /\.console-select-menu\s*\{[\s\S]*?box-shadow:/);
  assert.doesNotMatch(configurationCss, /\.web-search-provider-fields\s*\{[^}]*\b(?:border|background|padding)\s*:/);
});

test("Web Search 摘要只回显当前生效的一个 Provider 选择", () => {
  const ui = loadGlobalSettingsUi();
  assert.equal(ui.webSearchSummaryLabel({ value: { mode: "native" } }), "会话原生搜索");
  assert.equal(
    ui.webSearchSummaryLabel({ value: { mode: "provider", provider: "tavily" } }),
    "Tavily",
  );
  assert.match(templateHtml, /id="webSearchSummaryValue">会话原生搜索/);
  const webSearchSection = templateHtml.match(/<section class="global-settings-section" id="globalSettingsWebSearch">[\s\S]*?<\/section>/)?.[0] || "";
  assert.match(webSearchSection, /Web Search[\s\S]*?配置 Agent 的搜索 Provider。[\s\S]*?id="webSearchSummaryValue"[\s\S]*?openWebSearchSettingsBtn/);
  assert.doesNotMatch(webSearchSection, /global-settings-summary-row|<p|当前支持|Kimi/);
  const runtimeSection = templateHtml.match(/<section class="global-settings-section" id="globalSettingsRuntime">[\s\S]*?<\/section>/)?.[0] || "";
  assert.doesNotMatch(runtimeSection, /数据库连接、密钥|查看当前 DeepAgent 进程/);
  assert.doesNotMatch(templateHtml, /配置状态|配置有效|跟随 Agent 当前模型/);
});

test("不同搜索 Provider 只生成必要配置", () => {
  const ui = loadGlobalSettingsUi();
  assert.deepEqual(
    JSON.parse(JSON.stringify(ui.buildPayload({ mode: "native" }))),
    { version: 1, value: { mode: "native" } },
  );
  assert.deepEqual(
    JSON.parse(JSON.stringify(ui.buildPayload({ mode: "provider", provider: "deepseek", model: "", apiKey: "secret", credentialSource: "api_key" }))),
    { version: 1, value: { mode: "provider", provider: "deepseek", model: "deepseek-v4-flash", credential_source: "api_key", api_key: "secret" } },
  );
  assert.deepEqual(
    JSON.parse(JSON.stringify(ui.buildPayload({ mode: "provider", provider: "kimi", model: "kimi-k3", credentialSource: "llm_provider", llmProviderId: "provider-1" }))),
    { version: 1, value: { mode: "provider", provider: "kimi", model: "kimi-k3", credential_source: "llm_provider", llm_provider_id: "provider-1" } },
  );
  assert.deepEqual(
    JSON.parse(JSON.stringify(ui.buildPayload({ mode: "provider", provider: "tavily", model: "ignored", apiKey: "key" }))),
    { version: 1, value: { mode: "provider", provider: "tavily", api_key: "key" } },
  );
  assert.deepEqual(
    JSON.parse(JSON.stringify(ui.buildPayload({ mode: "provider", provider: "duckduckgo", model: "ignored", apiKey: "ignored" }))),
    { version: 1, value: { mode: "provider", provider: "duckduckgo" } },
  );
  assert.equal(ui.providerState("kimi").requiresModel, true);
  assert.equal(ui.providerState("exa").requiresModel, false);
  assert.equal(ui.providerState("duckduckgo").requiresKey, false);
});

test("全局配置列表展示值、单位、环境变量和来源", () => {
  const ui = loadGlobalSettingsUi();
  const html = ui.tableHtml([{
    label: "基础运行",
    items: [
      {
        label: "最大模型请求次数",
        env_name: "DEEP_AGENT_MAX_REQUESTS",
        display_value: "30",
        source: "environment",
        unit: "次/轮",
        description: "用于阻止失控循环。",
      },
    ],
  }]);

  assert.match(html, /最大模型请求次数/);
  assert.match(html, /30 次\/轮/);
  assert.match(html, /DEEP_AGENT_MAX_REQUESTS/);
  assert.match(html, /环境配置/);
  assert.doesNotMatch(html, /input|textarea|保存/);
});

test("全局配置渲染会转义服务端文本并格式化空值", () => {
  const ui = loadGlobalSettingsUi();
  const html = ui.tableHtml([{
    label: "<script>",
    items: [
      {
        label: "<b>配置</b>",
        env_name: "SAFE_ENV",
        display_value: "未限制",
        source: "default",
        unit: null,
        description: "<img src=x>",
      },
    ],
  }]);

  assert.doesNotMatch(html, /<script>|<b>|<img/);
  assert.match(html, /&lt;script&gt;/);
  assert.match(html, /代码默认值/);
  assert.equal(ui.settingValue({ display_value: null }), "—");
  assert.equal(
    ui.settingValue({
      value: null,
      display_value: "未限制",
      unit: "Token/轮",
    }),
    "未限制",
  );
});

test("全局配置使用单表分组行并支持大小写不敏感搜索", () => {
  const ui = loadGlobalSettingsUi();
  const groups = [
    { label: "MCP 运行", items: [{ label: "初始化超时", env_name: "DEEP_AGENT_MCP_INIT_TIMEOUT", description: "连接等待", display_value: "30", source: "default" }] },
    { label: "基础运行", items: [{ label: "应用名称", env_name: "DEEP_AGENT_APP_NAME", description: "服务标识", display_value: "deep-agent", source: "environment" }] },
  ];
  const allHtml = ui.tableHtml(groups);
  assert.equal((allHtml.match(/<thead>/g) || []).length, 1);
  assert.equal((allHtml.match(/global-settings-group-row/g) || []).length, 2);
  const filtered = ui.tableHtml(groups, "mcp_init");
  assert.match(filtered, /DEEP_AGENT_MCP_INIT_TIMEOUT/);
  assert.doesNotMatch(filtered, /DEEP_AGENT_APP_NAME/);
  assert.equal(ui.matches(groups[0], groups[0].items[0], "初始化"), true);
  assert.match(templateHtml, /id="globalSettingsSearch"/);
  assert.doesNotMatch(templateHtml, /id="refreshGlobalSettingsBtn"/);
});
