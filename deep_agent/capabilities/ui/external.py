from __future__ import annotations

from collections.abc import Callable
from typing import Any

from jsonschema.exceptions import SchemaError, ValidationError
from jsonschema.validators import validator_for
from loguru import logger
from pydantic_ai import ExternalToolset, ModelRetry
from pydantic_ai.tools import ToolDefinition

from deep_agent.capabilities.ui.types import UIToolRecord


UI_TOOL_MAX_RETRIES = 1

ArgsValidator = Callable[..., None]


def _error_path(error: ValidationError) -> str:
    parts = [str(part) for part in error.absolute_path]
    return ".".join(parts) or "arguments"


def _validate_clarify_args(args: dict[str, Any]) -> None:
    fields = args.get("fields")
    if not isinstance(fields, list) or not fields:
        raise ModelRetry("clarify 参数不完整：fields 必须是非空数组，请补全后重新调用。")

    for index, field in enumerate(fields):
        if not isinstance(field, dict):
            raise ModelRetry(f"clarify.fields[{index}] 必须是对象，请修正后重新调用。")
        if not any(field.get(key) for key in ("field", "id", "name", "dimension", "label")):
            raise ModelRetry(
                f"clarify.fields[{index}] 缺少稳定字段标识，"
                "请至少提供 field、id、name、dimension 或 label。"
            )

        field_type = field.get("type")
        if field_type == "select":
            options = field.get("options")
            if not isinstance(options, list) or not options:
                raise ModelRetry(
                    f"clarify.fields[{index}] 的 type=select，但 options 为空，"
                    "请提供候选项或改用 text/textarea/input。"
                )


def _semantic_validator(record: UIToolRecord, args: dict[str, Any]) -> None:
    if record.widget == "clarify" or record.name.endswith("__clarify"):
        _validate_clarify_args(args)


def build_args_validator(record: UIToolRecord) -> ArgsValidator:
    try:
        validator_class = validator_for(record.parameters_json_schema)
        validator_class.check_schema(record.parameters_json_schema)
        schema_validator = validator_class(record.parameters_json_schema)
    except SchemaError as exc:
        logger.warning("Invalid UI tool schema, semantic validation only: {} ({})", record.name, exc)
        schema_validator = None

    def validate(_ctx: Any, **args: Any) -> None:
        if schema_validator is not None:
            try:
                errors = sorted(
                    schema_validator.iter_errors(args),
                    key=lambda item: tuple(str(part) for part in item.absolute_path),
                )
            except Exception as exc:
                logger.warning(
                    "UI tool schema validation failed, semantic validation only: {} ({})",
                    record.name,
                    exc,
                )
                errors = []
            if errors:
                error = errors[0]
                raise ModelRetry(
                    f"工具 {record.name} 参数结构不合法：{_error_path(error)}: {error.message}。"
                    "请按工具参数 schema 修正后重新调用。"
                )
        _semantic_validator(record, args)

    return validate


class RetryableExternalToolset(ExternalToolset):
    """External UI tools with one model-correction retry and schema validation."""

    def __init__(
        self,
        tool_defs: list[ToolDefinition],
        *,
        validators: dict[str, ArgsValidator],
        id: str | None = None,
        max_retries: int = UI_TOOL_MAX_RETRIES,
    ) -> None:
        super().__init__(tool_defs, id=id)
        self._validators = validators
        self._max_retries = max_retries

    async def get_tools(self, ctx: Any):
        tools = await super().get_tools(ctx)
        for name, tool in tools.items():
            tool.max_retries = self._max_retries
            tool.args_validator_func = self._validators.get(name)
        return tools
