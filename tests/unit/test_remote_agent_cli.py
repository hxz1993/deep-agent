from __future__ import annotations

import asyncio
import os
import shlex
import shutil
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from deep_agent.remote_agents.cli import CliAdapter
from deep_agent.remote_agents.contracts import (
    CodeBuddyConfig, CodexConfig, InputFile, RemoteAgentError, RemoteState, RemoteTaskExpired,
)


_CLI_FIXTURE = r"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

protocol = Path(sys.argv[0]).name
args = sys.argv[1:]
if args == ["--version"]:
    print("1.2.3")
    sys.exit(0)
start = not any(part in args for part in ("resume", "--resume"))
native = "actual-native-session-id"
if not start:
    assert native in args
if protocol == "codex":
    assert 'sandbox_mode="workspace-write"' in args
    first = {"type": "thread.started", "thread_id": native}
else:
    first = {"type": "system", "subtype": "init", "session_id": native}
print(json.dumps(first), flush=True)
prompt = sys.stdin.read()
header, message = prompt.split("<task_or_reply>\n")
message = message.removesuffix("\n</task_or_reply>")
values = {
    line.split(": ", 1)[0]: json.loads(line.split(": ", 1)[1])
    for line in header.splitlines() if line.startswith(("Output directory:", "New attachments:"))
}
output = Path(values["Output directory"])
inputs = [Path(path) for path in values.get("New attachments", [])]
assert Path.cwd() == output.parent.parent
if not inputs:
    assert "New attachments" not in values
    assert not (Path.cwd() / "inputs" / output.name).exists()
    assert output.is_dir()
if message == "slow":
    time.sleep(120)
if message == "expired":
    if protocol == "codex":
        print(f"Error: thread/resume failed: no rollout found for thread id {native}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps({"type": "error", "error": f"No conversation found with session ID: {native}"}))
    sys.exit(0)
if message == "group-child":
    subprocess.Popen([sys.executable, "-c", "import signal, sys, time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); Path(sys.argv[1]).touch(); time.sleep(12)",
        str(output / "child-ready")])
    sys.exit(0)
if message == "invalid":
    data = {"state": [], "text": "invalid status", "files": []}
elif message == "start":
    assert inputs[0].read_bytes() == b"original"
    assert os.environ.get("DEEP_AGENT_TEST_SECRET") is None
    data = {"state": "needs_input", "text": "Which month?", "files": []}
elif message == "first":
    data = {"state": "needs_input", "text": "Which region?", "files": []}
elif message == "second":
    assert inputs[0].read_bytes() == b"follow-up"
    (output / "out.csv").write_bytes(b"diff\n20\n")
    (output / "outside").symlink_to(inputs[0])
    os.link(inputs[0], output / "hardlink")
    (output / "unlisted.txt").write_text("ignore")
    data = {"state": "completed", "text": "Difference is 20.", "files": ["out.csv", "outside", "hardlink", "../escape"]}
else:
    data = {"state": "completed", "text": "October", "files": []}
if protocol == "codex":
    print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(data)}}))
    print(json.dumps({"type": "turn.completed"}))
else:
    print(json.dumps({"type": "result", "subtype": "success", "session_id": native, "structured_output": data}))
