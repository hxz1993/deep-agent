import { createPerformanceFixture, fixtureHistory, fixtureConversation } from "./performance-fixture.mjs";

const params = new URLSearchParams(location.search);
const base = params.get("source") === "baseline" ? "/baseline/console" : "/console";
const source = params.get("source") === "baseline" ? "baseline" : "current";
document.getElementById("variant").textContent = source === "baseline" ? "优化前" : "当前源码";
for (const name of ["themes.css", "console.css", "workspace.css"]) {
  const link = document.createElement("link"); link.rel = "stylesheet"; link.href = `${base}/${name}`; document.head.append(link);
}
for (const name of ["vendor/lucide.min.js", "vendor/marked.min.js", "vendor/purify.min.js", "vendor/highlight.min.js", "markdown.js", "json-view.js"]) {
  await new Promise((resolve, reject) => { const script = document.createElement("script"); script.src = `${base}/${name}`; script.onload = resolve; script.onerror = reject; document.head.append(script); });
}
window.serviceUrl = path => `/${path}`;
window.daFetch = (...args) => fetch(...args);
const [{ createConversationPresenter }, { createTrajectoryController }, presentation, { projectConversationItems }] = await Promise.all([
  import(`${base}/conversation-presenter.js`), import(`${base}/trajectory.js`), import(`${base}/assistant-presentation.js`), import(`${base}/conversation-items.js`),
]);
const fixture = createPerformanceFixture();
const messages = document.getElementById("messages");
const summary = document.getElementById("summary");
let presenters = [], trajectory = null, stopped = false, running = false, scrollFrame = null;
let metrics, mutationObserver, longTaskObserver, sampleFrame, gaps, tasks;
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const settle = () => pause(120);
const hash = async text => [...new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text)))].map(n => n.toString(16).padStart(2, "0")).join("");
const fixtureHash = await hash(JSON.stringify(fixture));
const originalReplace = window.DeepAgentMarkdown.replaceBody;
const originalJson = window.DeepAgentJson.renderBlock;
const originalIcons = window.lucide.createIcons;
// Observe calls without replacing production parsing, sanitization or rendering.
window.DeepAgentMarkdown = { ...window.DeepAgentMarkdown, replaceBody(...args) { if (metrics) metrics.markdownRenders++; return originalReplace(...args); } };
window.DeepAgentJson = { ...window.DeepAgentJson, renderBlock(...args) { if (metrics) metrics.jsonRenders++; return originalJson(...args); } };
window.lucide.createIcons = (...args) => { if (metrics) metrics.globalIconScans++; return originalIcons(...args); };

function cleanup() {
  for (const p of presenters) p.dispose(); presenters = [];
  trajectory?.destroy(); trajectory = null;
  if (scrollFrame !== null) cancelAnimationFrame(scrollFrame);
  if (sampleFrame !== null) cancelAnimationFrame(sampleFrame);
  mutationObserver?.disconnect(); longTaskObserver?.disconnect();
  messages.replaceChildren(); document.getElementById("trajectoryList").replaceChildren(); document.getElementById("inspectorContent").replaceChildren();
}
function setup() {
  cleanup(); stopped = false; gaps = []; tasks = [];
  document.querySelector("main.chat").classList.remove("trajectory-active");
  metrics = { source, fixtureHash, markdownRenders: 0, jsonRenders: 0, globalIconScans: 0, listMutations: 0, framesApplied: 0, peakObservedHeapBytes: performance.memory?.usedJSHeapSize ?? null };
  trajectory = createTrajectoryController({ state: {}, attachmentUrl: file => `/files/${encodeURIComponent(file.file_id || file.id)}` });
  trajectory.selectView("conversation");
  mutationObserver = new MutationObserver(records => { if (metrics) metrics.listMutations += records.length; });
  mutationObserver.observe(document.getElementById("trajectoryList"), { childList: true, subtree: true });
  longTaskObserver = new PerformanceObserver(list => tasks.push(...list.getEntries().map(entry => entry.duration)));
  try { longTaskObserver.observe({ type: "longtask", buffered: false }); } catch { /* Firefox / older WebKit */ }
  let last = performance.now();
  const sample = now => { gaps.push(now - last); last = now; if (metrics && performance.memory) metrics.peakObservedHeapBytes = Math.max(metrics.peakObservedHeapBytes || 0, performance.memory.usedJSHeapSize); sampleFrame = requestAnimationFrame(sample); };
  sampleFrame = requestAnimationFrame(sample);
}
function makePresenter() {
  const view = createConversationPresenter({ messages, attachmentUrl: file => `/files/${encodeURIComponent(file.file_id || file.id)}`, scrollToBottom() {
    if (scrollFrame !== null) cancelAnimationFrame(scrollFrame);
    scrollFrame = requestAnimationFrame(() => { scrollFrame = null; messages.scrollTop = messages.scrollHeight; });
  } });
  const presenter = view.createAssistantMessage(); presenters.push(presenter); return presenter;
}

