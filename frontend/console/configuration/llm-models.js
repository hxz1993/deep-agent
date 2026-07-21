const CAPABILITIES_SOURCE_LABELS = {
  user: "手动",
  models_dev: "models.dev",
  bundled: "内置",
  heuristic: "启发式",
};

function readModalityMode() {
  const checked = document.querySelector('input[name="llmModelModalityMode"]:checked');
  return checked?.value === "manual" ? "manual" : "auto";
}

function setModalityMode(mode) {
  const auto = mode !== "manual";
  document.querySelectorAll('input[name="llmModelModalityMode"]').forEach((input) => {
    input.checked = input.value === (auto ? "auto" : "manual");
  });
  const manualPanel = document.getElementById("llmModelManualModalities");
  const autoHint = document.getElementById("llmModelAutoModalitiesHint");
  if (manualPanel) manualPanel.classList.toggle("hidden", auto);
  if (autoHint) autoHint.classList.toggle("hidden", !auto);
  if (!auto) {
    const preview = document.getElementById("llmModelAutoModalitiesPreview");
    if (preview) preview.classList.add("hidden");
  }
}

function updateAutoModalityPreview(modalities, source) {
  const preview = document.getElementById("llmModelAutoModalitiesPreview");
  if (!preview || readModalityMode() !== "auto") return;
  const items = Array.isArray(modalities) ? modalities : [];
  if (!items.length && !source) {
    preview.textContent = "保存后将自动解析模态。";
    preview.classList.remove("hidden");
    return;
  }
  const sourceLabel = source ? CAPABILITIES_SOURCE_LABELS[source] || source : "未刷新";
  preview.textContent = items.length
    ? `当前：${items.join(", ")}（来源：${sourceLabel}）`
    : `当前无模态数据（来源：${sourceLabel}）`;
  preview.classList.remove("hidden");
}

function setLlmModelModalities(modalities) {
  const selected = new Set(Array.isArray(modalities) ? modalities : []);
  document.querySelectorAll('input[name="llmModelModality"]').forEach((input) => {
    input.checked = selected.has(input.value);
  });
}

function readLlmModelModalities() {
  return [...document.querySelectorAll('input[name="llmModelModality"]:checked')].map(
    (input) => input.value,
  );
}

function formatModalities(modalities) {
  if (!Array.isArray(modalities) || modalities.length === 0) return "—";
  return modalities.map((item) => escapeHtml(item)).join(", ");
}

function formatCapabilitiesSource(source) {
  if (!source) {
    return `<span class="badge off">未刷新</span>`;
  }
  const label = CAPABILITIES_SOURCE_LABELS[source] || source;
  return `<span class="badge">${escapeHtml(label)}</span>`;
}

