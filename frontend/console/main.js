import {
  initConfiguration,
  restoreConfigurationTab,
} from "./configuration.js?v=20260831-13";
import { bindContextControls, refreshIdentity } from "./context.js?v=20260822-1";
import { initDiagnostics } from "./diagnostics.js?v=20260720-1";
import { initWorkspace } from "./workspace.js?v=20260831-13";
import "./time.js?v=20260831-1";

const VALID_PAGES = new Set(["workspace", "configuration", "diagnostics"]);
const PAGE_LABELS = {
  workspace: "对话测试",
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
  const page = location.hash.replace(/^#\/?/, "");
  return VALID_PAGES.has(page) ? page : "workspace";
}

async function showPage(page, { updateHash = true } = {}) {
  const next = VALID_PAGES.has(page) ? page : "workspace";
  const current = document.querySelector("[data-page-panel].active")?.dataset.pagePanel;
  if (current === "configuration" && next !== "configuration" && window.agentsAdmin?.isEditorOpen?.()) {
    if (!(await window.agentsAdmin.requestCloseEditor())) return;
  }
  if (current === "configuration" && next !== "configuration" && window.mcpAdmin?.isEditorOpen?.()) {
    if (!(await window.mcpAdmin.requestCloseEditor())) return;
  }
  document.querySelectorAll("[data-page-panel]").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.pagePanel === next);
  });
  document.querySelectorAll("[data-page]").forEach((button) => {
    button.classList.toggle("active", button.dataset.page === next);
  });
  const sectionName = document.getElementById("consoleSectionName");
  if (sectionName) sectionName.textContent = PAGE_LABELS[next];
  if (updateHash && location.hash !== `#/${next}`) history.replaceState(null, "", `#/${next}`);
  if (consoleAllowed && next === "configuration") {
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
  window.addEventListener("deep-agent-console-api-status", (event) => showApiBanner(event.detail?.status));
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

boot();
