import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = await readFile(new URL("../../../console/ui-field-help.js", import.meta.url), "utf8");
const { initFieldHelp } = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);

function harness() {
  const events = new Map();
  const viewEvents = new Map();
  const view = { innerWidth: 360, innerHeight: 500,
    addEventListener: (name, fn) => viewEvents.set(name, fn),
    removeEventListener: (name) => viewEvents.delete(name),
  };
  const root = { defaultView: view,
    addEventListener: (name, fn) => events.set(name, fn),
    removeEventListener: (name) => events.delete(name),
  };
  const classes = new Set();
  const tooltip = { offsetWidth: 280, offsetHeight: 60, style: {}, open: false,
    classList: { add: name => classes.add(name), remove: name => classes.delete(name) },
    setAttribute() {}, matches() { return this.open; },
    showPopover() { this.open = true; }, hidePopover() { this.open = false; },
  };
  const trigger = { classList: { contains: () => false },
    querySelector: () => tooltip, closest() { return this; }, contains(target) { return target === this; },
    getBoundingClientRect: () => ({ left: 330, top: 8, bottom: 24, width: 14 }),
  };
  const cleanup = initFieldHelp(root);
  return { events, viewEvents, trigger, tooltip, classes, cleanup };
}

test("问号帮助在悬停和键盘焦点时进入顶层，并限制在视口内", () => {
  const { events, trigger, tooltip, classes, cleanup } = harness();
  events.get("focusin")({ target: trigger });
  assert.equal(tooltip.open, true);
  assert.equal(tooltip.style.left, "68px");
  assert.equal(tooltip.style.top, "32px");
  let prevented = 0;
  let stopped = 0;
  const escape = { key: "Escape", preventDefault() { prevented += 1; }, stopPropagation() { stopped += 1; } };
  events.get("keydown")(escape);
  assert.equal(prevented, 1);
  assert.equal(stopped, 1);
  events.get("keydown")(escape);
  assert.equal(prevented, 1, "closed tooltip leaves Escape available to its dialog");
  assert.equal(stopped, 1);
  assert.equal(tooltip.open, false);
  assert.equal(classes.has("is-open"), false);
  events.get("pointerover")({ target: trigger });
  assert.equal(tooltip.open, true);
  events.get("pointerout")({ target: trigger, relatedTarget: {} });
  assert.equal(tooltip.open, false);
  cleanup();
  assert.equal(events.size, 0);
});

test("问号帮助在离开焦点、关闭弹框、滚动与调整窗口后收起", () => {
  const { events, viewEvents, trigger, tooltip, cleanup } = harness();
  for (const action of [
    () => events.get("focusout")({ target: trigger, relatedTarget: null }),
    () => events.get("close")(),
    () => events.get("scroll")(),
    () => viewEvents.get("resize")(),
  ]) {
    events.get("focusin")({ target: trigger });
    action();
    assert.equal(tooltip.open, false);
  }
  cleanup();
  assert.equal(viewEvents.size, 0);
});
