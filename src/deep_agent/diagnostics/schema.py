"""Allowlisted operational facts. Never serialize arbitrary application objects."""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .messages import describe

EVENTS = {
    "document_configuration_updated": "文档解析配置已更新",
    "document_parse_started": "文档解析开始",
    "document_parse_phase": "文档解析阶段更新",
    "document_parse_completed": "文档解析完成",
    "document_parse_failed": "文档解析失败",
    "document_parse_cancelled": "文档解析取消",
    "document_http_request": "文档解析HTTP请求开始",
    "document_http_response": "文档解析HTTP响应已收到",
    "document_read_completed": "文档分页读取完成",
    "document_read_failed": "文档读取失败",
    "document_adaptation_completed": "文档可读格式适配完成",
    "document_layout_completed": "文档定位视图生成完成",
    "document_layout_failed": "文档定位视图生成失败",
    "document_export_completed": "文档证据导出状态已确认",
    "document_bundle_completed": "文档证据包生成结束",
    "document_image_download": "文档处理页图获取结束",
    "mcp_request_context_resolved": "MCP 请求上下文已组装",
    "history_tool_result_matched": "历史工具已匹配后续结果",
    "history_tool_result_synthesized": "历史缺失工具结果已补齐",
    "title_generation_started": "会话标题生成开始",
    "title_generation_completed": "会话标题生成完成",
    "title_generation_failed": "会话标题生成失败，保留兜底标题",
    "title_generation_cancelled": "会话标题生成已取消",
    "service_starting": "服务正在初始化", "service_started": "服务已启动",
    "service_stopped": "服务已停止", "service_initialization_failed": "服务初始化失败",
    "service_shutdown_failed": "服务关闭失败",
    "agent_run_started": "执行开始", "agent_run_completed": "执行结束",
    "agent_run_failed": "执行失败", "agent_run_cancelled": "执行已取消",
    "agent_cancel_observed": "执行取消来源已记录",
    "agent_cancel_api_received": "收到停止请求",
    "agent_stream_closed": "会话发送连接已结束",
    "agent_run_pending": "执行等待人工操作", "message_persistence_failed": "会话内容保存失败",
    "manual_compaction_started": "上下文压缩开始", "manual_compaction_completed": "上下文压缩结束",
    "context_compaction_progress": "上下文压缩阶段变化", "context_compaction_completed": "上下文压缩结束",
    "context_compaction_phase": "上下文压缩阶段变化",
    "tool_output_spilled": "大工具结果已转存", "tool_output_spill_failed": "大工具结果转存失败",
    "channel_registration_state": "渠道扫码状态更新",
    "channel_registration_response": "渠道扫码平台响应",
    "channel_registration_failed": "渠道扫码任务失败",
    "channel_received": "渠道消息已接收", "channel_queued": "渠道消息已排队",
    "channel_dispatched": "渠道消息已分发", "channel_agent_linked": "渠道消息已关联 Agent 执行",
    "channel_completed": "渠道消息处理结束", "channel_cancelled": "渠道消息处理已取消",
    "channel_processing_failed": "渠道消息处理失败", "channel_ingress_failed": "渠道消息接收失败",
    "channel_preview_failed": "渠道进度预览投递失败", "channel_delivery_sent": "渠道最终回复投递成功",
    "channel_delivery_failed": "渠道最终回复投递失败", "channel_adapter_started": "渠道连接已启动",
    "channel_adapter_stopped": "渠道连接已停止", "channel_adapter_start_failed": "渠道连接启动失败",
    "diagnostic_users_completed": "平台用户名批量查询完成",
    "diagnostic_users_failed": "平台用户名批量查询失败",
    "diagnostic_users_fallback": "平台用户名查询降级为 ID",
    "wecom_profile_failed": "企微成员资料查询失败",
    "channel_identity_unavailable": "渠道成员资料暂不可用，继续对话",
    "channel_reconcile_failed": "渠道配置同步失败",
    "tool_validation_retry": "工具参数校验要求重试",
    "model_api_request": "模型 API 请求开始",
    "model_api_response": "模型 API 响应头已收到",
    "model_api_stream_first_byte": "模型 API 首批传输字节已收到",
    "model_api_stream_progress": "模型 API 传输流进度",
    "model_api_stream_finished": "模型 API 传输流结束",
    "builtin_operation_started": "内置工具处理步骤开始",
    "builtin_operation_completed": "内置工具处理步骤完成",
    "builtin_operation_failed": "内置工具处理步骤失败",
    "builtin_operation_cancelled": "内置工具处理步骤已取消",
}
for prefix, label in (("model_request", "模型请求"), ("agent_tool", "工具调用")):
    for suffix, detail in {
        "started": "开始", "completed": "结束", "failed": "失败", "retrying": "要求重试",
        "cancelled": "已取消", "deferred": "等待人工操作", "still_waiting": "仍在等待",
    }.items():
        EVENTS[f"{prefix}_{suffix}"] = label + detail

