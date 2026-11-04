// Query scope is independent of the debug App and the opened conversation.
export function sessionAppQuery(value = "all") {
  if (value === "unassigned") return { app_scope: "unassigned" };
  if (value.startsWith("app:")) return { app_scope: "all", app_id: value.slice(4) };
  return { app_scope: "all" };
}

export function sessionAppOptions(snapshot, selected = "all") {
  const ready = snapshot?.status === "ready";
  const options = [
    { value: "all", label: "全部应用", icon: "layers", count: ready ? snapshot.total : null },
    { value: "unassigned", label: "未关联应用", icon: "unlink", count: ready ? snapshot.unassigned_count : null },
    ...(ready ? snapshot.apps : []).map((app, index) => ({ value: `app:${app.app_id}`,
      label: app.app_name ? `${app.app_name} · ${app.app_id}` : app.app_id,
      icon: "app-window", count: app.session_count, separatorBefore: index === 0 })),
  ];
  // Deleting the last matching conversation must not silently change a filter.
  if (selected.startsWith("app:") && !options.some(option => option.value === selected)) {
    options.push({ value: selected, label: selected.slice(4), icon: "app-window", count: ready ? 0 : null, separatorBefore: options.length === 2 });
  }
  return options;
}

export function createSessionFilterOptionsController({ fetchOptions, changed }) {
  let current = null;
  let snapshot = { agentId: null, status: "idle", agents: [], apps: [] };
  function refresh(agentId, { force = false } = {}) {
    if (!agentId) {
      current?.controller.abort(); current = null;
      snapshot = { agentId: null, status: "idle", agents: [], apps: [] };
      changed(snapshot);
      return Promise.resolve();
    }
    if (!force && current?.agentId === agentId) return current.promise;
    current?.controller.abort();
    const request = { agentId, controller: new AbortController() };
    current = request;
    snapshot = { agentId, status: "loading", agents: [], apps: [] };
    changed(snapshot);
    request.promise = (async () => {
      try {
        const data = await fetchOptions(agentId, request.controller.signal);
        if (current !== request || request.controller.signal.aborted) return;
        if (!Array.isArray(data?.agents) || !Array.isArray(data?.apps)
          || !Number.isInteger(data.total) || !Number.isInteger(data.unassigned_count)) {
          throw new Error("会话统计响应不完整");
        }
        snapshot = { ...data, agentId, status: "ready" };
      } catch (error) {
        if (current !== request || request.controller.signal.aborted) return;
        snapshot = { agentId, status: "error", agents: [], apps: [] };
      } finally {
        if (current === request) { current = null; changed(snapshot); }
      }
      return snapshot;
    })();
    return request.promise;
  }
  return { refresh, dispose() { current?.controller.abort(); current = null; } };
}
