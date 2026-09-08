import { readSse } from "./protocol.js?v=20260729-1";

/** Observe exactly one existing Run. Closing this connection never cancels it. */
export function createSessionObserver({
  runId, connect, onSync, onEvent, onEnd, onFallback, onError,
  isCurrent = () => true, retryMs = 1500, handshakeTimeoutMs = 10000,
  maxFrames = 4096, maxBytes = 4 * 1024 * 1024,
  schedule = setTimeout, unschedule = clearTimeout,
}) {
  let stopped = false;
  let ended = false;
  let controller = null;
  let timer = null;
  let ready;
  const initial = new Promise((resolve) => { ready = resolve; });
  const current = () => !stopped && !ended && isCurrent();

  async function observe() {
    if (!current()) return;
    const attempt = new AbortController();
    controller = attempt;
    const active = () => current() && controller === attempt && !attempt.signal.aborted;
    let pending = Promise.resolve();
    let frameCount = 0;
    let bytes = 0;
    let failure = null;
    let terminal = false;
    const fail = (error) => { failure ||= error; attempt.abort(); };
    const handshake = schedule(() => fail(new Error("实时同步连接超时")), handshakeTimeoutMs);
    try {
      const response = await connect(attempt.signal);
      if (!active()) return;
      if (!response.ok || !response.body) {
        const error = new Error(`实时同步 HTTP ${response.status}`);
        error.status = response.status;
        throw error;
      }
      await readSse(response.body, (frame) => {
        if (!active()) return;
        const size = JSON.stringify(frame).length * 2;
        if (++frameCount > maxFrames || (bytes += size) > maxBytes) {
          fail(new Error("实时同步积压，重新读取历史"));
          return;
        }
        pending = pending.then(async () => {
          if (!active()) return;
          const data = frame.data || {};
          if (frame.event === "observation_reset") {
            const error = new Error("实时同步已重置，重新读取历史");
            error.executionChanged = data.reason === "execution_changed";
            throw error;
          }
          if (data.run_id !== runId) return;
          if (frame.event === "observation_sync") {
            unschedule(handshake);
            await onSync(data, attempt.signal);
            if (active()) ready();
          } else if (frame.event === "observation") {
            if (terminal) return;
            await onEvent(data, attempt.signal);
            terminal = ["final", "error", "cancelled"].includes(data.event);
          } else if (frame.event === "observation_end") {
            await onEnd(data, attempt.signal);
            if (active()) { ended = true; ready(); attempt.abort(); }
          }
        }).catch(fail).finally(() => { frameCount -= 1; bytes -= size; });
      });
      await pending;
      if (current() && controller === attempt) throw failure || new Error("实时同步连接中断");
    } catch (error) {
      if (!current() || controller !== attempt) return;
      attempt.abort();
      await pending;
      const cause = failure || error;
      const expectedReconciliation = cause.status === 409 || cause.executionChanged === true;
      // A stop/reconnect can race producer cleanup. This is normal history
      // reconciliation, not a failed connection; real network/reset errors stay visible.
      if (!expectedReconciliation) onError?.(cause);
      await onFallback?.(cause);
      ready();
      // A rejected target has ended or exceeded the replay bound. Poll only
      // that Run; never open an idle session subscription or a different Run.
      if (expectedReconciliation) ended = true;
    } finally {
      unschedule(handshake);
      attempt.abort();
      if (current() && controller === attempt) timer = schedule(() => {
        timer = null;
        void observe();
      }, retryMs);
    }
  }

  return {
    start() { void observe(); return initial; },
    refresh() {
      if (!current()) return;
      controller?.abort();
      if (timer !== null) unschedule(timer);
      timer = null;
      void observe();
    },
    stop() {
      stopped = true;
      controller?.abort();
      if (timer !== null) unschedule(timer);
      timer = null;
      ready();
    },
  };
}

/** Bound Conversation History or raw Event History to the same observed Run. */
export function historyThroughRun(history, runId, { replay = false, baselineSequence = null } = {}) {
  if (!runId) return history;
  if (Number.isInteger(baselineSequence) && baselineSequence >= 0) {
    return { ...history, items: history.items.filter((item) => {
      if (item.run_id === runId) return !replay || item.kind === "user_message";
      return Number(item.order?.sequence ?? item.sequence ?? 0) <= baselineSequence;
    }) };
  }
  const target = history.items.filter((item) => item.run_id === runId);
  // Before the input commits, all current history predates this Run.
  if (!target.length) return history;
  const cutoff = Math.max(...target.map((item) => Number(item.order?.sequence ?? item.sequence ?? 0)));
  return { ...history, items: history.items.filter((item) => {
    if (Number(item.order?.sequence ?? item.sequence ?? 0) > cutoff) return false;
    return !replay || item.run_id !== runId || item.kind === "user_message";
  }) };
}
