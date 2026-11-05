/** Administrator resources. No credential or anonymous download URL is copied into prompts. */
const CODES = ["initialization-guide", "development-skill"];
const TYPES = { "initialization-guide": "application/pdf", "development-skill": "application/zip" };

export function codingPrompt(baseUrl) {
  const base = new URL(baseUrl);
  base.username = ""; base.password = ""; base.search = ""; base.hash = "";
  return `请解压附带的 DeepAgent 开发 Skill ZIP，按 README.md 安装并使用。\nDeepAgent 服务：${base.href.replace(/\/$/, "")}\n安装后检查服务连接和权限；缺少授权时，请引导我在私有配置中补充。`;
}

export function resourceSummary(item) {
  const size = Number(item.size_bytes);
  const display = size >= 1024 * 1024 ? `${(size / 1024 / 1024).toFixed(1)} MB` : `${(size / 1024).toFixed(1)} KB`;
  return item.code === "initialization-guide" ? `PDF · ${item.page_count} 页 · ${display}`
    : `ZIP · ${item.document_count} 份资料 · ${display}`;
}

async function requestError(response) {
  if (response.status === 401) return "登录已失效，请重新登录后重试。";
  if (response.status === 403) return "需要管理员权限才能下载开发资料。";
  if (response.status === 404) return "资料暂不可用，请联系管理员核对发行文件。";
  return `资料请求失败（${response.status}），请稍后重试。`;
}

/** Lifecycle and downloads are independent of workspace streaming. */
export function createResourcesController({ request, url, view, saveBlob, timeoutMs = 30000 }) {
  let active = false, generation = 0, items = new Map();
  const pending = new Map();
  function cancelAll() { for (const c of pending.values()) c.abort(); pending.clear(); }
  async function run(key, task) {
    if (!active || pending.has(key)) return;
    const ticket = generation, controller = new AbortController();
    pending.set(key, controller);
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    const current = () => active && ticket === generation && pending.get(key) === controller;
    try { await task(controller.signal, current); }
    catch (error) {
      if (current()) view.error(key, error.name === "AbortError" ? "请求超时，请重试。" : error.message || "请求失败，请重试。");
    } finally {
      clearTimeout(timer);
      if (current()) { pending.delete(key); view.busy(key, false); }
    }
  }
  async function load() {
    items.clear();
    view.busy("catalog", true);
    return run("catalog", async (signal, current) => {
      const response = await request(url("api/v1/developer-resources"), { signal });
      if (!response.ok) throw new Error(await requestError(response));
      const payload = await response.json();
      if (!current()) return;
      const next = new Map();
      for (const code of CODES) {
        const item = payload?.items?.find(entry => entry.code === code);
        if (!item || item.media_type !== TYPES[code] || !Number.isSafeInteger(item.size_bytes) || item.size_bytes <= 0
          || !/^[a-f0-9]{64}$/.test(item.sha256) || typeof item.filename !== "string" || /[\\/]/.test(item.filename)) {
          throw new Error("资料目录不完整，请重试或联系管理员。");
        }
        next.set(code, item);
      }
      items = next; view.catalog([...items.values()]);
    });
  }
  return {
    setActive(value) {
      if (value === active) return;
      active = value; generation++; cancelAll();
      for (const code of CODES) view.busy(code, false);
      if (active) { items.clear(); void load(); }
    },
    reload() { if (active && !pending.has("catalog")) return load(); },
    async download(code) {
      if (!active || !items.has(code) || pending.has(code)) return;
      const item = items.get(code);
      view.busy(code, true);
      return run(code, async (signal, current) => {
        // Fixed resource codes, never follow an arbitrary catalog-provided destination.
        const response = await request(url(`api/v1/developer-resources/${code}/download`), { signal });
        if (!response.ok) throw new Error(await requestError(response));
        if (response.headers.get("Content-Type")?.split(";")[0].trim() !== TYPES[code]) throw new Error("下载内容格式不正确，请重新登录或重试。");
        const blob = await response.blob();
        if (!current()) return;
        if (blob.size !== item.size_bytes) throw new Error("资料已更新或下载不完整，请刷新资料后重试。");
        saveBlob(blob, item.filename);
      });
    },
  };
}

function downloadBlob(blob, filename) {
  const objectUrl = URL.createObjectURL(blob), link = document.createElement("a");
  link.href = objectUrl; link.download = filename;
  document.body.append(link);
  try { link.click(); }
  finally { link.remove(); setTimeout(() => URL.revokeObjectURL(objectUrl), 1000); }
}

