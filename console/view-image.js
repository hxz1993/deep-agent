import { attachmentDownloadUrl } from './attachment-preview.js?v=20261103-view-image1';

const escapeHtml = (value) => String(value ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const icon = (name) => `<i data-lucide="${name}" aria-hidden="true"></i>`;
const IMAGE_FORMATS = new Set(['png', 'jpeg', 'webp', 'gif']);
const PREVIEW_SELECTOR = '[data-view-image-preview]';

// Tool identity is part of the trust boundary. An MCP result with the same JSON
// fields must remain ordinary JSON, never become an authorized file preview.
export function findViewImageResult(value, { toolName, metadata } = {}) {
  if (toolName !== 'view_image') return null;
  const candidates = [Array.isArray(value) ? value[0] : value, metadata?.view_image];
  return candidates.find((candidate) => candidate?.kind === 'view-image-result' && Array.isArray(candidate.items)) || null;
}

function publicUrl(value) {
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}

function skillPath(value) {
  const path = String(value || '');
  return path && !path.startsWith('/') && !path.includes('\\') && !path.split('/').some((part) => !part || part === '.' || part === '..') ? path : null;
}

function skillPackageUrl(code, baseURI) {
  return code && baseURI ? new URL(`api/v1/skills/${encodeURIComponent(code)}/package`, new URL('../', baseURI)).href : null;
}

export function renderViewImageResult(value, { toolName, metadata, attachmentUrl = attachmentDownloadUrl, baseURI = globalThis.document?.baseURI } = {}) {
  const result = findViewImageResult(value, { toolName, metadata });
  if (!result) return '';
  const rows = result.items.map((item) => {
    const resource = item?.resource || {};
    const format = String(item?.format || '').toLowerCase();
    const isPdf = format === 'pdf';
    const ready = item?.status === 'ready';
    const name = item?.name || resource.path || resource.file_id || resource.url || '图片';
    let href = null; let source = ''; let previewAttributes = '';
    if (resource.type === 'attachment' && resource.file_id) {
      href = attachmentUrl({ id: resource.file_id, name, mime: isPdf ? 'application/pdf' : `image/${format}` }, baseURI);
      source = '附件';
    } else if (resource.type === 'url') {
      href = publicUrl(resource.url); source = '网络';
    } else if (resource.type === 'skill' && resource.skill_code && skillPath(resource.path)) {
      href = skillPackageUrl(resource.skill_code, baseURI); source = 'Skill';
      if (ready && IMAGE_FORMATS.has(format)) {
        previewAttributes = `data-view-image-skill-code="${escapeHtml(resource.skill_code)}" data-view-image-skill-path="${escapeHtml(resource.path)}"`;
      }
    }
    if (ready && IMAGE_FORMATS.has(format) && href && resource.type !== 'skill') previewAttributes = `data-attachment-image-url="${escapeHtml(href)}"`;
    const preview = previewAttributes
      ? `<button type="button" class="view-image-preview attachment-image-trigger" data-view-image-preview="1" ${previewAttributes} data-attachment-image-name="${escapeHtml(name)}" aria-label="预览图片 ${escapeHtml(name)}">${icon('image')}<span>${escapeHtml(name)}</span></button>`
      : `<span class="view-image-name">${icon(isPdf ? 'file-text' : 'image')}<span>${escapeHtml(name)}</span></span>`;
    const details = [source, format.toUpperCase()];
    if (isPdf && Number.isInteger(item.page)) details.push(`第 ${item.page} 页${Number.isInteger(item.page_count) ? ` / 共 ${item.page_count} 页` : ''}`);
    if (ready && item.width && item.height) details.push(`${item.width} × ${item.height}`);
    if (item.scaled) details.push('已缩放');
    if (item.gif_first_frame) details.push('GIF 首帧');
    const label = resource.type === 'skill' ? '下载Skill原包' : isPdf ? '打开原文件' : '打开原图';
    const link = href ? `<a href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer" referrerpolicy="no-referrer">${icon(resource.type === 'skill' || resource.type === 'attachment' ? 'download' : 'external-link')}<span>${label}</span></a>` : '';
    const failure = ready ? '' : `<p class="view-image-error">${escapeHtml(item?.error || '未能准备此图片，请检查来源后重试。')}</p>`;
    return `<li class="view-image-item${ready ? '' : ' is-error'}"><div class="view-image-item-heading">${preview}${link}</div><div class="view-image-item-meta">${escapeHtml(details.filter(Boolean).join(' · '))}</div>${failure}<p class="view-image-error hidden" data-view-image-preview-error></p></li>`;
  }).join('');
  const success = result.items.filter((item) => item?.status === 'ready').length;
  const status = result.status === 'partial' ? '部分成功' : success ? '图片已准备' : '未能准备图片';
  return `<section class="view-image-results" data-view-image-results><div class="view-image-result-status">${escapeHtml(status)} · ${success} / ${result.items.length} 项</div><ul>${rows}</ul></section>`;
}

// Skill image bytes use the existing authenticated raw resource endpoint. Blob
// URLs are view-only and are released when the preview leaves the active pane.
export function bindViewImagePreviews({
  root = globalThis.document, dialog, openImage,
  fetcher = (...args) => globalThis.daFetch(...args),
  serviceUrl = (path) => globalThis.serviceUrl(path),
  createObjectURL = (blob) => URL.createObjectURL(blob),
  revokeObjectURL = (url) => URL.revokeObjectURL(url),
  Observer = globalThis.MutationObserver,
} = {}) {
  const entries = new Map();
  let disposed = false; let generation = 0; let activeButton = null;
  const connected = (button) => button.isConnected !== false && (root.contains?.(button) ?? true);
  const visible = (button) => connected(button) && (button.getClientRects?.().length ?? 1) > 0;
  function release(button, entry) {
    entry.controller.abort();
    if (entry.url) {
      revokeObjectURL(entry.url);
      if (button.dataset.attachmentImageUrl === entry.url) delete button.dataset.attachmentImageUrl;
    }
    entries.delete(button);
  }
  function clear() {
    generation += 1;
    activeButton = null;
    visibilityObserver?.disconnect();
    for (const [button, entry] of entries) release(button, entry);
  }
  function dismiss() {
    clear();
    if (dialog?.open) dialog.close();
  }
  function showError(button, message) {
    const node = button.closest?.('.view-image-item')?.querySelector?.('[data-view-image-preview-error]');
    if (node) { node.textContent = message || ''; node.classList.toggle('hidden', !message); }
  }
  async function resolveImage(button) {
    if (!button.dataset.viewImageSkillCode) return button.dataset.attachmentImageUrl ? { previewUrl: button.dataset.attachmentImageUrl, name: button.dataset.attachmentImageName } : null;
    let entry = entries.get(button);
    if (entry) return entry.promise;
    entry = { controller: new AbortController(), url: null };
    entries.set(button, entry);
    showError(button, null);
    button.setAttribute('aria-busy', 'true');
    const timeout = setTimeout(() => entry.controller.abort(), 15000);
    entry.promise = (async () => {
      try {
        const path = skillPath(button.dataset.viewImageSkillPath);
        if (!path) throw new Error('invalid resource');
        const resource = `api/v1/skills/${encodeURIComponent(button.dataset.viewImageSkillCode)}/resources/${path.split('/').map(encodeURIComponent).join('/')}?raw=true`;
        const response = await fetcher(serviceUrl(resource), { signal: entry.controller.signal });
        if (!response.ok) throw new Error('resource unavailable');
        const blob = await response.blob();
        if (!/^image\/(png|jpe?g|webp|gif)$/i.test(blob.type)) throw new Error('unsupported image');
        if (disposed || entry.controller.signal.aborted || entries.get(button) !== entry || !visible(button)) throw new Error('preview no longer current');
        entry.url = createObjectURL(blob);
        button.dataset.attachmentImageUrl = entry.url;
        return { previewUrl: entry.url, name: button.dataset.attachmentImageName };
      } catch {
        if (!disposed && entries.get(button) === entry && visible(button)) showError(button, entry.controller.signal.aborted ? '图片预览加载超时，请重试或下载Skill原包查看。' : '图片预览加载失败，请下载Skill原包查看。');
        if (entries.get(button) === entry) release(button, entry);
        return null;
      } finally { clearTimeout(timeout); if (!entries.has(button) || entries.get(button) === entry) button.removeAttribute('aria-busy'); }
    })();
    return entry.promise;
  }
  async function click(event) {
    const button = event.target.closest?.(PREVIEW_SELECTOR);
    if (!button || disposed || !visible(button)) return;
    event.preventDefault(); event.stopImmediatePropagation();
    const area = button.closest('[data-view-image-results]');
    const previousArea = activeButton?.closest('[data-view-image-results]') || activeButton;
    if (previousArea && previousArea !== (area || button)) clear();
    activeButton = button;
    visibilityObserver?.disconnect();
    // Tab switches can hide a pane without removing it. Watch only the active
    // button's ancestors for visibility changes, leaving the root observer lean.
    for (let node = button; visibilityObserver && node; node = node.parentElement) {
      visibilityObserver.observe(node, { attributes: true, attributeFilter: ['class', 'hidden', 'style'] });
    }
    const requestGeneration = ++generation;
    const buttons = area ? [...area.querySelectorAll(PREVIEW_SELECTOR)] : [button];
    const images = await Promise.all(buttons.map(resolveImage));
    if (disposed || requestGeneration !== generation) return;
    if (!visible(button)) { dismiss(); return; }
    const selected = images[buttons.indexOf(button)];
    if (selected) openImage(selected, images.filter(Boolean));
    else clear();
  }
  const visibilityObserver = Observer ? new Observer(() => {
    if (activeButton && !visible(activeButton)) dismiss();
  }) : null;
  root.addEventListener('click', click);
  dialog?.addEventListener('close', clear);
  const observer = Observer ? new Observer(() => {
    if (activeButton && !connected(activeButton)) dismiss();
    for (const [button, entry] of entries) if (!connected(button)) release(button, entry);
  }) : null;
  observer?.observe(root, { childList: true, subtree: true });
  return () => {
    disposed = true; clear(); observer?.disconnect();
    root.removeEventListener('click', click); dialog?.removeEventListener('close', clear);
  };
}