async function verifyContent(cancelled, appliedFrames = []) {
  if (cancelled) {
    const thinking = [...messages.querySelectorAll('.thinking-content')].map(node => node.textContent).join('');
    const expectedThinking = appliedFrames.filter(frame => frame.event === 'thinking').map(frame => frame.data.delta).join('');
    const calls = [...messages.querySelectorAll('.tool-block')].map(node => node.dataset.toolCallId);
    const expectedCalls = appliedFrames.filter(frame => frame.event === 'tool_call').map(frame => frame.data.tool_call_id);
    return { cancelled: true, partialVisible: messages.textContent.length > 0, thinkingPrefix: thinking === expectedThinking, callOrder: JSON.stringify(calls) === JSON.stringify(expectedCalls), interrupted: messages.textContent.includes('已停止') };
  }
  const toolBlocks = [...messages.querySelectorAll(".tool-block")];
  const calls = toolBlocks.map(node => node.dataset.toolCallId);
  const thinking = [...messages.querySelectorAll(".thinking-content")].map(node => node.textContent);
  const finals = [...messages.querySelectorAll(".assistant-final")].map(node => node.textContent.trim());
  const expectedFinals = fixture.turns.map(turn => { const node = document.createElement("div"); node.innerHTML = window.DeepAgentMarkdown.render(turn.answer); return node.textContent.trim(); });
  const checks = { callOrder: JSON.stringify(calls) === JSON.stringify(fixture.expectedCalls), thinkingExact: JSON.stringify(thinking) === JSON.stringify(fixture.turns.flatMap(t => t.thinking)), finalText: JSON.stringify(finals) === JSON.stringify(expectedFinals), tools: calls.length };
  // Expand on demand after collecting performance metrics, then check full results.
  for (const block of toolBlocks) {
    if (!block.classList.contains("open")) block.querySelector(".tool-head").click();
  }
  const expected = fixture.turns.flatMap(t => t.calls);
  checks.fullResults = toolBlocks.every((block, index) => {
    const raw = expected[index].returned.result;
    const result = block.querySelector("[data-tool-result]");
    if (typeof raw === "string") return (result?.textContent || "").includes(raw.trim());
    const code = result?.querySelector('.json-view code, pre.json-view code');
    try { return JSON.stringify(JSON.parse(code?.textContent || "")) === JSON.stringify(raw); } catch { return false; }
  });
  trajectory.selectView("trajectory"); await settle();
  checks.trajectoryCalls = trajectory.events().filter(event => event.kind === "tool").length === fixture.expectedCalls.length;
  checks.downloads = [...messages.querySelectorAll("a[download]")].map(link => ({ name: link.textContent.trim(), href: link.getAttribute("href") }));
  trajectory.selectView("conversation");
  checks.passed = checks.callOrder && checks.thinkingExact && checks.finalText && checks.fullResults && checks.trajectoryCalls;
  return checks;
}

async function replay({ mode = "hidden", scenario = "complete", batch = 32, tickMs = 8 } = {}) {
  if (running) throw new Error("Replay already running");
  running = true; document.getElementById("start").disabled = true; document.getElementById("stop").disabled = false;
  setup(); let assistant; const appliedFrames = []; const started = performance.now();
  try {
    for (let index = 0; index < fixture.frames.length && !stopped; index += 1) {
      const frame = fixture.frames[index];
      appliedFrames.push(frame);
      if (frame.event === "replay_user") {
        trajectory.addUser({ content: frame.data.content }); assistant = makePresenter();
      } else {
        trajectory.ingest(frame);
        presentation.applyAssistantPresentationActions(assistant, presentation.assistantActionsFromSse(frame.event, frame.data));
      }
      metrics.framesApplied++;
      if (scenario === "cancelled" && index === Math.floor(fixture.frames.length / 2)) { stopped = true; }
      if (mode === "visible" && index === 10) trajectory.selectView("trajectory");
      if (mode === "toggle" && index % 500 === 0) trajectory.selectView(index % 1000 === 0 ? "trajectory" : "conversation");
      if (index % batch === batch - 1) await pause(tickMs);
    }
    if (stopped) {
      const frame = { event: "cancelled", data: { status: "cancelled" } };
      trajectory.ingest(frame); presentation.applyAssistantPresentationActions(assistant, presentation.assistantActionsFromSse(frame.event, frame.data));
    }
    await settle();
    metrics.elapsedMs = performance.now() - started;
    metrics.domNodes = document.querySelectorAll("#workspaceGrid *").length;
    metrics.longTasks = tasks.length; metrics.longTaskMs = tasks.reduce((a, b) => a + b, 0);
    metrics.maxFrameGapMs = Math.max(0, ...gaps); metrics.heapUsedBytes = performance.memory?.usedJSHeapSize ?? null;
    metrics.mode = mode; metrics.scenario = stopped ? "cancelled" : "complete";
    mutationObserver.disconnect(); longTaskObserver.disconnect(); cancelAnimationFrame(sampleFrame); sampleFrame = null;
    const measured = { ...metrics }; metrics = null;
    measured.content = await verifyContent(stopped, appliedFrames);
    window.replayResult = measured; summary.textContent = JSON.stringify(measured, null, 2);
    return measured;
  } finally { running = false; document.getElementById("start").disabled = false; document.getElementById("stop").disabled = true; }
}

document.getElementById("start").onclick = () => replay({ mode: params.get("mode") || "hidden" });
document.getElementById("stop").onclick = () => { stopped = true; };
window.addEventListener("pagehide", cleanup, { once: true });
async function restoreHistory() {
  setup();
  const view = createConversationPresenter({ messages, readOnly: true, attachmentUrl: file => `/files/${encodeURIComponent(file.file_id || file.id)}` });
  for (const node of projectConversationItems(fixtureConversation(fixture))) {
    if (node.type === 'user') view.addMessage('user', node.text, node.createdAt);
    else {
      const presenter = view.createAssistantMessage(node.createdAt, node.order?.sequence);
      presenters.push(presenter);
      presentation.applyAssistantPresentationActions(presenter, node.actions);
    }
  }
  trajectory.setHistory(fixtureHistory(fixture));
  await settle(); metrics = null;
  const result = await verifyContent(false);
  summary.textContent = JSON.stringify({ history: result }, null, 2);
  return result;
}
window.performanceReplay = { replay, restoreHistory, cleanup, fixture, fixtureHash, history: fixtureHistory(fixture) };
summary.textContent = `已就绪：${fixture.frames.length} 个固定增量，SHA256 ${fixtureHash}`;
