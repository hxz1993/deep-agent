import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const catalogSource = await readFile(
  new URL("../../../frontend/console/configuration/catalog.js", import.meta.url),
  "utf8",
);
const catalogTemplate = await readFile(
  new URL("../../../frontend/console/catalog-template.html", import.meta.url),
  "utf8",
);
const modelSource = await readFile(
  new URL("../../../frontend/console/configuration/llm-models.js", import.meta.url),
  "utf8",
);
const modelTemplate = await readFile(
  new URL("../../../frontend/console/configuration-template.html", import.meta.url),
  "utf8",
);
const consoleStyles = await readFile(
  new URL("../../../frontend/console/console.css", import.meta.url),
  "utf8",
);

test("模型目录只使用数据库查询和管理员手工刷新", () => {
  assert.match(catalogSource, /\/api\/v1\/llm-models\/catalog\?\$\{queryString\(\)\}/);
  assert.match(catalogSource, /\/api\/v1\/llm-models\/catalog\/refresh/);
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
    /class="models-filters"[\s\S]*catalogProviderSelect[\s\S]*catalogModalitySelect[\s\S]*class="field catalog-view-control"[\s\S]*data-catalog-view="model"[\s\S]*data-catalog-view="provider"/,
  );
  assert.match(catalogSource, /catalog-provider-view/);
  assert.match(consoleStyles, /\.catalog-browser\.catalog-provider-view\s*\{\s*grid-template-columns:\s*minmax\(0,\s*1fr\)/);
  assert.match(consoleStyles, /\.catalog-provider-table\s*\{[^}]*width:\s*100%[^}]*min-width:\s*0/);
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

test("模型表单必填上下文和最大输出且模态与能力标签分开", () => {
  assert.match(
    modelTemplate,
    /llmModelApiKey[\s\S]*llm-advanced-divider[\s\S]*llmModelContextWindow/,
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
  assert.match(modelSource, /findCatalogProvider/);
  assert.doesNotMatch(modelSource, /capabilities_source|llmModelSource/);
  assert.match(modelSource, /保存后生效/);
  assert.doesNotMatch(modelSource, /refresh-metadata/);
});

test("模型配置列表只展示核心运行信息", () => {
  const tableMarkup = modelTemplate.slice(
    modelTemplate.indexOf('id="panel-llm-models"'),
    modelTemplate.indexOf("<!-- Auxiliary models panel -->"),
  );
  assert.match(tableMarkup, /<th>名称<\/th>[\s\S]*<th>模型<\/th>/);
  assert.match(
    tableMarkup,
    /<th>上下文<\/th>[\s\S]*<th>最大输出<\/th>[\s\S]*<th>模态标签<\/th>/,
  );
  assert.match(tableMarkup, /<th>API Key<\/th>[\s\S]*<th>Agent 数<\/th>/);
  assert.doesNotMatch(tableMarkup, /<th>Base URL<\/th>|<th>能力标签<\/th>/);
  assert.match(modelSource, /formatTokenCount\(model\.context_window_tokens\)/);
  assert.match(modelSource, /formatTokenCount\(model\.max_output_tokens\)/);
  assert.match(modelSource, /formatList\(model\.input_modalities\)/);
  assert.doesNotMatch(modelSource, /<td>\$\{escapeHtml\(model\.base_url/);
  assert.doesNotMatch(modelSource, /formatList\(model\.tags\)/);
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
