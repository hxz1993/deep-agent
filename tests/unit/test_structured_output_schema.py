from __future__ import annotations

import copy
import unittest

from deep_agent.structured_output.schema import (
    CACHE_SIZE, MAX_SCHEMA_BYTES, _normalize_cached, expand_schema, json_bytes,
    normalize_output_config, normalize_schema, schema_digest, validate_output_name,
)


class StructuredOutputSchemaTests(unittest.TestCase):
    def test_optional_outer_required_inner_and_annotations_are_preserved(self):
        schema = {"type": "object", "properties": {"contract": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}, "x-render": {"color": "blue"}}
        source = copy.deepcopy(schema)
        actual = normalize_schema(schema)
        self.assertEqual(actual, source)
        actual["properties"].clear()
        self.assertEqual(schema, source)
        self.assertEqual(normalize_schema(source), source)

    def test_empty_root_and_root_json_types_are_supported(self):
        for schema in ({}, {"type": "object", "properties": {}}, {"type": "array", "items": {"type": "integer"}}, {"type": "null"}, {"anyOf": [{"type": "string"}, {"type": "number"}]}):
            with self.subTest(schema=schema):
                self.assertEqual(normalize_schema(schema), schema)
        for schema in (True, False, [], None, "object"):
            with self.subTest(schema=schema), self.assertRaises(ValueError):
                normalize_schema(schema)

    def test_dialect_formats_and_dynamic_references_are_explicit(self):
        for schema in ({"$schema": "http://json-schema.org/draft-07/schema#"}, {"format": "email"}, {"$dynamicRef": "#node"}, {"$dynamicAnchor": "node"}, {"$recursiveRef": "#"}, {"$defs": {"inner": {"$id": "https://other/schema"}}}):
            with self.subTest(schema=schema), self.assertRaises(ValueError):
                normalize_schema(schema)
        for fmt in ("date", "date-time", "time", "uuid"):
            self.assertEqual(normalize_schema({"type": "string", "format": fmt})["format"], fmt)

    def test_external_missing_and_cyclic_references_are_rejected_without_io(self):
        schemas = [
            {"$ref": "https://127.0.0.1/private"}, {"$ref": "file:///etc/passwd"},
            {"$ref": "#/properties/elsewhere"}, {"$ref": "#/$defs/missing"},
            {"$defs": {"A": {"$ref": "#/$defs/A"}}},
            {"$defs": {"A": {"$ref": "#/$defs/B"}, "B": {"$ref": "#/$defs/A"}}},
        ]
        for schema in schemas:
            with self.subTest(schema=schema), self.assertRaises(ValueError):
                normalize_schema(schema)

    def test_local_pointer_escaping_and_ref_sibling_constraints(self):
        schema = {"$defs": {"A/B~C": {"type": "string", "minLength": 1}}, "type": "object", "properties": {"a": {"$ref": "#/$defs/A~1B~0C", "maxLength": 5}}}
        expanded = expand_schema(schema)
        self.assertEqual(expanded["properties"]["a"]["allOf"], [{"type": "string", "minLength": 1}, {"maxLength": 5}])
        self.assertNotIn("$defs", expanded)
        self.assertIn("$defs", normalize_schema(schema))

    def test_ref_like_business_annotation_values_are_not_executed(self):
        schema = {"const": {"$ref": "https://example.invalid", "format": "email"}, "default": {"$dynamicRef": "#evil"}}
        self.assertEqual(normalize_schema(schema), schema)

    def test_invalid_schema_and_patterns_fail_without_candidate_values(self):
        for schema in ({"type": "made-up"}, {"required": "sensitive-value"}, {"pattern": "["}, {"pattern": "x" * 513}, {"patternProperties": {"[": {}}}):
            with self.subTest(schema=schema):
                with self.assertRaises(ValueError) as caught:
                    normalize_schema(schema)
                self.assertNotIn("sensitive-value", str(caught.exception))

    def test_name_and_description_limits(self):
        for name in ("a", "a" * 64, "lease_result", "A0"):
            self.assertEqual(validate_output_name(name), name)
        for name in ("", "1a", "_a", "a-b", "汉字", "a" * 65, None):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_output_name(name)
        config = {"name": "a", "description": "汉" * 8192, "json_schema": {}}
        self.assertEqual(normalize_output_config(config), config)
        config["description"] += "汉"
        with self.assertRaises(ValueError):
            normalize_output_config(config)

    def test_schema_byte_boundary(self):
        overhead = len(json_bytes({"description": ""}))
        for delta in (-1, 0):
            schema = {"description": "x" * (MAX_SCHEMA_BYTES - overhead + delta)}
            self.assertEqual(len(json_bytes(normalize_schema(schema))), MAX_SCHEMA_BYTES + delta)
        with self.assertRaises(ValueError):
            normalize_schema({"description": "x" * (MAX_SCHEMA_BYTES - overhead + 1)})

    def test_node_property_enum_and_depth_boundaries(self):
        for size in (2045, 2046):
            self.assertEqual(len(normalize_schema({"const": [0] * size})["const"]), size)
        with self.assertRaises(ValueError):
            normalize_schema({"const": [0] * 2047})
        for count in (511, 512):
            self.assertEqual(len(normalize_schema({"properties": {f"f{i}": {} for i in range(count)}})["properties"]), count)
        with self.assertRaises(ValueError):
            normalize_schema({"properties": {f"f{i}": {} for i in range(513)}})
        for size in (1023, 1024):
            self.assertEqual(len(normalize_schema({"enum": list(range(size))})["enum"]), size)
        with self.assertRaises(ValueError):
            normalize_schema({"enum": list(range(1025))})
        value = 0
        for _ in range(14):
            value = [value]
        normalize_schema({"x-note": value})
        with self.assertRaises(ValueError):
            normalize_schema({"x-note": [value]})

    def test_reference_expansion_is_also_bounded(self):
        schema = {"$defs": {"A": {"description": "x" * 3000}}, "properties": {f"f{i}": {"$ref": "#/$defs/A"} for i in range(25)}}
        self.assertLess(len(json_bytes(schema)), MAX_SCHEMA_BYTES)
        with self.assertRaises(ValueError):
            normalize_schema(schema)

    def test_cache_bounded_digest_canonical_and_defensive_copies(self):
        _normalize_cached.cache_clear()
        self.assertEqual(schema_digest({"type": "object", "title": "x"}), schema_digest({"title": "x", "type": "object"}))
        for i in range(CACHE_SIZE + 3):
            normalize_schema({"title": str(i)})
        self.assertEqual(_normalize_cached.cache_info().currsize, CACHE_SIZE)

    def test_strict_json_rejects_python_values_and_accepts_shared_subtrees(self):
        repeated = {"type": "string"}
        normalize_schema({"properties": {"a": repeated, "b": repeated}})
        cyclic = {}; cyclic["loop"] = cyclic
        for value in ({1: "x"}, {"x": (1, 2)}, {"x": float("nan")}, {"x": float("inf")}, cyclic):
            with self.subTest(type=type(value)), self.assertRaises(ValueError):
                json_bytes(value)


if __name__ == "__main__":
    unittest.main()
