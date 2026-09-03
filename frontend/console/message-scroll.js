export const MESSAGE_BOTTOM_FOLLOW_THRESHOLD = 48;

export function messageDistanceToBottom(container) {
  if (!container) return Infinity;
  return Math.max(0, container.scrollHeight - container.clientHeight - container.scrollTop);
}

export function shouldFollowMessages(container, threshold = MESSAGE_BOTTOM_FOLLOW_THRESHOLD) {
  return messageDistanceToBottom(container) <= threshold;
}
