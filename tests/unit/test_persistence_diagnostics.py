from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pydantic_ai.messages import ModelRequest, ToolReturnPart
from sqlalchemy.exc import IntegrityError

from deep_agent.db.persistence_errors import ImmutableMessagePartConflict
from deep_agent.diagnostics.persistence import persistence_failure_fields
from deep_agent.diagnostics.schema import safe_fields, sanitize_record
from deep_agent.diagnostics.text import format_record, parse_record
from deep_agent.diagnostics.query import query_logs
from tests.unit import test_message_persistence as fixture


class PersistenceDiagnosticsTests(unittest.TestCase):
    def test_traceback_walk_is_bounded_to_256_frames_and_two_locations(self):
        from deep_agent.diagnostics.persistence import _PACKAGE
        visited = []
        class Trace:
            def __init__(self, index):
                self.index = index
                self.tb_lineno = index
                self.tb_frame = SimpleNamespace(f_code=SimpleNamespace(
                    co_filename=_PACKAGE + "db/synthetic.py", co_name=f"frame_{index}"))
            @property
            def tb_next(self):
                visited.append(self.index)
                return Trace(self.index + 1)
        class DeepError(Exception):
            @property
            def __traceback__(self):
                return Trace(0)
        facts = persistence_failure_fields(DeepError("PRIVATE_ERROR"))
        self.assertEqual(len(visited), 256)
        self.assertEqual(facts["failure_location"], "db/synthetic.py:255:frame_255")
        self.assertEqual(facts["failure_caller"], "db/synthetic.py:254:frame_254")
        self.assertNotIn("PRIVATE", json.dumps(facts))

    def test_malformed_diagnostic_input_is_best_effort(self):
        facts = persistence_failure_fields(ValueError("PRIVATE_ERROR"), messages=[object()])
        self.assertIsInstance(facts, dict)
        self.assertNotIn("PRIVATE", json.dumps(facts))

    def test_driver_details_are_classified_without_strings_parameters_or_sql(self):
        class DriverError(Exception):
            sqlstate = '23000'
            def __str__(self): raise AssertionError('must not stringify')
        error = IntegrityError('PRIVATE_SQL', {'secret':'PRIVATE_PARAMETERS'}, DriverError(1062, 'PRIVATE_VALUE'))
        facts = persistence_failure_fields(error, hook='terminal')
        self.assertEqual(facts['persistence_reason'], 'database_integrity')
        self.assertEqual(facts['database_error_code'], 1062)
        self.assertEqual(facts['database_sqlstate'], '23000')
        self.assertNotIn('PRIVATE', json.dumps(facts))

    def test_typed_conflict_roundtrips_the_real_text_and_read_sanitizer(self):
        error = ImmutableMessagePartConflict(event_id='a'*32, message_ordinal=2, part_index=0,
            stored_parts=[{'part_kind':'tool-return','metadata':{'deepagent_timing':{'secret':'PRIVATE_OLD'}}}],
            incoming_parts=[{'part_kind':'tool-return','metadata':{'deepagent_timing':{'secret':'PRIVATE_NEW'}}}])
        messages=[ModelRequest(parts=[ToolReturnPart('tool','PRIVATE_BODY','call')])]
        store=SimpleNamespace(_session_factory=SimpleNamespace(kw={'bind':SimpleNamespace(dialect=SimpleNamespace(name='mysql'))}))
        facts = persistence_failure_fields(error, store=store,messages=messages,hook='wrap_run_event_stream')
        record={'event':'message_persistence_failed','summary':'', 'timestamp':'2026-10-19T00:00:00+00:00',
                'source':'api','level':'ERROR','process_id':123,'record_id':'a'*32,'boot_id':'b'*32,
                'instance_id':'c'*16,'session_id':'session','run_id':'run','error_type':type(error).__name__,
                'phase':'incremental',**facts}
        encoded=format_record(record)
        decoded=parse_record(encoded)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded['persistence_reason'],'immutable_part_conflict')
        self.assertEqual(decoded['conflict_field'],'metadata.deepagent_timing')
        self.assertEqual(decoded['db_dialect'],'mysql')
        self.assertEqual(decoded['message_ordinal'],2)
        self.assertEqual(decoded['write_part_count'],1)
        self.assertIn('product_version',decoded)
        self.assertIn('pydantic_ai_version',decoded)
        self.assertNotIn('PRIVATE',encoded)
        self.assertEqual(sanitize_record(decoded)['conflict_event_id'],'a'*32)
        with TemporaryDirectory(prefix='TMP_to_delete-persistence-log-') as temporary:
            directory = Path(temporary)
            (directory / f"da-{'c'*16}-api-123-{'b'*32}-000001.log").write_text(encoded+'\n')
            config = SimpleNamespace(directory=directory, instance_id='c'*16, enabled=True, retention_days=7)
            result = query_logs(config, start='2026-10-19T00:00:00Z', end='2026-10-20T00:00:00Z',
                                session_id='session', run_id='run')
            self.assertEqual(len(result['items']), 1)
            self.assertEqual(result['items'][0]['conflict_field'], 'metadata.deepagent_timing')
            self.assertEqual(result['items'][0]['part_index'], 0)
            self.assertNotIn('PRIVATE', json.dumps(result))

    def test_specialized_fields_do_not_leak_arbitrary_values_or_other_events(self):
        hostile={'conflict_field':'PRIVATE_KEY','write_hook':'PRIVATE_VALUE',
                 'failure_location':'/Users/PRIVATE/file.py:12:foo','database_sqlstate':'PRIVATE',
                 'conflict_event_id':'PRIVATE','database_error_code':True,'write_part_count':-1}
        self.assertEqual(safe_fields(hostile,event='message_persistence_failed'),{})
        self.assertEqual(safe_fields({'message_ordinal':3,'write_hook':'terminal'},event='agent_run_failed'),{})


class PersistenceFailureLoggingTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixture.MessagePersistenceCapabilityTests.setUp
    capability = fixture.MessagePersistenceCapabilityTests.capability
    asyncTearDown = fixture.MessagePersistenceCapabilityTests.asyncTearDown
    async def test_failure_is_still_rejected_and_emits_safe_location_and_hook(self):
        cap=self.capability()
        cap._results['call']=ToolReturnPart('lookup','PRIVATE_BODY','call')
        async def reject(*args,**kwargs):
            raise ImmutableMessagePartConflict(event_id='a'*32,message_ordinal=1,part_index=0,
                stored_parts=[{'part_kind':'tool-return','content':'PRIVATE_OLD'}],
                incoming_parts=[{'part_kind':'tool-return','content':'PRIVATE_NEW'}])
        rows=[]
        with patch.object(self.store,'append_message_parts',side_effect=reject), patch('deep_agent.diagnostics.emit',side_effect=lambda name,**kw:rows.append((name,kw))):
            from deep_agent.agent.message_persistence import MessagePersistenceError
            with self.assertRaises(MessagePersistenceError):
                await cap._flush(self.ctx,hook='wrap_run_event_stream')
            with self.assertRaises(MessagePersistenceError):
                await cap._flush(self.ctx,hook='after_node_run')
        failures=[fields for name,fields in rows if name=='message_persistence_failed']
        self.assertEqual(len(failures),1)
        self.assertEqual(failures[0]['persistence_reason'],'immutable_part_conflict')
        self.assertEqual(failures[0]['write_hook'],'wrap_run_event_stream')
        self.assertEqual(failures[0]['write_stage'],'append')
        self.assertTrue(failures[0]['failure_location'].startswith('agent/message_persistence.py:'))
        self.assertEqual(failures[0]['conflict_field'],'content')
        self.assertNotIn('PRIVATE',json.dumps(rows))
