import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const styles = await readFile(new URL("../../../console/console.css", import.meta.url), "utf8");
const detailStyles = await readFile(new URL("../../../console/diagnostic-detail.css", import.meta.url), "utf8");

test("工作台和只读诊断共用标题栏、操作和正文间距规则，不维护复制样式", () => {
  const scope = ":is(#workspaceLegacyHost, .diagnostic-session-detail)";
  for (const selector of [".chat-header", ".chat-title", ".chat-actions", ".workspace-header-action", ".workspace-header-action:hover", ".workspace-header-action.active", ".workspace-header-action svg", ".messages"]) {
    assert.ok(styles.includes(`${scope} ${selector} {`), `Missing shared presentation: ${selector}`);
  }
  assert.doesNotMatch(detailStyles, /\.diagnostic-detail-header[^{}]*\{/);
  assert.doesNotMatch(detailStyles, /\.messages\s*\{[^}]*padding:/);
  assert.match(styles, /:is\(#workspaceLegacyHost, \.diagnostic-session-detail\) \.chat-header\s*\{[^}]*min-height: 56px;[^}]*padding: 0 17px;/);
});
