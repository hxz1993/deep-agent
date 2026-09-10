import { initDiagnosticLogs } from "./diagnostics.js?v=20260910-15";
import { initDiagnosticSessions } from "./diagnostics-sessions.js?v=20260910-15";
import { createDiagnosticNavigation, diagnosticHash, parseDiagnosticRoute } from "./diagnostics-navigation.js?v=20260910-13";

let center = null;
export function initDiagnostics() {
  if (center) return;
  const logPage = document.getElementById("diagnosticsLogPage"), sessionPage = document.getElementById("diagnosticsSessionsPage");
  let active = false, route = null, currentHash = "", lastSessions = diagnosticHash("overview"), lastLogs = diagnosticHash("logs");
  const capture = () => route ? { kind: route.kind, value: route.kind === "logs" ? logs.snapshot() : sessions.snapshot() } : null;
  const navigation = createDiagnosticNavigation({ getHash: () => location.hash, history, getSnapshot: capture, onNavigate: () => showRoute() });
  const replace = hash => {
    currentHash = hash; navigation.replaceCurrent(hash);
    const parsed = parseDiagnosticRoute(hash);
    if (parsed?.kind === "logs") lastLogs = hash;
    else if (parsed?.view !== "detail") lastSessions = hash;
  };
  const back = () => navigation.back(lastSessions);
  const logs = initDiagnosticLogs({ page: logPage, onQuery: filters => replace(diagnosticHash("logs", filters)), onOpenSession: id => navigation.navigate(diagnosticHash("detail", { view: "conversation" }, id)), onBack: back });
  const sessions = initDiagnosticSessions({ root: sessionPage, navigate: navigation.navigate, replace, onBack: back });
  function showRoute() {
    if (!active) return;
    const next = parseDiagnosticRoute(location.hash);
    if (!next) { navigation.navigate(lastLogs, { replace: true }); return; }
    if (route && currentHash === location.hash) return;
    const snapshot = navigation.enter();
    logs.deactivate(); sessions.deactivate();
    route = next; currentHash = location.hash;
    logPage.classList.toggle("hidden", next.kind !== "logs"); sessionPage.classList.toggle("hidden", next.kind === "logs");
    document.querySelectorAll("[data-diagnostics-nav]").forEach(button => {
      const selected = button.dataset.diagnosticsNav === next.kind;
      button.classList.toggle("active", selected); selected ? button.setAttribute("aria-current", "page") : button.removeAttribute("aria-current");
    });
    if (next.kind === "logs") logs.activate(next.filters, snapshot?.kind === "logs" ? snapshot.value : null, Boolean(parseDiagnosticRoute(history.state?.diagnosticsFrom)));
    else { if (next.view !== "detail") lastSessions = diagnosticHash(next.view, next.filters); sessions.activate(next, snapshot?.kind === "sessions" ? snapshot.value : null); }
  }
  center = {
    setActive(value) {
      if (!value) { if (active) navigation.remember(); active = false; logs.deactivate(); sessions.deactivate(); route = null; currentHash = ""; return; }
      active = true; showRoute();
    },
    open(kind) {
      const target = kind === "sessions" ? lastSessions : lastLogs;
      // The main page controller retains unsaved configuration guards.
      return window.DeepAgentConsole.showPage("diagnostics", { diagnosticTarget: target });
    },
    clear() { navigation.clear(); logs.clear(); sessions.clear(); route = null; currentHash = ""; active = false; },
  };
  window.addEventListener("deep-agent-diagnostics-denied", event => {
    center.clear();
    window.dispatchEvent(new CustomEvent("deep-agent-console-api-status", { detail: { status: event.detail?.status || 403 } }));
  });
  window.addEventListener("pagehide", () => center.clear());
}
export function setDiagnosticsActive(active) { center?.setActive(active); }
export function openDiagnostics(kind) { return center?.open(kind); }
