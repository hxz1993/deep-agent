from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from deep_agent.db.dialect import DbDialect
from deep_agent.files.storage import (
    FileStoreIntegrityError,
    FileStorePermissionError,
    LocalFileStore,
    PlatformFileStore,
    build_file_store,
)


class LocalFileStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_bounded_read_repeated_cancel_waits_for_thread_file_close(self) -> None:
        import asyncio
        import threading
        from unittest.mock import patch

        source = self.root / "cancel-fixture"
        source.write_bytes(b"bounded")
        started, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        handles = []

        def blocking_read(_file_id, _headers, max_bytes):
            with source.open("rb") as handle:
                handles.append(handle)
                loop.call_soon_threadsafe(started.set)
                release.wait(5)
                return handle.read(max_bytes + 1)

        with patch.object(self.store, "_download_bounded_sync", side_effect=blocking_read):
            call = asyncio.create_task(self.store.download_bounded("fixture", self.headers, max_bytes=8))
            try:
                await started.wait()
                call.cancel()
                await asyncio.sleep(0)
                call.cancel()
                await asyncio.sleep(0)
                self.assertFalse(call.done())
                self.assertFalse(handles[0].closed)
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await call
                self.assertTrue(handles[0].closed)
            finally:
                release.set()
                await asyncio.gather(call, return_exceptions=True)

    async def asyncSetUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="local-file-store-TMP_to_delete-"))
        self.store = LocalFileStore(self.root)
        await self.store.initialize()
        self.headers = {
            "user": "user-1",
            "app": "app001",
            "space": "app",
        }

    async def asyncTearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    async def test_blob_crud_permissions_and_restart_recovery(self) -> None:
        uploaded = await self.store.upload(
            file_type="DL",
            file_name="预算.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            data=b"xlsx-payload",
            request_headers=self.headers,
        )
        self.assertTrue(uploaded.id.startswith("local_"))
        self.assertEqual(await self.store.download(uploaded.id, self.headers), b"xlsx-payload")
        metadata = await self.store.describe(uploaded.id, self.headers)
        self.assertEqual(metadata.owner_user, "user-1")
        self.assertEqual(metadata.app, "app001")
        self.assertEqual(metadata.space, "app")

        # App/Space are source metadata, not an authorization boundary.
        self.assertEqual(
            await self.store.download(uploaded.id, {"user": "user-1", "app": "other"}),
            b"xlsx-payload",
        )
        with self.assertRaises(FileStorePermissionError):
            await self.store.download(uploaded.id, {"user": "user-2"})

        restarted = LocalFileStore(self.root)
        await restarted.initialize()
        self.assertEqual(await restarted.download(uploaded.id, self.headers), b"xlsx-payload")
        record_id = await restarted.register_download(
            file_id=uploaded.id,
            file_name="导出预算.xlsx",
            byte_size=len(b"xlsx-payload"),
            request_headers=self.headers,
        )
        self.assertTrue(record_id.startswith("local_record_"))
        await restarted.delete(uploaded.id, self.headers)
        with self.assertRaises(OSError):
            await restarted.download(uploaded.id, self.headers)

    async def test_system_skill_file_does_not_require_app_space_or_user(self) -> None:
        uploaded = await self.store.upload(
            file_type="INF",
            file_name="skill.zip",
            mime="application/zip",
            data=b"PK-test",
            request_headers={},
        )
        self.assertEqual(await self.store.download(uploaded.id, {}), b"PK-test")
        await self.store.delete(uploaded.id, {})

    async def test_corrupt_blob_and_path_escape_fail_closed(self) -> None:
        uploaded = await self.store.upload(
            file_type="DL",
            file_name="note.txt",
            mime="text/plain",
            data=b"trusted",
            request_headers=self.headers,
        )
        metadata = await self.store.describe(uploaded.id, self.headers)
        blob = next(self.store.blob_root.rglob(f"{uploaded.id}.blob"))
        blob.write_bytes(b"corrupt")
        with self.assertRaises(FileStoreIntegrityError):
            await self.store.download(uploaded.id, self.headers)

        with sqlite3.connect(self.store.metadata_path) as db:
            db.execute(
                "UPDATE files SET blob_relpath=? WHERE file_id=?",
                ("../outside", uploaded.id),
            )
        with self.assertRaises(FileStoreIntegrityError):
            await self.store.download(uploaded.id, self.headers)
        self.assertEqual(metadata.sha256, "a9a089195c68d2adeee23beaa2c3a93b1d4cdf09046e7a9e520b3b166dff3e6a")

    async def test_initialize_cleans_partial_files_and_sets_private_modes(self) -> None:
        partial = self.store.blob_root / "orphan.partial-TMP_to_delete"
        partial.write_bytes(b"partial")
        await self.store.initialize()
        self.assertFalse(partial.exists())
        if os.name == "posix":
            self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
            self.assertEqual(self.store.metadata_path.stat().st_mode & 0o777, 0o600)

    async def test_factory_requires_local_entry_and_sqlite(self) -> None:
        with patch("deep_agent.files.storage.local_runtime_enabled", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "local_dev"):
                await build_file_store(
                    backend="local",
                    local_root=self.root,
                    database_dialect=DbDialect.SQLITE,
                )
        with patch("deep_agent.files.storage.local_runtime_enabled", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "SQLite"):
                await build_file_store(
                    backend="local",
                    local_root=self.root,
                    database_dialect=DbDialect.MYSQL,
                )
            built = await build_file_store(
                backend="local",
                local_root=self.root,
                database_dialect=DbDialect.SQLITE,
            )
        self.assertIsInstance(built, LocalFileStore)


class PlatformFileStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_adapter_preserves_platform_protocol(self) -> None:
        upload = AsyncMock(
            return_value=SimpleNamespace(id="file-1", fileName="a.txt", fileSize=3)
        )
        download = AsyncMock(return_value=b"abc")
        delete = AsyncMock()
        save = AsyncMock(return_value="record-1")
        api = SimpleNamespace(
            file=SimpleNamespace(upload=upload, download=download, delete_file=delete)
        )
        space = SimpleNamespace(business_record=SimpleNamespace(save=save))
        headers = {"user": "u", "app": "a", "space": "s", "authorization": "secret"}
        with patch("deep_agent.files.storage.PlatformAPI", return_value=api), patch(
            "deep_agent.files.storage.SpaceAPI", return_value=space
        ):
            store = PlatformFileStore()
            item = await store.upload(
                file_type="DL",
                file_name="a.txt",
                mime="text/plain",
                data=b"abc",
                request_headers=headers,
            )
            self.assertEqual(await store.download(item.id, headers), b"abc")
            record = await store.register_download(
                file_id=item.id,
                file_name="a.txt",
                byte_size=3,
                request_headers=headers,
            )
            await store.delete(item.id, headers)
        self.assertEqual(record, "record-1")
        upload.assert_awaited_once_with(file_type="DL", file_name="a.txt", file=b"abc")
        download.assert_awaited_once_with("file-1")
        delete.assert_awaited_once_with("file-1")
        payload = save.await_args.args[0]
        self.assertEqual(payload["fileId"], "file-1")
        self.assertEqual(payload["fileDescription"], "a.txt")


if __name__ == "__main__":
    unittest.main()
