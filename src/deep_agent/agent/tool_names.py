"""Separate provider-facing names from durable and client-facing tool names."""
from __future__ import annotations

import json
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from hashlib import sha256

from pydantic_ai.messages import (
    ModelMessage, ModelResponse, RetryPromptPart, ToolAvailabilityDeltaPart, ToolCallPart, ToolReturnPart,
)
from pydantic_ai.toolsets import WrapperToolset

MCP_IDENTITY = "deepagent_mcp_identity"
WIRE_NAMES = "deepagent_wire_names"
_READABLE_SCOPE = re.compile(r"[A-Za-z0-9]+(?:[_-][A-Za-z0-9]+)*")
_READABLE_NAME = re.compile(r"[A-Za-z0-9]+(?:(?:__?|-)[A-Za-z0-9]+)*")
_NAMED_PARTS = (ToolCallPart, ToolReturnPart, RetryPromptPart)


def wire_name(group: str, *components: str) -> str:
    readable = "__".join((group, *components))
    if (len(readable) <= 64 and _READABLE_NAME.fullmatch(components[-1])
            and all(_READABLE_SCOPE.fullmatch(value) for value in components[:-1])):
        return readable
    digest = sha256(json.dumps(components, ensure_ascii=True).encode()).hexdigest()[:24]
    stem = re.sub(r"[^a-zA-Z0-9-]+", "_", "_".join(components)).strip("_")[:24] or "tool"
    # A triple separator cannot occur in a readable name.
    return f"{group}__{stem}___{digest}"


@dataclass(frozen=True)
class ToolName:
    group: str
    name: str
    scope: str = ""

    @property
    def wire(self) -> str:
        return wire_name(self.group, *((self.scope, self.name) if self.scope else (self.name,)))

    @property
    def public(self) -> str:
        return f"{self.scope}_{self.name}" if self.group == "mcp" else self.name


@dataclass
class ScopedToolset(WrapperToolset):
    group: str
    scope: str

    async def get_tools(self, ctx):
        result = {}
        registry = ctx.deps.tool_names
        for name, tool in (await super().get_tools(ctx)).items():
            identity = ToolName(self.group, name, self.scope)
            alias = identity.wire
            if registry.setdefault(alias, identity) != identity:
                raise ValueError(f"Conflicting provider tool name: {alias}")
            definition = tool.tool_def
            metadata = definition.metadata
            if self.group == "mcp":
                metadata = {**(metadata or {}), MCP_IDENTITY: (self.scope, name)}
            if self.group == "ui":
                description = f"{definition.description or ''}\n\nUI 工具业务编码：{name}。".strip()
                definition = replace(definition, description=description)
            result[alias] = replace(tool, toolset=self, tool_def=replace(definition, name=alias, metadata=metadata))
        return result

    async def call_tool(self, name, tool_args, ctx, tool):
        original = ctx.deps.tool_names[name].name
        return await super().call_tool(
            original, tool_args, replace(ctx, tool_name=original),
            replace(tool, tool_def=replace(tool.tool_def, name=original)),
        )


def public_tool_messages(messages: Sequence[ModelMessage], names: Mapping[str, ToolName]) -> list[ModelMessage]:
    """Record the original provider name only on parts with an alias."""
    result = []
    for message in messages:
        aliases = {}
        parts = list(message.parts)
        for index, part in enumerate(parts):
            if isinstance(part, _NAMED_PARTS) and (identity := names.get(part.tool_name)):
                parts[index] = replace(part, tool_name=identity.public)
                aliases[str(index)] = identity.wire
            elif isinstance(part, ToolAvailabilityDeltaPart) and any(name in names for name in part.tools_added):
                aliases[str(index)] = part.tools_added
                parts[index] = replace(part, tools_added=[
                    names[name].public if name in names else name for name in part.tools_added
                ])
        result.append(replace(
            message, parts=parts, metadata={**(message.metadata or {}), WIRE_NAMES: aliases},
        ) if aliases else message)
    return result


def model_tool_history(
    messages: Sequence[ModelMessage],
    *,
    pending_ids: Collection[str] = (),
    ui_aliases: Mapping[str, str] | None = None,
) -> list[ModelMessage]:
    """Restore each saved provider name and adapt authorized legacy UI calls."""
    pending_response = next(
        (message for message in reversed(messages) if isinstance(message, ModelResponse)), None,
    ) if pending_ids else None
    result = []
    previous_calls = {}
    known_aliases = {}
    ui_aliases = ui_aliases or {}
    for message in messages:
        aliases = (message.metadata or {}).get(WIRE_NAMES) or {}
        parts = list(message.parts)
        for index, part in enumerate(parts):
            alias = aliases.get(str(index))
            if isinstance(part, ToolAvailabilityDeltaPart) and isinstance(alias, list):
                parts[index] = replace(part, tools_added=alias)
                continue
            if not isinstance(part, _NAMED_PARTS):
                continue
            key = (part.tool_call_id, part.tool_name)
            if isinstance(part, ToolCallPart):
                if not alias and message is pending_response and part.tool_call_id in pending_ids:
                    alias = ui_aliases.get(part.tool_name)
                if part.tool_call_id:
                    if isinstance(alias, str):
                        known_aliases[key] = alias
                        previous_calls.pop(key, None)
                    else:
                        previous_calls[key] = (len(result), index)
                        known_aliases.pop(key, None)
            elif part.tool_call_id:
                previous = previous_calls.pop(key, None)
                alias = alias or known_aliases.pop(key, None)
                if previous and isinstance(alias, str) and alias.startswith("ui__"):
                    # An old UI call can have a new result from a resumed run.
                    # Use that result's recorded wire name on both sides of replay.
                    message_index, part_index = previous
                    old_message = result[message_index]
                    old_parts = list(old_message.parts)
                    old_parts[part_index] = replace(old_parts[part_index], tool_name=alias)
                    result[message_index] = replace(old_message, parts=old_parts)
            if isinstance(alias, str):
                parts[index] = replace(part, tool_name=alias)
        if aliases:
            metadata = dict(message.metadata)
            metadata.pop(WIRE_NAMES, None)
            result.append(replace(message, parts=parts, metadata=metadata or None))
        else:
            result.append(replace(message, parts=parts) if parts != message.parts else message)
    return result


def is_output_call(metadata, part, index: int) -> bool:
    if part.get("part_kind") != "tool-call":
        return False
    name = (metadata.get("deepagent_output") or {}).get("name")
    if not name or part.get("tool_name") != name:
        return False
    wire = (metadata.get(WIRE_NAMES) or {}).get(str(index))
    return wire is None or (isinstance(wire, str) and wire.startswith("output__"))
