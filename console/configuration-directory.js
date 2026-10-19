// Configuration lists are shared; conversation state stays owned by chat.js.
export function createConfigurationDirectory({ request, scope = () => "", notify = () => {} }) {
  const entries = new Map();
  const listeners = new Set();
  const resource = (path) => path.startsWith("/api/v1/agents") ? "agents" : "models";
  function invalidate(resources = ["agents", "models"], broadcast = true) {
    for (const [key, entry] of entries) {
      if (resources.includes(entry.resource)) entries.delete(key);
    }
    for (const listener of listeners) listener(resources);
    if (broadcast) {
      try { notify(resources); } catch { /* A closed channel does not invalidate the save. */ }
    }
  }
  async function read(path, { force = false } = {}) {
    const key = `${scope()}|${path}`;
    let entry = entries.get(key);
    if (force && entry?.value) entries.delete(key);
    entry = entries.get(key);
    if (!entry) {
      entry = { resource: resource(path) };
      entries.set(key, entry);
      entry.promise = request(path).then(value => {
        if (!Array.isArray(value)) throw new Error("配置列表响应格式错误");
        entry.value = value;
        return value;
      }).catch(error => {
        if (entries.get(key) === entry) entries.delete(key);
        throw error;
      });
    }
    const value = await entry.promise;
    // A save may have overtaken this GET. Never publish its stale response.
    if (key !== `${scope()}|${path}`) throw new Error("请求上下文已改变");
    if (entries.get(key) !== entry) return read(path);
    return structuredClone(value);
  }
  function mutation(url, method) {
    if (!["POST", "PUT", "PATCH", "DELETE"].includes(String(method).toUpperCase())) return;
    const path = new URL(url, "http://console.local").pathname;
    if (/\/api\/v1\/agents(?:\/[^/]+)?\/?$/.test(path) && !/\/(prompt-estimate|copy-context)$/.test(path)) invalidate(["agents"]);
    else if (/\/api\/v1\/(?:llm-models|llm-providers)(?:\/[^/]+)?\/?$/.test(path)
      || /\/api\/v1\/llm-providers\/[^/]+\/disconnect\/?$/.test(path)) invalidate(["agents", "models"]);
    else if (/\/api\/v1\/agent-packages\/install\/?$/.test(path)) invalidate();
  }
  return { read, invalidate, mutation, subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); } };
}

if (typeof window !== "undefined") {
  const scope = () => JSON.stringify([
    window.defaultApiBaseUrl(), window.DeepAgentConsoleAPI?.loadContext()?.app || "",
    window.DeepAgentConsoleContext?.identity?.user || "",
  ]);
  let channel;
  const directory = createConfigurationDirectory({
    scope,
    request: async path => {
      const response = await window.daFetch(window.serviceUrl(path), { signal: AbortSignal.timeout(10000) });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    },
    notify: resources => channel?.postMessage({ scope: scope(), resources }),
  });
  const connect = () => {
    try { channel = new BroadcastChannel("deep-agent-console-directory"); } catch { return; }
    channel.onmessage = ({ data }) => {
      if (data?.scope === scope() && Array.isArray(data.resources)
        && data.resources.every(item => ["agents", "models"].includes(item))) directory.invalidate(data.resources, false);
    };
  };
  connect();
  window.addEventListener("pagehide", () => { channel?.close(); channel = null; });
  window.addEventListener("pageshow", () => { if (!channel) connect(); });
  window.DeepAgentConsoleDirectory = directory;
}
