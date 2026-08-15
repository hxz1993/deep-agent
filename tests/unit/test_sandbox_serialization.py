from __future__ import annotations

import datetime
import decimal
import enum
import importlib.util
import json
import unittest
import uuid
from fractions import Fraction

from deep_agent.sandbox.serialization import repr_value, serialize_result, to_json_value

_NUMPY_AVAILABLE = importlib.util.find_spec("numpy") is not None
_PANDAS_AVAILABLE = importlib.util.find_spec("pandas") is not None


class SandboxSerializationTests(unittest.TestCase):
    def test_scalar_types(self) -> None:
        self.assertIsNone(to_json_value(None))
        self.assertEqual(to_json_value(True), True)
        self.assertEqual(to_json_value("hello"), "hello")
        self.assertEqual(to_json_value(42), 42)
        self.assertEqual(to_json_value(3.14), 3.14)
        self.assertEqual(to_json_value(float("nan")), "nan")
        self.assertEqual(to_json_value(float("inf")), "inf")
        self.assertEqual(to_json_value(1 + 2j), "(1+2j)")

    def test_containers_and_frozenset(self) -> None:
        self.assertEqual(to_json_value({"a": 1}), {"a": 1})
        self.assertEqual(to_json_value([1, 2]), [1, 2])
        self.assertEqual(to_json_value((1, 2)), [1, 2])
        self.assertEqual(to_json_value({1, 2}), [1, 2])
        self.assertEqual(to_json_value(frozenset({1, 2})), [1, 2])

    def test_range_short_and_long(self) -> None:
        self.assertEqual(to_json_value(range(5)), [0, 1, 2, 3, 4])
        self.assertEqual(to_json_value(range(100)), "range(0, 100)")

    def test_python_types_not_repr_fallback(self) -> None:
        cases = [
            (decimal.Decimal("1.5"), "1.5"),
            (datetime.date(2024, 1, 2), "2024-01-02"),
            (datetime.datetime(2024, 1, 2, 3, 4, 5), "2024-01-02T03:04:05"),
            (datetime.time(3, 4, 5), "03:04:05"),
            (datetime.timedelta(hours=1), "1:00:00"),
            (uuid.UUID("550e8400-e29b-41d4-a716-446655440000"), "550e8400-e29b-41d4-a716-446655440000"),
            (Fraction(1, 3), "1/3"),
            (b"abc", "b'abc'"),
        ]
        for value, expected in cases:
            with self.subTest(value=type(value).__name__):
                result = to_json_value(value)
                self.assertEqual(result, expected)
                if not isinstance(value, bytes):
                    self.assertNotEqual(result, repr_value(value))

    def test_enum_recurses_value(self) -> None:
        class Color(enum.Enum):
            RED = 1

        self.assertEqual(to_json_value(Color.RED), 1)

    def test_circular_reference_marker(self) -> None:
        a: list = []
        a.append(a)
        serialized = to_json_value(a)
        self.assertEqual(serialized, ["[...]"])
        result, _ = serialize_result(a)
        self.assertIsInstance(result, str)
        assert isinstance(result, str)
        self.assertIn("[...]", result)

    @unittest.skipUnless(_NUMPY_AVAILABLE, "numpy not installed")
    def test_numpy_types(self) -> None:
        import math

        import numpy as np

        self.assertEqual(to_json_value(np.int64(7)), 7)
        self.assertTrue(math.isnan(float(to_json_value(np.float64(float("nan"))))))
        arr = np.array([1, 2, 3])
        preview, truncated = serialize_result(arr)
        self.assertFalse(truncated)
        self.assertIsInstance(preview, dict)
        assert isinstance(preview, dict)
        self.assertEqual(preview["kind"], "ndarray")
        self.assertEqual(preview["shape"], [3])
        self.assertEqual(len(preview["values"]), 3)

    @unittest.skipUnless(_PANDAS_AVAILABLE, "pandas not installed")
    def test_pandas_types(self) -> None:
        import pandas as pd

        ts = pd.Timestamp("2024-01-02T03:04:05")
        self.assertEqual(to_json_value(ts), "2024-01-02T03:04:05")
        self.assertIsNone(to_json_value(pd.NA))
        self.assertIsNone(to_json_value(pd.NaT))

        df = pd.DataFrame({"a": [1, 2]})
        preview, _ = serialize_result(df)
        self.assertIsInstance(preview, dict)
        assert isinstance(preview, dict)
        self.assertEqual(preview["kind"], "dataframe")

        series = pd.Series([1, 2], name="s")
        preview, _ = serialize_result(series)
        self.assertIsInstance(preview, dict)
        assert isinstance(preview, dict)
        self.assertEqual(preview["kind"], "series")

    def test_serialize_result_respects_byte_limit(self) -> None:
        big = "x" * 600_000
        result, truncated = serialize_result(big)
        self.assertTrue(truncated)
        self.assertLessEqual(
            len(json.dumps(result, ensure_ascii=False).encode("utf-8")),
            512 * 1024,
        )


if __name__ == "__main__":
    unittest.main()
