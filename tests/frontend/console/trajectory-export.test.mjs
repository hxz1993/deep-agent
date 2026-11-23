import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { createTrajectoryFileExport, fileExportEstimate, fileExportUnavailable, readExportHistory, trajectoryExportUrl } from "../../../console/trajectory-export.js";
import { buildTrajectory, createTrajectoryController } from "../../../console/trajectory.js";
import { trajectoryDomHarness } from "./trajectory-dom-harness.mjs";

const tick = () => new Promise(resolve => setImmediate(resolve));
const gate = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
const preview = (changes = {}) => ({ session_id: "s1", event_head_sequence: 10, snapshot_digest: "saved-snapshot",
  file_count: 2, upload_count: 1, artifact_count: 1, known_size_bytes: 1200, unknown_size_count: 1, can_export: true,
  limits: { max_trace_bytes: 1024 * 1024, max_total_bytes: 200 * 1024 * 1024 }, ...changes });
const zip = (status = "complete", included = 2, skipped = 0, mime = "application/zip") => new Response("PK fake zip", { headers: {
  "Content-Type": mime, "X-Deep-Agent-Export-Status": status,
  "X-Deep-Agent-Export-Included-Files": String(included), "X-Deep-Agent-Export-Skipped-Files": String(skipped),
} });
function harness(request = async () => Response.json(preview()), options = {}) {
  const state = { sessionId: "s1", agentId: "a1", readOnly: false, ready: true, running: false };
  const changes = [], calls = [], saved = [], notices = [];
  const trajectory = { format: "deepagent-session-trajectory", version: 1, session_id: "s1", events: [{ id: "first" }, { id: "second" }] };
  const view = Object.fromEntries(["open", "close", "loading", "reading", "estimate", "error", "packing", "availability"].map(name => [name, (...args) => changes.push([name, ...args])]));
  const controller = createTrajectoryFileExport({ getScope: () => state, getTrajectory: () => trajectory,
    request: (url, options) => { calls.push([url, options]); return request(url, options); }, url: path => `http://local/prefix/${path}`,
    view, saveBlob: (blob, filename) => saved.push([blob, filename]), notify: (...args) => notices.push(args), ...options });
  return { controller, state, trajectory, changes, calls, saved, notices };
}

test("ordinary and diagnostic export URLs preserve session scope and service prefix", () => {
  assert.equal(trajectoryExportUrl({ sessionId: "s/1", agentId: "a 1" }, "/preview"), "api/v1/sessions/s%2F1/trajectory-export/preview?agent_id=a+1");
  assert.equal(trajectoryExportUrl({ sessionId: "s1", agentId: "a1", readOnly: true }, "", true), "api/v1/diagnostics/sessions/s1/trajectory-export?include_files=true");
});

test("file export waits for a saved idle session and complete history", async () => {
  for (const patch of [{ sessionId: null }, { running: true }, { ready: false }]) {
    const h = harness(); Object.assign(h.state, patch);
    assert.ok(fileExportUnavailable(h.state)); await h.controller.open();
    assert.equal(h.calls.length, 0); assert.equal(h.notices.length, 1); h.controller.destroy();
  }
  const h = harness(); await h.controller.open(); assert.equal(h.calls.length, 1); h.controller.destroy();
});

test("unknown sizes are explicit and all-unknown files never appear as zero bytes", () => {
  const mixed = fileExportEstimate(preview());
  assert.match(mixed.title, /已知大小/); assert.match(mixed.unknown, /1 个文件大小未知/);
  const unknown = fileExportEstimate(preview({ known_size_bytes: 0, unknown_size_count: 2 }));
  assert.match(unknown.title, /大小未知/); assert.doesNotMatch(unknown.title, /0 B|约/);
  const empty = fileExportEstimate(preview({ file_count: 0, upload_count: 0, artifact_count: 0, known_size_bytes: 0, unknown_size_count: 0 }));
  assert.match(empty.title, /0 个文件，约 0 B/);
});

