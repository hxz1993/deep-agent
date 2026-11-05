import assert from 'node:assert/strict';
import test from 'node:test';
import { Element } from './helpers/presenter-dom.mjs';
import { createConversationPresenter } from '../../../console/conversation-presenter.js';
import { createRenderBatch, renderIconsWithin } from '../../../console/render-scheduler.js';

function harness({ readOnly = false } = {}) {
  const host = new Element();
  const timeouts = new Map(), intervals = new Map(), frames = new Map();
  const markdown = [], json = [];
  let next = 1;
  const window = {
    setTimeout(fn, delay) { const id = next++; timeouts.set(id, { fn, delay }); return id; },
    clearTimeout(id) { timeouts.delete(id); },
    setInterval(fn) { const id = next++; intervals.set(id, fn); return id; },
    clearInterval(id) { intervals.delete(id); },
    requestAnimationFrame(fn) { const id = next++; frames.set(id, fn); return id; },
    serviceUrl(path) { return `/test/${path}`; },
    DeepAgentMarkdown: {
      replaceBody(root, source, options) { markdown.push({ source, options }); root.textContent = source; },
      render(source) { markdown.push({ source }); return String(source); },
      renderBody(source) { markdown.push({ source }); return `<div class="markdown-body">${source}</div>`; },
      captureCodeState() { return []; }, restoreCodeState() {}, renderDiagrams() {},
    },
    DeepAgentJson: {
      renderBlock(value, options = {}) { json.push(value); return `<pre class="${options.className || 'json-view'}">${JSON.stringify(value)}</pre>`; },
      updateBlock(pre, value) { json.push(value); pre.textContent = JSON.stringify(value); },
    },
  };
  const factory = createConversationPresenter({ messages: host, readOnly, window,
    document: { createElement: tag => new Element(tag), createComment: () => new Element('#comment') },
  });
  const assistant = factory.createAssistantMessage(readOnly ? '2026-11-05T00:00:00Z' : null);
  return { host, assistant, factory, window, markdown, json, timeouts, intervals, frames,
    tick() { const pending = [...timeouts.values()]; timeouts.clear(); pending.forEach(timer => timer.fn()); },
  };
}

test('75ms render batch merges bursts and flush/dispose cancel pending callbacks', () => {
  const timers = new Map(); let calls = 0, next = 0;
  const batch = createRenderBatch(() => calls++, {
    schedule(fn, delay) { assert.equal(delay, 75); timers.set(++next, fn); return next; },
    cancel(id) { timers.delete(id); },
  });
  for (let i = 0; i < 1000; i++) batch.request();
  assert.equal(timers.size, 1); assert.equal(calls, 0);
  batch.flush(); assert.equal(calls, 1); assert.equal(timers.size, 0);
  batch.request(); batch.dispose(); assert.equal(timers.size, 0);
  batch.request(); batch.flush(); assert.equal(calls, 1);
});

test('streaming text burst is buffered once, then final text is complete and highlighted', () => {
  const h = harness();
  const text = '中文 **正文**\n'.repeat(100);
  for (const character of text) h.assistant.appendText(character);
  assert.equal(h.markdown.length, 0);
  assert.equal(h.timeouts.size, 1);
  h.tick();
  assert.equal(h.markdown.length, 1);
  assert.equal(h.markdown[0].source, text);
  assert.equal(h.markdown[0].options.highlight, false);
  h.assistant.appendText('末尾');
  h.assistant.complete(text + '末尾');
  assert.equal(h.timeouts.size, 0);
  assert.equal(h.host.querySelector('.assistant-final').textContent, text + '末尾');
  assert.notEqual(h.markdown.at(-1).options?.highlight, false);
  assert.equal(h.intervals.size, 0);
});

test('thinking burst and semantic boundaries preserve every segment in original order', () => {
  const h = harness();
  h.assistant.appendText('准备步骤');
  h.assistant.appendThinking('思考甲');
  h.assistant.appendThinking('思考乙');
  assert.equal(h.timeouts.size, 1);
  h.assistant.addToolCall({ tool: 'query', tool_call_id: 'one', args: { id: 1 } });
  assert.equal(h.host.querySelector('.assistant-process-text').textContent, '准备步骤');
  assert.equal(h.host.querySelector('.thinking-content').textContent, '思考甲思考乙');
  assert.equal(h.timeouts.size, 0);
  h.assistant.addToolResult({ tool: 'query', tool_call_id: 'one', result: '结果' });
  h.assistant.appendText('最终结论');
  h.assistant.complete('最终结论');
  assert.equal(h.host.querySelector('.assistant-final').textContent, '最终结论');
  const body = h.host.querySelector('.assistant-process-body');
  assert.deepEqual(body.children.map(child => child.className), ['assistant-process-text markdown', 'assistant-thinking', 'tool-block']);
});

test('error and stop flush trailing text, dispose drops work on abandoned presenters', () => {
  for (const stop of [false, true]) {
    const h = harness(); h.assistant.appendText('尚未完成的尾部');
    h.assistant.finalizeThinking(); h.assistant.finalizeText();
    if (stop) h.assistant.markInterrupted();
    assert.equal(h.host.querySelector('.assistant-final').textContent, '尚未完成的尾部');
    assert.equal(h.timeouts.size, 0); assert.equal(h.intervals.size, 0);
    assert.equal(h.host.classList.contains('interrupted'), false);
    assert.equal(h.host.querySelectorAll('.message-run-status').length, stop ? 1 : 0);
  }
  const h = harness(); h.assistant.appendText('离开的会话'); h.assistant.dispose(); h.tick();
  assert.equal(h.markdown.length, 0); assert.equal(h.timeouts.size, 0); assert.equal(h.intervals.size, 0);
});

