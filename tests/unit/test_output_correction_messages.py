from __future__ import annotations

import copy
import json
import unittest

from deep_agent.structured_output.feedback import (
    OutputArgumentsError, correction_feedback, decode_output_arguments, protocol_error,
)


class OutputCorrectionMessagesTests(unittest.TestCase):
    def test_strict_json_and_native_root_semantics(self):
        for schema, raw, expected in (
            ({"type": "object"}, '{"ok":false}', {"ok": False}),
            ({"type": "array"}, '{"response":[0,false,null]}', [0, False, None]),
            ({"type": "boolean"}, {"response": False}, False),
            ({"type": "string"}, {"response": ""}, ""),
            ({"type": "null"}, {"response": None}, None),
        ):
            with self.subTest(schema=schema, raw=raw):
                original = copy.deepcopy(raw)
                self.assertEqual(decode_output_arguments(raw, schema), expected)
                self.assertEqual(raw, original)

    def test_diagnostics_preserve_codes_and_do_not_copy_candidates(self):
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
        cases = (
            ('{"ok":false,"ok":true}', "duplicateKey", "ok"),
            ('{"ok":NaN}', "json", "finite"),
            ('{"ok":Infinity}', "json", "finite"),
            ('{"ok":"PRIVATE_PAYLOAD"', "json", "column"),
            ('{"PRIVATE_DYNAMIC_KEY":1,"PRIVATE_DYNAMIC_KEY":2}', "duplicateKey", "only once"),
        )
        for raw, constraint, phrase in cases:
            with self.subTest(constraint=constraint, raw=raw):
                with self.assertRaises(OutputArgumentsError) as caught:
                    decode_output_arguments(raw, schema)
                error = caught.exception.error
                self.assertEqual(error["code"], "invalid_json")
                self.assertEqual(error["constraint"], constraint)
                self.assertIn(phrase, error["message"])
                self.assertNotIn("PRIVATE", json.dumps(error))
                self.assertTrue(all(len(field) <= 256 for field in error.values()))

    def test_missing_response_wrapper_explains_actual_contract(self):
        for raw in ({}, [], None):
            with self.subTest(raw=raw), self.assertRaises(OutputArgumentsError) as caught:
                decode_output_arguments(raw, {"type": "array"})
            self.assertEqual(caught.exception.error["path"], "/response")
            self.assertEqual(caught.exception.error["constraint"], "wrapper")

    def test_correction_budget_keeps_json_and_original_errors(self):
        errors = [{"path": "/" + str(i) + "x" * 150, "constraint": "required",
                   "message": "Missing configured property " + "y" * 220} for i in range(20)]
        original = copy.deepcopy(errors)
        feedback = correction_feedback("schema_validation_failed", errors, "business_result")
        payload = json.loads(feedback)
        self.assertLessEqual(len(feedback), 6000)
        self.assertEqual(payload["code"], "schema_validation_failed")
        self.assertIn("business_result", payload["instruction"])
        self.assertEqual(len(payload["errors"]) + payload["truncated_errors"], len(errors))
        self.assertEqual(errors, original)

    def test_protocol_actions_use_configured_name_and_keep_legacy_codes(self):
        for reason in ("missing_output", "mixed_output_calls", "multiple_output_calls"):
            with self.subTest(reason=reason):
                error = protocol_error(reason, "business_result")
                payload = json.loads(correction_feedback(reason, [error], "business_result"))
                self.assertEqual(error["code"], reason)
                self.assertEqual(payload["code"], reason)
                self.assertIn("business_result", payload["instruction"])
                self.assertTrue(payload["errors"])

    def test_complete_patch_contracts_are_not_prohibited(self):
        patch = [{"op": "replace", "path": "/rows/0/quantity", "value": 2}]
        self.assertEqual(decode_output_arguments({"response": patch}, {"type": "array"}), patch)
        feedback = json.loads(correction_feedback("invalid_json", [], "bom_patch_result"))
        self.assertIn("entire configured result", feedback["instruction"])
        self.assertNotIn("or patches", feedback["instruction"])


if __name__ == "__main__":
    unittest.main()
