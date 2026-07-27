let initialized = false;
let loadingPromise = null;
const CONFIGURATION_ASSET_VERSION = "20260727-1";

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

  const catalog = document.createElement("section");
  catalog.className = "panel catalog-panel";
  catalog.id = "panel-catalog";
  const card = modelsDoc.querySelector(".models-card");
  if (!card) throw new Error("模型目录结构不完整");
  card.querySelector(".settings-theme-field")?.remove();
  catalog.append(...Array.from(card.children));
  host.insertBefore(catalog, document.getElementById("panel-mcp"));

  adminDoc.querySelectorAll("body > dialog").forEach((dialog) => document.body.appendChild(dialog));
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
  const scripts = [
    "./configuration/core.js",
    "./configuration/form-feedback.js",
    "./configuration/global-settings.js",
    "./configuration/llm-models.js",
    "./configuration/auxiliary-models.js",
    "./configuration/agents.js",
    "./configuration/mcp.js",
    "./configuration/ui-schema-editor.js",
    "./configuration/ui-tools.js",
  ];
  for (const script of scripts) await loadClassicScript(script);

  // The legacy scripts were loaded after the browser's original DOMContentLoaded.
  // Replaying it once initializes their existing, tested bindings against the new DOM.
  document.dispatchEvent(new Event("DOMContentLoaded"));
  await import("./configuration/catalog.js?v=20260721-1");
  installCatalogActions();
}

function installCatalogActions() {
  const table = document.getElementById("modelsTable");
  const body = document.getElementById("modelsTableBody");
  if (!table || !body) return;
  const header = table.querySelector("thead tr");
  if (header && !header.querySelector(".console-catalog-action")) {
    const cell = document.createElement("th");
    cell.className = "console-catalog-action";
    cell.textContent = "操作";
    header.appendChild(cell);
  }

  const decorateRows = () => {
    body.querySelectorAll("tr").forEach((row) => {
      const cells = row.querySelectorAll(":scope > td");
      if (cells.length === 1 && cells[0].classList.contains("empty-cell")) {
        cells[0].colSpan = 10;
        return;
      }
      if (cells.length < 9 || row.querySelector(".console-catalog-action")) return;
      const provider = cells[0].querySelector(".models-sub")?.textContent.trim()
        || cells[0].querySelector("strong")?.textContent.trim()
        || "";
      const modelId = cells[1].textContent.trim();
      const action = document.createElement("td");
      action.className = "console-catalog-action";
      const button = document.createElement("button");
      button.type = "button";
      button.className = "btn btn-sm";
      button.textContent = "使用此模型";
      button.addEventListener("click", () => openModelFromCatalog(provider, modelId));
      action.appendChild(button);
      row.appendChild(action);
    });
  };
  new MutationObserver(decorateRows).observe(body, { childList: true });
  decorateRows();
}

function openModelFromCatalog(provider, modelId) {
  document.querySelector('[data-tab="llm-models"]')?.click();
  window.llmModelsAdmin?.openCreate();
  const normalizedProvider = String(provider || "").trim().toLowerCase();
  document.getElementById("llmModelName").value = modelId;
  document.getElementById("llmModelModel").value = normalizedProvider
    ? `${normalizedProvider}:${modelId}`
    : modelId;
  document.getElementById("llmModelDescription").value = `从模型目录选择：${provider || "未知 Provider"}`;
}

function decorateConfiguration() {
  document.querySelectorAll("#configurationHost .panel-toolbar h2").forEach((heading) => {
    heading.parentElement?.classList.add("configuration-heading");
  });
  document.querySelectorAll("#configurationHost dialog.modal").forEach((dialog) => {
    dialog.classList.add("console-detail-dialog");
  });
  document.getElementById("configurationHost")?.addEventListener("click", (event) => {
    if (event.target.closest("[data-open-catalog]")) {
      document.querySelector('[data-tab="catalog"]')?.click();
      return;
    }
    const link = event.target.closest("a[href]");
    if (!link) return;
    const url = new URL(link.href, location.href);
    const agentId = url.searchParams.get("agent_id");
    if (agentId && link.textContent.includes("发起对话")) {
      event.preventDefault();
      window.DeepAgentConsoleChat?.selectAgent(agentId);
      window.DeepAgentConsole?.showPage("workspace");
      return;
    }
  });
  window.lucide?.createIcons();
}

export function refreshCurrentConfiguration() {
  const active = document.querySelector(".resource-btn.active")?.dataset.tab;
  if (active === "global-settings") return window.globalSettingsAdmin?.refresh();
  if (active === "agents") return window.agentsAdmin?.refresh();
  if (active === "llm-models") return window.llmModelsAdmin?.refresh();
  if (active === "auxiliary-models") return window.auxiliaryModelsAdmin?.refresh();
  if (active === "mcp") return window.mcpAdmin?.refresh();
  if (active === "ui-tools") return window.uiToolsAdmin?.refresh();
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
