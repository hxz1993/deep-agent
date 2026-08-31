import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import test from "node:test";

import {
  parseServerTimestamp,
  serverTimestampMilliseconds,
} from "../../../frontend/console/time.js";

test("时间解析兼容旧无时区值、Z 和显式偏移", () => {
  const expected = "2026-08-31T10:23:46.000Z";
  assert.equal(parseServerTimestamp("2026-08-31T10:23:46")?.toISOString(), expected);
  assert.equal(parseServerTimestamp("2026-08-31T10:23:46Z")?.toISOString(), expected);
  assert.equal(parseServerTimestamp("2026-08-31T18:23:46+08:00")?.toISOString(), expected);
  assert.equal(parseServerTimestamp("2026-08-31 10:23:46")?.toISOString(), expected);
  assert.equal(parseServerTimestamp("not-a-time"), null);
  assert.equal(serverTimestampMilliseconds("not-a-time"), 0);
});

function browserLocalHour(tz) {
  const moduleUrl = new URL("../../../frontend/console/time.js", import.meta.url).href;
  const source = `
    const { parseServerTimestamp } = await import(${JSON.stringify(moduleUrl)});
    const value = parseServerTimestamp("2026-08-31T10:23:46+00:00");
    process.stdout.write(JSON.stringify({ hour: value.getHours(), minute: value.getMinutes() }));
  `;
  return JSON.parse(execFileSync(
    process.execPath,
    ["--input-type=module", "--eval", source],
    { env: { ...process.env, TZ: tz }, encoding: "utf8" },
  ));
}

test("展示时区跟随浏览器运行环境而不是固定加八小时", () => {
  assert.deepEqual(browserLocalHour("Asia/Shanghai"), { hour: 18, minute: 23 });
  assert.deepEqual(browserLocalHour("America/Los_Angeles"), { hour: 3, minute: 23 });
});