test("preview reads metadata once, validates identity and cannot accept an incomplete response", async () => {
  const h = harness(); await h.controller.open();
  assert.match(h.calls[0][0], /s1\/trajectory-export\/preview\?agent_id=a1$/);
  assert.equal(h.changes.filter(row => row[0] === "estimate").length, 1);
  h.controller.destroy();
  for (const patch of [{ session_id: "other" }, { snapshot_digest: "" }, { unknown_size_count: 3 }, { known_size_bytes: null }]) {
    const bad = harness(async () => Response.json(preview(patch))); await bad.controller.open();
    assert.match(bad.changes.find(row => row[0] === "error")[1], /不完整/); bad.controller.destroy();
  }
});

test("closing preview aborts only the export and ignores a late response", async () => {
  const delayed = gate(); const h = harness(() => delayed.promise);
  const pending = h.controller.open(); h.controller.close();
  assert.equal(h.calls[0][1].signal.aborted, true);
  delayed.resolve(Response.json(preview())); await pending;
  assert.equal(h.changes.some(row => row[0] === "estimate" || row[0] === "error"), false);
  assert.deepEqual(h.saved, []); h.controller.destroy();
});

test("switching session or Agent cancels an export and separate hosts remain isolated", async () => {
  for (const patch of [{ sessionId: "s2" }, { agentId: "a2" }, { running: true }]) {
    const delayed = gate(); const h = harness(() => delayed.promise);
    const pending = h.controller.open(); Object.assign(h.state, patch); h.controller.sync();
    assert.equal(h.calls[0][1].signal.aborted, true);
    delayed.resolve(Response.json(preview())); await pending;
    assert.equal(h.changes.some(row => row[0] === "estimate"), false); h.controller.destroy();
  }
  const one = harness(), two = harness(); await Promise.all([one.controller.open(), two.controller.open()]);
  one.controller.close(); assert.equal(two.changes.filter(row => row[0] === "close").length, 0);
  one.controller.destroy(); two.controller.destroy();
});

test("valid ZIP export posts the unchanged JSON plus both snapshot validators and downloads once", async () => {
  const h = harness(async (_url, options) => options.method === "POST" ? zip() : Response.json(preview()));
  const original = structuredClone(h.trajectory); await h.controller.open(); await h.controller.download();
  assert.equal(h.saved.length, 1); assert.equal(h.saved[0][1], "deepagent-trajectory-s1.zip");
  const [url, options] = h.calls[1]; assert.match(url, /agent_id=a1&include_files=true$/);
  assert.deepEqual(JSON.parse(options.body), { trajectory: original, event_head_sequence: 10, snapshot_digest: "saved-snapshot" });
  assert.deepEqual(h.trajectory, original); assert.match(h.notices[0][0], /2 个文件/); h.controller.destroy();
});

test("duplicate opens, retry clicks and download clicks do not duplicate requests", async () => {
  const delayed = gate(); const h = harness(async (_url, options) => options.method === "POST" ? delayed.promise : Response.json(preview()));
  await h.controller.open(); await h.controller.open(); assert.equal(h.calls.length, 1);
  const pending = h.controller.download(); await h.controller.download(); await h.controller.retry();
  assert.equal(h.calls.length, 2); delayed.resolve(zip()); await pending;
  assert.equal(h.saved.length, 1); h.controller.destroy();
});

test("cancel or destroy while packing or reading a Blob cannot trigger a download", async () => {
  for (const stage of ["packing", "blob"]) {
    const delayed = gate();
    const h = harness(async (_url, options) => options.method !== "POST" ? Response.json(preview()) : stage === "packing"
      ? delayed.promise : { ok: true, headers: zip().headers, blob: () => delayed.promise });
    await h.controller.open(); const pending = h.controller.download(); await tick();
    h.controller.destroy(); assert.equal(h.calls[1][1].signal.aborted, true);
    delayed.resolve(stage === "packing" ? zip() : new Blob(["zip"])); await pending;
    assert.equal(h.saved.length, 0); assert.equal(h.notices.length, 0);
  }
});

