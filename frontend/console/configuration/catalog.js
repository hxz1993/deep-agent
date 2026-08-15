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
  tool_call: "工具调用",
  structured_output: "结构化输出",
  temperature: "Temperature",
  open_weights: "开放权重",
  interleaved: "交错推理",
};

const catalogState = {
  view: "model",
  items: [],
  total: 0,
  offset: 0,
  query: "",
  labId: "",
  providerId: "",
  modality: "",
  facets: { labs: [], providers: [] },
  refreshedAt: null,
  loading: false,
};

let searchTimer = null;

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
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "尚未更新" : date.toLocaleString("zh-CN");
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
    view: catalogState.view,
    offset: String(catalogState.offset),
    limit: String(PAGE_SIZE),
  });
  if (catalogState.query.trim()) params.set("q", catalogState.query.trim());
  if (catalogState.labId) params.set("lab_id", catalogState.labId);
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
  const labs = Array.isArray(catalogState.facets?.labs) ? catalogState.facets.labs : [];
  const total = labs.reduce((sum, item) => sum + Number(item.count || 0), 0);
  const sidebar = element("catalogLabSidebar");
  element("catalogBrowser")?.classList.toggle(
    "catalog-provider-view",
    catalogState.view === "provider",
  );
  if (sidebar) {
    sidebar.innerHTML = [
      `<button type="button" class="catalog-lab-item ${catalogState.labId ? "" : "active"}" data-lab-id="">
        <span>全部 Lab</span><strong>${total}</strong>
      </button>`,
      ...labs.map(
        (lab) => `<button type="button" class="catalog-lab-item ${catalogState.labId === lab.id ? "active" : ""}"
          data-lab-id="${escapeHtml(lab.id)}">
          <span>${escapeHtml(lab.label)}</span><strong>${Number(lab.count || 0)}</strong>
        </button>`,
      ),
    ].join("");
    sidebar.querySelectorAll("[data-lab-id]").forEach((button) => {
      button.addEventListener("click", () => {
        catalogState.labId = button.dataset.labId || "";
        catalogState.offset = 0;
        loadCatalog();
      });
    });
    sidebar.classList.toggle("hidden", catalogState.view !== "model");
  }

  const providerSelect = element("catalogProviderSelect");
  if (providerSelect) {
    const providers = Array.isArray(catalogState.facets?.providers)
      ? catalogState.facets.providers
      : [];
    providerSelect.innerHTML = [
      '<option value="">全部</option>',
      ...providers.map(
        (provider) =>
          `<option value="${escapeHtml(provider.id)}">${escapeHtml(provider.label)}（${Number(provider.count || 0)}）</option>`,
      ),
    ].join("");
    providerSelect.value = catalogState.providerId;
  }
}

function modelDetailRow(model) {
  const providers = Array.isArray(model.providers) ? model.providers : [];
  return `<tr class="catalog-detail-row hidden" data-detail-for="${escapeHtml(model.canonical_model_id)}">
    <td colspan="6">
      <div class="catalog-provider-options">
        <div class="catalog-model-description">
          ${escapeHtml(model.model_description || "暂无模型说明")}
        </div>
        ${providers
          .map(
            (provider, index) => `<div class="catalog-provider-option">
              <div>
                <strong>${escapeHtml(provider.provider_label)}</strong>
                <code>${escapeHtml(provider.provider_model_id)}</code>
                ${
                  provider.configured_preset
                    ? `<span class="badge on">已配置：${escapeHtml(provider.configured_preset)}</span>`
                    : ""
                }
              </div>
              <div class="catalog-provider-meta">
                <span>${escapeHtml(formatTokenCount(provider.context_window_tokens))} 上下文</span>
                <span>${escapeHtml(formatTokenCount(provider.max_output_tokens))} 最大输出</span>
                <span>${escapeHtml(provider.adapter)}</span>
              </div>
              <button type="button" class="btn btn-sm ${index === 0 ? "primary" : ""}"
                data-use-model="${escapeHtml(model.canonical_model_id)}"
                data-provider-id="${escapeHtml(provider.provider_id)}">使用此模型</button>
            </div>`,
          )
          .join("")}
      </div>
    </td>
  </tr>`;
}

function renderModelRows() {
  return catalogState.items
    .map(
      (model) => `<tr class="catalog-result-row">
        <td>
          <strong>${escapeHtml(model.model_label)}</strong>
          <div class="models-sub"><code>${escapeHtml(model.canonical_model_id)}</code></div>
        </td>
        <td>${escapeHtml(formatTokenCount(model.context_window_tokens))}</td>
        <td>${escapeHtml(formatTokenCount(model.max_output_tokens))}</td>
        <td class="model-tag-cell">${renderBadges(model.input_modalities, MODALITY_LABELS)}</td>
        <td class="model-tag-cell">${renderBadges(model.tags, TAG_LABELS)}</td>
        <td><button type="button" class="btn btn-sm" data-toggle-model="${escapeHtml(model.canonical_model_id)}">
          ${Number(model.provider_count || 0)} 个可用渠道
        </button></td>
      </tr>
      ${modelDetailRow(model)}`,
    )
    .join("");
}

