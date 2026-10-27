import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const read = name => readFile(new URL(`../../../console/${name}`, import.meta.url), "utf8");
const [configuration, core, agents, globalSettings, database, catalog, contextSource] = await Promise.all([
  "configuration.js", "configuration/core.js", "configuration/agents.js", "configuration/global-settings.js",
  "configuration/database-connection.js", "configuration/catalog.js", "context.js",
].map(read));
const configurationTemplate = await read("configuration-template.html");
const tick = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const escapeHtml = value => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");

function node() {
  const classes = new Set();
  return { dataset: {}, textContent: "", innerHTML: "", value: "", hidden: false, disabled: false,
    classList: { add: key => classes.add(key), remove: key => classes.delete(key), contains: key => classes.has(key), toggle(key, on) { on ? classes.add(key) : classes.delete(key); } },
    setAttribute() {}, removeAttribute() {}, addEventListener(type, listener) { this[type] = listener; },
    append(...items) { this.children ||= []; this.children.push(...items); }, appendChild(item) { this.append(item); },
    replaceChildren(...items) { this.children = items; }, focus() {},
  };
}

function classic(source, overrides = {}) {
  const ready = [];
  const nodes = new Map();
  const get = id => { if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id); };
  const sandbox = vm.createContext({
    window: {}, document: { addEventListener(type, listener) { if (type === "DOMContentLoaded") ready.push(listener); }, getElementById: get, querySelectorAll: () => [] },
    escapeHtml, setTimeout, clearTimeout, URL, Date,
    ...overrides,
  });
  vm.runInContext(source, sandbox);
  return { sandbox, ready, get, nodes, window: sandbox.window };
}

function moduleSource(source) {
  return source.replace(/^import [^\n]+;\n/gm, "").replaceAll("export ", "")
    .replaceAll("import.meta.url", '"http://test/proxy/console/configuration.js"')
    .replace(/import\(([^)]+)\)/g, "testImport($1)");
}

test("配置 classic 脚本一起发起下载，按插入顺序执行，完成后只初始化一次", async () => {
  const scripts = [];
  const events = [];
  const sandbox = vm.createContext({
    window: {}, JsonInput: {}, OutputSchema: {}, ProviderCatalog: {}, SkillFrontmatter: {}, SchemaText: {}, URL,
    Event: class { constructor(type) { this.type = type; } }, testImport: async () => ({}),
    document: { createElement: () => ({}), body: { appendChild: script => scripts.push(script) }, dispatchEvent: event => events.push(event.type) },
  });
  vm.runInContext(moduleSource(configuration), sandbox);
  const loading = sandbox.loadAdminScripts();
  assert.equal(scripts.length, 23, "下载不能等前一个 onload 才开始");
  assert.ok(scripts.every(script => script.async === false));
  assert.match(scripts[0].src, /\/proxy\/console\/configuration\/core\.js\?v=/);
  assert.match(scripts.at(-1).src, /integrations\.js\?v=/);
  assert.deepEqual(events, []);
  for (const script of scripts) script.onload();
  await loading;
  assert.deepEqual(events, ["DOMContentLoaded"]);
});

function configurationLoaderHarness() {
  const scripts = [];
  const requests = [];
  const events = [];
  const loading = node();
  const counts = { structure: 0, decorated: 0, reload: 0 };
  const listeners = new Map();
  const sandbox = vm.createContext({
    window: { addEventListener(type, listener) { listeners.set(type, listener); }, removeEventListener(type) { listeners.delete(type); } }, JsonInput: {}, OutputSchema: {}, ProviderCatalog: {}, SkillFrontmatter: {}, SchemaText: {}, URL,
    counts, location: { reload() { counts.reload += 1; } },
    Event: class { constructor(type) { this.type = type; } }, testImport: async () => ({}),
    DOMParser: class { parseFromString() { return {}; } },
    fetch: url => { const request = { url, ...deferred() }; requests.push(request); return request.promise; },
    document: { createElement: node, getElementById: () => loading, body: { appendChild: script => scripts.push(script) }, dispatchEvent: event => events.push(event.type) },
  });
  vm.runInContext(moduleSource(configuration), sandbox);
  vm.runInContext("appendAdminStructure = () => { counts.structure += 1; }; decorateConfiguration = () => { counts.decorated += 1; };", sandbox);
  const resolveTemplates = list => list.forEach(request => request.resolve({ ok: true, text: async () => "<html></html>" }));
  return { sandbox, scripts, requests, events, loading, counts, resolveTemplates, listeners };
}

