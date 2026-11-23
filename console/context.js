import { renderUserIdentity } from "./user-identity.js?v=20260917-release01010";
const api = window.DeepAgentConsoleAPI;

let inheritedContext = { app: null, space: null, language: null };
let effectiveContext = { app: null, space: null, language: null };
let resolvedContext = null;
let identity = null;
let identityGeneration = 0;
let databaseVersionLoaded = false;
let databaseVersionPromise = null;
let inspectionMcp = null;

function loadUserDatabaseVersion() {
  if (databaseVersionLoaded || databaseVersionPromise) return databaseVersionPromise;
  const target = document.getElementById("databaseSchemaVersion");
  if (!target) return null;
  databaseVersionPromise = (async () => {
    try {
      const response = await window.daFetch(api.serviceUrl("/api/v1/database-info?include_charset=false"));
      if (!response.ok) throw new Error(`数据库版本读取失败：HTTP ${response.status}`);
      const info = await response.json();
      const revision = String(info?.schema_version ?? "").trim();
      target.textContent = revision ? `V${/^\d+$/.test(revision) ? Number.parseInt(revision, 10) : revision}` : "—";
      target.removeAttribute("title");
      databaseVersionLoaded = true;
    } catch (error) {
      target.textContent = "—";
      target.title = error.message || "数据库版本读取失败";
    } finally { databaseVersionPromise = null; }
  })();
  return databaseVersionPromise;
}

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
    app_name: app && resolvedContext?.app === app ? resolvedContext.app_name || null
      : app && inheritedContext.app === app ? inheritedContext.app_name || null : null,
    space_name: space && resolvedContext?.space === space ? resolvedContext.space_name || null
      : space && inheritedContext.space === space ? inheritedContext.space_name || null : null,
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
  const input = document.getElementById("contextAppInput");
  input?.setCustomValidity("");
  const app = valueOrNull(input?.value);
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
    const appLabel = effectiveContext.app_name || appId;
    const label = button.querySelector("[data-test-app-id]");
    if (label) {
      label.textContent = appLabel;
      label.classList.toggle("is-unassigned", !effectiveContext.app);
    }
    button.title = `调试：${appLabel}\nApp：${appId}\nSpace：${effectiveContext.space || "未提供"}\nLanguage：${effectiveContext.language || "未提供"}\nUser ID：${identity?.user || "未认证"}`;
    button.setAttribute("aria-label", `调试：${appLabel}，查看或修改调试上下文`);
  });

  document.querySelectorAll("[data-empty-context-prefix]").forEach((prefix) => {
    prefix.textContent = effectiveContext.app ? "在应用 " : "当前";
  });
  document.querySelectorAll("[data-empty-context-label]").forEach((label) => {
    label.textContent = effectiveContext.app_name && effectiveContext.app_name !== effectiveContext.app
      ? `${effectiveContext.app_name} · ${effectiveContext.app}` : effectiveContext.app || "不分应用";
  });
  document.querySelectorAll("[data-empty-context-note]").forEach((note) => {
    note.textContent = effectiveContext.app ? " 的上下文中开始新对话。" : "，无法调用 DeepFOS MCP 及相关平台工具。";
  });

  const appInput = document.getElementById("contextAppInput");
  const contextOpen = document.getElementById("contextPopover")?.open;
  if (appInput && !contextOpen) {
    appInput.value = overrides.app || "";
    appInput.placeholder = inheritedContext.app || "继承平台";
  }

  const currentUser = identity?.username || identity?.user || "未认证";
  const profileHost = document.getElementById("currentUserProfile");
  if (profileHost) {
    profileHost.innerHTML = renderUserIdentity(identity?.profile || {}, {userId:identity?.user || "", userIcon:true});
    window.lucide?.createIcons({root:profileHost});
  }
  const sidebarUser = document.getElementById("consoleUserName");
  if (sidebarUser) sidebarUser.textContent = currentUser;
  const currentUserButton = document.getElementById("currentUserBtn");
  if (currentUserButton) {
    currentUserButton.removeAttribute("title");
    currentUserButton.setAttribute("aria-label", `当前平台用户：${currentUser}`);
  }
  if (!contextOpen) renderEffectiveContextValues(effectiveContext);
  window.DeepAgentConsoleWorkspace?.renderInspector?.();
}

function setAuthLocked(locked) {
  document.getElementById("authLock")?.classList.toggle("hidden", !locked);
  document.body.classList.toggle("console-auth-locked", locked);
}

