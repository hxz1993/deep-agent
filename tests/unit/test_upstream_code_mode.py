"""Stable upstream gains must survive DeepAgent's Hybrid CodeMode wrappers."""
from __future__ import annotations

import unittest
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel
from pydantic_ai import Agent, FunctionToolset
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from deep_agent.agent.deps import AgentDeps
from deep_agent.ptc import FilePayloadResolvingToolset, HybridCodeMode
from deep_agent.ptc.file_payload import build_code_mode_tool_package


class _Receipt(BaseModel):
    amount: Decimal
    identifier: UUID
    at: datetime


class UpstreamCodeModeTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, tool, snippets, *, limits=None):
        step = 0

        def respond(messages, info):
            nonlocal step
            step += 1
            if step <= len(snippets):
                return ModelResponse(parts=[ToolCallPart(
                    'run_code', {'code': snippets[step - 1]}, f'code-{step}',
                )])
            return ModelResponse(parts=[TextPart('done')])

        agent = Agent(
            FunctionModel(respond), deps_type=AgentDeps,
            toolsets=[build_code_mode_tool_package().toolset,
                      FilePayloadResolvingToolset(FunctionToolset(tools=[tool]).prefixed('demo'))],
            capabilities=[HybridCodeMode(tools=[], dynamic_catalog=True, resource_limits=limits)],
        )
        result = await agent.run('Exercise authorized code tools.', deps=AgentDeps(session_id='upstream-code'))
        self.assertEqual(result.output, 'done')
        return [part for message in result.new_messages() for part in message.parts
                if isinstance(part, ToolReturnPart) and part.tool_name == 'run_code']

    async def test_typed_financial_result_matches_generated_schema(self):
        async def receipt() -> _Receipt:
            return _Receipt(amount=Decimal('1.50'), identifier=UUID(int=1), at=datetime(2026, 9, 22))

        returns = await self._run(receipt, [
            "r = await demo_receipt()\n[r['amount'], r['identifier'], r['at']]",
        ])
        self.assertEqual(returns[0].outcome, 'success')
        self.assertEqual(returns[0].content, ['1.50', str(UUID(int=1)), '2026-09-22T00:00:00'])
        self.assertEqual(returns[0].metadata['nested_calls'][0]['status'], 'completed')

    async def test_suspension_limit_spans_snippets_and_preserves_success_audit(self):
        executed = []

        async def record(value: int) -> int:
            executed.append(value)
            return value

        returns = await self._run(record, [
            'saved = await demo_record(value=0)\nsaved',
            'for i in range(1, 4):\n    await demo_record(value=i)',
        ], limits={'max_suspensions': 4})
        self.assertEqual(executed, [0, 1])
        self.assertEqual(returns[0].outcome, 'success')
        self.assertEqual(returns[1].outcome, 'failed')
        self.assertIn('suspension limit 4 exceeded', str(returns[1].content))
        self.assertEqual(returns[1].metadata['nested_calls'][0]['status'], 'completed')
        self.assertEqual(returns[1].metadata['nested_calls'][0]['result'], 1)
