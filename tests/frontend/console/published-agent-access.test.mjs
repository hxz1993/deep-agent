import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import { trajectoryDomHarness } from "./trajectory-dom-harness.mjs";

const script = readFileSync(new URL("../../../console/configuration/agent-service.js", import.meta.url), "utf8");
const prefix = "https://gateway.example.test/deepfos-server/deep-agent-server1-0/";
const publication = {
  agent_code: "reports", name: "Reports", enabled: true,
  scope: { apps: { mode: "all" } },
};
function setup(apiFetch) {
  const { window, document, element } = trajectoryDomHarness();
  document.addEventListener = (_, ready) => { document.ready = ready; };
  document.body = { dataset: { consolePage: "configuration" } };
  window.bindConsolePaneResize = () => {};
  window.serviceUrl = path => new URL(path.replace(/^\/+/, ""), prefix).href;
  element("panel-agent-service").className = "active";
  element("agentTaskRows").contains = element("agentTaskDetail").contains = () => false;
  element("agentTaskDetail").querySelector = () => ({ scrollTop: 0 });
  element("agentTaskApp").replaceChildren = (...options) => { element("agentTaskApp").options = options; };
  let tick;
  const mcpTab = element("agentServiceTabMcp");
  const a2aTab = element("agentServiceTabA2a");
  mcpTab.dataset.serviceAccess = "mcp";
  a2aTab.dataset.serviceAccess = "a2a";
  element("agentServiceTabs").querySelectorAll = () => [mcpTab, a2aTab];
  vm.runInNewContext(script, {
    window, document, apiFetch, AbortController, URLSearchParams, clearInterval,
    Option: class { constructor(label, value) { this.value = value; } },
    formatDate: String, escapeHtml: value => String(value ?? ""),
    setInterval: callback => { tick = callback; },
  });
  document.ready();
  return { refresh: window.agentServiceAdmin.refresh, element, poll: () => tick() };
}

test("both A2A card modes supply copyable headers for RPC", async () => {
  const ui = setup(async path => {
    if (path.endsWith("/publications")) return [publication];
    if (path.includes("/tasks?")) return { items: [], total: 0 };
    return [];
  });
  await ui.refresh();
  ui.element("agentServiceTabA2a").listeners.get("click")();
  const card = ui.element("agentServiceAccess");
  assert.match(card.innerHTML, /https:\/\/gateway\.example\.test\/deepfos-server\/deep-agent-server1-0\/api\/v1\/agent-service\/a2a\/reports\/agent-card\.json/);
  assert.match(card.innerHTML, /<h4 class="agent-service-access-title"[^>]*><span class="agent-protocol-logo" data-protocol="a2a"/);
  assert.match(card.innerHTML, /&quot;A2A-Version&quot;: &quot;1\.0&quot;/);
  assert.match(card.innerHTML, /&quot;app&quot;: &quot;<App ID>&quot;/);
  assert.match(card.innerHTML, /&quot;space&quot;: &quot;<Space ID>&quot;/);
  assert.match(card.innerHTML, /aria-label="复制地址">复制<\/button>/);
  assert.match(card.innerHTML, /aria-label="复制 Header 示例">复制<\/button>/);
  card.querySelector = () => ({ focus() {} });
  card.listeners.get("click")({ target: { closest: () => ({
    dataset: { a2aCardMode: "app" }, getAttribute: () => "false",
  }) } });
  assert.match(card.innerHTML, /\/apps\/\{app_id\}\/agent-card\.json/);
  assert.match(card.innerHTML, /&quot;A2A-Version&quot;: &quot;1\.0&quot;/);
  assert.doesNotMatch(card.innerHTML, /&quot;app&quot;:/);
});

test("disabled publication hides the Card but leaves the environment MCP address available", async () => {
  const ui = setup(async path => {
    if (path.endsWith("/publications")) return [{ ...publication, enabled: false }];
    if (path === "/api/v1/agents") return [];
    assert.match(path, /\/tasks\?/);
    return { items: [], total: 0 };
  });
  await ui.refresh();
  const card = ui.element("agentServiceAccess");
  assert.equal(ui.element("agentServiceTabMcp").getAttribute("aria-selected"), "true");
  assert.ok(card.innerHTML.includes(`${prefix}api/v1/agent-service/mcp`));
  ui.element("agentServiceTabA2a").listeners.get("click")();
  assert.match(card.innerHTML, /该发布已停用/);
  assert.doesNotMatch(card.innerHTML, /agent-card\.json/);
  ui.element("agentServiceTabMcp").listeners.get("click")();
  assert.ok(card.innerHTML.includes(`${prefix}api/v1/agent-service/mcp`));
});

