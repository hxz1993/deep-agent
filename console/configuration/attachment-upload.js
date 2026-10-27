/* General settings: shared upload limits, with no long-lived business-data cache. */
(() => {
  const ENDPOINT = "/api/v1/global-settings/attachment-upload";
  const ids = { dialog: "attachmentUploadSettingsDialog", size: "attachmentUploadMaxSize", count: "attachmentUploadMaxCount" };
  const node = id => document.getElementById(id);
  let settings = null, ready = false, pending = null, saving = false, sequence = 0;
  const fields = [
    { id: ids.size, key: "max_size_mb", min: 1, max: 200, fallback: 20, error: "单文件大小需为 1～200 MB 的整数。" },
    { id: ids.count, key: "max_count", min: 1, max: 20, fallback: 10, error: "单次文件数量需为 1～20 个的整数。" },
  ];
  const MIME_NAMES = {
    "application/pdf": ["文档", "PDF"],
    "application/msword": ["文档", "DOC"],
    "application/vnd.ms-excel": ["文档", "XLS"],
    "application/vnd.ms-powerpoint": ["文档", "PPT"],
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ["文档", "DOCX"],
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ["文档", "XLSX"],
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ["文档", "PPTX"],
    "text/plain": ["文本", "TXT / LOG"], "text/csv": ["文本", "CSV"],
    "text/markdown": ["文本", "Markdown"], "text/html": ["文本", "HTML"],
    "text/xml": ["文本", "XML"], "application/xml": ["文本", "XML"],
    "application/json": ["文本", "JSON"], "text/json": ["文本", "JSON"],
    "image/jpeg": ["图片", "JPG / JPEG"], "image/png": ["图片", "PNG"],
    "image/webp": ["图片", "WebP"], "image/gif": ["图片", "GIF"],
    "audio/mpeg": ["音频", "MP3"], "audio/amr": ["音频", "AMR"],
    "audio/wav": ["音频", "WAV"], "video/mp4": ["视频", "MP4"],
  };
  function allowedTypesHtml(mimes) {
    const groups = new Map(["文档", "文本", "图片", "音频", "视频", "其他"].map(label => [label, new Set()]));
    for (const mime of mimes || []) {
      const [group, name] = MIME_NAMES[mime] || ["其他", mime];
      groups.get(group).add(name);
    }
    return [...groups].filter(([, names]) => names.size).map(([group, names]) => `<div><dt>${escapeHtml(group)}</dt><dd>${[...names].map(escapeHtml).join("、")}</dd></div>`).join("") || "<div><dt>类型</dt><dd>未允许任何类型</dd></div>";
  }
  function validateResponse(response) {
    if (!response || !Array.isArray(response.allowed_mimes)) throw new Error("附件上传配置响应格式错误");
    for (const field of fields) {
      const value = response.effective_value?.[field.key];
      if (!Number.isInteger(value) || value < field.min || value > field.max) throw new Error("附件上传配置响应格式错误");
      const saved = response.value?.[field.key];
      if (saved != null && (!Number.isInteger(saved) || saved < field.min || saved > field.max)) throw new Error("附件上传配置响应格式错误");
    }
    return response;
  }
  function clearError() {
    node("attachmentUploadFormError")?.classList.add("hidden");
    for (const field of fields) node(field.id)?.removeAttribute("aria-invalid");
  }
  function errorFeedback(message) {
    const target = node("attachmentUploadFormError");
    if (target) { target.textContent = message; target.classList.remove("hidden"); }
  }
  function setBusy(busy) {
    saving = busy;
    for (const id of [ids.size, ids.count, "saveAttachmentUploadSettingsBtn", "cancelAttachmentUploadSettingsBtn", "closeAttachmentUploadSettingsBtn", "resetAttachmentUploadSettingsBtn"]) {
      if (node(id)) node(id).disabled = busy;
    }
    if (node("saveAttachmentUploadSettingsBtn")) node("saveAttachmentUploadSettingsBtn").textContent = busy ? "保存中…" : "保存";
  }
  function renderSummary() {
    node("attachmentUploadSizeSummary").textContent = `${settings.effective_value.max_size_mb} MB`;
    node("attachmentUploadCountSummary").textContent = `${settings.effective_value.max_count} 个`;
    node("attachmentUploadSummary")?.classList.remove("hidden");
    node("attachmentUploadLoadError")?.classList.add("hidden");
    node("retryAttachmentUploadSettingsBtn")?.classList.add("hidden");
    node("openAttachmentUploadSettingsBtn").disabled = !ready;
  }
  function active() {
    return window.globalSettingsAdmin?.page === "general"
      && (!document.body?.dataset.consolePage || (document.body.dataset.consolePage === "configuration"
        && node("panel-global-settings")?.classList.contains("active")));
  }
  function buildPayload() {
    const value = {};
    for (const field of fields) {
      const raw = String(node(field.id)?.value ?? "").trim();
      const number = raw === "" ? field.fallback : Number(raw);
      if (!Number.isSafeInteger(number) || number < field.min || number > field.max) {
        const error = new Error(field.error); error.fieldId = field.id; throw error;
      }
      value[field.key] = number;
    }
    return { value };
  }
  const admin = {
    refresh({ reuseCompleted = false } = {}) {
      if (pending) return pending;
      if (saving || node(ids.dialog)?.open || (reuseCompleted && ready)) return Promise.resolve();
      const requestId = ++sequence;
      ready = false;
      node("openAttachmentUploadSettingsBtn").disabled = true;
      node("attachmentUploadSummary")?.classList.add("hidden");
      const status = node("attachmentUploadLoadError");
      if (status) { status.textContent = "正在加载…"; status.classList.remove("hidden"); }
      node("retryAttachmentUploadSettingsBtn")?.classList.add("hidden");
      pending = (async () => {
        try {
          const response = validateResponse(await apiFetch(ENDPOINT));
          if (requestId !== sequence || !active() || node(ids.dialog)?.open) return;
          settings = response; ready = true; renderSummary();
        } catch (error) {
          if (requestId !== sequence || !active()) return;
          if (status) status.textContent = "配置加载失败";
          node("retryAttachmentUploadSettingsBtn")?.classList.remove("hidden");
          showToast(error?.message || "附件上传配置加载失败", true);
        } finally { pending = null; }
      })();
      return pending;
    },
    open() {
      if (!ready || saving || !active()) return;
      clearError();
      for (const field of fields) node(field.id).value = settings.value?.[field.key] ?? "";
      node("attachmentUploadAllowedTypes").innerHTML = allowedTypesHtml(settings.allowed_mimes);
      node(ids.dialog)?.showModal();
    },
    close() { if (!saving) node(ids.dialog)?.close(); },
    async save() {
      if (saving || !ready) return;
      clearError();
      let payload;
      try { payload = buildPayload(); }
      catch (error) { errorFeedback(error.message); node(error.fieldId)?.setAttribute("aria-invalid", "true"); node(error.fieldId)?.focus(); return; }
      setBusy(true);
      let committed = false;
      try {
        const saved = await apiFetch(ENDPOINT, { method: "PUT", body: JSON.stringify(payload) });
        committed = true;
        settings = validateResponse(saved); ready = true; renderSummary();
        window.dispatchEvent(new Event("attachment-upload-settings-changed"));
        node(ids.dialog)?.close();
        try {
          settings = validateResponse(await apiFetch(ENDPOINT));
          renderSummary(); showToast("附件上传配置已保存");
        } catch (error) { showToast(`附件上传配置已保存，刷新失败：${error.message}`, true); }
      } catch (error) {
        if (committed) {
          ready = false; node("openAttachmentUploadSettingsBtn").disabled = true;
          errorFeedback("附件上传配置已保存，响应读取失败，请关闭后重新加载核对。");
          window.dispatchEvent(new Event("attachment-upload-settings-changed"));
        } else errorFeedback(error?.message || "附件上传配置保存失败");
      } finally { setBusy(false); }
    },
  };
  window.attachmentUploadAdmin = admin;
  window.__AttachmentUploadUI = { buildPayload, allowedTypesHtml, validateResponse };
  document.addEventListener("DOMContentLoaded", () => {
    node("openAttachmentUploadSettingsBtn")?.addEventListener("click", () => admin.open());
    node("retryAttachmentUploadSettingsBtn")?.addEventListener("click", () => { void admin.refresh(); });
    for (const id of ["closeAttachmentUploadSettingsBtn", "cancelAttachmentUploadSettingsBtn"]) node(id)?.addEventListener("click", () => admin.close());
    node("resetAttachmentUploadSettingsBtn")?.addEventListener("click", () => { clearError(); for (const field of fields) node(field.id).value = field.fallback; });
    node("attachmentUploadSettingsForm")?.addEventListener("input", clearError);
    node("attachmentUploadSettingsForm")?.addEventListener("submit", event => { event.preventDefault(); void admin.save(); });
    node(ids.dialog)?.addEventListener("cancel", event => { if (saving) event.preventDefault(); });
    node(ids.dialog)?.addEventListener("click", event => {
      if (event.target !== node(ids.dialog) || saving) return;
      const rect = event.target.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) admin.close();
    });
  });
})();
