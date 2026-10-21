import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";
import * as JsonInput from "../../../console/json-input.js";

const source = await readFile(
  new URL("../../../console/configuration/mcp.js", import.meta.url),
  "utf8",
);
const template = await readFile(
  new URL("../../../console/configuration-template.html", import.meta.url),
  "utf8",
);
const styles = await readFile(
  new URL("../../../console/console.css", import.meta.url),
  "utf8",
);

function loadInspectionUi(overrides = {}) {
  const window = overrides.window || {};
  window.DeepAgentJsonInput = JsonInput;
  const document = {
    addEventListener() {},
  };
  vm.runInContext(
    source,
    vm.createContext({
      document,
      escapeHtml: (value) => String(value),
      formatDate: (value) => String(value),
      window,
      ...overrides,
    }),
  );
  return window.__McpInspectionUI;
}

test("内部 MCP 缺少有效 App 时提示真实配置入口", () => {
  const ui = loadInspectionUi();
  const server = { request_context: { mode: "inherit" } };
  assert.match(ui.missingInspectionAppHint(server, { app: null }), /未提供测试 App.*「对话测试」右上角「调试上下文」/);
  assert.equal(ui.missingInspectionAppHint(server, { app: "platform001" }), "");
  assert.equal(ui.missingInspectionAppHint({ ...server, config: { headers: { App: "static001" } } }, { app: null }), "");
  assert.equal(ui.missingInspectionAppHint({ request_context: { mode: "isolated" } }, { app: null }), "");
});

test("失败提示查询后端有效 App 并保留原错误", async () => {
  const requests = [];
  const server = { request_context: { mode: "inherit" } };
  const ui = loadInspectionUi({
    window: { DeepAgentConsoleAPI: { requestContext: async () => { requests.push("context"); return { app: null }; } } },
  });
  const message = await ui.inspectionFailureMessage(server, "MCP 初始化失败（TimeoutError）");
  assert.match(message, /^MCP 初始化失败（TimeoutError）。当前请求未提供测试 App/);
  assert.equal(requests.length, 1);
});

test("API 鉴权或网络失败不会被改写成缺少测试 App", async () => {
  const messages = [];
  let contextRequests = 0;
  const window = { DeepAgentConsoleAPI: { requestContext: async () => { contextRequests++; return { app: null }; } } };
  loadInspectionUi({
    window,
    apiFetch: async () => { throw new Error("权限不足"); },
    showToast: (message) => messages.push(message),
  });
  const label = { textContent: "检测" };
  const button = { querySelector: () => label, isConnected: true };
  await window.mcpAdmin.inspect("deepfos", button, { request_context: { mode: "inherit" } });
  assert.deepEqual(messages, ["权限不足"]);
  assert.equal(contextRequests, 0);
  assert.equal(button.disabled, false);
  assert.equal(label.textContent, "检测");
});

test("App 已生效或上下文查询失败时不推断为 App 未配置", async () => {
  for (const requestContext of [async () => ({ app: "inherited001" }), async () => { throw new Error("network"); }]) {
    const ui = loadInspectionUi({
      window: { DeepAgentConsoleAPI: { requestContext } },
    });
    assert.equal(await ui.inspectionFailureMessage({ request_context: { mode: "inherit" } }, "MCP Ping 失败"), "MCP Ping 失败");
  }
});

test("URL MCP 在提交前拒绝空地址", () => {
  const ui = loadInspectionUi();
  assert.equal(ui.validateMcpConfig("url", { url: "" }), "MCP URL 不能为空");
  assert.equal(ui.validateMcpConfig("url", { url: "   " }), "MCP URL 不能为空");
  assert.equal(ui.validateMcpConfig("url", { url: "http://example.test/mcp" }), null);
  assert.equal(ui.validateMcpConfig("builtin", {}), null);
});

