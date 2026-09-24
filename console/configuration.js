let initialized = false;
import "./model-options.js?v=20260924-config-catalog-5";
import * as ProviderCatalog from "./provider-catalog.js?v=20260913-49";
import * as SkillFrontmatter from "./skill-frontmatter.js?v=20260913-49";
import * as SchemaText from "./schema-editor.js?v=20260913-49";
if (typeof window !== "undefined") window.DeepAgentProviderCatalog = ProviderCatalog;
if (typeof window !== "undefined") window.DeepAgentSkillFrontmatter = SkillFrontmatter;
if (typeof window !== "undefined") window.DeepAgentSchemaText = SchemaText;
let loadingPromise = null;
const CONFIGURATION_ASSET_VERSION = "20260924-docs-entry-1";
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

export function restoreConfigurationTab() {
  const tab = document.querySelector(`[data-tab="${activeConfigurationTab}"]`);
  if (tab && !tab.classList.contains("active")) tab.click();
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
    script.src = versionedAsset(src).href;
    script.onload = resolve;
    script.onerror = () => reject(new Error(`脚本加载失败：${src}`));
    document.body.appendChild(script);
  });
}

async function loadAdminScripts() {
  window.loadWecomMemberTable = () => import("./configuration/wecom-members.js?v=20260918-release01012");
  const scripts = [
    "./configuration/core.js",
    "./configuration/form-feedback.js",
    "./configuration/global-settings.js",
    "./configuration/database-connection.js",
    "./configuration/llm-providers.js",
    "./configuration/llm-models.js",
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
    "./configuration/channels.js",
    "./configuration/integrations.js",
  ];
  for (const script of scripts) await loadClassicScript(script);

  // The legacy scripts were loaded after the browser's original DOMContentLoaded.
  // Replaying it once initializes their existing, tested bindings against the new DOM.
  document.dispatchEvent(new Event("DOMContentLoaded"));
  await import("./configuration/catalog.js?v=20260913-49");
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
      await window.DeepAgentConsole?.showPage("workspace");
      await window.DeepAgentConsoleChat?.selectAgent(agentId);
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

export async function initConfiguration() {
  if (initialized) return loadingPromise;
  initialized = true;
  loadingPromise = (async () => {
    const [adminDoc, modelsDoc] = await Promise.all([
      fetchDocument("./configuration-template.html"),
      fetchDocument("./catalog-template.html"),
    ]);
    appendAdminStructure(adminDoc, modelsDoc);
    await loadAdminScripts();
    decorateConfiguration();
    document.getElementById("configurationLoading")?.classList.add("hidden");
  })().catch((error) => {
    const loading = document.getElementById("configurationLoading");
    if (loading) loading.innerHTML = `<span class="configuration-error">${error.message}</span>`;
    throw error;
  });
  return loadingPromise;
}
