import assert from "node:assert/strict";
import test from "node:test";
import { bindChannelResizer, channelWidthBounds } from "../../../console/channel-resizer.js";

test("inspector respects desktop caps and preserves room for the table", () => {
  assert.deepEqual(channelWidthBounds(2800), { min: 320, max: 720 });
  assert.deepEqual(channelWidthBounds(1000), { min: 320, max: 540 });
  assert.deepEqual(channelWidthBounds(740), { min: 280, max: 280 });
});
test("drag and keyboard share bounds; cleanup releases capture and observer", () => {
  const previous = globalThis.ResizeObserver;
  let resize, disconnected = false, captured = false, hostWidth = 1400;
  const handlers = {}, attributes = {}, changes = [];
  globalThis.ResizeObserver = class { constructor(fn) { resize = fn; } observe() {} disconnect() { disconnected = true; } };
  const handle = {
    setAttribute: (key, value) => { attributes[key] = value; },
    addEventListener: (key, value) => { handlers[key] = value; },
    removeEventListener: key => { delete handlers[key]; },
    setPointerCapture() { captured = true; }, hasPointerCapture: () => captured,
    releasePointerCapture() { captured = false; },
  };
  const host = { querySelector: () => handle, getBoundingClientRect: () => ({ width: hostWidth }), style: { setProperty() {} } };
  let dispose;
  try {
    dispose = bindChannelResizer(host, 420, width => changes.push(width));
    handlers.pointerdown({ button: 0, clientX: 900, pointerId: 1, preventDefault() {} });
    handlers.pointermove({ clientX: -900 });
    assert.equal(attributes["aria-valuenow"], 720);
    handlers.pointermove({ clientX: 1900 });
    assert.equal(attributes["aria-valuenow"], 320);
    handlers.keydown({ key: "End", preventDefault() {} });
    assert.equal(changes.at(-1), 720);
    hostWidth = 960; resize();
    assert.equal(attributes["aria-valuenow"], 500);
    dispose(); dispose = null;
    assert.equal(captured, false); assert.equal(disconnected, true);
    assert.deepEqual(Object.keys(handlers), []);
  } finally { dispose?.(); globalThis.ResizeObserver = previous; }
});
