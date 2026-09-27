/** Registration secrets remain in the API process, never in this editor. */
window.createChannelRegistration = function createChannelRegistration({ channelType = "feishu", request = apiFetch, onChange = () => {} } = {}) {
  if (!["feishu", "wecom"].includes(channelType)) throw new Error("不支持的扫码渠道");
  const base = `/api/v1/channel-accounts/${channelType}/registrations`;
  let generation = 0, timer = null, expiryTimer = null, controller = null;
  let authorizedUntil = 0;
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
      publish(data);
      if (["starting", "waiting"].includes(data.status)) timer = setTimeout(() => poll(token), 2000);
    } catch (error) {
      if (token !== generation) return;
      publish({ ...registration, status: "failed", error: error.name === "AbortError" ? "授权状态查询超时，请重新生成二维码。" : error.message });
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
        if (["starting", "waiting"].includes(data.status)) timer = setTimeout(() => poll(token), 2000);
      } catch (error) {
        clearTimeout(creationTimeout);
        if (token === generation) onChange({ status: "failed", error: error.message });
      }
    },
  };
};
