function setCheckedValues(name, values) {
  const selected = new Set(Array.isArray(values) ? values : []);
  document.querySelectorAll(`input[name="${name}"]`).forEach((input) => {
    input.checked = selected.has(input.value);
  });
}

function readCheckedValues(name) {
  return [...document.querySelectorAll(`input[name="${name}"]:checked`)].map(
    (input) => input.value,
  );
}

function formatList(values) {
  if (!Array.isArray(values) || !values.length) return "—";
  const labels = {
    text: "文本",
    image: "图像",
    audio: "音频",
    video: "视频",
    pdf: "PDF",
  };
  return values
    .map((item) => `<span class="badge">${escapeHtml(labels[item] || item)}</span>`)
    .join(" ");
}

function formatTokenCount(value) {
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) return "待补充";
  if (number >= 1_000_000) {
    const digits = number % 1_000_000 === 0 ? 0 : 1;
    return `${(number / 1_000_000).toFixed(digits)}M`;
  }
  if (number >= 1000) return `${Math.round(number / 1000)}K`;
  return String(number);
}

function positiveInteger(id, label) {
  const raw = document.getElementById(id).value.trim();
  const value = Number(raw);
  if (!Number.isSafeInteger(value) || value <= 0) {
    throw new Error(`${label}必须是正整数`);
  }
  return value;
}

function modelCodeAliases(value) {
  const normalized = String(value || "").trim().toLowerCase();
  if (!normalized) return new Set();
  const aliases = new Set([normalized]);
  const colonIndex = normalized.indexOf(":");
  if (colonIndex >= 0 && colonIndex < normalized.length - 1) {
    aliases.add(normalized.slice(colonIndex + 1));
  }
  const slashIndex = normalized.indexOf("/");
  if (slashIndex >= 0 && slashIndex < normalized.length - 1) {
    aliases.add(normalized.slice(slashIndex + 1));
  }
  return aliases;
}

function normalizedEndpoint(value) {
  try {
    const url = new URL(String(value || "").trim());
    return `${url.hostname.toLowerCase()}${url.pathname.replace(/\/+$/, "")}`;
  } catch {
    return "";
  }
}

