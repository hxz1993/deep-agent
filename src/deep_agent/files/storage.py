from __future__ import annotations

import asyncio
import hashlib
import os
import sqlite3
import uuid
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Protocol, runtime_checkable

from deep_agent.db.dialect import DbDialect
from deep_agent.local_security import local_runtime_enabled
from deepfos_platform.client.platform import FILE_TYPE_SKILL_PACKAGE, PlatformAPI, File
from deepfos_platform.client.base import ContentType
from deepfos_platform.lib.httpcli import AioHttpCli
from deepfos_platform.client.space import SpaceAPI


LOCAL_FILE_PREFIX = "local_"
LOCAL_DOWNLOAD_PREFIX = "local_record_"
LOCAL_METADATA_SCHEMA_VERSION = 1


class FileStoreError(OSError):
    pass


class FileStoreNotFoundError(FileStoreError):
    pass


class FileStorePermissionError(FileStoreError):
    pass


class FileStoreIntegrityError(FileStoreError):
    pass


@dataclass(frozen=True)
class StoredFile:
    id: str
    file_name: str
    file_size: int
    file_type: str
    mime: str | None = None


@dataclass(frozen=True)
class LocalFileMetadata(StoredFile):
    sha256: str = ""
    owner_user: str | None = None
    app: str | None = None
    space: str | None = None
    created_at: str = ""


@runtime_checkable
class FileStore(Protocol):
    async def upload(
        self,
        *,
        file_type: str,
        file_name: str,
        mime: str | None,
        data: bytes,
        request_headers: Mapping[str, str],
    ) -> StoredFile: ...

    async def download(
        self,
        file_id: str,
        request_headers: Mapping[str, str],
    ) -> bytes: ...

    async def download_bounded(
        self, file_id: str, request_headers: Mapping[str, str], *, max_bytes: int,
    ) -> bytes: ...

    async def delete(
        self,
        file_id: str,
        request_headers: Mapping[str, str],
    ) -> None: ...

    async def register_download(
        self,
        *,
        file_id: str,
        file_name: str,
        byte_size: int,
        request_headers: Mapping[str, str],
    ) -> str: ...


def _headers(value: Mapping[str, str]) -> dict[str, str]:
    return {str(name).lower(): str(item) for name, item in value.items()}


def _display_size(byte_size: int) -> tuple[float, str]:
    size = float(max(0, byte_size))
    unit = "B"
    for candidate in ("KB", "MB", "GB"):
        if size < 1024:
            break
        size /= 1024
        unit = candidate
    return round(size, 2), unit


