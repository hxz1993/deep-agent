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

test("全局配置是配置中心默认页且 Agent 表单不再包含请求上限", () => {
  assert.match(
    indexHtml,
    /class="tab resource-btn active" data-tab="global-settings"/,
  );
  assert.match(templateHtml, /id="panel-global-settings"/);
  assert.match(templateHtml, /当前页面只读；修改环境变量并重启 DeepAgent 后生效/);
  assert.doesNotMatch(templateHtml, /agentMaxRequests|max_requests|最大请求次数（可选）/);
  assert.ok(
    indexHtml.indexOf('data-tab="auxiliary-models"') <
      indexHtml.indexOf('data-tab="global-settings"'),
  );
  assert.ok(
    indexHtml.indexOf('data-tab="global-settings"') <
      indexHtml.indexOf('<div class="resource-group-label separated">'),
  );
});

test("全局配置列表展示值、单位、环境变量和来源", () => {
  const ui = loadGlobalSettingsUi();
  const html = ui.groupHtml({
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
  const html = ui.groupHtml({
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
