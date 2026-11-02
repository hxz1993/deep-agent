import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const catalogSource = await readFile(
  new URL("../../../console/configuration/catalog.js", import.meta.url),
  "utf8",
);
const catalogTemplate = await readFile(
  new URL("../../../console/catalog-template.html", import.meta.url),
  "utf8",
);
const modelSource = await readFile(
  new URL("../../../console/configuration/llm-models.js", import.meta.url),
  "utf8",
);
const modelTemplate = await readFile(
  new URL("../../../console/configuration-template.html", import.meta.url),
  "utf8",
);
const consoleStyles = await readFile(
  new URL("../../../console/console.css", import.meta.url),
  "utf8",
);
const sharedPrimitives = await readFile(new URL("../../../console/ui-primitives.css", import.meta.url), "utf8");
const coreSource = await readFile(
  new URL("../../../console/configuration/core.js", import.meta.url),
  "utf8",
);

test("模型目录支持联网刷新和可往返的JSON导入导出", () => {
  assert.match(catalogSource, /\/api\/v1\/llm-models\/catalog\?\$\{queryString\(\)\}/);
  assert.match(catalogSource, /\/api\/v1\/llm-models\/catalog\/refresh/);
  assert.match(catalogSource, /api\/v1\/llm-models\/catalog\/import/);
  assert.match(catalogSource, /api\/v1\/llm-models\/catalog\/export/);
  assert.match(catalogSource, /new FormData\(\)/);
  assert.match(catalogTemplate, /id="catalogExportBtn"[^>]*>[\s\S]*data-lucide="download"[\s\S]*导出/);
  assert.match(catalogTemplate, /id="catalogImportBtn"[^>]*>[\s\S]*data-lucide="upload"[\s\S]*导入/);
  assert.match(catalogTemplate, /id="catalogRefreshBtn"[^>]*>[\s\S]*data-lucide="cloud-download"[\s\S]*在线更新/);
  assert.match(catalogTemplate, /catalogExportBtn[\s\S]*catalogImportBtn[\s\S]*catalogRefreshBtn/);
  assert.doesNotMatch(catalogTemplate, /导入JSON|导出JSON|更新模型目录/);
  assert.match(catalogTemplate, /id="catalogImportInput"[^>]*accept="application\/json,.json"/);
  assert.doesNotMatch(catalogTemplate, /catalogReloadBtn|重新加载/);
  assert.doesNotMatch(catalogSource, /source=|sourceSelect|refresh=true|bundled/);
  assert.doesNotMatch(catalogTemplate, /内置 JSON|自动（优先|modality_data/);
});

test("目录按供应商筛选模型且不提供行内添加入口", () => {
  assert.match(catalogTemplate, /id="catalogSupplierSidebar"/);
  assert.match(catalogSource, /params.set\("provider_id", catalogState.providerId\)/);
  assert.doesNotMatch(catalogSource, /data-use-model|<th>操作<\/th>/);
  assert.doesNotMatch(catalogTemplate, /data-catalog-view|catalogProviderSelect|全部 Lab|接入渠道/);
  assert.doesNotMatch(catalogSource, /modelDetailRow|data-toggle-model/);
});

test("模型目录标题栏复用配置页标题层级", () => {
  assert.match(catalogTemplate, /class="panel-toolbar models-toolbar catalog-header"/);
  assert.match(catalogTemplate, /class="panel-subtitle settings-desc models-desc"/);
  assert.match(consoleStyles, /\.catalog-panel \.models-toolbar\s*\{[^}]*min-height: 50px;[^}]*justify-content: space-between/);
  assert.match(consoleStyles, /\.catalog-panel \.catalog-title\s*\{[^}]*font-size: 20px/);
});

test("模型目录筛选左对齐并复用 Console 下拉控件", () => {
  const filterMarkup = catalogTemplate.split('<div class="models-filters">')[1]?.split('<div class="catalog-feedback')[0] || "";
  assert.match(filterMarkup, /placeholder="搜索当前供应商的模型"/);
  assert.match(filterMarkup, />全部输入模态</);
  assert.doesNotMatch(filterMarkup, /<span>搜索<\/span>|<span>接入渠道<\/span>|<span>输入模态<\/span>|<span>查看方式<\/span>/);
  assert.match(consoleStyles, /\.catalog-panel \.models-filters\s*\{[^}]*justify-content: flex-start;[^}]*border: 0;[^}]*background: transparent;/);
});

