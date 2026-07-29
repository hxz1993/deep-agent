import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { formatSessionIdSubtitle } from "../../../frontend/console/session-display.js";

const template = await readFile(
  new URL("../../../frontend/console/workspace-template.html", import.meta.url),
  "utf8",
);
const chatSource = await readFile(
  new URL("../../../frontend/console/chat.js", import.meta.url),
  "utf8",
);

test("会话标题第二行显示带前缀的完整会话 ID", () => {
  const sessionId = "019f1857-9463-7db3-a9d3-654a4ac24b24";
  assert.equal(formatSessionIdSubtitle(sessionId), `会话 ID：${sessionId}`);
  assert.equal(formatSessionIdSubtitle(null), "");
  assert.match(
    template,
    /id="chatTitle"[\s\S]*?class="session-badge session-subtitle" id="sessionBadge"/,
  );
  assert.match(
    chatSource,
    /sessionBadge\.textContent = formatSessionIdSubtitle\(state\.sessionId\)/,
  );
  assert.doesNotMatch(chatSource, /sessionBadge\.textContent = .*slice\(/);
});
