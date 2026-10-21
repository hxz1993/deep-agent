from __future__ import annotations

import asyncio
import copy
import os
import time
import unittest
from unittest.mock import patch

from deep_agent.structured_output import validation
from deep_agent.structured_output.validation import OutputValidationServiceError, validate_output


class OutputValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_optional_combination_required_inner_no_defaults_or_coercion(self):
        schema = {"type": "object", "properties": {"contract": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}, "text": {"type": "string", "default": "never-added"}, "count": {"type": "integer"}}, "additionalProperties": False}
        for value in ({}, {"contract": {"name": "合同"}}, {"contract": {"name": "合同"}, "text": "正文"}, {"text": ""}, {"count": 0}):
            original = copy.deepcopy(value)
            self.assertEqual(await validate_output(schema, value), [])
            self.assertEqual(value, original)
        for value in ({"contract": {}}, {"contract": None}, {"count": "0"}, {"count": False}, {"extra": 1}):
            self.assertTrue(await validate_output(schema, value))

    async def test_array_scalar_null_false_zero_and_boolean_subschemas(self):
        for schema, value in [({"type": "array", "items": {"type": "integer"}}, [0, 1]), ({"type": "null"}, None), ({"type": "boolean"}, False), ({"type": "number"}, 0), ({"properties": {"blocked": False}}, {})]:
            with self.subTest(schema=schema):
                self.assertEqual(await validate_output(schema, value), [])
        self.assertTrue(await validate_output({"properties": {"blocked": False}}, {"blocked": 1}))

    async def test_empty_array_string_and_additional_property_semantics(self):
        for schema, value in (({"type": "array"}, []), ({"type": "string"}, ""), ({"type": "object"}, {"extra": True}), ({"additionalProperties": True}, {"extra": True})):
            self.assertEqual(await validate_output(schema, value), [])
        for schema, value in (({"minItems": 1}, []), ({"minLength": 1}, ""), ({"additionalProperties": False}, {"extra": True})):
            self.assertTrue(await validate_output(schema, value))

    async def test_output_utf8_byte_limit_exact_boundaries_and_deep_value(self):
        for size in (1024 * 1024 - 3, 1024 * 1024 - 2):
            self.assertEqual(await validate_output({}, "x" * size), [])
        self.assertEqual((await validate_output({}, "x" * (1024 * 1024 - 1)))[0]["constraint"], "maxBytes")
        self.assertEqual(await validate_output({}, "汉😀\n\\\""), [])
        # A deeply nested Python value cannot exhaust the event loop stack or
        # turn into a truncated successful result when the JSON encoder stops.
        value = None
        for _ in range(1100):
            value = [value]
        errors = await validate_output({}, value)
        self.assertTrue(not errors or errors[0]["constraint"] == "json")
        depth, remaining = 0, value
        while isinstance(remaining, list):
            depth += 1
            remaining = remaining[0]
        self.assertEqual((depth, remaining), (1100, None))

    async def test_standard_combinations_conditions_and_refs_are_enforced(self):
        schema = {"$defs": {"N": {"type": "integer", "minimum": 1}}, "type": "object", "properties": {"n": {"$ref": "#/$defs/N", "maximum": 3}, "kind": {"enum": ["a", "b"]}}, "if": {"properties": {"kind": {"const": "a"}}, "required": ["kind"]}, "then": {"required": ["n"]}, "not": {"required": ["bad"]}}
        self.assertEqual(await validate_output(schema, {"kind": "a", "n": 2}), [])
        for value in ({"kind": "a"}, {"kind": "b", "n": 0}, {"n": 4}, {"bad": True}):
            self.assertTrue(await validate_output(schema, value))
        for key in ("anyOf", "oneOf", "allOf"):
            self.assertTrue(await validate_output({key: [{"type": "integer"}, {"minimum": 10}]}, "bad") if key == "allOf" else await validate_output({key: [{"type": "integer"}, {"type": "null"}]}, "bad"))

    async def test_supported_formats_are_checked_without_optional_packages(self):
        examples = {"date": ("2024-02-29", "2025-02-29"), "date-time": ("2026-10-22T12:34:56+08:00", "2026-10-22T12:34:56"), "time": ("12:34:56Z", "25:34:56Z"), "uuid": ("a1234567-1234-4321-8123-123456789abc", "a1234567123443218123123456789abc")}
        for fmt, (good, bad) in examples.items():
            with self.subTest(format=fmt):
                self.assertEqual(await validate_output({"format": fmt}, good), [])
                self.assertTrue(await validate_output({"format": fmt}, bad))

    async def test_errors_bounded_and_do_not_echo_values(self):
        errors = await validate_output({"items": {"type": "integer"}}, ["private-contract-content"] * 30)
        self.assertEqual(len(errors), 20)
        self.assertTrue(all(len(field) <= 256 for error in errors for field in error.values()))
        self.assertNotIn("private-contract-content", str(errors))

    async def test_non_json_and_output_size_are_candidate_errors_without_worker(self):
        with patch.object(validation.asyncio, "create_subprocess_exec", side_effect=AssertionError("must not spawn")):
            for value in (float("nan"), float("inf"), {1: "bad"}, (1, 2)):
                self.assertEqual((await validate_output({}, value))[0]["constraint"], "json")
            self.assertEqual((await validate_output({}, "x" * (1024 * 1024)))[0]["constraint"], "maxBytes")

    async def test_invalid_saved_schema_is_service_failure(self):
        with self.assertRaises(OutputValidationServiceError) as caught:
            await validate_output({"$ref": "https://example.invalid"}, {})
        self.assertEqual(caught.exception.code, "invalid_schema")

    async def test_worker_start_failure_is_service_failure_and_slot_is_released(self):
        with patch.object(validation.asyncio, "create_subprocess_exec", side_effect=OSError("private failure")):
            with self.assertRaises(OutputValidationServiceError) as caught:
                await validate_output({}, {})
        self.assertEqual(caught.exception.code, "worker_start_failed")
        self.assertNotIn("private", str(caught.exception))
        self.assertEqual(await validate_output({}, {}), [])

    async def test_catastrophic_regex_times_out_kills_reaps_and_does_not_starve_loop(self):
        processes = []
        spawn = asyncio.create_subprocess_exec
        async def capture(*args, **kwargs):
            proc = await spawn(*args, **kwargs); processes.append(proc); return proc
        heartbeats = 0
        async def heartbeat():
            nonlocal heartbeats
            while True:
                heartbeats += 1
                await asyncio.sleep(0.02)
        beating = asyncio.create_task(heartbeat())
        started = time.monotonic()
        try:
            with patch.object(validation.asyncio, "create_subprocess_exec", side_effect=capture):
                with self.assertRaises(OutputValidationServiceError) as caught:
                    await validate_output({"type": "string", "pattern": "^(a+)+$"}, "a" * 34 + "!")
            self.assertEqual(caught.exception.code, "validation_timeout")
            self.assertLess(time.monotonic() - started, 3.5)
            self.assertGreater(heartbeats, 20)
            self.assertLess(processes[0].returncode, 0)
            with self.assertRaises(ProcessLookupError):
                os.kill(processes[0].pid, 0)
        finally:
            beating.cancel()
            await asyncio.gather(beating, return_exceptions=True)

    async def test_cancel_reaps_worker_and_frees_capacity(self):
        started = asyncio.Event(); processes = []
        spawn = asyncio.create_subprocess_exec
        async def capture(*args, **kwargs):
            proc = await spawn(*args, **kwargs); processes.append(proc); started.set(); return proc
        with patch.object(validation.asyncio, "create_subprocess_exec", side_effect=capture):
            task = asyncio.create_task(validate_output({"pattern": "^(a+)+$"}, "a" * 34 + "!"))
            await asyncio.wait_for(started.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertLess(processes[0].returncode, 0)
        self.assertEqual(await validate_output({}, {}), [])

    async def test_per_worker_capacity_queue_full_wait_timeout_and_cancel(self):
        capacity = validation._capacity()
        await capacity.slots.acquire(); await capacity.slots.acquire()
        tasks = [asyncio.create_task(validate_output({}, {})) for _ in range(16)]
        try:
            await asyncio.sleep(0)
            with self.assertRaises(OutputValidationServiceError) as caught:
                await validate_output({}, {})
            self.assertEqual(caught.exception.code, "queue_full")
            tasks[0].cancel()
            await asyncio.gather(tasks[0], return_exceptions=True)
            self.assertEqual(capacity.waiting, 15)
            with patch.object(validation, "QUEUE_TIMEOUT", 0.01):
                with self.assertRaises(OutputValidationServiceError) as timed:
                    await validate_output({}, {})
                self.assertEqual(timed.exception.code, "queue_timeout")
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            capacity.slots.release(); capacity.slots.release()
        self.assertEqual(capacity.waiting, 0)


if __name__ == "__main__":
    unittest.main()
