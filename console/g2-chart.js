(function initDeepAgentG2(global) {
  "use strict";

  const CHANNEL = "deepagent-g2";
  const LIMITS = Object.freeze({ bytes: 1024 * 1024, depth: 32, nodes: 50000, rows: 10000, marks: 1000 });
  const PROTOTYPE_KEYS = new Set(["__proto__", "constructor", "prototype"]);
  const DOM_KEYS = new Set(["html", "innerhtml", "outerhtml", "container", "canvas", "renderer", "render", "formatter", "callback", "mount"]);

  function parseSpec(source) {
    const text = String(source ?? "");
    if (text.length > LIMITS.bytes || new TextEncoder().encode(text).length > LIMITS.bytes) throw new Error("图表配置超过 1 MB，请精简后重试。");
    let parsed;
    try { parsed = JSON.parse(text); } catch (_) { throw new Error("图表配置不是有效的 JSON，请展开配置检查。"); }
    let spec = parsed;
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed) && parsed.type === "g2") {
      if (!parsed.config || typeof parsed.config !== "object" || Array.isArray(parsed.config)) throw new Error("G2 图表的 config 必须是对象。");
      if (Object.keys(parsed).some(key => key !== "type" && key !== "config")) throw new Error("G2 图表外层仅支持 type 和 config。");
      spec = parsed.config;
    }
    if (!spec || typeof spec !== "object" || Array.isArray(spec)) throw new Error("图表配置必须是对象。");
    let nodes = 0, rows = 0, marks = 0;
    function visit(value, depth, data = false, mark = false) {
      if (++nodes > LIMITS.nodes) throw new Error("图表配置节点过多，请精简后重试。");
      if (depth > LIMITS.depth) throw new Error("图表配置嵌套过深，请精简后重试。");
      if (typeof value === "number" && !Number.isFinite(value)) throw new Error("图表数值必须是有限数字。");
      if (!value || typeof value !== "object") return;
      if (Array.isArray(value)) { for (const item of value) visit(item, depth + 1, data, mark); return; }
      if (mark && ++marks > LIMITS.marks) throw new Error("图表图层超过 1,000 个，请精简后重试。");
      for (const [key, item] of Object.entries(value)) {
        if (PROTOTYPE_KEYS.has(key)) throw new Error("图表配置包含不支持的属性。");
        if (!data && (DOM_KEYS.has(key.toLowerCase()) || /^on[A-Z]/.test(key))) throw new Error("图表仅支持声明式配置，不支持 HTML、回调或自定义容器。");
        if (!data && key === "data") {
          if (!Array.isArray(item)) throw new Error("图表数据必须是内联数组，不支持远程数据源。");
          rows += item.length;
          if (rows > LIMITS.rows) throw new Error("图表数据超过 10,000 行，请汇总后重试。");
          visit(item, depth + 1, true);
        } else if (!data && key === "children") {
          if (!Array.isArray(item)) throw new Error("图表 children 必须是数组。");
          visit(item, depth + 1, false, true);
        } else {
          visit(item, depth + 1, data);
        }
      }
    }
    // Inspect the envelope too; ordinary data field names and text stay untouched.
    for (const key of Object.keys(parsed)) if (PROTOTYPE_KEYS.has(key)) throw new Error("图表配置包含不支持的属性。");
    visit(spec, 0, false, true);
    return spec;
  }

  function randomToken() {
    const bytes = new Uint8Array(18);
    if (!global.crypto?.getRandomValues) throw new Error("当前浏览器不支持安全的图表预览。");
    global.crypto.getRandomValues(bytes);
    return [...bytes].map(value => value.toString(16).padStart(2, "0")).join("");
  }

  function escapeAttribute(value) {
    return String(value).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function frameDocument({ nonce, token, g2Url, frameUrl }) {
    const policy = `default-src 'none'; script-src 'nonce-${nonce}'; style-src 'unsafe-inline'; connect-src 'none'; img-src data: blob:; font-src 'none'; frame-src 'none'; worker-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'`;
    // Only trusted URLs and random identifiers enter srcdoc. Message JSON never does.
    return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${escapeAttribute(policy)}"><meta name="referrer" content="no-referrer"><style>html,body{margin:0;width:100%;height:300px;overflow:hidden;background:#fff;color:#263c4e;font:12px -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei","PingFang SC",Arial,sans-serif}#chart{width:100%;height:300px}.g2-safe-tooltip{max-width:300px;padding:8px 10px;background:#fff;color:#263c4e;font:12px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif;overflow-wrap:anywhere}.g2-safe-tooltip-title{font-weight:600;margin-bottom:3px}.g2-safe-tooltip-row{display:flex;justify-content:space-between;gap:12px}</style><script nonce="${nonce}" src="${escapeAttribute(g2Url)}" defer></script><script nonce="${nonce}" data-token="${token}" src="${escapeAttribute(frameUrl)}" defer></script></head><body><div id="chart" aria-label="G2 图表"></div></body></html>`;
  }

  if (global.HTMLElement && global.customElements && !global.customElements.get("deepagent-g2-chart")) {
    class G2ChartElement extends global.HTMLElement {
      constructor() {
        super();
        this._generation = 0;
        this._frame = null;
        this._observer = null;
        this._viewport = null;
        this._timer = null;
        this._listener = null;
        this._started = false;
        this._view = "chart";
      }

      connectedCallback() {
        const generation = ++this._generation;
        // innerHTML-created elements connect before their child code is parsed.
        Promise.resolve().then(() => {
          if (!this.isConnected || generation !== this._generation) return;
          for (const button of this.querySelectorAll("[data-g2-view]")) {
            button.disabled = this.dataset.g2State === "streaming";
            button.onclick = () => this._setView(button.dataset.g2View);
          }
          if (this.dataset.g2State === "streaming") { this._status("图表生成中…"); this._setView("chart", false); return; }
          if (this._started) return;
          this._status("图表加载中…");
          this._setView(this._view, false);
          if (global.IntersectionObserver) {
            this._observer = new global.IntersectionObserver(entries => {
              if (!this.isConnected || generation !== this._generation || !entries.some(entry => entry.target === this && entry.isIntersecting)) return;
              this._observer?.disconnect(); this._observer = null;
              this._mount(generation);
            }, { rootMargin: "120px" });
            this._observer.observe(this);
          } else this._mount(generation);
        });
      }

      _setView(view, mount = true) {
        this._view = view === "config" ? "config" : "chart";
        this.dataset.g2View = this._view;
        const isChart = this._view === "chart";
        const config = this.querySelector(".g2-chart-config");
        if (config) config.hidden = isChart;
        if (this._viewport) this._viewport.hidden = !isChart;
        for (const button of this.querySelectorAll("[data-g2-view]")) {
          const active = button.dataset.g2View === this._view;
          button.classList.toggle("active", active);
          button.setAttribute("aria-pressed", String(active));
        }
        if (isChart && mount && this.dataset.g2State !== "streaming") this._mount(this._generation);
      }

      _status(message, error = false) {
        if (!this._viewport) {
          this._viewport = this.ownerDocument.createElement("div");
          this._viewport.className = "g2-chart-viewport";
          this.insertBefore(this._viewport, this.querySelector(".g2-chart-config") || this.firstChild);
        }
        let status = this._viewport.querySelector(".g2-chart-status");
        if (!status) {
          status = this.ownerDocument.createElement("div");
          status.className = "g2-chart-status";
          this._viewport.appendChild(status);
        }
        status.setAttribute("role", error ? "alert" : "status");
        status.classList.toggle("is-error", error);
        status.textContent = message;
        status.hidden = false;
        return status;
      }

      _mount(generation) {
        if (this._started || this._view !== "chart" || !this.isConnected || generation !== this._generation) return;
        this._started = true;
        try {
          const spec = parseSpec(this.querySelector("code")?.textContent || "");
          const token = randomToken(), nonce = randomToken();
          const frame = this.ownerDocument.createElement("iframe");
          frame.className = "g2-chart-frame is-loading";
          frame.title = "G2 图表";
          frame.setAttribute("sandbox", "allow-scripts");
          frame.setAttribute("referrerpolicy", "no-referrer");
          const g2Url = new URL("../vendor/g2-5.4.8.min.js", this.ownerDocument.baseURI).href;
          const frameUrl = new URL("./g2-frame.js?v=20261105-v01511", this.ownerDocument.baseURI).href;
          this._frame = frame;
          let sent = false;
          this._listener = event => {
            const data = event.data;
            if (!this.isConnected || generation !== this._generation || this._frame !== frame || event.source !== frame.contentWindow
              || !data || data.channel !== CHANNEL || data.token !== token) return;
            if (data.type === "ready" && !sent) {
              sent = true;
              frame.contentWindow.postMessage({ channel: CHANNEL, type: "init", token, spec }, "*");
            } else if (data.type === "rendered" && sent) {
              this.dataset.g2State = "ready";
              frame.classList.remove("is-loading");
              const status = this._viewport.querySelector(".g2-chart-status");
              if (status) status.hidden = true;
              this._clearTimer();
            } else if (data.type === "error") this._fail("图表渲染失败，请展开配置检查。");
          };
          global.addEventListener("message", this._listener);
          frame.srcdoc = frameDocument({ nonce, token, g2Url, frameUrl });
          this.dataset.g2State = "loading";
          this._timer = global.setTimeout(() => {
            if (generation === this._generation) this._fail("图表加载超时，已保留完整配置。");
          }, 15000);
          this._viewport.appendChild(frame);
        } catch (error) { this._fail(error.message || "图表暂时无法显示，已保留完整配置。"); }
      }

      _clearTimer() { if (this._timer !== null) global.clearTimeout(this._timer); this._timer = null; }
      _releaseFrame() {
        this._clearTimer();
        if (this._listener) global.removeEventListener("message", this._listener);
        this._listener = null;
        this._frame?.remove();
        this._frame = null;
      }
      _fail(message) {
        this.dataset.g2State = "error";
        this._releaseFrame();
        this._status(message, true);
      }
      disconnectedCallback() {
        ++this._generation;
        this._observer?.disconnect(); this._observer = null;
        this._releaseFrame();
        this._viewport?.remove(); this._viewport = null;
        this._started = false;
        if (this.dataset.g2State !== "streaming") this.dataset.g2State = "pending";
      }
    }
    global.customElements.define("deepagent-g2-chart", G2ChartElement);
  }
  global.DeepAgentG2 = Object.freeze({ parseSpec, frameDocument, limits: LIMITS });
})(window);
