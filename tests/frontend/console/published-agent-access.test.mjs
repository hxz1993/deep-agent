import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import { trajectoryDomHarness } from "./trajectory-dom-harness.mjs";

const script = readFileSync(new URL("../../../console/configuration/agent-service.js", import.meta.url), "utf8");
const publication = {
  agent_code: "reports", name: "Reports", enabled: true,
  scope: { apps: { mode: "all" } }, card_url: "https://api.test/card",
  app_card_url_template: "https://api.test/apps/{app_id}/card",
};
function setup(apiFetch) {
  const { window, document, element } = trajectoryDomHarness();
  document.addEventListener = (_, ready) => { document.ready = ready; };
  document.body = { dataset: { consolePage: "configuration" } };
  window.bindConsolePaneResize = () => {};
  element("panel-agent-service").className = "active";
  element("agentTaskRows").contains = element("agentTaskDetail").contains = () => false;
  element("agentTaskDetail").querySelector = () => ({ scrollTop: 0 });
  element("agentTaskApp").replaceChildren = (...options) => { element("agentTaskApp").options = options; };
  let tick;
  const mcpTab = element("fixtureMcpTab");
  mcpTab.dataset.serviceAccess = "mcp";
  element("agentServiceTabs").querySelectorAll = () => [mcpTab];
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
  const card = ui.element("agentServiceAccess");
  assert.match(card.innerHTML, /<h4 class="agent-service-access-title"[^>]*><span class="agent-protocol-logo" data-protocol="a2a"/);
  assert.match(card.innerHTML, /&quot;A2A-Version&quot;: &quot;1\.0&quot;/);
  card.querySelector = () => ({ focus() {} });
  card.listeners.get("click")({ target: { closest: () => ({
    dataset: { a2aCardMode: "app" }, getAttribute: () => "false",
  }) } });
  assert.match(card.innerHTML, /\/apps\/\{app_id\}\/card/);
  assert.match(card.innerHTML, /&quot;A2A-Version&quot;: &quot;1\.0&quot;/);
  assert.doesNotMatch(card.innerHTML, /&quot;app&quot;:/);
});

test("access panels distinguish missing deployment address from a failed configuration request", async () => {
  let clientConfigFails = false;
  const ui = setup(async path => {
    if (path.endsWith("/publications")) return [{ ...publication, card_url: null }];
    if (path.endsWith("/client-config")) {
      if (clientConfigFails) throw new Error("Unavailable");
      return { mcp_url: null };
    }
    if (path.includes("/tasks?")) return { items: [], total: 0 };
    return [];
  });
  await ui.refresh();
  const card = ui.element("agentServiceAccess");
  assert.match(card.innerHTML, /未配置 A2A 接入地址.*DEEP_AGENT_AGENT_SERVICE_PUBLIC_BASE_URL/);
  ui.element("fixtureMcpTab").listeners.get("click")();
  assert.match(card.innerHTML, /未配置 MCP 接入地址.*DEEP_AGENT_AGENT_SERVICE_PUBLIC_BASE_URL/);
  clientConfigFails = true;
  await ui.refresh();
  assert.match(card.innerHTML, /暂时无法读取 MCP 接入地址/);
  assert.doesNotMatch(card.innerHTML, /DEEP_AGENT_AGENT_SERVICE_PUBLIC_BASE_URL/);
});

test("MCP copy includes the app's space in the gateway headers", async () => {
  const scope = { apps: { mode: "selected", items: [{ app_id: "budget001", space_id: "budget" }] } };
  const ui = setup(async path => {
    if (path.endsWith("/publications")) return [{ ...publication, scope }];
    if (path.endsWith("/client-config")) return { mcp_url: "https://api.test/gateway/mcp" };
    if (path.includes("/tasks?")) return { items: [], total: 0 };
    return [];
  });
  await ui.refresh();
  ui.element("fixtureMcpTab").listeners.get("click")();
  const html = ui.element("agentServiceAccess").innerHTML;
  assert.match(html, /&quot;app&quot;: &quot;budget001&quot;/);
  assert.match(html, /&quot;space&quot;: &quot;budget&quot;/);
});

test("timer leaves slow list and detail requests in flight and resumes after delivery", async () => {
  const pending = [];
  const ui = setup(async (path, options) => {
    if (path.endsWith("/publications")) return [publication];
    if (path.includes("/tasks")) return new Promise(resolve => pending.push({ ...options, resolve }));
    return [];
  });
  const refresh = ui.refresh();
  await new Promise(resolve => setImmediate(resolve));
  ui.poll();
  assert.equal(pending.length, 1);
  assert.equal(pending[0].signal.aborted, false);
  const task = { id: "sample", title: "Example", status: "COMPLETED", source: "mcp" };
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
  pending[1].resolve({ ...task, message: [], files: [{ name: "output.xlsx" }] });
  await new Promise(resolve => setImmediate(resolve));
  assert.match(ui.element("agentTaskDetail").innerHTML, /output\.xlsx/);
  assert.match(ui.element("agentTaskDetail").innerHTML, /agent-protocol-logo" data-protocol="mcp"/);
  ui.poll();
  assert.equal(pending.length, 3);
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
