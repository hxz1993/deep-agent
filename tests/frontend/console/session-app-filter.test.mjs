import assert from "node:assert/strict";
import test from "node:test";
import { createSessionFilterOptionsController, sessionAppOptions, sessionAppQuery } from "../../../console/session-app-filter.js";

const data = (total = 3) => ({ agents: [{ agent_id: "a", session_count: total }],
  apps: [{ app_id: "budget001", app_name: "年度预算", session_count: total - 1 }], unassigned_count: 1, total });

test("App 查询范围始终显式跨App，未关联和指定App不改变调试范围", () => {
  assert.deepEqual(sessionAppQuery(), { app_scope: "all" });
  assert.deepEqual(sessionAppQuery("app:budget001"), { app_scope: "all", app_id: "budget001" });
  assert.deepEqual(sessionAppQuery("unassigned"), { app_scope: "unassigned" });
});

test("名称编码和真实统计回显；失败不能伪装为零，消失的选中App不隐式清空", () => {
  const ready = sessionAppOptions({ ...data(), status: "ready" });
  assert.deepEqual(ready.map(option => option.count), [3, 1, 2]);
  assert.equal(ready[2].label, "年度预算 · budget001");
  assert.ok(ready.every(option => option.icon));
  assert.equal(ready[2].separatorBefore, true);
  assert.ok(ready.slice(0, 2).every(option => !option.separatorBefore));
  const failed = sessionAppOptions({ status: "error" }, "app:budget001");
  assert.ok(failed.every(option => option.count === null));
  assert.equal(failed[2].value, "app:budget001");
  assert.equal(failed[2].separatorBefore, true);
  const deleted = sessionAppOptions({ ...data(), apps: [], status: "ready" }, "app:budget001");
  assert.equal(deleted[2].count, 0);
});

test("重复统计请求合并，切Agent取消旧请求，迟到响应不覆盖当前目录", async () => {
  const requests = [], changes = [];
  const controller = createSessionFilterOptionsController({
    fetchOptions: (id, signal) => new Promise(resolve => requests.push({ id, signal, resolve })),
    changed: snapshot => changes.push(snapshot),
  });
  const first = controller.refresh("a"), duplicate = controller.refresh("a");
  assert.equal(first, duplicate);
  const next = controller.refresh("b");
  assert.equal(requests[0].signal.aborted, true);
  requests[1].resolve(data(4)); await next;
  requests[0].resolve(data(9)); await first;
  assert.equal(changes.at(-1).agentId, "b");
  assert.equal(changes.at(-1).total, 4);
  controller.dispose();
});

test("写入后强制刷新，不接受操作前在途统计；错误可重新加载", async () => {
  const pending = [], changes = [];
  const controller = createSessionFilterOptionsController({
    fetchOptions: (_, signal) => new Promise((resolve, reject) => pending.push({ signal, resolve, reject })),
    changed: value => changes.push(value),
  });
  const old = controller.refresh("a");
  const current = controller.refresh("a", { force: true });
  assert.equal(pending[0].signal.aborted, true);
  pending[1].reject(new Error("503")); await current;
  assert.equal(changes.at(-1).status, "error");
  assert.equal(changes.at(-1).total, undefined);
  pending[0].resolve(data(99)); await old;
  const retry = controller.refresh("a"); pending[2].resolve(data(2)); await retry;
  assert.equal(changes.at(-1).total, 2);
  controller.dispose();
});

test("不完整统计响应报错，卸载后迟到响应不更新页面", async () => {
  const changes = []; let resolve;
  const controller = createSessionFilterOptionsController({
    fetchOptions: () => new Promise(done => { resolve = done; }), changed: value => changes.push(value),
  });
  let task = controller.refresh("a"); resolve({ agents: [], apps: [] }); await task;
  assert.equal(changes.at(-1).status, "error");
  task = controller.refresh("a"); const count = changes.length; controller.dispose(); resolve(data()); await task;
  assert.equal(changes.length, count);
});

test("目录中没有Agent时清除旧统计并取消在途请求", async () => {
  const changes = []; let resolve, signal;
  const controller = createSessionFilterOptionsController({
    fetchOptions: (_, currentSignal) => { signal = currentSignal; return new Promise(done => { resolve = done; }); },
    changed: value => changes.push(value),
  });
  const task = controller.refresh("deleted-agent");
  await controller.refresh(null);
  assert.equal(signal.aborted, true);
  resolve(data()); await task;
  assert.equal(changes.at(-1).status, "idle");
  assert.equal(changes.at(-1).agentId, null);
  assert.deepEqual(changes.at(-1).apps, []);
});
