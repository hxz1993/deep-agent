"use strict";

const PAGE_SIZE = 50;
const MODALITY_LABELS = {
  text: "文本",
  image: "图像",
  audio: "音频",
  video: "视频",
};
const TAG_LABELS = {
  attachment: "附件",
  reasoning: "推理",
  tool_call: "工具",
  structured_output: "结构化",
  temperature: "温度",
  open_weights: "开放",
  interleaved: "交错",
};

const catalogState = {
  items: [],
  total: 0,
  offset: 0,
  query: "",
  providerId: "",
  modality: "",
  facets: { labs: [], providers: [] },
  refreshedAt: null,
  loading: false,
};

let searchTimer = null;
let loadGeneration = 0;
let pendingLoad = false;

function element(id) {
  return document.getElementById(id);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function formatTokenCount(value) {
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) return "—";
  if (number >= 1_000_000) {
    const digits = number % 1_000_000 === 0 ? 0 : 1;
    return `${(number / 1_000_000).toFixed(digits)}M`;
  }
  if (number >= 1000) return `${Math.round(number / 1000)}K`;
  return String(number);
}

function formatDateTime(value) {
  if (!value) return "尚未更新";
  const date = window.DeepAgentTime?.parseServerTimestamp?.(value);
  return date ? date.toLocaleString("zh-CN") : "尚未更新";
}

function renderBadges(values, labels = {}) {
  if (!Array.isArray(values) || !values.length) {
    return '<span class="catalog-muted">—</span>';
  }
  return values
    .map((value) => `<span class="badge">${escapeHtml(labels[value] || value)}</span>`)
    .join(" ");
}

async function parseJsonResponse(response) {
  const text = await response.text();
  const trimmed = text.trimStart();
  if (trimmed.startsWith("<") || trimmed.toLowerCase().startsWith("<!doctype")) {
    throw new Error("后端返回了 HTML，请确认当前服务已升级到 0.2.2");
  }
  try {
    return JSON.parse(text);
  } catch (error) {
    throw new Error(trimmed.slice(0, 160) || error.message || "响应不是有效 JSON");
  }
}

