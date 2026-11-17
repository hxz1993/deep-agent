"""Standalone workspace/process helper. SSH invokes this file with the remote Python."""

from __future__ import annotations

import asyncio
import base64
import fcntl
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
from pathlib import Path

MAX_INPUT = 8 * 1024 * 1024
MAX_OUTPUT = 20 * 1024 * 1024
MAX_EVENTS = 8 * 1024 * 1024
MAX_LINE = 1024 * 1024
MAX_FILES = 10
TURN_SECONDS = 300
SAFE_ID = re.compile(r"[a-f0-9]{32}")
DEFAULT_CLI_WORKSPACE_ROOT = "~/.deep-agent/remote-agent-workspaces"


def file_name(value: str) -> bool:
    return (
        bool(value) and value not in {".", ".."} and value.isprintable()
        and len(value.encode()) <= 180 and not any(char in value for char in "/\\")
    )


def _execution_env() -> dict[str, str]:
    # DeepAgent configuration belongs to the host API, not the execution account.
    return {name: value for name, value in os.environ.items()
            if not name.startswith(("DEEP_AGENT_", "DEEPFOS_", "EXPORT_")) and name != "DATABASE_URL"}


def _json(path: Path, value: object) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    temporary.replace(path)


def _paths(root: Path, workspace_id: str, turn_id: str) -> tuple[Path, Path, Path]:
    if not SAFE_ID.fullmatch(workspace_id) or not SAFE_ID.fullmatch(turn_id):
        raise ValueError("invalid workspace identifier")
    workspace = root / workspace_id
    return workspace, workspace / "control" / turn_id, workspace / "work" / "outputs" / turn_id


def _prepare(workspace: Path, turn: Path, output: Path, request: dict) -> dict:
    workdir = workspace / "work"
    files = request["files"]
    if len(files) > MAX_FILES:
        raise ValueError("too many attachments")
    if request.get("reuse_workspace"):
        if not workdir.is_dir():
            return {"workspace_missing": True}
    else:
        workspace.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        workspace.mkdir(mode=0o700)
    input_dir = workdir / "inputs" / turn.name
    turn.mkdir(parents=True, mode=0o700)
    output.mkdir(parents=True, mode=0o700)
    if files:
        input_dir.mkdir(parents=True, mode=0o700)
    paths: list[str] = []
    size = 0
    for index, item in enumerate(files, start=1):
        name = item["name"]
        if not file_name(name):
            name = "attachment"
        data = base64.b64decode(item["data"], validate=True)
        size += len(data)
        if size > MAX_INPUT:
            raise ValueError("attachments too large")
        path = input_dir / f"{index:02d}-{name}"
        with path.open("xb") as stream:
            stream.write(data)
        path.chmod(0o400)
        paths.append(str(path))
    schema = turn / "schema.json"
    _json(schema, request["schema"])
    return {"cwd": str(workdir), "input_paths": paths, "output_dir": str(output), "schema_path": str(schema)}


def _event_facts(event: dict) -> dict:
    kind = event.get("type")
    match kind:
        case "thread.started" | "system":
            field = "thread_id" if kind == "thread.started" else "session_id"
            native_id = event.get(field)
            if (isinstance(native_id, str) and len(native_id) <= 128
                    and (kind == "thread.started" or event.get("subtype") == "init")
                    and not event.get("parent_tool_use_id")):
                return {"type": kind, field: native_id}
        case "turn.completed":
            return {"type": kind}
        case "item.completed":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message":
                return {"type": kind, "item": {"type": "agent_message", "text": item.get("text")}}
        case "result":
            if not event.get("parent_tool_use_id"):
                return {key: event[key] for key in ("type", "session_id", "subtype", "is_error",
                                                   "structured_output", "result") if key in event}
    return {}


def _write_events_line(line: bytes, event_file, count: int) -> int:
    if count + len(line) > MAX_EVENTS:
        raise ValueError("too many CLI events")
    try:
        event = json.loads(line)
    except ValueError:
        return count + len(line)
    fact = _event_facts(event) if isinstance(event, dict) else {}
    if not fact:
        return count + len(line)
    event_file.write(json.dumps(fact, ensure_ascii=False).encode("utf-8", errors="backslashreplace") + b"\n")
    event_file.flush()
    ids = {name: fact[name] for name in ("thread_id", "session_id")
           if isinstance(fact.get(name), str) and len(fact[name]) <= 128}
    try:
        if ids:
            os.write(sys.stdout.fileno(), json.dumps({"type": fact["type"], **ids}).encode() + b"\n")
    except (BrokenPipeError, BlockingIOError):
        # Retain native events on disk even if the observer disconnects.
        pass
    return count + len(line)


def _signal_group(group_id: int, sig: int) -> bool:
    try:
        os.killpg(group_id, sig)
    except ProcessLookupError:
        return False
    except PermissionError:
        # macOS reports EPERM while the group contains only zombies awaiting reap.
        if sys.platform != "darwin":
            raise
        return False
    return True


async def _terminate(process: asyncio.subprocess.Process) -> None:
    _signal_group(process.pid, signal.SIGTERM)
    try:
        async with asyncio.timeout(3):
            # A CLI child can still own the output pipe after the group leader exits.
            while _signal_group(process.pid, 0):
                await asyncio.sleep(0.1)
    except TimeoutError:
        _signal_group(process.pid, signal.SIGKILL)
    await process.wait()