function renderProviderRows() {
  return catalogState.items
    .map(
      (provider) => `<tr>
        <td>
          <strong>${escapeHtml(provider.provider_label)}</strong>
          <div class="models-sub"><code>${escapeHtml(provider.provider_id)}</code></div>
        </td>
        <td>${Number(provider.model_count || 0)}</td>
        <td><code>${escapeHtml(provider.adapter || "—")}</code></td>
        <td>${escapeHtml(provider.provider_api_url || "—")}</td>
        <td class="model-tag-cell">${(provider.models || [])
          .slice(0, 5)
          .map((model) => `<span class="badge">${escapeHtml(model.model_label)}</span>`)
          .join(" ")}${
            provider.models?.length > 5
              ? ` <span class="catalog-muted">等 ${provider.models.length} 个</span>`
              : ""
          }</td>
      </tr>`,
    )
    .join("");
}

function bindModelActions() {
  element("catalogTableBody")?.querySelectorAll("[data-toggle-model]").forEach((button) => {
    button.addEventListener("click", () => {
      const selector = `[data-detail-for="${CSS.escape(button.dataset.toggleModel)}"]`;
      element("catalogTableBody")?.querySelector(selector)?.classList.toggle("hidden");
    });
  });
  element("catalogTableBody")?.querySelectorAll("[data-use-model]").forEach((button) => {
    button.addEventListener("click", () => {
      const model = catalogState.items.find(
        (item) => item.canonical_model_id === button.dataset.useModel,
      );
      const provider = model?.providers?.find(
        (item) => item.provider_id === button.dataset.providerId,
      );
      if (!model || !provider) return;
      document.querySelector('[data-tab="llm-models"]')?.click();
      window.llmModelsAdmin?.openCreateFromCatalog(model, provider);
    });
  });
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
    !catalogState.labId &&
    !catalogState.providerId &&
    !catalogState.modality;
  empty.classList.toggle("hidden", !databaseEmpty);
  table.classList.toggle("hidden", databaseEmpty);
  table.classList.toggle("catalog-provider-table", catalogState.view === "provider");

  if (catalogState.view === "model") {
    head.innerHTML = `<tr>
      <th>模型</th><th>上下文</th><th>最大输出</th><th>输入模态</th><th>能力标签</th><th>接入渠道</th>
    </tr>`;
    body.innerHTML = catalogState.items.length
      ? renderModelRows()
      : '<tr><td colspan="6" class="empty-cell">无匹配模型</td></tr>';
    bindModelActions();
  } else {
    head.innerHTML = `<tr>
      <th>接入渠道</th><th>可用模型</th><th>运行协议</th><th>API 地址</th><th>模型示例</th>
    </tr>`;
    body.innerHTML = catalogState.items.length
      ? renderProviderRows()
      : '<tr><td colspan="5" class="empty-cell">无匹配接入渠道</td></tr>';
  }
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
  if (catalogState.loading) return;
  catalogState.loading = true;
  setFeedback();
  if (element("catalogStatusLine")) element("catalogStatusLine").textContent = "加载中…";
  if (element("catalogTableBody")) {
    element("catalogTableBody").innerHTML =
      '<tr><td colspan="6" class="empty-cell">加载中…</td></tr>';
  }
  renderPagination();
  try {
    const response = await request(`/api/v1/llm-models/catalog?${queryString()}`);
    catalogState.items = Array.isArray(response.items) ? response.items : [];
    catalogState.total = Number(response.total || 0);
    catalogState.facets = response.facets || { labs: [], providers: [] };
    catalogState.refreshedAt = response.refreshed_at || null;
    renderFacets();
    renderResults();
    element("catalogStatusLine").textContent =
      `当前视图 ${catalogState.total} 项 · 最近更新 ${formatDateTime(catalogState.refreshedAt)}`;
  } catch (error) {
    catalogState.items = [];
    catalogState.total = 0;
    setFeedback(`加载失败：${error.message}`, true);
    element("catalogStatusLine").textContent = "模型目录加载失败";
    renderResults();
  } finally {
    catalogState.loading = false;
    renderPagination();
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
      `目录更新完成：${result.labs} 个 Lab、${result.models} 个模型、${result.providers} 个接入渠道、${result.rows} 条组合。`,
    );
  } catch (error) {
    setFeedback(`目录更新失败，旧目录已保留：${error.message}`, true);
    catalogState.loading = false;
    renderPagination();
    element("catalogRefreshBtn").disabled = false;
    return;
  }
  catalogState.loading = false;
  element("catalogRefreshBtn").disabled = false;
  catalogState.offset = 0;
  await loadCatalog();
}

function initCatalog() {
  document.querySelectorAll("[data-catalog-view]").forEach((button) => {
    button.addEventListener("click", () => {
      catalogState.view = button.dataset.catalogView;
      catalogState.offset = 0;
      document.querySelectorAll("[data-catalog-view]").forEach((item) => {
        item.classList.toggle("active", item === button);
      });
      loadCatalog();
    });
  });
  element("catalogSearchInput")?.addEventListener("input", (event) => {
    catalogState.query = event.target.value || "";
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      catalogState.offset = 0;
      loadCatalog();
    }, 250);
  });
  element("catalogProviderSelect")?.addEventListener("change", (event) => {
    catalogState.providerId = event.target.value || "";
    catalogState.offset = 0;
    loadCatalog();
  });
  element("catalogModalitySelect")?.addEventListener("change", (event) => {
    catalogState.modality = event.target.value || "";
    catalogState.offset = 0;
    loadCatalog();
  });
  element("catalogReloadBtn")?.addEventListener("click", loadCatalog);
  element("catalogRefreshBtn")?.addEventListener("click", refreshCatalog);
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
  loadCatalog();
}

window.modelCatalogAdmin = {
  refresh: loadCatalog,
  refreshFromModelsDev: refreshCatalog,
};

initCatalog();
