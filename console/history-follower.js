import { boundedStreamRequest } from "./stop-recovery.js?v=20260907-1";

const RUN_STATUSES = new Set(["idle", "running", "cancel_requested"]);

/** Observe a Run owned by another page/channel, without creating a second SSE Run. */
export function createHistoryFollower({
  readStatus, readHistory, onStatus, onHistory, onError,
  isCurrent = () => true, intervalMs = 1000, timeoutMs = 5000,
  schedule = setTimeout, unschedule = clearTimeout,
}) {
  const controller = new AbortController();
  let timer = null;
  let pending = null;
  let finished = false;
  let refreshRequested = false;
  const current = () => !controller.signal.aborted && isCurrent();

  async function poll() {
    if (!current() || finished) return;
    if (pending) {
      refreshRequested = true;
      return pending;
    }
    pending = (async () => {
      try {
        const status = await boundedStreamRequest(readStatus, {
          signal: controller.signal, timeoutMs,
        });
        if (!current()) return;
        if (!RUN_STATUSES.has(status?.status)) throw new Error("无法确认会话运行状态");
        // A running status is safe to show immediately; idle is only applied
        // after its authoritative terminal History has arrived successfully.
        if (status.status !== "idle") onStatus?.(status);
        const history = await boundedStreamRequest((signal) => readHistory(signal, status), {
          signal: controller.signal, timeoutMs,
        });
        if (!current()) return;
        await onHistory?.(history, status);
        if (!current()) return;
        onStatus?.(status);
        if (status.status === "idle") finished = true;
      } catch (error) {
        if (current()) onError?.(error);
      }
    })();
    try {
      await pending;
    } finally {
      pending = null;
      if (current() && !finished) {
        timer = schedule(() => { timer = null; void poll(); }, refreshRequested ? 0 : intervalMs);
      }
      refreshRequested = false;
    }
  }

  return {
    start: poll,
    refresh() {
      if (timer !== null) unschedule(timer);
      timer = null;
      return poll();
    },
    stop() {
      finished = true;
      if (timer !== null) unschedule(timer);
      timer = null;
      controller.abort();
    },
  };
}
