"""Keep reversible local Skill directories until the installation DB commits."""
from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path


class RuntimeCheckpoint:
    def __init__(self, runtime):
        self.runtime = runtime
        self.root: Path | None = None
        self.saved = {}

    async def capture(self, code):
        if code in self.saved:
            return
        async with self.runtime._lock(code):
            self.runtime.root.mkdir(parents=True, exist_ok=True)
            if self.root is None:
                self.root = Path(tempfile.mkdtemp(prefix='.agent-install-TMP_to_delete-', dir=self.runtime.root))
            destination = self.runtime._destination(code)
            saved = self.root / code
            existed = destination.exists()
            if existed:
                await asyncio.to_thread(shutil.copytree, destination, saved)
            self.saved[code] = (saved if existed else None, self.runtime.state(code))

    async def restore(self, code):
        if code not in self.saved:
            return
        async with self.runtime._lock(code):
            saved, state = self.saved[code]
            destination = self.runtime._destination(code)
            if destination.exists():
                await asyncio.to_thread(shutil.rmtree, destination)
            if saved is not None:
                saved.replace(destination)
            self.runtime._states[code] = state
            del self.saved[code]

    async def discard(self):
        if self.root and self.root.exists():
            await asyncio.to_thread(shutil.rmtree, self.root)
        self.saved.clear()
