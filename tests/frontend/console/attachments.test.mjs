import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const chatSource = await readFile(
  new URL("../../../frontend/console/chat.js", import.meta.url),
  "utf8",
);
const agentSource = await readFile(
  new URL("../../../frontend/console/configuration/agents.js", import.meta.url),
  "utf8",
);
const template = await readFile(
  new URL("../../../frontend/console/workspace-template.html", import.meta.url),
  "utf8",
);

test("附件上传不再要求可配置的附件工具包", () => {
  assert.match(agentSource, /builtin:\s*"内置工具"/);
  assert.match(agentSource, /return toolPackage\.category/);
  assert.match(
    agentSource,
    /GROUP_ORDER\s*=\s*\["builtin",\s*"mcp",\s*"ui"\]/,
  );
  assert.doesNotMatch(agentSource, /GROUP_ORDER[^;]*"skill"/);
  assert.match(chatSource, /read_file 系统工具按需分页读取/);
  assert.doesNotMatch(agentSource, /builtin:attachment/);
});

test("控制台从后端读取 MIME、数量与大小限制", () => {
  assert.match(chatSource, /state\.uploadCapabilities\.allowed_mimes/);
  assert.match(chatSource, /allowed\.includes\("text\/plain"\)/);
  assert.match(chatSource, /accept\.push\("\.log"\)/);
  assert.match(chatSource, /el\.fileInput\.accept = accept\.join\(","\)/);
  assert.match(chatSource, /state\.uploadCapabilities\?\.max_count/);
  assert.match(chatSource, /state\.uploadCapabilities\?\.max_size_mb/);
  assert.match(chatSource, /file\.size > maxSizeMb \* 1024 \* 1024/);
});

test("新附件请求不再把前端抽取文本写入聊天载荷", () => {
  const sendPayloadBlock = chatSource.match(
    /payload\.attachments = attachments\.map\([\s\S]*?\}\)\);/,
  )?.[0] || "";
  assert.ok(sendPayloadBlock);
  assert.doesNotMatch(sendPayloadBlock, /extracted_text/);
  assert.match(template, /\.docx/);
  assert.match(template, /\.xlsx/);
  assert.match(template, /\.log/);
  assert.match(template, /\.mp4/);
});
