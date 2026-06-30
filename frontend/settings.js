"use strict";

const headerRowsEl = document.getElementById("headerRows");
const rowsView = document.getElementById("rowsView");
const jsonView = document.getElementById("jsonView");
const headersInput = document.getElementById("headersInput");
const modeToggle = document.getElementById("modeToggle");
const addRowBtn = document.getElementById("addRowBtn");
const saveBtn = document.getElementById("saveBtn");
const clearBtn = document.getElementById("clearBtn");
const statusEl = document.getElementById("status");

let mode = "rows";

function showStatus(message, isError = false) {
  statusEl.textContent = message;
  statusEl.classList.remove("hidden", "ok", "err");
  statusEl.classList.add(isError ? "err" : "ok");
}

function escapeAttr(text) {
  return String(text ?? "")
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;");
}

function validateHeadersObject(parsed) {
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("必须是 JSON 对象");
  }
  for (const [key, value] of Object.entries(parsed)) {
    if (typeof value !== "string") {
      throw new Error(`「${key}」的值必须是字符串`);
    }
  }
  return parsed;
}

function parseHeadersJson(text) {
  const trimmed = (text || "").trim();
  if (!trimmed) return {};
  return validateHeadersObject(JSON.parse(trimmed));
}

function rowsFromHeaders(headers) {
  const entries = Object.entries(headers || {});
  if (!entries.length) return [{ key: "", value: "" }];
  return entries.map(([key, value]) => ({ key, value }));
}

function readRowsFromDom() {
  return Array.from(headerRowsEl.querySelectorAll(".header-row")).map((row) => ({
    key: row.querySelector(".header-key")?.value ?? "",
    value: row.querySelector(".header-value")?.value ?? "",
  }));
}

function rowsToHeaders(rows) {
  const headers = {};
  const seen = new Set();
  for (const { key, value } of rows) {
    const name = key.trim();
    if (!name) continue;
    if (seen.has(name)) throw new Error(`重复的请求头名称：${name}`);
    seen.add(name);
    headers[name] = value;
  }
  return headers;
}

function createRowElement(key = "", value = "") {
  const row = document.createElement("div");
  row.className = "header-row";
  row.innerHTML = `
    <input type="text" class="header-key" placeholder="Authorization" value="${escapeAttr(key)}" />
    <input type="text" class="header-value" placeholder="Bearer your-token" value="${escapeAttr(value)}" />
    <button type="button" class="row-remove" title="删除" aria-label="删除">×</button>
  `;
  row.querySelector(".row-remove").addEventListener("click", () => {
    row.remove();
    if (!headerRowsEl.children.length) addEmptyRow();
  });
  return row;
}

function renderRows(headers) {
  headerRowsEl.replaceChildren();
  for (const { key, value } of rowsFromHeaders(headers)) {
    headerRowsEl.appendChild(createRowElement(key, value));
  }
}

function addEmptyRow() {
  headerRowsEl.appendChild(createRowElement());
}

function setMode(nextMode) {
  mode = nextMode;
  const isRows = mode === "rows";
  rowsView.classList.toggle("hidden", !isRows);
  jsonView.classList.toggle("hidden", isRows);
  modeToggle.textContent = isRows ? "JSON 模式" : "字段模式";
}

function collectHeaders() {
  return mode === "rows" ? rowsToHeaders(readRowsFromDom()) : parseHeadersJson(headersInput.value);
}

function syncViewsFromHeaders(headers) {
  renderRows(headers);
  headersInput.value = Object.keys(headers).length ? JSON.stringify(headers, null, 2) : "";
}

function loadForm() {
  syncViewsFromHeaders(loadRequestHeaders());
  syncConfiguredCookies();
  setMode("rows");
}

modeToggle.addEventListener("click", () => {
  try {
    if (mode === "rows") {
      const headers = rowsToHeaders(readRowsFromDom());
      headersInput.value = Object.keys(headers).length ? JSON.stringify(headers, null, 2) : "";
      setMode("json");
    } else {
      const headers = parseHeadersJson(headersInput.value);
      renderRows(headers);
      setMode("rows");
    }
    statusEl.classList.add("hidden");
  } catch (e) {
    showStatus(e.message || "切换失败", true);
  }
});

addRowBtn.addEventListener("click", addEmptyRow);

function cookieSaveMessage(headers) {
  const cookie = configuredCookieValue(headers);
  if (!cookie) return Object.keys(headers).length ? "已保存" : "已清空配置";
  const apiOrigin = resolveOrigin(defaultApiBaseUrl());
  if (apiOrigin && apiOrigin !== location.origin) {
    return "已保存，但 Cookie 仅在同源后端时生效（当前配置的后端地址跨域）";
  }
  return "已保存，Cookie 已写入浏览器，请求将自动携带";
}

saveBtn.addEventListener("click", () => {
  try {
    const headers = collectHeaders();
    saveRequestHeaders(headers);
    syncViewsFromHeaders(headers);
    showStatus(cookieSaveMessage(headers));
  } catch (e) {
    showStatus(e.message || "保存失败", true);
  }
});

clearBtn.addEventListener("click", () => {
  clearRequestHeaders();
  syncViewsFromHeaders({});
  showStatus("已清空配置");
});

loadForm();
