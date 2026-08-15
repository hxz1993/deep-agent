import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const chatSource = await readFile(
  new URL("../../../frontend/console/chat.js", import.meta.url),
  "utf8",
);

test("Console 只把新名称识别为代码内置 UI 工具", () => {
  const builtinDeclaration = chatSource.match(/const BUILTIN_UI_TOOLS[^\n]+/)?.[0] || "";
  assert.match(
    builtinDeclaration,
    /BUILTIN_UI_TOOLS = new Set\(\["builtin__confirm", "builtin__choice"\]\)/,
  );
  assert.doesNotMatch(
    builtinDeclaration,
    /confirm_action|ask_user_choice/,
  );
  assert.match(
    chatSource,
    /LEGACY_BUILTIN_UI_TOOLS\.has\(call\.tool\) && !uiToolsByName\.has\(call\.tool\)/,
  );
});

test("choice 始终渲染自由输入并使用统一结果构造器", () => {
  assert.match(chatSource, /custom_label \|\| "其他（自行输入）"/);
  assert.match(chatSource, /custom_placeholder \|\| "请输入其他内容"/);
  assert.match(chatSource, /buildChoiceResult\(/);
});
