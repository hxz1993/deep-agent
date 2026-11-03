"""Native viewer pixels stay in the live Run; all public history remains light."""
from __future__ import annotations

import base64
import json
import unittest
from copy import deepcopy
from unittest.mock import AsyncMock, patch

import httpx2
from pydantic_ai import Agent, ToolReturn
from pydantic_ai.messages import BinaryContent, ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from deep_agent.agent.attachments import resolve_history_for_llm
from deep_agent.agent.events import map_part
from deep_agent.db.event_codec import encode_messages, encode_message_parts
from deep_agent.db.serialization import serialize_message, deserialize_message
from tests.unit.test_web_image_persistence import DATA


def visual_result(index=1):
    safe = {"kind": "view-image-result", "status": "ready", "items": [
        {"resource": {"type": "attachment", "file_id": f"file-{index}"}, "name": "chart.pdf",
         "format": "pdf", "page": index, "page_count": 2, "width": 2, "height": 2,
         "sha256": "fixture", "bytes": len(DATA), "status": "ready"}]}
    return ToolReturn(return_value=[safe, f"chart.pdf physical page {index}",
                                   BinaryContent(DATA, media_type="image/png")],
                      metadata={"view_image": safe})


def part(index=1):
    r=visual_result(index)
    return ToolReturnPart("view_image", r.return_value, f"call-{index}", metadata=r.metadata)


class ViewImageProjectionTests(unittest.IsolatedAsyncioTestCase):
    def assert_light(self, value):
        s=json.dumps(value, default=str)
        self.assertNotIn(base64.b64encode(DATA).decode(),s)
        self.assertNotIn('"kind": "binary"',s)
        self.assertNotIn('"data"',s)
        self.assertNotIn('data:image/',s)

    async def test_all_persistence_paths_clone_without_mutating_live_images(self):
        p=part();original=deepcopy(p.metadata);m=ModelRequest(parts=[p])
        saved=serialize_message(m)
        self.assert_light(saved)
        self.assertEqual(saved['parts'][0]['content'],original['view_image'])
        self.assertEqual(p.metadata,original)
        self.assertIsInstance(p.content[-1],BinaryContent)
        self.assertEqual(p.content[-1].data,DATA)
        self.assert_light(map_part(p))
        self.assertEqual(map_part(p)['result']['items'][0]['page'],1)
        for encode in [encode_messages,encode_message_parts]:
            drafts=encode([m],session_id='viewer',run_id='viewer-run')
            self.assertTrue(drafts)
            for draft in drafts:self.assert_light(draft.payload)
        with patch('deep_agent.agent.attachments._download_file',new=AsyncMock()) as download:
            history=await resolve_history_for_llm([deserialize_message(saved)],current_user_index=None,request_headers={})
        download.assert_not_awaited()
        self.assert_light(serialize_message(history[0]))
        self.assertEqual(history[0].parts[0].content['items'][0]['resource']['file_id'],'file-1')

    async def test_missing_metadata_fallback_never_serializes_pixels(self):
        p=part();p.metadata=None
        self.assert_light(serialize_message(ModelRequest(parts=[p])))
        p.content=[BinaryContent(DATA,media_type='image/png')]
        self.assert_light(serialize_message(ModelRequest(parts=[p])))

    async def test_sdk_accumulates_images_across_calls_and_keeps_tool_identity(self):
        calls=[]
        def model(messages,info):
            returns=[p for m in messages for p in m.parts if isinstance(p,ToolReturnPart)]
            if len(returns)<2:
                n=len(returns)+1
                return ModelResponse(parts=[ToolCallPart('view_image',{'index':n},f'call-{n}')])
            self.assertEqual([p.tool_call_id for p in returns],['call-1','call-2'])
            self.assertEqual(sum(isinstance(v,BinaryContent) for p in returns for v in p.content),2)
            calls.append(True)
            return ModelResponse(parts=[TextPart('compared')])
        agent=Agent(FunctionModel(model))
        @agent.tool_plain
        async def view_image(index:int)->ToolReturn:return visual_result(index)
        result=await agent.run('compare pages')
        self.assertEqual(result.output,'compared');self.assertEqual(calls,[True])
        for m in result.new_messages():self.assert_light(serialize_message(m))

    async def test_real_openai_sdk_wire_includes_both_images_with_source_labels(self):
        wire=[]
        def transport(request):
            wire.append(json.loads(request.content))
            msg=({'role':'assistant','tool_calls':[{'id':'call-1','type':'function','function':{
                'name':'view_image','arguments':'{}'}}]} if len(wire)==1 else {'role':'assistant','content':'viewed'})
            return httpx2.Response(200,json={'id':'fixture','object':'chat.completion','created':1,
                'model':'gpt-4.1','choices':[{'index':0,'message':msg,'finish_reason':'tool_calls' if len(wire)==1 else 'stop'}]})
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(transport)) as client:
            model=OpenAIChatModel('gpt-4.1',provider=OpenAIProvider(api_key='fixture-only',http_client=client))
            agent=Agent(model)
            @agent.tool_plain
            async def view_image()->ToolReturn:
                a,b=visual_result(1),visual_result(2)
                a.return_value+=b.return_value
                a.metadata['view_image']['items']+=b.metadata['view_image']['items']
                return a
            result=await agent.run('compare')
        self.assertEqual(result.output,'viewed');self.assertEqual(len(wire),2)
        contents=[v for m in wire[1]['messages'] if isinstance(m.get('content'),list) for v in m['content']]
        images=[v for v in contents if v.get('type')=='image_url']
        self.assertEqual(len(images),2)
        self.assertTrue(all(v['image_url']['url']=='data:image/png;base64,'+base64.b64encode(DATA).decode() for v in images))
        self.assertIn('physical page 2', json.dumps(wire[1]))
        self.assertTrue(any(m.get('role')=='tool' and m.get('tool_call_id')=='call-1' for m in wire[1]['messages']))