# Public ingress and private processing stages intentionally log facts only.
EVENTS.update({
    "webhook_received": "Webhook 请求已到达",
    "webhook_checked": "Webhook 请求检查完成",
    "webhook_verified": "Webhook 验签解密通过",
    "webhook_accepted": "Webhook 消息已确认接收",
    "webhook_rejected": "Webhook 请求已拒绝",
    "webhook_completed": "Webhook 请求结束",
    "webhook_failed": "Webhook 请求处理失败",
    "webhook_log_limited": "Webhook 入口日志已限量",
    "webhook_gateway_received": "网关已接收 Webhook 消息",
    "webhook_gateway_duplicate": "网关已去重 Webhook 消息",
    "webhook_media_download_started": "企微媒体下载开始",
    "webhook_media_download_completed": "企微媒体下载完成",
    "webhook_audio_conversion_started": "企微语音转码开始",
    "webhook_audio_conversion_completed": "企微语音转码完成",
    "webhook_audio_conversion_failed": "企微语音转码失败",
    "webhook_media_upload_completed": "企微媒体已进入附件管线",
    "webhook_reply_started": "企微应用回复开始",
    "webhook_reply_completed": "企微应用回复完成",
    "webhook_loading_failed": "企微应用等待提示发送失败",
    "webhook_bridge_completed": "企微应用接口调用完成",
    "webhook_bridge_failed": "企微应用接口调用失败",
})

IDENTIFIER_FIELDS = frozenset({
    "mcp_server", "app_id", "space_id", "language",
    "request_id", "session_id", "run_id", "agent_id", "conversation_id", "channel_run_id", "account_id", "wecom_application_id",
    "tool_call_id", "tool", "model_id", "model_name", "provider_name", "phase", "status",
    "message_type", "external_message_id", "profile_service", "discovery_type", "discovery_namespace", "auth_context",
    "error_type", "error_code", "result_status", "command", "mode", "finish_reason", "compaction", "reason",
})
NUMBER_FIELDS = frozenset({
    "elapsed_ms", "token_count", "returned_pages", "offset", "char_offset",
    "requested_count", "resolved_count", "count",
    "duration_ms", "request_index", "attempt", "queue_depth", "input_tokens", "output_tokens",
    "total_tokens", "requests", "tool_calls", "tool_search_calls", "cache_read_tokens",
    "cache_write_tokens", "spill_count", "spill_failures", "byte_size", "spilled_bytes",
    "tool_count", "unique_tool_count",
    "stream_chunks", "stream_bytes", "first_byte_ms", "last_byte_age_ms",
})
ADAPTATION_NUMBER_FIELDS = frozenset({
    "source_characters", "adapted_characters", "native_token_count", "adapted_token_count",
    "table_count", "markdown_table_count", "compact_html_table_count", "adaptation_fallback_count", "image_placeholder_count", "supplemented_block_count",
})
ADAPTATION_FIELDS = ADAPTATION_NUMBER_FIELDS | frozenset({
    "adaptation_version", "request_id", "session_id", "run_id", "tool_call_id",
    "provider_name", "model_name", "tool", "phase", "elapsed_ms", "duration_ms", "source_hash", "file_type",
})
LAYOUT_NUMBER_FIELDS = frozenset({"block_count", "matched_blocks", "appended_blocks"})
LAYOUT_FIELDS = LAYOUT_NUMBER_FIELDS | frozenset({
    "layout_version", "request_id", "session_id", "run_id", "agent_id", "tool_call_id",
    "provider_name", "model_name", "tool", "phase", "status", "elapsed_ms", "duration_ms",
    "cache_hit", "source_hash", "file_type", "error_type", "error_code",
})
LAYOUT_EVENTS = frozenset({"document_layout_completed", "document_layout_failed"})
BUNDLE_EVENTS = frozenset({"document_bundle_completed", "document_image_download"})
BUNDLE_NUMBER_FIELDS = frozenset({"saved_images", "missing_images", "page_ordinal"})
BUNDLE_FIELDS = BUNDLE_NUMBER_FIELDS | frozenset({
    "request_id", "session_id", "run_id", "agent_id", "tool_call_id", "provider_name",
    "model_name", "tool", "phase", "status", "elapsed_ms", "byte_size", "reason_code",
})
BOOLEAN_FIELDS = frozenset({"cache_hit", "has_more", "parse_merge_tables", "parse_relevel_titles", "parse_layout_visualization"})
PERSISTENCE_NUMBER_FIELDS = frozenset({
    "write_message_count", "write_part_count", "message_ordinal", "part_index",
    "stored_part_count", "incoming_part_count", "database_error_code",
    "message_identity_merges", "response_id_mismatches",
})


