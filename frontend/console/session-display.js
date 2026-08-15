export function formatSessionIdSubtitle(sessionId) {
  if (sessionId == null || sessionId === "") return "";
  return `会话 ID：${String(sessionId)}`;
}
