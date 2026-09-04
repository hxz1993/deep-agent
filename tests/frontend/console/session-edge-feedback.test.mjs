import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(new URL("../../../frontend/console/chat.js", import.meta.url), "utf8");
const binding = source.slice(source.indexOf("function bindSessionListFeedback()"), source.indexOf("function autoGrow()"));

function harness() {
  let now = 0;
  let nextId = 0;
  let animations = 0;
  const timers = new Map();
  const handlers = {};
  const classes = new Set();
  const page = { hasMore: false, loading: false, error: false, hasScrolled: false };
  const media = { matches: false };
  const list = {
    scrollTop: 100, scrollHeight: 400, clientHeight: 300,
    classList: {
      contains: (name) => classes.has(name),
      add(name) { classes.add(name); animations += 1; },
      remove: (name) => classes.delete(name),
    },
    addEventListener(type, handler) { handlers[type] = handler; },
  };
  const context = vm.createContext({
    el: { sessionList: list }, sessionPage: page,
    updateSessionListFooter() {}, window: { matchMedia: () => media },
    ResizeObserver: class { observe() {} },
    setTimeout(callback, delay) { timers.set(++nextId, { callback, at: now + delay }); return nextId; },
    clearTimeout(id) { timers.delete(id); },
  });
  vm.runInContext(`${binding}\nbindSessionListFeedback();`, context);
  return {
    list, page, media, handlers, classes,
    count: () => animations,
    wheel: (deltaY, extra = {}) => handlers.wheel({ deltaY, deltaX: 0, ctrlKey: false, ...extra }),
    advance(ms) {
      const until = now + ms;
      while (true) {
        const next = [...timers].filter(([, value]) => value.at <= until).sort((a, b) => a[1].at - b[1].at)[0];
        if (!next) break;
        timers.delete(next[0]);
        now = next[1].at;
        next[1].callback();
      }
      now = until;
    },
  };
}

test("向上滚动在顶部或底部都不回弹，列表中部也不回弹", () => {
  const h = harness();
  h.wheel(-20);
  h.list.scrollTop = 0;
  h.wheel(-20);
  h.wheel(20);
  assert.equal(h.count(), 0);
  assert.equal(h.page.hasScrolled, true);
});

test("连续向下滚动及惯性尾段只轻弹一次，停止后下一次手势可再触发", () => {
  const h = harness();
  h.wheel(20);
  for (let i = 0; i < 12; i += 1) {
    h.advance(100);
    h.wheel(2);
  }
  assert.equal(h.count(), 1);
  assert.equal(h.classes.size, 0);
  assert.equal(h.list.scrollTop, 100);
  h.advance(351);
  h.wheel(20);
  assert.equal(h.count(), 2);
});

test("触摸每次手势最多一次，向上滚动方向不回弹", () => {
  const h = harness();
  const touch = (clientY) => ({ touches: [{ clientY }] });
  h.handlers.touchstart(touch(200));
  h.handlers.touchmove(touch(180));
  h.advance(500);
  h.handlers.touchmove(touch(160));
  assert.equal(h.count(), 1);
  h.handlers.touchend();
  h.handlers.touchstart(touch(200));
  h.handlers.touchmove(touch(220));
  assert.equal(h.count(), 1);
  h.handlers.touchcancel();
  h.handlers.touchstart(touch(200));
  h.handlers.touchmove(touch(180));
  assert.equal(h.count(), 2);
});

test("加载更多、失败、减少动画偏好以及非纵向滚动不触发", () => {
  for (const flag of ["hasMore", "loading", "error"]) {
    const h = harness();
    h.page[flag] = true;
    h.wheel(20);
    assert.equal(h.count(), 0);
  }
  const h = harness();
  h.media.matches = true;
  h.wheel(20);
  h.media.matches = false;
  h.wheel(20, { ctrlKey: true });
  h.wheel(20, { deltaX: 30 });
  assert.equal(h.count(), 0);
});

test("回弹幅度4px、200ms，只有一次偏移后回到原位", async () => {
  const css = await readFile(new URL("../../../frontend/console/console.css", import.meta.url), "utf8");
  assert.match(css, /animation: session-edge-bounce \.2s ease-out/);
  assert.match(css, /0%, 100% \{ transform: translateY\(0\); \}\s*40% \{ transform: translateY\(-4px\); \}/);
  assert.doesNotMatch(source, /--session-edge-shift/);
});
