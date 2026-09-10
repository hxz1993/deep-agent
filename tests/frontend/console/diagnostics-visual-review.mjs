#!/usr/bin/env node
/**
 * Repeatable real-browser visual evidence. Does not use @playwright/test or
 * install dependencies. Fixtures are intercepted in a fresh browser context;
 * live mode permits only same-origin GET/HEAD and never sends chat/tool calls.
 *
 * node tests/frontend/console/diagnostics-visual-review.mjs
 * node tests/frontend/console/diagnostics-visual-review.mjs --mode live
 * Optional: --url URL --out-dir PATH --theme light --viewport 1736x1024
 *           --screens-only --playwright-path /path/to/playwright/index.mjs
 */
import assert from "node:assert/strict";
import { mkdir, readdir, writeFile, access, readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { homedir } from "node:os";
import { dirname, isAbsolute, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { FIXTURE_START, FIXTURE_END, FIXTURE_NOW, LOG_START, TIMEZONE, fixtureResponse, selectedLog, agents } from "../../../docs/design/23-diagnostics-and-logs/visual-rebuild-20260910/visual-fixtures.mjs";

const repo = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const require = createRequire(import.meta.url);
const args = process.argv.slice(2), option = (key, fallback) => args.includes(key) ? args[args.indexOf(key) + 1] : fallback;
const mode = option("--mode", "fixture"), theme = option("--theme", "light");
assert(["fixture", "live"].includes(mode), "--mode must be fixture or live");
const origin = new URL(option("--url", "http://127.0.0.1:8000/console/")).origin;
assert(["localhost", "127.0.0.1", "[::1]"].includes(new URL(origin).hostname), "Review targets a loopback development service only");
const baseURL = new URL("/console/", origin).href;
const viewportText = option("--viewport", "1736x1024");
const viewportParts = viewportText.match(/^(\d+)x(\d+)$/);
assert(viewportParts, "--viewport must be WIDTHxHEIGHT");
const viewport = { width: Number(viewportParts[1]), height: Number(viewportParts[2]) };
const outputArg = option("--out-dir", `output/playwright/TMP_to_delete-diagnostics-visual-review-${mode}-${Date.now()}`);
const output = isAbsolute(outputArg) ? outputArg : resolve(repo, outputArg);
const screenOnly = args.includes("--screens-only");
const startedAt = new Date().toISOString();
const report = { mode, theme, startedAt, viewport, output, note: "Screenshot and DOM measurement evidence only; a passing behavioral test does not prove visual equivalence.", checks: [], screenshots: [], layouts: {}, requests: [], blockedRequests: [], pageErrors: [] };
await mkdir(output, { recursive: true });

async function playwrightRuntime() {
  const candidates = [];
  const explicit = option("--playwright-path", process.env.DIAGNOSTICS_PLAYWRIGHT_PATH);
  if (explicit) candidates.push(resolve(explicit));
  try { candidates.push(require.resolve("playwright")); } catch { /* Use existing CLI cache below. */ }
  const npxCache = join(homedir(), ".npm", "_npx");
  try {
    const caches = await readdir(npxCache, { withFileTypes: true });
    for (const entry of caches.filter(item => item.isDirectory())) candidates.push(join(npxCache, entry.name, "node_modules", "playwright", "index.mjs"));
  } catch { /* No installation or network fallback. */ }
  for (const candidate of candidates) {
    try {
      await access(candidate); const runtime = await import(pathToFileURL(candidate));
      if (runtime.chromium) { await access(runtime.chromium.executablePath()); return { runtime, candidate }; }
    } catch { /* Try a runtime whose matching Chromium is already installed. */ }
  }
  throw new Error("No existing Playwright runtime found. Pass --playwright-path; this script does not install dependencies.");
}
const { runtime, candidate } = await playwrightRuntime();
report.playwrightRuntime = candidate;
let browser, context, page, closed = false;
const cleanup = async () => {
  if (closed) return;
  closed = true;
  try { await context?.close(); } finally { await browser?.close(); }
};
const signalHandlers = new Map();
for (const signal of ["SIGINT", "SIGTERM"]) {
  const handler = () => { void cleanup().finally(() => process.exit(signal === "SIGINT" ? 130 : 143)); };
  signalHandlers.set(signal, handler); process.once(signal, handler);
}
async function check(name, callback) {
  try { const detail = await callback(); report.checks.push({ name, passed: true, ...(detail === undefined ? {} : { detail }) }); }
  catch (error) {
    const entry = { name, passed: false, message: error.message }; report.checks.push(entry);
    console.error(`FAIL ${name}: ${error.message}`);
    try {
      if (page && !page.isClosed() && await first("#page-diagnostics").isVisible()) {
        const filename = `failure-${report.checks.length}.png`;
        await first("#page-diagnostics").screenshot({ path: join(output, filename), animations: "disabled", timeout: 1500 });
        report.screenshots.push(filename); entry.screenshot = filename;
      }
    } catch { /* Preserve the initial assertion if screenshotting also fails. */ }
  }
}
const first = selector => page.locator(selector).first();
const sessionParams = () => new URLSearchParams({ start: FIXTURE_START, end: FIXTURE_END, timezone: TIMEZONE, period: "168", limit: "50", offset: "0" });
const logParams = () => new URLSearchParams({ start: LOG_START, end: FIXTURE_END, period: "24" });
const hashFor = view => `#/diagnostics/${view === "logs" ? "logs" : `sessions/${view}`}${mode === "fixture" ? `?${view === "logs" ? logParams() : sessionParams()}` : ""}`;
const currentFilters = () => new URLSearchParams(new URL(page.url()).hash.split("?")[1]);
async function settleSessions() {
  await page.waitForFunction(() => {
    const root = document.getElementById("diagnosticsSessionsPage");
    return root && !root.classList.contains("hidden") && root.getAttribute("aria-busy") !== "true" && root.querySelector(".diagnostic-sessions-page");
  });
  await first("#diagnosticsSessionsPage h1").waitFor({ state: "visible" });
  await page.evaluate(() => document.fonts.ready);
}
async function settleLogs() {
  await page.waitForFunction(() => {
    const root = document.getElementById("diagnosticsLogPage");
    return root && !root.classList.contains("hidden") && root.getAttribute("aria-busy") === "false";
  });
  await page.evaluate(() => document.fonts.ready);
}
async function gotoView(view) {
  await page.goto(baseURL + hashFor(view), { waitUntil: "domcontentloaded" });
  await (view === "logs" ? settleLogs() : settleSessions());
  if (!args.includes("--viewport")) {
    const width = (await first("#page-diagnostics").boundingBox()).width;
    if (Math.abs(width - 1536) > 1) await page.setViewportSize({ width: Math.round(viewport.width + 1536 - width), height: 1024 });
  }
  // Let layout observers settle; not a network-idle wait (Console health can poll).
  await page.evaluate(() => new Promise(requestAnimationFrame));
}

// Image masters are content-only 1536×1024. Measurements remain useful when
// strings/counts differ; they deliberately do not make a raster equality claim.
const masterTargets = {
  logs: { title: [45, 54, 85, 44], workbench: [41, 147, 1452, 838], drawer: [1057, 148, 436, 836], firstRow: [50, 208, 999, 33] },
  overview: { title: [44, 53, 88, 44], metrics: [41, 173, 1455, 116], trend: [41, 305, 914, 254], recent: [41, 576, 914, 410] },
  list: { title: [44, 53, 88, 44], workbench: [41, 165, 1455, 825], columns: [55, 245, 1428, 46], firstRow: [55, 291, 1428, 65] },
};
const layoutSelectors = {
  logs: { title: "#diagnosticsLogPage h1", header: ".diagnostics-log-header", filters: "#diagnosticsFilters", workbench: "#diagnosticsSplit", drawer: "#diagnosticsDrawer", firstRow: "#diagnosticsRecords .diagnostics-row", rowSummary: "#diagnosticsRecords .diagnostics-summary", resultHeader: ".diagnostics-result-head", detailSummary: ".diagnostics-full-summary" },
  overview: { title: "#diagnosticsSessionsPage h1", header: "#diagnosticsSessionsPage .diagnostics-head", filters: ".diagnostic-session-filters", metrics: ".diagnostic-stats", trend: ".diagnostic-chart-section", recent: ".diagnostic-recent", firstQuestion: ".diagnostic-question[data-question-index]", username: ".diagnostic-username" },
  list: { title: "#diagnosticsSessionsPage h1", header: "#diagnosticsSessionsPage .diagnostics-head", filters: ".diagnostic-session-filters", workbench: ".diagnostic-list-workbench, .diagnostic-session-table", columns: ".diagnostic-session-columns", firstRow: ".diagnostic-session-row[data-session-id]", username: ".diagnostic-session-table .diagnostic-username", pagination: ".diagnostic-pagination" },
};
async function layout(view) {
  return page.evaluate(({ selectors, targets }) => {
    const main = document.getElementById("page-diagnostics"), mainBounds = main.getBoundingClientRect();
    const result = { main: { x: mainBounds.x, y: mainBounds.y, width: mainBounds.width, height: mainBounds.height }, theme: document.documentElement.dataset.theme, documentOverflow: document.documentElement.scrollWidth - innerWidth, elements: {} };
    for (const [key, selector] of Object.entries(selectors)) {
      const element = main.querySelector(selector); if (!element) { result.elements[key] = { missing: true }; continue; }
      const box = element.getBoundingClientRect(), style = getComputedStyle(element);
      const rectangle = [box.x - mainBounds.x, box.y - mainBounds.y, box.width, box.height].map(n => Math.round(n * 10) / 10);
      const text = document.createRange(); text.selectNodeContents(element); const textBounds = text.getBoundingClientRect();
      const textRectangle = [textBounds.x - mainBounds.x, textBounds.y - mainBounds.y, textBounds.width, textBounds.height].map(n => Math.round(n * 10) / 10);
      const compared = key === "title" ? textRectangle : rectangle;
      let scrollOwner = element.parentElement;
      while (scrollOwner && scrollOwner !== main && !(scrollOwner.scrollWidth > scrollOwner.clientWidth && ["auto", "scroll"].includes(getComputedStyle(scrollOwner).overflowX))) scrollOwner = scrollOwner.parentElement;
      const overflowOwner = scrollOwner && scrollOwner !== main ? { className: scrollOwner.className, clientWidth: scrollOwner.clientWidth, scrollWidth: scrollOwner.scrollWidth, x: scrollOwner.getBoundingClientRect().x - mainBounds.x, width: scrollOwner.getBoundingClientRect().width } : null;
      result.elements[key] = { rectangle, textRectangle, fontSize: style.fontSize, fontWeight: style.fontWeight, lineHeight: style.lineHeight, color: style.color, background: style.backgroundColor, borderColor: style.borderColor, radius: style.borderRadius, overflowX: style.overflowX, overflowOwner, clientWidth: element.clientWidth, scrollWidth: element.scrollWidth, ...(targets[key] ? { target: targets[key], delta: compared.map((value, i) => Math.round((value - targets[key][i]) * 10) / 10) } : {}) };
    }
    const lines = [...main.querySelectorAll(".diagnostic-trend .diagnostic-chart-grid, .diagnostic-trend .diagnostic-chart-axis")].map(element => element.getBoundingClientRect());
    if (lines.length) result.chartPlot = { x: Math.min(...lines.map(box => box.x)) - mainBounds.x, y: Math.min(...lines.map(box => box.y)) - mainBounds.y, width: Math.max(...lines.map(box => box.right)) - Math.min(...lines.map(box => box.x)), height: Math.max(...lines.map(box => box.bottom)) - Math.min(...lines.map(box => box.y)) };
    return result;
  }, { selectors: layoutSelectors[view], targets: masterTargets[view] });
}
async function capture(view, suffix = "") {
  const filename = `${view}${suffix ? `-${suffix}` : ""}.png`;
  const main = await first("#page-diagnostics").boundingBox();
  // A previous row click must not leave an unrelated hover state in the next page.
  await page.mouse.move(main.x + 5, main.y + 5);
  await first("#page-diagnostics").screenshot({ path: join(output, filename), animations: "disabled" });
  report.screenshots.push(filename); report.layouts[`${view}${suffix ? `-${suffix}` : ""}`] = await layout(view);
}
async function assertLayout(view, key = view) {
  const value = report.layouts[key];
  if (theme === "light") {
    const surface = await first(view === "logs" ? "#diagnosticsLogPage" : ".diagnostic-sessions-page").evaluate(node => getComputedStyle(node).backgroundColor);
    assert.equal(surface, "rgb(255, 255, 255)", "Query pages use the white Console surface");
  }
  assert(value, "A screenshot measurement must exist first");
  assert(value.documentOverflow <= 1, `Document horizontal overflow: ${value.documentOverflow}px`);
  if (!args.includes("--viewport")) assert.equal(Math.round(value.main.width), 1536, "Content width matches master canvas");
  assert.equal(Math.round(value.main.height), viewport.height, "Content fills viewport height");
  const title = value.elements.title;
  assert(!title.missing, "Page title present");
  assert(Number.parseFloat(title.fontSize) === 16, `Title hierarchy: ${title.fontSize}`);
  for (const name of ["header", "filters", "metrics", "trend", "recent", "columns", "pagination"]) {
    const item = value.elements[name]; if (!item || item.missing) continue;
    const [x, y, w] = item.rectangle;
    const scrollingTable = name === "columns" && value.main.width < 1300 && item.overflowOwner;
    if (scrollingTable) assert(item.overflowOwner.x >= -1 && item.overflowOwner.x + item.overflowOwner.width <= value.main.width + 1, "Table scroll container stays inside page");
    else assert(x >= -1 && x + w <= value.main.width + 1, `${name} exceeds content horizontally (${x}, ${w})`);
    assert(y >= -1, `${name} starts above page`);
  }
  const body = value.elements.rowSummary || value.elements.username;
  if (body && !body.missing) assert(Number.parseFloat(body.fontSize) >= 12 && Number.parseFloat(body.fontSize) <= 13, `Body readability: ${body.fontSize}`);
  if (view === "logs" && !value.elements.firstRow.missing) {
    const height = value.elements.firstRow.rectangle[3];
    assert(height >= 26 && height <= 38, `Dense log row height: ${height}px`);
  }
  if (value.main.width >= 1300) {
    for (const name of view === "list" ? ["pagination"] : view === "overview" ? ["recent"] : []) {
      const item = value.elements[name]; if (item && !item.missing) assert(item.rectangle[1] + item.rectangle[3] <= value.main.height + 1, `${name} must remain in the desktop workbench, not below the viewport`);
    }
  }
  return value;
}
async function choose(root, name, value) {
  const host = page.locator(`${root} [data-diagnostic-control="${name}"]`).first();
  await host.locator(".console-select-trigger").click();
  await host.locator(".console-select-menu").waitFor({ state: "visible" });
  await host.locator(`[data-value="${value}"]`).click();
}
const diagnosticCalls = prefix => report.requests.filter(item => item.path.startsWith(prefix));

async function logReview() {
  await gotoView("logs");
  await check("navigation: diagnostic group precedes bottom System group and API docs", async () => {
    assert.equal(await first("#consoleResourceNav .diagnostic-nav-label").innerText(), "诊断");
    assert.deepEqual(await page.locator("#consoleResourceNav [data-diagnostics-nav]").evaluateAll(nodes => nodes.map(node => node.dataset.diagnosticsNav)), ["logs", "sessions"]);
    assert.deepEqual(await page.locator(".console-nav-footer [data-tab]").evaluateAll(nodes => nodes.map(node => node.dataset.tab)), ["global-settings", "catalog"]);
    assert.equal(await page.locator('.console-nav-footer a[title="接口文档"]').count(), 1);
    const diagnostic = await first(".diagnostic-nav-label").boundingBox(), system = await first(".console-nav-footer .resource-group-label").boundingBox();
    assert(diagnostic.y < system.y);
  });
  const rows = page.locator("#diagnosticsRecords [data-record-index]");
  await check("logs: real rows or explicit truthful empty state", async () => {
    const count = await rows.count();
    if (mode === "fixture") assert.equal(count, 200);
    else if (!count) assert(await first("#diagnosticsEmpty").isVisible(), "Live empty state is visible");
    const ids = await page.locator('#diagnosticsRecords [data-open-session]').evaluateAll(nodes => nodes.map(node => ({ text:node.textContent, id:node.dataset.openSession })));
    assert(ids.every(item => item.text === item.id.slice(0, 8)), 'Log rows shorten display only, preserving full navigation ID');
    const entries = await rows.evaluateAll(nodes => nodes.map((node, index) => ({ sequence:node.querySelector('.diagnostics-sequence')?.textContent, expected:String(index + 1), height:node.getBoundingClientRect().height, font:getComputedStyle(node.querySelector('.diagnostics-summary')).fontSize })));
    assert(entries.every(item => item.sequence === item.expected && item.height === 26 && item.font === '12px'));
    assert.equal(await page.locator('#diagnosticsLogPage .diagnostics-footer').count(), 0);
    assert.equal(await page.locator('.diagnostics-result-title #diagnosticsMeta').count(), 1);
    assert.equal(await page.locator('.diagnostics-result-title #diagnosticsMore').count(), 1);
    return { loadedRows: count };
  });
  if (await rows.count()) {
    await rows.nth(mode === "fixture" ? 10 : 0).click();
    await first("#diagnosticsDrawer h2").waitFor({ state: "visible" });
  }
  await capture("logs");
  await check("logs: master-sized layout and font boundaries", () => assertLayout("logs"));
  await check("logs: only Session ID has actions; all fields use compact label/value columns", async () => {
    const keys = await page.locator("#diagnosticsDrawer [data-copy-id]").evaluateAll(nodes => nodes.map(node => node.dataset.copyId));
    assert(keys.every(key => key === "session_id"), `Unexpected copy actions: ${keys}`);
    if (mode === "fixture") assert.deepEqual(keys, ["session_id"]);
    const alignment = await page.locator("#diagnosticsDrawer .diagnostics-property").evaluateAll(nodes => nodes.map(node => {
      const label = node.querySelector("dt").getBoundingClientRect(), value = node.querySelector("dd").getBoundingClientRect();
      return { dx: value.x - label.right, dy: Math.abs(value.y - label.y) };
    }));
    assert(alignment.every(row => row.dx >= 0 && row.dy < 2));
    assert.equal(await page.locator("#diagnosticsDrawer details, #diagnosticsDrawer summary").count(), 0, "All log fields are flat, never collapsed");
    const fields = await page.locator("#diagnosticsDrawer .diagnostics-property").evaluateAll(nodes => nodes.map(node => ({ visible: node.getClientRects().length > 0, labelSize: getComputedStyle(node.querySelector("dt")).fontSize, valueSize: getComputedStyle(node.querySelector("code")).fontSize })));
    assert(fields.every(field => field.visible && field.labelSize === "12px" && ["12px", "13px"].includes(field.valueSize)), "Flat fields use compact Console typography");
    return keys;
  });
  if (mode === "live" || screenOnly) return;
  await check("logs: selecting/copying is non-navigating", async () => {
    const before = page.url(); await page.locator('#diagnosticsDrawer [data-copy-id="session_id"]').click();
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), selectedLog.session_id);
    assert.equal(page.url(), before); assert(await first("#diagnosticsDrawer").isVisible());
  });
  await check("logs: dropdown keyboard, outside dismissal and literal source query", async () => {
    const trigger = first('#diagnosticsLogPage [data-diagnostic-control="source"] .console-select-trigger');
    await trigger.focus(); await page.keyboard.press("ArrowDown");
    assert.equal(await trigger.getAttribute("aria-expanded"), "true");
    await page.keyboard.press("Escape"); assert.equal(await trigger.getAttribute("aria-expanded"), "false");
    await trigger.click(); await first("#diagnosticsLogPage h1").click(); assert.equal(await trigger.getAttribute("aria-expanded"), "false");
    await choose("#diagnosticsLogPage", "source", "gateway"); await first("#diagnosticsQuery").click(); await settleLogs();
    assert.equal(new URLSearchParams(diagnosticCalls("/api/v1/diagnostics/logs").at(-1).query).get("source"), "gateway");
    assert((await page.locator("#diagnosticsRecords .diagnostics-source").allTextContents()).every(text => text === "网关"));
    await first("#diagnosticsReset").click(); await settleLogs();
  });
  await check("logs: Run ID remains a value with no input, filtering, copy or jump", async () => {
    await rows.nth(10).click();
    assert.equal(await page.locator('#diagnosticsRun, #diagnosticsDrawer [data-filter-id], #diagnosticsDrawer [data-copy-id="run_id"]').count(), 0);
    assert.equal(await first('#diagnosticsDrawer [data-field="run_id"] code').innerText(), selectedLog.run_id);
    assert.equal(new URLSearchParams(diagnosticCalls("/api/v1/diagnostics/logs").at(-1).query).get("run_id"), null);
  });
  await check("logs: aligned rows with dropdowns and Session ID above search and query", async () => {
    assert.equal(await page.locator("#diagnosticsAdvanced, #diagnosticsAdvancedPanel").count(), 0);
    const keyword = await first("#diagnosticsKeyword").locator("..").boundingBox(), session = await first("#diagnosticsSession").locator("..").boundingBox();
    const query = await first("#diagnosticsQuery").boundingBox();
    const primary = await first(".diagnostics-primary-row").boundingBox(), secondary = await first(".diagnostics-search-row").boundingBox();
    assert(Math.abs(primary.x - secondary.x) < 1 && Math.abs(primary.width - secondary.width) < 1);
    assert(Math.abs(keyword.y - query.y) < 2 && keyword.y >= session.y + session.height);
    assert.equal(await first("#diagnosticsSession").evaluate(node => !!node.parentElement.querySelector("svg")), true);
    assert.equal(await page.locator("#diagnosticsRefresh").count(), 0);
    assert.equal(await first("#diagnosticsReset").evaluate(node => getComputedStyle(node).borderTopWidth), "0px");
    await capture("logs", "filters");
  });
  await check("logs: focused keyword and Session ID have no inset outline or glow", async () => {
    for (const selector of ["#diagnosticsKeyword", "#diagnosticsSession"]) {
      await first(selector).focus();
      const styles = await first(selector).evaluate(node => [node, node.parentElement].map(item => { const style = getComputedStyle(item); return { outline: style.outlineStyle, shadow: style.boxShadow }; }));
      assert(styles.every(item => item.outline === "none" && item.shadow === "none"), JSON.stringify(styles));
    }
  });
  await check("logs: Session ID jump opens correct readonly detail", async () => {
    await rows.first().click(); await page.locator("#diagnosticsDrawer [data-open-session]").click();
    await page.waitForFunction(id => location.hash.includes(`/sessions/${id}`), selectedLog.session_id);
    await first("#diagnosticsSessionsPage .diagnostic-detail-header").waitFor({ state: "visible" });
    assert.equal(currentFilters().get("run_id"), null);
    assert.equal(await page.locator("#diagnosticsSessionsPage textarea, #diagnosticsSessionsPage input[type=file], #diagnosticsSessionsPage .composer").count(), 0);
    assert.equal(await page.locator("#diagnosticsSessionsPage [data-detail-logs], #diagnosticsSessionsPage [data-diagnostic-run-logs]").count(), 0);
    await page.goBack(); await settleLogs(); assert.equal(currentFilters().get("run_id"), null);
  });
  await check("logs: cursor appends rather than replacing rows", async () => {
    await gotoView("logs"); await first("#diagnosticsMore").click(); await settleLogs();
    assert.equal(await rows.count(), 240);
    assert.equal(await rows.last().locator('.diagnostics-sequence').innerText(), '240');
    assert.equal(new URLSearchParams(diagnosticCalls("/api/v1/diagnostics/logs").at(-1).query).get("cursor"), "200");
  });
}

