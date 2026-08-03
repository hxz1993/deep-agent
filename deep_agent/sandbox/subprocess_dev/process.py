from __future__ import annotations

import asyncio
import os
import signal

import shutil


async def terminate_process_group(proc: asyncio.subprocess.Process) -> None:
    if proc.pid is None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(proc.wait(), timeout=5.0)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()


def remove_work_dir(work_dir: str) -> None:
    try:
        shutil.rmtree(work_dir, ignore_errors=True)
    except OSError:
        pass
