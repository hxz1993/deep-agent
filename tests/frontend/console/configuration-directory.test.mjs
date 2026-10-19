import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import { createConfigurationDirectory } from '../../../console/configuration-directory.js';
import { preserveCurrentModel } from '../../../console/model-options.js';

test('shared list deduplicates reads, isolates projections, and invalidates only successful maintenance routes', async () => {
  let requests = 0;
  const notifications = [];
  const directory = createConfigurationDirectory({ request: async () => [{ id: ++requests }], notify: r => notifications.push(r) });
  const [a, b] = await Promise.all([directory.read('/api/v1/agents'), directory.read('/api/v1/agents')]);
  a[0].id = 99;
  assert.equal(b[0].id, 1);
  await directory.read('/api/v1/llm-models?selectable_only=false');
  await directory.read('/api/v1/llm-models');
  assert.equal(requests, 3);
  directory.mutation('https://host/prefix/api/v1/agents/prompt-estimate', 'POST');
  directory.mutation('https://host/prefix/api/v1/llm-models/id/test', 'POST');
  directory.mutation('https://host/prefix/api/v1/agents', 'GET');
  assert.equal(notifications.length, 0);
  directory.mutation('https://host/prefix/api/v1/agents/id', 'PATCH');
  assert.equal((await directory.read('/api/v1/agents'))[0].id, 4);
  assert.deepEqual(notifications, [['agents']]);
});

test('save overtaking an in-flight read never publishes stale data', async () => {
  let finish;
  let count = 0;
  const directory = createConfigurationDirectory({ request: () => ++count === 1 ? new Promise(r => { finish = r; }) : Promise.resolve([{ id: 'new' }]) });
  const old = directory.read('/api/v1/agents');
  directory.invalidate(['agents']);
  const fresh = directory.read('/api/v1/agents');
  finish([{ id: 'old' }]);
  assert.equal((await old)[0].id, 'new');
  assert.equal((await fresh)[0].id, 'new');
});

test('disconnecting a provider invalidates cached Agent and model availability', async () => {
  let requests = 0;
  const notifications = [];
  const directory = createConfigurationDirectory({ request: async () => [{ id: ++requests }], notify: r => notifications.push(r) });
  await directory.read('/api/v1/agents');
  await directory.read('/api/v1/llm-models');
  directory.mutation('https://host/prefix/api/v1/llm-providers/provider/disconnect', 'POST');
  assert.equal((await directory.read('/api/v1/agents'))[0].id, 3);
  assert.equal((await directory.read('/api/v1/llm-models'))[0].id, 4);
  assert.deepEqual(notifications, [['agents', 'models']]);
  directory.mutation('https://host/prefix/api/v1/llm-providers/codex/authorization/flow/verify', 'POST');
  assert.equal(notifications.length, 1); // Temporary verification does not save credentials.
});

test('failed refresh is retryable and a changed identity cannot receive an old result', async () => {
  let identity = 'first';
  let finish;
  const directory = createConfigurationDirectory({ scope: () => identity, request: () => new Promise(r => { finish = r; }) });
  const pending = directory.read('/api/v1/agents');
  identity = 'second'; finish([]);
  await assert.rejects(pending, /上下文已改变/);
  let failures = 0;
  const retry = createConfigurationDirectory({ request: async () => { if (!failures++) throw Error('offline'); return []; } });
  await assert.rejects(retry.read('/api/v1/agents'));
  assert.deepEqual(await retry.read('/api/v1/agents'), []);
});

