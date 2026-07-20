const STORAGE_KEY = "deep-agent-base-url";

const state = {
  baseUrl: "",
  capabilities: [],
  llmModels: [],
  editingAgentId: null,
  editingLlmModelId: null,
  llmModelInitialModalityMode: "auto",
  editingMcp: null,
};

function defaultBaseUrl() {
  if (location.protocol === "file:") return "http://localhost:8000";
  const root = window.__DEEP_AGENT_APP_ROOT__ || window.__DEEP_AGENT_BASE__ || "/";
  return `${location.origin}${root.replace(/\/$/, "")}`;
}

function getBaseUrl() {
  return state.baseUrl || defaultBaseUrl();
}

function loadSettings() {
  const saved = localStorage.getItem(STORAGE_KEY);
  state.baseUrl = saved || defaultBaseUrl();
  const input = document.getElementById("baseUrlInput");
  if (input) input.value = state.baseUrl;
}

function saveBaseUrl(value) {
  state.baseUrl = value.trim() || defaultBaseUrl();
  localStorage.setItem(STORAGE_KEY, state.baseUrl);
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

async function apiFetch(path, options = {}) {
  const { headers: optionHeaders, ...rest } = options;
  const url = `${getBaseUrl()}${path}`;
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
      if (name === "agents" && window.agentsAdmin) {
        loadLlmModels().then(() => window.agentsAdmin.refresh());
      }
      if (name === "llm-models" && window.llmModelsAdmin) window.llmModelsAdmin.refresh();
      if (name === "auxiliary-models" && window.auxiliaryModelsAdmin) window.auxiliaryModelsAdmin.refresh();
      if (name === "mcp" && window.mcpAdmin) window.mcpAdmin.refresh();
      if (name === "ui-tools" && window.uiToolsAdmin) window.uiToolsAdmin.refresh();
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
  loadSettings();

  const baseInput = document.getElementById("baseUrlInput");
  baseInput?.addEventListener("change", () => {
    saveBaseUrl(baseInput.value);
    if (window.agentsAdmin) window.agentsAdmin.refresh();
    if (window.llmModelsAdmin) window.llmModelsAdmin.refresh();
    if (window.auxiliaryModelsAdmin) window.auxiliaryModelsAdmin.refresh();
    if (window.mcpAdmin) window.mcpAdmin.refresh();
    if (window.uiToolsAdmin) window.uiToolsAdmin.refresh();
  });

  const chatLink = document.getElementById("chatLink");
  if (chatLink && location.protocol !== "file:") {
    chatLink.href = "/";
  }

  initTabs();
  loadCapabilities().catch(() => {});
  loadLlmModels().catch(() => {});
});
