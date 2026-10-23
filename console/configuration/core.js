const state = {
  baseUrl: "",
  capabilities: [],
  skills: [],
  llmModels: [],
  llmProviders: [],
  llmProviderTypes: [],
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
  const date = window.DeepAgentTime?.parseServerTimestamp?.(iso);
  return date ? date.toLocaleString("zh-CN") : iso;
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

let activeActionDialogCancel = null;

function ensureActionDialog() {
  let dialog = document.getElementById("consoleActionDialog");
  if (dialog) return dialog;
  dialog = document.createElement("dialog");
  dialog.id = "consoleActionDialog";
  dialog.className = "console-action-dialog";
  dialog.setAttribute("aria-labelledby", "consoleActionDialogTitle");
  dialog.innerHTML = `
    <form method="dialog" novalidate>
      <div class="console-action-dialog-main">
        <span class="console-action-dialog-icon" aria-hidden="true"><i data-lucide="circle-alert"></i></span>
        <div class="console-action-dialog-copy">
          <h3 id="consoleActionDialogTitle"></h3>
          <p id="consoleActionDialogMessage"></p>
          <div class="console-action-dialog-fields hidden" id="consoleActionDialogFields"></div>
        </div>
      </div>
      <footer>
        <button type="button" class="btn" data-action-dialog-cancel>取消</button>
        <button type="submit" class="btn primary" data-action-dialog-confirm>确认</button>
      </footer>
    </form>`;
  document.body.appendChild(dialog);
  return dialog;
}

function openActionDialog({
  title,
  message = "",
  icon = "circle-alert",
  tone = "default",
  variant = "default",
  confirmLabel = "确认",
  cancelLabel = "取消",
  fields = [],
} = {}) {
  const dialog = ensureActionDialog();
  activeActionDialogCancel?.();

  dialog.dataset.tone = tone;
  dialog.dataset.variant = variant;
  dialog.querySelector(".console-action-dialog-icon i")?.setAttribute("data-lucide", icon);
  dialog.querySelector("#consoleActionDialogTitle").textContent = title || "请确认";
  const messageHost = dialog.querySelector("#consoleActionDialogMessage");
  messageHost.replaceChildren();
  for (const part of Array.isArray(message) ? message : [message]) {
    if (typeof part === "string") {
      messageHost.append(document.createTextNode(part));
    } else {
      const emphasis = document.createElement("strong");
      emphasis.textContent = part.text;
      if (part.danger) emphasis.classList.add("console-action-dialog-warning");
      messageHost.append(emphasis);
    }
  }
  messageHost.classList.toggle("hidden", !messageHost.textContent);

  const fieldsHost = dialog.querySelector("#consoleActionDialogFields");
  fieldsHost.replaceChildren();
  fieldsHost.classList.toggle("hidden", !fields.length);
  for (const field of fields) {
    const label = document.createElement("label");
    const caption = document.createElement("span");
    caption.textContent = field.label || field.name;
    const input = field.options ? document.createElement("select") : document.createElement(field.multiline ? "textarea" : "input");
    input.name = field.name;
    if (field.options) {
      for (const option of field.options) {
        const item = document.createElement("option");
        item.value = option.value;
        item.textContent = option.label;
        input.appendChild(item);
      }
    }
    input.value = field.value ?? "";
    input.required = Boolean(field.required);
    if (!field.options && !field.multiline) input.type = "text";
    if (field.placeholder) input.placeholder = field.placeholder;
    label.append(caption, input);
    fieldsHost.appendChild(label);
  }

  const cancelButton = dialog.querySelector("[data-action-dialog-cancel]");
  const confirmButton = dialog.querySelector("[data-action-dialog-confirm]");
  cancelButton.textContent = cancelLabel;
  confirmButton.textContent = confirmLabel;
  confirmButton.classList.toggle("danger", tone === "danger");
  confirmButton.classList.toggle("primary", tone !== "danger");
  window.lucide?.createIcons();

  return new Promise((resolve) => {
    let settled = false;
    const form = dialog.querySelector("form");
    const finish = (result) => {
      if (settled) return;
      settled = true;
      form.removeEventListener("submit", onSubmit);
      cancelButton.removeEventListener("click", onCancel);
      dialog.removeEventListener("cancel", onCancel);
      dialog.removeEventListener("close", onClose);
      if (dialog.open) dialog.close();
      if (activeActionDialogCancel === onCancel) activeActionDialogCancel = null;
      resolve(result);
    };
    const onSubmit = (event) => {
      event.preventDefault();
      if (!form.reportValidity()) return;
      const values = Object.fromEntries(new FormData(form).entries());
      finish(fields.length ? values : true);
    };
    const onCancel = (event) => {
      event?.preventDefault?.();
      finish(null);
    };
    const onClose = () => finish(null);
    form.addEventListener("submit", onSubmit);
    cancelButton.addEventListener("click", onCancel);
    dialog.addEventListener("cancel", onCancel);
    dialog.addEventListener("close", onClose);
    activeActionDialogCancel = onCancel;
    dialog.showModal();
    (fieldsHost.querySelector("input, textarea, select") || (tone === "danger" ? cancelButton : confirmButton)).focus();
  });
}

async function confirmAction(options) {
  return (await openActionDialog(options)) === true;
}

// The server supplies current impact and binds the confirmation to that set.
// A changed set requires another explicit confirmation, never an automatic retry.
async function deleteWithImpactConfirmation(path, { title, describeImpact, confirmLabel }) {
  let confirmationToken;
  for (;;) {
    try {
      await apiFetch(path, {
        method: "DELETE",
        ...(confirmationToken ? { body: JSON.stringify({ confirmation_token: confirmationToken }) } : {}),
      });
      return true;
    } catch (error) {
      const detail = error.detail;
      if (error.status !== 409 || detail?.code !== "delete_confirmation_required"
          || !detail.confirmation_token || !detail.impact) throw error;
      if (!(await confirmAction({
        title,
        message: describeImpact(detail.impact),
        icon: "triangle-alert",
        tone: "danger",
        confirmLabel,
      }))) return false;
      confirmationToken = detail.confirmation_token;
    }
  }
}

async function requestActionFields(options) {
  return openActionDialog(options);
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
  if ((!options.method || options.method === "GET") && !options.signal
    && /^\/api\/v1\/(agents|llm-models)(\?selectable_only=false)?$/.test(path)
    && window.DeepAgentConsoleDirectory) {
    return window.DeepAgentConsoleDirectory.read(path, { force: true });
  }
  const { headers: optionHeaders, ...rest } = options;
  const url = typeof serviceUrl === "function" ? serviceUrl(path) : `${getBaseUrl()}${path}`;
  const resp = await daFetch(url, {
    ...rest,
    headers: { "Content-Type": "application/json", ...optionHeaders },
  });

  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const body = await resp.json();
      detail = body.detail || body.message || JSON.stringify(body);
    } catch {
      /* ignore */
    }
    if (resp.status === 503 && typeof detail === "string" && detail.startsWith("Database is not configured.")) showDbBanner(true);
    const error = new Error(window.AdminFormFeedback?.formatDetail(detail) || String(detail));
    error.status = resp.status;
    error.detail = detail;
    throw error;
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

async function loadCapabilities() {
  state.capabilities = await apiFetch("/api/v1/tools");
  return state.capabilities;
}

async function loadLlmModels() {
  try {
    state.llmModels = await apiFetch("/api/v1/llm-models?selectable_only=false");
  } catch {
    state.llmModels = [];
  }
  return state.llmModels;
}

async function loadLlmProviders() {
  try {
    state.llmProviders = await apiFetch("/api/v1/llm-providers");
  } catch {
    state.llmProviders = [];
  }
  return state.llmProviders;
}

async function loadLlmProviderTypes() {
  try {
    const response = await apiFetch("/api/v1/llm-provider-types");
    state.llmProviderTypes = Array.isArray(response?.items) ? response.items : response;
  } catch {
    state.llmProviderTypes = [];
  }
  return state.llmProviderTypes;
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
  select.disabled = false;
  select.innerHTML = `<option value="" hidden></option>`
    + window.DeepAgentModelOptions.renderGroupedModelOptions(models, {
      selectedId, emptyLabel: null, enabledOnly: true, groupIcon: (provider) => providerBrand(provider).src,
    });
  select.value = selectedId || "";
}

function initTabs() {
  const tabs = document.querySelectorAll(".tab");
  window.configurationTabsAdmin = { pending: null };
  tabs.forEach((tab) => {
    const activate = async () => {
      if (window.agentsAdmin?.isEditorOpen?.() && !(await window.agentsAdmin.requestCloseEditor())) return;
      if (window.mcpAdmin?.isEditorOpen?.() && !(await window.mcpAdmin.requestCloseEditor())) return;
      if (tab.dataset.tab !== "integrations" && window.integrationsAdmin?.deactivate() === false) return;
      if (tab.dataset.tab !== "channels" && window.wecomApplicationsAdmin?.deactivate() === false) return;
      tabs.forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      const name = tab.dataset.tab;
      document
        .querySelector(".configuration-content")
        ?.classList.toggle("global-settings-active", name === "global-settings");
      document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
      document.getElementById(`panel-${name}`)?.classList.add("active");
      document.dispatchEvent(new CustomEvent("deep-agent-configuration-tab", { detail: { name } }));
      if (name === "global-settings" && window.globalSettingsAdmin) {
        return window.globalSettingsAdmin.refresh();
      }
      if (name === "agents" && window.agentsAdmin) {
        return window.agentsAdmin.refresh();
      }
      if (name === "llm-providers" && window.llmProvidersAdmin) return window.llmProvidersAdmin.refresh();
      if (name === "tools") return window.toolsAdmin?.refresh();
      if (name === "skills" && window.skillsAdmin) {
        window.skillsAdmin.selectedCode = null;
        return window.skillsAdmin.refresh();
      }
      if (name === "mcp" && window.mcpAdmin) return window.mcpAdmin.refresh();
      if (name === "integrations") return window.integrationsAdmin?.refresh();
      if (name === "channels" && window.channelsAdmin) return window.channelsAdmin.refresh();
    };
    tab.addEventListener("click", () => {
      const pending = activate();
      window.configurationTabsAdmin.pending = pending;
      pending.catch((error) => showToast(error.message || "配置加载失败", true));
      return pending;
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
});

window.__DatabaseSchemaVersionUI = {
  formatDatabaseSchemaVersion,
};
window.__ConfigurationSaveUI = {
  refreshAfterSuccessfulMutation,
  refreshAfterSuccessfulSave,
};
