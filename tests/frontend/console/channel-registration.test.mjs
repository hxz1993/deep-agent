import assert from "node:assert/strict";
import test from "node:test";
import vm from "node:vm";
import { readFile } from "node:fs/promises";
const source = await readFile(new URL("../../../console/configuration/channel-registration.js", import.meta.url), "utf8");
function harness(request, channelType = "feishu") {
  const timers = new Map(); let sequence = 0;
  const context = vm.createContext({ window: {}, AbortController,
    setTimeout(fn) { timers.set(++sequence, fn); return sequence; },
    clearTimeout(id) { timers.delete(id); },
  });
  vm.runInContext(source, context);
  const changes = [];
  const registration = context.window.createChannelRegistration({ request, channelType, onChange: data => changes.push(data) });
  return { registration, changes, timers, async tick() { const [id, fn] = [...timers][0]; timers.delete(id); await fn(); } };
}
test("二维码轮询授权后停止，仅暴露registration_id供保存", async () => {
  const calls = [];
  const h = harness(async (path, options = {}) => { calls.push({path, options}); return options.method === "POST"
    ? { registration_id: "r1", status: "waiting", verification_url: "https://open.feishu.cn/mock" }
    : { registration_id: "r1", status: "authorized", app_id: "cli_test" }; });
  await h.registration.start(); assert.equal(h.registration.authorizedId, null);
  await h.tick(); assert.equal(h.registration.authorizedId, "r1"); assert.equal(h.timers.size, 0);
  h.registration.reset({consumed: true}); assert.equal(h.registration.authorizedId, null);
  assert.equal(calls.filter(c => c.options.method === "DELETE").length, 0);
});
test("关闭时取消已创建注册并拒绝迟到轮询", async () => {
  let resolve; const calls = [];
  const h = harness(async (path, options = {}) => { calls.push({path, options});
    if (options.method === "POST") return {registration_id: "r1", status: "waiting"};
    if (options.method === "DELETE") return;
    return new Promise(r => {resolve = r;});
  });
  await h.registration.start(); const polling = h.tick();
  h.registration.reset(); assert.equal(calls[1].options.signal.aborted, true);
  resolve({registration_id: "r1", status: "authorized"}); await polling;
  assert.equal(h.registration.authorizedId, null); assert.equal(h.changes.at(-1).status, "waiting");
  assert.equal(calls[2].options.method, "DELETE"); assert.equal(h.timers.size, 0);
});
test("创建尚未返回时关闭，迟到创建被取消而非恢复二维码", async () => {
  let resolve; const calls = [];
  const h = harness((path, options) => { calls.push(options.method); return options.method === "POST"
    ? new Promise(r => {resolve = r;}) : Promise.resolve(); });
  const start = h.registration.start(); h.registration.reset();
  resolve({registration_id: "late", status: "waiting"}); await start;
  assert.deepEqual(calls, ["POST", "DELETE"]); assert.equal(h.timers.size, 0);
  assert.equal(h.changes.length, 1);
});
test("过期和错误结束轮询，重新开始丢弃旧注册", async () => {
  let posts = 0; const calls = [];
  const h = harness(async (path, options = {}) => { calls.push(options.method || "GET");
    if (options.method === "POST") return {registration_id: `r${++posts}`, status: "waiting"};
    if (options.method === "DELETE") return;
    return {registration_id: "r1", status: "expired"};
  });
  await h.registration.start(); await h.tick(); assert.equal(h.changes.at(-1).status, "expired");
  assert.equal(h.timers.size, 0); await h.registration.start();
  assert.deepEqual(calls, ["POST", "GET", "DELETE", "POST"]); h.registration.reset();
});
test("网络失败显示错误且不无限重试", async () => {
  const h = harness(async () => { throw new Error("offline"); });
  await h.registration.start(); assert.equal(h.changes.at(-1).status, "failed");
  assert.equal(h.changes.at(-1).error, "offline"); assert.equal(h.timers.size, 0);
});

