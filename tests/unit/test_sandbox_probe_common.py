"""Unit tests for sandbox qualification harness (stage B)."""

from __future__ import annotations

import unittest

from scripts.sandbox_probe.attacks import ATTACKS, ATTACK_BY_ID, ATTACK_IDS
from scripts.sandbox_probe.common import (
    AttackRunRecord,
    BaselineSnapshot,
    ProbeRunResult,
    aggregate_attack_group,
    assert_cleanup,
    classify_outcome,
    evaluate_attack,
    parse_probe_marker,
)


class TestSandboxProbeAttacks(unittest.TestCase):
    def test_attack_ids_are_unique_and_complete(self) -> None:
        ids = [item.attack_id for item in ATTACKS]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(tuple(ids), ATTACK_IDS)

    def test_expected_outcomes_cover_plan_matrix(self) -> None:
        by_id = {item.attack_id: item.expected for item in ATTACKS}
        self.assertEqual(by_id["read_env"], "denied")
        self.assertEqual(by_id["fork_pressure"], "PID_LIMIT")
        self.assertEqual(by_id["allocate_600m"], "MEMORY_LIMIT")
        self.assertEqual(by_id["infinite_loop"], "TIMEOUT")
        self.assertEqual(by_id["infinite_output"], "OUTPUT_LIMIT")

    def test_marker_attacks_include_probe_preamble(self) -> None:
        for spec in ATTACKS:
            if spec.attack_id in {
                "allocate_600m",
                "infinite_loop",
                "infinite_output",
            }:
                continue
            self.assertIn("PROBE_DENIED", spec.code, spec.attack_id)


class TestSandboxProbeMarkers(unittest.TestCase):
    def test_parse_probe_denied(self) -> None:
        parsed = parse_probe_marker("noise\nPROBE_DENIED:1")
        self.assertEqual(parsed, ("denied", "1"))

    def test_parse_probe_inconclusive(self) -> None:
        parsed = parse_probe_marker("PROBE_INCONCLUSIVE:111")
        self.assertEqual(parsed, ("inconclusive", "111"))

    def test_parse_probe_skip(self) -> None:
        parsed = parse_probe_marker("PROBE_SKIP:no_service_account_mount")
        self.assertEqual(parsed, ("skip", "no_service_account_mount"))

    def test_parse_probe_leak(self) -> None:
        parsed = parse_probe_marker("PROBE_LEAK:connected")
        self.assertEqual(parsed, ("ok", "connected"))


class TestSandboxProbeClassification(unittest.TestCase):
    def test_classify_memory_limit_flag(self) -> None:
        result = ProbeRunResult(
            attack_id="allocate_600m",
            stdout="",
            stderr="",
            exit_code=137,
            memory_limited=True,
        )
        self.assertEqual(classify_outcome(result), "MEMORY_LIMIT")

    def test_marker_denied_beats_generic_stderr(self) -> None:
        result = ProbeRunResult(
            attack_id="connect_loopback",
            stdout="PROBE_DENIED:1",
            stderr="ConnectionRefusedError",
            exit_code=1,
        )
        self.assertEqual(classify_outcome(result), "denied")

    def test_connection_refused_without_marker_is_inconclusive(self) -> None:
        result = ProbeRunResult(
            attack_id="connect_loopback",
            stdout="",
            stderr="ConnectionRefusedError: [Errno 111] Connection refused",
            exit_code=1,
        )
        self.assertEqual(classify_outcome(result), "inconclusive")

    def test_ptrace_blocked_marker_counts_as_denied(self) -> None:
        result = ProbeRunResult(
            attack_id="ptrace",
            stdout="PROBE_DENIED:1",
            stderr="",
            exit_code=0,
        )
        record = evaluate_attack(ATTACK_BY_ID["ptrace"], result)
        self.assertEqual(record.status, "pass")
        self.assertEqual(record.observed, "denied")

    def test_evaluate_attack_passes_on_expected_outcome(self) -> None:
        spec = ATTACK_BY_ID["infinite_loop"]
        result = ProbeRunResult(
            attack_id=spec.attack_id,
            stdout="",
            stderr="",
            exit_code=None,
            timed_out=True,
        )
        record = evaluate_attack(spec, result)
        self.assertEqual(record.status, "pass")
        self.assertEqual(record.observed, "TIMEOUT")

    def test_evaluate_attack_fails_on_probe_leak(self) -> None:
        spec = ATTACK_BY_ID["read_env"]
        result = ProbeRunResult(
            attack_id=spec.attack_id,
            stdout="PROBE_LEAK:env:KUBERNETES_SERVICE_HOST='10.0.0.1'",
            stderr="",
            exit_code=0,
        )
        record = evaluate_attack(spec, result)
        self.assertEqual(record.status, "fail")
        self.assertEqual(record.observed, "ok")

    def test_evaluate_attack_marks_inconclusive_as_fail(self) -> None:
        spec = ATTACK_BY_ID["connect_public"]
        result = ProbeRunResult(
            attack_id=spec.attack_id,
            stdout="PROBE_INCONCLUSIVE:111",
            stderr="",
            exit_code=0,
        )
        record = evaluate_attack(spec, result)
        self.assertEqual(record.status, "fail")
        self.assertEqual(record.observed, "inconclusive")

    def test_service_account_skip(self) -> None:
        spec = ATTACK_BY_ID["read_service_account"]
        result = ProbeRunResult(
            attack_id=spec.attack_id,
            stdout="PROBE_SKIP:no_service_account_mount",
            stderr="",
            exit_code=0,
        )
        record = evaluate_attack(spec, result)
        self.assertEqual(record.status, "skip")
        self.assertEqual(record.observed, "skip")

    def test_sandbox_pidns_proc1_counts_as_denied(self) -> None:
        spec = ATTACK_BY_ID["read_host_proc"]
        result = ProbeRunResult(
            attack_id=spec.attack_id,
            stdout="PROBE_DENIED:sandbox_pidns",
            stderr="",
            exit_code=0,
        )
        record = evaluate_attack(spec, result)
        self.assertEqual(record.status, "pass")
        self.assertEqual(record.observed, "denied")

    def test_host_systemd_proc1_counts_as_leak(self) -> None:
        spec = ATTACK_BY_ID["read_host_proc"]
        result = ProbeRunResult(
            attack_id=spec.attack_id,
            stdout="PROBE_LEAK:host_proc1:'/sbin/init'",
            stderr="",
            exit_code=0,
        )
        record = evaluate_attack(spec, result)
        self.assertEqual(record.status, "fail")
        self.assertEqual(record.observed, "ok")


