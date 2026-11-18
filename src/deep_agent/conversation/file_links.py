"""Resolve FileStore references only when presenting assistant prose."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping
from urllib.parse import quote, urlencode

from loguru import logger

from deep_agent.agent.attachments import index_attachment_refs
from deep_agent.agent.tool_output_limits import session_file_metadata_from_history
from deep_agent.conversation.models import AssistantConversationItem, ToolConversationItem, UserConversationItem
from deep_agent.files.storage import LOCAL_FILE_PREFIX, FileStoreError, LocalFileStore
from deepfos_platform.client.platform import FILE_TYPE_ATTACHMENT

_SCHEME = "attachment://"
_DESTINATION = re.compile(r"[ \t]*(?:<attachment://([^<>\s]+)>|attachment://([^()\s]+))[ \t]*\)")
_FENCE = re.compile(r"^(?: {0,3}>[ \t]?)*(?: {0,3})(\x60{3,}|~{3,})(.*)$")
_MAX_PENDING = 8192


class MarkdownFileLinks:
    """Incremental scanner for the inline Markdown spelling used by the Agent."""

    def __init__(self, resolve: Callable[[str, bool], Awaitable[str | None]], *, in_link_label: bool = False):
        self.resolve = resolve
        self.pending = ""
        self.in_link_label = in_link_label
        self.normal_line = in_link_label
        self.verbatim_line = False
        self.fence = ""
        self.code_ticks = 0
        self.link_depth = 0

    async def feed(self, delta: str, *, finish: bool = False) -> str:
        source = self.pending + delta
        self.pending = ""
        result = []
        cursor = 0
        while cursor < len(source):
            if self.verbatim_line:
                end = source.find("\n", cursor)
                self.verbatim_line = end == -1
                next_line = len(source) if self.verbatim_line else end + 1
                result.append(source[cursor:next_line])
                cursor = next_line
                continue
            if not self.normal_line:
                end = source.find("\n", cursor)
                line = source[cursor:] if end == -1 else source[cursor:end]
                if self.fence or (not self.code_ticks and not self.link_depth and line.startswith((" ", "\t", ">", "~", "\x60"))):
                    fence = _FENCE.match(line.rstrip("\r"))
                    if end == -1 and not finish and len(line) < _MAX_PENDING and (
                        self.fence or fence or not line.strip(" >\t~\x60")
                    ):
                        break
                    if self.fence or fence or line.startswith(("    ", "\t")):
                        if fence and not self.code_ticks:
                            marker, tail = fence.groups()
                            if not self.fence and not (marker[0] == "\x60" and "\x60" in tail):
                                self.fence = marker
                            elif self.fence and marker.startswith(self.fence) and not tail.strip():
                                self.fence = ""
                        if end != -1:
                            result.append(source[cursor:end + 1])
                            cursor = end + 1
                            continue
                        result.append(line)
                        cursor = len(source)
                        self.verbatim_line = True
                        continue
                self.normal_line = True

            char = source[cursor]
            if char == "\n":
                self.normal_line = False
            if char == "\\" and not self.code_ticks:
                if cursor + 1 == len(source) and not finish:
                    break
                if cursor + 1 < len(source) and source[cursor + 1] != "\n":
                    result.append(source[cursor:cursor + 2])
                    cursor += 2
                    continue
            if self.link_depth:
                self.link_depth += (char == "(") - (char == ")")
                if char == "\n":
                    self.link_depth = 0
            elif char == "\x60":
                end = cursor + 1
                while end < len(source) and source[end] == char:
                    end += 1
                if end == len(source) and not finish and end - cursor < _MAX_PENDING:
                    break
                count = end - cursor
                if not self.code_ticks or self.code_ticks == count:
                    self.code_ticks = count if not self.code_ticks else 0
                result.append(source[cursor:end])
                cursor = end
                continue
            elif not self.code_ticks and (char == "!" or (char == "[" and not self.in_link_label)):
                start = cursor + (char == "!")
                if start == len(source) and not finish:
                    break
                if source[start:start + 1] == "[":
                    end = self._label_end(source, start)
                    if end is None and not finish and len(source) - cursor < _MAX_PENDING:
                        break
                    if end is not None and end >= 0:
                        rest = source[end + 1:]
                        if not rest and not finish:
                            break
                        if rest.startswith("("):
                            target = rest[1:].lstrip(" \t")
                            prefix = target.removeprefix("<")
                            match = None
                            if prefix.startswith(_SCHEME) or _SCHEME.startswith(prefix):
                                match = _DESTINATION.match(rest, 1)
                                if not match and not finish and ")" not in rest and "\n" not in rest and len(rest) < _MAX_PENDING:
                                    break
                            label = source[start + 1:end]
                            if char == "[" and "![" in label and _SCHEME in label:
                                # A link's visible text can contain an inline image.
                                label = await MarkdownFileLinks(self.resolve, in_link_label=True).feed(label, finish=True)
                            head = source[cursor:start + 1] + label + "]"
                            if match:
                                angle, bare = match.groups()
                                url = await self.resolve(angle or bare, char == "!")
                                result.append(f"{head}(<{url}>)" if url else label)
                                cursor = end + 1 + match.end()
                                continue
                            self.link_depth = 1
                            result.append(head + "(")
                            cursor = end + 2
                            continue
            result.append(char)
            cursor += 1
        self.pending = source[cursor:]
        return "".join(result)

    @staticmethod
    def _label_end(source: str, start: int) -> int | None:
        depth = 1
        escaped = False
        for index in range(start + 1, min(start + _MAX_PENDING, len(source))):
            char = source[index]
            if char in "\r\n":
                return -1
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            depth += (char == "[") - (char == "]")
            if not depth:
                return index
        return None


class ReplyFiles:
    """Request-scoped file authority and output projection; no model-history mutation."""

    def __init__(self, request, file_store, session_id, headers, *, diagnostic=False, load_history=None):
        self.base = request.base_url
        self.file_store = file_store
        self.session_id = session_id
        self.headers = headers
        self.diagnostic = diagnostic
        self.load_history = load_history
        self.files: dict[str, tuple[str | None, str | None]] = {}
        self.stream = MarkdownFileLinks(self.url)

    def add_artifact(self, artifact: Mapping) -> None:
        file_id = artifact.get("file_id")
        if isinstance(file_id, str) and artifact.get("kind") in {"sandbox_output", "document_evidence"}:
            self.files[file_id] = artifact.get("file_name"), artifact.get("mime") or artifact.get("mime_type")

    async def url(self, file_id: str, image: bool) -> str | None:
        if (len(file_id) > 256 or not re.fullmatch(r"[A-Za-z0-9_-]+", file_id)
                or self.base.scheme not in {"http", "https"}
                or not re.fullmatch(r"[\w.\[\]:-]+", self.base.netloc, re.ASCII)):
            return None
        if file_id not in self.files and self.load_history is not None:
            load_history, self.load_history = self.load_history, None
            try:
                messages = await load_history()
            except Exception as exc:
                logger.warning("Reply file references unavailable error_type={}", type(exc).__name__)
                return None
            for ref in index_attachment_refs(messages=messages).values():
                self.files.setdefault(ref.id, (ref.name, ref.mime))
            for artifact in session_file_metadata_from_history(messages).values():
                self.add_artifact(artifact)
        if file_id not in self.files:
            return None
        name, mime = self.files[file_id]
        if file_id.startswith(LOCAL_FILE_PREFIX):
            if not isinstance(self.file_store, LocalFileStore):
                return None
            try:
                meta = await self.file_store.describe_available(file_id, self.headers)
            except FileStoreError:
                return None
            if meta.file_type != FILE_TYPE_ATTACHMENT:
                return None
            mime = meta.mime
            path = f"/api/v1/local-files/{quote(file_id, safe='')}/download"
            if self.diagnostic:
                session = quote(self.session_id, safe="")
                path = f"/api/v1/diagnostics/sessions/{session}/files/{quote(file_id, safe='')}/download"
            path = self.base.path.rstrip("/") + path
            query = ""
        else:
            # Match the existing Platform download link used by Console file cards.
            root, sep, _ = self.base.path.partition("/deepfos-server/")
            prefix = f"{root}/deepfos-server" if sep else "/deepfos-server"
            path = f"{prefix}/platform-file-server/files/download"
            query = urlencode({"id": file_id, **({"fileName": name} if name else {})})
        if image and not (mime or "").lower().startswith("image/"):
            return None
        return str(self.base.replace(path=path, query=query, fragment=""))

    async def text(self, text: str) -> str:
        if _SCHEME not in text:
            return text
        return await MarkdownFileLinks(self.url).feed(text, finish=True)

    async def history(self, history):
        items = []
        for item in history.items:
            if isinstance(item, UserConversationItem):
                for ref in item.attachments:
                    self.files.setdefault(ref.id, (ref.name, ref.mime))
            elif isinstance(item, ToolConversationItem):
                for artifact in item.artifacts:
                    self.add_artifact(artifact.model_dump())
            elif isinstance(item, AssistantConversationItem) and item.text and _SCHEME in item.text:
                item = item.model_copy(update={"text": await self.text(item.text)})
            items.append(item)
        return history.model_copy(update={"items": items})

    async def event(self, event: dict, *, event_type: str | None = None):
        """Yield existing SSE event types with projected assistant text."""
        kind = event_type or event.get("type")
        if kind == "text" and isinstance(event.get("delta"), str):
            self.text_event = event
            text = await self.stream.feed(event["delta"])
            if text:
                yield event if text == event["delta"] else {**event, "delta": text}
            return
        if kind in {"tool_call", "tool_result", "final", "error", "cancelled", "observation_end", "observation_reset"}:
            if self.stream.pending:
                text = await self.stream.feed("", finish=True)
                if text:
                    yield {"type": "text", **self.text_event, "delta": text}
            self.stream = MarkdownFileLinks(self.url)
        if kind == "tool_result":
            for artifact in event.get("artifacts") or ():
                self.add_artifact(artifact)
        if kind == "final" and not event.get("output_info") and isinstance(event.get("output"), str):
            text = await self.text(event["output"])
            if text != event["output"]:
                event = {**event, "output": text}
        yield event
