from __future__ import annotations

import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from deep_agent.structured_output.worker import validate


class StructuredOutputFeedbackTests(unittest.TestCase):
    def errors(self, schema, value):
        original = copy.deepcopy(value)
        errors = validate({"schema": schema, "value": value})
        self.assertEqual(value, original)
        self.assertTrue(all(set(error) == {"path", "constraint", "message"} for error in errors))
        self.assertTrue(all(len(field) <= 256 for error in errors for field in error.values()))
        self.assertLessEqual(len(errors), 20)
        return errors

    def test_nullable_object_reports_actual_leaf_limit_and_keeps_combination(self):
        schema = {"properties": {"recon_analysis": {"anyOf": [
            {"type": "object", "properties": {"questions": {"type": "array", "maxItems": 4}}},
            {"type": "null"},
        ]}}}
        errors = self.errors(schema, {"recon_analysis": {"questions": ["private question"] * 5}})
        self.assertTrue(any(error["constraint"] == "anyOf" for error in errors))
        leaf = next(error for error in errors if error["constraint"] == "maxItems")
        self.assertEqual(leaf["path"], "/recon_analysis/questions")
        self.assertIn("4", leaf["message"])
        self.assertIn("5", leaf["message"])
        self.assertFalse(any(error["constraint"] == "type" for error in errors))
        self.assertNotIn("private question", json.dumps(errors))

    def test_missing_and_unknown_properties_are_locatable_and_distinct(self):
        schema = {"type": "object", "properties": {"amount": {"type": "number"}, "code": {"type": "string"}},
                  "required": ["amount", "code"], "additionalProperties": False}
        errors = self.errors(schema, {"ammount": "private-value"})
        self.assertEqual({e["path"] for e in errors if e["constraint"] == "required"}, {"/amount", "/code"})
        self.assertTrue(any(e["constraint"] == "additionalProperties" and "ammount" in e["message"] for e in errors))
        self.assertNotIn("private-value", json.dumps(errors))

    def test_types_lengths_counts_and_numeric_limits_never_echo_candidate_values(self):
        cases = [
            ({"type": "integer"}, "private-value", ("integer", "string")),
            ({"type": "number"}, False, ("number", "boolean")),
            ({"maxLength": 3}, "secret-value", ("3", "12")),
            ({"minItems": 2}, [], ("2", "0")),
            ({"maxProperties": 1}, {"x": "private-value", "y": 2}, ("1", "2")),
            ({"minimum": 20}, 19, ("20",)),
            ({"exclusiveMaximum": 10}, 10, ("10",)),
        ]
        for schema, value, expected in cases:
            with self.subTest(schema=schema):
                errors = self.errors(schema, value)
                for part in expected:
                    self.assertIn(part, errors[0]["message"])
                self.assertNotIn("private-value", json.dumps(errors))
                self.assertNotIn("secret-value", json.dumps(errors))

    def test_schema_enum_const_pattern_and_format_explain_expected_constraint(self):
        for schema, expected in [({"enum": ["draft", "ready"]}, "draft"), ({"const": "ready"}, "ready"),
                                 ({"pattern": "^[A-Z]{2}$"}, "^[A-Z]{2}$"), ({"format": "uuid"}, "uuid")]:
            with self.subTest(schema=schema):
                errors = self.errors(schema, "private-value")
                self.assertIn(expected, errors[0]["message"])
                self.assertNotIn("private-value", json.dumps(errors))

    def test_combination_alternatives_stay_conditional_and_wrapped_null_branch_is_omitted(self):
        errors = self.errors({"anyOf": [{"type": "object", "required": ["a"]},
                                       {"type": "object", "required": ["b"]}]}, {})
        self.assertEqual([e["constraint"] for e in errors], ["anyOf", "required", "required"])
        self.assertEqual([e["path"] for e in errors[1:]], ["/a", "/b"])
        self.assertIn("branch 1", errors[1]["message"])
        self.assertIn("branch 2", errors[2]["message"])
        wrapped = self.errors({"anyOf": [{"type": "object", "required": ["a"]},
                                         {"allOf": [{"type": "null"}]}]}, {})
        self.assertEqual([e["constraint"] for e in wrapped], ["anyOf", "required"])

    def test_oneof_multiple_matching_branches_is_not_a_leaf_success(self):
        errors = self.errors({"oneOf": [{"type": "number"}, {"type": "integer"}]}, 4)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["constraint"], "oneOf")
        self.assertIn("exactly one", errors[0]["message"])
        self.assertIn("multiple", errors[0]["message"])

    def test_candidate_property_name_validation_and_unsafe_unknown_names_are_not_echoed(self):
        secret_name = "private contact@example.invalid\ncredential"
        for schema, value in [({"propertyNames": {"pattern": "^[A-Z]+$"}}, {secret_name: 1}),
                              ({"additionalProperties": False}, {secret_name: "secret-value"})]:
            with self.subTest(schema=schema):
                errors = self.errors(schema, value)
                self.assertTrue(errors)
                self.assertNotIn(secret_name, json.dumps(errors))
                self.assertNotIn("contact@example", json.dumps(errors))
                self.assertNotIn("secret-value", json.dumps(errors))

    def test_pattern_properties_diagnostic_does_not_repeat_regex_evaluation(self):
        with patch("deep_agent.structured_output.worker.re", SimpleNamespace()):
            errors = self.errors({"patternProperties": {"^allowed$": {"type": "integer"}},
                                  "additionalProperties": False}, {"other": "private-value"})
        self.assertEqual(errors[0]["constraint"], "additionalProperties")
        self.assertIn("patternProperties", errors[0]["message"])
        self.assertNotIn("private-value", str(errors))

    def test_pointer_escaping_deduplication_and_bounded_order_are_stable(self):
        schema = {"properties": {"a/b~c": {"type": "object", "required": ["first", "second"]}}}
        errors = self.errors(schema, {"a/b~c": {}})
        self.assertEqual([e["path"] for e in errors], ["/a~1b~0c/first", "/a~1b~0c/second"])
        self.assertEqual(errors, self.errors(schema, {"a/b~c": {}}))
        many = self.errors({"required": ["field_" + str(i) for i in range(30)]}, {})
        self.assertEqual(len(many), 20)
        self.assertEqual(len({(e["path"], e["constraint"], e["message"]) for e in many}), 20)

    def test_dependent_required_names_both_fields_without_echoing_values(self):
        errors = self.errors({"dependentRequired": {"kind": ["code"]}}, {"kind": "private-value"})
        self.assertIn("kind", errors[0]["message"])
        self.assertIn("code", errors[0]["message"])
        self.assertNotIn("private-value", json.dumps(errors))

    def test_dynamic_map_paths_keep_only_static_fields_and_array_indexes(self):
        schema = {"type": "object", "additionalProperties": {"type": "object", "properties": {
            "rows": {"type": "array", "items": {"type": "object", "properties": {
                "amount": {"type": "integer"}}, "required": ["amount"], "additionalProperties": False}},
        }}}
        value = {"private-person@example.invalid": {"rows": [{"amount": "private-value"}, {}, {"ammount": 1}]}}
        errors = self.errors(schema, value)
        self.assertCountEqual([(e["path"], e["constraint"]) for e in errors], [
            ("/<key>/rows/0/amount", "type"), ("/<key>/rows/1/amount", "required"),
            ("/<key>/rows/2/amount", "required"), ("/<key>/rows/2/ammount", "additionalProperties"),
        ])
        self.assertNotIn("private-person", str(errors))
        self.assertNotIn("private-value", str(errors))
        self.assertTrue(any('"ammount"' in e["message"] for e in errors))

    def test_combined_schemas_and_pattern_value_paths_do_not_expose_dynamic_keys(self):
        schema = {"allOf": [{"properties": {"groups": {"anyOf": [
            {"type": "object", "patternProperties": {".*": {"allOf": [
                {"properties": {"rows": {"type": "array", "items": {"oneOf": [
                    {"properties": {"a/b~c": {"type": "integer"}}}, {"type": "null"},
                ]}}}},
            ]}}}, {"type": "null"},
        ]}}}]}
        value = {"groups": {"private-group@example.invalid": {"rows": [{"a/b~c": "private-value"}]}}}
        # Formatting follows every possible pattern value Schema, not its regex.
        with patch("deep_agent.structured_output.worker.re", SimpleNamespace()):
            errors = self.errors(schema, value)
        leaf = next(e for e in errors if e["constraint"] == "type")
        self.assertEqual(leaf["path"], "/groups/<key>/rows/0/a~1b~0c")
        self.assertNotIn("private-group", str(errors))
        self.assertNotIn("private-value", str(errors))

    def test_long_dynamic_paths_remain_bounded_without_exposing_prefixes(self):
        dynamic_key = "private-user-" + "x" * 500
        schema = {"additionalProperties": {"additionalProperties": {
            "properties": {"amount": {"type": "integer"}},
        }}}
        errors = self.errors(schema, {dynamic_key: {dynamic_key: {"amount": "private-value"}}})
        self.assertEqual(errors[0]["path"], "/<key>/<key>/amount")
        self.assertNotIn("private-user", str(errors))
        self.assertLessEqual(len(errors[0]["path"]), 160)


if __name__ == "__main__":
    unittest.main()