test("MCP 编辑使用详情页式工作台并保留原配置字段", () => {
  assert.match(template, /class="modal agent-editor-page mcp-editor-page" id="mcpModal"/);
  assert.match(template, /id="closeMcpModal"[^>]*>[\s\S]*arrow-left[\s\S]*返回/);
  assert.match(template, /class="mcp-editor-workbench"/);
  assert.doesNotMatch(template, /mcp-editor-section-head|定义服务名称和用途|连接与请求上下文/);
  ["mcpName", "mcpDescription", "mcpTransport", "mcpUrl", "mcpHeaders", "mcpRequestContextDescription"]
    .forEach((id) => assert.match(template, new RegExp(`id="${id}"`)));
  assert.doesNotMatch(template, /id="mcpEnabled"/);
  assert.doesNotMatch(template, /id="cancelMcpBtn"/);
  assert.match(styles, /dialog\.mcp-editor-page\.console-detail-dialog/);
  assert.match(styles, /\.mcp-editor-workbench\s*\{[^}]*grid-template-columns:/);
  assert.match(source, /document\.getElementById\("mcpModal"\)\.show\(\)/);
  assert.doesNotMatch(source, /document\.getElementById\("mcpModal"\)\.showModal\(\)/);
  assert.match(source, /放弃未保存的修改/);
  assert.match(source, /data-open-mcp/);
});

test("MCP 表单居中限宽，窄屏不保留横向最小宽度", () => {
  assert.match(styles, /\.mcp-editor-page > form\s*\{ width: min\(1080px, 100%\); \}/);
  assert.doesNotMatch(styles, /\.mcp-editor-workbench\s*\{[^}]*min-width: (?:720|760)px/);
  assert.match(styles, /@media \(max-width: 1100px\)[\s\S]*?\.mcp-editor-workbench \{ grid-template-columns: minmax\(0, 1fr\); \}/);
});

test("MCP JSON 编辑器使用普通输入底色，高亮与输入层保持透明叠加", () => {
  assert.match(styles, /\.json-input\s*\{[^}]*background: var\(--bg\);/);
  assert.match(styles, /\.json-input > \.json-input-highlight\s*\{[^}]*background: transparent;/);
  assert.match(styles, /\.json-input > \.json-input-text\s*\{[^}]*background: transparent;/);
});

test("MCP 高亮层与输入层共用字形度量且高亮不撑大输入框", () => {
  assert.match(styles, /\.json-input > \.json-input-highlight,\s*\.json-input > \.json-input-text\s*\{[^}]*font-weight: 400;[^}]*word-break: normal;[^}]*scrollbar-gutter: stable;/);
  assert.match(styles, /\.json-input \.json-input-highlight code span\s*\{\s*font: inherit;\s*letter-spacing: inherit;/);
  assert.match(styles, /\.json-input > \.json-input-highlight\s*\{[^}]*position: absolute;[^}]*height: 100%;/);
});