test("快速重复进入共用配置初始化 Promise，DOM 和绑定均只有一次", async () => {
  const h = configurationLoaderHarness();
  const one = h.sandbox.initConfiguration();
  const two = h.sandbox.initConfiguration();
  assert.equal(one, two);
  assert.equal(h.requests.length, 2);
  h.resolveTemplates(h.requests);
  await tick();
  assert.equal(h.counts.structure, 1);
  assert.equal(h.scripts.length, 23);
  h.scripts.forEach(script => script.onload());
  await one;
  assert.equal(h.counts.decorated, 1);
  assert.deepEqual(h.events, ["DOMContentLoaded"]);
  await h.sandbox.initConfiguration();
  assert.equal(h.requests.length, 2);
  assert.equal(h.scripts.length, 23);
});

test("模板下载失败可安全重试，部分脚本失败只提供重载且不能重复绑定", async () => {
  const h = configurationLoaderHarness();
  const failing = h.sandbox.initConfiguration();
  h.requests[0].resolve({ ok: false, status: 503 });
  h.requests[1].resolve({ ok: true, text: async () => "" });
  await assert.rejects(failing, /HTTP 503/);
  assert.equal(h.counts.structure, 0);
  assert.equal(h.loading.children[1].textContent, "重试加载");
  const retry = h.sandbox.initConfiguration();
  h.resolveTemplates(h.requests.slice(2));
  await tick();
  assert.equal(h.scripts.length, 23);
  h.scripts[5].onerror();
  await assert.rejects(retry, /脚本加载失败/);
  assert.equal(h.loading.children[1].textContent, "重新加载页面");
  h.loading.children[1].click();
  assert.equal(h.counts.reload, 1);
  await assert.rejects(h.sandbox.initConfiguration(), /脚本加载失败/);
  h.scripts.filter((_, index) => index !== 5).forEach(script => script.onload());
  await tick();
  assert.deepEqual(h.events, []);
  assert.equal(h.counts.structure, 1);
  assert.equal(h.scripts.length, 23);
});

test("配置脚本运行时异常进入重载状态，不误报加载成功，也不拦截无关脚本错误", async () => {
  const h = configurationLoaderHarness();
  const loading = h.sandbox.initConfiguration();
  h.resolveTemplates(h.requests);
  await tick();
  h.listeners.get("error")({ filename: "http://test/unrelated.js" });
  assert.equal(h.counts.decorated, 0);
  h.listeners.get("error")({ filename: h.scripts[3].src });
  await assert.rejects(loading, /脚本执行失败/);
  assert.equal(h.loading.children[1].textContent, "重新加载页面");
  assert.equal(h.listeners.size, 0);
  // Remaining downloads still settle, but cannot turn the failed load into success.
  h.scripts.forEach(script => script.onload());
  await tick();
  assert.equal(h.counts.decorated, 0);
  assert.deepEqual(h.events, []);
});

test("恢复配置标签等待目标刷新，已激活标签和未激活标签均只刷新一次", async () => {
  const h = configurationLoaderHarness();
  const calls = [];
  const pending = deferred();
  const active = node();
  active.classList.add("active");
  const inactive = node();
  inactive.click = () => { calls.push("mcp"); h.sandbox.window.configurationTabsAdmin = { pending: pending.promise }; };
  h.sandbox.document.querySelector = selector => selector === ".resource-btn.active" ? { dataset: { tab: "agents" } } : selector.includes('"mcp"') ? inactive : active;
  h.sandbox.window.agentsAdmin = { refresh: async () => calls.push("agents") };
  await h.sandbox.restoreConfigurationTab();
  assert.deepEqual(calls, ["agents"]);
  h.sandbox.rememberConfigurationTab("mcp");
  let restored = false;
  const restoring = h.sandbox.restoreConfigurationTab().then(() => { restored = true; });
  await tick();
  assert.deepEqual(calls, ["agents", "mcp"]);
  assert.equal(restored, false);
  pending.resolve();
  await restoring;
  assert.equal(restored, true);
});

