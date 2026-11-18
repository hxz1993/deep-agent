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
  assert.match(card.innerHTML, /&quot;A2A-Version&quot;: &quot;1\.0&quot;/);
  card.querySelector = () => ({ focus() {} });
  card.listeners.get("click")({ target: { closest: () => ({
    dataset: { a2aCardMode: "app" }, getAttribute: () => "false",
  }) } });
  assert.match(card.innerHTML, /\/apps\/\{app_id\}\/card/);
  assert.match(card.innerHTML, /&quot;A2A-Version&quot;: &quot;1\.0&quot;/);
  assert.doesNotMatch(card.innerHTML, /&quot;app&quot;:/);
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
  const task = { id: "sample", title: "Example", status: "COMPLETED" };
  pending[0].resolve({ items: [task], total: 1 });
  await refresh;
  ui.poll();
  assert.equal(pending.length, 2);
  assert.equal(pending[1].signal.aborted, false);
  pending[1].resolve({ ...task, message: [], files: [{ name: "output.xlsx" }] });
  await new Promise(resolve => setImmediate(resolve));
  assert.match(ui.element("agentTaskDetail").innerHTML, /output\.xlsx/);
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
