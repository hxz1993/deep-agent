"""Exercise the actual HTTP/SSE SDK request and durable lightweight projection."""
from __future__ import annotations

import asyncio
import base64
import json
import tempfile
import unittest
from pathlib import Path

from aiohttp import web
import httpx2
from pydantic_ai import Agent, AgentRunResultEvent, ToolReturn
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from sqlalchemy import text

from deep_agent.agent.attachments import resolve_history_for_llm
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.message_persistence import MessagePersistenceCapability, StreamMessagePersistenceCapability
from deep_agent.conversation.projector import project_session_events
from deep_agent.conversation.models import ToolConversationItem
from tests.helpers.sqlite_stores import make_sqlite_stores, seed_default_agent
from tests.unit.test_view_image_persistence import visual_result, DATA


class ViewImageWireTests(unittest.IsolatedAsyncioTestCase):
    def assert_light(self, value):
        encoded=json.dumps(value,default=str)
        self.assertNotIn(base64.b64encode(DATA).decode(),encoded)
        self.assertNotIn('"kind": "binary"',encoded)
        self.assertNotIn('data:image/',encoded)

    async def test_http_and_sse_accumulate_pixels_without_persisting_them(self):
        for streaming in (False,True):
            with self.subTest(streaming=streaming):
                await self.case(streaming)

    async def case(self,streaming):
        wire=[]
        async def endpoint(request):
            payload=await request.json();wire.append(payload);n=len(wire)
            if n<=2:
                msg={'role':'assistant','tool_calls':[{'id':f'view-{n}','type':'function','function':{
                    'name':'view_image','arguments':json.dumps({'index':n})}}]}
            else:msg={'role':'assistant','content':'two physical pages compared'}
            common={'id':f'request-{n}','created':1,'model':'fixture-vision'}
            if not payload.get('stream'):
                return web.json_response({**common,'object':'chat.completion','choices':[{
                    'index':0,'message':msg,'finish_reason':'tool_calls' if n<=2 else 'stop'}]})
            delta={'role':'assistant'}
            if n<=2:delta['tool_calls']=[{'index':0,**msg['tool_calls'][0]}]
            else:delta['content']=msg['content']
            chunks=[{**common,'object':'chat.completion.chunk','choices':[{'index':0,'delta':delta,'finish_reason':None}]},
                    {**common,'object':'chat.completion.chunk','choices':[{'index':0,'delta':{},'finish_reason':'tool_calls' if n<=2 else 'stop'}]}]
            body=''.join('data: '+json.dumps(c)+'\n\n' for c in chunks)+'data: [DONE]\n\n'
            return web.Response(text=body,content_type='text/event-stream')
        app=web.Application();app.router.add_post('/v1/chat/completions',endpoint)
        runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start()
        port=site._server.sockets[0].getsockname()[1]
        client=httpx2.AsyncClient()
        try:
            with tempfile.TemporaryDirectory(prefix='TMP to delete-view-image-wire-') as tmp:
                stores,engine,_=await make_sqlite_stores('sqlite+aiosqlite:///'+str(Path(tmp)/'events.db'))
                try:
                    agent_id=await seed_default_agent(stores)
                    deps=AgentDeps(session_id='viewer-wire',agent_id=agent_id,user='fixture-user',turn_id='viewer-turn',run_id='viewer-run')
                    queue=asyncio.Queue();cap_cls=StreamMessagePersistenceCapability if streaming else MessagePersistenceCapability
                    cap=cap_cls(stores.coordinator,history_length=0,skip_user_prompt=False,event_queue=queue)
                    model=OpenAIChatModel('fixture-vision',provider=OpenAIProvider(base_url=f'http://127.0.0.1:{port}/v1',api_key='fixture-only',http_client=client))
                    agent=Agent(model,deps_type=AgentDeps,capabilities=[cap])
                    @agent.tool_plain
                    async def view_image(index:int)->ToolReturn:return visual_result(index)
                    if streaming:
                        result=None
                        async with agent.run_stream_events('compare',deps=deps,run_id=deps.run_id) as events:
                            async for event in events:
                                if isinstance(event,AgentRunResultEvent):result=event.result
                    else:result=await agent.run('compare',deps=deps,run_id=deps.run_id)
                    self.assertEqual(result.output,'two physical pages compared');self.assertIsNone(cap.persistence_error)
                    counts=[]
                    for req in wire:
                        parts=[p for m in req['messages'] if isinstance(m.get('content'),list) for p in m['content']]
                        images=[p['image_url']['url'] for p in parts if p.get('type')=='image_url']
                        counts.append(len(images));self.assertTrue(all(x=='data:image/png;base64,'+base64.b64encode(DATA).decode() for x in images))
                    self.assertEqual(counts,[0,1,2])
                    self.assertIn('physical page 1',json.dumps(wire[-1]));self.assertIn('physical page 2',json.dumps(wire[-1]))
                    self.assertEqual([m['tool_call_id'] for m in wire[-1]['messages'] if m['role']=='tool'],['view-1','view-2'])
                    async with engine.connect() as conn:
                        rows=(await conn.execute(text('SELECT payload FROM chat_session_events'))).scalars().all()
                    self.assertTrue(rows)
                    for row in rows:self.assert_light(json.loads(row) if isinstance(row,str) else row)
                    while not queue.empty():self.assert_light(queue.get_nowait())
                    events=await stores.coordinator.list_events(deps.session_id,limit=100)
                    projected=project_session_events(events)
                    items=[i for i in projected.items if isinstance(i,ToolConversationItem)]
                    self.assertEqual([i.output['items'][0]['page'] for i in items],[1,2])
                    raw=await stores.coordinator.get_raw_history(deps.session_id,agent_id=agent_id)
                    resumed=await resolve_history_for_llm(raw,current_user_index=None,request_headers={})
                    from deep_agent.db.serialization import serialize_message
                    for message in resumed:self.assert_light(serialize_message(message))
                finally:await engine.dispose()
        finally:
            await client.aclose()
            await runner.cleanup()
