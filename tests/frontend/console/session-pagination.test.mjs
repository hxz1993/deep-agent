import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(new URL("../../../console/chat.js", import.meta.url), "utf8");
function harness(fetch) {
  const merges = [];
  const urls = [];
  const requests = [];
  const search = { value: "" };
  const context = vm.createContext({
    state: { agentId: "agent-a" }, URLSearchParams, AbortController, setTimeout, clearTimeout,
    document: { getElementById: () => search },
    el: { sessionList: { scrollTop: 0 } },
    serviceUrl: (url) => url,
    daFetch: (url, options) => { urls.push(url); requests.push(options); return fetch(url); },
    renderSessions() {}, requestAnimationFrame() {},
    mergeSessionsFromBackend: (rows, options) => merges.push({ rows, options }),
  });
  vm.runInContext(source.slice(source.indexOf("let sessionPage ="), source.indexOf("function autoGrow()")), context);
  return { context, merges, urls, requests, search };
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

test("检索在后端分页，完整ID原样发送；清空回到首个未筛选页", async () => {
  const { context, urls, search } = harness(async () => response([{ session_id: "exact-ID" }], 1));
  search.value = "  exact-ID  ";
  await context.syncSessionsFromBackend();
  assert.equal(new URLSearchParams(urls[0].split("?")[1]).get("query"), "exact-ID");
  search.value = "";
  await context.syncSessionsFromBackend();
  assert.equal(new URLSearchParams(urls[1].split("?")[1]).has("query"), false);
  assert.match(urls[1], /offset=0/);
});

test("新检索立即取消旧页，防抖前禁止触底请求，迟到结果不会覆盖", async () => {
  const pending = [];
  const { context, merges, urls, requests, search } = harness(() => new Promise((resolve) => pending.push(resolve)));
  const first = context.syncSessionsFromBackend();
  search.value = "销售";
  context.scheduleSessionSearch({ isComposing: true });
  assert.equal(requests[0].signal.aborted, true);
  await context.loadMoreSessions();
  assert.equal(urls.length, 1);
  pending[0](response([{ session_id: "old" }], 1));
  await first;
  assert.equal(merges.length, 0);
  const next = context.syncSessionsFromBackend();
  pending[1](response([{ session_id: "new" }], 1));
  await next;
  assert.equal(merges.length, 1);
  assert.equal(merges[0].rows[0].session_id, "new");
});

test("连续输入仅发最后一次请求且检索分页保留query", async () => {
  const { context, urls, search } = harness(async () => response([{ session_id: "match" }], 2));
  search.value = "销";
  context.scheduleSessionSearch({});
  search.value = "销售";
  context.scheduleSessionSearch({});
  await new Promise((resolve) => setTimeout(resolve, 280));
  assert.equal(urls.length, 1);
  assert.equal(new URLSearchParams(urls[0].split("?")[1]).get("query"), "销售");
  await context.loadMoreSessions();
  assert.equal(new URLSearchParams(urls[1].split("?")[1]).get("query"), "销售");
  assert.match(urls[1], /offset=1/);
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