let controller;
export function setDeveloperActive(active) { controller?.setActive(active); }
export function initDeveloper() {
  if (controller) return;
  const css = document.createElement("link"); css.rel = "stylesheet";
  css.href = new URL("./developer.css?v=20261106-developer-release", import.meta.url).href;
  document.head.append(css);
  const root = document.getElementById("page-developer");
  root.innerHTML = `
    <div class="developer-page">
      <header class="panel-toolbar console-page-header"><div>
        <h1>开发助手</h1><p class="panel-subtitle">初始化指南与业务 Agent 开发 Skill。</p>
      </div></header>
      <div class="developer-resources">
        <div class="developer-load-state" role="status"><span data-catalog-status>正在读取资料…</span><button type="button" class="btn small" data-retry hidden>重试</button></div>
        <section class="developer-resource-row" data-resource="initialization-guide">
          <div class="developer-file">
            <button type="button" class="developer-download" data-download="initialization-guide" aria-label="下载电子表格初始化" title="下载电子表格初始化" disabled>
            <span class="developer-file-copy"><strong><i data-lucide="file-text" class="developer-file-icon is-pdf" aria-hidden="true"></i>电子表格初始化</strong><span data-metadata>PDF</span></span><i data-lucide="download" class="developer-download-icon" aria-hidden="true"></i></button>
            <p class="developer-error" data-error role="status" hidden></p>
          </div>
          <div class="console-section developer-description"><h2 class="console-section-title">快速上手</h2><p>按电子表格助手示例完成模型与工具配置、预制 Agent 安装和首次验证。下载指南后按需查看即可。</p></div>
        </section>
        <section class="developer-resource-row" data-resource="development-skill">
          <div class="developer-file">
            <button type="button" class="developer-download" data-download="development-skill" aria-label="下载 DeepAgent 开发 Skill" title="下载 DeepAgent 开发 Skill" disabled>
            <span class="developer-file-copy"><strong><i data-lucide="package" class="developer-file-icon is-skill" aria-hidden="true"></i>DeepAgent 开发 Skill</strong><span data-metadata>DeepAgent-agents-dev</span></span><i data-lucide="download" class="developer-download-icon" aria-hidden="true"></i></button>
            <p class="developer-error" data-error role="status" hidden></p>
          </div>
          <div class="console-section developer-description"><h2 class="console-section-title">Coding 开发</h2><p>下载 ZIP 并附给 Coding Agent，再复制下面的提示。安装步骤与开发资料均在包内，按需读取。</p>
            <div class="developer-prompt"><div class="developer-prompt-heading"><span>给 Coding Agent 的提示</span><button type="button" class="btn ghost small" data-copy><i data-lucide="copy" aria-hidden="true"></i><span data-copy-label>复制</span></button></div><pre data-prompt></pre></div>
            <p class="developer-copy-status" data-copy-status role="status" hidden></p>
          </div>
        </section>
      </div>
    </div>`;
  const text = codingPrompt(serviceUrl(""));
  root.querySelector("[data-prompt]").textContent = text;
  const state = root.querySelector(".developer-load-state"), status = root.querySelector("[data-catalog-status]"), retry = root.querySelector("[data-retry]");
  const row = code => root.querySelector(`[data-resource="${code}"]`);
  controller = createResourcesController({ request: daFetch, url: serviceUrl, saveBlob: downloadBlob, view: {
    catalog(items) {
      state.hidden = true;
      for (const item of items) {
        const target = row(item.code), button = target.querySelector("[data-download]");
        if (item.code === "initialization-guide") target.querySelector("[data-metadata]").textContent = resourceSummary(item);
        button.title = `下载${item.name} · ${resourceSummary(item)} · v${item.version}`;
        button.disabled = false;
      }
    },
    busy(key, busy) {
      if (key === "catalog") {
        if (busy) { state.hidden = false; status.textContent = "正在读取资料…"; retry.hidden = true; CODES.forEach(code => row(code).querySelector("[data-download]").disabled = true); }
        return;
      }
      const button = row(key).querySelector("[data-download]");
      button.disabled = busy; button.setAttribute("aria-busy", String(busy)); button.classList.toggle("is-downloading", busy);
      if (busy) row(key).querySelector("[data-error]").hidden = true;
    },
    error(key, message) {
      if (key === "catalog") { state.hidden = false; status.textContent = message; retry.hidden = false; }
      else { const el = row(key).querySelector("[data-error]"); el.textContent = message; el.hidden = false; }
    },
  }});
  root.querySelectorAll("[data-download]").forEach(button => button.addEventListener("click", () => controller.download(button.dataset.download)));
  retry.addEventListener("click", () => controller.reload());
  root.querySelector("[data-copy]").addEventListener("click", async () => {
    const status = root.querySelector("[data-copy-status]");
    try { await navigator.clipboard.writeText(text); status.textContent = "提示词已复制。"; }
    catch { status.textContent = "无法访问剪贴板，请选中上方提示词复制。"; }
    status.hidden = false;
  });
  window.addEventListener("pagehide", () => controller.setActive(false));
  window.addEventListener("pageshow", () => controller.setActive(root.classList.contains("active")));
  window.lucide?.createIcons();
}