def _persistence_fields(fields: dict, result: dict) -> None:
    """Failure-only vocabulary; no arbitrary SQL, exception or payload text."""
    choices = {
        "persistence_reason": {"immutable_part_conflict", "database_integrity", "database_operational",
                               "database_data", "database_driver", "timeout", "invalid_value", "unexpected"},
        "db_dialect": {"mysql", "postgresql", "sqlite"},
        "write_stage": {"assemble", "append", "publish"},
        "write_hook": {"unknown", "_prepare_model_node", "after_model_request", "after_node_run",
                       "before_tool_execute", "wrap_tool_execute", "after_tool_execute",
                       "wrap_run_event_stream", "terminal", "status_finalization", "event_worker"},
        "conflict_field": {"part_kind", "content", "args", "tool_name", "tool_call_id", "tool_kind",
                           "metadata", "provider_details", "id", "signature", "provider_name", "outcome",
                           "tools_added", "tools_removed", "other", "parts_count", "metadata.deepagent_timing",
                           "metadata.deepagent_plan", "metadata.overflow_handle", "metadata.ptc_diagnostics"},
    }
    kinds = {"text", "thinking", "tool-call", "tool-return", "builtin-tool-call", "builtin-tool-return",
             "retry-prompt", "user-prompt", "system-prompt", "tool-availability-delta", "other"}
    choices.update(stored_part_kind=kinds, incoming_part_kind=kinds)
    for key, allowed in choices.items():
        value = fields.get(key)
        if type(value) is str and value in allowed:
            result[key] = value
    patterns = {"conflict_event_id": r"[0-9a-f]{32}", "database_sqlstate": r"[A-Z0-9]{5}",
                "cause_type": r"[A-Za-z_][A-Za-z0-9_]{0,79}",
                "exception_chain": r"[A-Za-z_][A-Za-z0-9_:]{0,179}"}
    for key in ("failure_location", "failure_caller"):
        patterns[key] = r"(?:agent|db|diagnostics)/[a-zA-Z0-9_/.]+\.py:[0-9]{1,6}:[A-Za-z_][A-Za-z0-9_]{0,79}"
    for key in ("product_version", "pydantic_ai_version", "pydantic_harness_version", "sqlalchemy_version"):
        patterns[key] = r"(?:[0-9][A-Za-z0-9_.+-]{0,39}|unavailable)"
    for key, pattern in patterns.items():
        value = fields.get(key)
        if type(value) is str and len(value) <= 180 and re.fullmatch(pattern, value):
            result[key] = value
    for key in PERSISTENCE_NUMBER_FIELDS:
        value = fields.get(key)
        if type(value) is int and 0 <= value <= 10**15:
            result[key] = value