export async function refreshIdentity() {
  const generation = ++identityGeneration;
  const overrides = api.loadContext();
  // Profile enrichment is optional. A slow profile service must not hold up
  // the effective App/Space or the Console's authentication bootstrap.
  const profilePromise = api.currentUser().catch(() => null);
  try {
    const [inherited, resolved] = await Promise.all([
      api.requestContext({ inherited: true }),
      overrides.app ? api.requestContext().catch(() => null) : Promise.resolve(null),
    ]);
    if (generation !== identityGeneration) return false;
    const previous = identity?.user === inherited.user ? identity : null;
    identity = { ...inherited, username: previous?.username || null, profile: previous?.profile || {resolution:"unavailable"} };
    inheritedContext = {
      app: inherited.app || null,
      space: inherited.space || null,
      language: inherited.language || null,
      app_name: inherited.app_name || null,
      space_name: inherited.space_name || null,
    };
    resolvedContext = resolved?.user === inherited.user ? resolved : null;
    setAuthLocked(false);
    renderContext();
    void profilePromise.then(profile => {
      if (generation !== identityGeneration) return;
      identity = { ...inherited,
        username: profile?.user_id === inherited.user ? valueOrNull(profile.username) : null,
        profile: profile?.user_id === inherited.user ? profile : {resolution:"unavailable"},
      };
      renderContext();
    });
    return true;
  } catch (error) {
    if (generation !== identityGeneration) return false;
    identity = null;
    resolvedContext = null;
    inheritedContext = { app: null, space: null, language: null };
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

let appOptionsRequest;
let appOptions = null;

function toggleContextAppChoices(open) {
  const choices = document.getElementById("contextAppChoices");
  if (!choices) return;
  choices.hidden = !open;
  for (const id of ["contextAppInput", "contextAppOptionsBtn"]) {
    document.getElementById(id)?.setAttribute("aria-expanded", String(open));
  }
  if (!open) {
    appOptionsRequest?.abort();
    appOptionsRequest = null;
    return;
  }
  if (appOptions !== null) renderContextAppChoices();
  if (appOptionsRequest) return;
  const controller = appOptionsRequest = new AbortController();
  if (appOptions === null) choices.textContent = "正在加载…";
  void daFetch(serviceUrl("/api/v1/integrations/deeptable-assistant/app-configs"), {
    signal: controller.signal, reportStatus: false,
  }).then(async response => {
    if (!response.ok) throw new Error("应用列表读取失败");
    const data = await response.json();
    if (appOptionsRequest !== controller) return;
    if (!Array.isArray(data)) throw new Error("应用列表返回格式错误");
    appOptions = data;
    renderContextAppChoices();
  }).catch(() => {
    if (appOptionsRequest === controller && appOptions === null) {
      choices.textContent = "暂无法读取应用列表，可直接输入应用 ID。";
    }
  }).finally(() => { if (appOptionsRequest === controller) appOptionsRequest = null; });
}

function renderContextAppChoices(filter = false) {
  const choices = document.getElementById("contextAppChoices");
  const input = document.getElementById("contextAppInput");
  if (!choices || !input || choices.hidden) return;
  const term = input.value.trim().toLowerCase();
  const rows = (appOptions || []).filter(app => !filter || `${app.app_name} ${app.app_id}`.toLowerCase().includes(term));
  const heading = document.createElement("div");
  heading.className = "context-app-choices-heading";
  heading.textContent = "已配置应用专用内容";
  choices.replaceChildren(heading);
  if (!rows.length) {
    const empty = document.createElement("div");
    empty.className = "context-app-choices-heading";
    empty.textContent = "无可选应用，可直接输入应用 ID";
    choices.append(empty);
  }
  for (const app of rows) {
    const button = document.createElement("button");
    button.type = "button";
    button.setAttribute("role", "option");
    button.setAttribute("aria-selected", String(input.value === app.app_id));
    const title = document.createElement("span");
    title.textContent = app.app_name || app.app_id;
    const detail = document.createElement("small");
    detail.textContent = [app.app_id, app.space_name || app.space_id].filter(Boolean).join(" · ");
    button.append(title, detail);
    button.addEventListener("click", () => {
      input.value = app.app_id;
      previewContextFromInput();
      toggleContextAppChoices(false);
      input.focus();
    });
    choices.append(button);
  }
}

function openContextPopover(trigger, { inspectMcp = null } = {}) {
  const popover = document.getElementById("contextPopover");
  if (!popover || popover.open) return;
  renderContext();
  inspectionMcp = inspectMcp;
  const modal = Boolean(inspectMcp);
  document.getElementById("contextTitle").textContent = modal ? "选择调试应用" : "调试";
  document.getElementById("contextHint").hidden = modal;
  document.getElementById("contextInspectionHint").hidden = !modal;
  document.getElementById("resetContextBtn").textContent = modal ? "取消" : "不分应用";
  if (modal) {
    popover.style.removeProperty("top");
    popover.style.removeProperty("left");
    popover.showModal();
  } else {
    positionContextPopover(trigger);
    popover.show();
  }
  document.getElementById("contextAppInput")?.focus();
}

function closeContextPopover() {
  const popover = document.getElementById("contextPopover");
  if (!popover?.open) return;
  toggleContextAppChoices(false);
  appOptions = null;
  document.getElementById("contextAppInput")?.setCustomValidity("");
  inspectionMcp = null;
  popover.close();
}

function closeUserPopover() {
  const button = document.getElementById("currentUserBtn");
  button?.classList.remove("popover-open");
  button?.setAttribute("aria-expanded", "false");
  const popover = document.getElementById("currentUserPopover");
  if (popover?.contains?.(document.activeElement)) button?.focus?.();
  if (popover) popover.hidden = true;
}

function toggleUserPopover() {
  const button = document.getElementById("currentUserBtn");
  if (!button) return;
  const open = !button.classList.contains("popover-open");
  button.classList.toggle("popover-open", open);
  button.setAttribute("aria-expanded", String(open));
  const popover = document.getElementById("currentUserPopover");
  if (popover) popover.hidden = !open;
  if (open) void loadUserDatabaseVersion();
}

function currentFormContext() {
  return api.sanitizeContext({
    app: valueOrNull(document.getElementById("contextAppInput")?.value),
  });
}

function reloadForContextChange(app, inspectMcp = null) {
  const tab = document.body.dataset.consolePage === "configuration"
    ? document.querySelector(".resource-btn.active")?.dataset.tab : null;
  if (tab) {
    history.replaceState({ ...history.state, debugContextResume: { tab, app, inspectMcp } }, "");
  }
  location.reload();
}

function saveContext() {
  if (window.DeepAgentConsoleChat?.state?.streaming) {
    window.DeepAgentConsole?.toast("请先停止当前生成，再修改调试上下文", true);
    return;
  }
  const before = api.loadContext();
  const selection = currentFormContext();
  const inspectMcp = inspectionMcp;
  const input = document.getElementById("contextAppInput");
  let next;
  try {
    if (inspectMcp && !selection.app) throw new Error("请选择或输入应用 ID");
    next = api.saveContext(selection);
  } catch (error) {
    input.setCustomValidity(error.message);
    input.reportValidity();
    return;
  }
  closeContextPopover();

  if (before.app !== next.app || inspectMcp) {
    reloadForContextChange(next.app || null, inspectMcp);
    return;
  }
  renderContext();
  window.DeepAgentConsole?.toast("调试上下文已应用");
}

function resetContext() {
  if (inspectionMcp) {
    closeContextPopover();
    return;
  }
  if (window.DeepAgentConsoleChat?.state?.streaming) {
    window.DeepAgentConsole?.toast("请先停止当前生成，再重置调试上下文", true);
    return;
  }
  const hadScope = Boolean(api.loadContext().app);
  api.clearContext();
  closeContextPopover();
  if (hadScope) {
    reloadForContextChange(null);
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
      if (!popover?.open) openContextPopover(trigger);
      else closeContextPopover();
      return;
    }
    if (!target?.closest(".user-account-host")) closeUserPopover();
  });
  document.addEventListener("pointerdown", (event) => {
    const target = event.target instanceof Element ? event.target : null;
    if (!target?.closest("#contextPopover") && !target?.closest("[data-open-context]")) {
      closeContextPopover();
    }
  });
  document.getElementById("closeContextBtn")?.addEventListener("click", closeContextPopover);
  document.getElementById("contextPopover")?.addEventListener("cancel", event => {
    event.preventDefault();
    closeContextPopover();
  });
  document.getElementById("saveContextBtn")?.addEventListener("click", saveContext);
  document.getElementById("resetContextBtn")?.addEventListener("click", resetContext);
  const appInput = document.getElementById("contextAppInput");
  const choices = document.getElementById("contextAppChoices");
  const appToggle = document.getElementById("contextAppOptionsBtn");
  appToggle?.addEventListener("click", () => toggleContextAppChoices(choices.hidden));
  appInput?.addEventListener("input", () => {
    previewContextFromInput();
    renderContextAppChoices(true);
  });
  appInput?.addEventListener("keydown", event => {
    if (event.key !== "ArrowDown") return;
    event.preventDefault();
    if (choices.hidden) toggleContextAppChoices(true);
    choices.querySelector("button")?.focus();
  });
  choices?.addEventListener("keydown", event => {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      toggleContextAppChoices(false);
      appInput.focus();
      return;
    }
    if (!["ArrowDown", "ArrowUp"].includes(event.key)) return;
    event.preventDefault();
    const rows = [...choices.querySelectorAll("button")];
    const index = rows.indexOf(event.target) + (event.key === "ArrowDown" ? 1 : -1);
    (rows[index] || appInput).focus();
  });
  document.getElementById("retryIdentityBtn")?.addEventListener("click", () => location.reload());
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeContextPopover();
      closeUserPopover();
    }
  });
  window.addEventListener("resize", () => { if (!inspectionMcp) closeContextPopover(); });
  window.addEventListener("scroll", event => {
    const insidePopover = event.target instanceof Node
      && document.getElementById("contextPopover")?.contains(event.target);
    if (!inspectionMcp && !insidePopover) closeContextPopover();
  }, true);
  window.addEventListener("storage", event => {
    if (event.key !== api.CONTEXT_KEY && event.key !== null) return;
    renderContext();
    void refreshIdentity().catch(error => window.DeepAgentConsole?.toast(error.message, true));
  });
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
