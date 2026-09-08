// Cancellation acknowledgement is not a terminal event. Confirm backend state
// without allowing a failed control request to lock the stop button forever.
export async function boundedStreamRequest(operation, { signal, timeoutMs = 5000 } = {}) {
  const controller = new AbortController();
  let timer;
  let rejectAbort;
  const abort = () => {
    controller.abort();
    rejectAbort?.(new Error(signal?.aborted ? '操作已结束' : '请求超时'));
  };
  try {
    return await Promise.race([
      new Promise((_, reject) => {
        rejectAbort = reject;
        signal?.addEventListener('abort', abort, { once: true });
        timer = setTimeout(abort, timeoutMs);
        if (signal?.aborted) abort();
      }),
      Promise.resolve().then(() => {
        if (controller.signal.aborted) throw new Error('操作已结束');
        return operation(controller.signal);
      }),
    ]);
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', abort);
    controller.abort();
  }
}

export async function verifyStreamStop({ request, isCurrent, signal, onAccepted,
  requestTimeoutMs = 5000, verificationTimeoutMs = 15000, pollIntervalMs = 750 }) {
  const current = () => !signal?.aborted && isCurrent();
  const deadline = Date.now() + verificationTimeoutMs;
  const read = (kind) => boundedStreamRequest(async (requestSignal) => {
    const response = await request(kind, requestSignal);
    const body = await response.json();
    if (!response.ok) throw new Error(typeof body?.detail === 'string' ? body.detail : `HTTP ${response.status}`);
    return body;
  }, { signal, timeoutMs: Math.max(1, Math.min(requestTimeoutMs, deadline - Date.now())) });
  try {
    if (!current()) return { status: 'stale' };
    const acknowledgement = await read('cancel');
    if (!current()) return { status: 'stale' };
    if (!['cancel_requested', 'not_running'].includes(acknowledgement.status)) {
      return { status: 'retry', message: '后端未确认取消请求' };
    }
    onAccepted?.();
    while (current() && Date.now() < deadline) {
      const result = await read('status');
      if (!current()) return { status: 'stale' };
      if (result.status === 'idle') return { status: 'idle' };
      if (!['running', 'cancel_requested'].includes(result.status)) {
        return { status: 'retry', message: '运行状态无法确认' };
      }
      await new Promise((resolve) => {
        const done = () => { clearTimeout(timer); signal?.removeEventListener('abort', done); resolve(); };
        const timer = setTimeout(done, Math.min(pollIntervalMs, Math.max(0, deadline - Date.now())));
        signal?.addEventListener('abort', done, { once: true });
        if (signal?.aborted) done();
      });
    }
    return current() ? { status: 'retry', message: '后端仍在结束运行，请稍后重试停止' } : { status: 'stale' };
  } catch (error) {
    return current() ? { status: 'retry', message: error.message } : { status: 'stale' };
  }
}
