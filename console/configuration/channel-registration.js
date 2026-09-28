/** Registration secrets remain in the API process, never in this editor. */
window.createChannelRegistration = function createChannelRegistration({ channelType = "feishu", request = apiFetch, onChange = () => {} } = {}) {
  if (!["feishu", "wecom", "dingtalk"].includes(channelType)) throw new Error("不支持的扫码渠道");
  const base = `/api/v1/channel-accounts/${channelType}/registrations`;
  let generation = 0, timer = null, expiryTimer = null, controller = null;
  let authorizedUntil = 0, pollFailures = 0, pollDeadline = 0;
  let registration = null;
  const pendingDiscards = new Set();
  function discard(id) {
    const abort = new AbortController();
    const timeout = setTimeout(() => abort.abort(), 5000);
    const task = request(`${base}/${encodeURIComponent(id)}`, { method: "DELETE", signal: abort.signal })
      .catch(() => {}).finally(() => {
        clearTimeout(timeout);
        pendingDiscards.delete(task);
      });
    pendingDiscards.add(task);
    return task;
  }
  function reset({ consumed = false } = {}) {
    generation++;
    clearTimeout(timer);
    clearTimeout(expiryTimer);
    authorizedUntil = 0;
    pollFailures = 0;
    pollDeadline = 0;
    controller?.abort();
    controller = null;
    const previous = registration;
    registration = null;
    if (previous?.registration_id && !consumed) void discard(previous.registration_id);
    return pendingDiscards.size ? Promise.all([...pendingDiscards]) : null;
  }
  function publish(data) {
    clearTimeout(expiryTimer);
    expiryTimer = null;
    authorizedUntil = 0;
    registration = data;
    if (["starting", "waiting", "scanned", "selecting", "creating"].includes(data.status) && Number.isFinite(data.expires_in)) {
      pollDeadline = Date.now() + Math.max(0, data.expires_in) * 1000;
    }
    if (data.status === "authorized" && Number.isFinite(data.expires_in)) {
      authorizedUntil = Date.now() + Math.max(0, data.expires_in) * 1000;
      const token = generation;
      expiryTimer = setTimeout(() => {
        if (token !== generation) return;
        publish({ ...registration, status: "expired", verification_url: null, expires_in: 0 });
      }, Math.max(0, authorizedUntil - Date.now()));
    }
    onChange(data);
  }
  async function poll(token) {
    if (token !== generation || !registration?.registration_id) return;
    const id = registration.registration_id;
    const current = new AbortController();
    controller = current;
    const timeout = setTimeout(() => current.abort(), 30000);
    try {
      const data = await request(`${base}/${encodeURIComponent(id)}`, { signal: current.signal });
      if (token !== generation) return;
      pollFailures = 0;
      publish(data);
      if (["starting", "waiting", "scanned", "selecting", "creating"].includes(data.status)) timer = setTimeout(() => poll(token), 2000);
    } catch (error) {
      if (token !== generation) return;
      const transient = !error.status || error.status === 408 || error.status === 429 || error.status >= 500;
      if (transient && ++pollFailures <= 5 && Date.now() < pollDeadline) {
        // Read the same registration again; never repeat the creation request.
        onChange({ ...registration, error: "授权状态查询暂时中断，正在重试，请勿重复创建机器人。" });
        timer = setTimeout(() => poll(token), 2000);
      } else {
        publish({ ...registration, status: "failed", verification_url: null,
          error: transient ? "暂时无法确认授权结果，请检查网络；如已创建机器人，可手动关联。" : error.message });
      }
    } finally {
      clearTimeout(timeout);
      if (controller === current) controller = null;
    }
  }
  return {
    reset,
    get authorizedId() {
      return registration?.status === "authorized" && (!authorizedUntil || Date.now() < authorizedUntil)
        ? registration.registration_id : null;
    },
    async start({ appName = "" } = {}) {
      const cleanup = reset();
      const token = generation;
      pollDeadline = Date.now() + 600000;
      onChange({ status: "starting" });
      const creationTimeout = setTimeout(() => {
        if (token !== generation) return;
        generation++;
        onChange({ status: "failed", error: "创建授权超时，请稍后重试。" });
      }, 30000);
      timer = creationTimeout;
      // Keep the creation response so a late created registration can be cancelled.
      // API registrations also expire server-side if the page itself is unloaded.
      try {
        if (cleanup) await cleanup;
        if (token !== generation) { clearTimeout(creationTimeout); return; }
        const data = await request(base, { method: "POST", body: JSON.stringify(channelType === "feishu" ? { app_name: appName.trim() } : {}) });
        clearTimeout(creationTimeout);
        if (token !== generation) { if (data.registration_id) await discard(data.registration_id); return; }
        publish(data);
        if (["starting", "waiting", "scanned", "selecting", "creating"].includes(data.status)) timer = setTimeout(() => poll(token), 2000);
      } catch (error) {
        clearTimeout(creationTimeout);
        if (token === generation) onChange({ status: "failed", error: error.message });
      }
    },
  };
};
