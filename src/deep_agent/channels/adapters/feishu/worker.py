"""Private local IPC host for one official SDK instance.

The pinned SDK owns a module-global WS event loop. A process per account avoids
cross-account shutdown/reconnect interference without patching SDK internals.
Only the parent-created anonymous pipe is accepted; no network listener exists.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import uuid
from types import SimpleNamespace
from typing import Any


def worker_entry(connection, account: dict, infinite_retry: bool) -> None:
    run_worker(connection, account, infinite_retry)


def run_worker(connection, account: dict, infinite_retry: bool, *, adapter_factory=None) -> None:
    # SDK-created loops can report raw exceptions to stderr outside logger
    # filters. The parent receives only sanitized status/errors over IPC.
    with open(os.devnull, "w") as sink:
        os.dup2(sink.fileno(), 1)
        os.dup2(sink.fileno(), 2)
    logging.disable(logging.CRITICAL)
    from loguru import logger
    logger.remove()
    from .core import FeishuSDKAdapter
    factory = adapter_factory or FeishuSDKAdapter
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_serve(connection, factory(SimpleNamespace(**account), infinite_retry),
                                       on_disconnect=_arm_exit_timer))
    finally:
        connection.close()
        # The official SDK's public stop leaves its module-global ping task.
        # This is our dedicated SDK process, never the API/gateway process.
        # Exiting also bounds abandoned SDK executor threads after network hangs.
        os._exit(0)


def _arm_exit_timer():
    timer = threading.Timer(20, lambda: os._exit(0))
    timer.daemon = True
    timer.start()


async def _serve(connection, adapter: Any, *, on_disconnect=None) -> None:
    loop = asyncio.get_running_loop()
    closed = asyncio.Event()
    requests: set[asyncio.Task] = set()
    admissions: dict[str, asyncio.Future] = {}
    send_lock = threading.Lock()

    def send(payload):
        with send_lock:
            connection.send(payload)

    async def on_message(message):
        identifier = str(uuid.uuid4())
        future = loop.create_future()
        admissions[identifier] = future
        try:
            await asyncio.to_thread(send, {"kind": "message", "id": identifier, "message": message})
            await asyncio.wait_for(future, 15)
        finally:
            admissions.pop(identifier, None)

    async def execute(packet):
        task = asyncio.current_task()
        requests.add(task)
        method = packet.get("method")
        try:
            args = packet.get("args", ())
            if method == "start":
                await adapter.start(on_message)
                result = adapter.status()
            elif method == "stop":
                await adapter.stop()
                result = adapter.status()
            elif method == "test_connection":
                result = await adapter.test_connection(*args)
            elif method in {"download_media", "reply_loading", "reply"}:
                result = await getattr(adapter, method)(*args)
            else:
                raise ValueError("Unknown worker operation")
            await asyncio.to_thread(send, {"kind": "result", "id": packet["id"], "result": result})
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.to_thread(send, {"kind": "result", "id": packet["id"], "error": "Feishu SDK operation failed"})
        finally:
            requests.discard(task)
            if method == "stop":
                closed.set()

    def dispatch(packet):
        if packet.get("kind") == "admitted":
            future = admissions.get(packet.get("id"))
            if future and not future.done():
                future.set_result(None)
        elif not closed.is_set():
            if len(requests) < 128:
                asyncio.create_task(execute(packet))
            else:
                send({"kind": "result", "id": packet["id"], "error": "Feishu worker capacity reached"})

    def read_pipe():
        try:
            while True:
                packet = connection.recv()
                loop.call_soon_threadsafe(dispatch, packet)
        except (EOFError, OSError, RuntimeError):
            # EOF is also parent-death detection. Never retain an orphan WS bot.
            if on_disconnect is not None:
                on_disconnect()
            if not loop.is_closed():
                loop.call_soon_threadsafe(closed.set)

    reader = threading.Thread(target=read_pipe, name="feishu-worker-ipc", daemon=True)
    reader.start()

    async def publish_status():
        previous = None
        while not closed.is_set():
            status = adapter.status()
            if status != previous:
                await asyncio.to_thread(send, {"kind": "status", "status": status})
                previous = status
            await asyncio.sleep(0.25)

    status_task = asyncio.create_task(publish_status())
    try:
        await closed.wait()
    finally:
        status_task.cancel()
        await asyncio.gather(status_task, return_exceptions=True)
        for task in list(requests):
            task.cancel()
        await asyncio.gather(*list(requests), return_exceptions=True)
        for future in admissions.values():
            future.cancel()
        try:
            async with asyncio.timeout(12):
                await adapter.stop()
        except Exception:
            pass
