const skillsAdmin = {
  selectedCode: null,
  details: new Map(),
  uploadMode: "install",

  async refresh() {
    const pane = document.getElementById("skillListPane");
    if (!pane) return;
    pane.innerHTML = `<div class="skill-list-state"><span class="loading-spinner"></span>正在加载 Skill…</div>`;
    try {
      state.skills = await apiFetch("/api/v1/skills");
      showDbBanner(false);
      if (!state.skills.length) {
        pane.innerHTML = `<div class="skill-list-state">暂无 Skill，可上传 ZIP 安装。</div>`;
        this.renderEmptyDetail();
        return;
      }
      if (!state.skills.some((item) => item.code === this.selectedCode)) {
        this.selectedCode = state.skills[0].code;
      }
      this.renderList();
      await this.select(this.selectedCode);
    } catch (error) {
      pane.innerHTML = `<div class="skill-list-state is-error"><p>${escapeHtml(error.message)}</p><button class="btn" type="button" data-skill-retry>重试</button></div>`;
      pane.querySelector("[data-skill-retry]")?.addEventListener("click", () => this.refresh());
      this.renderEmptyDetail("Skill 列表加载失败");
    }
  },

  renderList() {
    const pane = document.getElementById("skillListPane");
    if (!pane) return;
    pane.innerHTML = state.skills.map((skill) => {
      const selected = skill.code === this.selectedCode;
      const source = skill.source === "builtin" ? "内置" : "上传";
      return `<button type="button" class="skill-list-item${selected ? " active" : ""}" data-skill-code="${escapeHtml(skill.code)}">
        <span class="skill-list-title"><strong>${escapeHtml(skill.label)}</strong><span class="badge source-${escapeHtml(skill.source)}">${source}</span></span>
        <code>${escapeHtml(skill.code)}</code>
        <span class="skill-list-meta">${skill.enabled ? "已启用" : "已停用"} · ${Number(skill.reference_count || 0)} References · ${Number(skill.asset_count || 0)} Assets</span>
      </button>`;
    }).join("");
    pane.querySelectorAll("[data-skill-code]").forEach((button) => {
      button.addEventListener("click", () => this.select(button.dataset.skillCode));
    });
    window.lucide?.createIcons();
  },

  renderEmptyDetail(message = "选择一个 Skill 查看说明和资源") {
    const detail = document.getElementById("skillDetailPane");
    if (!detail) return;
    detail.innerHTML = `<div class="skill-detail-empty"><i data-lucide="book-open-check"></i><p>${escapeHtml(message)}</p></div>`;
    window.lucide?.createIcons();
  },

  async select(code) {
    if (!code) return;
    this.selectedCode = code;
    this.renderList();
    const detail = document.getElementById("skillDetailPane");
    if (!detail) return;
    detail.innerHTML = `<div class="skill-list-state"><span class="loading-spinner"></span>正在加载详情…</div>`;
    try {
      const skill = await apiFetch(`/api/v1/skills/${encodeURIComponent(code)}`);
      this.details.set(code, skill);
      this.renderDetail(skill);
    } catch (error) {
      detail.innerHTML = `<div class="skill-list-state is-error"><p>${escapeHtml(error.message)}</p><button class="btn" type="button" data-skill-detail-retry>重试</button></div>`;
      detail.querySelector("[data-skill-detail-retry]")?.addEventListener("click", () => this.select(code));
    }
  },

  renderDetail(skill) {
    const detail = document.getElementById("skillDetailPane");
    if (!detail) return;
    const isBuiltin = skill.source === "builtin";
    const resources = skill.resources || [];
    const resourceRows = resources.length
      ? resources.map((resource) => {
          const isScript = resource.category === "script";
          const canRead = resource.readable && !isScript;
          const category = resource.category === "reference" ? "Reference" : resource.category === "asset" ? "Asset" : "Script";
          return `<li class="skill-resource-row${isScript ? " is-script" : ""}">
            <span><i data-lucide="${isScript ? "file-code-2" : resource.category === "reference" ? "file-text" : "file"}"></i><code>${escapeHtml(resource.path)}</code></span>
            <span class="skill-resource-meta">${category} · ${escapeHtml(resource.mime)} · ${this.formatBytes(resource.size)}</span>
            ${canRead ? `<button class="btn-link" type="button" data-read-resource="${escapeHtml(resource.path)}">查看</button>` : `<span class="skill-resource-lock">${isScript ? "禁止执行" : "仅可下载原包"}</span>`}
          </li>`;
        }).join("")
      : `<li class="skill-resource-empty">该 Skill 没有附加资源</li>`;
    detail.innerHTML = `
      <header class="skill-detail-header">
        <div>
          <div class="skill-detail-title"><h3>${escapeHtml(skill.label)}</h3><span class="badge source-${escapeHtml(skill.source)}">${isBuiltin ? "代码内置 · 只读" : "上传安装"}</span>${skill.enabled ? "" : `<span class="badge is-disabled">已停用</span>`}</div>
          <code>${escapeHtml(skill.code)}</code>
        </div>
        <div class="row-actions">
          ${isBuiltin ? "" : `<button class="btn" type="button" data-skill-action="edit">编辑</button><button class="btn" type="button" data-skill-action="replace">替换 ZIP</button><button class="btn" type="button" data-skill-action="download">下载原包</button><button class="btn" type="button" data-skill-action="toggle">${skill.enabled ? "停用" : "启用"}</button><button class="btn danger" type="button" data-skill-action="delete">删除</button>`}
        </div>
      </header>
      <p class="skill-detail-description">${escapeHtml(skill.description)}</p>
      <dl class="skill-facts">
        <div><dt>版本</dt><dd>${escapeHtml(skill.version || "未声明")}</dd></div>
        <div><dt>包格式</dt><dd>${escapeHtml(skill.schema_version)}</dd></div>
        <div><dt>References</dt><dd>${Number(skill.reference_count || 0)}</dd></div>
        <div><dt>Assets</dt><dd>${Number(skill.asset_count || 0)}</dd></div>
        <div><dt>脚本</dt><dd>${Number(skill.script_count || 0)}${skill.contains_scripts ? "（不可执行）" : ""}</dd></div>
        <div><dt>更新时间</dt><dd>${escapeHtml(formatDate(skill.updated_at))}</dd></div>
      </dl>
      ${skill.contains_scripts ? `<div class="skill-security-note"><i data-lucide="shield-alert"></i><span>包内含脚本；当前版本只保存，不向模型披露、读取或执行。</span></div>` : ""}
      <section class="skill-detail-section"><h4>资源</h4><ul class="skill-resource-list">${resourceRows}</ul></section>
      <details class="skill-manifest"><summary>Manifest</summary><pre>${escapeHtml(JSON.stringify(skill.manifest || {}, null, 2))}</pre></details>
      <section class="skill-resource-preview hidden" id="skillResourcePreview"></section>`;
    detail.querySelectorAll("[data-skill-action]").forEach((button) => {
      button.addEventListener("click", () => this.handleAction(button.dataset.skillAction, skill));
    });
    detail.querySelectorAll("[data-read-resource]").forEach((button) => {
      button.addEventListener("click", () => this.readResource(skill.code, button.dataset.readResource));
    });
    window.lucide?.createIcons();
  },

  formatBytes(value) {
    const bytes = Number(value || 0);
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MiB`;
  },

  async readResource(code, path) {
    const preview = document.getElementById("skillResourcePreview");
    if (!preview) return;
    preview.classList.remove("hidden");
    preview.innerHTML = `<div class="skill-list-state"><span class="loading-spinner"></span>正在读取资源…</div>`;
    try {
      const page = await apiFetch(`/api/v1/skills/${encodeURIComponent(code)}/resources/${path.split("/").map(encodeURIComponent).join("/")}?limit=32768`);
      preview.innerHTML = `<header><strong>${escapeHtml(path)}</strong><button class="icon-btn" type="button" aria-label="关闭资源预览"><i data-lucide="x"></i></button></header><pre>${escapeHtml(page.content)}</pre>${page.has_more ? `<p>内容较长，仅展示前 32 KiB。</p>` : ""}`;
      preview.querySelector("button")?.addEventListener("click", () => preview.classList.add("hidden"));
      window.lucide?.createIcons();
    } catch (error) {
      preview.innerHTML = `<div class="skill-list-state is-error">${escapeHtml(error.message)}</div>`;
    }
  },

  async handleAction(action, skill) {
    if (action === "replace") {
      this.uploadMode = "replace";
      document.getElementById("skillPackageInput")?.click();
      return;
    }
    if (action === "download") {
      try {
        const response = await daFetch(serviceUrl(`/api/v1/skills/${encodeURIComponent(skill.code)}/package`));
        if (!response.ok) throw new Error(`下载 Skill 失败（HTTP ${response.status}）`);
        const blobUrl = URL.createObjectURL(await response.blob());
        const anchor = document.createElement("a");
        anchor.href = blobUrl;
        anchor.download = skill.file_name || `${skill.code}.zip`;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        URL.revokeObjectURL(blobUrl);
      } catch (error) {
        showToast(error.message, true);
      }
      return;
    }
    if (action === "edit") {
      const label = prompt("Skill 显示名称", skill.label);
      if (label === null) return;
      const description = prompt("Skill 描述", skill.description);
      if (description === null) return;
      await this.patch(skill.code, { label, description }, "Skill 信息已更新");
      return;
    }
    if (action === "toggle") {
      await this.patch(skill.code, { enabled: !skill.enabled }, skill.enabled ? "Skill 已停用" : "Skill 已启用");
      return;
    }
    if (action === "delete") {
      if (!confirm(`确定删除 Skill “${skill.label}”？平台原始 ZIP 也会尝试清理。`)) return;
      try {
        await apiFetch(`/api/v1/skills/${encodeURIComponent(skill.code)}`, { method: "DELETE" });
        this.selectedCode = null;
        showToast("Skill 已删除");
        await this.refresh();
      } catch (error) {
        showToast(error.message, true);
      }
    }
  },

  async patch(code, payload, message) {
    try {
      await apiFetch(`/api/v1/skills/${encodeURIComponent(code)}`, { method: "PATCH", body: JSON.stringify(payload) });
      showToast(message);
      await this.refresh();
    } catch (error) {
      showToast(error.message, true);
    }
  },

  async upload(file) {
    if (!file) return;
    const replacing = this.uploadMode === "replace" && this.selectedCode;
    const path = replacing
      ? `/api/v1/skills/${encodeURIComponent(this.selectedCode)}/package`
      : "/api/v1/skills";
    const method = replacing ? "PUT" : "POST";
    const form = new FormData();
    form.append("file", file);
    try {
      const response = await daFetch(serviceUrl(path), { method, body: form });
      if (!response.ok) {
        let detail = response.statusText;
        try {
          const body = await response.json();
          detail = body.detail || body.message || detail;
        } catch { /* ignore */ }
        throw new Error(window.AdminFormFeedback?.formatDetail(detail) || String(detail));
      }
      const result = await response.json();
      this.selectedCode = result.skill.code;
      showToast(result.status === "noop" ? "Skill 包内容没有变化" : replacing ? "Skill 包已替换" : "Skill 已安装");
      await this.refresh();
    } catch (error) {
      showToast(error.message, true);
    } finally {
      this.uploadMode = "install";
      const input = document.getElementById("skillPackageInput");
      if (input) input.value = "";
    }
  },

  init() {
    document.getElementById("refreshSkillsBtn")?.addEventListener("click", () => this.refresh());
    document.getElementById("installSkillBtn")?.addEventListener("click", () => {
      this.uploadMode = "install";
      document.getElementById("skillPackageInput")?.click();
    });
    document.getElementById("skillPackageInput")?.addEventListener("change", (event) => this.upload(event.target.files?.[0]));
  },
};

window.skillsAdmin = skillsAdmin;

document.addEventListener("DOMContentLoaded", () => {
  skillsAdmin.init();
});
