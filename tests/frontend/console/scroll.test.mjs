import assert from "node:assert/strict";
import test from "node:test";
import { centerInScrollContainer } from "../../../console/scroll.js";

function pane(scrollTop = 600) {
  return { scrollTop, scrollLeft: 45, clientTop: 2, clientHeight: 500, scrollHeight: 3000,
    getBoundingClientRect: () => ({ top: 56 }) };
}
function row(top, height = 100) {
  return { getBoundingClientRect: () => ({ top, height }),
    scrollIntoView() { throw new Error("must not scroll ancestor panes or the page shell"); } };
}

test("centers a message within the bordered pane, preserving horizontal scroll", () => {
  const container = pane();
  centerInScrollContainer(container, row(1058));
  assert.equal(container.scrollTop, 1400);
  assert.equal(container.scrollLeft, 45);
  // Centering an already centered message must not move it again.
  centerInScrollContainer(container, row(258));
  assert.equal(container.scrollTop, 1400);
});

test("keeps the beginning of an oversized message visible and clamps at both ends", () => {
  const container = pane();
  centerInScrollContainer(container, row(1058, 900));
  assert.equal(container.scrollTop, 1600);
  centerInScrollContainer(container, row(-2000));
  assert.equal(container.scrollTop, 0);
  centerInScrollContainer(container, row(5000));
  assert.equal(container.scrollTop, 2500);
});

test("an absent focus row leaves the scroll position unchanged", () => {
  const container = pane();
  centerInScrollContainer(container, undefined);
  assert.equal(container.scrollTop, 600);
});
