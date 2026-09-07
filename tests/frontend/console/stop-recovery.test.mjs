import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { boundedStreamRequest, verifyStreamStop } from '../../../frontend/console/stop-recovery.js';

const response = (body) => ({ ok: true, json: async () => body });
const options = (request) => ({ request, isCurrent: () => true, requestTimeoutMs: 15, verificationTimeoutMs: 40, pollIntervalMs: 1 });

test('cancel_requested is not success; only backend idle closes a hanging stream', async () => {
  const calls = [];
  const result = await verifyStreamStop(options(async (kind) => {
    calls.push(kind);
    return response({ status: kind === 'cancel' ? 'cancel_requested' : 'idle' });
  }));
  assert.equal(result.status, 'idle');
  assert.deepEqual(calls, ['cancel', 'status']);
});

test('a running backend has a bounded wait and permits retry', async () => {
  const result = await verifyStreamStop(options(async () => response({ status: 'cancel_requested' })));
  assert.equal(result.status, 'retry');
});

test('a hanging cancel request times out instead of permanently disabling stop', async () => {
  let signal;
  const result = await verifyStreamStop(options(async (_, requestSignal) => {
    signal = requestSignal;
    return new Promise(() => {});
  }));
  assert.equal(result.status, 'retry');
  assert.equal(signal.aborted, true);
});

test('a hanging response body is bounded as well as the fetch', async () => {
  const result = await verifyStreamStop(options(async () => ({ ok: true, json: () => new Promise(() => {}) })));
  assert.equal(result.status, 'retry');
});

test('not_running still requires a successful idle verification', async () => {
  const result = await verifyStreamStop(options(async (kind) => {
    if (kind === 'cancel') return response({ status: 'not_running' });
    return { ok: false, status: 503, json: async () => ({ detail: 'unavailable' }) };
  }));
  assert.equal(result.status, 'retry');
});

test('old Run responses cannot recover the current Run', async () => {
  let current = true;
  const result = await verifyStreamStop({ ...options(async () => {
    current = false;
    return response({ status: 'idle' });
  }), isCurrent: () => current });
  assert.equal(result.status, 'stale');
});

test('real terminal or teardown aborts pending verification', async () => {
  const controller = new AbortController();
  const resultPromise = verifyStreamStop({ ...options(async () => new Promise(() => {})), signal: controller.signal });
  controller.abort();
  assert.equal((await resultPromise).status, 'stale');
});

function stopHarness(verify) {
  const source = readFileSync(new URL('../../../frontend/console/chat.js', import.meta.url), 'utf8');
  const stop = source.slice(source.indexOf('async function stopStream()'), source.indexOf('\nfunction setStreaming('));
  const controller = new AbortController();
  const state = { streaming: true, streamSessionConfirmed: true, sessionId: 'session', agentId: 'agent', abortController: controller, refreshHistoryAfterStream: true };
  const button = { setAttribute() {} };
  const context = { state, el: { stopBtn: button }, AbortController, verifyStreamStop: verify,
    sessionApiUrl: () => '', daFetch: async () => response({ status: 'cancel_requested' }), showConsoleToast() {} };
  vm.createContext(context);
  vm.runInContext(`${stop}\nthis.stopStream = stopStream;`, context);
  return { ...context, controller, button };
}

test('Console closes the stale transport only on confirmed idle, with abnormal recovery enabled', async () => {
  const h = stopHarness(async () => ({ status: 'idle' }));
  await h.stopStream();
  assert.equal(h.controller.signal.aborted, true);
  assert.equal(h.state.refreshHistoryAfterStream, true);
});

test('Console restores stop after failed verification without aborting a live Run', async () => {
  const h = stopHarness(async () => ({ status: 'retry', message: 'still running' }));
  await h.stopStream();
  assert.equal(h.state.stopRequested, false);
  assert.equal(h.button.disabled, false);
  assert.equal(h.controller.signal.aborted, false);
});

test('Console ignores old cancel results after switching to a newer stream', async () => {
  let finish;
  const h = stopHarness(() => new Promise((resolve) => { finish = resolve; }));
  const stopping = h.stopStream();
  const next = new AbortController();
  h.state.abortController = next;
  h.state.stopRequested = true;
  finish({ status: 'idle' });
  await stopping;
  assert.equal(next.signal.aborted, false);
  assert.equal(h.state.stopRequested, true);
});

function streamHarness() {
  const source = readFileSync(new URL('../../../frontend/console/chat.js', import.meta.url), 'utf8');
  const run = source.slice(source.indexOf('async function runChatStream('), source.indexOf('\nfunction updateHitlInputState('));
  const state = { streaming: true, sessionId: 'session' };
  const calls = [];
  const noop = () => {};
  const context = { state, calls, AbortController, boundedStreamRequest,
    serviceUrl: (url) => url, setConn: noop, trajectory: null,
    applyAssistantPresentationActions: noop, assistantActionsFromSse: noop,
    failAutomaticContextCompaction: noop, clearComposerPlan: noop, addErrorTo: noop,
    addSystemNote: (message) => calls.push(message),
    setStreaming: (value) => { state.streaming = value; },
    syncSessionsFromBackend: async () => calls.push('sessions'),
    refreshCurrentHistory: async () => calls.push('history'),
    daFetch: async (_, { signal }) => ({ ok: true, body: { signal } }),
    readSse: async ({ signal }) => new Promise((_, reject) => {
      signal.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' })), { once: true });
    }),
  };
  vm.createContext(context);
  vm.runInContext(`${run}\nthis.runChatStream = runChatStream;`, context);
  return context;
}

test('abnormal idle transport recovery reloads History and unlocks the composer', async () => {
  const h = streamHarness();
  const running = h.runChatStream({}, {});
  await new Promise(setImmediate);
  h.state.streamRecoveryNote = '异常中断';
  h.state.abortController.abort();
  await running;
  assert.equal(h.state.streaming, false);
  assert.equal(h.state.abortController, null);
  assert.deepEqual(h.calls, ['history', '异常中断', 'sessions']);
});

test('an old stream finally cannot unlock or reload a newer stream', async () => {
  const h = streamHarness();
  const running = h.runChatStream({}, {});
  await new Promise(setImmediate);
  const previous = h.state.abortController;
  const next = new AbortController();
  h.state.abortController = next;
  previous.abort();
  await running;
  assert.equal(h.state.abortController, next);
  assert.equal(h.state.streaming, true);
  assert.deepEqual(h.calls, []);
});

test('a real terminal frame closes transport without reloading normal History', async () => {
  const h = streamHarness();
  h.handleEvent = () => { h.state.refreshHistoryAfterStream = false; };
  h.readSse = async ({ signal }, handle) => {
    const aborted = new Promise((resolve) => signal.addEventListener('abort', resolve, { once: true }));
    handle({ event: 'final', data: { status: 'completed' } });
    await aborted;
  };
  await h.runChatStream({}, {});
  assert.equal(h.state.streaming, false);
  assert.deepEqual(h.calls, ['sessions']);
});