class _BoundedPlatformFile(File):
    """Keep platform discovery/auth/request construction, bound response bytes."""

    def __init__(self, root: PlatformAPI, max_bytes: int):
        super().__init__(root)
        self.max_bytes = max_bytes
        # The generic SDK logger includes request headers. This new path never
        # logs caller credentials or full file URLs.
        self.log = lambda *_args, **_kwargs: None

    async def get(self, url, params=None, header=None):
        # Keep the component's standard header merge (including internal auth)
        # but disable eager decompression before our byte budget is applied.
        headers = {**self._merge_header(header), "Accept-Encoding": "identity"}
        return await AioHttpCli.get(url, params=params, headers=headers, auto_decompress=False)

    async def check_response(self, response, request, data_wrapped=True, content_type=ContentType.json):
        try:
            if not 200 <= response.status < 300:
                raise FileStoreError("Platform file download failed")
            if content_type is not ContentType.bytes:
                raise FileStoreError("Invalid platform file response type")
            encoding = response.headers.get("Content-Encoding", "identity").strip().lower()
            if encoding not in {"identity", "gzip", "deflate"}:
                raise FileStoreError("Unsupported platform file transfer encoding")
            declared = response.content_length
            if encoding == "identity" and declared is not None and declared > self.max_bytes:
                raise FileStoreError("File exceeds download size limit")
            # Bound the encoded body too: a gzip header may contain an
            # arbitrarily long filename/comment without producing any output.
            # Allow normal compression overhead even for an empty/tiny file.
            wire_limit = self.max_bytes + max(64 * 1024, self.max_bytes // 100)
            if declared is not None and declared > wire_limit:
                raise FileStoreError("File exceeds download size limit")
            decoder = zlib.decompressobj(31 if encoding == "gzip" else 15) if encoding != "identity" else None
            wire_size = 0
            payload = bytearray()
            async for chunk in response.content.iter_chunked(64 * 1024):
                wire_size += len(chunk)
                if wire_size > wire_limit:
                    raise FileStoreError("File exceeds download size limit")
                while chunk:
                    decoded = decoder.decompress(chunk, self.max_bytes - len(payload) + 1) if decoder else chunk
                    if len(payload) + len(decoded) > self.max_bytes:
                        raise FileStoreError("File exceeds download size limit")
                    payload.extend(decoded)
                    chunk = decoder.unconsumed_tail if decoder else b""
            if decoder and (not decoder.eof or decoder.unused_data):
                raise FileStoreError("Invalid platform file transfer encoding")
            return True, bytes(payload)
        finally:
            # Closing an unread over-limit/error response prevents draining an
            # arbitrarily large body; complete responses remain pool-reusable.
            response.close()


def _validate_download_limit(max_bytes: int) -> None:
    if type(max_bytes) is not int or max_bytes < 0:
        raise FileStoreError("Invalid download size limit")


class PlatformFileStore:
    """Thin adapter preserving the existing DeepFOS Platform file protocol."""

    async def upload(
        self,
        *,
        file_type: str,
        file_name: str,
        mime: str | None,
        data: bytes,
        request_headers: Mapping[str, str],
    ) -> StoredFile:
        del mime
        uploaded = await PlatformAPI(header=dict(request_headers)).file.upload(
            file_type=file_type,
            file_name=file_name,
            file=data,
        )
        file_id = getattr(uploaded, "id", None)
        if not isinstance(file_id, str) or not file_id:
            raise FileStoreError("Platform file upload returned no id")
        returned_name = getattr(uploaded, "fileName", None)
        returned_size = getattr(uploaded, "fileSize", None)
        return StoredFile(
            id=file_id,
            file_name=returned_name if isinstance(returned_name, str) and returned_name else file_name,
            file_size=returned_size if isinstance(returned_size, int) and returned_size >= 0 else len(data),
            file_type=file_type,
        )

    async def download(
        self,
        file_id: str,
        request_headers: Mapping[str, str],
    ) -> bytes:
        payload = await PlatformAPI(header=dict(request_headers)).file.download(file_id)
        if not isinstance(payload, (bytes, bytearray)):
            raise FileStoreError("Platform file download did not return bytes")
        return bytes(payload)

    async def download_bounded(
        self, file_id: str, request_headers: Mapping[str, str], *, max_bytes: int,
    ) -> bytes:
        _validate_download_limit(max_bytes)
        try:
            client = _BoundedPlatformFile(PlatformAPI(header=dict(request_headers)), max_bytes)
            return await client.download(file_id)
        except FileStoreError:
            raise
        except Exception:
            raise FileStoreError("Platform file download failed") from None

    async def delete(
        self,
        file_id: str,
        request_headers: Mapping[str, str],
    ) -> None:
        await PlatformAPI(header=dict(request_headers)).file.delete_file(file_id)

    async def register_download(
        self,
        *,
        file_id: str,
        file_name: str,
        byte_size: int,
        request_headers: Mapping[str, str],
    ) -> str:
        headers = _headers(request_headers)
        display_size, unit = _display_size(byte_size)
        payload = {
            "app": headers["app"],
            "space": headers["space"],
            "fileId": file_id,
            "fileName": file_name,
            "fileSize": display_size,
            "unit": unit,
            "status": "SUCCESS",
            "percentage": 100,
            "opeType": "ELEMENT_EXPORT",
            "opeTypeDesc": "Agent文件导出",
            "fileDescription": file_name,
            "createTime": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"),
        }
        record_id = await SpaceAPI(header=dict(request_headers)).business_record.save(payload)
        if not isinstance(record_id, str) or not record_id:
            raise FileStoreError("Download Center registration returned no id")
        return record_id


class LocalFileStore:
    """Local-development Blob store with a private SQLite metadata index."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.blob_root = self.root / "blobs"
        self.metadata_path = self.root / "metadata.sqlite3"

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    def _initialize_sync(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.blob_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name == "posix":
            os.chmod(self.root, 0o700)
            os.chmod(self.blob_root, 0o700)
        for partial in self.blob_root.rglob("*.partial-TMP_to_delete"):
            try:
                partial.unlink()
            except FileNotFoundError:
                pass
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS files (
                    file_id TEXT PRIMARY KEY,
                    file_type TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    mime TEXT,
                    byte_size INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    blob_relpath TEXT NOT NULL UNIQUE,
                    owner_user TEXT,
                    app TEXT,
                    space TEXT,
                    created_at TEXT NOT NULL,
                    deleted_at TEXT
                );
                CREATE INDEX IF NOT EXISTS ix_local_files_owner_created
                    ON files(owner_user, created_at);
                CREATE TABLE IF NOT EXISTS download_records (
                    record_id TEXT PRIMARY KEY,
                    file_id TEXT NOT NULL REFERENCES files(file_id),
                    owner_user TEXT NOT NULL,
                    app TEXT,
                    space TEXT,
                    file_name TEXT NOT NULL,
                    byte_size INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_local_downloads_owner_created
                    ON download_records(owner_user, created_at);
                """
            )
            db.execute(f"PRAGMA user_version={LOCAL_METADATA_SCHEMA_VERSION}")
        if os.name == "posix":
            os.chmod(self.metadata_path, 0o600)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.metadata_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @staticmethod
    def _identity(request_headers: Mapping[str, str]) -> tuple[str | None, str | None, str | None]:
        headers = _headers(request_headers)
        return headers.get("user"), headers.get("app"), headers.get("space")

    @staticmethod
    def _is_system_file(file_type: str) -> bool:
        return file_type == FILE_TYPE_SKILL_PACKAGE

    def _authorize(self, row: sqlite3.Row, request_headers: Mapping[str, str]) -> None:
        if self._is_system_file(str(row["file_type"])):
            return
        user, _app, _space = self._identity(request_headers)
        if not user or user != row["owner_user"]:
            raise FileStorePermissionError("Local file does not belong to the current user")

    async def upload(
        self,
        *,
        file_type: str,
        file_name: str,
        mime: str | None,
        data: bytes,
        request_headers: Mapping[str, str],
    ) -> StoredFile:
        return await asyncio.to_thread(
            self._upload_sync,
            file_type,
            file_name,
            mime,
            bytes(data),
            dict(request_headers),
        )

    def _upload_sync(
        self,
        file_type: str,
        file_name: str,
        mime: str | None,
        data: bytes,
        request_headers: Mapping[str, str],
    ) -> StoredFile:
        user, app, space = self._identity(request_headers)
        if not self._is_system_file(file_type) and not user:
            raise FileStorePermissionError("Local file upload requires a user identity")
        file_id = f"{LOCAL_FILE_PREFIX}{uuid.uuid4().hex}"
        shard = file_id[len(LOCAL_FILE_PREFIX):][:2]
        directory = self.blob_root / shard
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name == "posix":
            os.chmod(directory, 0o700)
        relative = Path("blobs") / shard / f"{file_id}.blob"
        target = self.root / relative
        partial = target.with_name(f"{target.name}.{uuid.uuid4().hex}.partial-TMP_to_delete")
        digest = hashlib.sha256(data).hexdigest()
        try:
            with partial.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            if os.name == "posix":
                os.chmod(partial, 0o600)
            os.replace(partial, target)
            with self._connect() as db:
                db.execute(
                    """
                    INSERT INTO files(
                        file_id, file_type, file_name, mime, byte_size, sha256,
                        blob_relpath, owner_user, app, space, created_at, deleted_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                    """,
                    (
                        file_id,
                        file_type,
                        file_name,
                        mime,
                        len(data),
                        digest,
                        relative.as_posix(),
                        None if self._is_system_file(file_type) else user,
                        app,
                        space,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
        except Exception:
            partial.unlink(missing_ok=True)
            target.unlink(missing_ok=True)
            raise
        return StoredFile(file_id, file_name, len(data), file_type, mime)

    def _row(self, file_id: str) -> sqlite3.Row:
        if not file_id.startswith(LOCAL_FILE_PREFIX):
            raise FileStoreNotFoundError("Unknown local file id")
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM files WHERE file_id=? AND deleted_at IS NULL",
                (file_id,),
            ).fetchone()
        if row is None:
            raise FileStoreNotFoundError("Local file not found")
        return row

    def _blob_path(self, row: sqlite3.Row) -> Path:
        relative = Path(str(row["blob_relpath"]))
        expected = (
            Path("blobs")
            / str(row["file_id"])[len(LOCAL_FILE_PREFIX):][:2]
            / f"{row['file_id']}.blob"
        )
        if relative != expected or relative.is_absolute():
            raise FileStoreIntegrityError("Local file path is not a valid Blob path")
        target = (self.root / relative).resolve()
        if self.root not in target.parents:
            raise FileStoreIntegrityError("Local file path escapes the configured root")
        return target

    async def describe(
        self,
        file_id: str,
        request_headers: Mapping[str, str],
    ) -> LocalFileMetadata:
        return await asyncio.to_thread(self._describe_sync, file_id, dict(request_headers))

    def _describe_sync(
        self,
        file_id: str,
        request_headers: Mapping[str, str],
    ) -> LocalFileMetadata:
        row = self._row(file_id)
        self._authorize(row, request_headers)
        return LocalFileMetadata(
            id=str(row["file_id"]),
            file_name=str(row["file_name"]),
            file_size=int(row["byte_size"]),
            file_type=str(row["file_type"]),
            mime=row["mime"],
            sha256=str(row["sha256"]),
            owner_user=row["owner_user"],
            app=row["app"],
            space=row["space"],
            created_at=str(row["created_at"]),
        )

    async def download(
        self,
        file_id: str,
        request_headers: Mapping[str, str],
    ) -> bytes:
        return await asyncio.to_thread(self._download_sync, file_id, dict(request_headers))

    def _download_sync(self, file_id: str, request_headers: Mapping[str, str]) -> bytes:
        row = self._row(file_id)
        self._authorize(row, request_headers)
        target = self._blob_path(row)
        try:
            data = target.read_bytes()
        except FileNotFoundError as exc:
            raise FileStoreIntegrityError("Local file Blob is missing") from exc
        if len(data) != int(row["byte_size"]):
            raise FileStoreIntegrityError("Local file size mismatch")
        if hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise FileStoreIntegrityError("Local file hash mismatch")
        return data

    async def download_bounded(
        self, file_id: str, request_headers: Mapping[str, str], *, max_bytes: int,
    ) -> bytes:
        _validate_download_limit(max_bytes)
        try:
            return await asyncio.to_thread(self._download_bounded_sync, file_id, dict(request_headers), max_bytes)
        except FileStoreError:
            raise
        except Exception:
            raise FileStoreError("Local file download failed") from None

    def _download_bounded_sync(self, file_id, request_headers, max_bytes):
        row = self._row(file_id)
        self._authorize(row, request_headers)
        size = int(row["byte_size"])
        if size > max_bytes:
            raise FileStoreError("File exceeds download size limit")
        target = self._blob_path(row)
        try:
            with target.open("rb") as handle:
                data = handle.read(max_bytes + 1)
        except FileNotFoundError:
            raise FileStoreIntegrityError("Local file Blob is missing") from None
        if len(data) > max_bytes:
            raise FileStoreError("File exceeds download size limit")
        if len(data) != size:
            raise FileStoreIntegrityError("Local file size mismatch")
        if hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise FileStoreIntegrityError("Local file hash mismatch")
        return data

    async def delete(
        self,
        file_id: str,
        request_headers: Mapping[str, str],
    ) -> None:
        await asyncio.to_thread(self._delete_sync, file_id, dict(request_headers))

    def _delete_sync(self, file_id: str, request_headers: Mapping[str, str]) -> None:
        row = self._row(file_id)
        self._authorize(row, request_headers)
        target = self._blob_path(row)
        with self._connect() as db:
            db.execute(
                "UPDATE files SET deleted_at=? WHERE file_id=? AND deleted_at IS NULL",
                (datetime.now(timezone.utc).isoformat(), file_id),
            )
        target.unlink(missing_ok=True)

    async def register_download(
        self,
        *,
        file_id: str,
        file_name: str,
        byte_size: int,
        request_headers: Mapping[str, str],
    ) -> str:
        return await asyncio.to_thread(
            self._register_download_sync,
            file_id,
            file_name,
            byte_size,
            dict(request_headers),
        )

    def _register_download_sync(
        self,
        file_id: str,
        file_name: str,
        byte_size: int,
        request_headers: Mapping[str, str],
    ) -> str:
        row = self._row(file_id)
        self._authorize(row, request_headers)
        if int(row["byte_size"]) != byte_size:
            raise FileStoreIntegrityError("Local download size does not match the file")
        user, app, space = self._identity(request_headers)
        if not user:
            raise FileStorePermissionError("Local download registration requires a user")
        record_id = f"{LOCAL_DOWNLOAD_PREFIX}{uuid.uuid4().hex}"
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO download_records(
                    record_id, file_id, owner_user, app, space,
                    file_name, byte_size, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    file_id,
                    user,
                    app,
                    space,
                    file_name,
                    byte_size,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        return record_id


async def build_file_store(
    *,
    backend: str,
    local_root: str | Path,
    database_dialect: DbDialect,
) -> FileStore:
    normalized = backend.strip().lower()
    if normalized == "platform":
        return PlatformFileStore()
    if normalized != "local":
        raise ValueError(f"Unsupported file backend: {backend}")
    if not local_runtime_enabled():
        raise RuntimeError(
            "DEEP_AGENT_FILE_BACKEND=local is only allowed through deep_agent.local_dev"
        )
    if database_dialect is not DbDialect.SQLITE:
        raise RuntimeError("DEEP_AGENT_FILE_BACKEND=local requires the SQLite database")
    store = LocalFileStore(local_root)
    await store.initialize()
    return store


__all__ = [
    "FileStore",
    "FileStoreError",
    "FileStoreIntegrityError",
    "FileStoreNotFoundError",
    "FileStorePermissionError",
    "LOCAL_FILE_PREFIX",
    "LocalFileMetadata",
    "LocalFileStore",
    "PlatformFileStore",
    "StoredFile",
    "build_file_store",
]
