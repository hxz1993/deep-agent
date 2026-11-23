import { attachmentDownloadUrl } from "./attachment-preview.js?v=20261103-view-image1";
import { escapeHtml } from "./diagnostics-ui.js";

export function formatFileBytes(bytes) {
  if (typeof bytes !== "number" || !Number.isFinite(bytes) || bytes < 0) return "";
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${Math.round(bytes)} B`;
}

function fileIconType(name = "") {
  const extension = name.toLowerCase().match(/\.([^.]+)$/)?.[1];
  if (extension === "jpeg") return "jpg";
  if (extension === "htm") return "html";
  return /^(csv|xls|xlsx|html|json|docx?|jpg|pdf|png)$/.test(extension) ? extension : "file";
}

function sameOriginUrl(value, base) {
  if (!value || !base) return null;
  try {
    const url = new URL(value, base);
    return /^(https?:)$/.test(url.protocol) && url.origin === new URL(base).origin
      && !url.username && !url.password ? url.href : null;
  } catch {
    return null;
  }
}

export function renderReplyFileCards(files, {
  base = globalThis.document?.baseURI,
  attachmentUrl = (file) => attachmentDownloadUrl(file, base),
} = {}) {
  if (!Array.isArray(files)) return "";
  const seen = new Set();
  const cards = [];
  for (const file of files) {
    if (!file?.id || seen.has(file.id)) continue;
    seen.add(file.id);
    const name = escapeHtml(file.name || file.id);
    const size = formatFileBytes(file.byte_size);
    const preview = sameOriginUrl(file.preview_url, base);
    const download = sameOriginUrl(attachmentUrl(file), base);
    cards.push(`<div class="reply-file-card">
      <span class="reply-file-icon" data-file-type="${fileIconType(file.name || "")}" aria-hidden="true"></span>
      <span class="reply-file-info"><strong title="${name}">${name}</strong>${size ? `<span>${size}</span>` : ""}</span>
      <span class="reply-file-actions">${preview ? `<a href="${escapeHtml(preview)}" target="_blank" rel="noopener noreferrer" aria-label="预览 ${name}">预览</a>` : ""}
      ${download ? `<a href="${escapeHtml(download)}" download aria-label="下载 ${name}">下载</a>` : ""}</span>
    </div>`);
  }
  return cards.length ? `<div class="reply-file-cards">${cards.join("")}</div>` : "";
}