test("目录说明收进副标题信息提示且不再保留底部展开区域", () => {
  assert.match(
    catalogTemplate,
    /<h1 class="catalog-title settings-group-title">[\s\S]*模型目录[\s\S]*class="catalog-info"/,
  );
  assert.match(catalogTemplate, /data-lucide="info"/);
  assert.match(catalogTemplate, /class="catalog-info-tooltip" role="tooltip"/);
  assert.match(consoleStyles, /\.catalog-info-tooltip\s*\{[^}]*left:\s*0;[^}]*top:/);
  assert.doesNotMatch(catalogTemplate, /<details|<summary>目录与运行配置的关系/);
});

test("模型目录使用供应商卡片和紧凑能力标签", () => {
  assert.match(catalogTemplate, /catalog-lab-heading[\s\S]*供应商/);
  assert.match(catalogSource, /tool_call: "工具"[\s\S]*structured_output: "结构化"[\s\S]*temperature: "温度"[\s\S]*open_weights: "开放"[\s\S]*interleaved: "交错"/);
  assert.match(catalogSource, /window\.lucide\?\.createIcons\(\)/);
  assert.match(consoleStyles, /\.catalog-lab-sidebar\s*\{[^}]*border: 0;[^}]*background: transparent/);
  assert.match(consoleStyles, /\.catalog-lab-item\s*\{[^}]*border-radius: 9px/);
  assert.match(consoleStyles, /\.catalog-lab-item\s*\{[^}]*align-items: center/);
  assert.match(consoleStyles, /\.catalog-lab-item > span,[\s\S]*\.catalog-lab-item > strong \{[^}]*align-items: center/);
  assert.match(consoleStyles, /\.catalog-model-table:not\(\.catalog-provider-table\)\s*\{ table-layout: fixed; \}/);
});

test("模型目录填满内容区且只滚动内部列表", () => {
  assert.match(coreSource, /classList\.toggle\("global-settings-active", name === "global-settings"\)/);
  assert.match(consoleStyles, /\.configuration-content\.global-settings-active\s*\{[^}]*overflow: hidden;/);
  assert.match(consoleStyles, /\.configuration-content\.global-settings-active #configurationHost,[\s\S]*?#panel-global-settings\.active\s*\{[^}]*height: 100%;[^}]*overflow: hidden;/);
  assert.match(consoleStyles, /\.global-settings-page\.catalog-panel:not\(\[hidden\]\)\s*\{[^}]*display: flex;[^}]*height: 100%;[^}]*overflow: hidden;/);
  assert.match(consoleStyles, /\.catalog-panel \.catalog-browser\s*\{[^}]*flex: 1 1 auto;[^}]*min-height: 0;[^}]*overflow: hidden;/);
  assert.match(consoleStyles, /\.catalog-panel \.catalog-results\s*\{[^}]*display: flex;[^}]*min-height: 0;[^}]*overflow: hidden;/);
  assert.match(consoleStyles, /#configurationHost \.catalog-panel \.models-table-wrap\s*\{[^}]*flex: 1 1 auto;[^}]*overflow: auto;/);
  assert.match(sharedPrimitives, /\.console-pagination, \.models-pagination \{ margin-top:10px; \}/);
});

test("模型表单必填上下文和最大输出且模态与能力标签分开", () => {
  assert.match(
    modelTemplate,
    /llmModelModel[\s\S]*llmModelDescription[\s\S]*llmModelName[\s\S]*llmModelProviderId[\s\S]*llm-advanced-divider[\s\S]*llmModelContextWindow/,
  );
  assert.match(modelTemplate, /模型能力配置/);
  assert.match(
    modelTemplate,
    /id="updateLlmModelFromCatalog"[\s\S]*aria-label="从模型目录更新"[\s\S]*data-lucide="refresh-cw"/,
  );
  assert.match(modelTemplate, /id="llmModelContextWindow"[^>]*required/);
  assert.match(modelTemplate, /id="llmModelMaxOutput"[^>]*required/);
  assert.match(modelTemplate, /name="llmModelModality"/);
  assert.match(modelTemplate, /name="llmModelTag"/);
  assert.match(modelSource, /context_window_tokens: contextWindow/);
  assert.match(modelSource, /max_output_tokens: maxOutput/);
  assert.match(modelSource, /tags: this\.modelTags\(\)/);
  assert.match(modelSource, /provider_id: document\.getElementById\("llmModelProviderId"\)\.value/);
  assert.match(modelSource, /findCatalogProvider/);
  assert.doesNotMatch(modelTemplate, /llmModelApiKey|llmModelBaseUrl/);
  assert.doesNotMatch(modelSource, /capabilities_source|llmModelSource/);
  assert.match(modelSource, /setCatalogFeedback\(`已从模型目录读取 \$\{model\.model_label\}，保存后生效`\)/);
  assert.match(modelTemplate, /id="llmModelCatalogFeedback"[^>]*aria-live="polite"/);
  assert.doesNotMatch(modelSource.match(/async updateFromCatalog\(\)[\s\S]*?\n  },/)?.[0] || "", /showToast/);
  assert.doesNotMatch(modelSource, /refresh-metadata/);
});

