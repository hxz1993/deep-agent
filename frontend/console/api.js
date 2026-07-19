"use strict";

(function () {
  const CONTEXT_KEY = "da_console_request_context_v1";
  const BASE_URL_KEY = "da_console_base_url_v1";
  const CONTEXT_HEADERS = new Set(["app", "space", "language"]);
  const SAFE_REQUEST_HEADERS = new Set(["accept", "content-type"]);
  const FORBIDDEN_HEADERS = new Set([
    "authorization",
    "cookie",
    "token",
    "user",
    "user-id",
    "user_id",
    "x-user-token",
    "authority",
    "request-id",
    "x-request-id",
  ]);

  function normalizeString(value) {
    const normalized = String(value ?? "").trim();
    return normalized || null;
  }

  function sanitizeContext(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return {};
    const result = {};
    for (const name of CONTEXT_HEADERS) {
      const normalized = normalizeString(value[name]);
      if (normalized) result[name] = normalized;
    }
    return result;
  }

  function loadContext() {
    try {
      return sanitizeContext(JSON.parse(localStorage.getItem(CONTEXT_KEY) || "{}"));
    } catch {
      return {};
    }
  }

  function saveContext(value) {
    const sanitized = sanitizeContext(value);
    if (Object.keys(sanitized).length) {
      localStorage.setItem(CONTEXT_KEY, JSON.stringify(sanitized));
    } else {
      localStorage.removeItem(CONTEXT_KEY);
    }
    return sanitized;
  }

  function clearContext() {
    localStorage.removeItem(CONTEXT_KEY);
  }

  function inferBaseUrl() {
    if (location.protocol === "file:") return "http://localhost:8000";
    const root = window.__DEEP_AGENT_APP_ROOT__ || "/";
    return `${location.origin}${root.replace(/\/$/, "")}`;
  }

  function defaultApiBaseUrl() {
    return (localStorage.getItem(BASE_URL_KEY) || inferBaseUrl()).replace(/\/$/, "");
  }

  function inheritedApiBaseUrl() {
    return inferBaseUrl().replace(/\/$/, "");
  }

  function clearBaseUrl() {
    localStorage.removeItem(BASE_URL_KEY);
    return inheritedApiBaseUrl();
  }

  function canFallbackToInheritedBaseUrl() {
    return defaultApiBaseUrl() !== inheritedApiBaseUrl();
  }

  function saveBaseUrl(value) {
    const normalized = normalizeString(value) || inferBaseUrl();
    localStorage.setItem(BASE_URL_KEY, normalized.replace(/\/$/, ""));
    return defaultApiBaseUrl();
  }

  function safeExtraHeaders(headers) {
    const output = {};
    for (const [rawName, rawValue] of Object.entries(headers || {})) {
      const name = String(rawName).trim();
      const lower = name.toLowerCase();
      if (!name || FORBIDDEN_HEADERS.has(lower) || !SAFE_REQUEST_HEADERS.has(lower)) continue;
      if (rawValue == null || String(rawValue).trim() === "") continue;
      output[name] = String(rawValue);
    }
    return output;
  }

  function buildHeaders(extra = {}, { includeContext = true } = {}) {
    const headers = safeExtraHeaders(extra);
    if (includeContext) {
      Object.assign(headers, loadContext());
    }
    return headers;
  }

  async function daFetch(url, options = {}) {
    const { headers: extra = {}, includeContext = true, ...rest } = options;
    const response = await fetch(url, {
      ...rest,
      credentials: "same-origin",
      headers: buildHeaders(extra, { includeContext }),
    });
    if ([401, 403, 503].includes(response.status)) {
      window.dispatchEvent(
        new CustomEvent("deep-agent-console-api-status", {
          detail: { status: response.status, url: String(url) },
        }),
      );
    }
    return response;
  }

  async function parseJson(response) {
    const text = await response.text();
    if (!text.trim()) return null;
    try {
      return JSON.parse(text);
    } catch {
      throw new Error(`后端返回了无法解析的内容（HTTP ${response.status}）`);
    }
  }

  async function requestContext({ inherited = false, baseUrl = defaultApiBaseUrl(), timeoutMs = 5000 } = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await daFetch(`${baseUrl.replace(/\/$/, "")}/api/v1/request-context`, {
        includeContext: !inherited,
        signal: controller.signal,
      });
      const body = await parseJson(response);
      if (!response.ok) {
        const detail = body?.detail || body?.message || `HTTP ${response.status}`;
        const error = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
        error.status = response.status;
        throw error;
      }
      return body;
    } catch (error) {
      if (controller.signal.aborted) {
        const timeoutError = new Error(`连接后端超时（${timeoutMs}ms）：${baseUrl}`);
        timeoutError.code = "request_timeout";
        throw timeoutError;
      }
      throw error;
    } finally {
      clearTimeout(timer);
    }
  }

  window.defaultApiBaseUrl = defaultApiBaseUrl;
  window.daFetch = daFetch;
  window.DeepAgentConsoleAPI = {
    CONTEXT_KEY,
    BASE_URL_KEY,
    buildHeaders,
    canFallbackToInheritedBaseUrl,
    clearBaseUrl,
    clearContext,
    defaultApiBaseUrl,
    inheritedApiBaseUrl,
    loadContext,
    requestContext,
    sanitizeContext,
    saveBaseUrl,
    saveContext,
  };
})();
