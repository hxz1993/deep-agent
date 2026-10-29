let initialized = false;
import * as JsonInput from "./json-input.js?v=20261022-output-compact4";
import * as OutputSchema from "./output-schema-fields.js?v=20261022-output-compact4";
if (typeof window !== "undefined") { window.DeepAgentJsonInput = JsonInput; window.DeepAgentOutputSchema = OutputSchema; }
import "./model-options.js?v=20260925-codex-1";
import * as ProviderCatalog from "./provider-catalog.js?v=20261015-provider-protocol-groups";
import * as SkillFrontmatter from "./skill-frontmatter.js?v=20260913-49";
import * as SchemaText from "./schema-editor.js?v=20260913-49";
if (typeof window !== "undefined") window.DeepAgentProviderCatalog = ProviderCatalog;
if (typeof window !== "undefined") window.DeepAgentSkillFrontmatter = SkillFrontmatter;
if (typeof window !== "undefined") window.DeepAgentSchemaText = SchemaText;
let loadingPromise = null;
const CONFIGURATION_ASSET_VERSION = "20261029-release01411";
const CONFIGURATION_TABS = new Set([
  "agents",
  "llm-providers",
  "tools",
  "skills",
  "mcp",
  "channels",
  "integrations",
  "global-settings",
]);
let activeConfigurationTab = "agents";

export function rememberConfigurationTab(name) {
  if (name === "builtin-tools" || name === "ui-tools") name = "tools";
  if (name === "database-connection" || name === "catalog") name = "global-settings";
  if (CONFIGURATION_TABS.has(name)) activeConfigurationTab = name;
  return activeConfigurationTab;
}

export function currentConfigurationTab() {
  return activeConfigurationTab;
}

export async function restoreConfigurationTab() {
  const tab = document.querySelector(`[data-tab="${activeConfigurationTab}"]`);
  if (tab && !tab.classList.contains("active")) {
    tab.click();
    await window.configurationTabsAdmin?.pending;
  } else {
    await refreshCurrentConfiguration();
  }
}

function versionedAsset(path) {
  const url = new URL(path, import.meta.url);
  url.searchParams.set("v", CONFIGURATION_ASSET_VERSION);
  return url;
}

async function fetchDocument(path) {
  const response = await fetch(versionedAsset(path));
  if (!response.ok) throw new Error(`加载配置结构失败：HTTP ${response.status}`);
  return new DOMParser().parseFromString(await response.text(), "text/html");
}

function appendAdminStructure(adminDoc, modelsDoc) {
  const host = document.getElementById("configurationHost");
  const adminApp = adminDoc.querySelector(".admin-app");
  if (!host || !adminApp) throw new Error("管理后台结构不完整");

  const dbBanner = adminApp.querySelector("#dbBanner");
  const toast = adminApp.querySelector("#toast");
  if (dbBanner) host.appendChild(dbBanner);
  if (toast) host.appendChild(toast);

  adminApp.querySelectorAll(":scope > section.panel").forEach((panel) => host.appendChild(panel));

  const catalog = document.getElementById("settings-models");
  const card = modelsDoc.querySelector(".models-card");
  if (!card) throw new Error("模型目录结构不完整");
  card.querySelector(".settings-theme-field")?.remove();
  catalog.append(...Array.from(card.children));

  adminDoc.querySelectorAll("body > dialog").forEach((dialog) => {
    dialog.classList.add("console-detail-dialog");
    document.body.appendChild(dialog);
  });
}

function loadClassicScript(src) {
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    // Ordered classic scripts download concurrently, but execute in insertion order.
    script.async = false;
    script.src = versionedAsset(src).href;
    script.onload = resolve;
    script.onerror = () => reject(new Error(`脚本加载失败：${src}`));
    document.body.appendChild(script);
  });
}

