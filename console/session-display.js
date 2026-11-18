export function formatSessionIdSubtitle(sessionId) {
  if (sessionId == null || sessionId === "") return "";
  return String(sessionId).slice(0, 8);
}

/** Read-only presentation of the Session's persisted channel origin snapshot. */
export function channelOriginPresentation(session) {
  const kind = String(session?.source_type || "internal").match(/^channel:(wecom_app|wecom|feishu|dingtalk)$/)?.[1];
  if (!kind) return null;
  const channel = { ...(session.channel || {}), channel_type: kind };

  const platform = channel.channel_type === "wecom_app" ? "企微应用" : channel.channel_type === "wecom" ? "企微" : channel.channel_type === "feishu" ? "飞书" : channel.channel_type === "dingtalk" ? "钉钉" : String(channel.channel_type);
  const peerKey = channel.peer_type === "group" ? "Chat ID" : "User ID";
  const metadata = channel.peer_metadata && typeof channel.peer_metadata === "object" ? channel.peer_metadata : {};
  const displayName = String(metadata.display_name || "");
  const peerName = displayName || String(channel.peer_id || "未记录");
  const conversationType = channel.peer_type === "group" ? "群聊" : channel.peer_type === "single" ? "私聊" : "聊天对象";
  const accountName = String(channel.account_deleted ? "账户已删除" : channel.account_name || "未记录");
  return {
    platform,
    kind,
    sourceId: String(session.source_id || "未记录"),
    logo: `./assets/channel-logos/${kind === "wecom_app" ? "wecom-app.svg" : kind === "wecom" ? "wecom.png" : kind === "feishu" ? "feishu.ico" : "dingtalk.svg"}`,
    peerKey,
    peerName,
    displayName,
    conversationType,
    chatId: String(metadata.chat_id || ""),
    peerId: String(channel.peer_id || "未记录"),
    accountName,
    description: `${accountName} · ${conversationType} · ${peerKey}: ${channel.peer_id || "未记录"}${displayName ? ` · ${peerName}` : ""}`,
  };
}

export function renderSessionSource(session) {
  const sourceType = session?.source_type;
  const channel = channelOriginPresentation(session);
  let source = '<img class="session-source-logo" src="./favicon.svg?v=20260922-d-rounded" alt="" />DeepFOS';
  if (channel) {
    source = `<img class="session-source-logo" src="${channel.logo}" alt="" />${channel.platform}`;
  } else if (sourceType === "a2a" || sourceType === "mcp") {
    source = `<span class="agent-protocol-logo" data-protocol="${sourceType}" aria-hidden="true"></span>${sourceType.toUpperCase()}`;
  } else if (sourceType === "http") {
    source = '<i class="session-source-logo" data-lucide="globe" aria-hidden="true"></i>HTTP';
  }
  return `<span class="inspector-channel-name">${source}</span>`;
}