test("模型管理只保留供应商内模态框而不再提供独立列表页", () => {
  assert.doesNotMatch(modelTemplate, /id="panel-llm-models"|id="llmModelsTableBody"|<h2>模型管理<\/h2>/);
  assert.match(modelTemplate, /id="llmModelModal"/);
  assert.match(modelTemplate, /模型名称/);
  assert.doesNotMatch(modelTemplate, /模型 API 编码/);
  assert.match(modelSource, /lockProvider/);
  assert.match(modelSource, /deriveName/);
});

test("模型能力行左右对齐且模型弹框使用更紧凑宽度", async () => {
  const consoleStyles = await readFile(
    new URL("../../../console/console.css", import.meta.url),
    "utf8",
  );
  assert.equal((modelTemplate.match(/class="field llm-option-row"/g) || []).length, 2);
  assert.match(consoleStyles, /\.llm-option-row \{[^}]*grid-template-columns: 76px minmax\(0, 1fr\);[^}]*align-items: center/);
  assert.match(consoleStyles, /dialog\.console-detail-dialog#llmModelModal \{ width: min\(760px, calc\(100vw - 32px\)\); \}/);
});

test("模型目录独立只读浏览，模型管理仍保留目录创建能力", () => {
  assert.doesNotMatch(catalogSource, /openCreateFromCatalog/);
  assert.doesNotMatch(catalogSource, /data-tab="llm-models"/);
  assert.match(modelSource, /provider_id: selected\.id/);
  assert.match(modelSource, /input_modalities: model\.input_modalities \|\| \[\]/);
  assert.match(modelSource, /tags: model\.tags \|\| \[\]/);
  assert.doesNotMatch(modelSource, /llmModelApiKey/);
});

test("编辑模型可按运行编码或供应商模型编码匹配目录", () => {
  const helperSource = modelSource.slice(
    modelSource.indexOf("function modelCodeAliases"),
    modelSource.indexOf("const llmModelsAdmin"),
  );
  const sandbox = {
    URL,
    catalog: {
      items: [
        {
          canonical_model_id: "moonshotai/kimi-k2.6",
          model_label: "Kimi K2.6",
          providers: [{
            provider_model_id: "kimi-k2.6",
            runtime_model: "openai:kimi-k2.6",
            provider_api_url: "https://api.moonshot.cn/v1",
          }],
        },
        {
          canonical_model_id: "deepseek/deepseek-v4-flash",
          model_label: "DeepSeek V4 Flash",
          providers: [{
            provider_model_id: "deepseek-v4-flash",
            runtime_model: "deepseek:deepseek-v4-flash",
            provider_api_url: "https://api.deepseek.com",
          }],
        },
      ],
    },
  };
  vm.runInNewContext(
    `${helperSource}
     result = [
       findCatalogProvider(catalog, "kimi-k2.6", "https://api.moonshot.cn/v1"),
       findCatalogProvider(catalog, "deepseek:deepseek-v4-flash"),
     ];`,
    sandbox,
  );
  assert.equal(sandbox.result?.[0]?.provider?.runtime_model, "openai:kimi-k2.6");
  assert.equal(sandbox.result?.[1]?.provider?.runtime_model, "deepseek:deepseek-v4-flash");
});