async function loadAdminScripts() {
  window.loadWecomMemberTable = () => import("./configuration/wecom-members.js?v=20260929-component-webhook");
  const scripts = [
    "./configuration/core.js",
    "./configuration/form-feedback.js",
    "./configuration/global-settings.js",
    "./configuration/attachment-upload.js",
    "./configuration/database-connection.js",
    "./configuration/llm-providers.js",
    "./configuration/llm-models.js",
    "./configuration/agent-output.js",
    "./configuration/agents.js",
    "./configuration/agent-packages.js",
    "./configuration/agent-package-export.js",
    "./configuration/builtin-tools.js",
    "./configuration/system-tools.js",
    "./configuration/skills.js",
    "./configuration/mcp.js",
    "./ui-editable-select.js",
    "./configuration/ui-tools.js",
    "./configuration/tools.js",
    "./configuration/wecom-applications.js",
    "./vendor/qrcode.js",
    "./configuration/channel-registration.js",
    "./configuration/channels.js",
    "./configuration/integrations.js",
  ];
  const scriptUrls = new Set(scripts.map((src) => versionedAsset(src).href));
  let executionFailed;
  let hasExecutionError = false;
  const failedExecution = new Promise((_, reject) => { executionFailed = reject; });
  const onScriptError = (event) => {
    if (scriptUrls.has(event.filename)) {
      hasExecutionError = true;
      executionFailed(new Error(`脚本执行失败：${event.filename}`));
    }
  };
  window.addEventListener?.("error", onScriptError);
  try {
    const loaded = Promise.all(scripts.map(loadClassicScript)).then(async () => {
      if (hasExecutionError) return;
      // These scripts arrived after the browser's original DOMContentLoaded.
      // Replay once for their existing bindings; initialization makes no API calls.
      document.dispatchEvent(new Event("DOMContentLoaded"));
      await import("./configuration/catalog.js?v=20261023-console-load1");
    });
    await Promise.race([loaded, failedExecution]);
  } finally {
    window.removeEventListener?.("error", onScriptError);
  }
}

function decorateConfiguration() {
  document.querySelectorAll(".resource-btn").forEach((button) => {
    const label = button.querySelector("span")?.textContent?.trim();
    if (label && !button.title) button.title = label;
  });
  document.querySelectorAll("#configurationHost .panel-toolbar h2").forEach((heading) => {
    heading.parentElement?.classList.add("configuration-heading");
  });
  document.querySelectorAll("#configurationHost dialog.modal").forEach((dialog) => {
    dialog.classList.add("console-detail-dialog");
  });
  document.getElementById("configurationHost")?.addEventListener("click", async (event) => {
    const link = event.target.closest("a[href]");
    if (!link) return;
    const url = new URL(link.href, location.href);
    const agentId = url.searchParams.get("agent_id");
    if (agentId && link.matches("[data-agent-chat]")) {
      event.preventDefault();
      if (await window.DeepAgentConsole?.showPage("workspace") === false) return;
      if (await window.DeepAgentConsoleChat?.selectAgent(agentId) === false) {
        window.DeepAgentConsole?.toast("Agent 已不可用，请刷新列表后重试", true);
      }
      return;
    }
  }, true);
  window.lucide?.createIcons();
}

export function refreshCurrentConfiguration() {
  const active = document.querySelector(".resource-btn.active")?.dataset.tab;
  if (active === "global-settings") return window.globalSettingsAdmin?.refresh();
  if (active === "agents") return window.agentsAdmin?.refresh();
  if (active === "llm-providers") return window.llmProvidersAdmin?.refresh();
  if (active === "tools") return window.toolsAdmin?.refresh();
  if (active === "skills") return window.skillsAdmin?.refresh();
  if (active === "mcp") return window.mcpAdmin?.refresh();
  if (active === "integrations") return window.integrationsAdmin?.refresh();
  if (active === "channels") return window.channelsAdmin?.refresh();
  return null;
}

export function initConfiguration() {
  if (initialized) return loadingPromise;
  initialized = true;
  let structureStarted = false;
  loadingPromise = (async () => {
    const [adminDoc, modelsDoc] = await Promise.all([
      fetchDocument("./configuration-template.html"),
      fetchDocument("./catalog-template.html"),
    ]);
    structureStarted = true;
    appendAdminStructure(adminDoc, modelsDoc);
    await loadAdminScripts();
    decorateConfiguration();
    document.getElementById("configurationLoading")?.classList.add("hidden");
  })().catch((error) => {
    const loading = document.getElementById("configurationLoading");
    if (loading) {
      const message = document.createElement("span");
      message.className = "configuration-error";
      message.textContent = error.message;
      const retry = document.createElement("button");
      retry.type = "button";
      retry.className = "btn";
      retry.textContent = structureStarted ? "重新加载页面" : "重试加载";
      retry.addEventListener("click", () => {
        if (structureStarted) location.reload();
        else void window.DeepAgentConsole?.showPage("configuration");
      });
      loading.replaceChildren(message, retry);
    }
    // Some classic scripts may have executed before a later script failed.
    // Only template download failures are safe to retry in this document.
    if (!structureStarted) {
      initialized = false;
      loadingPromise = null;
    }
    throw error;
  });
  return loadingPromise;
}
