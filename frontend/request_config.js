"use strict";

const REQUEST_HEADERS_KEY = "da_request_headers";
const COOKIE_NAMES_KEY = "da_request_cookie_names";

function loadRequestHeaders() {
  try {
    const raw = localStorage.getItem(REQUEST_HEADERS_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    return parsed;
  } catch {
    return {};
  }
}

function saveRequestHeaders(headers) {
  localStorage.setItem(REQUEST_HEADERS_KEY, JSON.stringify(headers));
  syncConfiguredCookies();
}

function clearRequestHeaders() {
  localStorage.removeItem(REQUEST_HEADERS_KEY);
  clearConfiguredCookies();
}

function defaultApiBaseUrl() {
  return (
    localStorage.getItem("da_base_url") ||
    localStorage.getItem("deep-agent-base-url") ||
    location.origin
  );
}

function resolveOrigin(url) {
  try {
    return new URL(url, location.href).origin;
  } catch {
    return null;
  }
}

function parseCookiePairs(cookieValue) {
  return String(cookieValue)
    .split(";")
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => {
      const eq = part.indexOf("=");
      if (eq < 0) return { name: part, value: "" };
      return { name: part.slice(0, eq).trim(), value: part.slice(eq + 1).trim() };
    });
}

function configuredCookieValue(headers) {
  for (const [key, value] of Object.entries(headers || {})) {
    if (key.toLowerCase() === "cookie") return value;
  }
  return null;
}

function applyCookieString(cookieValue) {
  const names = [];
  for (const { name, value } of parseCookiePairs(cookieValue)) {
    if (!name) continue;
    document.cookie = `${name}=${value}; path=/; SameSite=Lax`;
    names.push(name);
  }
  localStorage.setItem(COOKIE_NAMES_KEY, JSON.stringify(names));
}

function clearConfiguredCookies() {
  try {
    const names = JSON.parse(localStorage.getItem(COOKIE_NAMES_KEY) || "[]");
    for (const name of names) {
      document.cookie = `${name}=; path=/; max-age=0`;
    }
  } catch {
    /* ignore */
  }
  localStorage.removeItem(COOKIE_NAMES_KEY);
}

/** Apply Cookie config via document.cookie when API is same-origin (browser blocks Cookie fetch header). */
function syncConfiguredCookies(apiBaseUrl = defaultApiBaseUrl()) {
  const cookie = configuredCookieValue(loadRequestHeaders());
  if (!cookie) {
    clearConfiguredCookies();
    return false;
  }
  const apiOrigin = resolveOrigin(apiBaseUrl);
  if (!apiOrigin || apiOrigin !== location.origin) return false;
  applyCookieString(cookie);
  return true;
}

function stripCookieHeader(headers) {
  const out = { ...headers };
  for (const key of Object.keys(out)) {
    if (key.toLowerCase() === "cookie") delete out[key];
  }
  return out;
}

/** ponytail: per-request headers override configured ones; Cookie uses document.cookie on same-origin only. */
function mergeFetchHeaders(extra = {}) {
  return stripCookieHeader({ ...loadRequestHeaders(), ...extra });
}

function daFetch(url, options = {}) {
  const { headers: extra = {}, ...rest } = options;
  const merged = { ...loadRequestHeaders(), ...extra };
  const cookie = configuredCookieValue(merged);
  if (cookie && resolveOrigin(url) === location.origin) {
    applyCookieString(cookie);
  }
  return fetch(url, { ...rest, headers: stripCookieHeader(merged) });
}