async function sessionReview() {
  await gotoView("overview");
  await capture("overview");
  await check("overview: master-sized layout and font boundaries", () => assertLayout("overview"));
  await check("overview: chart and metrics use loaded data", async () => {
    assert.equal(await page.locator("[data-stat]").count(), 4);
    if (mode === "fixture") {
      assert.equal((await page.locator('[data-stat="question_count"] strong').innerText()).replaceAll(",", ""), "486");
      assert.equal(await page.locator("[data-day-index]").count(), 7);
      assert.equal(await page.locator("[data-question-index]").count(), 10);
    }
  });
  await check("overview: account labels contain username only", async () => {
    assert.equal(await page.locator("#diagnosticsSessionsPage .diagnostic-user img, #diagnosticsSessionsPage .diagnostic-user [class*=avatar]").count(), 0);
    const labels = await page.locator("#diagnosticsSessionsPage .diagnostic-username").allTextContents();
    assert(labels.every(label => !label.includes("@invalid.example")), "Emails are not part of account labels");
    if (mode === "fixture") assert(labels.some(label => label === "chenliang"));
  });
  await check("overview: compact single-line questions and a Console-style view switch", async () => {
    assert.equal(await page.locator(".agent-view-switch [data-session-view] svg").count(), 2);
    assert.equal(await first('.diagnostic-session-filters button[type="submit"]').innerText(), "查询");
    const rows = await page.locator(".diagnostic-question[data-question-index]").evaluateAll(nodes => nodes.map(node => {
      const title = node.querySelector("strong").getBoundingClientRect(), meta = node.querySelector(".diagnostic-question-meta").getBoundingClientRect();
      return { height: node.getBoundingClientRect().height, metaWidth: meta.width, alignment: Math.abs(title.y + title.height / 2 - meta.y - meta.height / 2) };
    }));
    assert(rows.every(row => row.height <= 40 && row.alignment < 2));
    const metadata = await page.locator(".diagnostic-question-meta").evaluateAll(nodes => nodes.map(node => ({ right: node.getBoundingClientRect().right, width: node.getBoundingClientRect().width, display: getComputedStyle(node).display, time: node.querySelector("time").textContent, fullTime: node.querySelector("time").title })));
    assert(metadata.every(item => item.display === "flex" && /^\d{2}-\d{2} \d{2}:\d{2}$/.test(item.time)), "Natural-width metadata uses short dates");
    assert(metadata.every(item => Math.abs(item.right - metadata[0].right) < 1), "Metadata is right aligned as a whole");
    assert(metadata.every(item => item.fullTime.length > item.time.length), "Full timestamp remains available on hover");
  });
  await check("overview: chart resizing preserves circular points and text scale", async () => {
    for (const width of [1594, 1100, viewport.width]) {
      await page.setViewportSize({ ...viewport, width });
      await page.waitForFunction(() => { const svg = document.querySelector(".diagnostic-trend"); return !svg || Math.abs(svg.viewBox.baseVal.width - svg.getBoundingClientRect().width) < 2; });
      const points = await page.locator(".diagnostic-chart-point").evaluateAll(nodes => nodes.map(node => { const rect = node.getBoundingClientRect(); return [rect.width, rect.height]; }));
      assert(points.every(([w, h]) => Math.abs(w - 9) < .1 && Math.abs(h - 9) < .1));
    }
  });
  await check("sessions: fixed second row and content position survive custom time, reset and view switching", async () => {
    const initial = await first(".diagnostic-session-scroll").boundingBox();
    const reset = first('.diagnostic-session-filters [data-session-action="reset"]');
    assert.equal(await reset.innerText(), "重置"); assert.equal(await reset.evaluate(node => getComputedStyle(node).borderTopWidth), "0px");
    const resetBox = await reset.boundingBox(), queryBox = await first('.diagnostic-session-filters button[type="submit"]').boundingBox();
    assert(resetBox.x > queryBox.x && Math.abs(resetBox.y - queryBox.y) < 2);
    const url = new URL(page.url()); url.hash += "&period=custom";
    const params = new URLSearchParams(url.hash.split("?")[1]); params.set("period", "custom");
    await page.goto(`${baseURL}#/diagnostics/sessions/overview?${params}`); await settleSessions();
    const apply = await first('.diagnostics-custom-time button[type="submit"]').boundingBox();
    const toggle = await first(".diagnostic-filter-secondary .diagnostic-view-switch").boundingBox();
    const agent = await first('.diagnostic-session-filters [data-diagnostic-control="agent_id"]').boundingBox();
    const title = await first("#diagnosticsSessionsPage h1").boundingBox();
    assert(Math.abs(apply.y - toggle.y) < 1 && toggle.x > apply.x);
    if (viewport.width >= 1500) assert(Math.abs(agent.y - title.y) < 4);
    assert(Math.abs((await first(".diagnostic-session-scroll").boundingBox()).y - initial.y) < 1);
    await first('.diagnostics-custom-time button[type="submit"]').click(); await settleSessions();
    assert.equal(currentFilters().get("period"), "custom");
    await capture("overview", "custom");
    await first('[data-session-action="reset"]').click(); await settleSessions();
    assert(Math.abs((await first(".diagnostic-session-scroll").boundingBox()).y - initial.y) < 1);
    await first('[data-session-view="list"]').click(); await settleSessions();
    assert(Math.abs((await first(".diagnostic-session-scroll").boundingBox()).y - initial.y) < 1);
    await gotoView("overview");
  });
  if (mode === 'fixture') await check('list: delayed loading stays in header without overlapping controls or shifting rows', async () => {
    const match = url => new URL(url).pathname === '/api/v1/diagnostics/sessions';
    let release; const gate = new Promise(resolve => { release = resolve; });
    const handler = async route => { await gate; await route.fallback(); };
    await page.route(match, handler);
    let before;
    try {
      await page.locator('[data-session-view="list"]').click();
      const status = first('.diagnostic-loading'); await status.getByText('正在加载…').waitFor();
      const statusBox = await status.boundingBox(), toolbar = await first('.diagnostic-list-toolbar').boundingBox();
      before = await first('.diagnostic-session-columns').boundingBox();
      assert(statusBox.y + statusBox.height <= toolbar.y);
      assert.equal(await status.evaluate(node => getComputedStyle(node).position), 'static');
      await capture('list', 'loading');
    } finally { release(); await page.unroute(match, handler); }
    await settleSessions();
    assert(Math.abs((await first('.diagnostic-session-columns').boundingBox()).y - before.y) < 1);
  });
  else { await page.locator('[data-session-view="list"]').click(); await settleSessions(); }
  await capture("list");
  await check("list: master-sized layout and font boundaries", () => assertLayout("list"));
  await check("list: username cells have no avatars/email", async () => {
    assert.equal(await page.locator(".diagnostic-session-table img, .diagnostic-session-table [class*=avatar]").count(), 0);
    assert(!(await first(".diagnostic-session-table").innerText()).includes("@invalid.example"));
    if (mode === "fixture") assert.equal(await page.locator(".diagnostic-session-row[data-session-id]").count(), 50);
  });
  await check("list: six compact columns show copy, eight-character ID and question", async () => {
    assert.equal(await page.locator(".diagnostic-session-title [data-copy-session]").count(), 0);
    const rows = await page.locator(".diagnostic-session-row[data-session-id]").evaluateAll(nodes => nodes.map(node => {
      const secondary = node.querySelector(".diagnostic-session-id");
      return { height: node.getBoundingClientRect().height, cells: node.children.length, text: secondary.textContent, id: secondary.querySelector("code").textContent, expected: node.dataset.sessionId.slice(0, 8), idColor: getComputedStyle(secondary.querySelector("code")).color, questionColor: getComputedStyle(secondary.querySelector("p")).color, copy: !!secondary.querySelector("[data-copy-session] + code") };
    }));
    assert(rows.every(row => row.height <= 52 && row.cells === 6 && row.id === row.expected && row.idColor === row.questionColor && row.copy && row.text.includes("·") && !row.text.includes("最近问题：")));
    assert.equal(await page.locator('.diagnostic-session-row [aria-label="进入会话详情"]').count(), 0);
  });
  await check("list: borderless pagination stays below the table at the workbench bottom", async () => {
    assert.equal(await page.locator(".diagnostic-list-toolbar .diagnostic-pagination").count(), 0);
    assert.equal(await page.locator(".diagnostic-list-workbench > footer .diagnostic-pagination").count(), 1);
    assert.equal(await page.locator('.diagnostic-pagination [data-diagnostic-control="limit"]').count(), 1);
    assert.equal(await page.locator('.diagnostic-page-range, [data-session-page]').count(), 0);
    assert.match(await first('.diagnostic-page-current').innerText(), /^\d+ \/ \d+$/);
    const pagination = await first(".diagnostic-pagination").boundingBox();
    const table = await first(".diagnostic-session-table-scroll").boundingBox(), workbench = await first(".diagnostic-list-workbench").boundingBox();
    assert(pagination.y >= table.y + table.height);
    assert(Math.abs(pagination.y + pagination.height - workbench.y - workbench.height) < 2);
    assert.equal(await first('.diagnostic-pagination').evaluate(node => getComputedStyle(node).borderTopWidth), '0px');
  });
  await check("list: bottom page-size menu opens upward and remains usable", async () => {
    await choose('#diagnosticsSessionsPage', 'limit', '20'); await settleSessions();
    assert.equal(currentFilters().get('limit'), '20');
    await choose('#diagnosticsSessionsPage', 'limit', '50'); await settleSessions();
    assert.equal(currentFilters().get('limit'), '50');
  });
  if (mode === "live") {
    await check("live: user labels use supplied names with truthful ID fallbacks", async () => {
      const counts = await page.locator('.diagnostic-session-table .diagnostic-username').evaluateAll(nodes => ({ total: nodes.length, named: nodes.filter(node => node.textContent.trim() && node.textContent.trim() !== node.title).length, idFallback: nodes.filter(node => node.textContent.trim() === node.title).length, blank: nodes.filter(node => !node.textContent.trim()).length }));
      assert.equal(counts.blank, 0, 'No blank platform user labels');
      return counts; // Do not record names or full platform IDs in live reports.
    });
    await check("live: saved session detail remains strictly readonly", async () => {
      const rows = page.locator(".diagnostic-session-row[data-session-id]");
      if (!(await rows.count())) return { skipped: "No saved sessions in the current live range" };
      const historyRead = page.waitForResponse(response => /\/api\/v1\/diagnostics\/sessions\/[^/]+\/history$/.test(new URL(response.url()).pathname));
      await rows.first().click();
      const response = await historyRead;
      assert(response.ok(), `History read failed with HTTP ${response.status()}`);
      await page.waitForFunction(() => {
        const messages = document.querySelector('#diagnosticsSessionsPage [data-session-role="messages"]');
        return messages && !messages.querySelector('.diagnostic-detail-error[role="status"]');
      });
      assert.equal(await page.locator("#diagnosticsSessionsPage textarea, #diagnosticsSessionsPage input[type=file], #diagnosticsSessionsPage .composer, #diagnosticsSessionsPage .hitl-widget").count(), 0);
      assert.equal(await page.locator("#diagnosticsSessionsPage [data-detail-logs], #diagnosticsSessionsPage [data-diagnostic-run-logs]").count(), 0);
      assert.equal(await page.locator('#diagnosticsSessionsPage [data-session-role="messages"] [role="alert"]').count(), 0);
      await page.goBack(); await settleSessions();
      return { historyStatus: response.status(), returnedToList: true };
    });
    return;
  }
  if (screenOnly) return;
  await check("list: bounded column resizing keeps headers and rows aligned", async () => {
    const handle = first('[data-resize-column="0"]');
    assert.equal(await page.locator('[data-resize-column]').count(), 5);
    const readWidths = () => first('.diagnostic-session-columns').evaluate(node => [...node.children].map(child => child.getBoundingClientRect().width));
    const original = await readWidths(); const box = await handle.boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2); await page.mouse.down();
    try { await page.mouse.move(box.x + box.width / 2 + 1000, box.y + box.height / 2, { steps: 5 }); } finally { await page.mouse.up(); }
    const resized = await readWidths();
    assert(resized[0] >= original[0] && resized[0] <= Math.max(720, original[0]) + 1);
    if (original[0] < 720) assert(resized[0] > original[0]);
    assert(resized[1] >= Math.min(80, original[1]) - 1);
    assert(Math.abs(resized.reduce((sum, value) => sum + value, 0) - original.reduce((sum, value) => sum + value, 0)) < 1);
    const alignment = await page.locator('.diagnostic-session-row').evaluateAll(nodes => nodes.slice(0, 8).map(node => [...node.children].map(child => child.getBoundingClientRect().x)));
    assert(alignment.every(row => row.every((x, i) => Math.abs(x - alignment[0][i]) < 1)), 'Headers and body use one column width definition');
    await handle.focus(); await page.keyboard.press('ArrowLeft'); const keyboard = await readWidths(); assert(keyboard[0] < resized[0]);
    await capture('list', 'resized');
  });
  await check("list: copy reports the existing toast and does not enter detail", async () => {
    const url = page.url(), copy = first('[data-copy-session]'), id = await copy.getAttribute('data-copy-session');
    await copy.click(); assert.equal(await page.evaluate(() => navigator.clipboard.readText()), id);
    await page.getByText('会话 ID 已复制', { exact: true }).waitFor({ state: 'visible' }); assert.equal(page.url(), url);
  });
  await check("list: borderless user and status align right of search; focus has no glow", async () => {
    assert.equal(await page.locator('.diagnostic-session-filters input[name="user_id"]').count(), 0);
    const userInput = first('.diagnostic-list-options input[name="user_id"]');
    const user = await userInput.boundingBox(); const search = first('.diagnostic-list-search input[name="query"]');
    const searchBox = await search.boundingBox(); assert(user.x >= searchBox.x + searchBox.width);
    const status = await first('[data-diagnostic-control="latest_status"]').boundingBox();
    assert(status.x >= user.x + user.width && Math.abs(status.y - user.y) < 2);
    assert.equal(await userInput.evaluate(node => getComputedStyle(node).borderTopColor), 'rgba(0, 0, 0, 0)');
    await search.focus();
    const styles = await search.evaluate(node => [node, node.parentElement].map(item => ({ outline: getComputedStyle(item).outlineStyle, shadow: getComputedStyle(item).boxShadow })));
    assert(styles.every(item => item.outline === 'none' && item.shadow === 'none'), JSON.stringify(styles));
  });
  await check("sessions: overview/list preserves common filters and fixed time", async () => {
    await choose("#diagnosticsSessionsPage", "agent_id", agents[0].id); await settleSessions();
    const before = currentFilters(); await page.locator('[data-session-view="overview"]').click(); await settleSessions();
    for (const key of ["agent_id", "start", "end", "timezone"]) assert.equal(currentFilters().get(key), before.get(key), `Preserve ${key}`);
    await page.locator('[data-session-view="list"]').click(); await settleSessions();
    assert.equal(currentFilters().get("agent_id"), agents[0].id);
    await choose("#diagnosticsSessionsPage", "agent_id", ""); await settleSessions();
  });
  await check("sessions: search submits query without losing common filters", async () => {
    const before = currentFilters(); const search = first('#diagnosticsSessionsPage input[name="query"]');
    await search.fill("费用"); await search.press("Enter"); await settleSessions();
    assert.equal(currentFilters().get("query"), "费用"); assert.equal(currentFilters().get("start"), before.get("start"));
    assert((await page.locator(".diagnostic-session-name strong").allTextContents()).every(title => title.includes("费用")));
    await first('[data-session-view="overview"]').click(); await settleSessions();
    assert.equal(await page.locator('#diagnosticsSessionsPage input[name="user_id"]').count(), 0);
    const overviewCall = new URLSearchParams(diagnosticCalls('/api/v1/diagnostics/sessions/overview').at(-1).query);
    for (const key of ['query', 'user_id', 'latest_status', 'has_questions']) assert(!overviewCall.has(key), `Overview excludes list-only ${key}`);
    assert(!currentFilters().get('query'));
    await first('[data-session-view="list"]').click(); await settleSessions(); assert.equal(currentFilters().get('query'), '费用');
    await search.fill(""); await search.press("Enter"); await settleSessions();
  });
  await check("sessions: username picker submits platform ID, not display name", async () => {
    const input = first('#diagnosticsSessionsPage input[name="user_id"]');
    await input.click();
    const options = await first('#diagnosticLoadedUsers').boundingBox(), workbench = await first('.diagnostic-list-workbench').boundingBox();
    assert(options.x >= workbench.x && options.x + options.width <= viewport.width, 'User options stay inside the list workbench');
    await first('[data-loaded-user="visual-user-01"]').click(); await settleSessions();
    assert.equal(await input.inputValue(), "chenliang"); assert.equal(currentFilters().get("user_id"), "visual-user-01");
    await input.press("Enter"); await settleSessions(); assert.equal(currentFilters().get("user_id"), "visual-user-01");
    await input.click(); await first('[data-loaded-user=""]').click(); await settleSessions(); assert(!currentFilters().get("user_id"));
  });
  await check("sessions: pagination advances stable rows and retains range", async () => {
    const before = currentFilters(); const row = first(".diagnostic-session-row[data-session-id]"); const original = await row.getAttribute("data-session-id");
    await page.locator('[data-session-action="next"]').click(); await settleSessions();
    assert.equal(currentFilters().get("offset"), "50"); assert.equal(currentFilters().get("start"), before.get("start"));
    assert.notEqual(await row.getAttribute("data-session-id"), original);
    await page.locator('[data-session-action="previous"]').click(); await settleSessions(); assert.equal(await row.getAttribute("data-session-id"), original);
    await page.locator('[data-session-action="next"]').click(); await settleSessions();
    await page.locator('[data-session-action="next"]').click(); await settleSessions();
    assert.equal(currentFilters().get("offset"), "100"); assert.equal(await page.locator(".diagnostic-session-row[data-session-id]").count(), 28);
    assert.equal(await first('.diagnostic-page-current').innerText(), '3 / 3'); assert(await first('[data-session-action="next"]').isDisabled());
    await page.locator('[data-session-action="previous"]').click(); await settleSessions();
    await page.locator('[data-session-action="previous"]').click(); await settleSessions();
  });
  await check("sessions: detail return restores list page and inner scroll", async () => {
    await page.locator('[data-session-action="next"]').click(); await settleSessions();
    const scroller = first(await page.locator(".diagnostic-session-table-scroll").count() ? ".diagnostic-session-table-scroll" : ".diagnostic-session-scroll");
    const before = await scroller.evaluate(element => { element.scrollTop = 300; return element.scrollTop; });
    assert(before >= 250, "50-row list has an independent scrolling viewport");
    const row = page.locator(".diagnostic-session-row[data-session-id]").nth(6), id = await row.getAttribute("data-session-id");
    await row.click(); await first(".diagnostic-detail-header").waitFor({ state: "visible" });
    assert(new URL(page.url()).hash.includes(`/sessions/${id}`));
    await page.goBack(); await settleSessions(); assert.equal(currentFilters().get("offset"), "50");
    const restored = await scroller.evaluate(element => element.scrollTop);
    assert(Math.abs(restored - before) <= 2, `List scroll changed from ${before} to ${restored}`);
    await page.locator('[data-session-action="previous"]').click(); await settleSessions();
  });
  await check("sessions: recently asked question focuses exact readonly message", async () => {
    await page.locator('[data-session-view="overview"]').click(); await settleSessions();
    await first("[data-question-index]").click();
    await page.waitForFunction(() => /#\/diagnostics\/sessions\/visual-session-/.test(location.hash));
    const params = currentFilters(); assert(params.get("event_id")?.startsWith("visual-question-")); assert(params.get("run_id")?.startsWith("visual-run-"));
    await first("#diagnosticsSessionsPage .diagnostic-detail-header").waitFor({ state: "visible" });
    await page.waitForFunction(() => document.querySelector('#diagnosticsSessionsPage [data-session-role="messages"]')?.children.length > 0);
    await page.waitForFunction(eventId => document.querySelector("#diagnosticsSessionsPage .diagnostic-message-focus")?.dataset.conversationItemId === `message:${eventId}`, params.get("event_id"));
    assert.equal(await first(".diagnostic-message-marker").innerText(), "定位消息");
    assert.equal(await first(".diagnostic-message-focus > .bubble-wrap").evaluate(node => getComputedStyle(node).outlineStyle), "none");
    assert.equal(await page.locator("#diagnosticsSessionsPage textarea, #diagnosticsSessionsPage input[type=file], #diagnosticsSessionsPage .composer, #diagnosticsSessionsPage .hitl-widget").count(), 0);
    assert.equal(await page.locator("#diagnosticsSessionsPage [data-detail-logs], #diagnosticsSessionsPage [data-diagnostic-run-logs]").count(), 0);
    const filename = "session-detail-readonly.png"; await first("#page-diagnostics").screenshot({ path: join(output, filename), animations: "disabled" }); report.screenshots.push(filename);
    await first('#diagnosticsSessionsPage [data-chat-view="trajectory"]').click();
    const exportButton = first('#diagnosticsSessionsPage [data-session-role="exportTrajectoryBtn"]');
    await page.waitForFunction(() => document.querySelector('#diagnosticsSessionsPage [data-session-role="exportTrajectoryBtn"]')?.disabled === false);
    const downloaded = page.waitForEvent("download"); await exportButton.click();
    const download = await downloaded;
    const exported = JSON.parse(await readFile(await download.path(), "utf8"));
    assert.equal(exported.source, "history"); assert(exported.events.length > 0);
    assert.equal(exported.session_id, new URL(page.url()).hash.split("?")[0].split("/").at(-1));
    await download.delete();
    await page.goBack(); await settleSessions(); assert(new URL(page.url()).hash.startsWith("#/diagnostics/sessions/overview"));
  });
  await check("sessions: all questions enters filtered session list without modal", async () => {
    await gotoView("overview");
    await page.locator('[data-session-action="all-questions"]').click(); await settleSessions();
    assert(new URL(page.url()).hash.startsWith("#/diagnostics/sessions/list")); assert.equal(currentFilters().get("has_questions"), "true");
    assert.equal(await page.locator("dialog[open]").count(), 0);
  });
}

