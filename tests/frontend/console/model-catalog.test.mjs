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

test("目录提供模型与接入渠道视图并按 Lab 分类", () => {
  assert.match(catalogTemplate, /data-catalog-view="model"/);
  assert.match(catalogTemplate, /data-catalog-view="provider"/);
  assert.match(catalogTemplate, /id="catalogLabSidebar"/);
  assert.match(catalogSource, /provider_count/);
  assert.match(catalogSource, /openCreateFromCatalog\(model, provider\)/);
});

test("目录查看方式位于筛选行且渠道视图使用完整宽度", () => {
  assert.match(
    catalogTemplate,
    /class="models-filters"[\s\S]*catalogProviderSelect[\s\S]*catalogModalitySelect[\s\S]*class="catalog-view-control"[\s\S]*data-catalog-view="model"[\s\S]*data-catalog-view="provider"/,
  );
  assert.match(catalogSource, /catalog-provider-view/);
  assert.match(consoleStyles, /\.catalog-browser\.catalog-provider-view\s*\{\s*grid-template-columns:\s*minmax\(0,\s*1fr\)/);
  assert.match(consoleStyles, /\.catalog-provider-table\s*\{[^}]*width:\s*100%[^}]*min-width:\s*0/);
  assert.match(catalogTemplate, /class="panel-toolbar models-toolbar catalog-header"[\s\S]*<\/div>\s*<div class="models-filters"[\s\S]*<div class="catalog-feedback/);
});

test("模型目录标题栏复用配置页标题层级", () => {
  assert.match(catalogTemplate, /class="panel-toolbar models-toolbar catalog-header"/);
  assert.match(catalogTemplate, /class="panel-subtitle settings-desc models-desc"/);
  assert.match(consoleStyles, /\.catalog-panel \.models-toolbar\s*\{[^}]*min-height: 50px;[^}]*justify-content: space-between/);
  assert.match(consoleStyles, /\.catalog-panel \.catalog-title\s*\{[^}]*font-size: 20px/);
});

test("模型目录筛选使用无标题透明命令栏并自然右对齐", () => {
  const filterMarkup = catalogTemplate.match(/<div class="models-filters">[\s\S]*?<\/div>\s*<\/div>/)?.[0] || "";
  assert.match(filterMarkup, /placeholder="搜索模型、Lab 或接入渠道"/);
  assert.match(filterMarkup, />全部接入渠道</);
  assert.match(catalogSource, /<option value="">全部接入渠道<\/option>/);
  assert.match(filterMarkup, />全部输入模态</);
  assert.doesNotMatch(filterMarkup, /<span>搜索<\/span>|<span>接入渠道<\/span>|<span>输入模态<\/span>|<span>查看方式<\/span>/);
  assert.match(consoleStyles, /\.catalog-panel \.models-filters\s*\{[^}]*justify-content: flex-end;[^}]*border: 0;[^}]*background: transparent;/);
});

test("目录说明收进副标题信息提示且不再保留底部展开区域", () => {
  assert.match(
    catalogTemplate,
    /<h1 class="catalog-title">[\s\S]*模型目录[\s\S]*class="catalog-info"/,
  );
  assert.match(catalogTemplate, /data-lucide="info"/);
  assert.match(catalogTemplate, /class="catalog-info-tooltip" role="tooltip"/);
  assert.match(consoleStyles, /\.catalog-info-tooltip\s*\{[^}]*left:\s*0;[^}]*top:/);
  assert.doesNotMatch(catalogTemplate, /<details|<summary>目录与运行配置的关系/);
});

