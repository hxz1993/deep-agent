"""Keep third-party SDK exceptions, message bodies and credentials out of logs."""
import logging


class SafeSdkFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # SDK errors can embed full API responses or WebSocket URLs. Our adapter
        # emits actionable state separately; retain only SDK severity here.
        record.msg = "Feishu SDK reported an internal event"
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


def configure_sdk_logging() -> None:
    sdk_logger = logging.getLogger("Lark")
    if not any(isinstance(item, SafeSdkFilter) for item in sdk_logger.filters):
        sdk_logger.addFilter(SafeSdkFilter())
