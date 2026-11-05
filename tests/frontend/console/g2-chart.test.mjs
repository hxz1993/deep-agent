import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";
import { Element } from "./helpers/presenter-dom.mjs";

const source = await readFile(new URL("../../../console/g2-chart.js", import.meta.url), "utf8");
const frameSource = await readFile(new URL("../../../console/g2-frame.js", import.meta.url), "utf8");
const themeSource = await readFile(new URL("../../../console/g2-theme.js", import.meta.url), "utf8");
const g2Source = await readFile(new URL("../../../console/vendor/g2-5.4.8.min.js", import.meta.url), "utf8");
const markdownSource = await readFile(new URL("../../../console/markdown.js", import.meta.url), "utf8");
const markedSource = await readFile(new URL("../../../console/vendor/marked.min.js", import.meta.url), "utf8");

function harness() {
  const listeners = new Map(), timers = new Map(), frames = [], observers = [];
  let ElementClass, nextTimer = 1;
  const document = {
    baseURI: "http://localhost:8000/console/",
    createElement(tag) {
      const el = new Element(tag); el.ownerDocument = document;
      if (tag === "iframe") { el.contentWindow = { messages: [], postMessage(message) { this.messages.push(message); } }; frames.push(el); }
      return el;
    },
  };
  class HTMLElement extends Element {
    constructor() { super("deepagent-g2-chart"); this.ownerDocument = document; this._connected = true; }
    get isConnected() { return this._connected; }
    get firstChild() { return this.childNodes[0] || null; }
  }
  const context = vm.createContext({
    console, TextEncoder, URL, Uint8Array, Promise, HTMLElement,
    document, customElements: { get: () => null, define(_name, value) { ElementClass = value; } },
    crypto: { getRandomValues(value) { for (let i = 0; i < value.length; i++) value[i] = i + nextTimer; return value; } },
    IntersectionObserver: class {
      constructor(callback) { this.callback = callback; this.active = true; observers.push(this); }
      observe(target) { this.target = target; }
      disconnect() { this.active = false; }
      visible() { this.callback([{ target: this.target, isIntersecting: true }]); }
    },
    addEventListener(type, callback) { listeners.set(type, new Set([...(listeners.get(type) || []), callback])); },
    removeEventListener(type, callback) { listeners.get(type)?.delete(callback); },
    setTimeout(callback) { const id = nextTimer++; timers.set(id, callback); return id; },
    clearTimeout(id) { timers.delete(id); },
  });
  context.window = context;
  vm.runInContext(source, context);
  return {
    context, frames, observers, listeners, timers,
    async create(spec = '{"type":"g2","config":{"type":"interval","data":[{"x":"甲","y":1}]}}', state = "pending") {
      const el = new ElementClass(); el.dataset.g2State = state;
      const code = document.createElement("code"); code.textContent = spec; el.appendChild(code);
      el.connectedCallback(); await Promise.resolve();
      return el;
    },
    visible() { observers.at(-1).visible(); },
    message(frame, type, { token, source: sender = frame.contentWindow } = {}) {
      const found = frame.srcdoc.match(/data-token="([^"]+)"/)[1];
      for (const callback of listeners.get("message") || []) callback({ source: sender, data: { channel: "deepagent-g2", type, token: token ?? found } });
    },
    disconnect(el) { el._connected = false; el.disconnectedCallback(); },
  };
}

test("G2 parser accepts wrapper/direct specs, financial layers, literal data and precision", () => {
  const { context } = harness(), parse = context.DeepAgentG2.parseSpec;
  const direct = { type: "view", data: [{ name: '<img src=x onerror="alert(1)">', value: 0.123456789, formatter: "ordinary data" }], children: [
    { type: "rect", data: [{ x: 1, y: 2 }] }, { type: "text", data: [{ text: "中文说明" }] }, { type: "link" }, { type: "point" },
  ] };
  assert.equal(JSON.stringify(parse(JSON.stringify(direct))), JSON.stringify(direct));
  assert.equal(JSON.stringify(parse(JSON.stringify({ type: "g2", config: direct }))), JSON.stringify(direct));
});

