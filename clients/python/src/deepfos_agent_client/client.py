from __future__ import annotations

import asyncio
import base64
import mimetypes
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit
from uuid import uuid4

import httpx

STOP_STATES = frozenset({"completed", "failed", "canceled", "rejected", "input_required", "auth_required"})
MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024
MAX_ATTACHMENTS = 20
MAX_ARTIFACT_BYTES = 200 * 1024 * 1024


class AgentServiceError(Exception):
    def __init__(self, status: int, detail: str, *, message_id: str | None = None):
        super().__init__(detail)
        self.status = status
        self.message_id = message_id


class WaitTimeout(TimeoutError):
    def __init__(self, task_id: str):
        super().__init__(f"Timed out waiting for {task_id}. Query the task again using this ID.")
        self.task_id = task_id


@dataclass
class Task:
    id: str
    context_id: str
    state: str
    source: str
    question_id: str | None
    message: str
    artifacts: list[dict]
    history: list[dict]


def segment(value: str) -> str:
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError("Invalid path segment")
    return quote(value, safe="")


def read_attachments(paths) -> list[dict]:
    paths = list(paths)
    if len(paths) > MAX_ATTACHMENTS:
        raise ValueError("Use at most 20 attachments")
    remaining = MAX_ATTACHMENT_BYTES
    items = []
    for value in paths:
        path = Path(value)
        if not path.is_file():
            raise ValueError(f"Not a regular attachment file: {path}")
        with path.open("rb") as stream:
            content = stream.read(remaining + 1)
        remaining -= len(content)
        if remaining < 0:
            raise ValueError("Attachments exceed 8 MiB")
        items.append({
            "name": path.name,
            "media_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            "data_base64": base64.b64encode(content).decode("ascii"),
        })
    return items


