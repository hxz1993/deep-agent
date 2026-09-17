import { initUserIdentityControls } from "./user-identity.js?v=20260917-release01010";
import { initFieldHelp } from "./ui-field-help.js?v=20260913-49";
import { initConsoleSelects } from "./ui-select.js?v=20260914-wecom-placeholder";
import {
  initConfiguration,
  restoreConfigurationTab,
  rememberConfigurationTab,
} from "./configuration.js?v=20260918-release01012";
import { bindContextControls, refreshIdentity } from "./context.js?v=20260917-release01010";
import { initDiagnostics, setDiagnosticsActive, openDiagnostics } from "./diagnostics-center.js?v=20260918-release01012";
import { initWorkspace } from "./workspace.js?v=20260918-release01012";
import "./time.js?v=20260913-49";

initUserIdentityControls();

const VALID_PAGES = new Set(["workspace", "configuration", "diagnostics"]);
const PAGE_LABELS = {
  workspace: "测试",
  configuration: "配置中心",
  diagnostics: "诊断中心",
};
let consoleAllowed = false;
let toastTimer = null;

async function loadProductVersion() {
  const target = document.getElementById("productVersion");
  if (!target) return;
  try {
    const response = await daFetch(serviceUrl("info"), { includeContext: false });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const info = await response.json();
    const version = String(info?.version || "").trim().replace(/^v/i, "");
    if (!version) throw new Error("响应中缺少版本");
    target.textContent = `v${version}`;
    target.title = `DeepAgent 产品版本 ${version}`;
  } catch (error) {
    target.textContent = "v—";
    target.title = `版本信息加载失败：${error instanceof Error ? error.message : String(error)}`;
  }
}

function toast(message, isError = false) {
  const element = document.getElementById("consoleToast");
  if (!element) return;
  element.textContent = message;
  element.classList.toggle("error", isError);
  element.classList.remove("hidden");
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => element.classList.add("hidden"), 3600);
}

function pageFromLocation() {
  const page = location.hash.replace(/^#\/?/, "").split(/[/?]/)[0];
  return VALID_PAGES.has(page) ? page : "workspace";
}

function syncNavigation() {
  const page = document.querySelector("[data-page-panel].active")?.dataset.pagePanel || "workspace";
  document.body.dataset.consolePage = page;
  document.querySelectorAll(".console-rail [data-page], .console-rail [data-tab]").forEach((button) => {
    const selected = button.dataset.diagnosticsNav ? page === "diagnostics" && location.hash.startsWith(`#/diagnostics/${button.dataset.diagnosticsNav}`) : button.dataset.page ? button.dataset.page === page : page === "configuration" && button.classList.contains("active");
    if (button.dataset.diagnosticsNav) button.classList.toggle("active", selected);
    if (selected) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  const sectionName = document.getElementById("consoleSectionName");
  if (sectionName) sectionName.textContent = page === "configuration"
    ? document.querySelector(".resource-btn.active span")?.textContent || PAGE_LABELS[page]
    : PAGE_LABELS[page];
}

async function showPage(page, { updateHash = true, restoreTab = true, diagnosticTarget = null } = {}) {
  const next = VALID_PAGES.has(page) ? page : "workspace";
  const current = document.querySelector("[data-page-panel].active")?.dataset.pagePanel;
  if (current === "configuration" && next !== "configuration" && window.agentsAdmin?.isEditorOpen?.()) {
    if (!(await window.agentsAdmin.requestCloseEditor())) return;
  }
  if (current === "configuration" && next !== "configuration" && window.mcpAdmin?.isEditorOpen?.()) {
    if (!(await window.mcpAdmin.requestCloseEditor())) return;
  }
  if (next !== "configuration" && window.integrationsAdmin?.deactivate() === false) return;
  if (next !== "configuration" && window.wecomApplicationsAdmin?.deactivate() === false) return;
  document.querySelectorAll("[data-page-panel]").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.pagePanel === next);
  });
  if (next !== "configuration") window.llmProvidersAdmin?.cancelModelTests();
  document.querySelectorAll("[data-page]").forEach((button) => {
    if (!button.dataset.diagnosticsNav) button.classList.toggle("active", button.dataset.page === next);
  });
  syncNavigation();
  if (updateHash) {
    const target = next === "diagnostics" ? diagnosticTarget || (location.hash.startsWith("#/diagnostics/") ? location.hash : "#/diagnostics/logs") : `#/${next}`;
    if (location.hash !== target) {
      if (next === "diagnostics" || current === "diagnostics") history.pushState(null, "", target);
      else history.replaceState(null, "", target);
    }
  }
  setDiagnosticsActive(consoleAllowed && next === "diagnostics");
  if (consoleAllowed && next === "configuration") {
    await initConfiguration().catch((error) => toast(error.message, true));
    if (restoreTab) restoreConfigurationTab();
    if (restoreTab && current !== "configuration" && document.querySelector('[data-tab="integrations"]')?.classList.contains("active")) {
      void window.integrationsAdmin?.refresh();
    }
  }
  if (consoleAllowed && next === "workspace" && current !== "workspace") {
    void window.DeepAgentConsoleWorkspace?.refreshChannelAccounts();
  }
  window.lucide?.createIcons();
}

