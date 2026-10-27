import { initDiagnosticLogs } from "./diagnostics.js?v=20261019-release0138";
import { initDiagnosticSessions } from "./diagnostics-sessions.js?v=20261027-image-dismiss1";
import { createDiagnosticNavigation, diagnosticHash, diagnosticTarget, parseDiagnosticRoute } from "./diagnostics-navigation.js?v=20261019-release0138";

import { initDiagnosticChannels } from "./diagnostics-channels.js?v=20261027-image-dismiss1";
import { initDiagnosticUsage } from "./diagnostics-usage.js?v=20261027-usage-average1";

let center = null;
export function initDiagnostics() {
  if (center) return;
  const logPage = document.getElementById("diagnosticsLogPage"), sessionPage = document.getElementById("diagnosticsSessionsPage"), channelPage = document.getElementById("diagnosticsChannelsPage"), usagePage = document.getElementById("diagnosticsUsagePage");
  let active = false, route = null, currentHash = "", currentEntryKey = null, pendingTarget = null, lastSessions = diagnosticTarget("overview"), lastLogs = diagnosticTarget("logs"), lastUsage = diagnosticTarget("usage");
  const capture = () => route ? { kind: route.view === "channels" ? "channels" : route.kind, value: route.kind === "logs" ? logs.snapshot() : route.kind === "usage" ? usage.snapshot() : route.view === "channels" ? channels.snapshot() : sessions.snapshot() } : null;
  const navigation = createDiagnosticNavigation({ getHash: () => location.hash, history, getSnapshot: capture, onNavigate: () => showRoute({ force: true }) });
  const replace = target => {
    navigation.replaceCurrent(target); currentHash = location.hash;
    const parsed = parseDiagnosticRoute(target);
    if (parsed?.kind === "logs") lastLogs = target;
    else if (parsed?.kind === "usage") lastUsage = target;
    else if (parsed?.view !== "detail") lastSessions = target;
  };
  const back = () => navigation.back(lastSessions);
  const logs = initDiagnosticLogs({ page: logPage, onQuery: filters => replace(diagnosticTarget("logs", filters)), onOpenSession: id => navigation.navigate(diagnosticTarget("detail", { view: "conversation" }, id)), onBack: back });
  const sessions = initDiagnosticSessions({ root: sessionPage, navigate: navigation.navigate, replace, onBack: back });
  const channels = initDiagnosticChannels({ root: channelPage, navigate: navigation.navigate, replace, onBack: fallback => navigation.back(fallback) });
  const usage = initDiagnosticUsage({ root: usagePage, navigate: navigation.navigate, replace });
  function showRoute({ force = false } = {}) {
    if (!active) return;
    let next = parseDiagnosticRoute(location.hash);
    if (!next) { navigation.navigate(lastLogs, { replace: true }); return; }
    if (!force && route && currentHash === location.hash && currentEntryKey === history.state?.diagnosticsKey && !pendingTarget) return;
    const supplied = pendingTarget?.hash === diagnosticHash(next.view, next.filters, next.id) ? pendingTarget : null;
    pendingTarget = null;
    const snapshot = navigation.enter(supplied);
    next = navigation.route; currentEntryKey = navigation.key;
    logs.deactivate(); sessions.deactivate(); channels.deactivate(); usage.deactivate();
    route = next; currentHash = location.hash;
    logPage.classList.toggle("hidden", next.kind !== "logs"); sessionPage.classList.toggle("hidden", next.kind !== "sessions" || next.view === "channels"); channelPage.classList.toggle("hidden", next.view !== "channels"); usagePage.classList.toggle("hidden", next.kind !== "usage");
    document.querySelectorAll("[data-diagnostics-nav]").forEach(button => {
      const selected = button.dataset.diagnosticsNav === next.kind;
      button.classList.toggle("active", selected); selected ? button.setAttribute("aria-current", "page") : button.removeAttribute("aria-current");
    });
    if (next.kind === "logs") logs.activate(next.filters, snapshot?.kind === "logs" ? snapshot.value : null, Boolean(parseDiagnosticRoute(history.state?.diagnosticsFrom)));
    else if (next.kind === "usage") { lastUsage = diagnosticTarget(next.view, next.filters); usage.activate(next, snapshot?.kind === "usage" ? snapshot.value : null); }
    else if (next.view === "channels") { lastSessions = diagnosticTarget("channels", next.filters); channels.activate(next, snapshot?.kind === "channels" ? snapshot.value : null); }
    else { if (next.view !== "detail") lastSessions = diagnosticTarget(next.view, next.filters); sessions.activate(next, snapshot?.kind === "sessions" ? snapshot.value : null); }
  }
  center = {
    setActive(value) {
      if (!value) { if (active) navigation.remember(); active = false; logs.deactivate(); sessions.deactivate(); channels.deactivate(); usage.deactivate(); route = null; currentHash = ""; currentEntryKey = null; return; }
      active = true; showRoute();
    },
    async open(kind) {
      const target = kind === "sessions" ? lastSessions : kind === "usage" ? lastUsage : lastLogs;
      // The main page controller retains unsaved configuration guards.
      pendingTarget = target;
      try { return await window.DeepAgentConsole.showPage("diagnostics", { diagnosticTarget: target.hash }); }
      finally { if (pendingTarget === target) pendingTarget = null; }
    },
    clear() { navigation.clear(); logs.clear(); sessions.clear(); channels.clear(); usage.clear(); route = null; currentHash = ""; currentEntryKey = null; pendingTarget = null; lastSessions = diagnosticTarget("overview"); lastLogs = diagnosticTarget("logs"); lastUsage = diagnosticTarget("usage"); active = false; },
  };
  window.addEventListener("deep-agent-diagnostics-denied", event => {
    center.clear();
    window.dispatchEvent(new CustomEvent("deep-agent-console-api-status", { detail: { status: event.detail?.status || 403 } }));
  });
  window.addEventListener("pagehide", () => center.clear());
}
export function setDiagnosticsActive(active) { center?.setActive(active); }
export function openDiagnostics(kind) { return center?.open(kind); }