test("G2 parser rejects executable/remote/unsafe structures without treating ordinary data as code", () => {
  const parse = harness().context.DeepAgentG2.parseSpec;
  for (const source of ["null", "[]", "broken", '{"type":"g2","config":[]}', '{"type":"g2","config":{},"extra":1}',
    '{"type":"interval","data":{"type":"fetch","value":"https://example.test/a"}}',
    '{"type":"text","style":{"innerHTML":"<img src=x>"}}', '{"type":"interval","container":"#app"}',
    '{"type":"interval","interaction":{"tooltip":{"render":"return alert(1)"}}}',
    '{"type":"interval","data":[{"__proto__":{"polluted":true}}]}', '{"type":"interval","constructor":{}}',
    '{"type":"interval","data":[{"value":1e999}]}']) assert.throws(() => parse(source));
  assert.equal({}.polluted, undefined);
});

test("G2 parser enforces source bytes, depth, nodes, rows and layers", () => {
  const parse = harness().context.DeepAgentG2.parseSpec;
  assert.throws(() => parse(JSON.stringify({ text: "中".repeat(400000) })), /1 MB/);
  let deep = {}; for (let n = 0; n < 34; n++) deep = { nested: deep };
  assert.throws(() => parse(JSON.stringify(deep)), /嵌套过深/);
  assert.throws(() => parse(JSON.stringify({ data: [{ values: Array(50000).fill(1) }] })), /节点过多/);
  assert.throws(() => parse(JSON.stringify({ data: Array(10001).fill({ x: 1 }) })), /10,000 行/);
  assert.throws(() => parse(JSON.stringify({ type: "view", children: Array(1000).fill({ type: "line" }) })), /1,000/);
});

test("streaming charts never create observers or frames; completed charts wait for visibility", async () => {
  const h = harness();
  const stream = await h.create('{"unfinished":', "streaming");
  assert.equal(h.frames.length, 0); assert.equal(h.observers.length, 0);
  assert.match(stream.textContent, /图表生成中/);
  const complete = await h.create();
  assert.equal(h.frames.length, 0); assert.equal(h.observers.length, 1);
  h.visible(); assert.equal(h.frames.length, 1); assert.equal(complete.dataset.g2State, "loading");
  h.visible(); assert.equal(h.frames.length, 1);
  h.disconnect(stream); h.disconnect(complete);
});

test("chart/config switch shows one representation and reuses the same chart and source", async () => {
  const h = harness(), el = await h.create(), doc = el.ownerDocument;
  const config = doc.createElement("div"); config.className = "g2-chart-config";
  const code = el.querySelector("code"), original = code.textContent;
  config.appendChild(code); el.appendChild(config);
  for (const mode of ["chart", "config"]) {
    const button = doc.createElement("button"); button.setAttribute("data-g2-view", mode); el.appendChild(button);
  }
  el._setView("config"); h.visible();
  assert.equal(config.hidden, false); assert.equal(el._viewport.hidden, true); assert.equal(h.frames.length, 0);
  el._setView("chart");
  const frame = h.frames[0]; h.message(frame, "ready"); h.message(frame, "rendered");
  assert.equal(config.hidden, true); assert.equal(el._viewport.hidden, false);
  el._setView("config"); el._setView("chart");
  assert.equal(h.frames.length, 1); assert.equal(el.querySelector("iframe"), frame);
  assert.equal(code.textContent, original);
  assert.equal(el.querySelector('[data-g2-view="chart"]').getAttribute("aria-pressed"), "true");
  h.disconnect(el);
});