function findCatalogProvider(catalog, modelCode, baseUrl = "") {
  const requested = modelCodeAliases(modelCode);
  const candidates = [];
  for (const model of catalog?.items || []) {
    for (const provider of model.providers || []) {
      const codes = [
        provider.runtime_model,
        provider.provider_model_id,
        model.canonical_model_id,
      ];
      const exactRank = codes.findIndex(
        (code) => String(code || "").trim().toLowerCase() === String(modelCode || "").trim().toLowerCase(),
      );
      const aliases = new Set(codes.flatMap((code) => [...modelCodeAliases(code)]));
      if ([...requested].some((alias) => aliases.has(alias))) {
        candidates.push({ model, provider, exactRank });
      }
    }
  }
  if (!candidates.length) return null;

  const exact = candidates.filter((candidate) => candidate.exactRank >= 0);
  const pool = exact.length ? exact : candidates;
  if (pool.length === 1) return pool[0];

  const endpoint = normalizedEndpoint(baseUrl);
  if (endpoint) {
    const endpointMatches = pool.filter(
      ({ provider }) => normalizedEndpoint(provider.provider_api_url) === endpoint,
    );
    if (endpointMatches.length === 1) return endpointMatches[0];
  }

  const bestRank = Math.min(...pool.map((candidate) => candidate.exactRank).filter((rank) => rank >= 0));
  const best = Number.isFinite(bestRank)
    ? pool.filter((candidate) => candidate.exactRank === bestRank)
    : pool;
  return best.length === 1 ? best[0] : null;
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
          (model) => `
        <tr>
          <td><strong>${escapeHtml(model.name)}</strong></td>
          <td><code>${escapeHtml(model.model)}</code></td>
          <td>${escapeHtml(formatTokenCount(model.context_window_tokens))}</td>
          <td>${escapeHtml(formatTokenCount(model.max_output_tokens))}</td>
          <td class="model-tag-cell">${formatList(model.input_modalities)}</td>
          <td><span class="badge ${model.api_key_set ? "on" : "off"}">${model.api_key_set ? "已配置" : "无"}</span></td>
          <td>${model.agent_count}</td>
          <td>
            <div class="row-actions">
              <button type="button" data-edit="${escapeHtml(model.id)}">编辑</button>
              <button type="button" class="danger" data-delete="${escapeHtml(model.id)}" data-name="${escapeHtml(model.name)}">删除</button>
            </div>
          </td>
        </tr>`
        )
        .join("");

      tbody.querySelectorAll("[data-edit]").forEach((button) => {
        button.addEventListener("click", () => this.openEdit(button.dataset.edit));
      });
      tbody.querySelectorAll("[data-delete]").forEach((button) => {
        button.addEventListener("click", () => this.remove(button.dataset.delete, button.dataset.name));
      });
    } catch (error) {
      tbody.innerHTML = `<tr><td colspan="8" class="empty-cell">${escapeHtml(error.message)}</td></tr>`;
    }
  },

  openCreate(prefill = null) {
    state.editingLlmModelId = null;
    document.getElementById("llmModelModalTitle").textContent = prefill
      ? `使用目录模型 · ${prefill.model_label}`
      : "新建模型配置";
    document.getElementById("llmModelName").disabled = false;
    document.getElementById("llmModelName").value = prefill?.model_label || "";
    document.getElementById("llmModelDescription").value = prefill?.model_description || "";
    document.getElementById("llmModelModel").value = prefill?.runtime_model || "";
    document.getElementById("llmModelBaseUrl").value = prefill?.provider_api_url || "";
    document.getElementById("llmModelApiKey").value = "";
    document.getElementById("llmModelContextWindow").value = prefill?.context_window_tokens || "";
    document.getElementById("llmModelMaxOutput").value = prefill?.max_output_tokens || "";
    document.getElementById("llmModelClearApiKey").checked = false;
    document.getElementById("llmModelClearApiKeyField").classList.add("hidden");
    document.getElementById("updateLlmModelFromCatalog").classList.add("hidden");
    setCheckedValues("llmModelModality", prefill?.input_modalities || ["text"]);
    setCheckedValues("llmModelTag", prefill?.tags || []);
    document.getElementById("llmModelModal").showModal();
  },

  openCreateFromCatalog(model, provider) {
    this.openCreate({
      ...provider,
      model_label: model.model_label,
      model_description: model.model_description,
    });
  },

  async openEdit(modelId) {
    try {
      const model = await apiFetch(`/api/v1/llm-models/${encodeURIComponent(modelId)}`);
      state.editingLlmModelId = modelId;
      document.getElementById("llmModelModalTitle").textContent = `编辑模型 · ${model.name}`;
      document.getElementById("llmModelName").disabled = false;
      document.getElementById("llmModelName").value = model.name;
      document.getElementById("llmModelDescription").value = model.description || "";
      document.getElementById("llmModelModel").value = model.model;
      document.getElementById("llmModelBaseUrl").value = model.base_url || "";
      document.getElementById("llmModelApiKey").value = "";
      document.getElementById("llmModelContextWindow").value = model.context_window_tokens || "";
      document.getElementById("llmModelMaxOutput").value = model.max_output_tokens || "";
      document.getElementById("llmModelClearApiKey").checked = false;
      document
        .getElementById("llmModelClearApiKeyField")
        .classList.toggle("hidden", !model.api_key_set);
      document.getElementById("updateLlmModelFromCatalog").classList.remove("hidden");
      setCheckedValues("llmModelModality", model.input_modalities || []);
      setCheckedValues("llmModelTag", model.tags || []);
      document.getElementById("llmModelModal").showModal();
    } catch (error) {
      showToast(error.message, true);
    }
  },

  async updateFromCatalog() {
    const button = document.getElementById("updateLlmModelFromCatalog");
    const modelCode = document.getElementById("llmModelModel").value.trim();
    if (!state.editingLlmModelId || !modelCode) {
      showToast("请先打开一个已有模型配置", true);
      return;
    }

    button.disabled = true;
    button.classList.add("is-loading");
    button.title = "正在从模型目录匹配";
    try {
      const queryCode = [...modelCodeAliases(modelCode)].at(-1) || modelCode;
      const params = new URLSearchParams({
        view: "model",
        q: queryCode,
        offset: "0",
        limit: "200",
      });
      const catalog = await apiFetch(`/api/v1/llm-models/catalog?${params}`);
      const match = findCatalogProvider(
        catalog,
        modelCode,
        document.getElementById("llmModelBaseUrl").value,
      );
      if (!match) {
        throw new Error(`模型目录中没有找到可唯一匹配的模型：${modelCode}`);
      }

      const { model, provider } = match;
      document.getElementById("llmModelContextWindow").value = provider.context_window_tokens;
      document.getElementById("llmModelMaxOutput").value = provider.max_output_tokens;
      setCheckedValues("llmModelModality", provider.input_modalities || []);
      setCheckedValues("llmModelTag", provider.tags || []);
      showToast(`已从模型目录读取 ${model.model_label}，保存后生效`);
    } catch (error) {
      showToast(error.message, true);
    } finally {
      button.disabled = false;
      button.classList.remove("is-loading");
      button.title = "从模型目录更新";
    }
  },

  async save(event) {
    event.preventDefault();
    const form = event.currentTarget;
    AdminFormFeedback.clear(form);
    AdminFormFeedback.setSubmitting(form, true);

    let saved = false;
    try {
      const name = document.getElementById("llmModelName").value.trim();
      const model = document.getElementById("llmModelModel").value.trim();
      if (!name || !model) throw new Error("名称和模型不能为空");
      const inputModalities = readCheckedValues("llmModelModality");
      if (!inputModalities.length) throw new Error("请至少选择一种输入模态");
      const contextWindow = positiveInteger("llmModelContextWindow", "上下文窗口");
      const maxOutput = positiveInteger("llmModelMaxOutput", "最大输出 Token");
      if (maxOutput > contextWindow) {
        throw new Error("最大输出 Token 不能超过上下文窗口");
      }

      const payload = {
        name,
        description: document.getElementById("llmModelDescription").value.trim() || null,
        model,
        base_url: document.getElementById("llmModelBaseUrl").value.trim() || null,
        input_modalities: inputModalities,
        context_window_tokens: contextWindow,
        max_output_tokens: maxOutput,
        tags: readCheckedValues("llmModelTag"),
      };
      const apiKey = document.getElementById("llmModelApiKey").value.trim();
      const clearApiKey = document.getElementById("llmModelClearApiKey").checked;
      if (clearApiKey) payload.api_key = null;
      else if (apiKey) payload.api_key = apiKey;

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
      saved = true;
      document.getElementById("llmModelModal").close();
    } catch (error) {
      AdminFormFeedback.show(form, error);
    } finally {
      AdminFormFeedback.setSubmitting(form, false);
    }
    if (saved) {
      await refreshAfterSuccessfulSave("模型配置", async () => {
        await this.refresh();
        await loadLlmModels();
      });
    }
  },

  async remove(modelId, name) {
    if (!confirm(`确定删除模型配置 "${name}"？`)) return;
    try {
      await apiFetch(`/api/v1/llm-models/${encodeURIComponent(modelId)}`, { method: "DELETE" });
      showToast("模型配置已删除");
      await refreshAfterSuccessfulMutation("模型配置已删除", async () => {
        await this.refresh();
        await loadLlmModels();
      });
    } catch (error) {
      showToast(error.message, true);
    }
  },
};

window.llmModelsAdmin = llmModelsAdmin;

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("newLlmModelBtn")?.addEventListener("click", () => llmModelsAdmin.openCreate());
  document.getElementById("llmModelForm")?.addEventListener("submit", (event) => llmModelsAdmin.save(event));
  document
    .getElementById("updateLlmModelFromCatalog")
    ?.addEventListener("click", () => llmModelsAdmin.updateFromCatalog());
  document.getElementById("closeLlmModelModal")?.addEventListener("click", () => {
    document.getElementById("llmModelModal").close();
  });
  document.getElementById("cancelLlmModelBtn")?.addEventListener("click", () => {
    document.getElementById("llmModelModal").close();
  });
});

window.__LlmModelConfigurationUI = {
  findCatalogProvider,
  formatTokenCount,
  modelCodeAliases,
};
