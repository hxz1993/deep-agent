from __future__ import annotations

import copy
import json
import unittest
from unittest.mock import patch

import httpx2
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelRequest, SystemPromptPart, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.providers.openai import OpenAIProvider

from deep_agent.models.provider_registry import ProviderRuntimeConfig, resolve_provider_model


def config(provider_type="openai_compatible"):
    return ProviderRuntimeConfig(
        id="compatibility-probe", name="probe", provider_type=provider_type,
        use_default_base_url=provider_type != "openai_compatible",
        base_url="https://model.example.test/v1", api_key="test-placeholder",
    )


def make_model(enabled=False, *, provider_type="openai_compatible", name="custom-model", video=False):
    return resolve_provider_model(
        model_name=name, provider=config(provider_type),
        tags=["single_system_message"] if enabled else [],
        input_modalities=["text", "video"] if video else ["text"],
    )


class SystemMessageProfileTests(unittest.TestCase):
    def test_historical_null_and_empty_tags_keep_the_same_profile(self):
        baseline = make_model().profile
        for tags in (None, [], ["tool_call"]):
            with self.subTest(tags=tags):
                model = resolve_provider_model(model_name="custom-model", provider=config(), tags=tags)
                self.assertEqual(model.profile, baseline)
                self.assertNotEqual(model.profile.get("openai_chat_supports_multiple_system_messages"), False)

    def test_override_preserves_every_existing_profile_value(self):
        for provider, name in (("openai_compatible", "custom-model"), ("openai", "gpt-4.1"),
                               ("alibaba", "qwen3.8-flash"), ("alibaba", "qwen3.8-max"),
                               ("deepseek", "deepseek-v4-pro"), ("moonshotai", "kimi-k2.6"),
                               ("openrouter", "openai/gpt-4.1"), ("zai", "glm-5.2"),
                               ("stepfun", "step-3.5-flash")):
            for video in (False, True):
                with self.subTest(provider=provider, name=name, video=video):
                    before = make_model(provider_type=provider, name=name, video=video)
                    after = make_model(True, provider_type=provider, name=name, video=video)
                    self.assertIs(type(before), type(after))
                    self.assertFalse(after.profile["openai_chat_supports_multiple_system_messages"])
                    flag = "openai_chat_supports_multiple_system_messages"
                    self.assertEqual({k: v for k, v in before.profile.items() if k != flag},
                                     {k: v for k, v in after.profile.items() if k != flag})

    def test_disabled_does_not_override_a_provider_that_already_merges(self):
        with patch.object(OpenAIProvider, "model_profile", return_value={
            "openai_chat_supports_multiple_system_messages": False,
        }):
            self.assertFalse(make_model().profile["openai_chat_supports_multiple_system_messages"])

    def test_other_protocols_ignore_marker(self):
        for provider, name in (("anthropic", "claude-sonnet-4-5"), ("google", "gemini-2.5-flash")):
            with self.subTest(provider=provider):
                before = make_model(provider_type=provider, name=name)
                after = make_model(True, provider_type=provider, name=name)
                self.assertEqual(before.profile, after.profile)


class SystemMessageWireTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, messages, *, enabled=False, streaming=False, strict_backend=False):
        captured = []

        def respond(request):
            body = json.loads(request.content)
            captured.append(body)
            if strict_backend and sum(m["role"] == "system" for m in body["messages"]) > 1:
                return httpx2.Response(400, json={"error": {"message": "System message must be at the beginning.",
                                                         "type": "BadRequestError", "code": 400}})
            if streaming:
                chunks = [{"id": "probe", "object": "chat.completion.chunk", "created": 1,
                           "model": "custom-model", "choices": [{"index": 0, "delta": {"content": "ok"},
                                                                  "finish_reason": None}]},
                          {"id": "probe", "object": "chat.completion.chunk", "created": 1,
                           "model": "custom-model", "choices": [{"index": 0, "delta": {},
                                                                  "finish_reason": "stop"}]}]
                content = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
                return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=content)
            return httpx2.Response(200, json={"id": "probe", "object": "chat.completion", "created": 1,
                "model": "custom-model", "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"},
                                                       "finish_reason": "stop"}]})

        async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
            provider = OpenAIProvider(base_url="https://model.example.test/v1", api_key="test", http_client=client)
            with patch("deep_agent.models.provider_registry._openai_style_provider", return_value=provider):
                model = make_model(enabled)
            if streaming:
                async with model.request_stream(messages, None, ModelRequestParameters()) as stream:
                    async for _ in stream:
                        pass
                    self.assertEqual(stream.get().parts[0].content, "ok")
            else:
                result = await model.request(messages, None, ModelRequestParameters())
                self.assertEqual(result.parts[0].content, "ok")
        return captured[0]

    async def test_unset_enabled_and_disabled_again_send_expected_wire_without_mutating_history(self):
        messages = [ModelRequest(parts=[SystemPromptPart("甲\nfirst"), SystemPromptPart("乙 second"),
                                        UserPromptPart("hi")])]
        original = copy.deepcopy(messages)
        for enabled in (False, True, False):
            for streaming in (False, True):
                with self.subTest(enabled=enabled, streaming=streaming):
                    body = await self.request(messages, enabled=enabled, streaming=streaming)
                    self.assertEqual([m["role"] for m in body["messages"]],
                                     ["system", "user"] if enabled else ["system", "system", "user"])
                    if enabled:
                        self.assertEqual(body["messages"][0]["content"], "甲\nfirst\n\n乙 second")
                    self.assertEqual(messages, original)

    async def test_zero_one_and_non_leading_system_messages_keep_order(self):
        for parts in ([UserPromptPart("hi")], [SystemPromptPart("A"), UserPromptPart("hi")],
                      [SystemPromptPart("A"), UserPromptPart("hi"), SystemPromptPart("later")]):
            with self.subTest(parts=parts):
                messages = [ModelRequest(parts=parts)]
                before = await self.request(messages)
                after = await self.request(messages, enabled=True)
                self.assertEqual(before["messages"], after["messages"])

    async def test_strict_backend_reproduces_400_and_accepts_enabled(self):
        messages = [ModelRequest(parts=[SystemPromptPart("A"), SystemPromptPart("B"), UserPromptPart("hi")])]
        with self.assertRaises(ModelHTTPError) as error:
            await self.request(messages, strict_backend=True)
        self.assertEqual(error.exception.status_code, 400)
        await self.request(messages, enabled=True, strict_backend=True)
        middle = [ModelRequest(parts=[SystemPromptPart("A"), UserPromptPart("hi"), SystemPromptPart("B")])]
        with self.assertRaises(ModelHTTPError):
            await self.request(middle, enabled=True, strict_backend=True)

    async def test_real_agent_tool_loop_and_next_turn_keep_tool_identity(self):
        for enabled in (False, True):
            captured, executed = [], []

            def respond(request):
                body = json.loads(request.content)
                captured.append(body)
                if len(captured) == 1:
                    message = {"role": "assistant", "content": None, "tool_calls": [{"id": "call-probe",
                        "type": "function", "function": {"name": "probe", "arguments": '{"value": 3}'}}]}
                    finish = "tool_calls"
                else:
                    message, finish = {"role": "assistant", "content": "3"}, "stop"
                return httpx2.Response(200, json={"id": "probe", "object": "chat.completion", "created": 1,
                    "model": "custom-model", "choices": [{"index": 0, "message": message, "finish_reason": finish}]})

            def probe(value: int) -> int:
                executed.append(value)
                return value

            async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
                provider = OpenAIProvider(base_url="https://model.example.test/v1", api_key="test", http_client=client)
                with patch("deep_agent.models.provider_registry._openai_style_provider", return_value=provider):
                    model = make_model(enabled)
                agent = Agent(model, system_prompt=["System A", "System B"], tools=[probe])
                first = await agent.run("Use probe(3).")
                second = await agent.run("Repeat the answer.", message_history=first.all_messages())
                self.assertEqual(second.output, "3")
            self.assertEqual(executed, [3])
            for body in captured:
                self.assertEqual(sum(m["role"] == "system" for m in body["messages"]), 1 if enabled else 2)
            tool_result = next(m for m in captured[1]["messages"] if m["role"] == "tool")
            self.assertEqual(tool_result["tool_call_id"], "call-probe")
            self.assertEqual(tool_result["content"], "3")


if __name__ == "__main__":
    unittest.main()