test("配置绑定阶段不查询隐藏页目录、数据库或 Agent，模型目录绑定不请求模型", () => {
  const calls = [];
  const h = classic(core, { location: { protocol: "http:" }, apiFetch: async path => calls.push(path) });
  h.sandbox.calls = calls;
  vm.runInContext("initTabs = () => {}; loadDatabaseSchemaVersion = () => calls.push('database'); loadCapabilities = () => calls.push('tools'); loadLlmModels = () => calls.push('models'); loadSkills = () => calls.push('skills');", h.sandbox);
  h.ready.forEach(listener => listener());
  assert.deepEqual(calls, []);
  const a = classic(agents);
  for (const name of ["initCapSection", "initPromptEstimate", "initListControls"]) a.window.agentsAdmin[name] = () => {};
  a.window.agentsAdmin.refresh = () => calls.push("agents");
  a.ready.forEach(listener => listener());
  assert.deepEqual(calls, []);
  const cat = moduleSource(catalog).replace(/\ninitCatalog\(\);\s*$/, "");
  const c = classic(cat, { window: { addEventListener() {} } });
  c.sandbox.calls = calls;
  vm.runInContext("bindCatalogSelect = () => {}; loadCatalog = () => calls.push('catalog'); initCatalog();", c.sandbox);
  assert.deepEqual(calls, []);
});

test("全局配置只请求当前子页面，操作页读轻量摘要，不运行字符集检查", async () => {
  const calls = [];
  const h = classic(globalSettings, {
    apiFetch: async path => { calls.push(path); return { groups: [] }; },
    loadLlmProviders: async () => calls.push("providers"), showToast() {},
  });
  h.window.databaseConnectionAdmin = { refresh: async () => calls.push("database-light") };
  h.window.modelCatalogAdmin = { refresh: async () => calls.push("catalog") };
  vm.runInContext("renderGlobalSettings = () => ''; renderWebSearchSettings = () => {}; renderDocumentParsingSummary = () => {};", h.sandbox);
  await h.window.globalSettingsAdmin.refresh();
  assert.deepEqual([...calls].sort(), ["/api/v1/global-settings/document-parsing", "/api/v1/global-settings/web-search"].sort());
  calls.length = 0;
  h.window.globalSettingsAdmin.page = "operations";
  await h.window.globalSettingsAdmin.refresh();
  assert.deepEqual(calls.sort(), ["/api/v1/global-settings", "database-light"].sort());
  calls.length = 0;
  h.window.globalSettingsAdmin.page = "models";
  await h.window.globalSettingsAdmin.refresh();
  assert.deepEqual(calls, ["catalog"]);
});

test("数据库检查只有主动点击才调用完整接口；重复点击共享一次检查，失败可重试", async () => {
  const calls = [];
  const pending = deferred();
  let fail = false;
  const h = classic(database, { apiFetch: async path => {
    calls.push(path);
    if (path.endsWith("include_charset=false")) return { database_type_label: "MySQL", schema_version: "013", charset: null };
    await pending.promise;
    if (fail) throw new Error("检查失败 <unsafe>");
    return { database_type: "mysql", charset: { status: "warning", issues: ["需要调整"] } };
  } });
  const admin = h.window.databaseConnectionAdmin;
  await admin.refresh();
  assert.deepEqual(calls, ["/api/v1/database-info?include_charset=false"]);
  assert.match(h.get("databaseConnectionContent").innerHTML, /尚未检查|未检查/);
  const one = admin.checkCharset();
  const two = admin.checkCharset();
  assert.equal(calls.length, 2);
  assert.match(h.get("databaseConnectionContent").innerHTML, /检查中/);
  pending.resolve();
  await Promise.all([one, two]);
  assert.match(h.get("databaseConnectionContent").innerHTML, /字符集需处理/);
  assert.match(h.get("databaseConnectionContent").innerHTML, /检查时间|获取时间/);
  fail = true;
  await admin.checkCharset();
  assert.match(h.get("databaseConnectionContent").innerHTML, /检查失败/);
  assert.doesNotMatch(h.get("databaseConnectionContent").innerHTML, /<unsafe>/);
  fail = false;
  await admin.checkCharset();
  assert.match(h.get("databaseConnectionContent").innerHTML, /字符集需处理/);
});

