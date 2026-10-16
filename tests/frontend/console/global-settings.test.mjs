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
  const toasts = [];
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
        return ["categoryRadio", "credentialRadio", "fetchRadio", "webSearchApiKey", "webSearchLlmProvider", "webSearchProviderTrigger", "webSearchLlmProviderTrigger", "openMenuOption"].map(node);
      },
    },
    apiFetch: (path, options) => new Promise((resolve, reject) => requests.push({ path, options, resolve, reject })),
    showToast: (...args) => toasts.push(args),
  });
  vm.runInContext(source, context);
  vm.runInContext(`
    selectedWebSearchCategory = () => "deepseek";
    currentWebSearchDraft = () => ({ version: 1, value: { mode: "provider", provider: "deepseek" } });
    renderConsoleSelect = () => {};
    closeWebSearchMenus = () => {};
    renderWebSearchSettings = () => setWebSearchTestFeedback();
  `, context);
  return { context, node, requests, toasts, admin: context.window.globalSettingsAdmin };
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
  const controls = ["categoryRadio", "credentialRadio", "fetchRadio", "webSearchApiKey", "webSearchLlmProvider", "webSearchProviderTrigger", "webSearchLlmProviderTrigger", "openMenuOption"];
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

test("Agent 是配置中心默认页，全局配置整合三个二级目录", () => {
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
  assert.match(templateHtml, /class="global-settings-nav"/);
  for (const page of ["general", "models", "operations"]) {
    assert.ok(templateHtml.includes(`data-settings-page="${page}"`));
    assert.ok(templateHtml.includes(`id="settings-${page}"`));
  }
  assert.match(templateHtml, /class="global-settings-stack"/);
  assert.match(
    templateHtml,
    /id="globalSettingsWebSearch"[\s\S]*?<h3>网络搜索<\/h3>/,
  );
  assert.match(templateHtml, /id="globalSettingsRuntime"/);
  assert.ok(
    templateHtml.indexOf('id="globalSettingsWebSearch"') <
      templateHtml.indexOf('id="globalSettingsRuntime"'),
  );
  assert.doesNotMatch(templateHtml, /当前页面只读|只读标签/);
  assert.doesNotMatch(templateHtml, /agentMaxRequests|max_requests|最大请求次数（可选）/);
  assert.doesNotMatch(indexHtml, /data-tab="auxiliary-models"|辅助模型|辅助工具/);
  assert.match(indexHtml, /class="console-nav-footer">\s*<div class="resource-group-label">系统<\/div>[\s\S]*data-tab="global-settings"/);
  assert.doesNotMatch(indexHtml, /data-tab="catalog"|href="\.\.\/docs"/);
  assert.match(templateHtml, /href="\.\.\/docs"[^>]*target="_blank"/);
  assert.doesNotMatch(indexHtml, /data-tab="database-connection"/);
});