let expandedNavigationWidth = 200;

function setConsoleNavigationWidth(width) {
  // Keep the rail under the pointer; only the labels fade near the compact end.
  const nextWidth = Math.max(56, Math.min(200, width));
  const collapsed = nextWidth <= 96;
  if (nextWidth >= 140) expandedNavigationWidth = nextWidth;
  document.body.classList.toggle("console-nav-collapsed", collapsed);
  document.body.style.setProperty("--console-rail-width", `${nextWidth}px`);
  document.body.style.setProperty("--console-nav-label-opacity", String(Math.max(0, Math.min(1, (nextWidth - 96) / 44))));
  document.getElementById("consoleNavResizer")?.setAttribute("aria-valuenow", String(nextWidth));
  const button = document.getElementById("toggleConsoleNavBtn");
  const label = collapsed ? "展开导航" : "收起导航";
  button.setAttribute("aria-expanded", String(!collapsed));
  button.setAttribute("aria-label", label);
  button.title = label;
  const brand = document.getElementById("expandConsoleNavBtn");
  brand.title = label;
  brand.setAttribute("aria-label", brand.title);
  window.dispatchEvent(new Event("resize"));
}

function toggleConsoleNavigation() {
  setConsoleNavigationWidth(document.body.classList.contains("console-nav-collapsed") ? expandedNavigationWidth : 56);
}

function bindConsoleNavigationResizer() {
  const handle = document.getElementById("consoleNavResizer");
  const snapWidth = 100;
  let drag = null;
  let snapTimer = null;
  const animateSnap = () => {
    clearTimeout(snapTimer);
    document.body.classList.add("console-nav-snapping");
    snapTimer = setTimeout(() => document.body.classList.remove("console-nav-snapping"), 180);
  };
  handle.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    drag = { x: event.clientX, width: document.querySelector(".console-rail").getBoundingClientRect().width, snapped: false };
    handle.setPointerCapture(event.pointerId);
    handle.classList.add("dragging");
    document.body.classList.add("workspace-pane-resizing");
    event.preventDefault();
  });
  handle.addEventListener("pointermove", (event) => {
    if (!drag) return;
    const width = drag.width + event.clientX - drag.x;
    // Snap only while shrinking; pulling out from the icon rail stays continuous.
    const snapped = width <= snapWidth && width < drag.width;
    if (snapped !== drag.snapped) animateSnap();
    drag.snapped = snapped;
    setConsoleNavigationWidth(snapped ? 56 : width);
  });
  const finish = () => {
    if (!drag) return;
    drag = null;
    clearTimeout(snapTimer);
    document.body.classList.remove("console-nav-snapping");
    handle.classList.remove("dragging");
    document.body.classList.remove("workspace-pane-resizing");
  };
  handle.addEventListener("pointerup", finish);
  handle.addEventListener("pointercancel", finish);
  handle.addEventListener("lostpointercapture", finish);
  handle.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const width = document.querySelector(".console-rail").getBoundingClientRect().width;
    const nextWidth = event.key === "Home" ? 56 : event.key === "End" ? 200 : width + (event.key === "ArrowRight" ? 20 : -20);
    setConsoleNavigationWidth(event.key === "ArrowLeft" && nextWidth <= snapWidth ? 56 : nextWidth);
  });
}

