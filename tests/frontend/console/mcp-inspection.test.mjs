import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(
  new URL("../../../frontend/console/configuration/mcp.js", import.meta.url),
  "utf8",
);
const template = await readFile(
  new URL("../../../frontend/console/configuration-template.html", import.meta.url),
  "utf8",
);
const styles = await readFile(
  new URL("../../../frontend/console/console.css", import.meta.url),
  "utf8",
);

function loadInspectionUi() {
  const window = {};
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
    }),
  );
  return window.__McpInspectionUI;
}

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
  assert.match(template, /基本信息[\s\S]*连接与请求上下文/);
  ["mcpName", "mcpDescription", "mcpTransport", "mcpUrl", "mcpHeaders", "mcpRequestContextDescription"]
    .forEach((id) => assert.match(template, new RegExp(`id="${id}"`)));
  assert.doesNotMatch(template, /id="mcpEnabled"/);
  assert.doesNotMatch(template, /id="cancelMcpBtn"/);
  assert.match(styles, /dialog\.mcp-editor-page\.console-detail-dialog/);
  assert.match(styles, /\.mcp-editor-workbench\s*\{[^}]*grid-template-columns:/);
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

test("Builtin 显示内置运行时且不伪装成待检测", () => {
  const ui = loadInspectionUi();
  const view = ui.inspectionView(null, "builtin");
  assert.equal(view.text, "内置运行时");
  assert.deepEqual(JSON.parse(JSON.stringify(view.counts)), { tools: 0, resources: 0, templates: 0 });
  assert.equal(ui.canDeleteMcp("builtin"), false);
  assert.equal(ui.canDeleteMcp("url"), true);
  assert.equal(ui.canDeleteMcp("stdio"), true);
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
