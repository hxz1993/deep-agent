"""Cancellable, bounded, per-worker isolated instance validation."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from weakref import WeakKeyDictionary, ref

from .schema import MAX_OUTPUT_BYTES, expand_schema, json_bytes

VALIDATION_TIMEOUT = 2.0
QUEUE_TIMEOUT = 2.0
MAX_ACTIVE_VALIDATIONS = 2
MAX_WAITING_VALIDATIONS = 16
_WORKER = Path(__file__).with_name("worker.py")


class OutputValidationServiceError(RuntimeError):
    """Infrastructure errors must fail the run, not consume model retries."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(f"Structured output validation service failed ({code})")


class _Capacity:
    def __init__(self):
        self.slots = asyncio.Semaphore(MAX_ACTIVE_VALIDATIONS)
        self.waiting = 0

    async def acquire(self):
        if not self.slots.locked():
            await self.slots.acquire()
            return
        if self.waiting >= MAX_WAITING_VALIDATIONS:
            raise OutputValidationServiceError("queue_full")
        self.waiting += 1
        try:
            try:
                await asyncio.wait_for(self.slots.acquire(), timeout=QUEUE_TIMEOUT)
            except TimeoutError as exc:
                raise OutputValidationServiceError("queue_timeout") from exc
        finally:
            self.waiting -= 1


_CAPACITIES: WeakKeyDictionary = WeakKeyDictionary()


def _capacity() -> _Capacity:
    loop = asyncio.get_running_loop()
    existing = _CAPACITIES.get(loop)
    capacity = existing() if existing is not None else None
    if capacity is None:
        capacity = _Capacity()
        # Semaphore binds its loop after a wait. A weak value prevents that
        # indirect loop reference keeping disposed test/application loops alive.
        _CAPACITIES[loop] = ref(capacity)
    return capacity


async def _kill_and_reap(process) -> None:
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    # Shield cleanup from the initiating cancellation. asyncio's process wait
    # uses the child watcher and performs waitpid, rather than leaving a zombie.
    cleanup = asyncio.create_task(process.communicate())
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            continue
    cleanup.result()


async def validate_output(schema: dict[str, Any], value: Any) -> list[dict[str, str]]:
    """Return safe bounded schema errors; raise for service failure/cancellation."""
    try:
        value_bytes = json_bytes(value)
    except ValueError:
        return [{"path": "/", "constraint": "json", "message": "/: output must contain finite UTF-8 JSON values"}]
    if len(value_bytes) > MAX_OUTPUT_BYTES:
        return [{"path": "/", "constraint": "maxBytes", "message": f"/: output exceeds {MAX_OUTPUT_BYTES} UTF-8 bytes"}]
    # Invalid saved configuration is a service/configuration error, never a
    # request to make the model repair output against an untrusted definition.
    try:
        expanded = expand_schema(schema)
    except ValueError as exc:
        raise OutputValidationServiceError("invalid_schema") from exc
    capacity = _capacity()
    await capacity.acquire()
    process = None
    try:
        try:
            spawning = asyncio.create_task(asyncio.create_subprocess_exec(
                sys.executable, str(_WORKER), stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            ))
            try:
                process = await asyncio.shield(spawning)
            except asyncio.CancelledError:
                # A cancellation can arrive between fork/exec and receiving the
                # Process handle. Recover that handle before finally reaps it.
                while not spawning.done():
                    try:
                        await asyncio.shield(spawning)
                    except asyncio.CancelledError:
                        continue
                process = spawning.result()
                raise
            payload = b'{"schema":' + json_bytes(expanded) + b',"value":' + value_bytes + b'}'
            stdout, _ = await asyncio.wait_for(process.communicate(payload), timeout=VALIDATION_TIMEOUT)
        except TimeoutError as exc:
            raise OutputValidationServiceError("validation_timeout") from exc
        except OSError as exc:
            raise OutputValidationServiceError("worker_start_failed") from exc
        if process.returncode != 0 or len(stdout) > 64 * 1024:
            raise OutputValidationServiceError("worker_failed")
        try:
            result = json.loads(stdout)
            errors = result["errors"]
            if not isinstance(errors, list) or len(errors) > 20 or any(
                not isinstance(error, dict) or set(error) != {"path", "constraint", "message"}
                or any(not isinstance(field, str) or len(field) > 256 for field in error.values())
                for error in errors
            ):
                raise ValueError("Invalid worker result")
        except (ValueError, KeyError, TypeError) as exc:
            raise OutputValidationServiceError("worker_protocol_error") from exc
        return errors
    finally:
        try:
            if process is not None:
                await _kill_and_reap(process)
        finally:
            capacity.slots.release()
