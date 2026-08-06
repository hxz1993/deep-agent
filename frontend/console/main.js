import {
  initConfiguration,
  restoreConfigurationTab,
} from "./configuration.js?v=20260731-1";
import { bindContextControls, refreshIdentity } from "./context.js?v=20260728-5";
import { initDiagnostics } from "./diagnostics.js?v=20260720-1";
import { initWorkspace } from "./workspace.js?v=20260806-1";

const VALID_PAGES = new Set(["workspace", "configuration", "diagnostics"]);
let authenticated = false;
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
  const page = location.hash.replace(/^#\/?/, "");
  return VALID_PAGES.has(page) ? page : "workspace";
}

async function showPage(page, { updateHash = true } = {}) {
  const next = VALID_PAGES.has(page) ? page : "workspace";
  document.querySelectorAll("[data-page-panel]").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.pagePanel === next);
  });
  document.querySelectorAll("[data-page]").forEach((button) => {
    button.classList.toggle("active", button.dataset.page === next);
  });
  if (updateHash && location.hash !== `#/${next}`) history.replaceState(null, "", `#/${next}`);
  if (authenticated && next === "configuration") {
    await initConfiguration().catch((error) => toast(error.message, true));
    restoreConfigurationTab();
  }
  window.lucide?.createIcons();
}

function bindNavigation() {
  document.querySelectorAll("[data-page]").forEach((button) => {
    button.addEventListener("click", () => showPage(button.dataset.page));
  });
  document.querySelectorAll("[data-page-link]").forEach((button) => {
    button.addEventListener("click", () => showPage(button.dataset.pageLink));
  });
  window.addEventListener("hashchange", () => showPage(pageFromLocation(), { updateHash: false }));
}

function bindThemes() {
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
    });
  };
  document.querySelectorAll("[data-console-theme]").forEach((button) => {
    button.addEventListener("click", () => apply(button.dataset.consoleTheme));
  });
  apply(localStorage.getItem("da_theme") || "paper");
}

function showApiBanner(status) {
  const banner = document.getElementById("consoleBanner");
  if (!banner) return;
  const messages = {
    401: "未获取到平台登录身份。请登录 DeepFOS 后重新打开控制台。",
    403: "当前用户没有执行该操作的权限。",
    503: "数据库或依赖服务尚未配置，请检查后端环境。",
  };
  banner.textContent = messages[status] || `请求失败（HTTP ${status}）`;
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

async function boot() {
  window.DeepAgentConsole = { showPage, toast };
  bindNavigation();
  bindThemes();
  bindContextControls();
  initDiagnostics();
  loadProductVersion();
  window.addEventListener("deep-agent-console-api-status", (event) => showApiBanner(event.detail?.status));
  window.lucide?.createIcons();

  try {
    authenticated = await refreshIdentity();
  } catch (error) {
    showApiBanner(error.status || 500);
    showWorkspaceLoadError(error);
    toast(error.message, true);
  }

  if (authenticated) {
    await initWorkspace().catch((error) => {
      showWorkspaceLoadError(error);
      toast(error.message, true);
    });
  }
  await showPage(pageFromLocation(), { updateHash: true });
}

window.__ProductVersionUI = { loadProductVersion };

boot();
