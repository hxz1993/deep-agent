import assert from 'node:assert/strict';
import test from 'node:test';
import { diagnosticRequest, diagnosticRequestOptions } from '../../../console/diagnostics-ui.js';

test('diagnostic queries send filters in POST JSON without URL parameters', () => {
  for (const route of [
    'sessions', 'sessions/overview', 'sessions/users', 'sessions/user-profile',
    'usage', 'usage/details', 'usage/sessions', 'usage/export', 'logs',
    'channels/account-a/overview', 'channels/account-a/peers', 'channels/account-a/sessions',
    'sessions/session-a', 'sessions/session-a/events',
  ]) {
    const path = `/api/v1/diagnostics/${route}`;
    const result = diagnosticRequestOptions(`${path}?start=2026-10-19T01%3A00%3A00Z&query=%E5%B7%A5%E5%8D%95&offset=50&descending=false`);
    assert.equal(result.path, path);
    assert.equal(result.options.method, 'POST');
    assert.equal(result.options.headers['Content-Type'], 'application/json');
    assert.deepEqual(JSON.parse(result.options.body), {start:'2026-10-19T01:00:00Z',query:'工单',offset:'50',descending:'false'});
  }
  assert.deepEqual(JSON.parse(diagnosticRequestOptions('api/v1/diagnostics/sessions').options.body), {});
});

test('status, accounts, ID history and unrelated APIs keep their read method', () => {
  for (const path of [
    'api/v1/diagnostics/logs/status', 'api/v1/diagnostics/channels/accounts',
    'api/v1/diagnostics/sessions/id/history', 'api/v1/diagnostics/sessions/id',
    'api/v1/sessions?limit=50', 'api/v1/agents',
  ]) assert.deepEqual(diagnosticRequestOptions(path), {path,options:{}});
});

test('POST diagnostic requests retain same-origin helper, no-store and cancellation', async () => {
  const previous = globalThis.window;
  const calls = [];
  globalThis.window = {
    serviceUrl: path => `/gateway/prefix/${path}`,
    daFetch: async (path, options) => { calls.push({path,options}); return {ok:true,json:async()=>({items:[]})}; },
  };
  try {
    const controller = new AbortController(); controller.abort();
    assert.deepEqual(await diagnosticRequest('api/v1/diagnostics/logs?q=needle&cursor=next', controller.signal), {items:[]});
    assert.equal(calls[0].path, '/gateway/prefix/api/v1/diagnostics/logs');
    assert.equal(calls[0].options.method, 'POST');
    assert.deepEqual(JSON.parse(calls[0].options.body), {q:'needle',cursor:'next'});
    assert.equal(calls[0].options.signal.aborted, true);
    assert.equal(calls[0].options.includeContext, false);
    assert.equal(calls[0].options.reportStatus, false);
    assert.equal(calls[0].options.cache, 'no-store');
  } finally { globalThis.window = previous; }
});