async function request(path, options = {}) {
  const response = await daFetch(serviceUrl(path), {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  const body = await parseJsonResponse(response);
  if (!response.ok) {
    const detail = body?.detail;
    throw new Error(
      typeof detail === "string"
        ? detail
        : detail
          ? JSON.stringify(detail)
          : `HTTP ${response.status}`,
    );
  }
  return body;
}

function queryString() {
  const params = new URLSearchParams({
    view: "model",
    offset: String(catalogState.offset),
    limit: String(PAGE_SIZE),
  });
  if (catalogState.query.trim()) params.set("q", catalogState.query.trim());
  if (catalogState.providerId) params.set("provider_id", catalogState.providerId);
  if (catalogState.modality) params.set("modality", catalogState.modality);
  return params.toString();
}

function setFeedback(message = "", isError = false) {
  const feedback = element("catalogFeedback");
  if (!feedback) return;
  feedback.textContent = message;
  feedback.classList.toggle("hidden", !message);
  feedback.classList.toggle("error", Boolean(isError));
}

function renderFacets() {
  const providers = Array.isArray(catalogState.facets?.providers) ? catalogState.facets.providers : [];
  const sidebar = element("catalogSupplierSidebar");
  if (!sidebar) return;
  sidebar.innerHTML = [
    '<div class="catalog-lab-heading"><span>供应商</span></div>',
    ...providers.map((provider) => `<button type="button" class="catalog-lab-item ${catalogState.providerId === provider.id ? "active" : ""}"
      data-provider-id="${escapeHtml(provider.id)}" aria-pressed="${catalogState.providerId === provider.id}">
      <span>${escapeHtml(provider.label)}</span><strong>${Number(provider.count || 0)}</strong>
    </button>`),
  ].join("");
  sidebar.querySelectorAll("[data-provider-id]").forEach((button) => {
    button.addEventListener("click", () => {
      if (catalogState.providerId === button.dataset.providerId) return;
      catalogState.providerId = button.dataset.providerId;
      catalogState.offset = 0;
      loadCatalog();
    });
  });
  const selected = providers.find((provider) => provider.id === catalogState.providerId);
  if (element("catalogSupplierTitle")) element("catalogSupplierTitle").textContent = selected?.label || "模型";
}

// Keep the backing select and change event as the filter contract; the visible
// control follows the same Console select markup as configuration and diagnostics.
function renderCatalogSelect(id) {
  const select = element(id);
  const menu = element(`${id}Menu`);
  const label = element(`${id}Label`);
  if (!select || !menu || !label) return;
  const options = Array.from(select.options);
  label.textContent = options.find((option) => option.value === select.value)?.textContent || "";
  menu.innerHTML = options.map((option) => `<button type="button" class="console-select-option${option.value === select.value ? " selected" : ""}" role="option" aria-selected="${option.value === select.value}" data-catalog-option="${escapeHtml(option.value)}"><span>${escapeHtml(option.textContent)}</span></button>`).join("");
}

function closeCatalogSelects({ restoreFocus = false } = {}) {
  document.querySelectorAll("[data-catalog-select]").forEach((host) => {
    const trigger = host.querySelector(".console-select-trigger");
    const menu = host.querySelector(".console-select-menu");
    if (restoreFocus && trigger.getAttribute("aria-expanded") === "true") trigger.focus();
    trigger.setAttribute("aria-expanded", "false");
    menu.classList.add("hidden");
  });
}

function bindCatalogSelect(id) {
  const select = element(id);
  const trigger = element(`${id}Trigger`);
  const menu = element(`${id}Menu`);
  if (!select || !trigger || !menu) return;
  function open() {
    closeCatalogSelects();
    renderCatalogSelect(id);
    menu.classList.remove("hidden");
    trigger.setAttribute("aria-expanded", "true");
    (menu.querySelector('[aria-selected="true"]') || menu.querySelector("button"))?.focus();
  }
  trigger.addEventListener("click", () => {
    if (trigger.getAttribute("aria-expanded") === "true") closeCatalogSelects();
    else open();
  });
  trigger.addEventListener("keydown", (event) => {
    if (!["ArrowDown", "ArrowUp"].includes(event.key)) return;
    event.preventDefault();
    open();
  });
  menu.addEventListener("click", (event) => {
    const option = event.target.closest("[data-catalog-option]");
    if (!option) return;
    select.value = option.dataset.catalogOption;
    renderCatalogSelect(id);
    closeCatalogSelects({ restoreFocus: true });
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  menu.addEventListener("keydown", (event) => {
    const options = Array.from(menu.querySelectorAll("[data-catalog-option]"));
    const index = options.indexOf(document.activeElement);
    if (event.key === "Escape") { event.preventDefault(); closeCatalogSelects({ restoreFocus: true }); }
    else if (event.key === "Tab") closeCatalogSelects();
    else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      const target = event.key === "Home" ? 0 : event.key === "End" ? options.length - 1 : (index + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length;
      options[target]?.focus();
    }
  });
  renderCatalogSelect(id);
}

function renderModelRows() {
  return catalogState.items.map((model) => {
    const provider = model.providers?.find((item) => item.provider_id === catalogState.providerId);
    if (!provider) return "";
    return `<tr class="catalog-result-row">
      <td>
        <strong title="${escapeHtml(model.model_description || model.model_label)}">${escapeHtml(model.model_label)}</strong>
        <div class="models-sub"><code>${escapeHtml(provider.provider_model_id)}</code></div>
      </td>
      <td>${escapeHtml(formatTokenCount(provider.context_window_tokens))}</td>
      <td>${escapeHtml(formatTokenCount(provider.max_output_tokens))}</td>
      <td class="model-tag-cell">${renderBadges(provider.input_modalities, MODALITY_LABELS)}</td>
      <td class="model-tag-cell">${renderBadges(provider.tags, TAG_LABELS)}</td>

    </tr>`;
  }).join("");
}

function renderResults() {
  const head = element("catalogTableHead");
  const body = element("catalogTableBody");
  const empty = element("catalogEmptyState");
  const table = element("catalogModelsTable");
  if (!head || !body || !empty || !table) return;

  const databaseEmpty =
    catalogState.total === 0 &&
    !catalogState.query &&
    !catalogState.providerId &&
    !catalogState.modality;
  empty.classList.toggle("hidden", !databaseEmpty);
  table.classList.toggle("hidden", databaseEmpty);
  head.innerHTML = `<tr>
    <th>模型</th><th>上下文</th><th>最大输出</th><th>输入模态</th><th>能力</th>
  </tr>`;
  body.innerHTML = catalogState.items.length
    ? renderModelRows()
    : '<tr><td colspan="5" class="empty-cell">无匹配模型</td></tr>';
  window.lucide?.createIcons();
}

function renderPagination() {
  const page = Math.floor(catalogState.offset / PAGE_SIZE) + 1;
  const pages = Math.max(1, Math.ceil(catalogState.total / PAGE_SIZE));
  const previous = element("catalogPrevPageBtn");
  const next = element("catalogNextPageBtn");
  if (previous) previous.disabled = catalogState.loading || catalogState.offset === 0;
  if (next) {
    next.disabled =
      catalogState.loading || catalogState.offset + PAGE_SIZE >= catalogState.total;
  }
  if (element("catalogPageInfo")) {
    element("catalogPageInfo").textContent = catalogState.total
      ? `第 ${page} / ${pages} 页 · 共 ${catalogState.total} 项`
      : "无数据";
  }
}

async function loadCatalog() {
  const generation = ++loadGeneration;
  if (catalogState.loading) { pendingLoad = true; return; }
  pendingLoad = false;
  catalogState.loading = true;
  setFeedback();
  if (element("catalogStatusLine")) element("catalogStatusLine").textContent = "加载中…";
  if (element("catalogTableBody")) {
    element("catalogTableBody").innerHTML =
      '<tr><td colspan="5" class="empty-cell">加载中…</td></tr>';
  }
  renderPagination();
  try {
    let response = await request(`/api/v1/llm-models/catalog?${queryString()}`);
    if (generation !== loadGeneration) return;
    const providers = response.facets?.providers || [];
    if (providers.length && !providers.some((provider) => provider.id === catalogState.providerId)) {
      catalogState.providerId = providers[0].id;
      catalogState.offset = 0;
      response = await request(`/api/v1/llm-models/catalog?${queryString()}`);
    }
    if (generation !== loadGeneration) return;
    catalogState.items = Array.isArray(response.items) ? response.items : [];
    catalogState.total = Number(response.total || 0);
    catalogState.facets = response.facets || { labs: [], providers: [] };
    catalogState.refreshedAt = response.refreshed_at || null;
    renderFacets();
    renderResults();
    element("catalogStatusLine").textContent =
      `${catalogState.total} 个模型 · 最近更新 ${formatDateTime(catalogState.refreshedAt)}`;
  } catch (error) {
    if (generation !== loadGeneration) return;
    catalogState.items = [];
    catalogState.total = 0;
    setFeedback(`加载失败：${error.message}`, true);
    element("catalogStatusLine").textContent = "模型目录加载失败";
    renderResults();
  } finally {
    catalogState.loading = false;
    renderPagination();
    if (pendingLoad) await loadCatalog();
  }
}

async function refreshCatalog() {
  if (catalogState.loading) return;
  catalogState.loading = true;
  setFeedback("正在联网获取并校验 models.dev 完整目录，请稍候…");
  element("catalogRefreshBtn").disabled = true;
  try {
    const result = await request("/api/v1/llm-models/catalog/refresh", { method: "POST" });
    setFeedback(
      `目录更新完成：${result.providers} 个供应商、${result.models} 个模型。`,
    );
  } catch (error) {
    setFeedback(`目录更新失败，旧目录已保留：${error.message}`, true);
    catalogState.loading = false;
    renderPagination();
    element("catalogRefreshBtn").disabled = false;
    if (pendingLoad) await loadCatalog();
    return;
  }
  catalogState.loading = false;
  element("catalogRefreshBtn").disabled = false;
  catalogState.offset = 0;
  await loadCatalog();
}

async function importCatalog(file) {
  if (catalogState.loading || !file) return;
  catalogState.loading = true;
  setFeedback(`正在校验并导入 ${file.name}，请稍候…`);
  element("catalogRefreshBtn").disabled = true;
  element("catalogImportBtn").disabled = true;
  const body = new FormData();
  body.append("file", file, file.name);
  try {
    const response = await daFetch(serviceUrl("api/v1/llm-models/catalog/import"), {
      method: "POST",
      body,
    });
    const result = await parseJsonResponse(response);
    if (!response.ok) {
      const detail = result?.detail;
      throw new Error(
        typeof detail === "string"
          ? detail
          : detail
            ? JSON.stringify(detail)
            : `HTTP ${response.status}`,
      );
    }
    setFeedback(
      `目录导入完成：${result.providers} 个供应商、${result.models} 个模型。`,
    );
  } catch (error) {
    setFeedback(`目录导入失败，旧目录已保留：${error.message}`, true);
    catalogState.loading = false;
    renderPagination();
    element("catalogRefreshBtn").disabled = false;
    element("catalogImportBtn").disabled = false;
    if (pendingLoad) await loadCatalog();
    return;
  }
  catalogState.loading = false;
  element("catalogRefreshBtn").disabled = false;
  element("catalogImportBtn").disabled = false;
  catalogState.offset = 0;
  await loadCatalog();
}

function exportFileName(response) {
  const disposition = response.headers.get("content-disposition") || "";
  const match = disposition.match(/filename="?([^";]+)"?/i);
  return match?.[1] || "models-dev-catalog.json";
}

async function exportCatalog() {
  if (catalogState.loading) return;
  catalogState.loading = true;
  setFeedback("正在导出当前模型目录…");
  element("catalogImportBtn").disabled = true;
  element("catalogExportBtn").disabled = true;
  element("catalogRefreshBtn").disabled = true;
  try {
    const response = await daFetch(serviceUrl("api/v1/llm-models/catalog/export"));
    if (!response.ok) {
      const result = await parseJsonResponse(response);
      const detail = result?.detail;
      throw new Error(
        typeof detail === "string"
          ? detail
          : detail
            ? JSON.stringify(detail)
            : `HTTP ${response.status}`,
      );
    }
    const url = URL.createObjectURL(await response.blob());
    const anchor = document.createElement("a");
    try {
      anchor.href = url;
      anchor.download = exportFileName(response);
      document.body.appendChild(anchor);
      anchor.click();
    } finally {
      anchor.remove();
      URL.revokeObjectURL(url);
    }
    setFeedback("当前模型目录已导出，该JSON可直接重新导入。");
  } catch (error) {
    setFeedback(`目录导出失败：${error.message}`, true);
  } finally {
    catalogState.loading = false;
    element("catalogImportBtn").disabled = false;
    element("catalogExportBtn").disabled = false;
    element("catalogRefreshBtn").disabled = false;
    if (pendingLoad) await loadCatalog();
  }
}

function initCatalog() {
  bindCatalogSelect("catalogModalitySelect");
  document.addEventListener("click", (event) => {
    if (!event.target.closest("[data-catalog-select]")) closeCatalogSelects();
  });
  window.addEventListener("hashchange", () => closeCatalogSelects());
  element("catalogSearchInput")?.addEventListener("input", (event) => {
    catalogState.query = event.target.value || "";
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      catalogState.offset = 0;
      loadCatalog();
    }, 250);
  });
  element("catalogModalitySelect")?.addEventListener("change", (event) => {
    catalogState.modality = event.target.value || "";
    catalogState.offset = 0;
    loadCatalog();
  });
  element("catalogRefreshBtn")?.addEventListener("click", refreshCatalog);
  element("catalogExportBtn")?.addEventListener("click", exportCatalog);
  element("catalogImportBtn")?.addEventListener("click", () => {
    const input = element("catalogImportInput");
    if (!input) return;
    input.value = "";
    input.click();
  });
  element("catalogImportInput")?.addEventListener("change", async (event) => {
    const input = event.target;
    const file = input.files?.[0];
    await importCatalog(file);
    input.value = "";
  });
  document.querySelectorAll("[data-catalog-refresh]").forEach((button) => {
    button.addEventListener("click", refreshCatalog);
  });
  element("catalogPrevPageBtn")?.addEventListener("click", () => {
    catalogState.offset = Math.max(0, catalogState.offset - PAGE_SIZE);
    loadCatalog();
  });
  element("catalogNextPageBtn")?.addEventListener("click", () => {
    catalogState.offset += PAGE_SIZE;
    loadCatalog();
  });
}

window.modelCatalogAdmin = {
  refresh: loadCatalog,
  refreshFromModelsDev: refreshCatalog,
};

initCatalog();
