import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(new URL("../../../console/configuration/agents.js", import.meta.url), "utf8");
const channelsSource = await readFile(new URL("../../../console/configuration/channels.js", import.meta.url), "utf8");
const agent = { id: "agent-a", name: "测试助手", model: { name: "测试模型" }, capabilities: [], skills: [] };

function harness(fetcher) {
  const elements = Object.fromEntries(["agentsCardGrid", "agentsTableBody", "agentsListView", "panel-agents", "channelTableBody"].map((id) => [id, {
    innerHTML: "", classList: { toggle() {} }, setAttribute() {}, querySelectorAll() { return []; },
  }]));
  const requests = [];
  const context = vm.createContext({
    window: { DeepAgentConsoleAPI: { consoleUrl: (path) => path } },
    document: { addEventListener() {}, getElementById: (id) => elements[id], querySelectorAll: () => [] },
    apiFetch: async (path) => { requests.push(path); return fetcher(path); },
    escapeHtml: (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;"),
    formatDate: () => "2026/09/04", showDbBanner() {}, showToast() {},
  });
  vm.runInContext(source, context);
  return { context, elements, requests, admin: context.window.agentsAdmin };
}

test("首次加载 Agent 的卡片和列表都回显真实渠道关联，停用渠道仍有关联", async () => {
  const { admin, elements, requests } = harness((path) => path.endsWith("/agents") ? [agent] : [
    { agent_id: agent.id, name: "财务群", enabled: true },
    { agent_id: agent.id, name: '<业务 "机器人">', enabled: false },
    { agent_id: "other", name: "无关渠道" },
  ]);
  await admin.refresh();
  assert.deepEqual(requests.sort(), ["/api/v1/agents", "/api/v1/channel-accounts"]);
  for (const id of ["agentsCardGrid", "agentsTableBody"]) {
    const html = elements[id].innerHTML;
    assert.match(html, /class="agent-channel-badge"/);
    assert.match(html, /已关联渠道：财务群、&lt;业务 &quot;机器人&quot;&gt;/);
    assert.match(html, /data-lucide="radio"/);
    assert.match(html, /渠道 2/);
    assert.doesNotMatch(html, /无关渠道|<业务/);
  }
});

test("渠道权限或读取失败不阻断 Agent 列表，也不保留过期关联标记", async () => {
  let denied = false;
  const { admin, elements } = harness((path) => {
    if (path.endsWith("/agents")) return [agent];
    if (denied) throw new Error("HTTP 403：无权限");
    return [{ agent_id: agent.id, name: "原渠道" }];
  });
  await admin.refresh();
  denied = true;
  await admin.refresh();
  assert.match(elements.agentsCardGrid.innerHTML, /测试助手/);
  assert.doesNotMatch(elements.agentsCardGrid.innerHTML, /agent-channel-badge|原渠道/);
});

test("渠道改绑和删除刷新后，同步更新两个 Agent 视图且不依赖网关可达", async () => {
  let accounts = [{ agent_id: agent.id, name: "群机器人" }];
  const { admin, context, elements } = harness((path) => {
    if (path.endsWith("/agents")) return [agent, { ...agent, id: "agent-b", name: "第二助手" }];
    if (path.endsWith("/channel-runtime")) throw new Error("网关状态读取失败");
    return accounts;
  });
  await admin.refresh();
  vm.runInContext(channelsSource, context);
  accounts = [{ agent_id: "agent-b", name: "已改绑机器人" }];
  await context.window.channelsAdmin.refresh();
  for (const id of ["agentsCardGrid", "agentsTableBody"]) {
    const html = elements[id].innerHTML;
    assert.doesNotMatch(html.split('data-open-agent="agent-b"')[0], /agent-channel-badge/);
    assert.match(html.split('data-open-agent="agent-b"')[1], /已改绑机器人/);
  }
  accounts = [];
  await context.window.channelsAdmin.refresh();
  assert.doesNotMatch(elements.agentsCardGrid.innerHTML, /agent-channel-badge/);
  assert.doesNotMatch(elements.agentsTableBody.innerHTML, /agent-channel-badge/);
});
