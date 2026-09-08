import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { channelOriginPresentation } from "../../../console/session-display.js";

test("普通或旧会话没有渠道来源时不增加展示", () => {
  for (const value of [null, undefined, {}, "wecom", { channel_type: "wecom" }]) {
    assert.equal(channelOriginPresentation(value), null);
  }
});

test("私聊来源使用持久快照，缺名称时保留原 User ID", () => {
  const result = channelOriginPresentation({
    account_id: "account-1", account_name: "业务助手", channel_type: "wecom", peer_type: "single", peer_id: "user-7",
  });
  assert.equal(result.platform, "企微");
  assert.equal(result.peerKey, "User ID");
  assert.equal(result.peerName, "user-7");
  assert.equal(result.description, "业务助手 · 私聊 · User ID: user-7");
});

test("群聊来源同时提供 Chat ID 与收到的名称", () => {
  const result = channelOriginPresentation({
    account_id: "account-1", account_name: "业务助手", channel_type: "wecom", peer_type: "group", peer_id: "group-8", peer_metadata: { chat_id: "group-8", display_name: "项目组" },
  });
  assert.equal(result.peerKey, "Chat ID");
  assert.equal(result.peerId, "group-8");
  assert.equal(result.description, "业务助手 · 群聊 · Chat ID: group-8 · 项目组");
});

test("私聊来源自带用户和可选会话ID，不从运行记录反查", () => {
  const result = channelOriginPresentation({ channel_type: "wecom", peer_type: "single", peer_id: "user-7",
    peer_metadata: { user_id: "user-7", chat_id: "chat-9" } });
  assert.equal(result.peerId, "user-7");
  assert.equal(result.chatId, "chat-9");
  assert.equal(result.displayName, "");
  assert.equal(result.conversationType, "私聊");
});

test("渠道来源保留在会话列表、当前快照和详情，展示不绑定当前渠道指针", async () => {
  const chat = await readFile(new URL("../../../console/chat.js", import.meta.url), "utf8");
  const workspace = await readFile(new URL("../../../console/workspace.js", import.meta.url), "utf8");
  assert.match(chat, /channel: row\.channel \|\| null/);
  assert.match(chat, /channel: persisted\?\.channel \|\| null/);
  assert.match(chat, /channelOriginPresentation\(s\.channel\)/);
  assert.match(chat, /session-channel-source.*escapeHtml\(origin\.description\)/);
  assert.match(workspace, /channelOriginPresentation\(activeSession\.channel\)/);
  assert.match(workspace, /const channelSource = channelOrigin \?/);
  assert.match(workspace, /inspectorFactHtml\("渠道名称",[\s\S]*escapeHtml\(channelOrigin\.accountName\)/);
  assert.match(workspace, /inspectorFact\(channelOrigin\.conversationType, channelOrigin\.peerId,[^\n]*copy: true/);
  assert.match(workspace, /当前会话不是由渠道发起的。/);
  assert.match(workspace, /hasChannelTab[\s\S]*data-inspector-tab="channel"/);
  assert.match(workspace, /session\.attachmentCount, session\.channel/);
});