test("partial ZIP download names missing files explicitly", async () => {
  const h = harness(async (_url, options) => options.method === "POST" ? zip("partial", 1, 1) : Response.json(preview()));
  await h.controller.open(); await h.controller.download();
  assert.equal(h.saved.length, 1); assert.match(h.notices[0][0], /1 个文件未包含.*清单/); assert.equal(h.notices[0][1], true); h.controller.destroy();
});

test("HTTP errors, changed snapshots, wrong MIME and missing result headers are never downloaded", async () => {
  for (const result of [Response.json({ detail: "请重新预估" }, { status: 409 }), Response.json({ detail: "无权限" }, { status: 403 }), zip("complete", 2, 0, "text/html"), new Response("zip", { headers: { "content-type": "application/zip" } })]) {
    const h = harness(async (_url, options) => options.method === "POST" ? result : Response.json(preview()));
    await h.controller.open(); await h.controller.download();
    assert.equal(h.saved.length, 0); assert.ok(h.changes.some(row => row[0] === "error"));
    await h.controller.download(); assert.equal(h.calls.length, 2, "errors require an explicit fresh preview"); h.controller.destroy();
  }
});

test("preview failure can retry, but known limits disable download", async () => {
  let calls = 0;
  const h = harness(async () => ++calls === 1 ? Response.json({ detail: "预估失败" }, { status: 503 }) : Response.json(preview()));
  await h.controller.open(); await h.controller.retry(); assert.ok(h.changes.some(row => row[0] === "estimate")); h.controller.destroy();
  const limited = harness(async () => Response.json(preview({ can_export: false, message: "文件数量超过100个" })));
  await limited.controller.open(); await limited.controller.download(); assert.equal(limited.calls.length, 1); limited.controller.destroy();
});

test("JSON byte limits reject before packaging and serialization errors remain visible", async () => {
  const h = harness(async () => Response.json(preview({ limits: { max_trace_bytes: 10 } })));
  await h.controller.open(); await h.controller.download(); assert.equal(h.calls.length, 1);
  assert.match(h.changes.find(row => row[0] === "error")[1], /超过导出上限/); h.controller.destroy();
  const circular = harness(); circular.trajectory.self = circular.trajectory;
  await circular.controller.open(); await circular.controller.download();
  assert.match(circular.changes.find(row => row[0] === "error")[1], /序列化/); circular.controller.destroy();
});

test("the trace byte boundary excludes its request envelope and malformed preview JSON stays friendly", async () => {
  let h;
  h = harness(async (_url, options) => options.method === "POST" ? zip() : Response.json(preview({ limits: { max_trace_bytes: new Blob([JSON.stringify(h.trajectory)]).size } })));
  await h.controller.open(); await h.controller.download(); assert.equal(h.saved.length, 1); h.controller.destroy();
  const html = harness(async () => new Response("<html>login</html>"));
  await html.controller.open(); assert.match(html.changes.find(row => row[0] === "error")[1], /文件预估未返回有效数据/); html.controller.destroy();
});

test("ZIP captures the preview first, waits for complete events and posts the independent persisted projection", async () => {
  const ready = gate(), order = [];
  const saved = { format: "deepagent-session-trajectory", version: 1, source: "history", session_id: "s1", events: [{ id: "saved" }] };
  const h = harness(async (_url, options) => {
    order.push(options.method || "preview");
    return options.method === "POST" ? zip() : Response.json(preview());
  }, { prepareTrajectory: async (signal, value, scope) => {
    order.push("events"); assert.equal(value.snapshot_digest, "saved-snapshot"); assert.equal(scope.sessionId, "s1");
    assert.equal(signal.aborted, false); await ready.promise; return saved;
  } });
  const opening = h.controller.open(); await tick();
  assert.deepEqual(order, ["preview", "events"]); assert.ok(h.changes.some(row => row[0] === "reading"));
  await h.controller.download(); assert.equal(h.calls.length, 1, "cannot download until all event pages have completed");
  ready.resolve(); await opening; await h.controller.download();
  assert.deepEqual(JSON.parse(h.calls[1][1].body).trajectory, saved);
  assert.equal(h.trajectory.events[0].id, "first", "active JSON and trajectory remain unchanged"); h.controller.destroy();
});

