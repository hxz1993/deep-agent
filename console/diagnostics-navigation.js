// URLs carry resource locators only. Query conditions and return snapshots
// stay in this page lifetime; a reload always resolves relative time afresh.
export const DIAGNOSTIC_PREFIX = "#/diagnostics";
export const COMMON_FILTER_KEYS = ["start", "end", "timezone", "agent_id", "user_id", "period"];
export const OVERVIEW_FILTER_KEYS = COMMON_FILTER_KEYS.filter(key => key !== "user_id");
export const overviewFilters = filters => Object.fromEntries(OVERVIEW_FILTER_KEYS.filter(key => filters[key] != null).map(key => [key, filters[key]]));
export const LIST_FILTER_KEYS = [...COMMON_FILTER_KEYS, "app_id", "query", "has_questions", "has_execution_error", "latest_status", "limit", "offset"];
export const LOG_FILTER_KEYS = ["start", "end", "source", "level", "q", "session_id", "period"];
export const USAGE_FILTER_KEYS = ["start", "end", "timezone", "agent_id", "period"];
export const CHANNEL_FILTER_KEYS = ["period", "start", "end", "timezone", "account_id", "kind", "query", "offset", "peer_id", "filter_peer_id", "channel_user_id", "channel_group_id"];
export const channelEntryFilters = filters => ({ period: ["24", "168", "720"].includes(String(filters.period)) ? String(filters.period) : "168" });
export const DETAIL_KEYS = ["view", "event_id", "run_id"];

const filterKeys = view => view === "channels" ? CHANNEL_FILTER_KEYS : view === "logs" ? LOG_FILTER_KEYS : view === "usage" ? USAGE_FILTER_KEYS : view === "detail" ? DETAIL_KEYS : view === "overview" ? OVERVIEW_FILTER_KEYS : LIST_FILTER_KEYS;
const selectFilters = (view, filters = {}) => Object.fromEntries(filterKeys(view).filter(key => filters[key] !== undefined && filters[key] !== null && filters[key] !== "" && filters[key] !== false).map(key => [key, String(filters[key])]));