test("sandbox document has local nonced scripts and no model content or eval permission", async () => {
  const h = harness(), el = await h.create('{"type":"interval","data":[{"name":"</script><img src=x>"}]}');
  h.visible(); const frame = h.frames[0];
  assert.equal(frame.getAttribute("sandbox"), "allow-scripts");
  assert.doesNotMatch(frame.srcdoc, /allow-same-origin|unsafe-eval|<img src=x>/);
  assert.doesNotMatch(frame.srcdoc.match(/script-src ([^;]+);/)[1], /unsafe-inline/);
  assert.match(frame.srcdoc, /connect-src 'none'/);
  assert.match(frame.srcdoc, /img-src data: blob:/);
  assert.match(frame.srcdoc, /http:\/\/localhost:8000\/vendor\/g2-5\.4\.8\.min\.js/);
  assert.match(frame.srcdoc, /g2-theme\.js\?v=20261106-skill-theme/);
  assert.ok(frame.srcdoc.indexOf("g2-theme.js") < frame.srcdoc.indexOf("g2-frame.js"));
  assert.equal(frame.contentWindow.messages.length, 0);
  h.message(frame, "ready", { source: {} }); h.message(frame, "ready", { token: "wrong" });
  assert.equal(frame.contentWindow.messages.length, 0);
  h.message(frame, "ready"); h.message(frame, "ready");
  assert.equal(frame.contentWindow.messages.length, 1);
  assert.equal(frame.contentWindow.messages[0].spec.data[0].name, "</script><img src=x>");
  h.message(frame, "rendered"); assert.equal(el.dataset.g2State, "ready"); assert.equal(h.timers.size, 0);
  h.disconnect(el); assert.equal(h.listeners.get("message").size, 0);
});

test("disconnect cleans pending visibility and in-flight frames; late replies cannot resurrect them", async () => {
  const h = harness(), pending = await h.create();
  h.disconnect(pending); h.visible(); assert.equal(h.frames.length, 0);
  const mounted = await h.create(); h.visible(); const frame = h.frames[0];
  h.disconnect(mounted); h.message(frame, "ready"); h.message(frame, "rendered");
  assert.equal(mounted.querySelector("iframe"), null); assert.equal(frame.contentWindow.messages.length, 0);
  assert.equal(h.listeners.get("message").size, 0); assert.equal(h.timers.size, 0);
  assert.equal(mounted.querySelector("code").textContent.includes('"interval"'), true);
});

test("parse/render/timeout failures keep the original complete code available", async () => {
  const h = harness(), bad = await h.create("not valid JSON"); h.visible();
  assert.equal(h.frames.length, 0); assert.equal(bad.dataset.g2State, "error"); assert.equal(bad.querySelector("code").textContent, "not valid JSON");
  assert.match(bad.textContent, /不是有效的 JSON/);
  const failed = await h.create(); h.visible(); const frame = h.frames[0]; h.message(frame, "error");
  assert.equal(failed.dataset.g2State, "error"); assert.equal(failed.querySelector("iframe"), null);
  assert.match(failed.textContent, /图表渲染失败/); assert.ok(failed.querySelector("code"));
  const timed = await h.create(); h.visible(); for (const callback of [...h.timers.values()]) callback();
  assert.equal(timed.dataset.g2State, "error"); assert.match(timed.textContent, /加载超时/); assert.ok(timed.querySelector("code"));
});

test("Markdown final/stream contracts preserve copyable JSON and only allow the exact G2 element", () => {
  const context = vm.createContext({ console }); context.window = context;
  let options;
  context.DOMPurify = { addHook() {}, sanitize(html, settings) { options = settings; return html; } };
  vm.runInContext(markedSource, context); vm.runInContext(markdownSource, context);
  const text = '{"type":"interval","data":[{"x":"<script>","y":0.123456789}]}';
  const final = context.DeepAgentMarkdown.render(`\`\`\`g2\n${text}\n\`\`\``);
  assert.match(final, /<deepagent-g2-chart data-g2-state="pending">/);
  assert.match(final, /markdown-code-copy/); assert.match(final, /&lt;script&gt;/); assert.match(final, /0\.123456789/);
  assert.equal(JSON.stringify(options.ADD_TAGS), '["deepagent-g2-chart"]');
  const streaming = context.DeepAgentMarkdown.render(`\`\`\`g2\n${text}\n\`\`\``, { highlight: false });
  assert.match(streaming, /data-g2-state="streaming"/);
});

