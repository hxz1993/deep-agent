(function () {
  function formatDetail(detail) {
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((item) => {
          if (!item || typeof item !== "object") return String(item);
          const path = Array.isArray(item.loc) ? item.loc.filter((part) => part !== "body").join(".") : "";
          return `${path ? `${path}: ` : ""}${item.msg || JSON.stringify(item)}`;
        })
        .join("；");
    }
    if (detail && typeof detail === "object") return JSON.stringify(detail);
    return String(detail || "请求失败");
  }

  function errorElement(form) {
    return form?.querySelector("[data-form-error]") || null;
  }

  function clear(form) {
    const element = errorElement(form);
    if (!element) return;
    element.textContent = "";
    element.classList.add("hidden");
  }

  function show(form, error) {
    const element = errorElement(form);
    if (!element) return;
    element.textContent = formatDetail(error?.message ?? error);
    element.classList.remove("hidden");
    element.scrollIntoView?.({ block: "nearest" });
  }

  function setSubmitting(form, submitting) {
    const submit = form?.querySelector('button[type="submit"]');
    if (!submit) return;
    if (!submit.dataset.label) submit.dataset.label = submit.textContent;
    submit.disabled = submitting;
    submit.textContent = submitting
      ? (submit.dataset.submittingLabel || "保存中…")
      : submit.dataset.label;
  }

  window.AdminFormFeedback = { formatDetail, clear, show, setSubmitting };

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("dialog.modal").forEach((dialog) => {
      if (dialog.dataset.formFeedbackBound) return;
      dialog.dataset.formFeedbackBound = "1";
      dialog.addEventListener("close", () => {
        const form = dialog.querySelector("form");
        clear(form);
        setSubmitting(form, false);
      });
    });
  });
})();