try {
  browser = await runtime.chromium.launch({ headless: true, executablePath: runtime.chromium.executablePath() });
  context = await browser.newContext({ viewport, deviceScaleFactor: 1, timezoneId: TIMEZONE, locale: "zh-CN", colorScheme: "light", reducedMotion: "reduce", serviceWorkers: "block" });
  await context.grantPermissions(["clipboard-read", "clipboard-write"], { origin });
  await context.addInitScript(themeValue => { localStorage.setItem("da_theme", themeValue); }, theme);
  if (mode === "fixture") await context.addInitScript(fixedISO => {
    const NativeDate = Date, now = NativeDate.parse(fixedISO);
    window.Date = class extends NativeDate { constructor(...values) { super(...(values.length ? values : [now])); } static now() { return now; } };
  }, FIXTURE_NOW);
  await context.route("**/*", async route => {
    const request = route.request(), url = new URL(request.url());
    if (!["http:", "https:"].includes(url.protocol)) return route.continue();
    const safe = { method: request.method(), path: mode === "live" ? url.pathname.replace(/(\/sessions\/)[^/]+/g, "$1:id") : url.pathname, ...(mode === "fixture" ? { query: url.search.slice(1) } : {}) };
    if (url.origin !== origin || !["GET", "HEAD"].includes(request.method())) {
      report.blockedRequests.push({ ...safe, reason: url.origin !== origin ? "external-network" : "mutation-method" }); return route.abort("blockedbyclient");
    }
    if (url.pathname.startsWith("/api/") || ["/health", "/info"].includes(url.pathname)) report.requests.push(safe);
    if (mode === "fixture") {
      const body = fixtureResponse(url.href);
      if (body !== undefined) {
        const status = body.__httpStatus || 200; const json = { ...body }; delete json.__httpStatus;
        return route.fulfill({ status, contentType: "application/json; charset=utf-8", body: JSON.stringify(Array.isArray(body) ? body : json), headers: { "Cache-Control": "no-store" } });
      }
    }
    return route.continue();
  });
  page = await context.newPage(); page.setDefaultTimeout(10_000); page.setDefaultNavigationTimeout(20_000);
  page.on("pageerror", error => report.pageErrors.push(error.message));
  await check("log review sequence", logReview);
  await check("session review sequence", sessionReview);
  await check("browser has no unhandled page errors", () => assert.deepEqual(report.pageErrors, []));
  await check("review made no mutation requests", () => assert.deepEqual(report.blockedRequests.filter(item => item.reason === "mutation-method"), []));
  await check("fixture usernames do not fetch avatar URLs", () => assert.deepEqual(report.blockedRequests.filter(item => item.reason === "external-network"), []));
} catch (error) {
  report.checks.push({ name: "harness startup", passed: false, message: error.stack || error.message });
} finally {
  await cleanup();
  for (const [signal, handler] of signalHandlers) process.removeListener(signal, handler);
  report.completedAt = new Date().toISOString(); report.browserClosed = closed;
  report.passed = report.checks.every(check => check.passed);
  await writeFile(join(output, "review.json"), JSON.stringify(report, null, 2) + "\n");
  const masterDirectory = "docs/design/23-diagnostics-and-logs/visual-rebuild-20260910";
  const summary = [`# 诊断中心浏览器视觉验收`, "", `- 模式：${mode}；主题：${theme}；视口：${viewport.width}×${viewport.height}`, `- 结果：${report.checks.filter(check => check.passed).length}/${report.checks.length} 检查通过`, `- 浏览器与上下文已关闭：${closed}`, `- 参考主稿：${masterDirectory}`, "- 截图仅是人工对照证据；通过 DOM/行为检查不代表像素一致。", "- Fixture 模式只返回合成账号与业务文本；live 模式只读且不把接口正文保存到报告。", "", "## 检查结果", "", ...report.checks.map(item => `- ${item.passed ? "通过" : "失败"}：${item.name}${item.passed ? "" : ` — ${item.message.replaceAll("\n", " ")}`}`), "", "## 截图", "", ...report.screenshots.map(name => `- [${name}](${name})`), "", "布局位置、字体、颜色、边框和相对于图片主稿的区域差值见 [review.json](review.json)。", ""];
  await writeFile(join(output, "README.md"), summary.join("\n"));
}
console.log(JSON.stringify({ passed: report.passed, checks: report.checks.length, passedChecks: report.checks.filter(item => item.passed).length, output, screenshots: report.screenshots, browserClosed: report.browserClosed }, null, 2));
process.exitCode = report.passed ? 0 : 1;
