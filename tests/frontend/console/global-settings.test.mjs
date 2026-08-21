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
  assert.match(templateHtml, /当前页面只读；修改环境变量并重启 DeepAgent 后生效/);
  assert.doesNotMatch(templateHtml, /agentMaxRequests|max_requests|最大请求次数（可选）/);
  assert.ok(
    indexHtml.indexOf('data-tab="auxiliary-models"') <
      indexHtml.indexOf('data-tab="global-settings"'),
  );
  assert.ok(
    indexHtml.indexOf("系统设置") <
      indexHtml.indexOf('data-tab="global-settings"'),
  );
  assert.ok(
    indexHtml.indexOf('data-tab="global-settings"') <
      indexHtml.indexOf('data-tab="database-connection"'),
  );
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
