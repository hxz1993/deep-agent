const api = window.DeepAgentConsoleAPI;

let inheritedContext = { app: null, space: null, language: null };
let effectiveContext = { app: null, space: null, language: null };
let identity = null;

function valueOrNull(value) {
  const normalized = String(value ?? "").trim();
  return normalized || null;
}

function mergedContext(overrides = api.loadContext()) {
  const app = overrides.app || inheritedContext.app || null;
  let space = inheritedContext.space || null;
  if (overrides.app) {
    space = api.deriveSpaceFromApp(overrides.app);
  } else if (app && !space) {
    try {
      space = api.deriveSpaceFromApp(app);
    } catch {
      space = null;
    }
  }
  return {
    app,
    space,
    language: api.DEFAULT_LANGUAGE,
  };
}

function renderEffectiveContextValues(context) {
  const rows = [
    ["App", context.app || "未提供"],
    ["Space", context.space || "未提供"],
    ["Language", context.language || "未提供"],
    ["User ID", identity?.user || "未认证"],
  ];
  const host = document.getElementById("effectiveContext");
  if (!host) return;
  host.innerHTML = rows
    .map(([label, value]) => `<dt>${label}</dt><dd>${String(value).replace(/</g, "&lt;")}</dd>`)
    .join("");
}

function previewContextFromInput() {
  const app = valueOrNull(document.getElementById("contextAppInput")?.value);
  let space = null;
  if (app) {
    try {
      space = api.deriveSpaceFromApp(app);
    } catch {
      space = "格式无效";
    }
  }
  renderEffectiveContextValues({ app, space, language: api.DEFAULT_LANGUAGE });
}

function renderContext() {
  const overrides = api.loadContext();
  effectiveContext = mergedContext(overrides);
  window.DeepAgentConsoleContext.effectiveContext = effectiveContext;

  document.querySelectorAll("[data-open-context]").forEach((button) => {
    const appId = effectiveContext.app || "不分应用";
    const label = button.querySelector("[data-test-app-id]");
    if (label) label.textContent = appId;
    button.title = `调试：${appId}\nSpace：${effectiveContext.space || "未提供"}\nLanguage：${effectiveContext.language || "未提供"}\nUser ID：${identity?.user || "未认证"}`;
    button.setAttribute("aria-label", `调试：${appId}，查看或修改调试上下文`);
  });

  const appInput = document.getElementById("contextAppInput");
  if (appInput) {
    appInput.value = overrides.app || "";
    appInput.placeholder = inheritedContext.app || "继承平台";
  }

  const currentUser = identity?.username || identity?.user || "未认证";
  document.getElementById("currentUserFull").textContent = currentUser;
  const sidebarUser = document.getElementById("consoleUserName");
  if (sidebarUser) sidebarUser.textContent = currentUser;
  const currentUserButton = document.getElementById("currentUserBtn");
  if (currentUserButton) {
    currentUserButton.removeAttribute("title");
    currentUserButton.setAttribute("aria-label", `当前平台用户：${currentUser}`);
  }
  renderEffectiveContextValues(effectiveContext);
}

function setAuthLocked(locked) {
  document.getElementById("authLock")?.classList.toggle("hidden", !locked);
  document.body.classList.toggle("console-auth-locked", locked);
}

export async function refreshIdentity() {
  try {
    const [inherited, profile] = await Promise.all([
      api.requestContext({ inherited: true }),
      api.currentUser().catch(() => null),
    ]);
    const username = profile?.user_id === inherited.user ? valueOrNull(profile.username) : null;
    identity = { ...inherited, username };
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

function positionContextPopover(trigger) {
  const popover = document.getElementById("contextPopover");
  if (!popover) return;
  const anchor = trigger || document.querySelector("[data-open-context]");
  const rect = anchor?.getBoundingClientRect();
  if (!rect) return;
  const width = Math.min(336, window.innerWidth - 24);
  const left = Math.max(12, Math.min(rect.right - width, window.innerWidth - width - 12));
  popover.style.left = `${left}px`;
  popover.style.top = `${Math.min(rect.bottom + 8, window.innerHeight - 240)}px`;
}

function openContextPopover(trigger) {
  renderContext();
  const popover = document.getElementById("contextPopover");
  if (!popover) return;
  positionContextPopover(trigger);
  popover.inert = false;
  popover.classList.remove("hidden");
  popover.setAttribute("aria-hidden", "false");
  document.getElementById("contextAppInput")?.focus();
}

function closeContextPopover() {
  const popover = document.getElementById("contextPopover");
  if (popover?.contains(document.activeElement)) document.activeElement.blur();
  if (popover) popover.inert = true;
  popover?.classList.add("hidden");
  popover?.setAttribute("aria-hidden", "true");
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
  });
}

function saveContext() {
  if (window.DeepAgentConsoleChat?.state?.streaming) {
    window.DeepAgentConsole?.toast("请先停止当前生成，再修改调试上下文", true);
    return;
  }
  const before = api.loadContext();
  let next;
  try {
    next = api.saveContext(currentFormContext());
  } catch (error) {
    window.DeepAgentConsole?.toast(error.message, true);
    return;
  }
  closeContextPopover();

  if (before.app !== next.app) {
    location.reload();
    return;
  }
  renderContext();
  window.DeepAgentConsole?.toast("调试上下文已应用");
}

function resetContext() {
  if (window.DeepAgentConsoleChat?.state?.streaming) {
    window.DeepAgentConsole?.toast("请先停止当前生成，再重置调试上下文", true);
    return;
  }
  const hadScope = Boolean(api.loadContext().app);
  api.clearContext();
  closeContextPopover();
  if (hadScope) {
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
    const trigger = target?.closest("[data-open-context]");
    if (trigger) {
      closeUserPopover();
      const popover = document.getElementById("contextPopover");
      if (popover?.classList.contains("hidden")) openContextPopover(trigger);
      else closeContextPopover();
      return;
    }
    if (!target?.closest("#currentUserBtn")) closeUserPopover();
  });
  document.addEventListener("pointerdown", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    if (!target?.closest("#contextPopover") && !target?.closest("[data-open-context]")) {
      closeContextPopover();
    }
  });
  document.getElementById("closeContextBtn")?.addEventListener("click", closeContextPopover);
  document.getElementById("saveContextBtn")?.addEventListener("click", saveContext);
  document.getElementById("resetContextBtn")?.addEventListener("click", resetContext);
  document.getElementById("contextAppInput")?.addEventListener("input", previewContextFromInput);
  document.getElementById("retryIdentityBtn")?.addEventListener("click", () => location.reload());
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeContextPopover();
      closeUserPopover();
    }
  });
  window.addEventListener("resize", closeContextPopover);
  window.addEventListener("scroll", closeContextPopover, true);
  renderContext();
}

window.DeepAgentConsoleContext = {
  effectiveContext,
  get identity() {
    return identity;
  },
  open: openContextPopover,
  render: renderContext,
  refreshIdentity,
};
