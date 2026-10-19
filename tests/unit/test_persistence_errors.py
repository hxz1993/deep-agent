from __future__ import annotations

import json
import unittest

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolReturnPart
from sqlalchemy import select

from deep_agent.db.models import ChatSession
from deep_agent.db.persistence_errors import ImmutableMessagePartConflict
from tests.helpers.sqlite_stores import make_sqlite_stores, seed_default_agent


class PersistenceErrorDiagnosticsTests(unittest.TestCase):
    def conflict(self, stored, incoming, **kwargs):
        return ImmutableMessagePartConflict(
            event_id="a" * 32, message_ordinal=2, part_index=0,
            stored_parts=stored, incoming_parts=incoming, **kwargs)

    def test_only_fixed_field_names_escape(self):
        secret = "PRIVATE_BODY_ARGUMENT_OR_CREDENTIAL"
        for field in ("content", "args", "tool_name", "tool_call_id", "provider_details"):
            with self.subTest(field=field):
                error = self.conflict(
                    [{"part_kind": "tool-return", field: secret}],
                    [{"part_kind": "tool-return", field: secret + "changed"}])
                self.assertIsInstance(error, ValueError)
                self.assertEqual(error.diagnostics["conflict_field"], field)
                self.assertEqual(error.diagnostics["persistence_reason"], "immutable_part_conflict")
                self.assertNotIn(secret, json.dumps(error.diagnostics) + str(error) + repr(error))
        error = self.conflict(
            [{"part_kind": secret, secret: secret}],
            [{"part_kind": secret, secret: "different"}])
        self.assertEqual(error.diagnostics["conflict_field"], "other")
        self.assertEqual(error.diagnostics["stored_part_kind"], "other")
        self.assertNotIn(secret, repr(error.__dict__) + repr(error))

    def test_metadata_diagnostics_never_expose_user_keys_or_values(self):
        secret = "PRIVATE_METADATA_KEY_AND_VALUE"
        error = self.conflict(
            [{"part_kind": "tool-return", "metadata": {secret: secret}}],
            [{"part_kind": "tool-return", "metadata": {secret: "changed"}}])
        self.assertEqual(error.diagnostics["conflict_field"], "metadata")
        self.assertNotIn(secret, repr(error.__dict__))
        standard = self.conflict(
            [{"part_kind": "tool-return", "metadata": {"deepagent_timing": {secret: secret}}}],
            [{"part_kind": "tool-return", "metadata": {"deepagent_timing": {secret: "changed"}}}])
        self.assertEqual(standard.diagnostics["conflict_field"], "metadata.deepagent_timing")
        self.assertNotIn(secret, repr(standard.__dict__))

    def test_part_count_and_untrusted_identifiers_are_bounded(self):
        error = ImmutableMessagePartConflict(
            event_id="PRIVATE_INVALID_EVENT_ID", message_ordinal="PRIVATE_ORDINAL", part_index=True,
            stored_parts=[], incoming_parts=[{"part_kind": "text", "content": "PRIVATE_TEXT"}])
        self.assertEqual(error.diagnostics["conflict_field"], "parts_count")
        self.assertEqual((error.diagnostics["stored_part_count"], error.diagnostics["incoming_part_count"]), (0, 1))
        self.assertIsNone(error.diagnostics["conflict_event_id"])
        self.assertIsNone(error.diagnostics["message_ordinal"])
        self.assertIsNone(error.diagnostics["part_index"])
        self.assertNotIn("PRIVATE", repr(error.__dict__) + repr(error))


class PersistenceErrorStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_conflict_rejects_write_and_rolls_back_all_changes(self):
        stores, engine, factory = await make_sqlite_stores()
        try:
            agent_id = await seed_default_agent(stores)
            store = stores.coordinator
            identity = dict(agent_id=agent_id, user_id="owner", run_id="synthetic-run", turn_id="synthetic-turn")
            secret = "PRIVATE_STORE_BODY_AND_CREDENTIAL"
            original = ModelRequest(parts=[ToolReturnPart("lookup", secret, "call-id")],
                metadata={"deepagent_message_ordinal": 2})
            first = await store.append_message_parts("session", messages=[original], **identity)
            before = await store.list_events("session")
            async with factory() as db:
                row = (await db.execute(select(ChatSession).where(ChatSession.session_id == "session"))).scalar_one()
                before_state = (row.next_sequence, row.updated_at)
            # The new assistant fact precedes the conflict in this transaction.
            # Neither it nor the session counter/time may survive the rejection.
            added = ModelResponse(parts=[TextPart("PRIVATE_NEW_FACT")],
                metadata={"deepagent_message_ordinal": 1})
            changed = ModelRequest(parts=[ToolReturnPart("lookup", secret + " changed", "call-id")],
                metadata={"deepagent_message_ordinal": 2})
            with self.assertRaises(ImmutableMessagePartConflict) as caught:
                await store.append_message_parts("session", messages=[added, changed], **identity)
            diagnostics = caught.exception.diagnostics
            self.assertEqual(diagnostics["conflict_event_id"], first[0].event_id)
            self.assertEqual((diagnostics["message_ordinal"], diagnostics["part_index"]), (2, 0))
            self.assertEqual(diagnostics["conflict_field"], "content")
            self.assertEqual(diagnostics["stored_part_kind"], "tool-return")
            self.assertNotIn("PRIVATE", json.dumps(diagnostics) + str(caught.exception))
            self.assertEqual(await store.list_events("session"), before)
            async with factory() as db:
                row = (await db.execute(select(ChatSession).where(ChatSession.session_id == "session"))).scalar_one()
                self.assertEqual((row.next_sequence, row.updated_at), before_state)
            # A failed conflict does not poison future idempotent saves.
            self.assertEqual(await store.append_message_parts("session", messages=[original], **identity), [])
        finally:
            await engine.dispose()
