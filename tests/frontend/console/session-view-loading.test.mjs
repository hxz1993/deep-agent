import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import test from "node:test";

const source = await readFile(new URL("../../../console/session-view-shell.js", import.meta.url), "utf8");
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };

function template() {
  return {
    changed: false,
    cloneNode() { return template(); },
    querySelector(selector) {
      assert.equal(selector, ".app > .chat");
      return { cloneNode() { return { querySelector() { return { remove() {} }; } }; } };
    },
  };
}

function harness(fetchTemplate) {
  const calls = [], documents = [];
  const context = vm.createContext({
    URL,
    fetch: async url => { calls.push(String(url)); return fetchTemplate(url); },
    DOMParser: class {
      parseFromString(text, mime) {
        assert.equal(text, "pristine template"); assert.equal(mime, "text/html");
        const document = template(); documents.push(document); return document;
      }
    },
  });
  vm.runInContext(source.replace(/^export /gm, "").replaceAll("import.meta.url", '"https://console.example/gateway/agent/console/session-view-shell.js"'), context);
  return { context, calls, documents };
}

test("shared session markup stays lazy and concurrent readonly/workspace loads share one fetch", async () => {
  const gate = deferred(), h = harness(async () => { await gate.promise; return { ok: true, text: async () => "pristine template" }; });
  assert.equal(h.calls.length, 0);
  const readonly = h.context.ensureSessionViewTemplate();
  const workspace = h.context.loadSessionViewTemplate();
  assert.equal(h.calls.length, 1);
  assert.equal(new URL(h.calls[0]).pathname, "/gateway/agent/console/workspace-template.html");
  gate.resolve(); await readonly;
  const first = await workspace; first.changed = true;
  const next = await h.context.loadSessionViewTemplate();
  assert.notEqual(first, next); assert.equal(next.changed, false);
  assert.equal(h.documents[0].changed, false);
  await h.context.ensureSessionViewTemplate(); assert.equal(h.calls.length, 1);
  assert.ok(h.calls.every(url => !url.includes("/api/")));
});

test("template failures can retry without caching an incomplete document", async () => {
  let attempts = 0;
  const h = harness(async () => ++attempts === 1 ? { ok: false, status: 503 } : { ok: true, text: async () => "pristine template" });
  await assert.rejects(h.context.ensureSessionViewTemplate(), /HTTP 503/);
  await h.context.ensureSessionViewTemplate(); assert.equal(h.calls.length, 2);
  await h.context.loadSessionViewTemplate(); assert.equal(h.calls.length, 2);
});

test("an already registered workspace shell needs no readonly fetch", async () => {
  const h = harness(() => { throw Error("must not fetch"); });
  h.context.registerSessionViewTemplate(template());
  await h.context.ensureSessionViewTemplate(); assert.equal(h.calls.length, 0);
});