test('saved history renders immediately without starting timers or delaying full contents', () => {
  const h = harness({ readOnly: true });
  h.assistant.appendThinking('已保存思考'); h.assistant.finalizeThinking();
  h.assistant.appendText('已保存正文');
  assert.equal(h.timeouts.size, 0); assert.equal(h.intervals.size, 0);
  assert.equal(h.host.querySelector('.thinking-content').textContent, '已保存思考');
  assert.equal(h.host.querySelector('.assistant-final').textContent, '已保存正文');
  assert.equal(h.markdown.at(-1).options.highlight, true);
});

test('folded tool data is parsed only when expanded while file download stays available', () => {
  const h = harness();
  const result = { large: 'x'.repeat(100000) };
  h.assistant.addToolCall({ tool: 'generate', tool_call_id: 'file', args: { query: 'a'.repeat(20000) } });
  h.assistant.addToolResult({ tool: 'generate', tool_call_id: 'file', result,
    artifacts: [{ kind: 'sandbox_output', file_id: 'local_fixture', file_name: 'report.txt', mime: 'text/plain', byte_size: 100000 }],
  });
  assert.equal(h.json.length, 0);
  assert.equal(h.host.querySelectorAll('.args').length, 0);
  assert.equal(h.host.querySelectorAll('.generated-file-item').length, 1);
  assert.match(h.host.querySelector('[data-tool-artifacts]').innerHTML, /download/);
  const block = h.host.querySelector('.tool-block');
  block.querySelector('.tool-head').click();
  assert.equal(h.json.length, 2); assert.equal(h.json[1], result);
  block.querySelector('.tool-head').click(); block.querySelector('.tool-head').click();
  assert.equal(h.json.length, 2, 'reopening unchanged details does not repeat parsing');
  h.assistant.complete('完成');
});

test('tool updates while folded use latest args/result and preserve truthful source values', () => {
  const h = harness();
  const call = { tool: 'query', tool_call_id: 'one' };
  h.assistant.addToolCall({ ...call, args: { value: 'old' } });
  h.assistant.addToolResult({ ...call, result: { value: 'old' } });
  h.assistant.addToolCall({ ...call, args: { value: 'new' } });
  h.assistant.addToolResult({ ...call, result: { value: 'new', empty: null, flag: false, count: 0 } });
  assert.equal(h.json.length, 0);
  h.host.querySelector('.tool-head').click();
  assert.deepEqual(h.json, [{ value: 'new' }, { value: 'new', empty: null, flag: false, count: 0 }]);
  h.assistant.complete('完成');
});

test('icons refresh uses only requested host and keeps placeholder attributes', () => {
  const attributes = { 'data-lucide': 'file-output', class: 'small', 'aria-hidden': 'true' };
  let replaced, definition;
  const placeholder = {
    getAttribute(name) { return attributes[name]; },
    attributes: Object.entries(attributes).map(([name, value]) => ({ name, value })),
    parentNode: { replaceChild(svg, old) { assert.equal(old, placeholder); replaced = svg; } },
  };
  const host = { querySelectorAll(selector) { assert.equal(selector, 'i[data-lucide]'); return [placeholder]; } };
  renderIconsWithin(host, { icons: { FileOutput: ['svg', { viewBox: '0 0 24 24' }, []] }, createElement(value) { definition = value; return {}; } });
  assert.ok(replaced); assert.equal(definition[1]['aria-hidden'], 'true');
  assert.match(definition[1].class, /lucide-file-output small/);
});

test('disposing during a paused icon frame drops the message subtree without dropping other messages', () => {
  const h = harness();
  h.window.lucide = { icons: {}, createElement() {} };
  const oldRow = h.assistant.row;
  oldRow.contains = node => {
    for (let current = node; current; current = current.parentNode) if (current === oldRow) return true;
    return false;
  };
  h.factory.renderMessageAttachments(oldRow, [{ id: 'old', name: 'old.txt' }]);
  const oldAttachment = oldRow.querySelector('.msg-attachments');
  let oldConnectionChecks = 0, oldScans = 0;
  Object.defineProperty(oldAttachment, 'isConnected', { get() { oldConnectionChecks++; return true; } });
  oldAttachment.querySelectorAll = () => { oldScans++; return []; };

  const active = h.factory.createAssistantMessage();
  h.factory.renderMessageAttachments(active.row, [{ id: 'detached', name: 'detached.txt' }]);
  const detached = active.row.querySelector('.msg-attachments');
  detached.remove();
  Object.defineProperty(detached, 'isConnected', { value: false });
  let activeScans = 0, detachedScans = 0;
  Object.defineProperty(active.row, 'isConnected', { value: true });
  active.row.querySelectorAll = () => { activeScans++; return []; };
  detached.querySelectorAll = () => { detachedScans++; return []; };

  assert.equal(h.frames.size, 1, 'all roots share one still-paused frame');
  h.assistant.dispose();
  const frames = [...h.frames.values()]; h.frames.clear(); frames.forEach(frame => frame());
  assert.equal(oldConnectionChecks, 0, 'disposed descendants were removed from the queue before it ran');
  assert.equal(oldScans, 0);
  assert.equal(detachedScans, 0, 'disconnected roots are not scanned on the delayed frame');
  assert.equal(activeScans, 1, 'the other live message keeps its pending icon refresh');
  active.dispose();
  assert.equal(h.intervals.size, 0);
});