test("模型目录使用轻量研发方卡片和紧凑图标渠道入口", () => {
  assert.match(catalogTemplate, /catalog-lab-heading[\s\S]*模型研发方/);
  assert.match(catalogSource, /class="catalog-channel-trigger"/);
  assert.match(catalogSource, /data-lucide="network"/);
  assert.match(catalogSource, /window\.lucide\?\.createIcons\(\)/);
  assert.match(consoleStyles, /\.catalog-lab-sidebar\s*\{[^}]*border: 0;[^}]*background: transparent/);
  assert.match(consoleStyles, /\.catalog-lab-item\s*\{[^}]*border-radius: 9px/);
  assert.match(consoleStyles, /\.catalog-lab-item\s*\{[^}]*align-items: center/);
  assert.match(consoleStyles, /\.catalog-lab-item > span,[\s\S]*\.catalog-lab-item > strong \{[^}]*align-items: center/);
  assert.match(consoleStyles, /\.catalog-model-table:not\(\.catalog-provider-table\)\s*\{ table-layout: fixed; \}/);
});

test("模型目录填满内容区且只滚动内部列表", () => {
  assert.match(coreSource, /classList\.toggle\("catalog-active", name === "catalog"\)/);
  assert.match(consoleStyles, /\.configuration-content\.catalog-active\s*\{[^}]*overflow: hidden;/);
  assert.match(consoleStyles, /\.configuration-content\.catalog-active #configurationHost,[\s\S]*?#panel-catalog\.active\s*\{[^}]*height: 100%;[^}]*overflow: hidden;/);
  assert.match(consoleStyles, /\.catalog-panel\.active\s*\{[^}]*display: flex;[^}]*height: 100%;[^}]*overflow: hidden;/);
  assert.match(consoleStyles, /\.catalog-panel \.catalog-browser\s*\{[^}]*flex: 1 1 auto;[^}]*min-height: 0;[^}]*overflow: hidden;/);
  assert.match(consoleStyles, /\.catalog-panel \.catalog-results\s*\{[^}]*display: flex;[^}]*min-height: 0;[^}]*overflow: hidden;/);
  assert.match(consoleStyles, /#configurationHost \.catalog-panel \.models-table-wrap\s*\{[^}]*flex: 1 1 auto;[^}]*overflow: auto;/);
  assert.match(consoleStyles, /\.catalog-panel \.models-pagination \{ margin: 10px 0 0; \}/);
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
  assert.match(modelSource, /tags: readCheckedValues\("llmModelTag"\)/);
  assert.match(modelSource, /provider_id: document\.getElementById\("llmModelProviderId"\)\.value/);
  assert.match(modelSource, /findCatalogProvider/);
  assert.doesNotMatch(modelTemplate, /llmModelApiKey|llmModelBaseUrl/);
  assert.doesNotMatch(modelSource, /capabilities_source|llmModelSource/);
  assert.match(modelSource, /setCatalogFeedback\(`已从模型目录读取 \$\{model\.model_label\}，保存后生效`\)/);
  assert.match(modelTemplate, /id="llmModelCatalogFeedback"[^>]*aria-live="polite"/);
  assert.doesNotMatch(modelSource.match(/async updateFromCatalog\(\)[\s\S]*?\n  },/)?.[0] || "", /showToast/);
  assert.doesNotMatch(modelSource, /refresh-metadata/);
});

test("模型管理只保留 Provider 内模态框而不再提供独立列表页", () => {
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
  assert.match(consoleStyles, /\.llm-option-row \{[^}]*grid-template-columns: minmax\(260px, 1fr\) minmax\(280px, auto\);[^}]*align-items: center/);
  assert.match(consoleStyles, /dialog\.console-detail-dialog#llmModelModal \{ width: min\(840px, calc\(100vw - 32px\)\); \}/);
});

test("模型目录继续独立浏览并直接打开模型创建模态框", () => {
  assert.match(catalogSource, /window\.llmModelsAdmin\?\.openCreateFromCatalog\(model, provider\)/);
  assert.doesNotMatch(catalogSource, /data-tab="llm-models"/);
  assert.match(modelSource, /provider_id: selected\.id/);
  assert.match(modelSource, /input_modalities: model\.input_modalities \|\| \[\]/);
  assert.match(modelSource, /tags: model\.tags \|\| \[\]/);
  assert.doesNotMatch(modelSource, /llmModelApiKey/);
});

test("编辑模型可按运行编码或 Provider 模型编码匹配目录", () => {
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