test("创建超时解锁重试，迟到成功仍取消后台注册", async () => {
  let resolve; const calls = [];
  const h = harness((path, options) => { calls.push(options.method); return options.method === "POST"
    ? new Promise(r => {resolve = r;}) : Promise.resolve(); });
  const start = h.registration.start(); await h.tick();
  assert.equal(h.changes.at(-1).status, "failed");
  resolve({registration_id: "late-after-timeout", status: "waiting"}); await start;
  assert.deepEqual(calls, ["POST", "DELETE"]); assert.equal(h.registration.authorizedId, null);
});
test("授权拒绝后停止轮询，重复关闭只取消一次", async () => {
  const calls = [];
  const h = harness(async (path, options = {}) => { calls.push(options.method || "GET");
    if (options.method === "POST") return {registration_id: "r", status: "waiting"};
    if (options.method === "DELETE") return;
    return {registration_id: "r", status: "denied"};
  });
  await h.registration.start(); await h.tick();
  assert.equal(h.changes.at(-1).status, "denied"); assert.equal(h.timers.size, 0);
  h.registration.reset(); h.registration.reset();
  assert.equal(calls.filter(method => method === "DELETE").length, 1);
});

test("创建注册发送渠道名称快照，省略名称仍可发起", async () => {
  const payloads = [];
  const h = harness(async (path, options = {}) => {
    if (options.method === "POST") {
      payloads.push(JSON.parse(options.body));
      return { registration_id: "r", status: "authorized" };
    }
  });
  await h.registration.start({ appName: "  财务助手  " });
  await h.registration.start();
  assert.deepEqual(payloads, [{ app_name: "财务助手" }, { app_name: "" }]);
  h.registration.reset();
});

test("企微注册轮询使用独立端点，不发送不支持的名称预填，关闭取消句柄", async () => {
  const calls = [];
  const h = harness(async (path, options = {}) => {
    calls.push({ path, options });
    if (options.method === "POST") return { registration_id: "wecom-r1", status: "waiting", verification_url: "https://work.weixin.qq.com/ai/qc/c?s=fixture" };
    if (options.method === "DELETE") return;
    return { registration_id: "wecom-r1", status: "authorized", app_id: "bot-fixture" };
  }, "wecom");
  await h.registration.start({ appName: "不会预填" });
  assert.deepEqual(JSON.parse(calls[0].options.body), {});
  assert.equal(calls[0].path, "/api/v1/channel-accounts/wecom/registrations");
  await h.tick();
  assert.equal(h.registration.authorizedId, "wecom-r1");
  assert.equal(h.timers.size, 0);
  h.registration.reset();
  assert.equal(h.registration.authorizedId, null);
  assert.equal(calls.at(-1).path, "/api/v1/channel-accounts/wecom/registrations/wecom-r1");
  assert.equal(calls.at(-1).options.method, "DELETE");
});

test("等待扫码时改名重新生成，取消旧轮询且忽略迟到授权", async () => {
  let releaseOldPoll, posts = 0;
  const calls = [];
  const h = harness((path, options = {}) => {
    calls.push({ path, options });
    if (options.method === "POST") return Promise.resolve({ registration_id: `r${++posts}`, status: "waiting" });
    if (options.method === "DELETE") return Promise.resolve();
    return new Promise(resolve => { releaseOldPoll = resolve; });
  });
  await h.registration.start({ appName: "旧名称" });
  const oldPoll = h.tick();
  await h.registration.start({ appName: "  新名称  " });
  assert.equal(calls[1].options.signal.aborted, true);
  assert.equal(calls[2].options.method, "DELETE");
  assert.equal(calls[2].path.endsWith("/r1"), true);
  assert.deepEqual(JSON.parse(calls[3].options.body), { app_name: "新名称" });
  releaseOldPoll({ registration_id: "r1", status: "authorized" });
  await oldPoll;
  assert.equal(h.registration.authorizedId, null);
  assert.equal(h.changes.at(-1).registration_id, "r2");
  assert.equal(h.changes.at(-1).status, "waiting");
  assert.equal(h.timers.size, 1);
  h.registration.reset();
});
