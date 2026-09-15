from __future__ import annotations

from types import SimpleNamespace
import unittest

from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from deep_agent.agent.headers import extract_forward_headers
from deep_agent.channels.app_context import channel_deepfos_app
from deep_agent.schemas.channel import ChannelAccountCreate, ChannelAccountUpdate


class ChannelAppContextTests(unittest.TestCase):
    def test_missing_disabled_and_unknown_settings_preserved(self):
        for settings in ({}, {"deepfos": None}, {"deepfos": {}},
                         {"deepfos": {"enabled": False, "app_id": "demo001"}}):
            self.assertIsNone(channel_deepfos_app(settings))
        settings = {"custom": {"keep": 1}, "wecom": {"user_details_enabled": False},
                    "deepfos": {"enabled": True, "app_id": "demo001", "future": 2}}
        for schema, base in ((ChannelAccountUpdate, {}), (ChannelAccountCreate, {
            "name": "test", "bot_id": "test", "secret": "fixture", "agent_id": "fixture",
            "session_user_id": "fixture",
        })):
            self.assertEqual(schema(**base, settings=settings).settings, settings)

    def test_invalid_settings_rejected_by_both_write_schemas(self):
        configs = [False, [], "demo001", {"enabled": "false"}, {"enabled": 1},
                   {"enabled": None}, {"enabled": True}, {"enabled": True, "app_id": ""},
                   {"app_id": 123}, {"app_id": "bad"}, {"app_id": " demo001"},
                   {"app_id": "de mo001"}, {"app_id": "demo001\n"}, {"app_id": "demo\x00001"}, {"app_id": "demo\u200b001"}]
        for config in configs:
            for schema, base in ((ChannelAccountUpdate, {}), (ChannelAccountCreate, {
                "name": "test", "bot_id": "test", "secret": "fixture", "agent_id": "fixture",
                "session_user_id": "fixture",
            })):
                with self.subTest(config=config, schema=schema.__name__):
                    with self.assertRaises(ValidationError):
                        schema(**base, settings={"deepfos": config})

    def test_channel_scope_ignores_incoming_overrides_even_when_disabled(self):
        request = Request({"type": "http", "headers": [
            (b"app", b"malformed"), (b"space", b"wrong"),
            (b"x-deep-agent-app", b"forged001"), (b"x-deep-agent-space", b"forged"),
            (b"authorization", b"fixture"),
        ]})
        for app in ("one001", "two002", None):
            account = SimpleNamespace(settings={"deepfos": {"enabled": app is not None, "app_id": app}})
            result = extract_forward_headers(request, channel_account=account)
            expected = {"authorization": "fixture", "language": "zh-cn"}
            if app:
                expected.update(app=app, space=app[:-3])
            self.assertEqual(result, expected)
        with self.assertRaises(HTTPException):
            extract_forward_headers(request, channel_account=SimpleNamespace(
                settings={"deepfos": {"enabled": True, "app_id": "broken"}}))

    def test_custom_allowlist_does_not_leak_channel_headers(self):
        request = Request({"type": "http", "headers": [(b"x-deep-agent-app", b"forged001")]})
        result = extract_forward_headers(request, allowed=frozenset({"x-deep-agent-*"}),
                                         channel_account=SimpleNamespace(settings={}))
        self.assertEqual(result, {})
