import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(
  new URL("../../../frontend/console/configuration/mcp.js", import.meta.url),
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

test("检测状态展示能力摘要和完整悬停信息", () => {
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
      tools: { count: 10 },
      resources: { count: 2, template_count: 1 },
    },
    "url",
  );

  assert.equal(view.text, "正常");
  assert.equal(
    view.summary,
    "10 工具 · 自动预选 Top 3 / 搜索 Top 5 · 2 资源 · 1 模板",
  );
  assert.match(view.title, /服务：mcp-biz-go 1.0.0/);
  assert.match(view.title, /协议：2025-06-18/);
  assert.match(view.title, /Instructions：可用/);
  assert.match(view.title, /耗时：428 ms/);
});

test("小型 MCP 直接披露全部工具", () => {
  const ui = loadInspectionUi();
  const view = ui.inspectionView(
    {
      status: "healthy",
      tools: { count: 2 },
      resources: { count: 0, template_count: 0 },
    },
    "url",
  );
  assert.equal(view.summary, "2 工具 · 全部直接披露 · 0 资源 · 0 模板");
});

test("Builtin 显示内置运行时且不伪装成待检测", () => {
  const ui = loadInspectionUi();
  const view = ui.inspectionView(null, "builtin");
  assert.equal(view.text, "内置运行时");
  assert.equal(view.summary, "运行时按需启动");
  assert.equal(ui.canDeleteMcp("builtin"), false);
  assert.equal(ui.canDeleteMcp("url"), true);
  assert.equal(ui.canDeleteMcp("stdio"), true);
});

test("历史 stdio MCP 明确显示为停止支持", () => {
  const ui = loadInspectionUi();
  const view = ui.inspectionView(null, "stdio");
  assert.equal(view.text, "已停止支持");
  assert.equal(view.summary, "请迁移为 URL MCP");
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
    "检测成功：工具 10 → 12，资源 0 → 2，模板 0 → 1",
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