function bindNavigation() {
  bindConsoleNavigationResizer();
  const resourceNav = document.getElementById("consoleResourceNav");
  const template = document.getElementById("configurationNavigation");
  if (resourceNav && template) resourceNav.append(template.content);
  const agentTab = resourceNav?.querySelector('[data-tab="agents"]');
  if (agentTab) document.querySelector(".console-rail .console-nav")?.append(agentTab);
  const integrationTab = resourceNav?.querySelector('[data-tab="integrations"]');
  if (integrationTab) document.querySelector(".console-rail .console-nav")?.append(integrationTab);
  const channelTab = resourceNav?.querySelector('[data-tab="channels"]');
  if (channelTab) document.querySelector(".console-rail .console-nav")?.append(channelTab);
  document.querySelectorAll(".console-rail [data-tab]").forEach((button) => {
    const label = button.querySelector("span")?.textContent.trim();
    if (label) { button.title = label; button.setAttribute("aria-label", label); }
  });
  document.getElementById("toggleConsoleNavBtn")?.addEventListener("click", toggleConsoleNavigation);
  document.getElementById("expandConsoleNavBtn")?.addEventListener("click", toggleConsoleNavigation);
  // Capture cross-page clicks before the lazy-loaded configuration tab handlers.
  document.querySelector(".console-rail")?.addEventListener("click", async (event) => {
    const tab = event.target.closest("[data-tab]");
    if (!tab) return;
    if (!consoleAllowed) { event.stopImmediatePropagation(); return; }
    if (document.body.dataset.consolePage === "configuration") return;
    event.stopImmediatePropagation();
    await showPage("configuration", { restoreTab: false });
    tab.click();
  }, true);
  document.addEventListener("deep-agent-configuration-tab", (event) => {
    rememberConfigurationTab(event.detail.name);
    syncNavigation();
  });
  document.querySelectorAll("[data-page]").forEach((button) => {
    button.addEventListener("click", () => button.dataset.diagnosticsNav ? openDiagnostics(button.dataset.diagnosticsNav) : showPage(button.dataset.page));
  });
  document.querySelectorAll("[data-page-link]").forEach((button) => {
    button.addEventListener("click", () => showPage(button.dataset.pageLink));
  });
  window.addEventListener("hashchange", () => showPage(pageFromLocation(), { updateHash: false }));
  window.addEventListener("popstate", () => showPage(pageFromLocation(), { updateHash: false }));
}

function bindThemes() {
  const trigger = document.getElementById("consoleThemeBtn");
  const control = trigger.closest(".console-theme-control");
  const settings = control.closest(".console-rail-settings");
  new ResizeObserver(() => {
    document.body.classList.toggle("console-theme-compact", settings.clientWidth < 124);
  }).observe(settings);
  const close = () => { control.classList.remove("open"); trigger.setAttribute("aria-expanded", "false"); };
  trigger.addEventListener("click", () => {
    const open = control.classList.toggle("open");
    trigger.setAttribute("aria-expanded", String(open));
    if (open) control.querySelector("[data-console-theme].active")?.focus();
  });
  document.addEventListener("click", (event) => { if (!control.contains(event.target)) close(); });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && control.classList.contains("open")) { close(); trigger.focus(); }
  });
  const apply = (theme) => {
    window.DATheme?.apply(theme);
    const highlightTheme = document.getElementById("hljsTheme");
    if (highlightTheme) {
      const root = window.__DEEP_AGENT_APP_ROOT__ || "/";
      const file = ["light", "paper", "contrast"].includes(theme)
        ? "highlight-github.min.css"
        : "highlight-github-dark.min.css";
      highlightTheme.href = `${root.replace(/\/$/, "")}/vendor/${file}`;
    }
    document.querySelectorAll("[data-console-theme]").forEach((button) => {
      button.classList.toggle("active", button.dataset.consoleTheme === theme);
      button.setAttribute("aria-pressed", String(button.dataset.consoleTheme === theme));
    });
    const icon = { light: "sun", paper: "leaf", dark: "moon", contrast: "contrast" }[theme] || "sun";
    trigger.innerHTML = `<i data-lucide="${icon}" aria-hidden="true"></i>`;
    window.lucide?.createIcons();
  };
  document.querySelectorAll("[data-console-theme]").forEach((button) => {
    button.addEventListener("click", () => {
      apply(button.dataset.consoleTheme);
      close();
      if (trigger.offsetParent !== null) trigger.focus();
    });
  });
  apply(localStorage.getItem("da_theme") || "paper");
}