test("MCP config and guide keep placeholders across publication changes", async () => {
  const scope = { apps: { mode: "selected", items: [{ app_id: "budget001", space_id: "budget" }] } };
  let current = { ...publication, scope };
  const ui = setup(async path => {
    if (path.endsWith("/publications")) return [current];
    if (path.includes("/tasks?")) return { items: [], total: 0 };
    return [];
  });
  await ui.refresh();
  const html = ui.element("agentServiceAccess").innerHTML;
  assert.equal(ui.element("agentTaskPublicationName").textContent, "Reports");
  assert.match(html, /<h5>配置示例<\/h5>[\s\S]*aria-label="复制配置">复制<\/button>/);
  assert.match(html, /&quot;app&quot;: &quot;<App ID>&quot;/);
  assert.match(html, /&quot;space&quot;: &quot;<Space ID>&quot;/);
  assert.match(html, /&quot;X-Deepfos-Key&quot;: &quot;<API Key>&quot;/);
  assert.doesNotMatch(html, /budget001/);
  assert.doesNotMatch(html, /agent-service-publication-name/);

  ui.element("agentServiceProtocol").open = true;
  current = { ...publication, agent_code: "recon", name: "Reconciliation", scope: {
    apps: { mode: "selected", items: [{ app_id: "recon001", space_id: "recon" }] },
  } };
  await ui.refresh();
  const guide = ui.element("agentServiceMcpGuideSetup").innerHTML;
  assert.match(guide, /"app": "<App ID>"/);
  assert.match(guide, /"space": "<Space ID>"/);
  assert.doesNotMatch(guide, /budget001|recon001/);
  assert.equal(ui.element("agentTaskPublicationName").textContent, "Reconciliation");
  assert.equal(ui.element("agentServiceAccess").innerHTML, html);
});