test("目录自定义下拉保留选中值并通过原 change 事件更新筛选", () => {
  const listeners = {};
  const hidden = new Set(["hidden"]);
  let changed = 0;
  let focused = 0;
  const trigger = {
    expanded: "false",
    addEventListener: (name, fn) => { listeners[`trigger:${name}`] = fn; },
    getAttribute() { return this.expanded; },
    setAttribute(name, value) { this.expanded = value; },
    focus() { focused += 1; },
  };
  const select = {
    value: "deepseek",
    options: [{ value: "", textContent: "全部接入渠道" }, { value: "deepseek", textContent: "DeepSeek（3）" }],
    dispatchEvent(event) { assert.equal(event.type, "change"); changed += 1; },
  };
  const menu = {
    classList: { add: (value) => hidden.add(value), remove: (value) => hidden.delete(value) },
    addEventListener: (name, fn) => { listeners[`menu:${name}`] = fn; },
    querySelector: () => ({ focus() {} }),
    querySelectorAll: () => [],
    innerHTML: "",
  };
  const label = { textContent: "" };
  const nodes = { filter: select, filterTrigger: trigger, filterMenu: menu, filterLabel: label };
  const sandbox = {
    Event,
    element: (id) => nodes[id],
    escapeHtml: (value) => value,
    document: { querySelectorAll: () => [{ querySelector: (selector) => selector.includes("trigger") ? trigger : menu }] },
  };
  vm.runInNewContext(catalogSource.slice(catalogSource.indexOf("function renderCatalogSelect"), catalogSource.indexOf("function renderModelRows")), sandbox);
  sandbox.bindCatalogSelect("filter");
  assert.equal(label.textContent, "DeepSeek（3）");
  listeners["trigger:click"]();
  assert.equal(hidden.has("hidden"), false);
  assert.equal(trigger.expanded, "true");
  listeners["menu:click"]({ target: { closest: () => ({ dataset: { catalogOption: "" } }) } });
  assert.equal(select.value, "");
  assert.equal(label.textContent, "全部接入渠道");
  assert.equal(changed, 1);
  assert.equal(focused, 1);
  assert.equal(hidden.has("hidden"), true);
  listeners["trigger:click"]();
  listeners["menu:keydown"]({ key: "Escape", preventDefault() {} });
  assert.equal(trigger.expanded, "false");
  assert.equal(changed, 1);
});


test("供应商模型行使用该供应商的编码和能力而非研发方上限", () => {
  const sandbox = {};
  const source = catalogSource.slice(0, catalogSource.indexOf("async function parseJsonResponse"))
    + catalogSource.slice(catalogSource.indexOf("function renderModelRows"), catalogSource.indexOf("function renderResults"));
  vm.runInNewContext(source + `
    catalogState.providerId = "second";
    catalogState.items = [{ canonical_model_id: "lab/model", model_label: "Model", context_window_tokens: 9999999,
      providers: [
        { provider_id: "first", provider_model_id: "wrong" },
        { provider_id: "second", provider_model_id: "correct", context_window_tokens: 32000, max_output_tokens: 8000, input_modalities: ["text"], tags: ["tool_call"] }
      ] }];
    globalThis.result = renderModelRows();`, sandbox);
  assert.match(sandbox.result, /correct/);
  assert.match(sandbox.result, /32K/);
  assert.match(sandbox.result, /8K/);
  assert.equal((sandbox.result.match(/<td(?: |>)/g) || []).length, 5);
  assert.doesNotMatch(sandbox.result, /data-use-model|使用此模型/);
  assert.match(sandbox.result, />工具</);
  assert.doesNotMatch(sandbox.result, /wrong|9999999|接入渠道/);
});


test("目录加载期间的新筛选会重新请求，迟到结果不覆盖新条件", async () => {
  const requests = [], renders = [];
  const state = { loading: false, query: "old", providerId: "p", offset: 0 };
  const node = { innerHTML: "", textContent: "" };
  const sandbox = {
    catalogState: state, loadGeneration: 0, pendingLoad: false,
    element: () => node, setFeedback() {}, renderPagination() {}, renderFacets() {},
    renderResults: () => renders.push(state.items[0]?.id), formatDateTime: () => "date",
    queryString: () => state.query,
    request: url => new Promise(resolve => requests.push({ url, resolve })),
  };
  vm.runInNewContext(catalogSource.slice(catalogSource.indexOf("async function loadCatalog"), catalogSource.indexOf("async function refreshCatalog")), sandbox);
  const first = sandbox.loadCatalog();
  state.query = "new";
  await sandbox.loadCatalog();
  requests[0].resolve({ items: [{ id: "old" }], facets: { providers: [{ id: "p" }] } });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(requests.length, 2);
  assert.match(requests[1].url, /new$/);
  assert.deepEqual(renders, []);
  requests[1].resolve({ items: [{ id: "new" }], total: 1, facets: { providers: [{ id: "p" }] } });
  await first;
  assert.deepEqual(renders, ["new"]);
  assert.equal(state.loading, false);
});
