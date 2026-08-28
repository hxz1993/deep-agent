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

function findCatalogProvider(catalog, modelCode) {
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

  const bestRank = Math.min(...pool.map((candidate) => candidate.exactRank).filter((rank) => rank >= 0));
  const best = Number.isFinite(bestRank)
    ? pool.filter((candidate) => candidate.exactRank === bestRank)
    : pool;
  return best.length === 1 ? best[0] : null;
}

const CATALOG_PROVIDER_TYPE_ALIASES = {
  moonshot: "moonshotai",
  qwen: "alibaba",
  zhipu: "zai",
  zhipuai: "zai",
};

function catalogProviderType(provider) {
  const code = String(provider?.provider_id || "").trim().toLowerCase();
  return CATALOG_PROVIDER_TYPE_ALIASES[code] || code;
}

function providerChoicesForCatalog(provider) {
  const providerType = catalogProviderType(provider);
  return (state.llmProviders || []).filter(
    (item) => item.provider_type === providerType,
  );
}

function populateModelProviderSelect(selectedId = "") {
  const select = document.getElementById("llmModelProviderId");
  if (!select) return;
  const providers = state.llmProviders || [];
  if (!providers.length) {
    select.innerHTML = '<option value="">（请先创建 Provider）</option>';
    select.disabled = true;
    return;
  }
  select.disabled = false;
  select.innerHTML = providers.map((provider) => {
    const selected = provider.id === selectedId ? " selected" : "";
    const suffix = provider.api_key_set ? "" : "（未配置密钥）";
    return `<option value="${escapeHtml(provider.id)}"${selected}>${escapeHtml(provider.name)} · ${escapeHtml(providerTypeLabel(provider))}${suffix}</option>`;
  }).join("");
}