TOKEN = re.compile(r"[A-Za-z0-9_.:/-]{1,180}\Z")
ISSUE_CODES = frozenset({
    "directory_unavailable", "write_failed", "queue_full", "capacity_exceeded", "shutdown_incomplete",
    "runtime_fault", "file_limit_exceeded", "status_unavailable", "source_stale", "operational_output_failed",
})
MAX_RECORD_BYTES = 8192
TOOL_LIST_BUDGET = 3600
TOOL_NAME = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
TOOL_LIST_FIELDS = ("redeclared_tool_names", "duplicate_tool_names", "added_tool_names", "history_tool_names", "tool_names")
API_PATHS = frozenset(("/chat/completions", "/responses", "/messages", "/custom"))
MANIFEST_STATUSES = frozenset(("complete", "unavailable", "body_too_large", "unsupported"))


def _safe_model_origin(value: object) -> str | None:
    """Never retain custom gateway paths, credentials or URL parameters."""
    if type(value) is not str or len(value) > 300 or not re.fullmatch(r"https?://[A-Za-z0-9.:[\]-]+", value):
        return None
    try:
        parsed = urlsplit(value)
        if (not parsed.hostname or parsed.username or parsed.password or parsed.path
                or parsed.query or parsed.fragment or parsed.port == 0):
            return None
    except ValueError:
        return None
    return value