test("closing, switching or starting a new Run while reading events aborts and ignores late calibration", async () => {
  for (const patch of [null, { sessionId: "s2" }, { agentId: "a2" }, { running: true }]) {
    const delayed = gate(); let signal;
    const h = harness(undefined, { prepareTrajectory: async incoming => { signal = incoming; await delayed.promise; return h.trajectory; } });
    const opening = h.controller.open(); await tick();
    if (patch) { Object.assign(h.state, patch); h.controller.sync(); } else h.controller.close();
    assert.equal(signal.aborted, true); delayed.resolve(); await opening;
    assert.equal(h.changes.some(row => row[0] === "estimate"), false); assert.equal(h.saved.length, 0); h.controller.destroy();
  }
});

test("incomplete events fail ZIP preparation and require explicit retry without packaging", async () => {
  const h = harness(undefined, { prepareTrajectory: async () => { throw new Error("轨迹未读完"); } });
  await h.controller.open(); await h.controller.download();
  assert.equal(h.calls.length, 1); assert.match(h.changes.find(row => row[0] === "error")[1], /轨迹未读完/);
  h.controller.destroy();
});

test("fresh export history reads every page in the same ordinary or diagnostic scope", async () => {
  for (const readOnly of [false, true]) {
    const calls = [], controller = new AbortController();
    const result = await readExportHistory({ sessionId: "s1", agentId: "a1", readOnly }, preview({ event_head_sequence: 3 }), {
      request: async (url, options) => { calls.push([url, options]); return Response.json(calls.length === 1
        ? { items: [{ sequence: 1 }, { sequence: 2 }], next_after_sequence: 2 }
        : { items: [{ sequence: 3 }], next_after_sequence: null }); }, signal: controller.signal,
    });
    assert.deepEqual(result.items.map(row => row.sequence), [1, 2, 3]); assert.equal(calls.length, 2);
    assert.match(calls[1][0], /after_sequence=2/); assert.equal(calls[0][1].signal, controller.signal);
    assert.equal(calls[0][0].includes("diagnostics/sessions/"), readOnly);
    assert.equal(calls[0][0].includes("agent_id=a1"), !readOnly);
  }
});

test("changed heads, invalid pagination, errors and aborted history never produce a ZIP snapshot", async () => {
  for (const page of [
    { items: [{ sequence: 1 }], next_after_sequence: null },
    { items: [{ sequence: 1 }, { sequence: 4 }], next_after_sequence: null },
    { items: [{ sequence: 1 }], next_after_sequence: 0 },
    { items: [{ sequence: 1 }, { sequence: 1 }], next_after_sequence: null },
    { items: [] },
  ]) {
    await assert.rejects(readExportHistory({ sessionId: "s1" }, preview({ event_head_sequence: 3 }), { request: async () => Response.json(page) }), /轨迹/);
  }
  await assert.rejects(readExportHistory({ sessionId: "s1" }, preview(), { request: async () => Response.json({ detail: "无权限" }, { status: 403 }) }), /无权限/);
  const abort = new AbortController(); abort.abort(); let requested = false;
  await assert.rejects(readExportHistory({ sessionId: "s1" }, preview(), { signal: abort.signal, request: async () => { requested = true; } }));
  assert.equal(requested, false);
});

