import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(new URL("../../../console/chat.js", import.meta.url), "utf8");
function harness(fetch) {
  const merges = [];
  const urls = [];
  const context = vm.createContext({
    state: { agentId: "agent-a" }, URLSearchParams,
    serviceUrl: (url) => url,
    daFetch: (url) => { urls.push(url); return fetch(url); },
    renderSessions() {}, requestAnimationFrame() {},
    mergeSessionsFromBackend: (rows, options) => merges.push({ rows, options }),
  });
  vm.runInContext(source.slice(source.indexOf("let sessionPage ="), source.indexOf("function autoGrow()")), context);
  return { context, merges, urls };
}
const response = (items, total) => ({ ok: true, json: async () => ({ items, total }) });

test("按Agent分页追加，到达total后停止请求", async () => {
  let page = 0;
  const { context, merges, urls } = harness(async () => ++page === 1
    ? response(Array.from({ length: 50 }, (_, i) => ({ session_id: String(i) })), 51)
    : response([{ session_id: "50" }], 51));
  await context.syncSessionsFromBackend();
  await context.loadMoreSessions();
  await context.loadMoreSessions();
  assert.equal(urls.length, 2);
  assert.match(urls[0], /agent_id=agent-a&limit=50&offset=0/);
  assert.match(urls[1], /offset=50/);
  assert.equal(merges[0].options.append, false);
  assert.equal(merges[1].options.append, true);
});

test("重复触底不会重复请求，切Agent忽略旧请求", async () => {
  const pending = [];
  const { context, merges, urls } = harness(() => new Promise((resolve) => pending.push(resolve)));
  const first = context.syncSessionsFromBackend();
  await context.loadMoreSessions();
  assert.equal(urls.length, 1);
  context.state.agentId = "agent-b";
  const second = context.syncSessionsFromBackend();
  pending[1](response([{ session_id: "b" }], 1));
  await second;
  pending[0](response([{ session_id: "a" }], 1));
  await first;
  assert.equal(merges.length, 1);
  assert.equal(merges[0].options.agentId, "agent-b");
});

test("失败可重试且不会推进offset，空页停止加载", async () => {
  let attempts = 0;
  const { context, urls } = harness(async () => ++attempts === 1 ? { ok: false, status: 503 } : response([], 0));
  await context.syncSessionsFromBackend();
  await context.loadMoreSessions();
  await context.loadMoreSessions();
  assert.equal(urls.length, 2);
  assert.equal(urls[0], urls[1]);
});

test("不足一屏不显示到底提示，一屏历史在滚动后显示且不把页脚算作内容", async () => {
  const { context } = harness(async () => response([{ session_id: "1" }], 1));
  const footer = {};
  let rows = [{ offsetTop: 0, offsetHeight: 56 }];
  const list = {
    clientHeight: 300, scrollHeight: 100, scrollTop: 0,
    querySelector: () => footer,
    querySelectorAll(selector) {
      assert.equal(selector, ".session-item:not(.console-filtered)");
      return rows;
    },
  };
  context.el = { sessionList: list };
  await context.syncSessionsFromBackend();
  context.maybeLoadMoreSessions();
  assert.equal(footer.hidden, true);
  // Even an oversized footer cannot make one short history row fill the viewport.
  list.scrollHeight = 600;
  list.scrollTop = 50;
  context.maybeLoadMoreSessions();
  assert.equal(footer.hidden, true);
  await context.syncSessionsFromBackend();
  rows = Array.from({ length: 5 }, (_, index) => ({ offsetTop: index * 56, offsetHeight: 56 }));
  list.scrollTop = 0;
  context.maybeLoadMoreSessions();
  assert.equal(footer.hidden, true);
  // A 20px gap below the last row cannot fit another row: this is one full screen.
  list.scrollTop = 50;
  context.maybeLoadMoreSessions();
  assert.equal(footer.hidden, false);
  rows = [];
  context.maybeLoadMoreSessions();
  assert.equal(footer.hidden, true);
});
