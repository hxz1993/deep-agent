// Stored attachments keep their original references; preview URLs are view-only.
export function isImageAttachment(attachment) {
  const mime = String(attachment?.mime || attachment?.mime_type || "").toLowerCase();
  return mime.startsWith("image/") || (!mime && /\.(png|jpe?g|gif|webp|avif|bmp)$/i.test(attachment?.name || ""));
}

export function attachmentPreviewUrl(attachment, baseURI = globalThis.document?.baseURI) {
  return isImageAttachment(attachment) ? attachmentDownloadUrl(attachment, baseURI) : null;
}

export function attachmentDownloadUrl(attachment, baseURI = globalThis.document?.baseURI, { diagnosticSessionId } = {}) {
  if (!attachment?.id || !baseURI) return null;
  const root = new URL("../", baseURI);
  const id = String(attachment.id);
  if (id.startsWith("local_")) {
    const path = diagnosticSessionId
      ? `api/v1/diagnostics/sessions/${encodeURIComponent(diagnosticSessionId)}/files/${encodeURIComponent(id)}/download`
      : `api/v1/local-files/${encodeURIComponent(id)}/download`;
    return new URL(path, root).href;
  }
  // Platform deployments mount sibling services below the same gateway prefix.
  // Match the platform client's existing file route, without proxying file bytes.
  const prefix = root.pathname.includes("/deepfos-server/")
    ? root.pathname.split("/deepfos-server/")[0] + "/deepfos-server"
    : "/deepfos-server";
  const url = new URL(`${prefix}/platform-file-server/files/download`, root);
  url.searchParams.set("id", id);
  if (attachment.name) url.searchParams.set("fileName", attachment.name);
  return url.href;
}

function escapeAttribute(value) {
  return String(value).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

export function attachmentPreviewAttributes(attachment, baseURI, downloadUrl) {
  const url = isImageAttachment(attachment) ? downloadUrl || attachmentPreviewUrl(attachment, baseURI) : null;
  if (!url) return "";
  const name = attachment.name || "图片附件";
  return `data-attachment-image-url="${escapeAttribute(url)}" data-attachment-image-name="${escapeAttribute(name)}" aria-label="预览图片 ${escapeAttribute(name)}"`;
}

export function createImageGallery(attachment, attachments = [attachment]) {
  const items = attachments.filter((item) => item?.previewUrl);
  let index = items.indexOf(attachment);
  if (index < 0) index = items.findIndex((item) => item.previewUrl === attachment?.previewUrl);
  return index < 0 ? { items: attachment?.previewUrl ? [attachment] : [], index: 0 } : { items, index };
}

// Delegate on the stable document: history, live bubbles and trajectory use the
// same hover preview and existing image dialog. The floating preview is outside
// scroll panes so a bubble near the viewport edge cannot clip it.
export function bindAttachmentImagePreviews({ root = document, openImage }) {
  const listeners = [];
  const listen = (target, name, handler, options) => { target.addEventListener(name, handler, options); listeners.push(() => target.removeEventListener(name, handler, options)); };
  let active = null;
  let preview = null;
  const selector = "[data-attachment-image-url]";
  const hide = () => {
    preview?.remove();
    preview = null;
    active = null;
  };
  const show = (button) => {
    if (active === button) return;
    hide();
    active = button;
    preview = document.createElement("div");
    preview.className = "attachment-hover-preview";
    preview.setAttribute("role", "tooltip");
    const image = document.createElement("img");
    image.alt = button.dataset.attachmentImageName || "图片预览";
    image.referrerPolicy = "no-referrer";
    image.src = button.dataset.attachmentImageUrl;
    image.addEventListener("error", () => {
      if (preview?.contains(image)) preview.textContent = "图片无法加载";
    });
    preview.append(image);
    document.body.append(preview);
    const rect = button.getBoundingClientRect();
    const box = preview.getBoundingClientRect();
    preview.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - box.width - 8))}px`;
    const top = rect.top >= box.height + 15 ? rect.top - box.height - 7 : rect.bottom + 7;
    preview.style.top = `${Math.max(8, Math.min(top, window.innerHeight - box.height - 8))}px`;
  };
  listen(root, "mouseover", (event) => {
    const button = event.target.closest?.(selector);
    if (button) show(button);
  });
  listen(root, "mouseout", (event) => {
    if (active && !active.contains(event.relatedTarget)) hide();
  });
  listen(root, "focusin", (event) => {
    const button = event.target.closest?.(selector);
    if (button) show(button);
  });
  listen(root, "focusout", hide);
  listen(root, "scroll", hide, true);
  listen(window, "resize", hide);
  listen(root, "click", (event) => {
    const button = event.target.closest?.(selector);
    if (!button) return;
    event.preventDefault();
    hide();
    // view_image resolves the entire result (including authenticated Skill
    // images) before opening the same dialog; keep hover cleanup shared.
    if (button.dataset.viewImagePreview) return;
    const area = button.closest(".trajectory-summary-links") || button.closest(".msg-attachments") || button.parentElement;
    const buttons = [...area.querySelectorAll(selector)];
    const items = buttons.map((item) => ({ previewUrl: item.dataset.attachmentImageUrl, name: item.dataset.attachmentImageName }));
    openImage(items[buttons.indexOf(button)], items);
  });
  return () => { hide(); listeners.splice(0).forEach((remove) => remove()); };
}
