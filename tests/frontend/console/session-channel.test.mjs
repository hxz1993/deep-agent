import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { channelOriginPresentation } from "../../../console/session-display.js";
import { readPresentationSource } from "./shared-presentation-source.mjs";

test("普通或旧会话没有渠道来源时不增加展示", () => {
  for (const value of [null, undefined, {}, "wecom", { channel_type: "wecom" }]) {
    assert.equal(channelOriginPresentation(value), null);
  }
});

test("私聊来源使用持久快照，缺名称时保留原 User ID", () => {
  const result = channelOriginPresentation({ source_type: "channel:wecom", source_id: "account-1", channel: {
    account_id: "account-1", account_name: "业务助手", channel_type: "wecom", peer_type: "single", peer_id: "user-7",
  } });
  assert.equal(result.platform, "企微");
  assert.equal(result.peerKey, "User ID");
  assert.equal(result.peerName, "user-7");
  assert.equal(result.description, "业务助手 · 私聊 · User ID: user-7");
});

test("群聊来源同时提供 Chat ID 与收到的名称", () => {
  const result = channelOriginPresentation({ source_type: "channel:wecom", source_id: "account-1", channel: {
    account_id: "account-1", account_name: "业务助手", channel_type: "wecom", peer_type: "group", peer_id: "group-8", peer_metadata: { chat_id: "group-8", display_name: "项目组" },
  } });
  assert.equal(result.peerKey, "Chat ID");
  assert.equal(result.peerId, "group-8");
  assert.equal(result.description, "业务助手 · 群聊 · Chat ID: group-8 · 项目组");
});

test("私聊来源自带用户和可选会话ID，不从运行记录反查", () => {
  const result = channelOriginPresentation({ source_type: "channel:wecom", channel: { channel_type: "wecom", peer_type: "single", peer_id: "user-7",
    peer_metadata: { user_id: "user-7", chat_id: "chat-9" } } });
  assert.equal(result.peerId, "user-7");
  assert.equal(result.chatId, "chat-9");
  assert.equal(result.displayName, "");
  assert.equal(result.conversationType, "私聊");
});

test("渠道来源保留在会话列表、当前快照和详情，展示不绑定当前渠道指针", async () => {
  const chat = await readFile(new URL("../../../console/chat.js", import.meta.url), "utf8");
  const workspace = await readPresentationSource("workspace.js");
  assert.match(chat, /channel: row\.channel \|\| null/);
  assert.match(chat, /channel: persisted\?\.channel \|\| null/);
  assert.match(chat, /channelOriginPresentation\(s\)/);
  assert.match(chat, /session-channel-source.*escapeHtml\(origin\.description\)/);
  assert.match(workspace, /channelOriginPresentation\(activeSession\)/);
  assert.match(workspace, /const channelSource = channelOrigin \?/);
  assert.match(workspace, /inspectorFactHtml\("渠道名称",[\s\S]*escapeHtml\(channelOrigin\.accountName\)/);
  assert.match(workspace, /inspectorFact\(channelOrigin\.conversationType, channelOrigin\.peerId,[^\n]*copy: true/);
  assert.match(workspace, /当前会话不是由渠道发起的。/);
  assert.match(workspace, /hasChannelTab[\s\S]*data-inspector-tab="channel"/);
  assert.match(workspace, /session\.attachmentCount, session\.channel/);
});

test("来源列优先，空来源不从旧channel补造；删除账户仍显示渠道", () => {
  assert.equal(channelOriginPresentation({channel:{channel_type:"wecom",peer_id:"u"}}), null);
  const result = channelOriginPresentation({source_type:"channel:dingtalk",source_id:"gone",channel:{account_deleted:true}});
  assert.equal(result.platform,"钉钉");
  assert.equal(result.accountName,"账户已删除");
  assert.equal(result.sourceId,"gone");
  assert.equal(result.peerId,"未记录");
});

test("渠道提醒位于输入框外，聚焦只切显隐，内部不预留位置", async () => {
  const html = await readFile(new URL("../../../console/workspace-template.html", import.meta.url), "utf8");
  const css = await readFile(new URL("../../../console/workspace.css", import.meta.url), "utf8");
  assert.ok(html.indexOf('id="composerChannelSource"') < html.indexOf('id="composerDropZone"'));
  assert.match(css, /\.composer-channel-source \{[^}]*height: 34px/);
  assert.match(css, /\.composer-channel-source\[hidden\] \{ display: none/);
  assert.match(css, /\.composer:has\(\.composer-inner:focus-within\) \.composer-channel-warning \{ visibility: visible/);
});