async def _execute(turn: Path, request: dict) -> dict:
    marker = turn / "cancel"
    if marker.exists():
        return {"returncode": None, "canceled": True}
    process = await asyncio.create_subprocess_exec(
        *request["argv"], cwd=request["cwd"], stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env=_execution_env(), limit=MAX_LINE, start_new_session=True,
    )
    count = 0
    expired = False
    session = request.get("session_id")
    missing_codex = f"no rollout found for thread id {session}".encode() if session else None
    missing_codebuddy = f"No conversation found with session ID: {session}".encode() if session else None

    async def stream() -> None:
        nonlocal count, expired
        with (turn / "events.jsonl").open("xb") as events:
            while line := await process.stdout.readline():
                if missing_codebuddy and missing_codebuddy in line:
                    expired = True
                count = _write_events_line(line, events, count)
        await process.wait()

    async def drain_errors() -> None:
        nonlocal expired
        tail = b""
        while chunk := await process.stderr.read(65536):
            if missing_codex:
                window = tail + chunk
                if missing_codex in window:
                    expired = True
                tail = window[-len(missing_codex):]

    async def feed() -> None:
        try:
            process.stdin.write(request["prompt"].encode())
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            process.stdin.close()

    tasks = [asyncio.create_task(operation()) for operation in (stream, drain_errors, feed)]
    try:
        async with asyncio.timeout(TURN_SECONDS):
            pending = set(tasks)
            while pending:
                if marker.exists():
                    return {"returncode": None, "canceled": True}
                done, pending = await asyncio.wait(pending, timeout=0.2, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
        return {"returncode": process.returncode, "session_expired": expired}
    except TimeoutError:
        return {"returncode": None, "timeout": True}
    finally:
        for task in tasks:
            task.cancel()
        await _terminate(process)
        await asyncio.gather(*tasks, return_exceptions=True)


def _run(workspace: Path, turn: Path, request: dict) -> None:
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    os.set_blocking(sys.stdout.fileno(), False)
    with (workspace / "control" / "active").open("a") as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with (turn / "started").open("x"):
            pass
        try:
            result = asyncio.run(_execute(turn, request))
        except (OSError, ValueError):
            result = {"returncode": None, "failed": True}
        _json(turn / "exit.json", result)


def _snapshot(workspace: Path, turn: Path) -> dict:
    exit_path = turn / "exit.json"
    events_path = turn / "events.jsonl"
    if not turn.is_dir():
        return {"status": "missing"}
    with (workspace / "control" / "active").open("a") as lease:
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            active = False
        except BlockingIOError:
            active = True
    started = (turn / "started").is_file()
    if not started and (turn / "cancel").exists():
        return {"status": "settled", "exit": {"returncode": None, "canceled": True}}
    response = {"status": "working" if active and started else "unknown"}
    if exit_path.is_file():
        response = {"status": "settled", "exit": json.loads(exit_path.read_bytes())}
    if events_path.is_file():
        with events_path.open("rb") as stream:
            response["events"] = stream.read(MAX_EVENTS).decode("utf-8", errors="replace")
    return response


def _open_output(workspace: Path, output: Path) -> int:
    flags = os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(workspace, flags)
    try:
        for component in output.relative_to(workspace).parts:
            child = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _collect(workspace: Path, output: Path, names: list[str]) -> dict:
    result: dict[str, str] = {}
    size = 0
    directory_fd = _open_output(workspace, output)
    try:
        for name in dict.fromkeys(names[:MAX_FILES]):
            if not file_name(name):
                continue
            try:
                descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
                with os.fdopen(descriptor, "rb") as stream:
                    properties = os.fstat(stream.fileno())
                    if not stat.S_ISREG(properties.st_mode) or properties.st_nlink != 1:
                        continue
                    if properties.st_size > MAX_OUTPUT - size:
                        continue
                    data = stream.read(MAX_OUTPUT - size + 1)
                    if len(data) > MAX_OUTPUT - size:
                        continue
            except OSError:
                continue
            size += len(data)
            result[name] = base64.b64encode(data).decode("ascii")
    finally:
        os.close(directory_fd)
    return result


def _inspect(root: Path, request: dict) -> dict:
    parent = root
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    if not parent.is_dir() or not os.access(parent, os.W_OK | os.X_OK):
        return {"workspace_unavailable": True}
    command = shutil.which(request["command"])
    if command is None:
        return {"available": False}
    try:
        probe = subprocess.run(
            [command, "--version"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=_execution_env(), timeout=8, check=False,
        )
    except subprocess.TimeoutExpired:
        return {"available": True, "ready": False}
    version = re.search(rb"\b[0-9]+\.[0-9]+\.[0-9]+(?:[-a-zA-Z0-9.+]{0,30})\b", probe.stdout[:256])
    return {"available": True, "ready": probe.returncode == 0,
            "version": version[0].decode("ascii") if version else None, "workspace_root": str(root)}


def main() -> None:
    root, operation, workspace_id, turn_id = sys.argv[1:]
    root = Path(root).expanduser().resolve()
    workspace, turn, output = _paths(root, workspace_id, turn_id)
    request = json.loads(sys.stdin.buffer.read(MAX_INPUT * 2))
    if operation == "prepare":
        result = _prepare(workspace, turn, output, request)
    elif operation == "run":
        _run(workspace, turn, request)
        return
    elif operation == "snapshot":
        result = _snapshot(workspace, turn)
    elif operation == "collect":
        result = _collect(workspace, output, request["names"])
    elif operation == "cancel":
        if turn.is_dir():
            (turn / "cancel").touch()
        result = _snapshot(workspace, turn)
    elif operation == "inspect":
        result = _inspect(root, request)
    else:
        raise ValueError("unrecognized operation")
    print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError):
        sys.exit(1)