test("Agent 列表先展示，渠道晚到补充；过时请求不能覆盖新列表", async () => {
  const requests = [];
  const renders = [];
  const channels = [];
  const h = classic(agents, { apiFetch: path => { const request = { path, ...deferred() }; requests.push(request); return request.promise; }, showDbBanner() {}, showToast() {} });
  const admin = h.window.agentsAdmin;
  admin.renderList = rows => renders.push(rows.map(row => row.id));
  admin.setListView = () => {};
  admin.setChannelAccounts = accounts => channels.push(accounts);
  const first = admin.refresh();
  const second = admin.refresh();
  const lists = requests.filter(request => request.path === "/api/v1/agents");
  const annotations = requests.filter(request => request.path === "/api/v1/channel-accounts");
  lists.at(-1).resolve([{ id: "new" }]);
  await second;
  assert.deepEqual(renders, [["new"]]);
  assert.deepEqual(channels, []);
  lists[0].resolve([{ id: "old" }]);
  annotations[0].resolve([{ agent_id: "old" }]);
  await first;
  await tick();
  assert.deepEqual(renders, [["new"]]);
  assert.deepEqual(channels, []);
  annotations.at(-1).resolve([{ agent_id: "new" }]);
  await tick();
  assert.equal(channels.length, 1);
  assert.equal(channels[0][0].agent_id, "new");
});

test("Agent 编辑等目录就绪才使用原模型默认和关联值，发起测试等待工作台初始化", async () => {
  const pending = deferred();
  const calls = [];
  const h = classic(agents, {
    apiFetch: async path => {
      if (path.startsWith("/api/v1/llm-models")) { calls.push("models"); await pending.promise; return [{ id: "first" }]; }
      calls.push(path.endsWith("tools") ? "tools" : "skills");
      return [];
    },
    state: { llmModels: [{ id: "first" }] }, populateAgentModelSelect: id => calls.push(`model:${id}`),
  });
  h.window.DeepAgentModelOptions = { firstSelectableModelId: models => models[0].id };
  h.window.agentsAdmin.renderCapabilityCheckboxes = () => {};
  h.window.agentsAdmin.renderSkillChoices = () => {};
  h.window.agentsAdmin.openEditor = () => calls.push("opened");
  h.window.agentsAdmin.schedulePromptEstimate = () => {};
  const creating = h.window.agentsAdmin.openCreate();
  assert.ok(calls.includes("models"));
  assert.ok(!calls.includes("opened"));
  pending.resolve();
  await creating;
  assert.ok(calls.includes("model:first"));
  assert.ok(calls.includes("opened"));
  const ready = deferred();
  h.window.DeepAgentConsole = { showPage: async () => { calls.push("workspace"); await ready.promise; } };
  h.window.DeepAgentConsoleChat = { selectAgent: async id => calls.push(`select:${id}`) };
  for (const name of ["initCapSection", "initPromptEstimate", "initListControls"]) h.window.agentsAdmin[name] = () => {};
  h.window.agentsAdmin.requestCloseEditor = async () => true;
  h.window.agentsAdmin.refresh = () => {};
  h.ready.forEach(listener => listener());
  h.sandbox.state.editingAgentId = "test-agent";
  const testing = h.get("testAgentBtn").click();
  await tick();
  assert.ok(!calls.some(call => call.startsWith("select:")));
  ready.resolve();
  await testing;
  assert.equal(calls.at(-1), "select:test-agent");
  calls.length = 0;
  h.window.DeepAgentConsole.showPage = async () => false;
  await h.get("testAgentBtn").click();
  assert.deepEqual(calls, []);
  const errors = [];
  h.sandbox.showToast = message => errors.push(message);
  h.window.DeepAgentConsole.showPage = async () => true;
  h.window.DeepAgentConsoleChat.selectAgent = async () => false;
  await h.get("testAgentBtn").click();
  assert.match(errors[0], /Agent 已不可用/);
});

