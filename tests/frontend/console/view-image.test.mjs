import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { findViewImageResult, renderViewImageResult, bindViewImagePreviews } from '../../../console/view-image.js';

const image = (resource, rest = {}) => ({ resource, name: '图 A', format: 'png', status: 'ready', width: 100, height: 60, bytes: 99, ...rest });
const batch = (items, status = 'ready') => ({ kind: 'view-image-result', status, items });
const options = { toolName: 'view_image', baseURI: 'https://cloud.example/prefix/deepfos-server/deep-agent-server1-0/console/' };

test('view_image only trusts the exact tool identity and bounded result envelope', () => {
  const value = batch([image({ type: 'url', url: 'https://example.com/a.png' })]);
  assert.equal(findViewImageResult(value, { toolName: 'mcp_untrusted' }), null);
  assert.equal(findViewImageResult({ nested: value }, options), null);
  assert.equal(findViewImageResult([value, { arbitrary: true }], options), value);
  assert.equal(findViewImageResult(null, { ...options, metadata: { view_image: value } }), value);
  assert.equal(renderViewImageResult(value, { toolName: 'mcp_untrusted' }), '');
});

test('mixed batch retains all pages and failures without pretending viewed files were generated', () => {
  const value = batch([
    image({ type: 'attachment', file_id: 'local_photo' }),
    image({ type: 'url', url: 'https://example.com/a.pdf' }, { name: '合同', format: 'pdf', page: 6, page_count: 12 }),
    image({ type: 'url', url: 'https://example.com/b.gif' }, { scaled: true, gif_first_frame: true }),
    image({ type: 'attachment', file_id: 'local_bad' }, { status: 'error', name: '坏文件', error: '<无文字>' }),
  ], 'partial');
  const html = renderViewImageResult(value, options);
  assert.equal((html.match(/<li class="view-image-item/g) || []).length, 4);
  assert.match(html, /部分成功/);
  assert.match(html, /第 6 页 \/ 共 12 页/);
  assert.match(html, /GIF 首帧/);
  assert.match(html, /已缩放/);
  assert.match(html, /&lt;无文字&gt;/);
  assert.doesNotMatch(html, /generated-file|sandbox_output|<img|base64/i);
  assert.match(html, /data-view-image-results/);
});

test('attachment and skill sources reuse authorized routes and PDF only offers original source', () => {
  const value = batch([
    image({ type: 'attachment', file_id: 'local_picture' }),
    image({ type: 'skill', skill_code: 'test/skill', path: 'assets/a b.png' }),
    image({ type: 'skill', skill_code: 'test/skill', path: 'assets/doc.pdf' }, { format: 'pdf', page: 1, page_count: 2 }),
  ]);
  const html = renderViewImageResult(value, { ...options, attachmentUrl: (file) => `/authorized/${file.id}` });
  assert.match(html, /data-attachment-image-url="\/authorized\/local_picture"/);
  assert.match(html, /data-view-image-skill-code="test\/skill"/);
  assert.match(html, /下载Skill原包/);
  assert.match(html, /skills\/test%2Fskill\/package/);
  assert.doesNotMatch(html, /screenshot|pages\/1/);
  assert.equal((html.match(/data-view-image-preview=/g) || []).length, 2);
});

test('unsafe image references cannot become links and labels remain escaped', () => {
  for (const url of ['javascript:alert(1)', 'data:image/png;base64,x', 'https://user:secret@example.com/a.png']) {
    assert.doesNotMatch(renderViewImageResult(batch([image({ type: 'url', url })]), options), /href=|data-attachment-image-url=/);
  }
  assert.match(renderViewImageResult(batch([image({ type: 'url', url: 'https://example.com/a.png' }, { name: '<script>' })]), options), /&lt;script&gt;/);
});

function previewHarness({ Observer = null } = {}) {
  const listeners = new Map(); const dialogListeners = new Map();
  const root = { addEventListener: (type, fn) => listeners.set(type, fn), removeEventListener: () => {}, contains: (node) => node.isConnected };
  const dialog = { open: false, addEventListener: (type, fn) => dialogListeners.set(type, fn), removeEventListener: () => {},
    close() { this.open = false; dialogListeners.get('close')?.(); } };
  const group = { querySelectorAll: () => buttons };
  const buttons = ['one', 'two'].map((name) => ({
    isConnected: true, dataset: { viewImagePreview: '1', viewImageSkillCode: 's', viewImageSkillPath: `assets/${name}.png`, attachmentImageName: name },
    closest: (selector) => selector === '[data-view-image-results]' ? group : null,
    setAttribute() {}, removeAttribute() {},
  }));
  const revoked = []; const requests = []; const opened = [];
  let resolveFetch;
  const fetcher = (url, init) => { requests.push({ url, init }); return new Promise((resolve) => { resolveFetch = resolve; }); };
  const dispose = bindViewImagePreviews({ root, dialog, openImage: (...args) => { dialog.open = true; opened.push(args); }, fetcher, serviceUrl: (path) => `/service/${path}`, createObjectURL: () => `blob:${requests.length}`, revokeObjectURL: (url) => revoked.push(url), Observer });
  const click = (button) => listeners.get('click')({ target: { closest: () => button }, preventDefault() {}, stopImmediatePropagation() {} });
  return { buttons, dialog, dialogListeners, dispose, click, requests, opened, revoked, resolve: (response) => resolveFetch(response) };
}

test('Skill preview uses authenticated fetch, same-result grouping and cleans blob on close', async () => {
  const h = previewHarness();
  // A single Skill image plus a regular image in this result.
  h.buttons[1].dataset = { viewImagePreview: '1', attachmentImageName: 'direct', attachmentImageUrl: 'https://example.com/direct.png' };
  const pending = h.click(h.buttons[0]);
  assert.match(h.requests[0].url, /resources\/assets\/one.png\?raw=true/);
  h.resolve({ ok: true, blob: async () => ({ type: 'image/png' }) });
  await pending;
  assert.equal(h.opened.length, 1);
  assert.equal(h.opened[0][1].length, 2);
  assert.equal(h.opened[0][1][1].previewUrl, 'https://example.com/direct.png');
  h.dialogListeners.get('close')();
  assert.deepEqual(h.revoked, ['blob:1']);
  h.dispose();
});

test('closing or leaving a Skill preview aborts fetch and ignores late responses', async () => {
  const h = previewHarness(); h.buttons.splice(1);
  const pending = h.click(h.buttons[0]);
  h.dispose();
  assert.equal(h.requests[0].init.signal.aborted, true);
  h.resolve({ ok: true, blob: async () => ({ type: 'image/png' }) });
  await pending;
  assert.equal(h.opened.length, 0);
  assert.deepEqual(h.revoked, []);
});

test('conversation and trajectory share batch presentation and skip first-file fallback only for view_image', async () => {
  const [conversation, trajectory] = await Promise.all(['conversation-presenter.js', 'trajectory.js'].map((name) => readFile(new URL(`../../../console/${name}`, import.meta.url), 'utf8')));
  for (const source of [conversation, trajectory]) {
    assert.match(source, /renderViewImageResult\(/);
    assert.match(source, /findViewImageResult\(/);
  }
});

test('authorized source links keep diagnostic ownership and gateway prefixes', () => {
  const value = batch([image({ type: 'attachment', file_id: 'local_s/a' }), image({ type: 'attachment', file_id: 'platform&file' })]);
  const html = renderViewImageResult(value, options);
  assert.match(html, /deep-agent-server1-0\/api\/v1\/local-files\/local_s%2Fa\/download/);
  assert.match(html, /prefix\/deepfos-server\/platform-file-server\/files\/download\?id=platform%26file/);
});

test('unsupported Skill paths cannot request raw resources or package links', () => {
  for (const path of ['../a.png', '/tmp/a.png', 'assets/../a.png', 'assets\\a.png']) {
    const html = renderViewImageResult(batch([image({ type: 'skill', skill_code: 's', path })]), options);
    assert.doesNotMatch(html, /data-view-image-preview=|href=/);
  }
});

test('a failed authenticated preview stays closed and can be retried', async () => {
  const h = previewHarness(); h.buttons.splice(1);
  const failed = h.click(h.buttons[0]); h.resolve({ ok: false }); await failed;
  assert.equal(h.opened.length, 0);
  const retried = h.click(h.buttons[0]);
  assert.equal(h.requests.length, 2);
  h.resolve({ ok: true, blob: async () => ({ type: 'image/png' }) }); await retried;
  assert.equal(h.opened.length, 1);
  h.dispose();
  assert.equal(h.revoked.length, 1);
});

test('last clicked item wins while a shared authenticated preview is loading', async () => {
  const h = previewHarness();
  h.buttons[1].dataset = { viewImagePreview: '1', attachmentImageName: 'direct', attachmentImageUrl: 'https://example.com/direct.png' };
  const first = h.click(h.buttons[0]);
  const last = h.click(h.buttons[1]);
  assert.equal(h.requests.length, 1);
  h.resolve({ ok: true, blob: async () => ({ type: 'image/png' }) });
  await Promise.all([first, last]);
  assert.equal(h.opened.length, 1);
  assert.equal(h.opened[0][0].name, 'direct');
  h.dispose();
});

test('modal close cancels pending authenticated loads without reopening a stale dialog', async () => {
  const h = previewHarness(); h.buttons.splice(1);
  const pending = h.click(h.buttons[0]);
  h.dialogListeners.get('close')();
  assert.equal(h.requests[0].init.signal.aborted, true);
  h.resolve({ ok: true, blob: async () => ({ type: 'image/png' }) }); await pending;
  assert.equal(h.opened.length, 0);
  assert.deepEqual(h.revoked, []);
  h.dispose();
});

test('rerender releases Skill blobs and disconnects lifecycle observation on disposal', async () => {
  let prune; let disconnected = false;
  class Observer { constructor(callback) { prune = callback; } observe() {} disconnect() { disconnected = true; } }
  const h = previewHarness({ Observer }); h.buttons.splice(1);
  const pending = h.click(h.buttons[0]);
  h.resolve({ ok: true, blob: async () => ({ type: 'image/png' }) }); await pending;
  h.buttons[0].isConnected = false; prune();
  assert.deepEqual(h.revoked, ['blob:1']);
  assert.equal(h.buttons[0].dataset.attachmentImageUrl, undefined);
  h.dispose();
  assert.equal(disconnected, true);
  assert.deepEqual(h.revoked, ['blob:1']);
});

test('a late Skill preview does not open after its connected pane has been hidden', async () => {
  const h = previewHarness(); h.buttons.splice(1);
  let hidden = false;
  h.buttons[0].getClientRects = () => hidden ? [] : [{}];
  try {
    const pending = h.click(h.buttons[0]);
    hidden = true;
    h.resolve({ ok: true, blob: async () => ({ type: 'image/png' }) });
    await pending;
    assert.equal(h.buttons[0].isConnected, true);
    assert.equal(h.opened.length, 0);
    assert.equal(h.buttons[0].dataset.attachmentImageUrl, undefined);
  } finally { h.dispose(); }
});

test('hiding the active pane aborts a pending Skill load or releases its prepared blob', async () => {
  for (const prepared of [false, true]) {
    const observers = [];
    class Observer {
      constructor(callback) { this.callback = callback; this.targets = new Map(); observers.push(this); }
      observe(target, options) { this.targets.set(target, options); }
      disconnect() { this.targets.clear(); }
    }
    const h = previewHarness({ Observer }); h.buttons.splice(1);
    const pane = {};
    let hidden = false;
    h.buttons[0].parentElement = pane;
    h.buttons[0].getClientRects = () => hidden ? [] : [{}];
    try {
      const pending = h.click(h.buttons[0]);
      const response = { ok: true, blob: async () => ({ type: 'image/png' }) };
      if (prepared) { h.resolve(response); await pending; }
      hidden = true;
      for (const observer of observers) {
        if (observer.targets.get(pane)?.attributes) observer.callback([{ target: pane, type: 'attributes', attributeName: 'class' }]);
      }
      const abortedOnHide = h.requests[0].init.signal.aborted;
      if (!prepared) { h.resolve(response); await pending; }
      assert.equal(abortedOnHide, true);
      assert.equal(h.opened.length, prepared ? 1 : 0);
      assert.equal(h.dialog.open, false);
      assert.deepEqual(h.revoked, prepared ? ['blob:1'] : []);
      assert.equal(h.buttons[0].dataset.attachmentImageUrl, undefined);
      assert.equal(observers.some(({ targets }) => [...targets.values()].some((options) => options.attributes && options.subtree)), false);
    } finally { h.dispose(); }
  }
});
