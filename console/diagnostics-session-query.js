import { LIST_FILTER_KEYS } from "./diagnostics-navigation.js?v=20260909-2";
export function buildSessionQuery(filters, overview = false) {
  const params = new URLSearchParams();
  const keys = overview ? ["start", "end", "timezone", "agent_id", "user_id", "source"] : LIST_FILTER_KEYS.filter(key => key !== "period");
  for (const key of keys) if (filters[key] !== undefined && filters[key] !== null && filters[key] !== "" && filters[key] !== false) params.set(key, String(filters[key]));
  return params.toString();
}
export function sessionQueryError(error) {
  const code = Number(error?.status);
  if (code === 401) return "登录状态已失效，请重新登录。";
  if (code === 403) return "没有诊断查询权限，仅企业管理员可查看。";
  if (code === 404) return "会话不存在或已删除。";
  if (code === 409) return "历史投影暂不可用，可切换轨迹继续排查。";
  if (code === 422) return `查询条件无效：${error.message || "请检查时间范围和标识。"}`;
  if (code === 503) return "查询暂不可用或服务繁忙，请稍后手动刷新。";
  return "读取失败，请检查网络后手动刷新。";
}
// Each controller is independent of live chat and owns a single read generation.
export function createSessionReader(request) {
  let generation = 0, controller = null;
  function cancel() { generation += 1; controller?.abort(); controller = null; }
  function begin() {
    cancel(); const own = generation; controller = new AbortController(); const signal = controller.signal;
    return { signal, current: () => generation === own && !signal.aborted, get: path => request(path, signal) };
  }
  async function readEvents(scope, id, onProgress = () => {}) {
    const items = [], seen = new Set(); let after = null;
    do {
      const params = new URLSearchParams({ limit: "500" });
      if (after !== null) params.set("after_sequence", String(after));
      const result = await scope.get(`api/v1/diagnostics/sessions/${encodeURIComponent(id)}/events?${params}`);
      if (!scope.current()) return null;
      if (!Array.isArray(result?.items)) throw new Error("Invalid events response");
      for (const event of result.items) if (!seen.has(event.event_id)) { seen.add(event.event_id); items.push(event); }
      if (result.next_after_sequence != null && result.next_after_sequence <= (after ?? -1)) throw new Error("Event cursor did not advance");
      after = result.next_after_sequence ?? null; onProgress(items.length, after === null);
    } while (after !== null);
    return items;
  }
  return { begin, cancel, readEvents };
}