const source = readFileSync(new URL('../../../console/chat.js', import.meta.url), 'utf8');
const refreshSource = source.slice(source.indexOf('let configurationRefreshRevision ='), source.indexOf('\nfunction currentAgent()', source.indexOf('let configurationRefreshRevision =')));
function harness({ busy = false, manual = false, sessionId = null } = {}) {
  const model = { id: 'm1', name: 'Old', selectable: true };
  const state = { agentId: 'a', agents: [{ id: 'a', name: 'Old agent', model_id: 'm1', model }], llmModelId: 'm1', llmModels: [model], sessionId,
    modelManuallySelected: manual, streaming: busy, pendingAttachments: [{ id: 'attachment' }], abortController: busy ? new AbortController() : null };
  const input = { value: 'unfinished draft' };
  const calls = [];
  let agents = [{ id: 'a', name: 'Renamed agent', model_id: 'm2', model: { id: 'm2', selectable: true } }];
  let models = [{ id: 'm2', selectable: true }];
  const h = vm.createContext({ state, console, preserveCurrentModel,
    el: { agentSelect: { replaceChildren() {}, appendChild() {} }, input },
    document: { createElement: () => ({}) },
    currentAgent: () => state.agents.find(a => a.id === state.agentId),
    selectedLlmModel: () => state.llmModels.find(m => m.id === state.llmModelId),
    defaultLlmModelId: () => state.agents.find(a => a.id === state.agentId)?.model_id || '',
    ensureAgentSelected: () => { state.agentId ||= state.agents[0]?.id || ''; },
    ensureLlmModelSelected: () => { state.llmModelId ||= models[0]?.id || ''; },
    newChat: (_, options) => { calls.push(['new', options]); state.sessionId = null; state.agentId = agents[0]?.id || ''; },
    updateEmptyAgentName() {}, updateAgentSelectState() {}, updateHitlInputState() {}, renderNewChatMenu() {},
    window: { DeepAgentConsoleDirectory: { read: async path => path.endsWith('agents') ? agents : models }, DeepAgentConsoleWorkspace: { renderDirectory() {} } },
  });
  vm.runInContext(refreshSource, h);
  return { h, state, input, calls, setAgents: value => { agents = value; }, setModels: value => { models = value; } };
}
test('automatic refresh preserves a new draft and attachments while following a changed default', async () => {
  const { h, state, input, calls } = harness(); const attachments = state.pendingAttachments;
  await h.refreshConfiguration();
  assert.equal(input.value, 'unfinished draft'); assert.equal(state.pendingAttachments, attachments);
  assert.equal(state.sessionId, null); assert.equal(state.llmModelId, 'm2'); assert.deepEqual(calls, []);
});
test('active stream and manually selected models survive configuration refresh', async () => {
  for (const options of [{ busy: true, sessionId: 'running' }, { manual: true }]) {
    const { h, state, calls } = harness(options); const stream = state.abortController;
    await h.refreshConfiguration();
    assert.equal(state.llmModelId, 'm1'); assert.equal(state.abortController, stream);
    assert.equal(stream?.signal.aborted || false, false);
    assert.equal(state.llmModels.find(m => m.id === 'm1').selectable, false);
    assert.deepEqual(calls, []);
  }
});
test('an unavailable Agent default remains visible instead of silently choosing another model', async () => {
  const { h, state, setModels } = harness();
  setModels([{ id: 'unrelated', selectable: true }]);
  await h.refreshConfiguration();
  assert.equal(state.llmModelId, 'm2');
  assert.equal(state.llmModels.find(model => model.id === 'm2').selectable, false);
  assert.equal(state.llmModels.find(model => model.id === 'unrelated').selectable, true);
});
test('deleted active Agent is retained until idle; idle fallback explicitly preserves draft', async () => {
  const { h, state, setAgents, calls } = harness({ busy: true, sessionId: 'running' });
  setAgents([]); await h.refreshConfiguration();
  assert.equal(state.agentId, 'a'); assert.equal(state.sessionId, 'running'); assert.deepEqual(calls, []);
  state.streaming = false; state.abortController = null;
  await h.refreshConfiguration();
  assert.equal(calls.length, 1); assert.equal(calls[0][1].preserveDraft, true);
});
test('failed background refresh leaves the complete current state untouched', async () => {
  const { h, state, input } = harness({ busy: true }); const before = JSON.stringify(state);
  h.window.DeepAgentConsoleDirectory.read = async () => { throw Error('offline'); };
  await h.refreshConfiguration();
  assert.equal(JSON.stringify(state), before); assert.equal(input.value, 'unfinished draft');
});
