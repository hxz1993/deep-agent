import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(
  new URL(
    "../../../console/configuration/global-settings.js",
    import.meta.url,
  ),
  "utf8",
);
const indexHtml = await readFile(
  new URL("../../../console/index.html", import.meta.url),
  "utf8",
);
const templateHtml = await readFile(
  new URL(
    "../../../console/configuration-template.html",
    import.meta.url,
  ),
  "utf8",
);
const configurationCss = await readFile(
  new URL(
    "../../../console/configuration/configuration.css",
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

function webSearchOperationHarness() {
  const nodes = new Map();
  const requests = [];
  const node = (id) => {
    if (!nodes.has(id)) nodes.set(id, {
      value: "", disabled: false, open: true, dataset: {},
      classList: { toggle() {} }, close() { this.open = false; },
    });
    return nodes.get(id);
  };
  const context = vm.createContext({
    window: {}, AbortController, escapeHtml,
    document: {
      addEventListener() {}, getElementById: node,
      querySelectorAll(selector) {
        assert.match(selector, /input.*select.*console-select-trigger.*console-select-option/);
        return ["categoryRadio", "credentialRadio", "webSearchApiKey", "webSearchModel", "webSearchLlmProvider", "webSearchProviderTrigger", "webSearchModelTrigger", "webSearchLlmProviderTrigger", "openMenuOption"].map(node);
      },
    },
    apiFetch: (path, options) => new Promise((resolve, reject) => requests.push({ path, options, resolve, reject })),
    showToast() {},
  });
  vm.runInContext(source, context);
  vm.runInContext(`
    selectedWebSearchCategory = () => "deepseek";
    currentWebSearchDraft = () => ({ version: 1, value: { mode: "provider", provider: "deepseek" } });
    renderConsoleSelect = () => {};
    closeWebSearchMenus = () => {};
    renderWebSearchSettings = () => setWebSearchTestFeedback();
  `, context);
  return { context, node, requests, admin: context.window.globalSettingsAdmin };
}

test("搜索测试和保存均拒绝重复调用", async () => {
  const h = webSearchOperationHarness();
  const pending = h.admin.testWebSearch();
  assert.equal(h.node("testWebSearchBtn").disabled, true);
  await h.admin.testWebSearch();
  await h.admin.saveWebSearch();
  assert.equal(h.requests.length, 1);
  h.requests[0].resolve({ ok: true, message: "搜索成功" });
  await pending;
  assert.equal(h.node("testWebSearchBtn").disabled, false);
});

test("测试和保存期间锁定草稿控件，成功或失败后恢复编辑", async () => {
  const h = webSearchOperationHarness();
  const controls = ["categoryRadio", "credentialRadio", "webSearchApiKey", "webSearchModel", "webSearchLlmProvider", "webSearchProviderTrigger", "webSearchModelTrigger", "webSearchLlmProviderTrigger", "openMenuOption"];
  const testing = h.admin.testWebSearch();
  controls.forEach(id => assert.equal(h.node(id).disabled, true, id));
  h.requests[0].resolve({ ok: true, message: "搜索成功" });
  await testing;
  controls.forEach(id => assert.equal(h.node(id).disabled, false, id));
  const saving = h.admin.saveWebSearch();
  controls.forEach(id => assert.equal(h.node(id).disabled, true, id));
  h.requests[1].reject(new Error("保存失败"));
  await saving;
  controls.forEach(id => assert.equal(h.node(id).disabled, false, id));
});

test("关闭重开后的旧测试结果和 finally 不得覆盖或解锁新测试", async () => {
  const h = webSearchOperationHarness();
  const old = h.admin.testWebSearch();
  vm.runInContext("closeWebSearchSettings()", h.context);
  assert.equal(h.requests[0].options.signal.aborted, true);
  h.node("webSearchSettingsDialog").open = true;
  const next = h.admin.testWebSearch();
  h.requests[0].resolve({ ok: true, message: "旧结果" });
  await old;
  assert.equal(h.node("webSearchTestFeedback").dataset.status, "testing");
  assert.equal(h.node("testWebSearchBtn").disabled, true);
  assert.equal(h.node("saveWebSearchBtn").disabled, true);
  h.requests[1].resolve({ ok: true, message: "新结果" });
  await next;
  assert.equal(h.node("webSearchTestFeedbackText").textContent, "新结果");
  assert.equal(h.node("saveWebSearchBtn").disabled, false);
});

test("保存期间阻止关闭和重复提交，失败后保留编辑器并解锁", async () => {
  const h = webSearchOperationHarness();
  const pending = h.admin.saveWebSearch();
  vm.runInContext("closeWebSearchSettings()", h.context);
  await h.admin.saveWebSearch();
  await h.admin.testWebSearch();
  assert.equal(h.node("webSearchSettingsDialog").open, true);
  assert.equal(h.requests.length, 1);
  h.requests[0].reject(new Error("保存失败"));
  await pending;
  assert.equal(h.node("webSearchSettingsDialog").open, true);
  assert.equal(h.node("saveWebSearchBtn").disabled, false);
});

test("旧搜索配置失效不阻止展示运行设置和打开修复草稿", async () => {
  const summary = {}, window = {};
  const context = vm.createContext({
    window,
    document: { addEventListener() {}, getElementById: id => id === 'webSearchSummaryValue' ? summary : null },
    apiFetch: async path => {
      if (path.endsWith('/web-search')) throw Object.assign(new Error('旧凭据失效'), { status: 409 });
      return { groups: [{ label: '运行配置', items: [] }] };
    },
    loadLlmProviders: async () => {},
    showToast: () => assert.fail('可修复状态不应阻止整个配置页'),
  });
  vm.runInContext(source, context);
  context.renderGlobalSettings = () => {};
  context.renderWebSearchSettings = value => assert.equal(value, null);
  window.globalSettingsAdmin.webSearch = { value: { mode: 'provider', api_key_set: true } };
  await window.globalSettingsAdmin.refresh();
  assert.equal(window.globalSettingsAdmin.groups.length, 1);
  assert.equal(window.globalSettingsAdmin.webSearch, null);
  assert.equal(summary.textContent, '配置不可用，请重新配置');
});

test("Agent 是配置中心默认页，系统分组包含全局配置和模型目录", () => {
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
  assert.match(indexHtml, /class="console-nav-footer">\s*<div class="resource-group-label">系统<\/div>[\s\S]*data-tab="global-settings"/);
  assert.ok(
    indexHtml.indexOf('data-tab="global-settings"') <
      indexHtml.indexOf('data-tab="catalog"'),
  );
  assert.doesNotMatch(indexHtml, /data-tab="database-connection"/);
});

test("Web Search 三段切换及独立暂不配置选项，直接展示 DeepSeek 模型", () => {
  const radios = templateHtml.match(/<input[^>]*name="webSearchCategory"[^>]*>/g);
  assert.equal(radios.length, 4);
  assert.ok(radios.every(radio => !radio.includes("checked")));
  for (const category of ["deepseek", "kimi", "third-party", "unconfigured"]) assert.ok(radios.some(radio => radio.includes(`value="${category}"`)));
  assert.match(templateHtml, /id="webSearchUnconfigured"/);
  assert.match(templateHtml, /id="saveWebSearchBtn"[^>]*disabled/);
  assert.match(templateHtml, /<div class="field" data-web-search-field="model">/);
  assert.doesNotMatch(templateHtml, /高级设置|第三方搜索/);
  assert.match(templateHtml, /name="webSearchCredentialSource" value="llm_provider"/);
  assert.match(templateHtml, /name="webSearchCredentialSource" value="api_key"/);
  assert.doesNotMatch(templateHtml, /webSearchTestModel|会话模型原生搜索|name="webSearchMode"/);
  assert.doesNotMatch(source, /test-model|webSearchTestModel|renderWebSearchTestModels/);
  assert.equal((templateHtml.match(/class="web-search-mode-option"/g) || []).length, 3);
  assert.match(templateHtml, /class="web-search-unconfigured-option"[\s\S]*?暂不配置/);
  assert.match(templateHtml, /id="webSearchTestFeedback"[^>]*role="status"[^>]*aria-live="polite"/);
  assert.match(source, /webSearchSettingsDialog[\s\S]*showModal\(\)/);
});

test("Web Search 连接测试在当前弹框内展示简短反馈", () => {
  const ui = loadGlobalSettingsUi();
  assert.deepEqual(
    JSON.parse(JSON.stringify(ui.webSearchTestFeedbackPresentation("testing"))),
    { visible: true, status: "testing", message: "正在测试连接…" },
  );
  assert.deepEqual(
    JSON.parse(JSON.stringify(ui.webSearchTestFeedbackPresentation("success"))),
    { visible: true, status: "success", message: "连接成功" },
  );
  assert.deepEqual(
    JSON.parse(JSON.stringify(ui.webSearchTestFeedbackPresentation("error", "API Key 无效"))),
    { visible: true, status: "error", message: "API Key 无效" },
  );
  assert.deepEqual(
    JSON.parse(JSON.stringify(ui.webSearchTestFeedbackPresentation(""))),
    { visible: false, status: "", message: "" },
  );
  const testMethod = source.match(/async testWebSearch\(\) \{[\s\S]*?\n  \},/)?.[0] || "";
  assert.match(testMethod, /setWebSearchTestFeedback\("testing"\)/);
  assert.match(testMethod, /requestId !== webSearchTestRunId/);
  assert.match(testMethod, /setWebSearchTestFeedback\([\s\S]*?result\?\.ok \? "success" : "error"/);
  assert.doesNotMatch(testMethod, /showToast/);
});

test("Web Search 摘要只回显当前生效的一个供应商选择", () => {
  const ui = loadGlobalSettingsUi();
  for (const settings of [null, {}, { value: {} }, { value: { mode: "native" } }, { value: { mode: "unconfigured" } }]) assert.equal(ui.webSearchSummaryLabel(settings), "未配置");
  assert.equal(
    ui.webSearchSummaryLabel({ value: { mode: "provider", provider: "tavily" } }),
    "Tavily",
  );
  assert.equal(ui.webSearchSummaryLabel({ value: { mode: "provider", provider: "kimi", configuration_error: "供应商已删除" } }), "配置不可用");
  assert.match(templateHtml, /id="webSearchSummaryValue">未配置/);
  const webSearchSection = templateHtml.match(/<section class="global-settings-section" id="globalSettingsWebSearch">[\s\S]*?<\/section>/)?.[0] || "";
  assert.match(webSearchSection, /Web Search[\s\S]*?配置 Agent 的搜索供应商。[\s\S]*?id="webSearchSummaryValue"[\s\S]*?openWebSearchSettingsBtn/);
  assert.doesNotMatch(webSearchSection, /global-settings-summary-row|<p|当前支持|Kimi/);
  const runtimeSection = templateHtml.match(/<section class="global-settings-section" id="globalSettingsRuntime">[\s\S]*?<\/section>/)?.[0] || "";
  assert.doesNotMatch(runtimeSection, /数据库连接、密钥|查看当前 DeepAgent 进程/);
  assert.doesNotMatch(templateHtml, /配置状态|配置有效|跟随 Agent 当前模型/);
});

test("不同搜索供应商只生成必要配置", () => {
  const ui = loadGlobalSettingsUi();
  assert.throws(() => ui.buildPayload({ mode: "native" }), /请选择搜索供应商/);
  assert.deepEqual(JSON.parse(JSON.stringify(ui.buildPayload({ mode: "unconfigured", provider: "kimi", apiKey: "discard", llmProviderId: "discard" }))), { version: 1, value: { mode: "unconfigured" } });
  assert.deepEqual(
    JSON.parse(JSON.stringify(ui.buildPayload({ mode: "provider", provider: "deepseek", model: "", apiKey: "secret", credentialSource: "api_key" }))),
    { version: 1, value: { mode: "provider", provider: "deepseek", model: "deepseek-flash", credential_source: "api_key", api_key: "secret" } },
  );
  assert.deepEqual(
    JSON.parse(JSON.stringify(ui.buildPayload({ mode: "provider", provider: "kimi", model: "kimi-k3", credentialSource: "llm_provider", llmProviderId: "provider-1" }))),
    { version: 1, value: { mode: "provider", provider: "kimi", credential_source: "llm_provider", llm_provider_id: "provider-1" } },
  );
  assert.deepEqual(
    JSON.parse(JSON.stringify(ui.buildPayload({ mode: "provider", provider: "tavily", model: "ignored", apiKey: "key" }))),
    { version: 1, value: { mode: "provider", provider: "tavily", api_key: "key" } },
  );
  assert.deepEqual(
    JSON.parse(JSON.stringify(ui.buildPayload({ mode: "provider", provider: "duckduckgo", model: "ignored", apiKey: "ignored" }))),
    { version: 1, value: { mode: "provider", provider: "duckduckgo" } },
  );
  assert.equal(ui.providerState("kimi").requiresModel, false);
  assert.equal(ui.providerState("exa").requiresModel, false);
  assert.equal(ui.providerState("duckduckgo").requiresKey, false);
});

test("历史 DeepSeek 模型不被推荐列表覆盖，失效 Provider 不静默换绑", () => {
  const nodes = new Map();
  const node = id => { if (!nodes.has(id)) nodes.set(id, { value: "", innerHTML: "" }); return nodes.get(id); };
  const context = vm.createContext({
    window: {}, escapeHtml,
    state: { llmProviders: [{ id: "new-provider", name: "新的 DeepSeek", provider_type: "deepseek", api_key_set: true }] },
    document: { addEventListener() {}, getElementById: node },
  });
  vm.runInContext(source, context);
  vm.runInContext('renderConsoleSelect = () => {}; renderWebSearchModelMenu("deepseek-v4-flash");', context);
  assert.equal(node("webSearchModel").value, "deepseek-v4-flash");
  assert.match(node("webSearchModel").innerHTML, /deepseek-v4-flash/);
  vm.runInContext('renderWebSearchModelMenu("proxy-custom-model");', context);
  assert.equal(node("webSearchModel").value, "proxy-custom-model");
  vm.runInContext('renderWebSearchModelMenu(""); renderWebSearchLlmProviderOptions("deepseek", "deleted-provider");', context);
  assert.equal(node("webSearchModel").value, "deepseek-flash");
  assert.equal(node("webSearchLlmProvider").value, "deleted-provider");
  assert.match(node("webSearchLlmProvider").innerHTML, /供应商已不可用/);
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


test("脱敏密钥与留空不提交替换，新密钥才提交", () => {
  const ui = loadGlobalSettingsUi();
  for (const provider of ["deepseek", "kimi", "tavily", "exa", "you"]) {
    const base = { mode: "provider", provider, credentialSource: "api_key", maskedApiKey: "sk-abc********xyz" };
    for (const apiKey of [base.maskedApiKey, "", "   "]) {
      assert.equal(Object.hasOwn(ui.buildPayload({ ...base, apiKey }).value, "api_key"), false);
    }
    assert.equal(ui.buildPayload({ ...base, apiKey: "test-new-key" }).value.api_key, "test-new-key");
    assert.equal(Object.hasOwn(ui.buildPayload({ ...base, mode: "unconfigured", apiKey: "test-new-key" }).value, "api_key"), false);
  }
});

test("重新打开回显同供应商脱敏值，切换供应商不串用密钥", () => {
  const input = { dataset: {}, value: "", type: "password" };
  const context = vm.createContext({ window: {}, document: { addEventListener() {}, getElementById: () => input } });
  vm.runInContext(source, context);
  vm.runInContext('globalSettingsAdmin.webSearch = {value: {mode: "provider", provider: "tavily", api_key_masked: "tvly-abc********xyz"}}; resetWebSearchApiKey("tavily")', context);
  assert.equal(input.value, "tvly-abc********xyz");
  assert.equal(input.type, "text");
  vm.runInContext('resetWebSearchApiKey("exa")', context);
  assert.equal(input.value, "");
  assert.equal(input.dataset.maskedValue, "");
  assert.equal(input.type, "password");
  vm.runInContext('resetWebSearchApiKey("tavily")', context);
  assert.equal(input.value, "tvly-abc********xyz");
  vm.runInContext('globalSettingsAdmin.webSearch.value.credential_source = "llm_provider"; resetWebSearchApiKey("tavily")', context);
  assert.equal(input.value, "");
});
