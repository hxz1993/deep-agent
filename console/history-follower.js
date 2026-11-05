import { boundedStreamRequest } from "./stop-recovery.js?v=20260912-21";

const RUN_STATUSES = new Set(["idle", "running", "cancel_requested"]);

/** Observe a Run owned by another page/channel, without creating a second SSE Run. */
export function createHistoryFollower({
  readStatus, readHistory, onStatus, onHistory, onError,
  isCurrent = () => true, intervalMs = 1000, timeoutMs = 5000,
  historyIntervalMs = 0, now = () => Date.now(),
  schedule = setTimeout, unschedule = clearTimeout,
}) {
  const controller = new AbortController();
  let timer = null;
  let pending = null;
  let finished = false;
  let refreshRequested = false;
  let forceNextHistory = false;
  let retryHistory = false;
  let lastHistoryAt = null;
  let lastHistoryStatus = null;
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
        const timestamp = now();
        const statusChanged = !lastHistoryStatus || status.status !== lastHistoryStatus.status
          || status.run_id !== lastHistoryStatus.run_id || status.read_only !== lastHistoryStatus.read_only;
        const readDue = status.status === "idle" || forceNextHistory || retryHistory || statusChanged
          || lastHistoryAt === null || timestamp < lastHistoryAt || timestamp - lastHistoryAt >= historyIntervalMs;
        if (!readDue) return;
        // Run status is not a History version: checkpoints can change in place.
        // The bounded interval only reduces fallback traffic, never skips future reads.
        forceNextHistory = false;
        const history = await boundedStreamRequest((signal) => readHistory(signal, status), {
          signal: controller.signal, timeoutMs,
        });
        if (!current()) return;
        await onHistory?.(history, status);
        if (!current()) return;
        lastHistoryAt = now();
        lastHistoryStatus = { status: status.status, run_id: status.run_id, read_only: status.read_only };
        retryHistory = false;
        onStatus?.(status);
        if (status.status === "idle") finished = true;
      } catch (error) {
        retryHistory = true;
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
      forceNextHistory = true;
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
