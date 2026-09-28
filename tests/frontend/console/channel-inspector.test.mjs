import { savedRunPresentation, sessionStatusLabels, renderContextReferenceContent } from "../../../console/session-presentation.js";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";
import { channelOriginPresentation } from "../../../console/session-display.js";
import { renderSessionInspector } from "../../../console/session-inspector.js";

const source = await readFile(new URL("../../../console/workspace.js", import.meta.url), "utf8");

function inspector({ accounts = [], channel = null, tab = "channel", extraState = {} } = {}) {
  const host = { innerHTML: "", querySelectorAll: () => [] };
  const state = {
    agentId: "agent-1", sessionId: "session-1", llmModels: [], agents: [{ id: "agent-1", name: "Agent" }],
    sessions: [{ id: "session-1", agentId: "agent-1", channel, source_type: channel ? "channel:wecom" : "internal", source_id: channel ? "account-1" : null }],
  };
  Object.assign(state, extraState);
  const context = vm.createContext({
    window: { DeepAgentConsoleChat: { state } },
    document: { getElementById: () => host, querySelector: () => null },
    channelOriginPresentation, renderSessionInspector, accounts, activeTab: tab,
    savedRunPresentation, sessionStatusLabels, renderContextReferenceContent,
  });
  vm.runInContext(source.replace(/^import .*;\n/gm, "").replaceAll("import.meta.url", '"http://localhost/console/workspace.js"').replace(/^export /gm, ""), context);
  vm.runInContext("workspaceChannelAccounts = accounts; inspectorActiveTab = activeTab; renderInspector();", context);
  return host.innerHTML;
}

const account = { id: "account-1", name: "Current bot", channel_type: "wecom", agent_id: "agent-1" };
const origin = { account_name: "Original bot", channel_type: "wecom", peer_type: "group", peer_id: 'chat<&"1' };

test("无渠道的Agent保留三页签并从渠道页退回概览", () => {
  const html = inspector({ accounts: [{ ...account, agent_id: "other" }] });
  assert.equal((html.match(/role="tab"/g) || []).length, 3);
  assert.doesNotMatch(html, /data-inspector-tab="channel"/);
  assert.match(html, /data-inspector-tab="session" aria-selected="true"/);
});

test("关联渠道的Agent显示第四页签但不伪造普通会话来源", () => {
  const html = inspector({ accounts: [account] });
  assert.equal((html.match(/role="tab"/g) || []).length, 4);
  assert.match(html, /data-inspector-tab="session"[\s\S]*data-inspector-tab="channel"[\s\S]*data-inspector-tab="run"/);
  assert.match(html, /当前会话不是由渠道发起的/);
  assert.match(html, /Current bot/);
});

test("历史来源独立于当前Agent绑定，ID转义并可复制", () => {
  const html = inspector({ channel: origin, accounts: [account] });
  assert.match(html, /Original bot/);
  assert.doesNotMatch(html, /Current bot/);
  assert.match(html, /data-inspector-copy="chat&lt;&amp;&quot;1"/);
  assert.match(html, /aria-label="复制 Chat ID"/);
  assert.match(html, /data-inspector-copy="session-1"/);
  assert.match(inspector({ channel: origin }), /data-inspector-tab="channel"/);
  const overview = inspector({ channel: origin, tab: "session" });
  assert.doesNotMatch(overview, /Original bot|chat&lt;/);
  assert.match(overview, /data-inspector-copy="session-1"/);
});


test("概览模型图标取实际Run Provider，不跟随当前选择模型", () => {
  const html = inspector({ tab: "session", extraState: {
    llmModelId: "new-model", llmModels: [{ id: "new-model", name: "other", provider: { provider_type: "openai" } }],
    lastRunMetrics: { run_id: "r", model_name: "deepseek-flash", provider_name: "deepseek" },
  } });
  assert.match(html, /data-provider-type="deepseek"/);
  assert.doesNotMatch(html, /data-provider-type="openai"/);
});

test("未开始会话采用所选模型Provider，未知历史Provider不伪造", () => {
  const html = inspector({ tab: "session", extraState: { sessionId: null, llmModelId: "m",
    llmModels: [{ id: "m", name: "model", provider: { provider_type: "moonshotai" } }],
  } });
  assert.match(html, /data-provider-type="moonshotai"/);
  const unknown = inspector({ tab: "session", extraState: { lastRunMetrics: { model_name: "unknown" } } });
  assert.doesNotMatch(unknown, /data-provider-type=/);
});


test("历史模型精确ID关联Provider类型优先于自定义名称", () => {
  const html = inspector({ tab: "session", extraState: {
    llmModels: [{ id: "historical-model", name: "same-name", provider: { name: "GPT", provider_type: "openai" } }],
    lastRunMetrics: { run_id: "r", model_id: "historical-model", model_name: "same-name", provider_name: "GPT" },
  } });
  assert.match(html, /data-provider-type="openai"/);
  assert.doesNotMatch(html, /data-provider-type="openai_compatible"/);
});
