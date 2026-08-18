"use strict";

(function () {
  const CONTEXT_KEY = "da_console_request_context_v1";
  const LEGACY_BASE_URL_KEY = "da_console_base_url_v1";
  const DEFAULT_LANGUAGE = "zh-cn";
  const CONTEXT_HEADERS = new Set(["app"]);
  const CONTEXT_REQUEST_HEADERS = {
    app: "X-Deep-Agent-App",
    language: "X-Deep-Agent-Language",
  };
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

  function deriveSpaceFromApp(value) {
    const app = normalizeString(value);
    if (!app || !/^.+\d{3}$/.test(app)) {
      throw new Error("应用编码应由 Space 编码和三位数字组成，例如 mdxath003");
    }
    return app.slice(0, -3);
  }

  function persistContext(value) {
    if (Object.keys(value).length) {
      localStorage.setItem(CONTEXT_KEY, JSON.stringify(value));
    } else {
      localStorage.removeItem(CONTEXT_KEY);
    }
  }

  function loadContext() {
    try {
      const sanitized = sanitizeContext(JSON.parse(localStorage.getItem(CONTEXT_KEY) || "{}"));
      if (sanitized.app) deriveSpaceFromApp(sanitized.app);
      persistContext(sanitized);
      return sanitized;
    } catch {
      localStorage.removeItem(CONTEXT_KEY);
      return {};
    }
  }

  function saveContext(value) {
    const sanitized = sanitizeContext(value);
    if (sanitized.app) deriveSpaceFromApp(sanitized.app);
    persistContext(sanitized);
    return sanitized;
  }

  function clearContext() {
    localStorage.removeItem(CONTEXT_KEY);
  }

  function serviceRootUrl() {
    if (location.protocol === "file:") return new URL("http://localhost:8000/");
    return new URL("../", document.baseURI);
  }

  function defaultApiBaseUrl() {
    return serviceRootUrl().href.replace(/\/$/, "");
  }

  function serviceUrl(path = "") {
    return new URL(String(path).replace(/^\/+/, ""), serviceRootUrl()).href;
  }

  function consoleUrl(path = "") {
    return new URL(String(path), document.baseURI).href;
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
      const context = loadContext();
      headers[CONTEXT_REQUEST_HEADERS.language] = DEFAULT_LANGUAGE;
      if (context.app) {
        headers[CONTEXT_REQUEST_HEADERS.app] = context.app;
      }
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

  async function requestContext({ inherited = false, timeoutMs = 5000 } = {}) {
    const baseUrl = defaultApiBaseUrl();
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await daFetch(serviceUrl("api/v1/request-context"), {
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

  async function adminAccess({ timeoutMs = 7000 } = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await daFetch(serviceUrl("api/v1/admin/access"), {
        includeContext: false,
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
        const timeoutError = new Error("验证控制台管理权限超时，请稍后重试");
        timeoutError.code = "admin_access_timeout";
        timeoutError.status = 503;
        throw timeoutError;
      }
      throw error;
    } finally {
      clearTimeout(timer);
    }
  }

  try {
    localStorage.removeItem(LEGACY_BASE_URL_KEY);
  } catch {
    /* LocalStorage may be unavailable in restricted browser contexts. */
  }

  window.defaultApiBaseUrl = defaultApiBaseUrl;
  window.serviceUrl = serviceUrl;
  window.daFetch = daFetch;
  window.DeepAgentConsoleAPI = {
    CONTEXT_KEY,
    DEFAULT_LANGUAGE,
    adminAccess,
    buildHeaders,
    clearContext,
    defaultApiBaseUrl,
    consoleUrl,
    deriveSpaceFromApp,
    loadContext,
    requestContext,
    serviceUrl,
    sanitizeContext,
    saveContext,
  };
})();