test("编辑目录失败保持原模型、关联及已打开表单；完整重试成功后才应用三目录", async () => {
  for (const failingPath of ["/api/v1/llm-models?selectable_only=false", "/api/v1/skills", "/api/v1/tools"]) {
    const state = { llmModels: [{ id: "old-model" }], capabilities: [{ code: "old-tool" }], skills: [{ code: "old-skill" }], editingAgentId: "old-agent" };
    const oldState = structuredClone(state);
    const requests = [], shown = [], errors = [];
    const pending = deferred();
    let fail = true;
    const h = classic(agents, {
      state, showToast: message => errors.push(message), populateAgentModelSelect: id => shown.push(`model:${id}`),
      // Legacy list helpers still swallow some failures. The editor must not use them.
      loadLlmModels: async () => { state.llmModels = []; }, loadSkills: async () => { state.skills = []; }, loadCapabilities: async () => {},
      apiFetch: async path => {
        requests.push(path);
        if (path === failingPath) { await pending.promise; if (fail) throw new Error("目录暂不可用"); }
        if (path === "/api/v1/llm-models?selectable_only=false") return [{ id: "saved-model" }];
        if (path === "/api/v1/tools") return [{ code: "saved-tool" }];
        if (path === "/api/v1/skills") return [{ code: "saved-skill" }];
        if (path.endsWith("/output-config")) return { structured_output_enabled: false };
        return { id: "target", name: "Target", model_id: "saved-model", prompt: "saved prompt", capabilities: ["saved-tool"], skills: ["saved-skill"] };
      },
    });
    h.window.DeepAgentOutputEditor = { reset: () => shown.push("output-reset") };
    const admin = h.window.agentsAdmin;
    admin.renderCapabilityCheckboxes = values => shown.push(`tools:${values.join()}`);
    admin.renderSkillChoices = values => shown.push(`skills:${values.join()}`);
    admin.openEditor = () => shown.push("opened");
    admin.schedulePromptEstimate = () => {};
    h.get("agentName").value = "untouched draft";
    const editing = admin.openEdit("target");
    await tick();
    assert.deepEqual(shown, []);
    assert.deepEqual(state, oldState);
    pending.resolve();
    await editing;
    assert.deepEqual(shown, []);
    assert.deepEqual(state, oldState);
    assert.equal(h.get("agentName").value, "untouched draft");
    assert.match(errors[0], /目录暂不可用/);
    fail = false;
    await admin.openEdit("target");
    assert.ok(shown.includes("model:saved-model"));
    assert.ok(shown.includes("tools:saved-tool"));
    assert.ok(shown.includes("skills:saved-skill"));
    assert.equal(state.llmModels[0].id, "saved-model");
    assert.equal(state.capabilities[0].code, "saved-tool");
    assert.equal(state.skills[0].code, "saved-skill");
    assert.equal(h.get("agentName").value, "Target");
  }
});