test("ZIP history byte budget counts UTF-8 once per page and stops before further pagination", async () => {
  const first = { sequence: 1, content: "中文" }, second = { sequence: 2, content: "more" };
  const exact = new Blob([JSON.stringify([first, second])]).size;
  for (const budget of [exact, exact - 1]) {
    let calls = 0;
    const reading = readExportHistory({ sessionId: "s1" }, preview({ event_head_sequence: 2, limits: { max_trace_bytes: budget } }), {
      request: async () => Response.json(++calls === 1
        ? { items: [first], next_after_sequence: 1 }
        : { items: [second], next_after_sequence: null }),
    });
    if (budget === exact) assert.deepEqual((await reading).items, [first, second]);
    else await assert.rejects(reading, /轨迹 JSON 超过导出上限.*仅轨迹/);
    assert.equal(calls, 2);
  }
  let calls = 0;
  await assert.rejects(readExportHistory({ sessionId: "s1" }, preview({ limits: { max_trace_bytes: 8 } }), {
    request: async () => { calls++; return Response.json({ items: [first], next_after_sequence: 1 }); },
  }), /超过导出上限/);
  assert.equal(calls, 1, "an oversized page cannot trigger another request");
});

test("persisted structured tool arguments are redacted before generating the exported summary", () => {
  const rows = buildTrajectory({ items: [{ event_id: "fact", sequence: 1, event_type: "assistant/message", run_id: "run", turn_id: "turn", status: "completed",
    payload: { message: { kind: "response", parts: [{ part_kind: "tool-call", tool_name: "lookup", tool_call_id: "call", args: { api_key: "private-value", safe: 1 } }] } } }] });
  const call = rows.find(row => row.kind === "tool");
  assert.ok(call); assert.doesNotMatch(call.summary, /private-value/); assert.match(call.summary, /已隐藏/);
});

test("the ordinary JSON download keeps its v1 format, ordered events and redaction even during a Run", async () => {
  const oldDocument = globalThis.document, oldWindow = globalThis.window, oldCreate = URL.createObjectURL, oldRevoke = URL.revokeObjectURL;
  const dom = trajectoryDomHarness(); let captured, clicks = 0;
  globalThis.document = dom.document; globalThis.window = dom.window;
  const originalCreate = dom.document.createElement;
  dom.document.createElement = name => { const node = originalCreate(name); node.click = () => { clicks++; }; return node; };
  URL.createObjectURL = blob => { captured = blob; return "blob:export-test"; }; URL.revokeObjectURL = () => {};
  const controller = createTrajectoryController({ state: { sessionId: "s1", streaming: true } });
  try {
    controller.addUser({ content: "first" });
    controller.ingest({ event: "tool_call", data: { tool: "lookup", tool_call_id: "c1", args: { token: "secret", count: 2 } } });
    dom.element("exportTrajectoryBtn").listeners.get("click")();
    const result = JSON.parse(await captured.text());
    assert.equal(clicks, 1); assert.equal(result.format, "deepagent-session-trajectory"); assert.equal(result.version, 1);
    assert.equal(result.source, "history+sse"); assert.equal(result.events[0].kind, "input");
    assert.doesNotMatch(JSON.stringify(result), /secret/); assert.match(JSON.stringify(result), /已隐藏/);
    assert.equal(result.session_id, "s1");
  } finally { controller.destroy(); globalThis.document = oldDocument; globalThis.window = oldWindow; URL.createObjectURL = oldCreate; URL.revokeObjectURL = oldRevoke; }
});

test("shared menu has single-line compact rows and a bounded modal with cancellation and retry", async () => {
  const css = await readFile(new URL("../../../console/trajectory-export.css", import.meta.url), "utf8");
  assert.match(css, /width:\s*176px/); assert.match(css, /white-space:\s*nowrap/); assert.match(css, /width:\s*min\(500px/);
  const src = await readFile(new URL("../../../console/trajectory-export.js", import.meta.url), "utf8");
  assert.match(src, /data-export-cancel/); assert.match(src, /data-export-retry/); assert.match(src, /"hashchange"/);
});
