export function formatSessionIdSubtitle(sessionId) {
  if (sessionId == null || sessionId === "") return "";
  return String(sessionId).slice(0, 8);
}

/** Read-only presentation of the Session's persisted channel origin snapshot. */
export function channelOriginPresentation(channel) {
  if (!channel || typeof channel !== "object" || !channel.channel_type || !channel.peer_id) return null;
  const platform = channel.channel_type === "wecom" ? "企微" : channel.channel_type === "feishu" ? "飞书" : String(channel.channel_type);
  const peerKey = channel.peer_type === "group" ? "Chat ID" : "User ID";
  const metadata = channel.peer_metadata && typeof channel.peer_metadata === "object" ? channel.peer_metadata : {};
  const displayName = String(metadata.display_name || "");
  const peerName = displayName || String(channel.peer_id);
  const conversationType = channel.peer_type === "group" ? "群聊" : "私聊";
  const accountName = String(channel.account_name || channel.account_id || platform);
  return {
    platform,
    peerKey,
    peerName,
    displayName,
    conversationType,
    chatId: String(metadata.chat_id || ""),
    peerId: String(channel.peer_id),
    accountName,
    description: `${accountName} · ${conversationType} · ${peerKey}: ${channel.peer_id}${displayName ? ` · ${peerName}` : ""}`,
  };
}