test("General 慢加载及失败时锁住编辑；只启用成功项，离页不发布迟到结果", async () => {
  assert.match(configurationTemplate, /id="openWebSearchSettingsBtn"[^>]*\bdisabled\b/);
  assert.match(configurationTemplate, /id="openDocumentParsingSettingsBtn"[^>]*\bdisabled\b/);
  const requests = [], toasts = [];
  const h = classic(globalSettings, { apiFetch: path => { const request = { path, ...deferred() }; requests.push(request); return request.promise; }, showToast: message => toasts.push(message) });
  const admin = h.window.globalSettingsAdmin;
  const loading = admin.refresh();
  assert.equal(h.get("openWebSearchSettingsBtn").disabled, true);
  assert.equal(h.get("openDocumentParsingSettingsBtn").disabled, true);
  requests.find(request => request.path.endsWith("/web-search")).reject(new Error("搜索配置暂不可用"));
  requests.find(request => request.path.endsWith("/document-parsing")).resolve({ value: { mode: "unconfigured" }, providers: [] });
  await loading;
  assert.equal(h.get("openWebSearchSettingsBtn").disabled, true);
  assert.equal(h.get("openDocumentParsingSettingsBtn").disabled, false);
  assert.match(h.get("webSearchSummaryValue").textContent, /加载失败/);
  const retry = admin.refresh();
  const latest = requests.slice(2);
  admin.page = "operations";
  latest.forEach(request => request.resolve({ value: { mode: "unconfigured" }, providers: [] }));
  await retry;
  assert.equal(h.get("openWebSearchSettingsBtn").disabled, true);
  assert.equal(h.get("openDocumentParsingSettingsBtn").disabled, true);
  assert.match(h.get("webSearchSummaryValue").textContent, /正在加载|加载失败/);
});

test("General 刷新仅更新摘要，不重绘已开编辑草稿和保存中的配置", async () => {
  const requests = [], forms = [];
  const h = classic(globalSettings, { apiFetch: path => { const request = { path, ...deferred() }; requests.push(request); return request.promise; }, showToast() {} });
  const admin = h.window.globalSettingsAdmin;
  h.sandbox.forms = forms;
  vm.runInContext("renderWebSearchSettings = () => forms.push('search form overwritten');", h.sandbox);
  const pending = admin.refresh();
  h.get("webSearchSettingsDialog").open = true;
  h.get("documentParsingSettingsDialog").open = true;
  h.get("webSearchApiKey").value = "unsaved search key";
  h.get("documentParsingApiKey").value = "unsaved document key";
  const savedSearch = { value: { mode: "provider", provider: "kimi" } };
  const savedDocument = { value: { mode: "provider", provider: "paddle" }, providers: [] };
  admin.webSearch = savedSearch;
  admin.documentParsing = savedDocument;
  requests.forEach(request => request.resolve({ value: { mode: "unconfigured" }, providers: [] }));
  await pending;
  assert.deepEqual(forms, []);
  assert.equal(admin.webSearch, savedSearch);
  assert.equal(admin.documentParsing, savedDocument);
  assert.equal(h.get("webSearchApiKey").value, "unsaved search key");
  assert.equal(h.get("documentParsingApiKey").value, "unsaved document key");
  const count = requests.length;
  await admin.refresh();
  assert.equal(requests.length, count, "已开草稿不再启动会覆盖草稿基线的摘要读取");
});

test("General 快速切出再返回补取离页丢弃项，search/doc 先后顺序对称", async () => {
  for (const firstSuffix of ["/web-search", "/document-parsing"]) {
    const requests = [];
    const h = classic(globalSettings, { apiFetch: path => { const request = { path, ...deferred() }; requests.push(request); return request.promise; }, showToast() {} });
    const admin = h.window.globalSettingsAdmin;
    const initial = admin.refresh();
    admin.page = "operations";
    const first = requests.find(request => request.path.endsWith(firstSuffix));
    const second = requests.find(request => request !== first);
    first.resolve({ value: { mode: "unconfigured" }, providers: [] });
    await tick();
    admin.page = "general";
    const returned = admin.refresh();
    assert.equal(requests.filter(request => request.path === second.path).length, 1, "另一子项仍在进行时复用原请求");
    second.resolve({ value: { mode: "unconfigured" }, providers: [] });
    await tick();
    assert.equal(requests.length, 3, "仅补取先前离页丢弃的子项");
    assert.equal(requests[2].path, first.path);
    requests[2].resolve({ value: { mode: "unconfigured" }, providers: [] });
    await Promise.all([initial, returned]);
    assert.equal(admin.webSearchReady, true);
    assert.equal(admin.documentParsingReady, true);
    assert.equal(h.get("openWebSearchSettingsBtn").disabled, false);
    assert.equal(h.get("openDocumentParsingSettingsBtn").disabled, false);
    assert.doesNotMatch(h.get("webSearchSummaryValue").textContent, /正在加载/);
    assert.doesNotMatch(h.get("documentParsingSummaryValue").textContent, /正在加载/);
  }
});

