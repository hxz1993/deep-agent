"""Run-only parsing tasks and native artifacts, never a source of authority."""
from __future__ import annotations

import asyncio
import tempfile
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from deep_agent.files.schema import FileResourceRef

from .models import ParsedDocument
from .providers import can_resume_document


@dataclass(frozen=True)
class CachedDocument:
    document_id: str
    resource: FileResourceRef
    source_key: str
    source_hash: str
    parsed: ParsedDocument


class DocumentRunCache:
    def __init__(self) -> None:
        self.records: dict[str, CachedDocument] = {}
        self.tasks: dict[tuple[str, str, str, str], asyncio.Task[CachedDocument]] = {}
        self._directory: tempfile.TemporaryDirectory | None = None
        self.closed = False

    @property
    def directory(self) -> Path:
        if self.closed:
            raise RuntimeError("Document cache is closed")
        if self._directory is None:
            self._directory = tempfile.TemporaryDirectory(prefix="TMP_to_delete_document_run_")
        return Path(self._directory.name)

    async def get_or_parse(
        self, *, source_key: str, source_hash: str, provider: str, model: str,
        resource: FileResourceRef, parse: Callable[[Path], Awaitable[ParsedDocument]],
    ) -> tuple[CachedDocument, bool]:
        if self.closed:
            raise RuntimeError("Document cache is closed")
        key = (source_key, source_hash, provider, model)
        task = self.tasks.get(key)
        hit = task is not None
        resume_id = None
        if task is not None and task.done() and not task.cancelled():
            error = task.exception()
            if error is not None:
                # Retain the original directory and task identity. A new tool
                # call may resume only an explicitly confirmed uploaded task;
                # it cannot silently create another paid submission.
                resume_id = task.get_name().removeprefix("document-parse:")
                if not can_resume_document(self.directory / resume_id, error):
                    resume_id = None
                else:
                    task = None
        if task is None:
            document_id = resume_id or "doc_" + uuid.uuid4().hex
            directory = self.directory / document_id
            directory.mkdir(mode=0o700, exist_ok=bool(resume_id))

            async def run() -> CachedDocument:
                parsed = await parse(directory)
                record = CachedDocument(document_id, resource, source_key, source_hash, parsed)
                self.records[document_id] = record
                return record

            task = asyncio.create_task(run(), name="document-parse:" + document_id)
            self.tasks[key] = task
        # Unknown submissions and terminal failures remain until Run cleanup.
        # Replacement happens before the first await, keeping recovery single flight.
        return await asyncio.shield(task), hit

    async def aclose(self) -> None:
        if self.closed:
            return
        self.closed = True
        tasks = list(self.tasks.values())
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()
        self.records.clear()
        if self._directory is not None:
            await asyncio.to_thread(self._directory.cleanup)
            self._directory = None