test("MCP Header 来源使用互斥的外部固定和内部继承", () => {
  assert.match(template, /Headers JSON/);
  assert.doesNotMatch(template, /Headers JSON（可选）/);
  assert.match(template, /value="isolated" checked[\s\S]*外部固定/);
  assert.match(template, /value="inherit"[\s\S]*内部继承/);
  assert.match(template, /class="json-input" id="mcpFixedHeaders"/);
  assert.match(template, /id="mcpHeadersHighlight"/);
  assert.doesNotMatch(template, /mcp-json-editor-bar/);
  assert.match(source, /mcpFixedHeaders"\)\?\.classList\.toggle\("hidden", mode === "inherit"\)/);
  assert.match(source, /if \(mode === "isolated"\) \{[\s\S]*config\.headers = headers;/);
  assert.match(source, /允许透传：/);
  assert.doesNotMatch(source, /data-context-help/);
  assert.doesNotMatch(source, /仅发送这里配置的固定 Header/);
  assert.match(styles, /\.json-input:focus-within/);
  assert.match(styles, /\.context-mode-description\s*\{[\s\S]*border-radius:/);
});

test("MCP Header JSON 编辑高亮会转义原文并区分主要类型", () => {
  const html = loadInspectionUi().highlightJsonEditorSource('{"name":"<script>","count":2,"ok":true}');
  assert.match(html, /class="json-key"/);
  assert.match(html, /class="json-string"/);
  assert.match(html, /class="json-number"/);
  assert.match(html, /class="json-boolean"/);
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /&lt;script&gt;/);
});

test("MCP 行内刷新和更多操作不会冒泡打开详情", () => {
  assert.match(source, /\.mcp-row-actions"\)\.forEach\(\(actions\) => \{[\s\S]*stopPropagation/);
  assert.match(source, /data-inspect-mcp[\s\S]*event\.stopPropagation\(\);[\s\S]*this\.inspect/);
  assert.match(source, /if \(event\.target\.closest\("a, button, \[role=menu\]"\)\) return;/);
});

test("检测状态展示紧凑能力计数和完整悬停信息", () => {
  const ui = loadInspectionUi();
  const view = ui.inspectionView(
    {
      status: "healthy",
      checked_at: "2026-07-23T10:00:00Z",
      duration_ms: 428,
      server: {
        name: "mcp-biz-go",
        version: "1.0.0",
        protocol_version: "2025-06-18",
        instructions_available: true,
      },
      tools: {
        count: 11,
        snapshot_status: "ready",
        snapshot_at: "2026-07-23T10:00:00Z",
        snapshot_hash: "1234567890abcdef",
      },
      resources: { count: 2, template_count: 1 },
    },
    "url",
  );

  assert.equal(view.text, "正常");
  assert.deepEqual(JSON.parse(JSON.stringify(view.counts)), { tools: 11, resources: 2, templates: 1 });
  assert.match(view.title, /服务：mcp-biz-go 1.0.0/);
  assert.match(view.title, /协议：2025-06-18/);
  assert.match(view.title, /Instructions：可用/);
  assert.match(view.title, /快照状态：工具快照可用/);
  assert.match(view.title, /耗时：428 ms/);
});

test("工具数量不再生成写死的披露策略文案", () => {
  const ui = loadInspectionUi();
  const view = ui.inspectionView(
    {
      status: "healthy",
      tools: { count: 10, snapshot_status: "ready" },
      resources: { count: 0, template_count: 0 },
    },
    "url",
  );
  assert.deepEqual(JSON.parse(JSON.stringify(view.counts)), { tools: 10, resources: 0, templates: 0 });
  assert.doesNotMatch(source, /全部直接披露|自动预选 Top/);
});

test("URL MCP 明确展示缺失、失效和沿用快照状态", () => {
  const ui = loadInspectionUi();
  assert.match(
    ui.inspectionView({ status: "healthy", tools: { count: 7 } }, "url").title,
    /缺少工具快照，请检测并更新/,
  );
  assert.match(
    ui.inspectionView(
      { status: "stale", tools: { count: 7, snapshot_status: "stale" } },
      "url",
    ).title,
    /配置已变更，需重新检测/,
  );
  assert.match(
    ui.inspectionView(
      { status: "unreachable", tools: { count: 7, snapshot_status: "ready" } },
      "url",
    ).title,
    /当前无法连接，沿用上次快照/,
  );
});

test("Builtin 默认显示正常", () => {
  const ui = loadInspectionUi();
  const view = ui.inspectionView({
    status: "not_applicable",
    checked_at: "2026-09-02T10:00:00Z",
    tools: { count: 18 },
    resources: { count: 2, template_count: 1 },
  }, "builtin");
  assert.equal(view.text, "正常");
  assert.deepEqual(JSON.parse(JSON.stringify(view.counts)), { tools: 18, resources: 2, templates: 1 });
  assert.equal(ui.canDeleteMcp("builtin"), false);
  assert.equal(ui.canDeleteMcp("url"), true);
  assert.equal(ui.canDeleteMcp("stdio"), true);
  assert.match(source, /s\.transport === "url" \|\| s\.transport === "builtin"/);
});

test("Builtin 刷新反馈只描述自身清单，不声称保存 URL 工具快照", () => {
  const ui = loadInspectionUi();
  const message = ui.inspectionToast({
    inspection: {
      status: "not_applicable",
      tools: { count: 18 },
      resources: { count: 2, template_count: 1 },
    },
    changes: {
      tool_count: { before: 0, after: 18 },
      resource_count: { before: 0, after: 2 },
      template_count: { before: 0, after: 1 },
    },
  }).message;
  assert.match(message, /内置清单已刷新并保存/);
  assert.doesNotMatch(message, /工具快照|连接成功/);
});

test("历史 stdio MCP 明确显示为停止支持", () => {
  const ui = loadInspectionUi();
  const view = ui.inspectionView(null, "stdio");
  assert.equal(view.text, "已停止支持");
  assert.deepEqual(JSON.parse(JSON.stringify(view.counts)), { tools: 0, resources: 0, templates: 0 });
});

test("MCP 传输类型使用产品语义而不是后端枚举值", () => {
  assert.match(template, /<option value="url">Streamable HTTP<\/option>/);
  assert.match(template, /<option value="builtin">STDIO（Builtin）<\/option>/);
  assert.match(source, /url: "Streamable HTTP"/);
  assert.match(source, /builtin: "STDIO（Builtin）"/);
  assert.match(source, /class="mcp-inspection-counts"/);
  assert.match(source, /data-lucide="\$\{iconName\}"/);
});

test("检测结果区分变化、无变化、部分异常和无法连接", () => {
  const ui = loadInspectionUi();
  assert.equal(
    ui.inspectionToast({
      inspection: {
        status: "healthy",
        tools: { count: 12 },
        resources: { count: 2, template_count: 1 },
      },
      changes: {
        tool_count: { before: 10, after: 12 },
        resource_count: { before: 0, after: 2 },
        template_count: { before: 0, after: 1 },
      },
    }).message,
    "检测成功并保存 12 个工具快照：工具 10 → 12，资源 0 → 2，模板 0 → 1",
  );
  assert.match(
    ui.inspectionToast({
      inspection: {
        status: "healthy",
        tools: { count: 12 },
        resources: { count: 2, template_count: 1 },
      },
      changes: {
        tool_count: { before: 12, after: 12 },
        resource_count: { before: 2, after: 2 },
        template_count: { before: 1, after: 1 },
      },
    }).message,
    /无变化/,
  );
  assert.equal(
    ui.inspectionToast({
      inspection: {
        status: "degraded",
        resources: {
          error: {
            stage: "resources/list",
            type: "RuntimeError",
            message: "MCP resources/list failed (RuntimeError)",
          },
        },
      },
    }).message,
    "连接成功，但Resource 清单更新失败（RuntimeError）",
  );
  assert.equal(
    ui.inspectionToast({
      inspection: {
        status: "unreachable",
        error: {
          stage: "initialize",
          type: "TimeoutError",
          message: "MCP initialize timed out",
        },
      },
    }).message,
    "检测失败：MCP 初始化失败（TimeoutError）",
  );
});


test("工具明细保留快照顺序并区分未检测", () => {
  const ui = loadInspectionUi();
  const html = ui.renderMcpCatalog({tools: {snapshot_status: "stale", definitions: [{name: "z_first", description: "first"}, {name: "a_second"}]}});
  assert.ok(html.indexOf("z_first") < html.indexOf("a_second"));
  assert.match(html, /上次检测结果/);
  assert.match(ui.renderMcpCatalog({tools: {snapshot_status: "missing"}}), /请先检测/);
  assert.doesNotMatch(ui.renderInspectionCounts({tools: 2, resources: 1, templates: 5}, "test"), /模板/);
});


test("清单搜索覆盖描述和资源 URI，保持工具顺序", () => {
  const ui = loadInspectionUi();
  const catalog = {resources: {count: 2, snapshot_status: "ready", definitions: [{name: "one", uri: "docs://one", description: "订单"}, {name: "two", uri: "docs://two"}]}};
  assert.match(ui.renderMcpCatalog(catalog, "resources", "订单"), /docs:\/\/one/);
  assert.doesNotMatch(ui.renderMcpCatalog(catalog, "resources", "订单"), /docs:\/\/two/);
  assert.match(ui.renderMcpCatalog(catalog, "resources", "absent"), /没有匹配/);
  assert.match(ui.renderInspectionCounts({tools: 2, resources: 1}, "demo"), /data-catalog-kind="resources"/);
});
