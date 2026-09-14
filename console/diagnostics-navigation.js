// Only identifiers and query conditions belong in diagnostic URLs. Content,
// identity credentials and return snapshots are deliberately memory-only.
export const DIAGNOSTIC_PREFIX = "#/diagnostics";
export const COMMON_FILTER_KEYS = ["start", "end", "timezone", "agent_id", "user_id", "period"];
export const OVERVIEW_FILTER_KEYS = COMMON_FILTER_KEYS.filter(key => key !== "user_id");
export const overviewFilters = filters => Object.fromEntries(OVERVIEW_FILTER_KEYS.filter(key => filters[key] != null).map(key => [key, filters[key]]));
export const LIST_FILTER_KEYS = [...COMMON_FILTER_KEYS, "query", "has_questions", "has_execution_error", "latest_status", "limit", "offset"];
export const LOG_FILTER_KEYS = ["start", "end", "source", "level", "q", "session_id", "period"];
export const CHANNEL_FILTER_KEYS = ["period", "start", "end", "timezone", "account_id", "kind", "query", "offset", "peer_id", "filter_peer_id", "channel_user_id", "channel_group_id"];
export const channelEntryFilters = filters => ({ period: ["24", "168", "720"].includes(String(filters.period)) ? String(filters.period) : "168" });
export const DETAIL_KEYS = ["view", "event_id", "run_id"];

export function parseDiagnosticRoute(hash = "") {
  const [path, search = ""] = String(hash).split("?");
  const match = path.match(/^#\/diagnostics(?:\/(logs|sessions)(?:\/([^/?#]+))?)?$/);
  if (!match) return null;
  const kind = match[1] || "logs";
  let tail;
  try { tail = decodeURIComponent(match[2] || ""); } catch { return null; }
  if (kind === "logs" && tail) return null;
  const view = kind === "logs" ? "logs" : !tail || ["overview", "list", "channels"].includes(tail) ? tail || "overview" : "detail";
  if (view === "detail" && (!tail || tail.length > 128 || /[\x00-\x1f/\\]/.test(tail))) return null;
  const allowed = view === "channels" ? CHANNEL_FILTER_KEYS : view === "logs" ? LOG_FILTER_KEYS : view === "detail" ? DETAIL_KEYS : view === "overview" ? OVERVIEW_FILTER_KEYS : LIST_FILTER_KEYS;
  const params = new URLSearchParams(search);
  const filters = Object.fromEntries(allowed.filter(key => params.has(key)).map(key => [key, params.get(key)]));
  return { kind, view, id: view === "detail" ? tail : null, filters };
}

export function diagnosticHash(view, filters = {}, id = null) {
  const keys = view === "channels" ? CHANNEL_FILTER_KEYS : view === "logs" ? LOG_FILTER_KEYS : view === "detail" ? DETAIL_KEYS : view === "overview" ? OVERVIEW_FILTER_KEYS : LIST_FILTER_KEYS;
  const path = view === "logs" ? "/logs" : view === "detail" ? `/sessions/${encodeURIComponent(id || "")}` : `/sessions/${["list", "channels"].includes(view) ? view : "overview"}`;
  const params = new URLSearchParams();
  for (const key of keys) if (filters[key] !== undefined && filters[key] !== null && filters[key] !== "" && filters[key] !== false) params.set(key, String(filters[key]));
  return `${DIAGNOSTIC_PREFIX}${path}${params.size ? `?${params}` : ""}`;
}

export function sessionRange(values = {}, now = new Date()) {
  const period = values.period || "168";
  const end = values.end ? new Date(values.end) : new Date(now);
  const start = values.start ? new Date(values.start) : new Date(end.getTime() - Number(period === "custom" ? 168 : period) * 3600000);
  if (!Number.isFinite(start.getTime()) || !Number.isFinite(end.getTime()) || start >= end) throw new Error("开始时间必须早于结束时间。");
  if (end - start > 31 * 86400000) throw new Error("会话查询最多支持 31 天，请缩小时间范围。");
  return { ...values, period, start: start.toISOString(), end: end.toISOString(), timezone: values.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC" };
}

export function sessionDrilldown(filters, extra = {}) {
  return { ...Object.fromEntries(COMMON_FILTER_KEYS.filter(key => filters[key]).map(key => [key, filters[key]])), ...extra, offset: 0 };
}

export function createDiagnosticNavigation({ getHash, history, onNavigate, getSnapshot = () => null }) {
  const snapshots = new Map();
  let serial = 0;
  let currentKey = null;
  let currentHash = null;
  function remember() {
    if (currentKey && currentHash) snapshots.set(currentKey, { hash: currentHash, value: getSnapshot() });
    while (snapshots.size > 12) snapshots.delete(snapshots.keys().next().value);
  }
  function enter() {
    remember();
    const hash = getHash();
    let key = history.state?.diagnosticsKey;
    if (key) serial = Math.max(serial, Number(String(key).split("-").at(-1)) || 0);
    if (!key) { key = `diagnostics-${++serial}`; history.replaceState({ diagnosticsKey: key }, "", hash); }
    currentKey = key; currentHash = hash;
    const saved = snapshots.get(key);
    return saved?.hash === hash ? saved.value : null;
  }
  function navigate(hash, { replace = false } = {}) {
    if (!parseDiagnosticRoute(hash)) return false;
    remember();
    const key = replace ? currentKey || `diagnostics-${++serial}` : `diagnostics-${++serial}`;
    const from = replace ? history.state?.diagnosticsFrom : parseDiagnosticRoute(getHash()) ? getHash() : undefined;
    history[replace ? "replaceState" : "pushState"]({ diagnosticsKey: key, ...(parseDiagnosticRoute(from) ? { diagnosticsFrom: from } : {}) }, "", hash);
    onNavigate();
    return true;
  }
  function back(fallback) {
    if (parseDiagnosticRoute(history.state?.diagnosticsFrom)) { remember(); history.back(); }
    else navigate(fallback, { replace: true });
  }
  function replaceCurrent(hash) {
    if (!parseDiagnosticRoute(hash)) return;
    currentHash = hash;
    history.replaceState(history.state, "", hash);
  }
  return { enter, remember, navigate, back, replaceCurrent, clear: () => { snapshots.clear(); currentKey = null; currentHash = null; } };
}