test("General 离页补取遇到真实HTTP失败就显示失败，不继续自动重试", async () => {
  const requests = [], errors = [];
  const h = classic(globalSettings, { apiFetch: path => { const request = { path, ...deferred() }; requests.push(request); return request.promise; }, showToast: message => errors.push(message) });
  const admin = h.window.globalSettingsAdmin;
  const initial = admin.refresh();
  admin.page = "operations";
  requests[0].resolve({ value: { mode: "unconfigured" }, providers: [] });
  await tick();
  admin.page = "general";
  const returned = admin.refresh();
  requests[1].resolve({ value: { mode: "unconfigured" }, providers: [] });
  await tick();
  assert.equal(requests.length, 3);
  requests[2].reject(Object.assign(new Error("配置读取失败"), { status: 503 }));
  await Promise.all([initial, returned]);
  await tick();
  assert.equal(requests.length, 3);
  assert.equal(admin.webSearchReady, false);
  assert.equal(admin.documentParsingReady, true);
  assert.equal(h.get("openWebSearchSettingsBtn").disabled, true);
  assert.match(h.get("webSearchSummaryValue").textContent, /加载失败/);
  assert.deepEqual(errors, ["配置读取失败"]);
});

test("General 同批另一项仍加载时重复刷新，已成功显示的子项保持就绪且不重读", async () => {
  for (const suffix of ["/web-search", "/document-parsing"]) {
    const requests = [];
    const h = classic(globalSettings, { apiFetch: path => { const request = { path, ...deferred() }; requests.push(request); return request.promise; }, showToast() {} });
    const admin = h.window.globalSettingsAdmin;
    const initial = admin.refresh();
    const completed = requests.find(request => request.path.endsWith(suffix));
    const pending = requests.find(request => request !== completed);
    completed.resolve({ value: { mode: "unconfigured" }, providers: [] });
    await tick();
    const readyKey = suffix === "/web-search" ? "webSearchReady" : "documentParsingReady";
    const summary = h.get(suffix === "/web-search" ? "webSearchSummaryValue" : "documentParsingSummaryValue");
    const text = summary.textContent;
    assert.equal(admin[readyKey], true);
    const repeated = admin.refresh();
    assert.equal(requests.length, 2);
    assert.equal(admin[readyKey], true);
    assert.equal(summary.textContent, text);
    pending.resolve({ value: { mode: "unconfigured" }, providers: [] });
    await Promise.all([initial, repeated]);
    assert.equal(requests.length, 2);
  }
});

test("用户资料首次展开才读取轻量数据库版本，同步展开只发送一次", async () => {
  const calls = [];
  const pending = deferred();
  const h = classic(moduleSource(contextSource), { renderUserIdentity: () => "", window: {
    DeepAgentConsoleAPI: { serviceUrl: path => path },
    daFetch: async path => { calls.push(path); await pending.promise; return { ok: true, json: async () => ({ schema_version: "013" }) }; },
  } });
  assert.deepEqual(calls, []);
  h.sandbox.toggleUserPopover();
  h.sandbox.toggleUserPopover();
  h.sandbox.toggleUserPopover();
  assert.deepEqual(calls, ["/api/v1/database-info?include_charset=false"]);
  pending.resolve();
  await tick();
  assert.equal(h.get("databaseSchemaVersion").textContent, "V13");
});
