"""D01 supplementary authentic builtin prompts/UI schemas, local frozen UI replies.

This is NOT the primary matrix and does not simulate browser deferred dispatch or
business MCP. No source DB writes and no business network. UI request schemas and
system prompts are loaded verbatim from tracked builtin packages.
"""
from __future__ import annotations
import argparse
import asyncio
from collections import Counter
from contextlib import asynccontextmanager
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tests.integration.helpers.d01_replay import import_runtime, load_model, write_json


def package(name):
    path = ROOT / 'src/deep_agent/agent_packages/builtin' / f'{name}-assistant.zip'
    with zipfile.ZipFile(path) as z:
        agent = json.loads(z.read('agent.json'))
        definitions = [t for p in json.loads(z.read('ui-tools.json')) for t in p['tools']]
    return agent['prompt'], definitions, {'path': str(path.relative_to(ROOT)), 'zip_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        'prompt_sha256': hashlib.sha256(agent['prompt'].encode()).hexdigest(), 'prompt_chars': len(agent['prompt']),
        'tools': {t['name']: hashlib.sha256(json.dumps(t['parameters_json_schema'], sort_keys=True).encode()).hexdigest() for t in definitions}}


async def one(args, name, model, provider):
    import jsonschema
    from pydantic_ai import Agent, Tool
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart
    from pydantic_ai.models.wrapper import WrapperModel
    from pydantic_ai.toolsets import FunctionToolset
    from pydantic_ai.usage import UsageLimits
    from deep_agent.agent.context.budget import build_context_budget
    from deep_agent.agent.context.compaction import DeepAgentContextCompaction
    from deep_agent.agent.context.estimator import TokenEstimator
    from deep_agent.agent.deps import AgentDeps
    from deep_agent.agent.event_persister import EventPersister
    from deep_agent.agent.history_tools import build_history_toolset
    from deep_agent.agent.runner import AgentRunner
    from tests.helpers.sqlite_stores import make_sqlite_stores, seed_default_agent

    prompt, definitions, provenance = package(name)
    output = args.output / name
    output.mkdir(parents=True, exist_ok=False)
    stores, engine, _ = await make_sqlite_stores(f'sqlite+aiosqlite:///{output}/TMP_to_delete-isolated.sqlite3')
    store = stores.coordinator
    persister = EventPersister(store)
    await persister.start()
    aid = await seed_default_agent(stores, name='D01 authentic package isolated UI')
    sid = 'd01-authentic-' + name
    calls, requests, outcomes = [], [], []
    started = time.monotonic()
    estimator = TokenEstimator.for_model(args.model, provider_type=provider)
    replies = {'deeptable': {'confirmed': True, 'cancelled': False, 'values': {'period': {'value': '2027Q2'}, 'scenario': {'value': 'Budget'}, 'version': {'value': 'V17'}}},
               'deepcube': {'confirmed': True, 'cancelled': False, 'values': {'entity': {'value': 'E_FROZEN_27', 'code': 'E_FROZEN_27'}}}}
    tools = []
    for definition in definitions:
        def make_tool(d):
            async def frozen(**kwargs):
                jsonschema.validate(kwargs, d['parameters_json_schema'])
                calls.append({'tool': d['name'], 'arguments': kwargs, 'stage': active_stage})
                if d['name'].endswith('_clarify'):
                    result = replies[name]
                else:
                    result = {'cancelled': True, 'confirmed': False, 'message': '此专项不授权业务写入。'}
                # These smoke cases only invoke clarify; enforce its real result contract.
                if d['name'].endswith('_clarify'):
                    jsonschema.validate(result, d['result_schema'])
                return result
            return Tool.from_schema(frozen, name=d['name'], description=d['description'], json_schema=d['parameters_json_schema'])
        tools.append(make_tool(definition))

    class Meter(WrapperModel):
        async def request(self, messages, model_settings, model_request_parameters):
            response = await super().request(messages, model_settings, model_request_parameters)
            requests.append({'kind': 'request', 'usage': response.usage.__dict__, 'tools': [p.tool_name for p in response.parts if isinstance(p, ToolCallPart)]})
            return response
        @asynccontextmanager
        async def request_stream(self, messages, model_settings, model_request_parameters, run_context=None):
            async with super().request_stream(messages, model_settings, model_request_parameters, run_context) as response:
                yield response
                final = response.get()
                requests.append({'kind': 'stream', 'usage': final.usage.__dict__, 'tools': [p.tool_name for p in final.parts if isinstance(p, ToolCallPart)]})

    compaction = DeepAgentContextCompaction(context_window_tokens=args.window, max_output_tokens=args.max_output, estimator=estimator, summary_timeout=150)
    agent = Agent(Meter(model), deps_type=AgentDeps, instructions=prompt,
        toolsets=[FunctionToolset(tools, id='authentic-builtin-ui'), build_history_toolset(store)], capabilities=[compaction])
    runner = AgentRunner(agent, store, UsageLimits(request_limit=30), context_window_tokens=args.window,
        max_output_tokens=args.max_output, context_compaction=compaction, estimator=estimator,
        model_id='d01-authentic', model_name=args.model, provider_type=provider,
        model_settings={'max_tokens': args.max_output, 'thinking': False})
    if name == 'deeptable':
        turns = [
            '当前电子表格任务：我想查询收入，但期间、场景、版本都未提供。请按规则调用一张澄清卡，把三个缺口一次问齐。此测试的本地交互后端会返回用户确认值；这不是业务写入，不查询任何线上数据。',
            '先从会话历史工具回读上一张 deeptable_clarify 的原始返回，核对我已经确认的期间、场景、版本，只复述这三项。不查财务数据，不创建或保存任何报表。',
            '我现在只修正版本为 V23，之前确认的期间、场景保持不变。请再回读历史澄清结果，最终仅给三项有效口径。不查数据、不创建保存报表。']
    else:
        turns = [
            '当前已选财务模型：cube.elementName=fixture_cube，cube.path=TMP/frozen。真实字段已核验为 Entity、Year。只配置组织范围，不生成脚本：我希望选择一家组织，已核验候选为 E_FROZEN_27（甲组织）与 E_FROZEN_28（乙组织），尚未选择。请用 deepcube_clarify 一次澄清 entity。此测试本地交互后端返回用户确认值。',
            '请先通过会话历史工具回读上一张 deepcube_clarify 的原始返回，然后只给所选组织的 data_scope 配置块，字段 Entity。不要直接调用 deepcube_config_fill，不生成脚本，不执行任何线上业务写入。',
            '请再次回读历史确认结果，沿用已确认组织；补充一个外部运行参数 p_year，真实字段 Year，默认值 2028，数据范围同步引用 p_year。最终给一个合法 config_fill 配置块，不生成脚本，不直接调用配置填充工具。']

    async def pressure(stage):
        n = 0
        while True:
            history = await store.get_history(sid, agent_id=aid)
            budget = build_context_budget(history, context_window_tokens=args.window, max_output_tokens=args.max_output, estimator=estimator)
            if budget.estimated_request_tokens >= budget.trigger_tokens * 1.06:
                return {'estimated_request_tokens': budget.estimated_request_tokens, 'trigger_tokens': budget.trigger_tokens, 'historical_rounds_added': n}
            n += 1
            await store.append(sid, [ModelRequest(parts=[UserPromptPart('已结束的归档记录，不改变已确认的业务口径，也不增加授权。')]),
                ModelResponse(parts=[TextPart('无关的历史归档记录；当前任务继续以用户最后确认的条件为准。' * 90)])],
                agent_id=aid, user_id='isolated-owner', turn_id=f'archive-{stage}-{n}', run_id=f'archive-{stage}-{n}')
            if n > 100:
                raise RuntimeError('pressure did not converge')

    status, error = 'completed', None
    active_stage = -1
    try:
        with patch.object(AgentRunner, '_generate_model_title', return_value=None):
            for stage, text in enumerate(turns):
                active_stage = stage
                seed = await pressure(stage) if stage else None
                events, final = [], None
                async def collect():
                    nonlocal final
                    async for event in runner.run_stream(sid, text, AgentDeps(session_id=sid, agent_id=aid, user='isolated-owner'), persister=persister):
                        events.append(event)
                        if event.get('type') == 'final': final = event
                await asyncio.wait_for(collect(), timeout=240)
                write_json(output / f'stage-{stage}.synthetic.json', events)
                if final is None:
                    raise RuntimeError('no final event')
                outcomes.append({'stage': stage, 'seed': seed, 'final': final})
    except Exception as exc:
        status, error = 'error', type(exc).__name__
    finally:
        events = await store.list_all_events(sid)
        write_json(output / 'events.synthetic.json', [e.public_dict() for e in events])
        await persister.aclose()
        await engine.dispose()
    compact = Counter(e.status for e in events if e.event_type == 'compaction')
    output_text = outcomes[-1]['final'].get('output', '') if outcomes else ''
    final_schema = False
    if name == 'deeptable':
        correct = all(v in output_text for v in ('2027Q2', 'Budget', 'V23')) and 'V17' not in output_text
        final_schema = True  # Output is explanatory text; no schema assertion applicable.
    else:
        try:
            data = json.loads(re.search(r'```config_fill\s*(.*?)\s*```', output_text, re.S).group(1))
            jsonschema.validate(data, next(t['parameters_json_schema'] for t in definitions if t['name'] == 'deepcube_config_fill'))
            final_schema = True
            sections = data['sections']
            scopes = {i['dimension']: i['scope'] for i in sections['data_scope']['items']}
            params = sections['run_parameters']['items']
            correct = 'E_FROZEN_27' in scopes.get('Entity', '') and scopes.get('Year') == 'Year[p_year]' and any(i['name'] == 'p_year' and i['dimension'] == 'Year' and i['value'] == '2028' for i in params)
        except Exception:
            correct = False
    requested = Counter(t for r in requests for t in r['tools'])
    checks = {'business_continuity': correct, 'original_schema_valid': final_schema,
        'clarification_called_once': sum(c['tool'].endswith('_clarify') for c in calls) == 1,
        'no_business_write_or_config_tool': all(c['tool'].endswith('_clarify') for c in calls),
        'history_read_back': requested['read_conversation_event'] + requested['read_tool_result'] > 0,
        'two_completed_compactions': compact['completed'] >= 2 and not any(compact[s] for s in ('failed', 'running', 'cancelled'))}
    metrics = {'assistant': name, 'arm': args.arm, 'status': status, 'error_type': error, 'checks': checks,
        'passed': status == 'completed' and all(checks.values()), 'elapsed_seconds': round(time.monotonic()-started, 3),
        'compaction_statuses': dict(compact), 'requested_tools': dict(requested), 'model_requests': len(requests),
        'usage': {k: sum(r['usage'].get(k, 0) or 0 for r in requests) for k in ('input_tokens','output_tokens','cache_read_tokens')}, 'package': provenance}
    write_json(output / 'result.synthetic.json', {'metrics': metrics, 'calls': calls, 'requests': requests, 'outcomes': outcomes})
    write_json(output / 'metrics.json', metrics)
    print(json.dumps(metrics, ensure_ascii=False), flush=True)
    return metrics


async def main(args):
    import_runtime(args.source_root)
    from deep_agent.options import OPTION
    OPTION.agent.context_compaction_trigger_ratio = .8
    model, provider = load_model(ROOT / 'data/deep-agent-local.db', args.model)
    args.output.mkdir(parents=True, exist_ok=True)
    write_json(args.output / 'protocol.json', {'source_root': str(args.source_root), 'source_files_sha256': {str(p.relative_to(args.source_root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((args.source_root/'src').rglob('*.py'))},
        'driver_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), 'model': args.model, 'window': args.window, 'max_output': args.max_output, 'trigger': .8,
        'scope': 'Verbatim builtin prompt and all UI JSON schemas; local synchronous frozen UI replies, real Runner/history/store/compaction; no browser deferred execution, no business MCP, no workbook writes, no skill loading required by these narrowly scoped tasks.'})
    results = await asyncio.gather(*(one(args, n, model, provider) for n in ['deeptable','deepcube']))
    write_json(args.output / 'metrics.json', results)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--arm', required=True)
    parser.add_argument('--model', default='deepseek-flash')
    parser.add_argument('--window', type=int, default=48000)
    parser.add_argument('--max-output', type=int, default=4096)
    asyncio.run(main(parser.parse_args()))