class AgentClient:
    def __init__(self, url: str, *, app: str | None = None, api_key: str | None = None,
                 token: str | None = None, timeout: float = 30.0,
                 transport: httpx.AsyncBaseTransport | None = None,
                 client_label: str = "python-sdk"):
        if not url:
            raise ValueError("Set DEEPFOS_AGENT_URL to the DeepAgent service URL")
        parsed = urlsplit(url)
        if (parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment):
            raise ValueError("Use an HTTP(S) service URL without credentials, query, or fragment")
        if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Use HTTPS for a remote DeepAgent")
        if bool(api_key) == bool(token):
            raise ValueError("Set exactly one of DEEPFOS_AGENT_API_KEY and DEEPFOS_AGENT_TOKEN")
        self.app = app
        self._uses_api_key = bool(api_key)
        credentials = {"X-Deepfos-Key": api_key} if api_key else {"Authorization": f"Bearer {token}"}
        credentials["X-DeepAgent-Client"] = client_label
        base = url.rstrip("/")
        suffix = "/api/v1/agent-service"
        if not base.endswith(suffix):
            base += suffix
        self._http = httpx.AsyncClient(
            base_url=base + "/", headers=credentials, timeout=timeout,
            follow_redirects=False, transport=transport,
        )

    @classmethod
    def from_env(cls, *, app: str | None = None, url: str | None = None,
                 client_label: str = "python-sdk") -> AgentClient:
        return cls(url or os.environ.get("DEEPFOS_AGENT_URL", ""),
                   app=app or os.environ.get("DEEPFOS_AGENT_APP"),
                   api_key=os.environ.get("DEEPFOS_AGENT_API_KEY"),
                   token=os.environ.get("DEEPFOS_AGENT_TOKEN"), client_label=client_label)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.aclose()

    async def aclose(self):
        await self._http.aclose()

    def _headers(self, app, *, required=True):
        selected = app or self.app
        if not selected and (required or self._uses_api_key):
            raise ValueError("Set DEEPFOS_AGENT_APP or provide an App ID")
        return {"app": selected} if selected else {}

    @staticmethod
    async def _check(response, *, message_id=None):
        if response.is_success:
            return
        await response.aread()
        try:
            detail = response.json().get("detail")
        except (ValueError, AttributeError):
            detail = None
        if not isinstance(detail, str):
            detail = f"DeepAgent returned HTTP {response.status_code}"
        raise AgentServiceError(response.status_code, detail, message_id=message_id)

    async def _request(self, method, path, app, *, require_app=True, body=None, params=None):
        message_id = body.get("message_id") if body else None
        headers = self._headers(app, required=require_app)
        try:
            response = await self._http.request(method, path, headers=headers, json=body, params=params)
        except httpx.RequestError as exc:
            raise AgentServiceError(0, "Unable to reach DeepAgent", message_id=message_id) from exc
        await self._check(response, message_id=message_id)
        return response.json()

    async def list_agents(self):
        return (await self._request("GET", "agents", None, require_app=False))["agents"]

    async def _message(self, message_id, text, data, attachments, **extras):
        files = await asyncio.to_thread(read_attachments, attachments)
        if not text and data is None and not files:
            raise ValueError("Provide text, JSON data or an attachment")
        return {
            "message_id": message_id or uuid4().hex, "text": text, "data": data, "attachments": files,
            **extras,
        }

    async def submit(self, *, agent_code: str, text: str | None = None, data: dict | None = None,
                     attachments=(), context_id: str | None = None, reference_task_ids=(),
                     message_id: str | None = None, app: str | None = None) -> Task:
        body = await self._message(message_id, text, data, attachments, context_id=context_id,
                                   reference_task_ids=list(reference_task_ids))
        return Task(**await self._request("POST", f"agents/{segment(agent_code)}/tasks", app, body=body))

    async def get_task(self, task_id: str, *, app: str | None = None, history_length: int | None = None) -> Task:
        params = {"history_length": history_length} if history_length is not None else None
        return Task(**await self._request("GET", f"tasks/{segment(task_id)}", app, params=params))

    async def list_tasks(self, *, agent_code: str, app: str | None = None, context_id: str | None = None,
                         state: str | None = None, page_size: int = 20, cursor: str | None = None):
        params = {"page_size": page_size}
        params.update({k: v for k, v in {"context_id": context_id, "state": state, "cursor": cursor}.items()
                       if v is not None})
        return await self._request("GET", f"agents/{segment(agent_code)}/tasks", app, params=params)

    async def reply(self, task_id: str, *, reply_to: str | None = None, text: str | None = None,
                    data: dict | None = None, attachments=(), message_id: str | None = None,
                    app: str | None = None) -> Task:
        body = await self._message(message_id, text, data, attachments, reply_to=reply_to)
        return Task(**await self._request("POST", f"tasks/{segment(task_id)}/messages", app, body=body))

    async def cancel(self, task_id: str, *, app: str | None = None) -> Task:
        return Task(**await self._request("POST", f"tasks/{segment(task_id)}/cancel", app))

    async def wait(self, task_id: str, *, timeout: float = 120.0, poll_interval: float = 1.0,
                   app: str | None = None) -> Task:
        if timeout <= 0 or poll_interval <= 0:
            raise ValueError("Timeout and polling interval must be positive")

        async def poll():
            while True:
                task = await self.get_task(task_id, app=app, history_length=0)
                if task.state in STOP_STATES:
                    return task
                await asyncio.sleep(poll_interval)

        try:
            return await asyncio.wait_for(poll(), timeout)
        except asyncio.TimeoutError as exc:
            raise WaitTimeout(task_id) from exc

    async def download_artifact(self, task_id: str, artifact_id: str, *, destination, app: str | None = None) -> Path:
        target = Path(destination)
        if target.exists():
            raise FileExistsError(f"Destination already exists: {target}")
        temp_path = None
        path = f"tasks/{segment(task_id)}/artifacts/{segment(artifact_id)}/download"
        headers = {**self._headers(app), "Accept-Encoding": "identity"}
        try:
            async with self._http.stream("GET", path, headers=headers) as response:
                await self._check(response)
                length_header = response.headers.get("Content-Length")
                expected = int(length_header) if length_header is not None else None
                if expected is not None and not 0 <= expected <= MAX_ARTIFACT_BYTES:
                    raise AgentServiceError(502, "Artifact exceeds the download size limit")
                with tempfile.NamedTemporaryFile(
                    dir=target.parent, prefix=".TMP_to_delete_deepfos_", delete=False,
                ) as stream:
                    temp_path = Path(stream.name)
                    size = 0
                    async for chunk in response.aiter_raw():
                        size += len(chunk)
                        if size > MAX_ARTIFACT_BYTES or (expected is not None and size > expected):
                            raise AgentServiceError(502, "Artifact exceeds the expected download size")
                        stream.write(chunk)
                    if expected is not None and size != expected:
                        raise AgentServiceError(502, "Incomplete artifact download")
            os.link(temp_path, target)
            return target
        except httpx.RequestError as exc:
            raise AgentServiceError(0, "Artifact download interrupted") from exc
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
