"use strict";

const LLM_MODALITY_ORDER = ["text", "image", "audio", "video", "pdf"];

const LLM_MODALITY_LABELS = {
  text: "文本",
  image: "图像",
  audio: "音频",
  video: "视频",
  pdf: "PDF",
};

const PAGE_SIZE = 100;

const state = {
  items: [],
  total: 0,
  offset: 0,
  source: "auto",
  sourceLabel: "",
  providers: [],
  provider: "",
  modality: "",
  query: "",
  loading: false,
};

let searchTimer = null;

function $(id) {
  return document.getElementById(id);
}

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

async function parseJsonResponse(r) {
  const text = await r.text();
  const trimmed = text.trimStart();
  const looksLikeHtml =
    trimmed.startsWith("<") || trimmed.toLowerCase().startsWith("<!doctype");

  if (looksLikeHtml) {
    throw new Error(
      "后端返回了 HTML 而非 JSON。请确认当前控制台后端已发版或启动",
    );
  }

  try {
    return JSON.parse(text);
  } catch (err) {
    const preview = trimmed.slice(0, 120);
    throw new Error(preview || err.message || "响应不是有效 JSON");
  }
}

async function apiFetch(path) {
  const url = serviceUrl(path);
  const r = await daFetch(url);
  if (!r.ok) {
    let detail = `HTTP ${r.status}`;
    try {
      const body = await parseJsonResponse(r);
      if (body?.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch (parseErr) {
      if (parseErr?.message) detail = parseErr.message;
    }
    throw new Error(detail);
  }
  return parseJsonResponse(r);
}

function modalitySet(item) {
  return new Set((item?.input_modalities || []).map((m) => String(m).toLowerCase()));
}

function renderModCell(on, label) {
  if (on) {
    return `<td class="mod-col mod-yes" title="支持 ${escapeHtml(label)}">✓</td>`;
  }
  return `<td class="mod-col mod-no" title="不支持 ${escapeHtml(label)}">—</td>`;
}

function formatContext(value) {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n % 1_000_000 === 0 ? 0 : 1)}M`;
  if (n >= 1000) return `${Math.round(n / 1000)}K`;
  return String(n);
}

function renderProviderCell(item) {
  const label = item.provider_label || item.provider || "—";
  const sub = item.provider && item.provider_label ? item.provider : "";
  if (sub) {
    return `<td><strong>${escapeHtml(label)}</strong><div class="models-sub">${escapeHtml(sub)}</div></td>`;
  }
  return `<td><strong>${escapeHtml(label)}</strong></td>`;
}

function renderConfiguredCell(name) {
  if (!name) {
    return '<td class="models-configured mod-no">—</td>';
  }
  return `<td class="models-configured"><span class="badge">${escapeHtml(name)}</span></td>`;
}

function renderProviderOptions() {
  const sel = $("providerSelect");
  if (!sel) return;
  const current = state.provider;
  const options = ['<option value="">全部</option>'];
  for (const p of state.providers) {
    options.push(`<option value="${escapeHtml(p)}">${escapeHtml(p)}</option>`);
  }
  sel.innerHTML = options.join("");
  sel.value = current;
  if (sel.value !== current) {
    state.provider = "";
    sel.value = "";
  }
}

function renderPagination() {
  const prev = $("prevPageBtn");
  const next = $("nextPageBtn");
  const info = $("pageInfo");
  if (!prev || !next || !info) return;

  const page = Math.floor(state.offset / PAGE_SIZE) + 1;
  const totalPages = Math.max(1, Math.ceil(state.total / PAGE_SIZE));
  prev.disabled = state.loading || state.offset <= 0;
  next.disabled = state.loading || state.offset + PAGE_SIZE >= state.total;
  info.textContent = state.total
    ? `第 ${page} / ${totalPages} 页 · 本页 ${state.items.length} 条`
    : "无数据";
}

function renderTable() {
  const tbody = $("modelsTableBody");
  const status = $("statusLine");
  if (!tbody) return;

  if (!state.items.length) {
    tbody.innerHTML = '<tr><td colspan="9" class="empty-cell">无匹配结果</td></tr>';
  } else {
    tbody.innerHTML = state.items
      .map((item) => {
        const mods = modalitySet(item);
        const cells = LLM_MODALITY_ORDER.map((key) =>
          renderModCell(mods.has(key), LLM_MODALITY_LABELS[key] || key),
        ).join("");
        const modelTitle = item.key || `${item.provider}/${item.model_id}`;
        return `<tr title="${escapeHtml(modelTitle)}">
          ${renderProviderCell(item)}
          <td class="models-model-cell"><code>${escapeHtml(item.model_id)}</code></td>
          ${cells}
          <td class="models-context">${escapeHtml(formatContext(item.max_context))}</td>
          ${renderConfiguredCell(item.configured_preset)}
        </tr>`;
      })
      .join("");
  }

  if (status) {
    const src = state.sourceLabel || state.source;
    status.textContent = `共 ${state.total} 个模型 · 数据源 ${src}`;
  }

  renderPagination();
}

function catalogQueryString({ refresh = false } = {}) {
  const params = new URLSearchParams();
  params.set("source", state.source);
  params.set("offset", String(state.offset));
  params.set("limit", String(PAGE_SIZE));
  if (state.query.trim()) params.set("q", state.query.trim());
  if (state.provider) params.set("provider", state.provider);
  if (state.modality) params.set("modality", state.modality);
  if (refresh) params.set("refresh", "true");
  return params.toString();
}

async function loadCatalog({ refresh = false } = {}) {
  const tbody = $("modelsTableBody");
  const status = $("statusLine");
  state.loading = true;
  renderPagination();

  if (tbody) tbody.innerHTML = '<tr><td colspan="9" class="empty-cell">加载中…</td></tr>';
  if (status) status.textContent = "加载中…";

  try {
    const catalog = await apiFetch(`/api/v1/llm-models/catalog?${catalogQueryString({ refresh })}`);

    state.items = Array.isArray(catalog?.items) ? catalog.items : [];
    state.total = Number(catalog?.total) || 0;
    state.sourceLabel = catalog?.source_label || catalog?.source || "";
    state.providers = Array.isArray(catalog?.providers) ? catalog.providers : [];

    renderProviderOptions();
    renderTable();
  } catch (err) {
    if (tbody) {
      tbody.innerHTML = `<tr><td colspan="9" class="empty-cell">${escapeHtml(err.message)}</td></tr>`;
    }
    if (status) status.textContent = `加载失败：${err.message}`;
  } finally {
    state.loading = false;
    renderPagination();
  }
}

function resetAndLoad() {
  state.offset = 0;
  return loadCatalog();
}

function init() {
  $("sourceSelect")?.addEventListener("change", (e) => {
    state.source = e.target.value || "auto";
    resetAndLoad();
  });

  $("providerSelect")?.addEventListener("change", (e) => {
    state.provider = e.target.value || "";
    resetAndLoad();
  });

  $("modalitySelect")?.addEventListener("change", (e) => {
    state.modality = e.target.value || "";
    resetAndLoad();
  });

  $("searchInput")?.addEventListener("input", (e) => {
    state.query = e.target.value || "";
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => resetAndLoad(), 300);
  });

  $("refreshBtn")?.addEventListener("click", () => loadCatalog({ refresh: true }));

  $("prevPageBtn")?.addEventListener("click", () => {
    if (state.offset <= 0) return;
    state.offset = Math.max(0, state.offset - PAGE_SIZE);
    loadCatalog();
  });

  $("nextPageBtn")?.addEventListener("click", () => {
    if (state.offset + PAGE_SIZE >= state.total) return;
    state.offset += PAGE_SIZE;
    loadCatalog();
  });

  const sourceSel = $("sourceSelect");
  if (sourceSel) sourceSel.value = state.source;

  loadCatalog();
}

init();
