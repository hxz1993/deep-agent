import assert from "node:assert/strict";
import test from "node:test";

import {
  messageDistanceToBottom,
  shouldFollowMessages,
} from "../../../frontend/console/message-scroll.js";

test("消息吸底只在用户接近底部时保持", () => {
  const container = { scrollHeight: 1000, clientHeight: 400, scrollTop: 552 };
  assert.equal(messageDistanceToBottom(container), 48);
  assert.equal(shouldFollowMessages(container), true);
  container.scrollTop = 500;
  assert.equal(shouldFollowMessages(container), false);
});

test("用户回到底部后可重新启用吸底", () => {
  const container = { scrollHeight: 1000, clientHeight: 400, scrollTop: 596 };
  assert.equal(shouldFollowMessages(container, 4), true);
  container.scrollTop = 595;
  assert.equal(shouldFollowMessages(container, 4), false);
});
