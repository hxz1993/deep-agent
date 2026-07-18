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

  document.getElementById("contextUserInput").value = identity?.user || "未认证";
  document.getElementById("contextAuthInput").value = identity ? "已通过平台身份校验" : "未认证";
  document.getElementById("currentUserLabel").textContent = identity?.user || "未认证";
  document.getElementById("contextSummary").textContent =
    effectiveContext.app || effectiveContext.space
      ? `${effectiveContext.app || "—"} / ${effectiveContext.space || "—"}`
      : "继承平台上下文";

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
  try {
    const inherited = await api.requestContext({ inherited: true });
    identity = inherited;
    inheritedContext = {
      app: inherited.app || null,
      space: inherited.space || null,
      language: inherited.language || null,
    };
    setAuthLocked(false);
    renderContext();
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
  document.getElementById("openContextBtn")?.addEventListener("click", openDrawer);
  document.getElementById("closeContextBtn")?.addEventListener("click", closeDrawer);
  document.getElementById("contextBackdrop")?.addEventListener("click", closeDrawer);
  document.getElementById("saveContextBtn")?.addEventListener("click", saveContext);
  document.getElementById("resetContextBtn")?.addEventListener("click", resetContext);
  document.getElementById("retryIdentityBtn")?.addEventListener("click", () => location.reload());
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeDrawer();
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
