from __future__ import annotations

import asyncio
import os
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from deep_agent.remote_agents.cli import CliAdapter
from deep_agent.remote_agents.contracts import CodeBuddyConfig, CodexConfig, InputFile, RemoteAgentError, RemoteState


_CLI_FIXTURE = r"""
import json
import os
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
inputs = [Path(path) for path in values["New attachments"]]
if message == "slow":
    time.sleep(120)
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
    (output / "unlisted.txt").write_text("ignore")
    data = {"state": "completed", "text": "Difference is 20.", "files": ["out.csv", "outside", "../escape"]}
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

    def adapter(self, protocol):
        command = self.root / protocol
        command.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(_CLI_FIXTURE), encoding="utf-8")
        command.chmod(0o700)
        config = (
            CodexConfig(command=str(command), sandbox="workspace-write")
            if protocol == "codex" else CodeBuddyConfig(command=str(command))
        )
        return CliAdapter(config, self.root / f"{protocol}-state")

    async def checkpoint(self, state):
        self.checkpoints.append(state)

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

    async def test_malformed_structured_response_is_checked(self):
        adapter = self.adapter("codebuddy")
        with self.assertRaisesRegex(RemoteAgentError, "格式无效"):
            await adapter.run(RemoteState(), "invalid", [], "start", self.checkpoint, message_id="invalid")