class TestGoJudgeOutputLimitMapping(unittest.TestCase):
    def test_truncated_stdout_maps_to_output_limit(self) -> None:
        from scripts.sandbox_probe.go_judge import DEFAULT_STDOUT_LIMIT, _map_go_judge_result

        item = {
            "status": "Nonzero Exit Status",
            "exitStatus": 120,
            "files": {"stdout": "x" * DEFAULT_STDOUT_LIMIT, "stderr": ""},
        }
        result = _map_go_judge_result(
            item,
            attack_id="infinite_output",
            duration_ms=1,
            stdout_limit=DEFAULT_STDOUT_LIMIT,
        )
        self.assertTrue(result.output_limited)
        self.assertFalse(result.denied)
        self.assertEqual(classify_outcome(result), "OUTPUT_LIMIT")


class TestSandboxProbeAggregation(unittest.TestCase):
    def test_aggregate_skips_do_not_fail_group(self) -> None:
        records = [
            AttackRunRecord(
                attack_id="read_service_account",
                expected="denied",
                observed="skip",
                status="skip",
                result=ProbeRunResult(
                    attack_id="read_service_account",
                    stdout="PROBE_SKIP:x",
                    stderr="",
                    exit_code=0,
                ),
            ),
            AttackRunRecord(
                attack_id="read_env",
                expected="denied",
                observed="denied",
                status="pass",
                result=ProbeRunResult(
                    attack_id="read_env",
                    stdout="PROBE_DENIED:env_isolated",
                    stderr="",
                    exit_code=0,
                ),
            ),
        ]
        self.assertEqual(aggregate_attack_group(records), "pass")

    def test_aggregate_inconclusive_fails_group(self) -> None:
        records = [
            AttackRunRecord(
                attack_id="connect_loopback",
                expected="denied",
                observed="inconclusive",
                status="fail",
                result=ProbeRunResult(
                    attack_id="connect_loopback",
                    stdout="PROBE_INCONCLUSIVE:111",
                    stderr="",
                    exit_code=0,
                ),
                detail="inconclusive",
            ),
        ]
        self.assertEqual(aggregate_attack_group(records), "fail")


class TestSandboxProbeCleanup(unittest.TestCase):
    def test_assert_cleanup_passes_when_baseline_unchanged(self) -> None:
        snapshot = BaselineSnapshot(
            pids=frozenset({1, 2}),
            cgroup_paths=frozenset(),
            temp_dirs=frozenset(),
            captured_at=0.0,
        )
        status, detail = assert_cleanup(snapshot, snapshot)
        self.assertEqual(status, "pass")
        self.assertEqual(detail, "baseline restored")

    def test_assert_cleanup_fails_on_leaked_pids(self) -> None:
        before = BaselineSnapshot(
            pids=frozenset({1}),
            cgroup_paths=frozenset(),
            temp_dirs=frozenset(),
            captured_at=0.0,
        )
        after = BaselineSnapshot(
            pids=frozenset({1, 99}),
            cgroup_paths=frozenset(),
            temp_dirs=frozenset(),
            captured_at=1.0,
        )
        status, detail = assert_cleanup(before, after)
        self.assertEqual(status, "fail")
        self.assertIn("new pids", detail)


class TestSandboxProbeHostCgroupVerify(unittest.TestCase):
    def test_resolve_host_cgroup_containers_dir_explicit(self) -> None:
        from pathlib import Path

        from scripts.sandbox_probe.common import resolve_host_cgroup_containers_dir

        path = resolve_host_cgroup_containers_dir("/tmp")
        self.assertIsNotNone(path)
        self.assertEqual(path, Path("/tmp"))

    def test_resolve_host_cgroup_containers_dir_missing(self) -> None:
        from scripts.sandbox_probe.common import resolve_host_cgroup_containers_dir

        path = resolve_host_cgroup_containers_dir("/definitely-missing-cgroup-dir")
        self.assertIsNone(path)


if __name__ == "__main__":
    unittest.main()