test("message replacement keeps the selected G2 view without changing its default", () => {
  const context = vm.createContext({ console }); context.window = context;
  vm.runInContext(markdownSource, context);
  let selected = "config";
  const card = { dataset: { g2View: "config" }, _setView(view) { selected = view; } };
  const block = {
    dataset: { codeLanguage: "g2" }, classList: { contains: () => false },
    closest: selector => selector === "deepagent-g2-chart" ? card : null,
    querySelector: selector => selector === "pre" ? { hidden: false, getAttribute: () => null } : null,
  };
  const root = { querySelectorAll: () => [block] };
  const saved = context.DeepAgentMarkdown.captureCodeState(root);
  assert.equal(saved.length, 1);
  assert.equal(saved[0].g2View, "config");
  selected = "chart";
  context.DeepAgentMarkdown.restoreCodeState(root, saved);
  assert.equal(selected, "config");
  card.dataset.g2View = "chart";
  assert.equal(context.DeepAgentMarkdown.captureCodeState(root).length, 0);
});

function frameHarness({ rendering } = {}) {
  const listeners = new Map(), messages = [], instances = [];
  const parent = { postMessage(data) { messages.push(data); } };
  class Chart {
    constructor(options) { this.initial = options; this.destroyed = 0; instances.push(this); }
    options(value) { this.spec = value; }
    render() { return rendering || Promise.resolve(); }
    destroy() { this.destroyed++; }
  }
  const context = vm.createContext({
    console, parent, G2: { Chart, Light: options => options, register() {} },
    document: { currentScript: { dataset: { token: "per-frame-token" } }, createElement: tag => new Element(tag), getElementById: () => new Element("div") },
    addEventListener(type, callback) { listeners.set(type, callback); },
  });
  context.window = context; vm.runInContext(themeSource, context); vm.runInContext(frameSource, context);
  return { context, messages, instances, listeners, init(spec, source = parent, token = "per-frame-token") { return listeners.get("message")({ source, data: { channel: "deepagent-g2", type: "init", token, spec } }); } };
}

test("frame authenticates initialization, fixes host size and safely renders precise tooltip text", async () => {
  const h = frameHarness();
  const spec = { type: "view", width: 99999, height: 88888, theme: { type: "classic" }, children: [{ type: "point", tooltip: { title: "category", items: [{ field: "actual", name: "实际" }] } }] };
  const original = JSON.stringify(spec);
  await h.init(spec, {}); await h.init(spec, h.context.parent, "wrong"); assert.equal(h.instances.length, 0);
  await h.init(spec); await h.init(spec); assert.equal(h.instances.length, 1);
  const options = h.instances[0].spec;
  assert.equal(options.width, undefined); assert.equal(options.height, 300); assert.equal(options.autoFit, true);
  assert.equal(options.children[0].tooltip.items[0].field, "actual");
  assert.equal(options.theme.type, "classic");
  assert.equal(JSON.stringify(spec), original);
  const root = options.interaction.tooltip.render(null, { title: '<img src=x onerror="bad()">', items: [{ name: "实际", value: 0.123456789 }] });
  assert.match(root.textContent, /<img src=x onerror="bad\(\)">/); assert.match(root.textContent, /0\.123456789/);
  assert.equal(root.querySelector("img"), null);
  assert.deepEqual(h.messages.map(item => item.type), ["ready", "rendered"]);
  h.listeners.get("pagehide")(); assert.equal(h.instances[0].destroyed, 1);
});