"""


class RemoteCliAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp = tempfile.TemporaryDirectory(prefix="TMP_to_delete-remote-cli-test-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.checkpoints = []

    def adapter(self, protocol, *, workspace_root=None):
        command = self.root / protocol
        command.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(_CLI_FIXTURE), encoding="utf-8")
        command.chmod(0o700)
        config = (
            CodexConfig(command=str(command), sandbox="workspace-write", workspace_root=workspace_root)
            if protocol == "codex" else CodeBuddyConfig(command=str(command), workspace_root=workspace_root)
        )
        return CliAdapter(config, self.root / f"{protocol}-state")

    async def checkpoint(self, state):
        self.checkpoints.append(state)

    async def test_custom_root_is_created_on_dispatch_and_isolates_parallel_jobs(self):
        root = self.root / "custom root"
        adapter = self.adapter("codex", workspace_root=str(root))
        self.assertEqual((await adapter.inspect())["workspace_root"], str(root.resolve()))
        self.assertFalse(root.exists())
        root.mkdir(mode=0o755)
        results = await asyncio.gather(*(
            adapter.run(RemoteState(), "plain task", [InputFile("same.txt", "text/plain", contents)],
                        "start", self.checkpoint, message_id="first")
            for contents in (b"one", b"two")
        ))
        self.assertEqual([result.state.status for result in results], ["completed", "completed"])
        self.assertNotEqual(results[0].state.task_id, results[1].state.task_id)
        for result, contents in zip(results, (b"one", b"two"), strict=True):
            turn = result.state.cursor["turn_id"]
            work = root / result.state.task_id / "work"
            self.assertEqual(work.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual((work / "inputs" / turn / "01-same.txt").read_bytes(), contents)
            self.assertTrue((work / "outputs" / turn).is_dir())
        removed = results[0].state
        shutil.rmtree(root / removed.task_id)
        with self.assertRaisesRegex(RemoteTaskExpired, "原任务工作目录不可用"):
            await adapter.run(removed, "resume", [], "resume", self.checkpoint, message_id="next")
        self.assertFalse((root / removed.task_id).exists())

    def test_ssh_passes_roots_to_the_remote_execution_account(self):
        for configured, expected in ((None, "~/.deep-agent/remote-agent-workspaces"),
                                     ("/srv/agent work", "/srv/agent work")):
            config = CodeBuddyConfig(execution="ssh", ssh_host="runner", workspace_root=configured)
            adapter = CliAdapter(config, self.root)
            remote_command = shlex.split(adapter._args("prepare", "a" * 32, "b" * 32)[-1])
            self.assertEqual(remote_command[-4], expected)

    async def test_two_pauses_native_resume_and_explicit_regular_artifacts(self):
        for protocol in ("codex", "codebuddy"):
            with self.subTest(protocol=protocol):
                adapter = self.adapter(protocol)
                inspection = await adapter.inspect()
                self.assertEqual((inspection["execution"], inspection["version"]), ("local", "1.2.3"))
                with patch.dict(os.environ, {"DEEP_AGENT_TEST_SECRET": "fixture"}):
                    started = await adapter.run(
                        RemoteState(), "start", [InputFile("source.csv", "text/csv", b"original")],
                        "start", self.checkpoint, message_id="start",
                    )
                self.assertEqual((started.state.status, started.text), ("input_required", "Which month?"))
                self.assertEqual(self.checkpoints[-2].session_id, "actual-native-session-id")
                recovered_adapter = CliAdapter(adapter.config, adapter.workspace_root)
                recovered = await recovered_adapter.refresh(started.state, self.checkpoint)
                self.assertEqual(recovered, started)
                next_question = await recovered_adapter.run(
                    recovered.state, "first", [], "answer", self.checkpoint, message_id="first",
                )
                self.assertEqual((next_question.state.status, next_question.text), ("input_required", "Which region?"))
                self.assertEqual(next_question.state.task_id, started.state.task_id)
                self.assertEqual(next_question.state.session_id, started.state.session_id)
                self.assertNotEqual(next_question.state.question_id, started.state.question_id)
                completed = await recovered_adapter.run(
                    next_question.state, "second", [InputFile("region.txt", "text/plain", b"follow-up")],
                    "answer", self.checkpoint, message_id="second",
                )
                self.assertEqual(completed.state.status, "completed")
                self.assertEqual([(f.name, f.mime, f.data) for f in completed.files], [("out.csv", "text/csv", b"diff\n20\n")])
                self.assertTrue(completed.warnings)
                self.assertEqual(await recovered_adapter.refresh(completed.state, self.checkpoint), completed)
                resumed = await recovered_adapter.run(
                    completed.state, "more", [], "resume", self.checkpoint, message_id="more",
                )
                self.assertEqual(resumed.state.session_id, started.state.session_id)
                self.assertEqual(resumed.text, "October")

    async def test_cancel_requires_exit_confirmation(self):
        adapter = self.adapter("codex")
        native_started = asyncio.Event()

        async def checkpoint(state):
            await self.checkpoint(state)
            if state.session_id:
                native_started.set()

        call = asyncio.create_task(
            adapter.run(RemoteState(), "slow", [], "start", checkpoint, message_id="slow")
        )
        try:
            await asyncio.wait_for(native_started.wait(), 5)
            canceled = await CliAdapter(adapter.config, adapter.workspace_root).cancel(self.checkpoints[-1])
            self.assertEqual(canceled.state.status, "canceled")
            self.assertEqual((await asyncio.wait_for(call, 5)).state.status, "canceled")
        finally:
            if not call.done() and self.checkpoints:
                await adapter.cancel(self.checkpoints[-1])
                await asyncio.wait_for(call, 5)

    async def test_cancel_before_cli_starts_or_after_it_asks_for_input(self):
        adapter = self.adapter("codebuddy")
        prepared = asyncio.Event()
        start_helper = asyncio.Event()

        async def checkpoint(state):
            await self.checkpoint(state)
            prepared.set()
            await start_helper.wait()

        call = asyncio.create_task(
            adapter.run(RemoteState(), "invalid", [], "start", checkpoint, message_id="never-run")
        )
        try:
            await asyncio.wait_for(prepared.wait(), 5)
            canceled = await adapter.cancel(self.checkpoints[-1])
            self.assertEqual(canceled.state.status, "canceled")
        finally:
            start_helper.set()
            finished = await asyncio.wait_for(call, 5)
        self.assertEqual(finished.state, canceled.state)

        with patch.dict(os.environ, {"DEEP_AGENT_TEST_SECRET": "fixture"}):
            waiting = await adapter.run(
                RemoteState(), "start", [InputFile("source.csv", "text/csv", b"original")],
                "start", self.checkpoint, message_id="asks-first",
            )
        self.assertEqual(waiting.state.status, "input_required")
        canceled = await adapter.cancel(waiting.state)
        self.assertEqual(canceled.state.status, "canceled")
        self.assertEqual((await adapter.refresh(canceled.state, self.checkpoint)).state, canceled.state)

    async def test_malformed_structured_response_is_checked(self):
        adapter = self.adapter("codebuddy")
        with self.assertRaisesRegex(RemoteAgentError, "格式无效"):
            await adapter.run(RemoteState(), "invalid", [], "start", self.checkpoint, message_id="invalid")

    async def test_resumed_turn_recheck_and_cancel_wait_for_the_cli_group(self):
        adapter = self.adapter("codex")
        completed = await adapter.run(RemoteState(), "done", [], "start", self.checkpoint, message_id="first")
        recovered = CliAdapter(adapter.config, adapter.workspace_root)
        call = asyncio.create_task(
            adapter.run(completed.state, "group-child", [], "resume", self.checkpoint, message_id="next")
        )
        try:
            async with asyncio.timeout(5):
                while not list(adapter.workspace_root.glob("*/work/outputs/*/child-ready")):
                    await asyncio.sleep(0.05)
            state = self.checkpoints[-1]
            replay = await recovered.run(state, "group-child", [], "resume", self.checkpoint, message_id="next")
            self.assertEqual(replay.state.status, "working")
            self.assertEqual(replay.state.cursor, state.cursor)
            canceled = await recovered.cancel(replay.state)
            self.assertEqual(canceled.state.status, "canceled")
            self.assertEqual((await asyncio.wait_for(call, 3)).state.status, "canceled")
        finally:
            if not call.done():
                await asyncio.wait_for(call, 15)

    async def test_malformed_artifact_preserves_other_files(self):
        adapter = self.adapter("codebuddy")
        with patch.object(adapter, "_helper", new=AsyncMock(return_value={"good.txt": "YWJj", "bad.txt": "!"})):
            files, warnings = await adapter._artifacts("0" * 32, "1" * 32, ["good.txt", "\ud800", "bad.txt"])
        self.assertEqual([(file.name, file.data) for file in files], [("good.txt", b"abc")])
        self.assertTrue(warnings)

    async def test_expired_native_session_has_a_distinct_error_without_retaining_cli_errors(self):
        for protocol in ("codex", "codebuddy"):
            with self.subTest(protocol=protocol):
                adapter = self.adapter(protocol)
                result = await adapter.run(RemoteState(), "done", [], "start", self.checkpoint, message_id="done")
                with self.assertRaisesRegex(RemoteTaskExpired, "原任务不可继续"):
                    await adapter.run(result.state, "expired", [], "resume", self.checkpoint, message_id="expired")
                snapshot = await adapter._helper("snapshot", *adapter._ids(self.checkpoints[-1]), {})
                self.assertTrue(snapshot["exit"]["session_expired"])
                self.assertNotIn("found", snapshot["events"])
