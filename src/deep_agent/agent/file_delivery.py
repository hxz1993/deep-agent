"""Declare which files the current chat run will deliver."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Annotated

from pydantic import Field, TypeAdapter, ValidationError
from pydantic_ai import ModelRetry, RunContext, ToolReturn
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import FunctionToolset

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.tool_names import PRESENT, without_present_history
from deep_agent.agent.tool_output_limits import session_file_metadata_from_history
from deep_agent.conversation.models import ConversationAttachment, ConversationOutputFile
from deep_agent.files.storage import FileStoreError, LocalFileStore
from deep_agent.files.upload_settings import MAX_ATTACHMENT_BYTES
from deepfos_platform.client.platform import FILE_TYPE_ATTACHMENT

MAX_PRESENT_FILES = 10
_DELIVERY_METADATA = "deepagent_presented_files"
_FILE_LIST = TypeAdapter(list[ConversationOutputFile])


@dataclass
class RunFileDelivery:
    selected: dict[str, ConversationAttachment] = field(default_factory=dict)

    def publish(self, reply: dict, messages: Sequence[ModelMessage]) -> None:
        if reply["status"] != "completed" or not self.selected:
            return
        files: dict[str, ConversationOutputFile] = {}
        for message in messages:
            if not isinstance(message, ModelRequest):
                continue
            for part in message.parts:
                if isinstance(part, ToolReturnPart) and part.tool_name == PRESENT and part.outcome == "success":
                    files.update((item.id, item) for item in presented_files(part.metadata))
        if files:
            reply["output_files"] = [item.model_dump(exclude_none=True) for item in files.values()]
            reply["_output_file_scope"] = next(iter(files.values())).preview_scope


def presented_files(metadata: object) -> list[ConversationOutputFile]:
    if not isinstance(metadata, Mapping) or _DELIVERY_METADATA not in metadata:
        return []
    try:
        files = _FILE_LIST.validate_python(metadata[_DELIVERY_METADATA])
    except ValidationError:
        return []
    scope = metadata.get("deepagent_present_scope")
    if isinstance(scope, Mapping):
        for file in files:
            file.preview_scope = (scope.get("app"), scope.get("space"))
    return files


class FileDeliveryHistory(AbstractCapability[AgentDeps]):
    async def prepare_model_request(
        self, ctx: RunContext[AgentDeps], request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        # Keep the persisted projection and its compaction cursor intact.
        request_context.messages = without_present_history(request_context.messages, current_run=ctx.run_id)
        return request_context


async def _resolve_delivery_file(
    deps: AgentDeps, file_id: str, artifact: Mapping | None,
) -> ConversationAttachment:
    attachment = deps.attachment_by_id.get(file_id)
    unavailable = "Use a File ID from an attachment or a file returned by a tool in this conversation."
    if artifact is not None and artifact.get("kind") != "tool_result_overflow":
        return ConversationAttachment(
            id=file_id, name=artifact.get("file_name"), mime=artifact.get("mime"),
            byte_size=artifact.get("byte_size"), content_hash=artifact.get("content_hash") or artifact.get("sha256"),
        )
    if attachment is None:
        raise ModelRetry(unavailable)
    if isinstance(deps.file_store, LocalFileStore):
        try:
            file = await deps.file_store.describe_available(file_id, deps.execution_request_headers or deps.request_headers)
        except FileStoreError:
            raise ModelRetry(unavailable) from None
        if file.file_type != FILE_TYPE_ATTACHMENT:
            raise ModelRetry(unavailable)
        return ConversationAttachment(
            id=file_id, name=file.file_name, mime=file.mime, byte_size=file.file_size, content_hash=file.sha256,
        )
    cached = deps.file_bytes.get(f"attachment:{file_id}")
    return ConversationAttachment(
        id=file_id, name=attachment.name, mime=attachment.mime,
        byte_size=len(cached) if cached is not None else None,
    )


def build_file_delivery_toolset() -> FunctionToolset[AgentDeps]:
    toolset = FunctionToolset[AgentDeps]()

    async def prepare(ctx: RunContext[AgentDeps], tool: ToolDefinition) -> ToolDefinition | None:
        if ctx.deps.file_delivery is None:
            return None
        if ctx.deps.publication_protocol:
            description = (
                "Select up to 10 exact File IDs from this conversation to deliver with the current publication result. "
                "Use only files already checked and ready for the caller, never intermediate drafts. This works even "
                "when you also need to ask a follow-up question. Call present separately before the final result."
            )
            if ctx.deps.publication_protocol == "mcp":
                description += (
                    " When the user requests a downloadable report, prefer also registering the finished file "
                    "with export_file_to_download_center using its File ID when that tool is available. "
                    "Registration is for the current caller. Only after registration succeeds, tell the user "
                    "the filename and where to find it in the DeepFOS Download Center. If registration is "
                    "unavailable or fails, still present the finished file and report the actual outcome."
                )
            return replace(tool, description=description)
        return tool

    @toolset.tool(prepare=prepare, name=PRESENT)
    async def present(
        ctx: RunContext[AgentDeps],
        file_ids: Annotated[list[str], Field(min_length=1, max_length=MAX_PRESENT_FILES)],
    ) -> ToolReturn:
        """Attach the finished files to the final reply.

        Finish any gathering, generation, and checks first. Once ready to send
        the final answer, call present once with the complete list of files the user
        should receive, then send the answer in the next step. Omit drafts and
        skip present when there are no files to deliver or you still need input.

        Use up to 10 exact File IDs from user attachments or tool results in this
        conversation. Present an existing file directly when that is the
        deliverable. Call present separately from any final structured output.
        Markdown links and inline images may accompany the file cards.
        """
        deps = ctx.deps
        delivery = deps.file_delivery
        if delivery is None:
            raise ModelRetry("File delivery is unavailable in this context.")
        requested = dict.fromkeys(file_ids)
        artifacts = ctx.deps.session_file_artifacts
        if any(file_id not in artifacts and file_id not in ctx.deps.attachment_by_id for file_id in requested):
            # Some tools return trusted metadata ahead of the shared file index.
            current = [message for message in ctx.messages if message.run_id == ctx.run_id]
            artifacts = {**artifacts, **session_file_metadata_from_history(current)}
        files = {}
        for file_id in requested:
            file = delivery.selected.get(file_id) or await _resolve_delivery_file(deps, file_id, artifacts.get(file_id))
            if deps.publication_protocol and file.byte_size is None:
                try:
                    contents = await deps.file_store.download_bounded(
                        file_id, deps.execution_request_headers or deps.request_headers, max_bytes=MAX_ATTACHMENT_BYTES,
                    )
                except FileStoreError:
                    raise ModelRetry("The selected file is unavailable for delivery.") from None
                file.byte_size = len(contents)
                del contents
            files[file_id] = file
        if len(delivery.selected.keys() | files.keys()) > MAX_PRESENT_FILES:
            raise ModelRetry(f"The current run can deliver at most {MAX_PRESENT_FILES} distinct files.")
        delivery.selected.update(files)
        return ToolReturn(
            {"file_ids": list(delivery.selected)},
            metadata={
                _DELIVERY_METADATA: [item.model_dump(exclude_none=True) for item in files.values()],
                "deepagent_present_scope": {"app": ctx.deps.app_id, "space": ctx.deps.space_id},
            },
        )

    return toolset
