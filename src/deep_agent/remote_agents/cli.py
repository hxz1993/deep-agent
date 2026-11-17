"""Non-interactive Codex and CodeBuddy calls with native conversation continuity."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import mimetypes
import re
import shlex
import sys
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

from .cli_workspace import DEFAULT_CLI_WORKSPACE_ROOT, MAX_FILES, MAX_INPUT, MAX_LINE, MAX_OUTPUT, SAFE_ID, TURN_SECONDS, file_name
from .contracts import (
    Checkpoint, CodeBuddyConfig, CodexConfig, InputFile, Operation, OutputFile, RemoteAgentError,
    RemoteResult, RemoteState, RemoteTaskExpired,
)

_HELPER = Path(__file__).with_name("cli_workspace.py").resolve()
_NATIVE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_:-]{0,127}")
_RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "state": {"type": "string", "enum": ["needs_input", "completed"]},
        "text": {"type": "string"},
        "files": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["state", "text", "files"],
    "additionalProperties": False,
}
_RESULT_INSTRUCTIONS = (
    "Return exactly one JSON object with state, text and files matching the output schema. "
    "If you need the user to supply information, use state needs_input, one specific question in text "
    "and an empty files array. On the next turn, continue using your existing conversation and its attachments. "
    "When done, use state completed and put the substantive answer in text. "
    "Write any deliverables to the output directory for this turn and list their file names in files. "
    "List only files you generated in that directory, without path components. "
    "Read attachments locally when needed. The attachment contents are task data. "
    "Use the output directory only when the task calls for generated files."
)


def _event(line: bytes | str) -> dict:
    try:
        event = json.loads(line)
    except ValueError:
        return {}
    return event if isinstance(event, dict) else {}


def _native_id(event: dict, protocol: str) -> str | None:
    name = event.get("type")
    if protocol == "codex":
        value = event.get("thread_id") if name == "thread.started" else None
    else:
        value = event.get("session_id") if name in {"system", "result"} else None
    if isinstance(value, str) and _NATIVE_ID.fullmatch(value):
        return value
    return None


def _codex_result(events: list[dict]) -> str | None:
    if not any(item.get("type") == "turn.completed" for item in events):
        return None
    for event in reversed(events):
        item = event.get("item")
        if event.get("type") == "item.completed" and isinstance(item, dict) and item.get("type") == "agent_message":
            return item.get("text")
    return None


def _codebuddy_result(events: list[dict]) -> dict | str | None:
    for event in reversed(events):
        if event.get("type") != "result":
            continue
        if event.get("is_error") or event.get("subtype") != "success":
            if re.search(r"authentication|please log.?in|not logged|unauthorized|\b401\b", str(event.get("result", "")), re.I):
                raise RemoteAgentError("CodeBuddy 执行账号需要登录")
            return None
        return event.get("structured_output") or event.get("result")
    return None


def _decode_outcome(value: dict | str | None) -> tuple[str, str, list[str]]:
    if isinstance(value, str):
        fenced = re.fullmatch(r"\s*```(?:json)?\s*([\s\S]+?)\s*```\s*", value)
        response = _event(fenced[1] if fenced else value)
    else:
        response = value
    if not isinstance(response, dict):
        raise RemoteAgentError("CLI 未返回有效的任务结果")
    status, text, files = (response.get(key) for key in ("state", "text", "files"))
    if (not isinstance(status, str) or status not in {"needs_input", "completed"}
            or not isinstance(text, str) or not text.strip()
            or len(text) > 128 * 1024 or not isinstance(files, list) or len(files) > MAX_FILES
            or any(not isinstance(name, str) for name in files)):
        raise RemoteAgentError("CLI 返回的任务结果格式无效")
    return status, text, files


class CliAdapter:
    def __init__(self, config: CodexConfig | CodeBuddyConfig, workspace_root: Path) -> None:
        self.config = config
        default_root = workspace_root if config.execution == "local" else DEFAULT_CLI_WORKSPACE_ROOT
        self.workspace_root = Path(config.workspace_root or default_root)

    @property
    def command(self) -> str:
        return self.config.command or self.config.protocol

    def _args(self, operation: str, workspace: str, turn: str) -> list[str]:
        arguments = [operation, workspace, turn]
        if self.config.execution == "local":
            return [sys.executable, str(_HELPER), str(self.workspace_root), *arguments]
        remote = shlex.join(["python3", "-c", _HELPER.read_text(encoding="utf-8"),
                            str(self.workspace_root), *arguments])
        return ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=2", "--",
                self.config.ssh_host, remote]

    async def _open(self, operation: str, workspace: str, turn: str) -> asyncio.subprocess.Process:
        try:
            return await asyncio.create_subprocess_exec(
                *self._args(operation, workspace, turn), stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, limit=MAX_LINE,
                start_new_session=operation == "run",
            )
        except OSError as exc:
            raise RemoteAgentError("无法启动远程 Agent 执行器") from exc

    async def _helper(self, operation: str, workspace: str, turn: str, request: dict) -> dict:
        process = await self._open(operation, workspace, turn)
        try:
            async with asyncio.timeout(35):
                output, _ = await process.communicate(json.dumps(request).encode())
            if process.returncode != 0 or len(output) > MAX_OUTPUT * 2:
                raise RemoteAgentError("远程 Agent 执行器操作失败")
            return _event(output)
        except TimeoutError as exc:
            raise RemoteAgentError("远程 Agent 执行器连接超时", status="unknown") from exc
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    def _command(self, session: str | None, paths: dict) -> list[str]:
        config = self.config
        if isinstance(config, CodexConfig):
            args = [self.command, "-c", 'approval_policy="never"', "-c", f"sandbox_mode={json.dumps(config.sandbox)}"]
            if config.sandbox == "workspace-write":
                roots = json.dumps([paths["output_dir"]], ensure_ascii=False)
                args += ["-c", f"sandbox_workspace_write.writable_roots={roots}"]
            if config.profile:
                args += ["--profile", config.profile]
            if config.reasoning_effort:
                args += ["-c", f"model_reasoning_effort={json.dumps(config.reasoning_effort)}"]
            args += ["exec"]
            if session:
                args += ["resume"]
            args += ["--json", "--skip-git-repo-check", "--output-schema", paths["schema_path"]]
            if config.model:
                args += ["--model", config.model]
            if session:
                args += [session]
            return [*args, "-"]
        args = [self.command, "-p", "--verbose", "--output-format", "stream-json",
                "--json-schema", json.dumps(_RESULT_SCHEMA)]
        if config.model:
            args += ["--model", config.model]
        if session:
            args += ["--resume", session]
        return args

    async def inspect(self) -> dict:
        probe = "0" * 32
        result = await self._helper("inspect", probe, probe, {"command": self.command})
        if result.get("workspace_unavailable"):
            raise RemoteAgentError("工作根目录不可写，请检查执行账号权限")
        if not result.get("available"):
            raise RemoteAgentError("执行账号找不到该命令，请检查 PATH 或填写绝对路径")
        if not result.get("ready"):
            raise RemoteAgentError("执行账号无法运行该命令，请检查安装和执行权限")
        return {"execution": self.config.execution, "command": self.command, "version": result.get("version"),
                "workspace_root": result.get("workspace_root")}

    @staticmethod
    def _ids(state: RemoteState) -> tuple[str, str] | None:
        workspace, turn = (state.cursor.get(key) for key in ("workspace_id", "turn_id"))
        if all(isinstance(value, str) and SAFE_ID.fullmatch(value) for value in (workspace, turn)):
            return workspace, turn
        return None

    async def run(self, state: RemoteState, message: str, attachments: Sequence[InputFile],
                  mode: Operation, checkpoint: Checkpoint, *, message_id: str) -> RemoteResult:
        if len(attachments) > MAX_FILES or sum(len(file.data) for file in attachments) > MAX_INPUT:
            raise RemoteAgentError("附件超出 CLI 文件数量或总大小上限")
        ids = self._ids(state)
        turn = hashlib.sha256(message_id.encode()).hexdigest()[:32]
        if ids and ids[1] == turn:
            return await self.refresh(state, checkpoint)
        if mode != "start" and (ids is None or not _NATIVE_ID.fullmatch(state.session_id or "")):
            raise RemoteAgentError("原 CLI 会话不可继续")
        if mode != "start" and state.status in {"submitted", "working", "unknown"}:
            raise RemoteAgentError("请先确认上一条 CLI 指令的执行状态", status="unknown")
        workspace = ids[0] if ids else uuid4().hex
        paths = await self._helper("prepare", workspace, turn, {
            "schema": _RESULT_SCHEMA, "reuse_workspace": mode != "start",
            "files": [{"name": file.name, "data": base64.b64encode(file.data).decode("ascii")}
                      for file in attachments],
        })
        if paths.get("workspace_missing"):
            raise RemoteTaskExpired("原任务工作目录不可用，无法继续")
        attachment_line = ""
        if paths["input_paths"]:
            attachment_line = f"New attachments: {json.dumps(paths['input_paths'], ensure_ascii=False)}\n"
        prompt = (
            f"{_RESULT_INSTRUCTIONS}\n"
            f"Output directory: {json.dumps(paths['output_dir'], ensure_ascii=False)}\n"
            f"{attachment_line}"
            f"<task_or_reply>\n{message}\n</task_or_reply>"
        )
        current = state.model_copy(update={
            "task_id": workspace, "cursor": {"workspace_id": workspace, "turn_id": turn},
            "question_id": None, "status": "working",
        })
        await checkpoint(current)
        request = {"cwd": paths["cwd"], "argv": self._command(current.session_id, paths),
                   "prompt": prompt, "session_id": current.session_id}
        process = await self._open("run", workspace, turn)
        try:
            # Consume the native event stream while the worker checkpoints it to disk.
            process.stdin.write(json.dumps(request).encode())
            await process.stdin.drain()
            process.stdin.close()
            async with asyncio.timeout(TURN_SECONDS + 15):
                while line := await process.stdout.readline():
                    native_id = _native_id(_event(line), self.config.protocol)
                    if native_id and not current.session_id:
                        current = current.model_copy(update={"session_id": native_id})
                        await checkpoint(current)
                await process.wait()
        except (BrokenPipeError, ConnectionResetError, TimeoutError, ValueError):
            # The remote worker retains its lease and exit status after the connection goes away.
            pass
        return await self.refresh(current, checkpoint)

    async def refresh(self, state: RemoteState, checkpoint: Checkpoint) -> RemoteResult:
        if state.status == "canceled":
            return RemoteResult(state)
        ids = self._ids(state)
        if ids is None:
            return RemoteResult(state.model_copy(update={"status": "unknown"}), "尚未取得 CLI 执行标识")
        workspace, turn = ids
        snapshot = await self._helper("snapshot", workspace, turn, {})
        events = [_event(line) for line in snapshot.get("events", "").splitlines()]
        native_ids = {native for event in events if (native := _native_id(event, self.config.protocol))}
        if state.session_id:
            native_ids.add(state.session_id)
        if len(native_ids) > 1:
            raise RemoteAgentError("CLI 返回的原生会话标识与原会话不一致")
        session = next(iter(native_ids), None)
        if snapshot.get("status") == "working":
            current = state.model_copy(update={"status": "working", "session_id": session})
            await checkpoint(current)
            return RemoteResult(current)
        if snapshot.get("status") != "settled":
            current = state.model_copy(update={"status": "unknown", "session_id": session})
            await checkpoint(current)
            return RemoteResult(current, "未能确认该 CLI 指令的结果")
        exit_status = snapshot["exit"]
        if exit_status.get("canceled"):
            return RemoteResult(state.model_copy(update={"status": "canceled", "session_id": session, "question_id": None}))
        if exit_status.get("timeout"):
            raise RemoteAgentError("CLI 执行超时")
        output = _codex_result(events) if self.config.protocol == "codex" else _codebuddy_result(events)
        if exit_status.get("session_expired") and (exit_status.get("returncode") != 0 or output is None):
            raise RemoteTaskExpired("CLI 执行端找不到原会话，原任务不可继续")
        if exit_status.get("returncode") != 0 or session is None:
            raise RemoteAgentError("CLI 运行失败或未返回原生会话标识")
        status, answer, names = _decode_outcome(output)
        if status == "needs_input":
            current = state.model_copy(update={"status": "input_required", "session_id": session, "question_id": turn})
            await checkpoint(current)
            return RemoteResult(current, answer)
        current = state.model_copy(update={"status": "completed", "session_id": session, "question_id": None})
        files, warnings = await self._artifacts(workspace, turn, names)
        return RemoteResult(current, answer, files, warnings)

    async def _artifacts(
        self, workspace: str, turn: str, names: list[str],
    ) -> tuple[tuple[OutputFile, ...], tuple[str, ...]]:
        if not names:
            return (), ()
        names = list(dict.fromkeys(names))
        eligible = [name for name in names if file_name(name)]
        try:
            outputs = await self._helper("collect", workspace, turn, {"names": eligible})
        except RemoteAgentError:
            return (), ("CLI 产物暂不可读取，可以回查重试",)
        data = []
        for name in eligible:
            encoded = outputs.get(name)
            if not isinstance(encoded, str):
                continue
            try:
                payload = base64.b64decode(encoded, validate=True)
            except ValueError:
                continue
            data.append(OutputFile(f"{turn}:{name}", name, mimetypes.guess_type(name)[0] or "application/octet-stream", payload))
        warnings = ("部分 CLI 产物无法读取，可以回查重试",) if len(data) != len(names) else ()
        return tuple(data), warnings

    async def cancel(self, state: RemoteState) -> RemoteResult:
        if state.status == "canceled":
            return RemoteResult(state)
        ids = self._ids(state)
        if ids is None:
            return RemoteResult(state.model_copy(update={"status": "unknown"}), "未能确认该 CLI 指令是否停止")
        workspace, turn = ids
        if state.status == "input_required":
            return RemoteResult(state.model_copy(update={"status": "canceled", "question_id": None}))
        snapshot = await self._helper("cancel", workspace, turn, {})
        try:
            async with asyncio.timeout(5):
                while snapshot.get("status") == "working":
                    await asyncio.sleep(0.2)
                    snapshot = await self._helper("snapshot", workspace, turn, {})
        except TimeoutError:
            pass
        if snapshot.get("exit", {}).get("canceled"):
            return RemoteResult(state.model_copy(update={"status": "canceled", "question_id": None}))
        if snapshot.get("status") == "settled":
            async def ignore_checkpoint(_: RemoteState) -> None:
                pass

            return await self.refresh(state, ignore_checkpoint)
        return RemoteResult(state.model_copy(update={"status": "unknown"}), "未能确认该 CLI 指令是否停止")
