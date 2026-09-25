"""Execution errors expose categories, never provider payloads or arguments."""
from __future__ import annotations

import json
import unittest

from pydantic_ai.exceptions import ModelHTTPError, UnexpectedModelBehavior, UsageLimitExceeded

from deep_agent.agent.message_persistence import MessagePersistenceError
from deep_agent.agent.runner import _safe_run_failure
from deep_agent.services.codex_provider import CodexUnavailable


class ExecutionErrorDiagnosticTests(unittest.TestCase):
    def test_provider_http_status_survives_without_body_model_name_headers_or_credentials(self):
        secret = "SYNTHETIC_CREDENTIAL_MUST_NOT_PERSIST"
        error = ModelHTTPError(403, f"model-{secret}", body={"token": secret, "prompt": "PRIVATE_ARGUMENT"},
                               headers={"Authorization": f"Bearer {secret}"})
        safe = _safe_run_failure(error)
        self.assertEqual((safe["code"], safe["phase"]), ("provider_http_error", "provider"))
        self.assertIn("403", safe["message"])
        self.assertNotIn(secret, json.dumps(safe))
        self.assertNotIn("PRIVATE_ARGUMENT", json.dumps(safe))

    def test_categories_and_phases_do_not_fall_back_to_raw_messages(self):
        cases = [
            (TimeoutError("PRIVATE_TIMEOUT_ARGUMENT"), "execution_timeout", "execution"),
            (UsageLimitExceeded("PRIVATE_USAGE_ARGUMENT"), "usage_limit_exceeded", "model"),
            (UnexpectedModelBehavior("PRIVATE_PROTOCOL_ARGUMENT"), "unexpected_model_behavior", "model"),
            (MessagePersistenceError("PRIVATE_DATABASE_ARGUMENT"), "message_persistence_failed", "persistence"),
            (ValueError("PRIVATE_UNEXPECTED_ARGUMENT"), "agent_run_failed", "agent"),
            (CodexUnavailable("PRIVATE_AUTH_ARGUMENT"), "provider_authorization_unavailable", "provider"),
        ]
        for error, code, phase in cases:
            with self.subTest(code=code):
                safe = _safe_run_failure(error)
                self.assertEqual((safe["code"], safe["phase"]), (code, phase))
                self.assertNotIn("PRIVATE_", json.dumps(safe))
                self.assertEqual(safe["error_type"], type(error).__name__)

    def test_tool_retry_exhaustion_records_bounded_tool_and_limit_not_argument_tail(self):
        safe = _safe_run_failure(UnexpectedModelBehavior(
            "Tool 'docs_read' exceeded max retries count of 3. PRIVATE_TOOL_ARGUMENT"))
        self.assertEqual((safe["code"], safe["phase"], safe["tool"], safe["max_retries"]),
                         ("tool_retry_exhausted", "tool_validation", "docs_read", 3))
        self.assertNotIn("PRIVATE_TOOL_ARGUMENT", json.dumps(safe))

    def test_untrusted_error_tool_name_is_not_copied(self):
        safe = _safe_run_failure(UnexpectedModelBehavior(
            "Tool 'docs_read SECRET=bad' exceeded max retries count of 3."))
        self.assertEqual(safe["code"], "unexpected_model_behavior")
        self.assertNotIn("tool", safe)
        self.assertNotIn("SECRET", json.dumps(safe))