test("网络搜索保留原切换样式，抓取使用左对齐Radio且不再配置模型", () => {
  const radios = templateHtml.match(/<input[^>]*name="webSearchCategory"[^>]*>/g);
  assert.equal(radios.length, 4);
  assert.ok(radios.every(radio => !radio.includes("checked")));
  for (const category of ["deepseek", "kimi", "third-party", "unconfigured"]) assert.ok(radios.some(radio => radio.includes(`value="${category}"`)));
  assert.match(templateHtml, /id="webSearchUnconfigured"/);
  assert.match(templateHtml, /id="saveWebSearchBtn"[^>]*disabled/);
  const dialog = templateHtml.match(/<dialog[^>]*id="webSearchSettingsDialog"[\s\S]*?<\/dialog>/)[0];
  assert.doesNotMatch(dialog, /webSearchModel|搜索模型|测试抓取/);
  assert.match(templateHtml, /<h3 id="webSearchSettingsTitle">网络搜索<\/h3>/);
  const fetchOptions = templateHtml.match(/name="webFetchMode" value="[^"]+"/g);
  assert.deepEqual(fetchOptions, ['name="webFetchMode" value="follow_search"', 'name="webFetchMode" value="system"']);
  assert.match(templateHtml, /class="web-fetch-options"/);
  assert.doesNotMatch(templateHtml, /高级设置|第三方搜索/);
  assert.match(templateHtml, /name="webSearchCredentialSource" value="llm_provider"/);
  assert.match(templateHtml, /name="webSearchCredentialSource" value="api_key"/);
  assert.doesNotMatch(templateHtml, /webSearchTestModel|会话模型原生搜索|name="webSearchMode"/);
  assert.doesNotMatch(source, /test-model|webSearchTestModel|renderWebSearchTestModels/);
  assert.equal((templateHtml.match(/class="web-search-mode-option"/g) || []).length, 3);
  assert.equal((dialog.match(/class="web-fetch-option"/g) || []).length, 2);
  assert.match(dialog, /搜索服务 <span class="web-fetch-caption">Web Search/);
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

test("网络搜索摘要仅显示搜索供应商", () => {
  const ui = loadGlobalSettingsUi();
  for (const settings of [null, {}, { value: {} }, { value: { mode: "native" } }, { value: { mode: "unconfigured" } }]) assert.equal(ui.webSearchSummaryLabel(settings), "未配置");
  assert.equal(
    ui.webSearchSummaryLabel({ value: { mode: "provider", provider: "tavily" } }),
    "Tavily",
  );
  assert.equal(ui.webSearchSummaryLabel({ value: { mode: "provider", provider: "kimi", configuration_error: "供应商已删除" } }), "配置不可用");
  assert.match(templateHtml, /id="webSearchSummaryValue">未配置/);
  const webSearchSection = templateHtml.match(/<section class="global-settings-section" id="globalSettingsWebSearch">[\s\S]*?<\/section>/)?.[0] || "";
  assert.match(webSearchSection, /网络搜索[\s\S]*?配置 Web Search 搜索及 Web Fetch 网页抓取。[\s\S]*?id="webSearchSummaryValue"[\s\S]*?openWebSearchSettingsBtn/);
  assert.doesNotMatch(webSearchSection, /global-settings-summary-row|<p|当前支持|Kimi/);
  const runtimeSection = templateHtml.match(/<section class="global-settings-section" id="globalSettingsRuntime">[\s\S]*?<\/section>/)?.[0] || "";
  assert.doesNotMatch(runtimeSection, /数据库连接、密钥|查看当前 DeepAgent 进程/);
  assert.doesNotMatch(templateHtml, /配置状态|配置有效|跟随 Agent 当前模型/);
});

test("不同搜索供应商只生成必要配置", () => {
  const ui = loadGlobalSettingsUi();
  assert.throws(() => ui.buildPayload({ mode: "native" }), /请选择搜索供应商/);
  assert.deepEqual(JSON.parse(JSON.stringify(ui.buildPayload({ mode: "unconfigured", provider: "kimi", apiKey: "discard", llmProviderId: "discard" }))), { version: 1, value: { mode: "unconfigured", fetch_mode: "system" } });
  assert.deepEqual(
    JSON.parse(JSON.stringify(ui.buildPayload({ mode: "provider", provider: "deepseek", model: "", apiKey: "secret", credentialSource: "api_key" }))),
    { version: 1, value: { mode: "provider", provider: "deepseek", fetch_mode: "system", credential_source: "api_key", api_key: "secret" } },
  );
  assert.deepEqual(
    JSON.parse(JSON.stringify(ui.buildPayload({ mode: "provider", provider: "kimi", model: "kimi-k3", credentialSource: "llm_provider", llmProviderId: "provider-1" }))),
    { version: 1, value: { mode: "provider", provider: "kimi", fetch_mode: "system", credential_source: "llm_provider", llm_provider_id: "provider-1" } },
  );
  assert.deepEqual(
    JSON.parse(JSON.stringify(ui.buildPayload({ mode: "provider", provider: "tavily", model: "ignored", apiKey: "key" }))),
    { version: 1, value: { mode: "provider", provider: "tavily", fetch_mode: "system", api_key: "key" } },
  );
  assert.deepEqual(
    JSON.parse(JSON.stringify(ui.buildPayload({ mode: "provider", provider: "duckduckgo", model: "ignored", apiKey: "ignored" }))),
    { version: 1, value: { mode: "provider", provider: "duckduckgo", fetch_mode: "system" } },
  );
  assert.equal(ui.providerState("deepseek").requiresModel, undefined);
  assert.equal(ui.providerState("duckduckgo").requiresKey, false);
});

test("历史模型不再生成配置，失效 Provider 不静默换绑", () => {
  const nodes = new Map();
  const node = id => { if (!nodes.has(id)) nodes.set(id, { value: "", innerHTML: "" }); return nodes.get(id); };
  const context = vm.createContext({
    window: {}, escapeHtml,
    state: { llmProviders: [{ id: "new-provider", name: "新的 DeepSeek", provider_type: "deepseek", api_key_set: true }] },
    document: { addEventListener() {}, getElementById: node },
  });
  vm.runInContext(source, context);
  vm.runInContext('renderConsoleSelect = () => {}; renderWebSearchLlmProviderOptions("deepseek", "deleted-provider");', context);
  const payload = context.window.__GlobalSettingsUI.buildPayload({ mode: "provider", provider: "deepseek", model: "proxy-custom-model" });
  assert.equal(Object.hasOwn(payload.value, "model"), false);
  assert.equal(node("webSearchLlmProvider").value, "deleted-provider");
  assert.match(node("webSearchLlmProvider").innerHTML, /供应商已不可用/);
});

test("全局配置列表展示值、单位、环境变量和来源", () => {
  const ui = loadGlobalSettingsUi();
  const html = ui.tableHtml({
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
  });

  assert.match(html, /最大模型请求次数/);
  assert.match(html, /30 次\/轮/);
  assert.match(html, /DEEP_AGENT_MAX_REQUESTS/);
  assert.match(html, /环境配置/);
  assert.doesNotMatch(html, /input|textarea|保存/);
});

test("全局配置渲染会转义服务端文本并格式化空值", () => {
  const ui = loadGlobalSettingsUi();
  const html = ui.tableHtml({
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
  });

  assert.doesNotMatch(html, /<script>|<b>|<img/);
  assert.doesNotMatch(html, /<script>/);
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

test("全局配置按精简分组横向切换并支持跨组搜索", () => {
  const ui = loadGlobalSettingsUi();
  const groups = [
    { key: "mcp", label: "MCP 连接", items: [{ label: "初始化超时", env_name: "DEEP_AGENT_MCP_INIT_TIMEOUT", description: "连接等待", display_value: "30", source: "default" }] },
    { key: "runtime", label: "对话运行", items: [{ label: "最大模型请求次数", env_name: "DEEP_AGENT_MAX_REQUESTS", description: "单轮上限", display_value: "40", source: "environment" }] },
  ];
  const allHtml = ui.tableHtml(groups[0]);
  assert.equal((allHtml.match(/<thead>/g) || []).length, 1);
  assert.doesNotMatch(allHtml, /DEEP_AGENT_MAX_REQUESTS/);
  const visible = ui.visibleGroups(groups, "mcp_init");
  assert.equal(visible.length, 1);
  assert.match(ui.tableHtml(visible[0]), /DEEP_AGENT_MCP_INIT_TIMEOUT/);
  const switches = ui.switchHtml(groups, "mcp");
  assert.match(switches, /data-global-settings-group="mcp" aria-pressed="true"/);
  assert.equal(ui.matches(groups[0], groups[0].items[0], "初始化"), true);
  assert.match(templateHtml, /id="globalSettingsSearch"/);
  assert.match(templateHtml, /id="globalSettingsGroupSwitch"/);
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
  assert.equal(input.type, "password");
  vm.runInContext('resetWebSearchApiKey("exa")', context);
  assert.equal(input.value, "");
  assert.equal(input.dataset.maskedValue, "");
  assert.equal(input.type, "password");
  vm.runInContext('resetWebSearchApiKey("tavily")', context);
  assert.equal(input.value, "tvly-abc********xyz");
  vm.runInContext('globalSettingsAdmin.webSearch.value.credential_source = "llm_provider"; resetWebSearchApiKey("tavily")', context);
  assert.equal(input.value, "");
});

function webSearchDraftHarness(settings) {
  const nodes = new Map();
  const radios = {};
  const node = id => {
    if (!nodes.has(id)) {
      const classes = new Set();
      nodes.set(id, { value: "", innerHTML: "", textContent: "", dataset: {}, disabled: false, open: false,
        classList: { toggle(name, force) { const add = force ?? !classes.has(name); if (add) classes.add(name); else classes.delete(name); }, contains: name => classes.has(name) },
        showModal() { this.open = true; }, close() { this.open = false; } });
    }
    return nodes.get(id);
  };
  for (const [group, values] of Object.entries({ webSearchCategory: ["deepseek", "kimi", "third-party", "unconfigured"], webSearchCredentialSource: ["llm_provider", "api_key"], webFetchMode: ["follow_search", "system"] })) {
    radios[group] = values.map(value => {
      const input = node(`${group}-${value}`);
      input.value = value;
      let checked = false;
      Object.defineProperty(input, "checked", { get: () => checked, set(value) {
        if (value) for (const other of radios[group] || []) if (other !== input) other.checked = false;
        checked = value;
      } });
      return input;
    });
  }
  const document = {
    addEventListener() {}, getElementById: node,
    querySelectorAll(selector) {
      const group = selector.match(/^input\[name="([^"]+)"\]$/)?.[1];
      return group ? radios[group] : [];
    },
    querySelector(selector) {
      const match = selector.match(/^input\[name="([^"]+)"\](?::checked|\[value="([^"]+)"\])$/);
      if (match) return radios[match[1]].find(input => match[2] ? input.value === match[2] : input.checked) || null;
      return node(selector);
    },
  };
  const context = vm.createContext({ window: {}, document, escapeHtml, state: { llmProviders: [] } });
  vm.runInContext(source, context);
  vm.runInContext('renderConsoleSelect = () => {};', context);
  context.window.globalSettingsAdmin.webSearch = settings;
  return { node, radios, context, run: code => vm.runInContext(code, context) };
}

test("历史配置默认系统内置，取消及重开恢复服务端抓取选择", () => {
  const h = webSearchDraftHarness({ value: { mode: "provider", provider: "deepseek", model: "historical-model" } });
  h.run("openWebSearchSettings()");
  assert.equal(h.run("currentWebSearchDraft().value.fetch_mode"), "system");
  assert.equal(h.run('Object.hasOwn(currentWebSearchDraft().value, "model")'), false);
  h.radios.webFetchMode[0].checked = true;
  h.run("updateWebFetchMode()");
  assert.equal(h.run("currentWebSearchDraft().value.fetch_mode"), "follow_search");
  h.run("closeWebSearchSettings(); openWebSearchSettings()");
  assert.equal(h.run("currentWebSearchDraft().value.fetch_mode"), "system");
  h.context.window.globalSettingsAdmin.webSearch.value.fetch_mode = "follow_search";
  h.run("renderWebSearchSettings(globalSettingsAdmin.webSearch)");
  assert.equal(h.run("currentWebSearchDraft().value.fetch_mode"), "follow_search");
});

test("跟随仅支持两家，切换搜索服务商和未配置时收敛为系统内置", () => {
  const h = webSearchDraftHarness({ value: { mode: "provider", provider: "kimi", fetch_mode: "follow_search" } });
  h.run("openWebSearchSettings()");
  assert.equal(h.node("webFetchFollowOption").classList.contains("hidden"), false);
  assert.match(h.node("webFetchDescription").textContent, /Kimi/);
  h.radios.webSearchCategory[2].checked = true;
  h.node("webSearchProvider").value = "exa";
  h.run("updateWebSearchCategory({ resetCredentialSource: true })");
  assert.equal(h.run("currentWebSearchDraft().value.fetch_mode"), "system");
  assert.equal(h.node("webFetchFollowOption").classList.contains("hidden"), true);
  h.radios.webSearchCategory[1].checked = true;
  h.run("updateWebSearchCategory({ resetCredentialSource: true })");
  assert.equal(h.run("currentWebSearchDraft().value.fetch_mode"), "system");
  h.radios.webFetchMode[0].checked = true;
  h.radios.webSearchCategory[3].checked = true;
  h.run("updateWebSearchCategory()");
  assert.equal(h.run("currentWebSearchDraft().value.fetch_mode"), "system");
  assert.equal(h.node("webFetchFollowOption").classList.contains("hidden"), true);
});

test("两家跟随配置不复制凭据，其他供应商不能生成跟随配置", () => {
  const ui = loadGlobalSettingsUi();
  for (const provider of Object.keys(ui.providers)) {
    const payload = ui.buildPayload({ mode: "provider", provider, fetchMode: "follow_search", credentialSource: "llm_provider", llmProviderId: "selected-provider" });
    const supported = ["deepseek", "kimi"].includes(provider);
    assert.equal(payload.value.fetch_mode, supported ? "follow_search" : "system");
    assert.equal(Object.hasOwn(payload.value, "model"), false);
    if (supported) {
      assert.equal(payload.value.llm_provider_id, "selected-provider");
      assert.equal(Object.hasOwn(payload.value, "api_key"), false);
    }
  }
  assert.equal(ui.webSearchSummaryLabel({ value: { mode: "provider", provider: "kimi", fetch_mode: "follow_search" } }), "Kimi");
  assert.equal(ui.webSearchSummaryLabel({ value: { mode: "provider", provider: "exa", fetch_mode: "follow_search" } }), "Exa");
});

test("保存网络配置后回读权威状态，回读失败不误报保存失败", async () => {
  const h = webSearchOperationHarness();
  const pending = h.admin.saveWebSearch();
  h.requests[0].resolve({ value: { mode: "provider", provider: "deepseek", fetch_mode: "follow_search" } });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.requests.length, 2);
  assert.equal(h.requests[1].options, undefined);
  h.requests[1].resolve({ value: { mode: "provider", provider: "deepseek", fetch_mode: "system" } });
  await pending;
  assert.equal(h.admin.webSearch.value.fetch_mode, "system");
  const retry = h.admin.saveWebSearch();
  h.requests[2].resolve({ value: { mode: "provider", provider: "deepseek", fetch_mode: "follow_search" } });
  await new Promise(resolve => setImmediate(resolve));
  h.requests[3].reject(new Error("网络断开"));
  await retry;
  assert.equal(h.admin.webSearch.value.fetch_mode, "follow_search");
  assert.match(h.toasts.at(-1)[0], /已保存，刷新失败：网络断开/);
});
