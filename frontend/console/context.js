const api = window.DeepAgentConsoleAPI;

let inheritedContext = { app: null, space: null, language: null };
let effectiveContext = { app: null, space: null, language: null };
let identity = null;

function valueOrNull(value) {
  const normalized = String(value ?? "").trim();
  return normalized || null;
}

function mergedContext(overrides = api.loadContext()) {
  return {
    app: overrides.app || inheritedContext.app || null,
    space: overrides.space || inheritedContext.space || null,
    language: overrides.language || inheritedContext.language || null,
  };
}

function renderContext() {
  const overrides = api.loadContext();
  effectiveContext = mergedContext(overrides);
  window.DeepAgentConsoleContext.effectiveContext = effectiveContext;

  const appInput = document.getElementById("contextAppInput");
  const spaceInput = document.getElementById("contextSpaceInput");
  const languageInput = document.getElementById("contextLanguageInput");
  if (appInput) {
    appInput.value = overrides.app || "";
    appInput.placeholder = inheritedContext.app || "继承平台";
  }
  if (spaceInput) {
    spaceInput.value = overrides.space || "";
    spaceInput.placeholder = inheritedContext.space || "继承平台";
  }
  if (languageInput) languageInput.value = overrides.language || "";

  const currentUser = identity?.user || "未认证";
  document.getElementById("contextUserInput").value = currentUser;
  document.getElementById("contextAuthInput").value = identity ? "已通过平台身份校验" : "未认证";
  document.getElementById("currentUserFull").textContent = currentUser;
  const currentUserButton = document.getElementById("currentUserBtn");
  if (currentUserButton) {
    currentUserButton.removeAttribute("title");
    currentUserButton.setAttribute("aria-label", `当前平台用户：${currentUser}`);
  }
  const rows = [
    ["App", effectiveContext.app || "未提供"],
    ["Space", effectiveContext.space || "未提供"],
    ["Language", effectiveContext.language || "未提供"],
    ["User", identity?.user || "未认证"],
  ];
  document.getElementById("effectiveContext").innerHTML = rows
    .map(([label, value]) => `<dt>${label}</dt><dd>${String(value).replace(/</g, "&lt;")}</dd>`)
    .join("");

  const baseUrl = document.getElementById("baseUrlInput");
  if (baseUrl && document.activeElement !== baseUrl) baseUrl.value = api.defaultApiBaseUrl();
}

function setAuthLocked(locked) {
  document.getElementById("authLock")?.classList.toggle("hidden", !locked);
  document.body.classList.toggle("console-auth-locked", locked);
}

export async function refreshIdentity() {
  let recoveredBaseUrl = false;
  try {
    let inherited;
    try {
      inherited = await api.requestContext({ inherited: true });
    } catch (error) {
      if (error.status || !api.canFallbackToInheritedBaseUrl()) throw error;
      api.clearBaseUrl();
      inherited = await api.requestContext({ inherited: true });
      recoveredBaseUrl = true;
    }
    identity = inherited;
    inheritedContext = {
      app: inherited.app || null,
      space: inherited.space || null,
      language: inherited.language || null,
    };
    setAuthLocked(false);
    renderContext();
    if (recoveredBaseUrl) {
      window.DeepAgentConsole?.toast("原后端地址不可用，已恢复为当前页面地址");
    }
    return true;
  } catch (error) {
    identity = null;
    setAuthLocked(error.status === 401);
    renderContext();
    if (error.status !== 401) throw error;
    return false;
  }
}

function openDrawer() {
  renderContext();
  document.getElementById("contextDrawer")?.classList.add("open");
  document.getElementById("contextDrawer")?.setAttribute("aria-hidden", "false");
  document.getElementById("contextBackdrop")?.classList.remove("hidden");
}

function closeDrawer() {
  document.getElementById("contextDrawer")?.classList.remove("open");
  document.getElementById("contextDrawer")?.setAttribute("aria-hidden", "true");
  document.getElementById("contextBackdrop")?.classList.add("hidden");
}

function closeUserPopover() {
  const button = document.getElementById("currentUserBtn");
  button?.classList.remove("popover-open");
  button?.setAttribute("aria-expanded", "false");
}

function toggleUserPopover() {
  const button = document.getElementById("currentUserBtn");
  if (!button) return;
  const open = !button.classList.contains("popover-open");
  button.classList.toggle("popover-open", open);
  button.setAttribute("aria-expanded", String(open));
}

function currentFormContext() {
  return api.sanitizeContext({
    app: valueOrNull(document.getElementById("contextAppInput")?.value),
    space: valueOrNull(document.getElementById("contextSpaceInput")?.value),
    language: valueOrNull(document.getElementById("contextLanguageInput")?.value),
  });
}

function saveContext() {
  const before = api.loadContext();
  const beforeBase = api.defaultApiBaseUrl();
  const next = api.saveContext(currentFormContext());
  const nextBase = api.saveBaseUrl(document.getElementById("baseUrlInput")?.value);
  closeDrawer();

  const hardChange = before.app !== next.app || before.space !== next.space || beforeBase !== nextBase;
  if (hardChange) {
    window.DeepAgentConsoleChat?.stopStream();
    location.reload();
    return;
  }
  renderContext();
  window.DeepAgentConsole?.toast("请求上下文已应用，后续请求将使用新语言");
}

function resetContext() {
  const hadScope = Object.keys(api.loadContext()).some((key) => key === "app" || key === "space");
  api.clearContext();
  closeDrawer();
  if (hadScope) {
    window.DeepAgentConsoleChat?.stopStream();
    location.reload();
    return;
  }
  renderContext();
  window.DeepAgentConsole?.toast("已恢复平台继承上下文");
}

export function bindContextControls() {
  document.getElementById("currentUserBtn")?.addEventListener("click", (event) => {
    event.stopPropagation();
    toggleUserPopover();
  });
  document.addEventListener("click", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    if (target?.closest("[data-open-context]")) {
      closeUserPopover();
      openDrawer();
      return;
    }
    if (!target?.closest("#currentUserBtn")) closeUserPopover();
  });
  document.getElementById("closeContextBtn")?.addEventListener("click", closeDrawer);
  document.getElementById("contextBackdrop")?.addEventListener("click", closeDrawer);
  document.getElementById("saveContextBtn")?.addEventListener("click", saveContext);
  document.getElementById("resetContextBtn")?.addEventListener("click", resetContext);
  document.getElementById("retryIdentityBtn")?.addEventListener("click", () => location.reload());
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeDrawer();
      closeUserPopover();
    }
  });
  renderContext();
}

window.DeepAgentConsoleContext = {
  effectiveContext,
  get identity() {
    return identity;
  },
  open: openDrawer,
  refreshIdentity,
};
