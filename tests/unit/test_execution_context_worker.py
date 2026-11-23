from __future__ import annotations

import asyncio
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from deep_agent.agent.deps import AgentDeps
from deep_agent.sandbox.context import ExecutionContextLifecycle, execution_context_env, execution_context_scope
from deep_agent.sandbox.context_env import EXECUTION_CONTEXT_ENV, MAX_EXECUTION_CONTEXT_BYTES
from deep_agent.sandbox.subprocess_dev.backend import SubprocessBackend
from deep_agent.sandbox.subprocess_dev.env import build_worker_env
from deep_agent.sandbox.types import ExecuteRequest
from deep_agent.security.current_user import CurrentUserProfile
from tests.helpers.sandbox import sandbox_test_python
from tests.unit.test_skill_execution_layout import script_request


class ExecutionContextEnvironmentTests(unittest.TestCase):
    def test_local_username_is_not_reused_for_a_different_channel_owner(self):
        from starlette.requests import Request
        from deep_agent.api.v1.chat import _local_execution_username
        from deep_agent.local_security import LOCAL_CURRENT_USERNAME_SCOPE_KEY
        request = Request({'type': 'http', 'headers': [(b'user', b'local-user')],
                           LOCAL_CURRENT_USERNAME_SCOPE_KEY: 'Local Name'})
        self.assertEqual(_local_execution_username(request, 'local-user'), 'Local Name')
        self.assertIsNone(_local_execution_username(request, 'channel-owner'))

    def test_host_variables_are_not_inherited_and_only_context_key_is_added(self):
        with patch.dict(os.environ, {EXECUTION_CONTEXT_ENV: 'host-value',
                                   'CONTEXT_USERNAME': 'old-user', 'TOKEN': 'secret'}):
            ordinary = build_worker_env()
            included = build_worker_env({EXECUTION_CONTEXT_ENV: '{"user":{"id":"u"}}',
                                         'TOKEN': 'secret'})
        self.assertNotIn(EXECUTION_CONTEXT_ENV, ordinary)
        self.assertNotIn('CONTEXT_USERNAME', included)
        self.assertNotIn('TOKEN', included)
        self.assertEqual(included[EXECUTION_CONTEXT_ENV], '{"user":{"id":"u"}}')

    def test_context_is_not_serialized_or_repr_and_rejects_other_environment_keys(self):
        deps = AgentDeps(session_id='private', execution_request_headers={'cookie': 'execution-only-private'})
        self.assertNotIn('execution-only-private', repr(deps))
        request = ExecuteRequest(request_id='context', code='1',
                                 context_env={EXECUTION_CONTEXT_ENV: '{"user":{"id":"private-user"}}'})
        self.assertNotIn('context_env', request.model_dump(mode='json'))
        self.assertNotIn('private-user', repr(request))
        for env in ({'TOKEN': 'secret'}, {EXECUTION_CONTEXT_ENV: '[]'},
                    {EXECUTION_CONTEXT_ENV: 'not-json'},
                    {EXECUTION_CONTEXT_ENV: 'x' * (MAX_EXECUTION_CONTEXT_BYTES + 1)}):
            with self.subTest(keys=list(env)), self.assertRaises(ValidationError):
                ExecuteRequest(request_id='invalid', code='1', context_env=env)


class ExecutionContextRealWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.backend = SubprocessBackend(python_executable=sandbox_test_python(), max_concurrency=2)
        self.addAsyncCleanup(self.backend.aclose)

    async def test_native_expression_and_skill_helper_receive_same_context_without_residue(self):
        value = {'user': {'id': 'user-1', 'username': '张三'}, 'app': 'app-a',
                 'space': 'space-a', 'language': 'zh_CN'}
        env = {EXECUTION_CONTEXT_ENV: json.dumps(value, ensure_ascii=False)}
        ordinary = await self.backend.execute(ExecuteRequest(request_id='expression',
            code='import json, os\njson.loads(os.environ["DEEP_AGENT_CONTEXT"])', context_env=env))
        self.assertEqual(ordinary.status, 'ok', ordinary.error)
        self.assertEqual(ordinary.result, value)
        stored = await self.backend.execute(script_request({
            'scripts/helpers.py': 'import json, os\ndef identity(): return json.loads(os.environ["DEEP_AGENT_CONTEXT"])\n',
            'scripts/report.py': 'import json\nfrom pathlib import Path\nfrom .helpers import identity\n'
                                 'Path("outputs/result.json").write_text(json.dumps(identity()))\n',
        }, context_env=env))
        self.assertEqual(stored.status, 'ok', stored.error)
        self.assertEqual(stored.result, value)
        without = await self.backend.execute(ExecuteRequest(request_id='not-included',
            code='import os\nos.getenv("DEEP_AGENT_CONTEXT")'))
        self.assertEqual(without.status, 'ok', without.error)
        self.assertIsNone(without.result)

    async def test_parallel_executions_keep_identity_separate_and_files_work(self):
        async def run(user):
            return await self.backend.execute(ExecuteRequest(request_id=user,
                context_env={EXECUTION_CONTEXT_ENV: json.dumps({'user': {'id': user}})},
                code='import json, os\nfrom pathlib import Path\n'
                     'from deepagent_files import publish_path\n'
                     'uid=json.loads(os.environ["DEEP_AGENT_CONTEXT"])["user"]["id"]\n'
                     'Path(publish_path("report.txt", mime="text/plain")).write_text(uid)\n'
                     '{"user": uid}'))
        results = await asyncio.gather(run('user-a'), run('user-b'))
        for user, result in zip(('user-a', 'user-b'), results):
            self.assertEqual(result.status, 'ok', result.error)
            self.assertEqual(result.result, {'user': user})
            self.assertEqual(result.published_files[0].data, user.encode())


class ExecutionContextLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_sdk_recovery_does_not_reset_the_outer_user_run_snapshot(self):
        resolver = SimpleNamespace(profile_for_headers=AsyncMock(return_value=CurrentUserProfile('u', 'name')))
        deps = AgentDeps(session_id='recovery', user='u')
        with patch('deep_agent.sandbox.context.get_current_user_profile_resolver', return_value=resolver):
            async def handler():
                return await execution_context_env(deps, include_context=True)
            async with execution_context_scope(deps):
                lifecycle = ExecutionContextLifecycle()
                first = await lifecycle.wrap_run(SimpleNamespace(deps=deps), handler=handler)
                second = await lifecycle.wrap_run(SimpleNamespace(deps=deps), handler=handler)
                self.assertEqual(first, second)
                resolver.profile_for_headers.assert_awaited_once()
                self.assertIsNotNone(deps.execution_context_state)
        self.assertIsNone(deps.execution_context_state)

    async def test_run_end_cancels_pending_profile_and_releases_state(self):
        entered = asyncio.Event()
        finished = asyncio.Event()
        async def lookup(*args, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                finished.set()
        deps = AgentDeps(session_id='cancel', user='user-1')
        resolver = SimpleNamespace(profile_for_headers=lookup)
        with patch('deep_agent.sandbox.context.get_current_user_profile_resolver', return_value=resolver):
            async def handler():
                waiter = asyncio.create_task(execution_context_env(deps, include_context=True))
                await entered.wait()
                waiter.cancel()
                await asyncio.gather(waiter, return_exceptions=True)
                return 'done'
            output = await ExecutionContextLifecycle().wrap_run(SimpleNamespace(deps=deps), handler=handler)
        self.assertEqual(output, 'done')
        self.assertTrue(finished.is_set())
        self.assertIsNone(deps.execution_context_state)

    async def test_new_run_with_same_deps_does_not_reuse_old_snapshot(self):
        resolver = SimpleNamespace(profile_for_headers=AsyncMock(side_effect=[
            CurrentUserProfile('user-1', 'first'), CurrentUserProfile('user-1', 'second')]))
        deps = AgentDeps(session_id='reuse', user='user-1')
        with patch('deep_agent.sandbox.context.get_current_user_profile_resolver', return_value=resolver):
            async def handler():
                env = await execution_context_env(deps, include_context=True)
                return json.loads(env[EXECUTION_CONTEXT_ENV])['user']['username']
            lifecycle = ExecutionContextLifecycle()
            self.assertEqual(await lifecycle.wrap_run(SimpleNamespace(deps=deps), handler=handler), 'first')
            self.assertEqual(await lifecycle.wrap_run(SimpleNamespace(deps=deps), handler=handler), 'second')
        self.assertEqual(resolver.profile_for_headers.await_count, 2)
