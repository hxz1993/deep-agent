"""The model's final result for either published-Agent transport."""
from __future__ import annotations

from typing import Any, Literal

from google.protobuf.json_format import MessageToDict, ParseDict, ParseError
from google.protobuf.struct_pb2 import Value
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from deep_agent.structured_output.schema import expand_schema, json_bytes
from deep_agent.structured_output.validation import validate_output

OUTPUT_NAME = "publication_result"
MAX_OUTPUT_BYTES = 256 * 1024
PublicationProtocol = Literal["a2a", "mcp", "http"]


class PublicationReply(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    text: str | None = None
    data: JsonValue = None


class PublicationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["completed", "input_required"]
    message: str | None = Field(default=None, max_length=2000)
    reply: PublicationReply | None = None


def publication_schema(business: dict[str, Any] | None) -> dict[str, Any]:
    schema = PublicationResult.model_json_schema()
    if business is not None:
        schema["$defs"]["PublicationReply"]["properties"]["data"] = expand_schema(business)
    return schema


def publication_instructions(config: dict[str, Any] | None) -> str:
    if config is None:
        completion = "For completed, provide reply.text and omit reply.data."
    else:
        text_rule = "Also provide reply.text." if config.get("allow_text_output") else "Omit reply.text."
        completion = f"For completed, provide reply.data conforming to the configured business schema. {text_rule}"
        if config.get("description"):
            completion += f" Business result: {config['description']}"
    return (
        "Return one publication result at the end of the run. "
        "Use completed when the task can be delivered. If essential input is missing, use input_required "
        "and ask a concise question in message. For input_required, include only useful, distinct context in reply.text "
        "and honor any request to withhold provisional results. Omit reply.data until the task is completed. "
        "Put the entire user-facing answer, including any requested tables and sections, in reply.text when text is "
        "required. Do not write the answer outside this result or point to a separate body. "
        f"{completion}"
    )


def _error(path: str, message: str) -> dict[str, str]:
    return {"path": path, "constraint": "publicationOutput", "message": message}


async def validate_publication(value: Any, config: dict[str, Any] | None) -> list[dict[str, str]]:
    try:
        if len(json_bytes(value)) > MAX_OUTPUT_BYTES:
            return [_error("/", "The publication result exceeds the output size limit.")]
        result = PublicationResult.model_validate(value)
    except ValueError:
        return [_error("/", "Return a JSON object with a valid status, optional message and reply fields.")]
    reply = result.reply
    text = bool(reply and reply.text and reply.text.strip())
    data = reply is not None and "data" in reply.model_fields_set
    if result.status == "input_required":
        if not result.message or not result.message.strip():
            return [_error("/message", "Ask a nonempty question when the task needs input.")]
        return [_error("/reply/data", "Omit business data until the task is completed.")] if data else []
    if text != (config is None or bool(config.get("allow_text_output"))):
        return [_error("/reply/text", "Provide nonempty text when required by the publication configuration, or omit it.")]
    if data != (config is not None):
        return [_error("/reply/data", "Provide business data only when a business schema is configured.")]
    if config is None:
        return []
    errors = await validate_output(config["json_schema"], reply.data)
    if errors:
        return errors
    # Both transports use protobuf Value, whose numeric field is a double.
    try:
        if MessageToDict(ParseDict(reply.data, Value())) == reply.data:
            return []
    except (ParseError, OverflowError, ValueError):
        pass
    return [_error("/reply/data", "Some values cannot be delivered exactly as JSON numbers. Use strings where the business schema permits.")]
