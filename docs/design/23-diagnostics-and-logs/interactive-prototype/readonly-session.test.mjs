// From repository root:
// node --experimental-vm-modules --test docs/design/23-diagnostics-and-logs/interactive-prototype/readonly-session.test.mjs
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

assert.equal(typeof vm.SourceTextModule, 'function', 'Run this test with node --experimental-vm-modules --test.');
const directory = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(directory, '../../../..');
// No window, document, fetch or timers: importing the prototype's real dependency
// graph must not initialize the production chat client or make business requests.
const context = vm.createContext({ console, URL });
const modules = new Map();

async function loadModule(file) {
  const filename = file.split('?')[0];
  if (modules.has(filename)) return modules.get(filename);
  const module = new vm.SourceTextModule(fs.readFileSync(filename, 'utf8'), {
    context,
    identifier: filename,
  });
  modules.set(filename, module);
  await module.link((specifier, parent) => {
    const target = specifier.startsWith('/console/')
      ? path.join(root, specifier)
      : path.resolve(path.dirname(parent.identifier), specifier);
    assert(target.startsWith(`${root}${path.sep}`), 'Only repository-local modules may load.');
    return loadModule(target);
  });
  return module;
}

const module = await loadModule(path.join(directory, 'readonly-session.js'));
await module.evaluate();
const { createReadonlyFixture } = module.namespace;
const { projectConversationItems } = modules.get(path.join(root, 'console/conversation-items.js')).namespace;
const { buildTrajectory } = modules.get(path.join(root, 'console/trajectory.js')).namespace;
const makeSession = status => ({
  id: 'd1a69009000000000000000000000001',
  runId: 'r-demo-preserved-0001',
  title: '数据流查询节点咨询',
  user: '陈同学',
  agent: '企微助手',
  channel: '企微',
  questions: ['数据流有哪些查询节点？', '这些节点分别适合什么场景？'],
  updated: '2026-09-09T07:00:00.000Z',
  status,
});
const rowCounts = { completed: 8, failed: 8, waiting: 9, running: 7, cancelled: 8 };

for (const status of Object.keys(rowCounts)) {
  test(`${status}: 真实投影、工具配对、终态与跨页时间一致`, () => {
    const session = makeSession(status);
    const before = JSON.stringify(session);
    const fixture = createReadonlyFixture(session);
    const nodes = projectConversationItems(fixture.conversation);
    const rows = buildTrajectory({ items: fixture.events });
    const users = nodes.filter(node => node.type === 'user');
    const assistants = nodes.filter(node => node.type === 'assistant');
    const userEvents = fixture.events.filter(event => event.event_type === 'user/message');
    const latest = fixture.events.at(-1);
    const latestRun = rows.filter(row => row.run_id === session.runId);

    assert.equal(JSON.stringify(session), before, 'Fixture input remains unchanged.');
    assert.deepEqual(Array.from(users, node => node.text), session.questions);
    assert.equal(assistants.length, 2);
    assert.equal(rows.length, rowCounts[status]);
    assert.equal(rows.filter(row => row.kind === 'input').length, 2);
    assert.equal(rows.filter(row => row.kind === 'error').length, status === 'failed' ? 1 : 0);
    assert(rows.every(row => !String(row.event_type).startsWith('run/')));
    assert.equal(latest.run_id, session.runId, 'Keep the linked Run ID unchanged.');
    assert(latestRun.length > 0);
    assert.equal(latest.created_at, session.updated);
    assert.equal(latest.updated_at, session.updated);
    assert.equal(Date.parse(userEvents[1].created_at) - Date.parse(userEvents[0].created_at), 90000);
    assert(fixture.events.every(event => Date.parse(event.updated_at) <= Date.parse(session.updated)));
    assert(fixture.conversation.every(item => Date.parse(item.updated_at) <= Date.parse(session.updated)));
    assert.equal(new Set(fixture.events.map(event => event.event_id)).size, fixture.events.length);
    assert.equal(new Set(fixture.conversation.map(item => item.item_id)).size, fixture.conversation.length);

    const firstTool = rows.find(row => row.kind === 'tool');
    assert(firstTool.native.call_event_id);
    assert(firstTool.native.result_event_id);
    assert.equal(firstTool.status, 'completed');
    assert.equal(users[0].contextRefs[0].key, 'query_scope');
    assert.equal(rows.find(row => row.kind === 'input').payload.context_refs[0].key, 'query_scope');

    const lastActions = assistants.at(-1).actions;
    if (status === 'waiting') {
      assert(latestRun.some(row => row.kind === 'deferred'));
      assert(lastActions.some(action => action.type === 'deferred'));
      assert(fixture.conversation.some(item => item.status === 'awaiting_input'));
    } else if (status === 'running') {
      assert(fixture.conversation.some(item => item.kind === 'tool' && item.status === 'pending' && item.output === null));
      assert(!lastActions.some(action => action.type === 'text'));
    } else if (status === 'failed') {
      const failure = rows.find(row => row.kind === 'error');
      assert.equal(failure.payload.error_kind, 'provider_timeout');
      assert.equal(failure.payload.phase, 'model_request');
      assert(!lastActions.some(action => action.type === 'text'));
    } else if (status === 'cancelled') {
      assert(lastActions.some(action => action.type === 'interrupt'));
    } else {
      assert(lastActions.some(action => action.type === 'text'));
      assert(!lastActions.some(action => action.type === 'interrupt'));
    }
  });
}

test('未指定状态按正常回答生成，单问题仍对齐更新时间且保留 Run ID', () => {
  const session = { ...makeSession(undefined), questions: ['数据流有哪些查询节点？'] };
  const fixture = createReadonlyFixture(session);
  assert.equal(fixture.events.at(-1).created_at, session.updated);
  assert(fixture.events.every(event => event.run_id === session.runId));
  const nodes = projectConversationItems(fixture.conversation);
  assert.equal(nodes.length, 2);
  assert(nodes[1].actions.some(action => action.type === 'text'));
});

test('依赖图复用只读模块，不导入会自动启动真实请求的工作台', () => {
  assert(modules.has(path.join(root, 'console/conversation-items.js')));
  assert(modules.has(path.join(root, 'console/assistant-presentation.js')));
  assert(modules.has(path.join(root, 'console/trajectory.js')));
  assert(!modules.has(path.join(root, 'console/chat.js')));
  assert(!modules.has(path.join(root, 'console/workspace.js')));
});
