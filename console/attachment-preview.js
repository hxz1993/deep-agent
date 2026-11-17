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

const imageLink = /\.(?:png|jpe?g|gif|webp|avif|bmp|svg)$/i;

function markdownImageLink(target) {
  const link = target.closest?.(".markdown-body a[href]");
  if (!link) return null;
  try {
    const url = new URL(link.href);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || !imageLink.test(url.pathname)) return null;
    return link;
  } catch {
    return null;
  }
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
  const position = () => {
    const rect = active.getBoundingClientRect();
    const box = preview.getBoundingClientRect();
    preview.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - box.width - 8))}px`;
    const top = rect.top >= box.height + 15 ? rect.top - box.height - 7 : rect.bottom + 7;
    preview.style.top = `${Math.max(8, Math.min(top, window.innerHeight - box.height - 8))}px`;
  };
  const show = (button) => {
    if (active === button) return;
    hide();
    active = button;
    preview = document.createElement("div");
    preview.className = "attachment-hover-preview";
    preview.setAttribute("role", "tooltip");
    const image = document.createElement("img");
    image.alt = button.dataset.attachmentImageName || button.textContent?.trim() || "图片预览";
    image.referrerPolicy = "no-referrer";
    image.src = button.dataset.attachmentImageUrl || button.href;
    image.addEventListener("error", () => {
      if (preview?.contains(image)) preview.textContent = "图片无法加载";
    });
    preview.append(image);
    document.body.append(preview);
    position();
  };
  listen(root, "mouseover", (event) => {
    const button = event.target.closest?.(selector) || markdownImageLink(event.target);
    if (button) show(button);
  });
  listen(root, "mouseout", (event) => {
    if (active && !active.contains(event.relatedTarget)) hide();
  });
  listen(root, "focusin", (event) => {
    const button = event.target.closest?.(selector) || markdownImageLink(event.target);
    if (button) show(button);
  });
  listen(root, "focusout", hide);
  listen(root, "scroll", () => {
    // Focusing an offscreen link scrolls it into view after focusin.
    if (active && document.activeElement === active) position();
    else hide();
  }, true);
  listen(window, "resize", hide);
  function openMarkdownImage(image) {
    hide();
    const gallery = [...image.closest(".markdown-body").querySelectorAll("img[data-markdown-image]")];
    const items = gallery.map(item => ({ previewUrl: item.src, name: item.alt || "图片" }));
    openImage(items[gallery.indexOf(image)], items);
  }
  listen(root, "keydown", (event) => {
    const image = event.target.closest?.("img[data-markdown-image]");
    if (image && (event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      openMarkdownImage(image);
    }
  });
  listen(root, "error", (event) => {
    const image = event.target;
    if (!image.matches?.("img[data-markdown-image]") || image.nextElementSibling?.classList.contains("markdown-image-fallback")) return;
    const link = document.createElement("a");
    link.className = "markdown-image-fallback";
    link.href = image.src;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.referrerPolicy = "no-referrer";
    link.textContent = "打开原图";
    image.after(link);
  }, true);
  listen(root, "click", (event) => {
    const image = event.target.closest?.("img[data-markdown-image]");
    if (image && !image.closest("a")) {
      openMarkdownImage(image);
      return;
    }
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
