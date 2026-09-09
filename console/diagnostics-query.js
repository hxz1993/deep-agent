const MAX_VISIBLE_RECORDS = 5000;

export function buildDiagnosticsQuery(filters = {}, cursor = null) {
  const params = new URLSearchParams({ limit: "200" });
  for (const key of ["start", "end", "source", "level", "session_id", "run_id", "q"]) {
    const value = String(filters[key] || "").trim();
    if (value) params.set(key, value);
  }
  if (cursor) params.set("cursor", cursor);
  return params.toString();
}

export function diagnosticsError(error) {
  const status = Number(error?.status || 0);
  if (status === 401) return { kind: "error", title: "登录状态已失效", message: "请重新登录后再查询日志。" };
  if (status === 403) return { kind: "error", title: "无日志查询权限", message: "日志仅对企业管理员开放。" };
  if (status === 409) return { kind: "warning", title: "查询位置已失效", message: "分页已过期，或日志已轮转、清理、服务实例已变化，请重新查询。" };
  if (status === 503) return { kind: "warning", title: "日志暂不可用", message: "当前实例的诊断日志暂不可读取，请稍后刷新。" };
  if (status === 422) return { kind: "error", title: "查询条件无效", message: "请检查时间范围、标识及关键词长度后重新查询。" };
  return { kind: "error", title: "日志查询失败", message: "连接或响应异常，请重新查询。" };
}

export function diagnosticsStatusNotice(status) {
  if (!status?.enabled) return { kind: "warning", title: "诊断日志未启用", message: "当前实例尚未启用受控诊断日志。" };
  if (!status?.available) return { kind: "warning", title: "日志暂不可用", message: "当前实例的诊断日志目录暂不可读取。" };
  if (status.issues?.length) return { kind: "warning", title: "日志可能不完整", message: "日志写入或读取存在异常；已记录的内容仍可查询。" };
  return null;
}

// An explicit query owns status and pages. Invalidate late responses even when
// an old transport ignores AbortSignal; this module never polls or executes a Run.
export function createDiagnosticsQuery({ request, onChange = () => {} }) {
  let generation = 0;
  let controller = null;
  const state = { active: false, busy: false, items: [], status: null, notice: null, nextCursor: null, partial: false, range: null, instanceId: "", filters: {}, capped: false };
  const notify = () => onChange({ ...state, items: [...state.items] });
  function cancel() {
    generation += 1;
    controller?.abort();
    controller = null;
    state.busy = false;
  }
  function clearResults() {
    state.items = [];
    state.nextCursor = null;
    state.partial = false;
    state.capped = false;
    state.range = null;
  }
  function setActive(active) {
    state.active = active;
    if (!active) cancel();
    notify();
  }
  function changeFilters() {
    cancel();
    clearResults();
    state.notice = { kind: "info", title: "筛选条件已更改", message: "点击查询查看匹配日志。" };
    notify();
  }
  async function query(filters = state.filters, { more = false } = {}) {
    if (!state.active || (more && (state.busy || !state.nextCursor))) return;
    cancel();
    const ownGeneration = generation;
    controller = new AbortController();
    const signal = controller.signal;
    const current = () => state.active && ownGeneration === generation && !signal.aborted;
    state.busy = true;
    state.notice = null;
    if (!more) {
      clearResults();
      state.status = null;
      state.instanceId = "";
      state.filters = { ...filters };
    }
    notify();
    try {
      if (!more) {
        const status = await request("api/v1/diagnostics/logs/status", signal);
        if (!current()) return;
        state.status = status;
        state.instanceId = status.instance_id || "";
        state.notice = diagnosticsStatusNotice(status);
        if (!status.enabled || !status.available) return;
      }
      const queryString = buildDiagnosticsQuery(state.filters, more ? state.nextCursor : null);
      const result = await request(`api/v1/diagnostics/logs?${queryString}`, signal);
      if (!current()) return;
      if (!Array.isArray(result?.items)) throw new Error("Invalid log response");
      if (more && state.instanceId && result.instance_id !== state.instanceId) throw Object.assign(new Error("Instance changed"), { status: 409 });
      const seen = new Set(state.items.map((item) => `${item.boot_id}:${item.record_id}`));
      for (const item of result.items) {
        const key = `${item.boot_id}:${item.record_id}`;
        if (!seen.has(key)) { state.items.push(item); seen.add(key); }
      }
      state.capped = state.items.length >= MAX_VISIBLE_RECORDS;
      state.items = state.items.slice(0, MAX_VISIBLE_RECORDS);
      state.nextCursor = state.capped ? null : result.next_cursor || null;
      state.partial = Boolean(result.partial);
      state.range = result.range || null;
      state.instanceId = result.instance_id || state.instanceId;
      state.notice = diagnosticsStatusNotice(state.status);
    } catch (error) {
      if (!current()) return;
      state.notice = diagnosticsError(error);
      if ([401, 403, 409, 503].includes(Number(error?.status))) clearResults();
    } finally {
      if (current()) { state.busy = false; controller = null; notify(); }
    }
  }
  return { state, setActive, changeFilters, query, cancel };
}
