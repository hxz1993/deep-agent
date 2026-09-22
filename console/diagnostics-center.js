import { initDiagnosticLogs } from "./diagnostics.js?v=20260915-ui-polish";
import { initDiagnosticSessions } from "./diagnostics-sessions.js?v=20260922-trajectory-8";
import { createDiagnosticNavigation, diagnosticHash, parseDiagnosticRoute } from "./diagnostics-navigation.js?v=20260915-app-filter";

import { initDiagnosticChannels } from "./diagnostics-channels.js?v=20260922-trajectory-8";

let center = null;
export function initDiagnostics() {
  if (center) return;
  const logPage = document.getElementById("diagnosticsLogPage"), sessionPage = document.getElementById("diagnosticsSessionsPage"), channelPage = document.getElementById("diagnosticsChannelsPage");
  let active = false, route = null, currentHash = "", lastSessions = diagnosticHash("overview"), lastLogs = diagnosticHash("logs");
  const capture = () => route ? { kind: route.view === "channels" ? "channels" : route.kind, value: route.kind === "logs" ? logs.snapshot() : route.view === "channels" ? channels.snapshot() : sessions.snapshot() } : null;
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
  const channels = initDiagnosticChannels({ root: channelPage, navigate: navigation.navigate, replace, onBack: fallback => navigation.back(fallback) });
  function showRoute() {
    if (!active) return;
    const next = parseDiagnosticRoute(location.hash);
    if (!next) { navigation.navigate(lastLogs, { replace: true }); return; }
    if (route && currentHash === location.hash) return;
    const snapshot = navigation.enter();
    logs.deactivate(); sessions.deactivate(); channels.deactivate();
    route = next; currentHash = location.hash;
    logPage.classList.toggle("hidden", next.kind !== "logs"); sessionPage.classList.toggle("hidden", next.kind === "logs" || next.view === "channels"); channelPage.classList.toggle("hidden", next.view !== "channels");
    document.querySelectorAll("[data-diagnostics-nav]").forEach(button => {
      const selected = button.dataset.diagnosticsNav === next.kind;
      button.classList.toggle("active", selected); selected ? button.setAttribute("aria-current", "page") : button.removeAttribute("aria-current");
    });
    if (next.kind === "logs") logs.activate(next.filters, snapshot?.kind === "logs" ? snapshot.value : null, Boolean(parseDiagnosticRoute(history.state?.diagnosticsFrom)));
    else if (next.view === "channels") { lastSessions = diagnosticHash("channels", next.filters); channels.activate(next, snapshot?.kind === "channels" ? snapshot.value : null); }
    else { if (next.view !== "detail") lastSessions = diagnosticHash(next.view, next.filters); sessions.activate(next, snapshot?.kind === "sessions" ? snapshot.value : null); }
  }
  center = {
    setActive(value) {
      if (!value) { if (active) navigation.remember(); active = false; logs.deactivate(); sessions.deactivate(); channels.deactivate(); route = null; currentHash = ""; return; }
      active = true; showRoute();
    },
    open(kind) {
      const target = kind === "sessions" ? lastSessions : lastLogs;
      // The main page controller retains unsaved configuration guards.
      return window.DeepAgentConsole.showPage("diagnostics", { diagnosticTarget: target });
    },
    clear() { navigation.clear(); logs.clear(); sessions.clear(); channels.clear(); route = null; currentHash = ""; active = false; },
  };
  window.addEventListener("deep-agent-diagnostics-denied", event => {
    center.clear();
    window.dispatchEvent(new CustomEvent("deep-agent-console-api-status", { detail: { status: event.detail?.status || 403 } }));
  });
  window.addEventListener("pagehide", () => center.clear());
}
export function setDiagnosticsActive(active) { center?.setActive(active); }
export function openDiagnostics(kind) { return center?.open(kind); }
