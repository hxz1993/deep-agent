const skillsAdmin = {
  selectedCode: null,
  selectedResourcePath: null,
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
      const runtime = this.runtimeLabel(skill);
      return `<button type="button" class="skill-list-item${selected ? " active" : ""}" data-skill-code="${escapeHtml(skill.code)}">
        <span class="skill-list-card-head">
          <span class="skill-list-icon"><i data-lucide="book-open-check"></i></span>
          <span class="skill-list-title"><strong>${escapeHtml(skill.label)}</strong><span class="badge source-${escapeHtml(skill.source)}">${source}</span></span>
          <i class="skill-list-chevron" data-lucide="chevron-right"></i>
        </span>
        <code class="skill-list-code">${escapeHtml(skill.code)}</code>
        <span class="skill-list-meta"><span class="skill-runtime-dot runtime-${escapeHtml(skill.runtime_status || "missing")}"></span>${runtime.label}<span>${Number(skill.reference_count || 0)} References</span><span>${Number(skill.asset_count || 0)} Assets</span></span>
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
    if (code !== this.selectedCode) this.selectedResourcePath = null;
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
    const runtime = this.runtimeLabel(skill);
    const resources = skill.resources || [];
    const canPreviewResource = (resource) =>
      ["text", "docx", "xlsx"].includes(resource.read_format) && resource.category !== "script";
    const firstPreviewableResource = resources.find(canPreviewResource);
    const selectedResource = resources.find((resource) => resource.path === this.selectedResourcePath && canPreviewResource(resource));
    const activeResourcePath = this.selectedResourcePath === "SKILL.md"
      ? "SKILL.md"
      : selectedResource?.path || firstPreviewableResource?.path || "SKILL.md";
    this.selectedResourcePath = activeResourcePath;
    const skillDefinitionRow = `<li class="skill-resource-row${activeResourcePath === "SKILL.md" ? " active" : ""}" data-read-resource="SKILL.md" data-resource-path="SKILL.md" role="button" tabindex="0">
      <span class="skill-resource-main"><i data-lucide="file-badge"></i><span><code>SKILL.md</code><small>Skill 定义 · 控制台索引摘要</small></span></span>
      <i class="skill-resource-open" data-lucide="chevron-right"></i>
    </li>`;
    const resourceRows = resources.length
      ? resources.map((resource) => {
          const isScript = resource.category === "script";
          const canRead = canPreviewResource(resource);
          const category = resource.category === "reference" ? "Reference" : resource.category === "asset" ? "Asset" : "Script";
          const readLabels = {
            text: "文本可读",
            docx: "Word 可读",
            xlsx: "Excel 可读",
          };
          const unreadableStatus = resource.path?.toLowerCase().endsWith(".pptx")
              ? "PPTX 暂不可读"
              : "当前暂不可读";
          return `<li class="skill-resource-row${isScript ? " is-script" : ""}${resource.path === activeResourcePath ? " active" : ""}" ${canRead ? `data-read-resource="${escapeHtml(resource.path)}" role="button" tabindex="0"` : ""} data-resource-path="${escapeHtml(resource.path)}">
            <span class="skill-resource-main"><i data-lucide="${isScript ? "file-code-2" : resource.category === "reference" ? "file-text" : "file"}"></i><span><code>${escapeHtml(resource.path)}</code><small>${category} · ${escapeHtml(resource.mime)} · ${this.formatBytes(resource.size)}${canRead ? ` · ${escapeHtml(readLabels[resource.read_format] || "可读")}` : ""}</small></span></span>
            ${canRead ? `<i class="skill-resource-open" data-lucide="chevron-right"></i>` : isScript ? `<span class="skill-resource-lock">脚本不披露源码</span>` : `<span class="skill-resource-lock">${escapeHtml(unreadableStatus)}</span>`}
          </li>`;
        }).join("")
      : `<li class="skill-resource-empty">该 Skill 没有附加资源</li>`;
    detail.innerHTML = `
      <header class="skill-detail-header">
        <div class="skill-detail-identity">
          <span class="skill-detail-eyebrow">Skill 详情</span>
          <div class="skill-detail-title"><h3>${escapeHtml(skill.label)}</h3><span class="badge source-${escapeHtml(skill.source)}">${isBuiltin ? "代码内置 · 只读" : "上传安装"}</span><span class="badge runtime-${escapeHtml(skill.runtime_status || "missing")}">${escapeHtml(runtime.label)}</span></div>
          <code>${escapeHtml(skill.code)}</code>
        </div>
        <div class="row-actions">
          ${isBuiltin ? "" : `<button class="btn" type="button" data-skill-action="edit">编辑</button><button class="btn" type="button" data-skill-action="replace">替换 ZIP</button><button class="btn" type="button" data-skill-action="download">导出</button>${skill.enabled ? `<button class="btn" type="button" data-skill-action="reload">重新加载</button>` : ""}<button class="btn" type="button" data-skill-action="toggle">${skill.enabled ? "停用" : "启用"}</button><button class="btn danger" type="button" data-skill-action="delete">删除</button>`}
        </div>
      </header>
      <div class="skill-detail-layout">
        <div class="skill-detail-main">
          <section class="skill-detail-summary">
            <p class="skill-detail-description">${escapeHtml(skill.description)}</p>
            <dl class="skill-facts">
              <div><i data-lucide="tag"></i><span><dt>版本</dt><dd>${escapeHtml(skill.version || "未声明")}</dd></span></div>
              <div><i data-lucide="braces"></i><span><dt>包格式</dt><dd>${escapeHtml(skill.schema_version)}</dd></span></div>
              <div><i data-lucide="book-open-text"></i><span><dt>References</dt><dd>${Number(skill.reference_count || 0)}</dd></span></div>
              <div><i data-lucide="paperclip"></i><span><dt>Assets</dt><dd>${Number(skill.asset_count || 0)}</dd></span></div>
              <div><i data-lucide="terminal-square"></i><span><dt>脚本</dt><dd>${Number(skill.script_count || 0)}</dd></span></div>
              <div><i data-lucide="clock-3"></i><span><dt>更新时间</dt><dd>${escapeHtml(formatDate(skill.updated_at))}</dd></span></div>
            </dl>
          </section>
          ${skill.runtime_status === "error" || skill.runtime_status === "missing" ? `<div class="skill-security-note"><i data-lucide="circle-alert"></i><span>${escapeHtml(runtime.detail)}</span></div>` : ""}
          <section class="skill-detail-section skill-resource-directory">
            <div class="skill-lane-heading"><span class="skill-lane-icon"><i data-lucide="folder-open"></i></span><div><h4>资源文件</h4><p>SKILL.md、References 与 Assets</p></div><span>${resources.length + 1} 项</span></div>
            <ul class="skill-resource-list">${skillDefinitionRow}${resourceRows}</ul>
          </section>
        </div>
        <aside class="skill-detail-lane">
          <section class="skill-resource-preview" id="skillResourcePreview">
            <div class="skill-lane-heading"><span class="skill-lane-icon"><i data-lucide="scan-text"></i></span><div><h4>文件预览</h4><p>选择可读资源后在此直接查看</p></div></div>
            <div class="skill-preview-empty">正在加载预览…</div>
          </section>
        </aside>
      </div>`;
    detail.querySelectorAll("[data-skill-action]").forEach((button) => {
      button.addEventListener("click", () => this.handleAction(button.dataset.skillAction, skill));
    });
    detail.querySelectorAll("[data-read-resource]").forEach((button) => {
      button.addEventListener("click", () => this.readResource(skill.code, button.dataset.readResource));
      button.addEventListener("keydown", (event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        this.readResource(skill.code, button.dataset.readResource);
      });
    });
    window.lucide?.createIcons();
    if (activeResourcePath) {
      this.readResource(skill.code, activeResourcePath);
    }
  },

  formatBytes(value) {
    const bytes = Number(value || 0);
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MiB`;
  },

  runtimeLabel(skill) {
    const labels = {
      ready: { label: "运行文件已就绪", detail: "当前 Pod 已加载该 Skill 的运行文件。" },
      missing: { label: "运行文件未加载", detail: "当前 Pod 缺少运行文件，请使用“重新加载”。" },
      syncing: { label: "运行文件同步中", detail: "正在同步 Skill 运行文件。" },
      error: { label: "运行文件同步失败", detail: `运行文件不可用（${skill.runtime_error_code || "unknown"}），普通对话和其他 Skill 不受影响。` },
      disabled: { label: "已停用", detail: "Skill 已停用。" },
    };
    return labels[skill.runtime_status] || labels.missing;
  },

  async readResource(code, path) {
    const preview = document.getElementById("skillResourcePreview");
    if (!preview) return;
    this.selectedResourcePath = path;
    document.querySelectorAll("#skillDetailPane [data-resource-path]").forEach((row) => {
      row.classList.toggle("active", row.dataset.resourcePath === path);
    });
    preview.innerHTML = `<div class="skill-lane-heading"><span class="skill-lane-icon"><i data-lucide="scan-text"></i></span><div><h4>文件预览</h4><p>${escapeHtml(path)}</p></div></div><div class="skill-preview-loading"><span class="loading-spinner"></span>正在读取资源…</div>`;
    window.lucide?.createIcons();
    if (path === "SKILL.md") {
      const skill = this.details.get(code) || {};
      const summary = [
        `# ${skill.label || code}`,
        "",
        String(skill.description || ""),
        "",
        `Skill code: ${skill.code || code}`,
        `Version: ${skill.version || "未声明"}`,
        "",
        "完整 SKILL.md 正文由运行时按需加载；此处展示控制台可用的索引摘要。",
      ].join("\n");
      preview.innerHTML = `<div class="skill-lane-heading"><span class="skill-lane-icon"><i data-lucide="scan-text"></i></span><div><h4>文件预览</h4><p>SKILL.md · 索引摘要</p></div><span>MARKDOWN</span></div><pre>${escapeHtml(summary)}</pre>`;
      window.lucide?.createIcons();
      return;
    }
    try {
      const page = await apiFetch(`/api/v1/skills/${encodeURIComponent(code)}/resources/${path.split("/").map(encodeURIComponent).join("/")}`);
      const unitLabels = { line: "行", block: "逻辑块", row: "行" };
      preview.innerHTML = `<div class="skill-lane-heading"><span class="skill-lane-icon"><i data-lucide="scan-text"></i></span><div><h4>文件预览</h4><p>${escapeHtml(path)}</p></div><span>${escapeHtml(page.format.toUpperCase())} · ${Number(page.returned_count || 0)} ${escapeHtml(unitLabels[page.unit] || page.unit)}</span></div><pre>${escapeHtml(page.content)}</pre>${page.has_more ? `<p class="skill-preview-more">内容较长，可由 Agent 使用 next_offset=${escapeHtml(page.next_offset)} 继续分页读取。</p>` : ""}`;
      window.lucide?.createIcons();
    } catch (error) {
      preview.innerHTML = `<div class="skill-lane-heading"><span class="skill-lane-icon"><i data-lucide="scan-text"></i></span><div><h4>文件预览</h4><p>${escapeHtml(path)}</p></div></div><div class="skill-preview-empty is-error">${escapeHtml(error.message)}</div>`;
      window.lucide?.createIcons();
    }
  },

  async download(path, fallbackName) {
    const response = await daFetch(serviceUrl(path));
    if (!response.ok) {
      let detail = `导出 Skill 失败（HTTP ${response.status}）`;
      try {
        const body = await response.json();
        detail = window.AdminFormFeedback?.formatDetail(body.detail || body.message) || detail;
      } catch { /* ignore */ }
      throw new Error(detail);
    }
    const blobUrl = URL.createObjectURL(await response.blob());
    try {
      const anchor = document.createElement("a");
      anchor.href = blobUrl;
      anchor.download = fallbackName;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
    } finally {
      URL.revokeObjectURL(blobUrl);
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
        await this.download(
          `/api/v1/skills/${encodeURIComponent(skill.code)}/package`,
          skill.file_name || `${skill.code}.zip`,
        );
      } catch (error) {
        showToast(error.message, true);
      }
      return;
    }
    if (action === "reload") {
      try {
        await apiFetch(`/api/v1/skills/${encodeURIComponent(skill.code)}/reload`, { method: "POST" });
        showToast("Skill 运行文件已重新加载");
        await this.refresh();
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
      showToast(result.status === "noop" ? "Skill 包内容没有变化" : result.status === "repaired" ? "Skill 原包与运行文件已恢复" : replacing ? "Skill 包已替换" : "Skill 已安装");
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
    document.getElementById("exportAllSkillsBtn")?.addEventListener("click", async () => {
      try {
        await this.download("/api/v1/skills/export", "deepagent-skills-export.zip");
      } catch (error) {
        showToast(error.message, true);
      }
    });
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