test("timer leaves slow requests in flight and task result uses the latest accepted output of its last turn", async () => {
  const pending = [], replyRequests = [];
  const attempt = (turn_id, text, accepted = true) => ({
    turn_id, status: "completed",
    payload: { name: "publication_result", accepted, output: { status: "completed", reply: { text } } },
  });
  const ui = setup(async (path, options) => {
    if (path.endsWith("/publications")) return [publication];
    if (path.includes("/tasks")) return new Promise(resolve => pending.push({ ...options, resolve }));
    if (path.startsWith("/api/v1/diagnostics/sessions/")) {
      replyRequests.push(path);
      return { items: [
        attempt("turn-1", "前一轮追问"),
        attempt("turn-2", "实际结果" + "结果".repeat(90) + "不展示的长回复尾部"),
        attempt("turn-2", "未接受的候选", false),
        attempt("turn-3", "另一个任务的回复"),
      ] };
    }
    return [];
  });
  const refresh = ui.refresh();
  await new Promise(resolve => setImmediate(resolve));
  ui.poll();
  assert.equal(pending.length, 1);
  assert.equal(pending[0].signal.aborted, false);
  const task = { id: "sample-task-id", title: "Example", status: "COMPLETED", source: "mcp" };
  pending[0].resolve({ items: [task], total: 1 });
  await refresh;
  assert.equal(pending.length, 1);
  const taskRows = ui.element("agentTaskRows");
  assert.match(taskRows.innerHTML, /aria-pressed="false"/);
  assert.match(ui.element("agentTaskDetail").innerHTML, /请选择一条任务查看详情/);
  const row = ui.element("fixtureTaskRow");
  const button = ui.element("fixtureTaskButton");
  row.dataset.agentTask = task.id;
  row.parentElement = { children: [row] };
  row.querySelector = () => button;
  taskRows.listeners.get("click")({ target: { closest: () => row } });
  assert.equal(button.getAttribute("aria-pressed"), "true");
  assert.ok(row.classList.contains("selected"));
  ui.poll();
  assert.equal(pending.length, 2);
  assert.equal(pending[1].signal.aborted, false);
  pending[1].resolve({ ...task, session_id: "session-1", context_id: "context-1", session_title: "预算核对会话",
    message: [],
    progress: ["SUBMITTED", "WORKING", "INPUT_REQUIRED", "SUBMITTED", "WORKING", "COMPLETED"].map((status, index) => ({
      status, timestamp: `2026-11-21T01:00:0${index}Z`,
    })),
    files: ["output.xlsx", "report.pdf", "chart.png", "extra.csv"].map(name => ({ name })), turns: [
      { id: "turn-1", event_id: "event-1", summary: "核对预算" + "甲".repeat(140) },
      { id: "turn-2", event_id: "event-2", summary: "十一月" },
    ],
  });
  await new Promise(resolve => setImmediate(resolve));
  const detail = ui.element("agentTaskDetail").innerHTML;
  assert.match(detail, /<h3 title="Example">Example<\/h3>\s*<div class="session-subtitle-row">[\s\S]*?<code title="sample-task-id">sample-t<\/code>/);
  assert.match(detail, /data-task-copy="sample-task-id" data-copy-label="Task ID" data-detail-control="copy-heading:Task ID:sample-task-id"/);
  assert.match(detail, /任务结果<\/h4>\s*<div class="agent-service-result-body">/);
  assert.deepEqual(replyRequests, ["/api/v1/diagnostics/sessions/session-1/events?turn_id=turn-2&event_type=output%2Fattempt"]);
  assert.match(detail, /<p>核对预算甲{92}…<\/p>/);
  assert.doesNotMatch(detail, /前一轮追问|未接受的候选|另一个任务的回复/);
  assert.match(detail, /agent-service-turns/);
  assert.match(detail, /2 轮/);
  assert.match(detail, /event_id=event-1[\s\S]*核对预算[\s\S]*event_id=event-2[\s\S]*十一月/);
  assert.match(detail, /class="channel-extension-heading">任务轮次<\/h4>/);
  assert.match(detail, /<dt>Task ID<\/dt><dd>/);
  assert.match(detail, /agent-service-turn-facts">\s*<div><dt>Turn ID<\/dt><dd>/);
  assert.match(detail, /<dt>Context ID<\/dt><dd>[\s\S]*?<\/dd><\/div>\s*<div class="agent-service-session-fact"><dt>Session ID<\/dt><dd>/);
  assert.match(detail, /查看 Session ID[\s\S]*?<dd class="agent-service-session-summary">预算核对会话<\/dd>/);
  assert.doesNotMatch(detail, /会话名称|排队中|进行中|不展示的长回复尾部|extra\.csv/);
  assert.match(detail, /待补材料[\s\S]*?agent-service-status" data-state="COMPLETED">已完成/);
  assert.match(detail, /agent-service-result-preview">\s*<p>实际结果[^<]+…<\/p>\s*<span class="agent-task-file-count" role="img" aria-label="4 个文件"/);
  assert.doesNotMatch(detail, /任务进度|回复摘要|交付产物|output\.xlsx|report\.pdf|chart\.png/);
  assert.match(ui.element("agentTaskDetail").innerHTML, /agent-protocol-logo" data-protocol="mcp"/);
  ui.poll();
  assert.equal(pending.length, 3);
});

test("an accepted structured-only reply keeps the empty-text placeholder and file count", async () => {
  const task = { id: "structured-task", title: "Structured", status: "TASK_STATE_COMPLETED", source: "a2a" };
  const ui = setup(async path => {
    if (path.endsWith("/publications")) return [publication];
    if (path.includes("/tasks?")) return { items: [task], total: 1 };
    if (path.endsWith("/tasks/structured-task")) return {
      ...task, session_id: "session-2", turns: [{ id: "only-turn" }], message: ["已完成状态说明"], artifact_count: 1,
    };
    if (path.startsWith("/api/v1/diagnostics/sessions/")) return { items: [{
      turn_id: "only-turn", status: "completed",
      payload: { name: "publication_result", accepted: true, output: { status: "completed", reply: { data: { total: 15 } } } },
    }] };
    return [];
  });
  await ui.refresh();
  const row = ui.element("fixtureTaskRow");
  row.dataset.agentTask = task.id;
  row.parentElement = { children: [row] };
  row.querySelector = () => ui.element("fixtureTaskButton");
  ui.element("agentTaskRows").listeners.get("click")({ target: { closest: () => row } });
  await new Promise(resolve => setImmediate(resolve));
  const detail = ui.element("agentTaskDetail").innerHTML;
  assert.match(detail, /agent-service-result-empty">无回复内容/);
  assert.match(detail, /aria-label="1 个文件"/);
  assert.doesNotMatch(detail, /已完成状态说明/);
});

test("an earlier directory refresh cannot overwrite the latest publications", async () => {
  const requests = [];
  const ui = setup(async path => {
    if (path.endsWith("/publications")) return new Promise(resolve => requests.push(resolve));
    if (path.includes("/tasks?")) return { items: [], total: 0 };
    return [];
  });
  const older = ui.refresh();
  const newer = ui.refresh();
  requests[1]([{ ...publication, name: "Updated reports" }]);
  await newer;
  requests[0]([publication]);
  await older;
  assert.match(ui.element("agentPublicationRows").innerHTML, /Updated reports/);
});

test("publication counts include unselected and empty publications and ignore table filters", async () => {
  const counts = { reports: 24, budgets: 4, empty: 0 };
  const ui = setup(async path => {
    if (path.endsWith("/publications")) {
      return Object.keys(counts).map(agent_code => ({ ...publication, agent_code }));
    }
    const match = path.match(/\/publications\/([^/]+)\/tasks/);
    if (!match) return [];
    const filtered = path.includes("state=TASK_STATE_WORKING");
    return { items: [], total: filtered ? 1 : counts[match[1]] };
  });
  await ui.refresh();
  for (const [code, total] of Object.entries(counts)) {
    const cell = ui.element(`agentPublicationTaskCount-${code}`);
    assert.equal(cell.textContent, total ? String(total) : "—");
    assert.equal(cell.dataset.empty, String(total === 0));
  }
  const state = ui.element("agentTaskState");
  state.value = "TASK_STATE_WORKING";
  state.listeners.get("change")();
  await new Promise(resolve => setImmediate(resolve));
  assert.match(ui.element("agentTaskPage").textContent, /共 1 条任务/);
  assert.equal(ui.element("agentPublicationTaskCount-reports").textContent, "24");
});
