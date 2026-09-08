import assert from "node:assert/strict";
import test from "node:test";

import { copyTextToClipboard } from "../../../console/message-actions.js";

test("用户消息复制仅向Clipboard写入可见正文", async () => {
  const written = [];
  await copyTextToClipboard("请分析这组数据", {
    async writeText(text) {
      written.push(text);
    },
  });
  assert.deepEqual(written, ["请分析这组数据"]);
});

test("Clipboard不可用时返回明确失败", async () => {
  await assert.rejects(
    () => copyTextToClipboard("内容", null),
    /Clipboard API is unavailable/,
  );
});
