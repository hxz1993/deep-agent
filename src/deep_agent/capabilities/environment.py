"""Unconditionally available environment tools for business Agent runs."""
from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic_ai import FunctionToolset, ModelRetry


def build_environment_toolset() -> FunctionToolset:
    toolset = FunctionToolset(
        instructions="需要确认实际当前时间时调用 get_current_time；默认 UTC，可指定 Asia/Shanghai 等 IANA 时区，不要将服务器或语言设置当作用户时区。"
    )

    @toolset.tool_plain
    def get_current_time(timezone: str = "UTC") -> str:
        """获取当前时间，返回包含 UTC 偏移的 ISO 8601 字符串。

        Args:
            timezone: IANA 时区名称，默认 UTC，例如 Asia/Shanghai 或 America/New_York。
        """
        zone_name = timezone.strip()
        try:
            if not zone_name:
                raise ValueError("empty timezone")
            zone = UTC if zone_name == "UTC" else ZoneInfo(zone_name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ModelRetry(
                "无法加载指定时区。请使用有效的 IANA 时区名称（如 UTC、Asia/Shanghai），"
                "并确认服务器已安装对应时区数据；不会回退到服务器时区。"
            ) from exc
        return datetime.now(zone).isoformat()

    return toolset