const llmModelsAdmin = {
  async refresh() {
    const tbody = document.getElementById("llmModelsTableBody");
    if (!tbody) return;
    tbody.innerHTML = `<tr><td colspan="8" class="empty-cell">加载中…</td></tr>`;
    try {
      const models = await apiFetch("/api/v1/llm-models");
      showDbBanner(false);
      if (!models.length) {
        tbody.innerHTML = `<tr><td colspan="8" class="empty-cell">暂无模型配置</td></tr>`;
        return;
      }
      tbody.innerHTML = models
        .map(
          (m) => `
        <tr>
          <td><strong>${escapeHtml(m.name)}</strong></td>
          <td>${escapeHtml(m.model)}</td>
          <td>${escapeHtml(m.base_url || "—")}</td>
          <td>${formatModalities(m.input_modalities)}</td>
          <td>${formatCapabilitiesSource(m.capabilities_source)}</td>
          <td><span class="badge ${m.api_key_set ? "on" : "off"}">${m.api_key_set ? "已配置" : "无"}</span></td>
          <td>${m.agent_count}</td>
          <td>
            <div class="row-actions">
              <button type="button" data-refresh-meta="${escapeHtml(m.id)}" data-name="${escapeHtml(m.name)}">刷新 Meta</button>
              <button type="button" data-edit="${escapeHtml(m.id)}">编辑</button>
              <button type="button" class="danger" data-delete="${escapeHtml(m.id)}" data-name="${escapeHtml(m.name)}">删除</button>
            </div>
          </td>
        </tr>`
        )
        .join("");

      tbody.querySelectorAll("[data-edit]").forEach((btn) => {
        btn.addEventListener("click", () => this.openEdit(btn.dataset.edit));
      });
      tbody.querySelectorAll("[data-refresh-meta]").forEach((btn) => {
        btn.addEventListener("click", () => this.refreshMetadata(btn.dataset.refreshMeta, btn.dataset.name));
      });
      tbody.querySelectorAll("[data-delete]").forEach((btn) => {
        btn.addEventListener("click", () => this.remove(btn.dataset.delete, btn.dataset.name));
      });
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="8" class="empty-cell">${escapeHtml(err.message)}</td></tr>`;
    }
  },

  async refreshAllMetadata() {
    const button = document.getElementById("refreshAllLlmModelMetaBtn");
    if (button) button.disabled = true;
    try {
      const result = await apiFetch("/api/v1/llm-models/refresh-metadata", { method: "POST" });
      const updated = Number(result?.updated ?? 0);
      showToast(`已刷新 ${updated} 条模型 Meta`);
      await this.refresh();
      await loadLlmModels();
    } catch (err) {
      showToast(err.message, true);
    } finally {
      if (button) button.disabled = false;
    }
  },

  async refreshMetadata(modelId, name) {
    try {
      await apiFetch(`/api/v1/llm-models/${encodeURIComponent(modelId)}/refresh-metadata`, {
        method: "POST",
      });
      showToast(`已刷新模型 Meta：${name}`);
      await this.refresh();
      await loadLlmModels();
    } catch (err) {
      showToast(err.message, true);
    }
  },

  openCreate() {
    state.editingLlmModelId = null;
    state.llmModelInitialModalityMode = "auto";
    document.getElementById("llmModelModalTitle").textContent = "新建模型配置";
    document.getElementById("llmModelName").disabled = false;
    document.getElementById("llmModelName").value = "";
    document.getElementById("llmModelDescription").value = "";
    document.getElementById("llmModelModel").value = "openai:gpt-4o";
    document.getElementById("llmModelBaseUrl").value = "";
    document.getElementById("llmModelApiKey").value = "";
    setModalityMode("auto");
    setLlmModelModalities(["text"]);
    const preview = document.getElementById("llmModelAutoModalitiesPreview");
    if (preview) preview.classList.add("hidden");
    document.getElementById("llmModelModal").showModal();
  },

  async openEdit(modelId) {
    try {
      const model = await apiFetch(`/api/v1/llm-models/${encodeURIComponent(modelId)}`);
      state.editingLlmModelId = modelId;
      const manual = model.capabilities_source === "user";
      state.llmModelInitialModalityMode = manual ? "manual" : "auto";
      document.getElementById("llmModelModalTitle").textContent = `编辑模型 · ${model.name}`;
      document.getElementById("llmModelName").disabled = false;
      document.getElementById("llmModelName").value = model.name;
      document.getElementById("llmModelDescription").value = model.description || "";
      document.getElementById("llmModelModel").value = model.model;
      document.getElementById("llmModelBaseUrl").value = model.base_url || "";
      document.getElementById("llmModelApiKey").value = "";
      setModalityMode(manual ? "manual" : "auto");
      setLlmModelModalities(manual ? model.input_modalities : ["text"]);
      updateAutoModalityPreview(model.input_modalities, model.capabilities_source);
      document.getElementById("llmModelModal").showModal();
    } catch (err) {
      showToast(err.message, true);
    }
  },

  applyModalityPayload(payload) {
    const mode = readModalityMode();
    if (mode === "manual") {
      const modalities = readLlmModelModalities();
      if (!modalities.length) {
        throw new Error("手动模式请至少选择一种输入模态");
      }
      if (state.editingLlmModelId) {
        payload.modalities_mode = "manual";
      }
      payload.input_modalities = modalities;
      return true;
    }
    if (state.editingLlmModelId && state.llmModelInitialModalityMode === "manual") {
      payload.modalities_mode = "auto";
    }
    return true;
  },

  async save(event) {
    event.preventDefault();
    const form = event.currentTarget;
    AdminFormFeedback.clear(form);
    AdminFormFeedback.setSubmitting(form, true);

    try {
      const payload = {
        name: document.getElementById("llmModelName").value.trim(),
        description: document.getElementById("llmModelDescription").value.trim() || null,
        model: document.getElementById("llmModelModel").value.trim(),
        base_url: document.getElementById("llmModelBaseUrl").value.trim() || null,
      };
      this.applyModalityPayload(payload);
      const apiKey = document.getElementById("llmModelApiKey").value.trim();
      if (apiKey) payload.api_key = apiKey;
      if (state.editingLlmModelId) {
        await apiFetch(`/api/v1/llm-models/${encodeURIComponent(state.editingLlmModelId)}`, {
          method: "PATCH",
          body: JSON.stringify(payload),
        });
        showToast("模型配置已更新");
      } else {
        await apiFetch("/api/v1/llm-models", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        showToast("模型配置已创建");
      }
      document.getElementById("llmModelModal").close();
      await this.refresh();
      await loadLlmModels();
    } catch (err) {
      AdminFormFeedback.show(form, err);
    } finally {
      AdminFormFeedback.setSubmitting(form, false);
    }
  },

  async remove(modelId, name) {
    if (!confirm(`确定删除模型配置 "${name}"？`)) return;
    try {
      await apiFetch(`/api/v1/llm-models/${encodeURIComponent(modelId)}`, { method: "DELETE" });
      showToast("模型配置已删除");
      await this.refresh();
      await loadLlmModels();
    } catch (err) {
      showToast(err.message, true);
    }
  },
};

window.llmModelsAdmin = llmModelsAdmin;

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("newLlmModelBtn")?.addEventListener("click", () => llmModelsAdmin.openCreate());
  document.getElementById("refreshAllLlmModelMetaBtn")?.addEventListener("click", () => {
    llmModelsAdmin.refreshAllMetadata();
  });
  document.getElementById("llmModelForm")?.addEventListener("submit", (e) => llmModelsAdmin.save(e));
  document.querySelectorAll('input[name="llmModelModalityMode"]').forEach((input) => {
    input.addEventListener("change", () => setModalityMode(readModalityMode()));
  });
  document.getElementById("closeLlmModelModal")?.addEventListener("click", () => {
    document.getElementById("llmModelModal").close();
  });
  document.getElementById("cancelLlmModelBtn")?.addEventListener("click", () => {
    document.getElementById("llmModelModal").close();
  });
});