const llmModelsAdmin = {
  catalogTargetProviderId: "",
  editorContext: { lockProvider: false, deriveName: false },

  setCatalogTargetProvider(providerId = "") {
    this.catalogTargetProviderId = providerId;
  },

  async refresh() {
    const tbody = document.getElementById("llmModelsTableBody");
    if (!tbody) return;
    tbody.innerHTML = `<tr><td colspan="8" class="empty-cell">加载中…</td></tr>`;
    try {
      const [models] = await Promise.all([loadLlmModels(), loadLlmProviders()]);
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
          <td><span class="model-provider-cell"><span class="provider-key-dot ${model.provider?.api_key_set ? "on" : "off"}"></span>${escapeHtml(model.provider?.name || "—")}</span></td>
          <td><code>${escapeHtml(model.model)}</code></td>
          <td>${escapeHtml(formatTokenCount(model.context_window_tokens))}</td>
          <td>${escapeHtml(formatTokenCount(model.max_output_tokens))}</td>
          <td class="model-tag-cell">${formatList(model.input_modalities)}</td>
          <td>${model.agent_count}</td>
          <td>
            <div class="row-actions">
              <button type="button" data-edit="${escapeHtml(model.id)}">编辑</button>
              <button type="button" data-copy="${escapeHtml(model.id)}">复制</button>
              <button type="button" class="danger" data-delete="${escapeHtml(model.id)}" data-name="${escapeHtml(model.name)}">删除</button>
            </div>
          </td>
        </tr>`
        )
        .join("");

      tbody.querySelectorAll("[data-edit]").forEach((button) => {
        button.addEventListener("click", () => this.openEdit(button.dataset.edit));
      });
      tbody.querySelectorAll("[data-copy]").forEach((button) => {
        button.addEventListener("click", () => this.openCopy(button.dataset.copy));
      });
      tbody.querySelectorAll("[data-delete]").forEach((button) => {
        button.addEventListener("click", () => this.remove(button.dataset.delete, button.dataset.name));
      });
    } catch (error) {
      tbody.innerHTML = `<tr><td colspan="8" class="empty-cell">${escapeHtml(error.message)}</td></tr>`;
    }
  },

  setEditorContext({ lockProvider = false, deriveName = false } = {}) {
    this.editorContext = { lockProvider, deriveName };
    document.getElementById("llmModelNameField")?.classList.toggle("hidden", deriveName);
    const nameInput = document.getElementById("llmModelName");
    if (nameInput) nameInput.required = !deriveName;
    document.getElementById("llmModelProviderField")?.classList.toggle("hidden", lockProvider);
  },

  async openCreate(prefill = null, options = {}) {
    await Promise.all([loadLlmProviders(), loadLlmProviderTypes()]);
    this.setEditorContext(options);
    state.editingLlmModelId = null;
    document.getElementById("llmModelModalTitle").textContent = prefill?.model_label
      ? `使用目录模型 · ${prefill.model_label}`
      : "新建模型配置";
    document.getElementById("llmModelName").disabled = false;
    document.getElementById("llmModelName").value = prefill?.model_label || "";
    document.getElementById("llmModelDescription").value = prefill?.model_description || "";
    document.getElementById("llmModelModel").value = prefill?.runtime_model || prefill?.provider_model_id || "";
    const selectedProviderId = prefill?.provider_id || state.llmProviders[0]?.id || "";
    populateModelProviderSelect(selectedProviderId);
    document.getElementById("llmModelProviderId").disabled = Boolean(options.lockProvider);
    document.getElementById("llmModelContextWindow").value = prefill?.context_window_tokens || "";
    document.getElementById("llmModelMaxOutput").value = prefill?.max_output_tokens || "";
    document.getElementById("updateLlmModelFromCatalog").classList.add("hidden");
    setCheckedValues("llmModelModality", prefill?.input_modalities || ["text"]);
    setCheckedValues("llmModelTag", prefill?.tags || []);
    document.getElementById("llmModelModal").showModal();
  },

  async openCreateFromCatalog(model, provider) {
    await Promise.all([loadLlmProviders(), loadLlmProviderTypes()]);
    const targetProviderId = this.catalogTargetProviderId;
    const providerType = catalogProviderType(provider);
    const targeted = this.catalogTargetProviderId
      ? state.llmProviders.find((item) => item.id === this.catalogTargetProviderId)
      : null;
    this.catalogTargetProviderId = "";
    if (targetProviderId && (!targeted || targeted.provider_type !== providerType)) {
      showToast(targeted
        ? `该目录渠道不能添加到 ${targeted.name}，请选择同类型模型`
        : "当前 Provider 不存在", true);
      return;
    }
    const matches = targeted ? [targeted] : providerChoicesForCatalog(provider);
    if (!matches.length) {
      const supported = state.llmProviderTypes.find((item) => item.code === providerType);
      if (!supported) {
        showToast(`当前目录渠道 ${provider.provider_label || provider.provider_id} 没有可用的 Provider 类型`, true);
        return;
      }
      await window.llmProvidersAdmin?.openCreate({
        providerType,
        afterSave: (created) => this.openCreate({
          ...provider,
          provider_id: created.id,
          model_label: model.model_label,
          model_description: model.model_description,
        }, { lockProvider: true, deriveName: true }),
      });
      showToast(`请先创建 ${supported.label} Provider`);
      return;
    }
    let selected = matches[0];
    if (matches.length > 1) {
      const result = await requestActionFields({
        title: "选择 Provider",
        message: `${model.model_label} 有多个可用接入，请明确选择本次使用的 Provider。`,
        confirmLabel: "继续",
        fields: [{
          name: "provider_id",
          label: "Provider",
          type: "select",
          required: true,
          value: matches[0].id,
          options: matches.map((item) => ({ value: item.id, label: item.name })),
        }],
      });
      if (!result) return;
      selected = matches.find((item) => item.id === result.provider_id);
    }
    if (!selected) return;
    await this.openCreate({
      ...provider,
      provider_id: selected.id,
      model_label: model.model_label,
      model_description: model.model_description,
    }, targeted ? { lockProvider: true, deriveName: true } : {});
  },

  async openEdit(modelId, options = {}) {
    try {
      const [model] = await Promise.all([
        apiFetch(`/api/v1/llm-models/${encodeURIComponent(modelId)}`),
        loadLlmProviders(),
      ]);
      this.setEditorContext(options);
      state.editingLlmModelId = modelId;
      document.getElementById("llmModelModalTitle").textContent = `编辑模型 · ${model.model}`;
      document.getElementById("llmModelName").disabled = false;
      document.getElementById("llmModelName").value = model.name;
      document.getElementById("llmModelDescription").value = model.description || "";
      document.getElementById("llmModelModel").value = model.model;
      populateModelProviderSelect(model.provider_id);
      document.getElementById("llmModelProviderId").disabled = Boolean(options.lockProvider);
      document.getElementById("llmModelContextWindow").value = model.context_window_tokens || "";
      document.getElementById("llmModelMaxOutput").value = model.max_output_tokens || "";
      document.getElementById("updateLlmModelFromCatalog").classList.remove("hidden");
      setCheckedValues("llmModelModality", model.input_modalities || []);
      setCheckedValues("llmModelTag", model.tags || []);
      document.getElementById("llmModelModal").showModal();
    } catch (error) {
      showToast(error.message, true);
    }
  },

  async openCopy(modelId) {
    try {
      const model = await apiFetch(`/api/v1/llm-models/${encodeURIComponent(modelId)}`);
      await this.openCreate({
        model_label: model.name,
        model_description: model.description || "",
        runtime_model: model.model,
        provider_id: model.provider_id,
        context_window_tokens: model.context_window_tokens,
        max_output_tokens: model.max_output_tokens,
        input_modalities: model.input_modalities || [],
        tags: model.tags || [],
      });
      document.getElementById("llmModelModalTitle").textContent = `复制模型 · ${model.name}`;
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
      const match = findCatalogProvider(catalog, modelCode);
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
      const model = document.getElementById("llmModelModel").value.trim();
      const name = this.editorContext.deriveName ? model : document.getElementById("llmModelName").value.trim();
      if (!name || !model) throw new Error("名称和模型 API 编码不能为空");
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
        provider_id: document.getElementById("llmModelProviderId").value,
        input_modalities: inputModalities,
        context_window_tokens: contextWindow,
        max_output_tokens: maxOutput,
        tags: readCheckedValues("llmModelTag"),
      };
      if (!payload.provider_id) throw new Error("请选择 Provider");

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
        await loadLlmModels();
        await window.llmProvidersAdmin?.refresh();
      });
    }
  },

  async toggleEnabled(modelId) {
    const model = (state.llmModels || []).find((item) => item.id === modelId);
    if (!model) return;
    try {
      await apiFetch(`/api/v1/llm-models/${encodeURIComponent(modelId)}`, {
        method: "PATCH",
        body: JSON.stringify({ enabled: model.enabled === false }),
      });
      showToast(model.enabled === false ? "模型已启用" : "模型已停用");
      await refreshAfterSuccessfulMutation("模型状态已更新", async () => {
        await loadLlmModels();
        await window.llmProvidersAdmin?.refresh();
      });
    } catch (error) {
      showToast(error.message, true);
    }
  },

  async remove(modelId, name) {
    if (!(await confirmAction({
      title: "删除模型配置？",
      message: `删除后，使用该配置的 Agent 可能无法运行。当前对象：${name}`,
      icon: "trash-2",
      tone: "danger",
      confirmLabel: "删除",
    }))) return;
    try {
      await apiFetch(`/api/v1/llm-models/${encodeURIComponent(modelId)}`, { method: "DELETE" });
      showToast("模型配置已删除");
      await refreshAfterSuccessfulMutation("模型配置已删除", async () => {
        await loadLlmModels();
        await window.llmProvidersAdmin?.refresh();
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
  catalogProviderType,
  findCatalogProvider,
  formatTokenCount,
  modelCodeAliases,
  providerChoicesForCatalog,
};