test("default and partial themes use DeepFOS while explicit themes, colors and input JSON stay intact", async () => {
  for (const theme of [undefined, { axis: { labelFontSize: 17 } }, "classicDark", { type: "classic", color: "#123456" }]) {
    const h = frameHarness();
    const spec = { type: "view", theme, children: [
      { type: "interval", style: { fill: "#ff0000" }, interaction: { tooltip: { shared: false } } },
      { type: "line", theme: { color: "#222222" }, style: { stroke: "#333333" } },
    ] };
    const original = JSON.stringify(spec);
    await h.init(spec);
    const options = h.instances[0].spec;
    assert.equal(options.theme.type ?? options.theme, typeof theme === "string" ? theme : theme?.type ?? "deepfos");
    if (theme?.axis) assert.equal(options.theme.axis.labelFontSize, 17);
    assert.equal(options.children[0].style.fill, "#ff0000");
    assert.equal(options.children[0].interaction.tooltip.shared, false);
    assert.equal(options.children[1].theme.type, "deepfos");
    assert.equal(options.children[1].theme.color, "#222222");
    assert.equal(options.children[1].style.stroke, "#333333");
    assert.equal(JSON.stringify(spec), original);
  }
});

test("generated theme uses real pinned G2 Light and preserves nested user overrides without changing defaults", () => {
  const context = vm.createContext({ window: {} });
  vm.runInContext(g2Source, context);
  context.window = context;
  const registrations = new Map(), register = context.G2.register;
  context.G2.register = (name, factory) => { registrations.set(name, factory); register(name, factory); };
  vm.runInContext(themeSource, context);
  const registered = registrations.get("theme.deepfos");
  assert.equal(typeof registered, "function");
  const defaults = registered();
  const tokens = context.DeepAgentG2Theme.tokens;
  assert.equal(defaults.color, tokens.colors.actual);
  assert.equal(JSON.stringify(defaults.category10), JSON.stringify(tokens.categoryColors));
  assert.equal(JSON.stringify(defaults.category20), JSON.stringify(tokens.categoryColors));
  assert.equal(defaults.axis.labelFontFamily, tokens.fontFamily);
  assert.equal(defaults.legendCategory.itemLabelFontFamily, tokens.fontFamily);
  assert.equal(defaults.label.fontFamily, tokens.fontFamily);
  assert.equal(defaults.title.titleFontSize, tokens.sizes.title);
  assert.equal(defaults.view.viewFill, tokens.colors.background);
  assert.equal(defaults.axis.gridStroke, tokens.colors.border);
  assert.equal(defaults.axis.titleSpacing, context.G2.Light().axis.titleSpacing);
  const changed = registered({ category10: ["#123456"], axis: { labelFontSize: 19 }, text: { text: { fill: "#abcdef" } } });
  assert.equal(JSON.stringify(changed.category10), '["#123456"]');
  assert.equal(changed.axis.labelFontSize, 19);
  assert.equal(changed.axis.labelFontFamily, tokens.fontFamily);
  assert.equal(changed.text.text.fill, "#abcdef");
  assert.equal(changed.text.text.fontFamily, tokens.fontFamily);
  assert.equal(registered().axis.labelFontSize, tokens.sizes.category);
  assert.equal(JSON.stringify(registered().category10), JSON.stringify(tokens.categoryColors));
});

test("frame release during asynchronous rendering destroys chart and suppresses stale success", async () => {
  let resolve; const rendering = new Promise(done => { resolve = done; });
  const h = frameHarness({ rendering }); const pending = h.init({ type: "line", data: [] });
  h.listeners.get("pagehide")(); resolve(); await pending;
  assert.equal(h.instances[0].destroyed, 1); assert.deepEqual(h.messages.map(item => item.type), ["ready"]);
});

test("frame rendering failure releases the chart and reports only its bounded failure state", async () => {
  const h = frameHarness({ rendering: Promise.reject(new Error("private renderer detail")) });
  await h.init({ type: "line", data: [] });
  assert.equal(h.instances[0].destroyed, 1);
  assert.deepEqual(h.messages.map(item => item.type), ["ready", "error"]);
  assert.doesNotMatch(JSON.stringify(h.messages), /private renderer detail/);
});
