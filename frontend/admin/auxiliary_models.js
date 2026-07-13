const auxiliaryModelsAdmin = {
  editingUseCase: null,

  formatModelCell(item) {
    if (item.mode === "auto") {
      return `<span class="badge on">主模型（auto）</span>`;
    }
    const name = item.llm_model_name || "—";
    const model = item.model ? ` · ${escapeHtml(item.model)}` : "";
    return `<strong>${escapeHtml(name)}</strong>${model}`;
  },

  async refresh() {
    const tbody = document.getElementById("auxiliaryModelsTableBody");
    if (!tbody) return;
    tbody.innerHTML = `<tr><td colspan="6" class="empty-cell">加载中…</td></tr>`;
    try {
      await loadLlmModels();
      const items = await apiFetch("/api/v1/auxiliary-models");
      showDbBanner(false);
      if (!items.length) {
        tbody.innerHTML = `<tr><td colspan="6" class="empty-cell">暂无辅助模型配置</td></tr>`;
        return;
      }
      tbody.innerHTML = items
        .map(
          (item) => `
        <tr>
          <td><strong>${escapeHtml(item.label)}</strong></td>
          <td class="text-dim">${escapeHtml(item.description)}</td>
          <td>${this.formatModelCell(item)}</td>
          <td>${escapeHtml(item.mode === "auto" ? "—" : item.base_url || "—")}</td>
          <td><span class="badge ${item.mode === "auto" ? "off" : item.api_key_set ? "on" : "off"}">${item.mode === "auto" ? "—" : item.api_key_set ? "已配置" : "无"}</span></td>
          <td>
            <div class="row-actions">
              <button type="button" data-use-auto="${escapeHtml(item.use_case)}" ${item.mode === "auto" ? "disabled" : ""}>设为主模型</button>
              <button type="button" data-change="${escapeHtml(item.use_case)}" data-label="${escapeHtml(item.label)}">更改</button>
            </div>
          </td>
        </tr>`
        )
        .join("");

      tbody.querySelectorAll("[data-use-auto]").forEach((btn) => {
        btn.addEventListener("click", () => this.setAuto(btn.dataset.useAuto));
      });
      tbody.querySelectorAll("[data-change]").forEach((btn) => {
        btn.addEventListener("click", () => this.openChange(btn.dataset.change, btn.dataset.label));
      });
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="6" class="empty-cell">${escapeHtml(err.message)}</td></tr>`;
    }
  },

  async setAuto(useCase) {
    try {
      await apiFetch(`/api/v1/auxiliary-models/${encodeURIComponent(useCase)}`, {
        method: "PATCH",
        body: JSON.stringify({ llm_model_id: null }),
      });
      showToast("已设为主模型");
      await this.refresh();
    } catch (err) {
      showToast(err.message, true);
    }
  },

  openChange(useCase, label) {
    this.editingUseCase = useCase;
    document.getElementById("auxiliaryModelModalTitle").textContent = `更改辅助模型 · ${label}`;
    document.getElementById("auxiliaryModelModalHint").textContent =
      "选择「模型配置」中的预设；保存后将用于该固定场景。";
    const select = document.getElementById("auxiliaryModelSelect");
    const models = state.llmModels || [];
    if (!models.length) {
      select.innerHTML = `<option value="">（请先创建模型配置）</option>`;
      select.disabled = true;
    } else {
      select.disabled = false;
      select.innerHTML = models
        .map(
          (m) =>
            `<option value="${escapeHtml(m.id)}">${escapeHtml(m.name)} (${escapeHtml(m.model)})</option>`
        )
        .join("");
    }
    document.getElementById("auxiliaryModelModal").showModal();
  },

  async save(event) {
    event.preventDefault();
    if (!this.editingUseCase) return;
    const select = document.getElementById("auxiliaryModelSelect");
    const llmModelId = select.value;
    if (!llmModelId) {
      showToast("请选择模型配置", true);
      return;
    }
    try {
      await apiFetch(`/api/v1/auxiliary-models/${encodeURIComponent(this.editingUseCase)}`, {
        method: "PATCH",
        body: JSON.stringify({ llm_model_id: llmModelId }),
      });
      document.getElementById("auxiliaryModelModal").close();
      showToast("辅助模型已更新");
      await this.refresh();
    } catch (err) {
      showToast(err.message, true);
    }
  },
};

window.auxiliaryModelsAdmin = auxiliaryModelsAdmin;

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("auxiliaryModelForm")?.addEventListener("submit", (event) => {
    auxiliaryModelsAdmin.save(event);
  });
  document.getElementById("closeAuxiliaryModelModal")?.addEventListener("click", () => {
    document.getElementById("auxiliaryModelModal").close();
  });
  document.getElementById("cancelAuxiliaryModelBtn")?.addEventListener("click", () => {
    document.getElementById("auxiliaryModelModal").close();
  });
});