export function parseDiagnosticRoute(hash = "") {
  if (hash && typeof hash === "object") {
    const route = parseDiagnosticRoute(hash.hash);
    return route ? { ...route, filters: selectFilters(route.view, hash.filters) } : null;
  }
  const [path, search = ""] = String(hash).split("?");
  const match = path.match(/^#\/diagnostics(?:\/(logs|sessions|usage)(?:\/([^/?#]+))?)?$/);
  if (!match) return null;
  const kind = match[1] || "logs";
  let tail;
  try { tail = decodeURIComponent(match[2] || ""); } catch { return null; }
  if ((kind === "logs" || kind === "usage") && tail) return null;
  const view = kind === "logs" || kind === "usage" ? kind : !tail || ["overview", "list", "channels"].includes(tail) ? tail || "overview" : "detail";
  if (view === "detail" && (!tail || tail.length > 128 || /[\x00-\x1f/\\]/.test(tail))) return null;
  const allowed = filterKeys(view);
  const params = new URLSearchParams(search);
  const filters = Object.fromEntries(allowed.filter(key => params.has(key)).map(key => [key, params.get(key)]));
  // Old links may carry a frozen relative range. Import only its period and
  // other conditions, then let the controller resolve the range against now.
  if (view !== "detail" && filters.period !== "custom") { delete filters.start; delete filters.end; }
  return { kind, view, id: view === "detail" ? tail : null, filters };
}

export function diagnosticHash(view, filters = {}, id = null) {
  const keys = view === "detail" ? DETAIL_KEYS : [];
  const path = view === "logs" ? "/logs" : view === "usage" ? "/usage" : view === "detail" ? `/sessions/${encodeURIComponent(id || "")}` : `/sessions/${["list", "channels"].includes(view) ? view : "overview"}`;
  const params = new URLSearchParams();
  for (const key of keys) if (filters[key] !== undefined && filters[key] !== null && filters[key] !== "" && filters[key] !== false) params.set(key, String(filters[key]));
  return `${DIAGNOSTIC_PREFIX}${path}${params.size ? `?${params}` : ""}`;
}

export function diagnosticTarget(view, filters = {}, id = null) {
  return { hash: diagnosticHash(view, filters, id), filters: selectFilters(view, filters) };
}

// Compare conditions directly: clean URLs intentionally cannot distinguish them.
export function sameDiagnosticFilters(left = {}, right = {}) {
  const keys = [...new Set([...Object.keys(left), ...Object.keys(right)])].filter(key => left[key] != null || right[key] != null).sort();
  return keys.every(key => String(left[key] ?? "") === String(right[key] ?? ""));
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
  const snapshots = new Map(), routes = new Map();
  let serial = 0, currentKey = null, currentHash = null, currentRoute = null, pendingEntry = false;
  const cleanHash = route => diagnosticHash(route.view, route.filters, route.id);
  const safeFrom = value => { const parsed = parseDiagnosticRoute(value); return parsed ? cleanHash(parsed) : undefined; };
  const trim = () => { while (snapshots.size > 12) { const key = snapshots.keys().next().value; snapshots.delete(key); routes.delete(key); } };
  function remember() {
    const value = getSnapshot();
    // Leaving diagnostics captures before deactivation. A later enter must
    // not overwrite that saved view with the now-inactive controller's null.
    if (currentKey && currentHash && value != null) snapshots.set(currentKey, { hash: currentHash, filters: { ...currentRoute?.filters }, value });
    trim();
  }
  function enter(target = null) {
    if (!pendingEntry) remember();
    pendingEntry = false;
    const parsed = parseDiagnosticRoute(target || getHash());
    if (!parsed) return null;
    let key = history.state?.diagnosticsKey;
    if (key) serial = Math.max(serial, Number(String(key).split("-").at(-1)) || 0);
    if (!key) key = `diagnostics-${++serial}`;
    const hash = cleanHash(parsed), stored = routes.get(key);
    currentRoute = !target && stored?.hash === hash ? stored.route : parsed;
    const from = safeFrom(history.state?.diagnosticsFrom);
    history.replaceState({ diagnosticsKey: key, ...(from ? { diagnosticsFrom: from } : {}) }, "", hash);
    currentKey = key; currentHash = hash;
    routes.set(key, { hash, route: currentRoute });
    const saved = snapshots.get(key);
    return saved?.hash === hash && sameDiagnosticFilters(saved.filters, currentRoute.filters) ? saved.value : null;
  }
  function navigate(target, { replace = false } = {}) {
    const route = parseDiagnosticRoute(target);
    if (!route) return false;
    remember();
    const hash = cleanHash(route), key = replace ? currentKey || `diagnostics-${++serial}` : `diagnostics-${++serial}`;
    const from = safeFrom(replace ? history.state?.diagnosticsFrom : getHash());
    routes.set(key, { hash, route });
    if (replace) snapshots.delete(key);
    history[replace ? "replaceState" : "pushState"]({ diagnosticsKey: key, ...(from ? { diagnosticsFrom: from } : {}) }, "", hash);
    pendingEntry = true;
    onNavigate();
    return true;
  }
  function back(fallback) {
    if (safeFrom(history.state?.diagnosticsFrom)) { remember(); history.back(); }
    else navigate(fallback, { replace: true });
  }
  function replaceCurrent(target) {
    const route = parseDiagnosticRoute(target);
    if (!route) return;
    currentHash = cleanHash(route); currentRoute = route;
    const key = currentKey || history.state?.diagnosticsKey || `diagnostics-${++serial}`;
    currentKey = key;
    routes.set(key, { hash: currentHash, route });
    const from = safeFrom(history.state?.diagnosticsFrom);
    history.replaceState({ diagnosticsKey: key, ...(from ? { diagnosticsFrom: from } : {}) }, "", currentHash);
  }
  return { enter, remember, navigate, back, replaceCurrent, get route() { return currentRoute; },
    get key() { return currentKey; }, clear: () => { snapshots.clear(); routes.clear(); currentKey = null; currentHash = null; currentRoute = null; pendingEntry = false; } };
}
