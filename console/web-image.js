function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function safePublicImageUrl(value) {
  if (typeof value !== "string" || !value.trim()) return null;
  try {
    const parsed = new URL(value);
    if (!["http:", "https:"].includes(parsed.protocol)) return null;
    if (parsed.username || parsed.password) return null;
    return parsed.href;
  } catch {
    return null;
  }
}

export function findWebImageReference(value) {
  if (Array.isArray(value)) {
    for (const child of value) {
      const found = findWebImageReference(child);
      if (found) return found;
    }
    return null;
  }
  if (!value || typeof value !== "object") return null;
  if (["web-image-reference", "image-url"].includes(value.kind)) {
    const url = safePublicImageUrl(value.url);
    if (url) {
      return {
        url,
        mediaType: typeof value.media_type === "string" ? value.media_type : null,
        forceDownload: Boolean(value.force_download),
      };
    }
  }
  for (const child of Object.values(value)) {
    const found = findWebImageReference(child);
    if (found) return found;
  }
  return null;
}

export function renderWebImagePreview(value, { className = "" } = {}) {
  const image = findWebImageReference(value);
  if (!image) return "";
  const url = escapeHtml(image.url);
  const media = image.mediaType ? `<span>${escapeHtml(image.mediaType)}</span>` : "";
  const classes = ["web-image-preview", className].filter(Boolean).join(" ");
  return `<figure class="${escapeHtml(classes)}">
    <div class="web-image-preview-frame">
      <img src="${url}" alt="Web Image View 预览" loading="lazy" decoding="async" referrerpolicy="no-referrer" />
      <p class="web-image-preview-fallback">图片预览加载失败，请打开原图查看。</p>
    </div>
    <figcaption>${media}<a href="${url}" target="_blank" rel="noopener noreferrer" referrerpolicy="no-referrer">打开原图</a></figcaption>
  </figure>`;
}

export function bindWebImagePreviewFallback(root = globalThis.document) {
  if (!root?.addEventListener || root.__deepAgentWebImageFallbackBound) return;
  root.__deepAgentWebImageFallbackBound = true;
  root.addEventListener("error", (event) => {
    const image = event.target;
    if (!image?.matches?.(".web-image-preview img")) return;
    image.closest(".web-image-preview")?.classList.add("load-failed");
  }, true);
}
