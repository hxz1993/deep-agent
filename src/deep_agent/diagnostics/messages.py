"""Readable operational messages built only from already allowlisted facts."""
from __future__ import annotations


_STATE = {
    "started": "开始", "completed": "完成", "failed": "失败", "cancelled": "已取消",
    "retrying": "需要重试", "deferred": "等待人工操作", "still_waiting": "仍在等待",
    "pending": "等待人工操作",
}


def describe(record: dict) -> str:
    """Generate the written message; never inspect tool arguments or results."""
    event = record["event"]
    suffix = event.rsplit("_", 1)[-1]
    state = _STATE.get(suffix, "")
    message = record["summary"]
    details = []
    if event.startswith("diagnostic_users_"):
        for key, label in (("profile_service", "服务"), ("discovery_type", "发现方式"),
                           ("discovery_namespace", "命名空间"), ("target_origin", "目标"),
                           ("http_status", "HTTP"), ("requested_count", "请求人数"),
                           ("resolved_count", "解析人数")):
            if key in record:
                details.append(f"{label} {record[key]}")
    tool, model, agent = (record.get(key) for key in ("tool", "model_name", "agent_name"))
    if event.startswith("agent_tool_"):
        state = "仍在等待" if event.endswith("_still_waiting") else state
        message = f"工具 {tool} 调用{state}" if tool else message
        if event == "agent_tool_completed" and record.get("result_status") == "error":
            message = (f"工具 {tool} " if tool else "工具") + "调用已返回，脚本执行失败"
    elif event.startswith("model_request_"):
        state = "仍在等待" if event.endswith("_still_waiting") else state
        message = f"模型 {model} 请求{state}" if model else message
    elif event.startswith("model_api_stream_"):
        if model:
            message = f"模型 {model} " + message
        for key, label, unit in (("stream_bytes", "已收传输字节", "B"),
                                  ("stream_chunks", "传输块", ""),
                                  ("first_byte_ms", "首字节耗时", "ms"),
                                  ("last_byte_age_ms", "距上次字节", "ms")):
            if key in record:
                details.append(f"{label} {record[key]}{unit}")
        if "stream_state" in record:
            details.append(f"流状态 {record['stream_state']}")
    elif event in ("model_api_request", "model_api_response"):
        message = f"模型 {model} API " if model else "模型 API "
        message += "请求开始" if event == "model_api_request" else "响应头已收到"
        endpoint = " ".join(record[key] for key in ("http_method", "api_path") if key in record)
        if endpoint:
            details.append(endpoint)
        if "http_status" in record:
            details.append(f"HTTP {record['http_status']}")
        if event == "model_api_request" and "tool_count" in record:
            details.append(f"提供 {record['tool_count']} 个工具")
    elif event.startswith("agent_run_"):
        message = f"Agent {agent} 执行{state}" if agent else message
        if model:
            details.append(f"模型 {model}")
    elif event == "tool_validation_retry" and tool:
        message = f"工具 {tool} 参数校验需要重试"
    elif tool:
        details.append(f"工具 {tool}")

    if agent and not event.startswith("agent_run_"):
        details.append(f"Agent {agent}")
    if event.startswith("model_") and "request_index" in record:
        details.append(f"第 {record['request_index']} 轮")
    if "attempt" in record:
        details.append(f"第 {record['attempt']} 次尝试")
    if "duration_ms" in record:
        details.append(f"耗时 {record['duration_ms']:g}ms")
    for key, label in (("input_tokens", "输入"), ("output_tokens", "输出")):
        if key in record:
            details.append(f"{label} {record[key]} Token")
    for key, label, unit in (
        ("requests", "模型请求", "次"), ("tool_calls", "工具调用", "次"),
        ("byte_size", "大小", "字节"), ("spilled_bytes", "转存", "字节"),
        ("queue_depth", "队列", "项"),
    ):
        if key in record:
            details.append(f"{label} {record[key]} {unit}")
    for key, label in (("error_type", "错误"), ("error_code", "错误码"),
                       ("phase", "阶段"), ("reason", "原因"), ("finish_reason", "结束原因")):
        if key in record:
            details.append(f"{label} {record[key]}")
    # Status is valuable for generic operations, but repeats the named lifecycle
    # for tool/model/run messages. Keep its original value in the extra fields.
    if "status" in record and not event.startswith(("agent_tool_", "model_request_", "agent_run_")):
        details.append(f"状态 {record['status']}")
    return "，".join([message, *details])
