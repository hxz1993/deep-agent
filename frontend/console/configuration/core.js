const state = {
  baseUrl: "",
  capabilities: [],
  skills: [],
  llmModels: [],
  editingAgentId: null,
  editingLlmModelId: null,
  editingMcp: null,
};

function getBaseUrl() {
  return typeof defaultApiBaseUrl === "function" ? defaultApiBaseUrl() : state.baseUrl;
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text ?? "";
  return div.innerHTML;
}

function formatDate(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("zh-CN");
  } catch {
    return iso;
  }
}

let toastTimer;
function showToast(message, isError = false) {
  const el = document.getElementById("toast");
  if (!el) return;
  el.textContent = message;
  el.style.borderColor = isError ? "rgba(248, 81, 73, 0.5)" : "var(--border)";
  el.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hidden"), 3200);
}

function showDbBanner(show) {
  const el = document.getElementById("dbBanner");
  if (el) el.classList.toggle("hidden", !show);
}

async function refreshAfterSuccessfulMutation(successLabel, refresh) {
  try {
    await refresh();
    return true;
  } catch (error) {
    const detail = typeof error?.message === "string" ? error.message : String(error);
    showToast(`${successLabel}，但页面重新加载失败：${detail}`, true);
    return false;
  }
}

async function refreshAfterSuccessfulSave(label, refresh) {
  return refreshAfterSuccessfulMutation(`${label}已保存`, refresh);
}

async function apiFetch(path, options = {}) {
  const { headers: optionHeaders, ...rest } = options;
  const url = typeof serviceUrl === "function" ? serviceUrl(path) : `${getBaseUrl()}${path}`;
  const resp = await daFetch(url, {
    ...rest,
    headers: { "Content-Type": "application/json", ...optionHeaders },
  });

  if (resp.status === 503) {
    showDbBanner(true);
    throw new Error("数据库未配置 (503)");
  }

  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const body = await resp.json();
      detail = body.detail || body.message || JSON.stringify(body);
    } catch {
      /* ignore */
    }
    throw new Error(window.AdminFormFeedback?.formatDetail(detail) || String(detail));
  }

  if (resp.status === 204) return null;
  return resp.json();
}

function formatDatabaseSchemaVersion(value) {
  const revision = String(value ?? "").trim();
  if (!revision) return "—";
  const displayRevision = /^\d+$/.test(revision)
    ? String(Number.parseInt(revision, 10))
    : revision;
  return `V${displayRevision}`;
}

async function loadDatabaseSchemaVersion() {
  const target = document.getElementById("databaseSchemaVersion");
  if (!target) return;
  try {
    const info = await apiFetch("/api/v1/database-info");
    target.textContent = formatDatabaseSchemaVersion(info?.schema_version);
    target.removeAttribute("title");
  } catch (error) {
    target.textContent = "—";
    target.title = error instanceof Error ? error.message : String(error);
  }
}

async function loadCapabilities() {
  state.capabilities = await apiFetch("/api/v1/tools");
  return state.capabilities;
}

async function loadLlmModels() {
  try {
    state.llmModels = await apiFetch("/api/v1/llm-models");
  } catch {
    state.llmModels = [];
  }
  return state.llmModels;
}

async function loadSkills() {
  try {
    state.skills = await apiFetch("/api/v1/skills");
  } catch {
    state.skills = [];
  }
  return state.skills;
}

function populateAgentModelSelect(selectedId = null) {
  const select = document.getElementById("agentModelId");
  if (!select) return;
  const models = state.llmModels || [];
  if (!models.length) {
    select.innerHTML = `<option value="">（请先创建模型配置）</option>`;
    select.disabled = true;
    return;
  }
  select.disabled = false;
  select.innerHTML = models
    .map(
      (m) =>
        `<option value="${escapeHtml(m.id)}" ${m.id === selectedId ? "selected" : ""}>${escapeHtml(m.name)} (${escapeHtml(m.model)})</option>`
    )
    .join("");
}

function initTabs() {
  const tabs = document.querySelectorAll(".tab");
  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      tabs.forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      const name = tab.dataset.tab;
      document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
      document.getElementById(`panel-${name}`)?.classList.add("active");
      if (name === "global-settings" && window.globalSettingsAdmin) {
        window.globalSettingsAdmin.refresh();
      }
      if (name === "database-connection" && window.databaseConnectionAdmin) {
        window.databaseConnectionAdmin.refresh();
      }
      if (name === "agents" && window.agentsAdmin) {
        Promise.all([loadLlmModels(), loadCapabilities(), loadSkills()]).then(() => window.agentsAdmin.refresh());
      }
      if (name === "llm-models" && window.llmModelsAdmin) window.llmModelsAdmin.refresh();
      if (name === "builtin-tools" && window.builtinToolsAdmin) window.builtinToolsAdmin.refresh();
      if (name === "skills" && window.skillsAdmin) window.skillsAdmin.refresh();
      if (name === "auxiliary-models" && window.auxiliaryModelsAdmin) window.auxiliaryModelsAdmin.refresh();
      if (name === "mcp" && window.mcpAdmin) window.mcpAdmin.refresh();
      if (name === "ui-tools" && window.uiToolsAdmin) window.uiToolsAdmin.refresh();
      if (name === "catalog" && window.modelCatalogAdmin) window.modelCatalogAdmin.refresh();
    });
  });
}

function parseJsonOrEmpty(text) {
  const trimmed = (text || "").trim();
  if (!trimmed) return undefined;
  return JSON.parse(trimmed);
}

function parseArgs(text) {
  const trimmed = (text || "").trim();
  if (!trimmed) return [];
  if (trimmed.startsWith("[")) return JSON.parse(trimmed);
  return trimmed.split(",").map((s) => s.trim()).filter(Boolean);
}

document.addEventListener("DOMContentLoaded", () => {
  const chatLink = document.getElementById("chatLink");
  if (chatLink && location.protocol !== "file:") {
    chatLink.href = "/";
  }

  initTabs();
  loadDatabaseSchemaVersion();
  loadCapabilities().catch(() => {});
  loadLlmModels().catch(() => {});
  loadSkills().catch(() => {});
});

window.__DatabaseSchemaVersionUI = {
  formatDatabaseSchemaVersion,
};
window.__ConfigurationSaveUI = {
  refreshAfterSuccessfulMutation,
  refreshAfterSuccessfulSave,
};