def _model_api_fields(fields: dict, result: dict) -> None:
    origin = _safe_model_origin(fields.get("model_api"))
    if origin is not None:
        result["model_api"] = origin
    for name, allowed in (("api_path", API_PATHS), ("tool_manifest_status", MANIFEST_STATUSES),
                          ("stream_state", ("unconsumed", "reading", "consumer_idle", "eof", "cancelled", "failed", "closed", "closed_unconsumed", "close_failed")),
                          ("http_method", ("POST", "GET", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"))):
        value = fields.get(name)
        if type(value) is str and value in allowed:
            result[name] = value
    status = fields.get("http_status")
    if type(status) is int and 100 <= status <= 599:
        result["http_status"] = status
    for name, pattern in (("api_request_id", r"[a-f0-9]{32}"), ("tool_names_hash", r"[a-f0-9]{64}")):
        value = fields.get(name)
        if type(value) is str and re.fullmatch(pattern, value):
            result[name] = value

    if not any(type(fields.get(name)) is list for name in TOOL_LIST_FIELDS) and "tools_omitted" not in fields:
        return

    # Reserve the fixed record envelope plus omission counters. Even a fully
    # populated scalar record must leave room for useful, bounded tool facts.
    budget = min(TOOL_LIST_BUDGET, max(0, MAX_RECORD_BYTES - 1152
        - len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode())))
    budget -= sum(len(name) + 6 for name in TOOL_LIST_FIELDS if type(fields.get(name)) is list)
    omitted = fields.get("tools_omitted", 0)
    omitted = omitted if type(omitted) is int and 0 <= omitted <= 10**15 else 0
    for name in TOOL_LIST_FIELDS:
        value = fields.get(name)
        if type(value) is not list:
            continue
        kept = []
        # Duplicate names are more actionable than the full inventory. Bound
        # inspection as well as output; count skipped entries without repr().
        for item in value[:256]:
            if budget < 4:
                break
            if type(item) is str and TOOL_NAME.fullmatch(item) and len(item) + 3 <= budget:
                kept.append(item)
                budget -= len(item) + 3
        result[name] = kept
        omitted += len(value) - len(kept)
    if any(type(fields.get(name)) is list for name in TOOL_LIST_FIELDS) or "tools_omitted" in fields:
        result["tools_omitted"] = omitted


def safe_fields(fields: dict, *, event: str | None = None) -> dict:
    # Document statistics belong to their event, not every model/lifecycle
    # record. Limit each specialized event's entire scalar set as well: adding
    # its fields to every base fact can exceed the wire limit before any prose
    # or tool names are included, and trimming those cannot recover the record.
    adapting = event == "document_adaptation_completed"
    layout = event in LAYOUT_EVENTS
    bundle = event in BUNDLE_EVENTS
    if adapting:
        fields = {key: value for key, value in fields.items() if key in ADAPTATION_FIELDS}
    elif layout:
        fields = {key: value for key, value in fields.items() if key in LAYOUT_FIELDS}
    elif bundle:
        fields = {key: value for key, value in fields.items() if key in BUNDLE_FIELDS}
    result = {}
    for name in BOOLEAN_FIELDS:
        if type(fields.get(name)) is bool:
            result[name] = fields[name]
    for name, pattern in (("source_hash", r"[a-f0-9]{64}"), ("file_type", r"[a-z0-9]{1,8}"),
                          ("document_task_id", r"[A-Za-z0-9_.-]{1,100}")):
        value = fields.get(name)
        if type(value) is str and re.fullmatch(pattern, value):
            result[name] = value
    if fields.get("document_api_path") in {
        "/api/v4/file-urls/batch", "/api/v4/extract-results/batch/{task}",
        "/api/v2/ocr/jobs", "/api/v2/ocr/jobs/{task}", "/api/paas/v4/layout_parsing", "/signed-artifact",
    }:
        result["document_api_path"] = fields["document_api_path"]
    event_identifiers = {"adaptation_version"} if adapting else {"layout_version"} if layout else {"reason_code"} if bundle else set()
    for name in IDENTIFIER_FIELDS | event_identifiers:
        value = fields.get(name)
        # No coercion/repr: even exception __str__ and custom number objects may
        # execute code. Values with URLs, whitespace or credentials are omitted.
        if type(value) is str and TOKEN.fullmatch(value) and "://" not in value:
            result[name] = value
    event_numbers = ADAPTATION_NUMBER_FIELDS if adapting else LAYOUT_NUMBER_FIELDS if layout else BUNDLE_NUMBER_FIELDS if bundle else set()
    for name in NUMBER_FIELDS | event_numbers:
        value = fields.get(name)
        if type(value) in (int, float) and 0 <= value <= 10**15 and math.isfinite(value):
            result[name] = value
    # Display names come from the loaded Agent configuration, never prompts.
    name = fields.get("agent_name")
    if type(name) is str and re.fullmatch(r"[\w .:/()\-|\\]{1,100}", name) and "://" not in name:
        result["agent_name"] = name
    _model_api_fields(fields, result)
    if event == "message_persistence_failed":
        _persistence_fields(fields, result)
    origin = _safe_model_origin(fields.get("target_origin"))
    if origin is not None:
        result["target_origin"] = origin
    return result


def sanitize_record(payload: dict) -> dict | None:
    """Revalidate file records at the read boundary, never trust file contents."""
    if type(payload) is not dict or type(payload.get("event")) is not str or payload["event"] not in EVENTS:
        return None
    if payload.get("source") not in ("api", "gateway") or payload.get("level") not in (
        "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL",
    ):
        return None
    for key in ("record_id", "boot_id", "instance_id"):
        value = payload.get(key)
        if type(value) is not str or not re.fullmatch(r"[a-f0-9-]{16,80}", value):
            return None
    timestamp = payload.get("timestamp")
    if type(timestamp) is not str or len(timestamp) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
    except ValueError:
        return None
    pid = payload.get("process_id")
    if type(pid) is not int or not 0 < pid < 2**32:
        return None
    record = {
        "schema_version": 1,
        "timestamp": parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds"),
        "event": payload["event"], "summary": EVENTS[payload["event"]],
        "level": payload["level"], "source": payload["source"], "process_id": pid,
        **{key: payload[key] for key in ("record_id", "boot_id", "instance_id")},
        **safe_fields(payload, event=payload["event"]),
    }

    record["summary"] = describe(record)
    return record