function showApiBanner(status, reason) {
  const banner = document.getElementById("consoleBanner");
  if (!banner) return;
  const messages = {
    401: "未获取到平台登录身份。请登录 DeepFOS 后重新打开控制台。",
    403: "当前用户没有执行该操作的权限。",
    503: "数据库或依赖服务尚未配置，请检查后端环境。",
  };
  banner.textContent = reason === "read_only_preview" ? "当前为本地 Test 只读预览，不支持删除、修改或执行会话。" : messages[status] || `请求失败（HTTP ${status}）`;
  banner.className = `console-banner status-${status}`;
}

function showWorkspaceLoadError(error) {
  const loading = document.getElementById("workspaceLoading");
  if (!loading) return;
  loading.replaceChildren();
  const message = document.createElement("span");
  message.className = "configuration-error";
  message.textContent = error?.message || "测试工作台加载失败";
  const retry = document.createElement("button");
  retry.type = "button";
  retry.textContent = "重新加载";
  retry.addEventListener("click", () => location.reload());
  loading.append(message, retry);
}

function showAccessLock({ title, message, icon = "shield-alert" }) {
  const lock = document.getElementById("authLock");
  if (!lock) return;
  const iconNode = lock.querySelector("[data-access-lock-icon]");
  if (iconNode) iconNode.setAttribute("data-lucide", icon);
  const titleNode = document.getElementById("authLockTitle");
  const messageNode = document.getElementById("authLockMessage");
  if (titleNode) titleNode.textContent = title;
  if (messageNode) messageNode.textContent = message;
  lock.classList.remove("hidden");
  document.body.classList.add("console-auth-locked");
  window.lucide?.createIcons();
}

function hideAccessLock() {
  document.getElementById("authLock")?.classList.add("hidden");
  document.body.classList.remove("console-auth-locked");
}

async function boot() {
  window.DeepAgentConsole = { showPage, toast };
  bindNavigation();
  bindThemes();
  bindContextControls();
  loadProductVersion();
  window.addEventListener("deep-agent-console-api-status", (event) => showApiBanner(event.detail?.status, event.detail?.reason));
  window.addEventListener("deep-agent-diagnostics-denied", event => {
    consoleAllowed = false;
    showAccessLock({ title: event.detail?.status === 401 ? "平台登录状态已失效" : "无诊断查询权限", message: "请重新验证平台管理员身份后再打开诊断中心。", icon: "shield-x" });
  });
  window.lucide?.createIcons();

  try {
    const authenticated = await refreshIdentity();
    if (!authenticated) {
      showAccessLock({
        title: "未获取到平台登录身份",
        message: "请先登录 DeepFOS，再重新打开一体化控制台。",
      });
      await showPage(pageFromLocation(), { updateHash: true });
      return;
    }

    const access = await window.DeepAgentConsoleAPI.adminAccess();
    if (!access?.allowed || !access?.roles?.includes("enterprise_admin")) {
      showAccessLock({
        title: "无控制台管理权限",
        message: "当前账号没有DeepAgent控制台管理权限。",
        icon: "shield-x",
      });
      await showPage(pageFromLocation(), { updateHash: true });
      return;
    }
    consoleAllowed = true;
    hideAccessLock();
  } catch (error) {
    showApiBanner(error.status || 500);
    if (error.status === 401) {
      showAccessLock({
        title: "平台登录状态已失效",
        message: "请重新登录 DeepFOS 后再打开控制台。",
      });
    } else if (error.status === 503) {
      showAccessLock({
        title: "暂时无法验证管理权限",
        message: "平台权限服务暂时不可用，请稍后重新检查。",
        icon: "shield-question",
      });
    } else {
      showAccessLock({
        title: "控制台初始化失败",
        message: error.message || "无法完成控制台权限检查。",
      });
    }
    await showPage(pageFromLocation(), { updateHash: true });
    return;
  }

  initDiagnostics();
  await initWorkspace().catch((error) => {
    showWorkspaceLoadError(error);
    toast(error.message, true);
  });
  await showPage(pageFromLocation(), { updateHash: true });
  const prewarmConfiguration = () => initConfiguration().catch(() => {});
  if ("requestIdleCallback" in window) {
    window.requestIdleCallback(prewarmConfiguration, { timeout: 1800 });
  } else {
    window.setTimeout(prewarmConfiguration, 450);
  }
}

window.__ProductVersionUI = { loadProductVersion };

initConsoleSelects();
initFieldHelp();
boot();
