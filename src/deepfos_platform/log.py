import logging
import re
import sys
from collections import deque
from types import TracebackType
from typing import Any, Dict, Optional

from loguru import logger

from .lib.context import get_request_id

__all__ = ["setup", "InterceptHandler", "logger_config", "safe_logger",
           "format_exception_stack", "is_exception_stack"]


_STACK_TOKEN = re.compile(r"[A-Za-z0-9_.<>-]{1,96}\Z")
_STACK_FRAME = r"[A-Za-z0-9_.<>-]{1,96}:[0-9]{1,9}:[A-Za-z0-9_.<>-]{1,96}"
_STACK_ENTRY = rf"[A-Za-z0-9_.<>-]{{1,96}}(?:@{_STACK_FRAME}(?:>{_STACK_FRAME}){{0,7}})?"
_STACK = re.compile(rf"{_STACK_ENTRY}(?:;{_STACK_ENTRY}){{0,2}}\Z")
_MAX_STACK_LENGTH = 2048


def is_exception_stack(value: object) -> bool:
    return type(value) is str and len(value) <= _MAX_STACK_LENGTH and _STACK.fullmatch(value) is not None


def _stack_token(value: str) -> str:
    return value if _STACK_TOKEN.fullmatch(value) else "unknown"


def format_exception_stack(error: BaseException, traceback: TracebackType | None = None) -> str:
    """Summarize exception types and frame locations without reading source or values."""
    entries = []
    seen = set()
    pending = [error]
    while pending and len(entries) < 3:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        frames = deque(maxlen=8 if not entries else 4)
        frame = traceback if not entries and traceback is not None else current.__traceback__
        while frame is not None:
            code = frame.tb_frame.f_code
            filename = code.co_filename.replace("\\", "/").rsplit("/", 1)[-1]
            frames.append(f"{_stack_token(filename)}:{frame.tb_lineno}:{_stack_token(code.co_name)}")
            frame = frame.tb_next
        name = _stack_token(type(current).__name__)
        entry = f"{name}@{'>'.join(frames)}" if frames else name
        if sum(map(len, entries)) + len(entries) + len(entry) > _MAX_STACK_LENGTH:
            break
        entries.append(entry)
        if isinstance(current, BaseExceptionGroup):
            pending.extend(reversed(current.exceptions[:3]))
        cause = current.__cause__
        cause = cause if cause is not None else current.__context__
        if cause is not None:
            pending.append(cause)
    return ";".join(entries)


def _redact_exception(record):
    exception = record["exception"]
    if exception is None:
        return
    record["exception"] = None
    if exception.value is not None:
        stack = format_exception_stack(exception.value, exception.traceback)
        record["message"] += f" | error_stack={stack}"


safe_logger = logger.patch(_redact_exception)


def _patch_record(record):
    record["extra"]["request-id"] = get_request_id()
    _redact_exception(record)


class InterceptHandler(logging.Handler):
    def __init__(self, level=logging.NOTSET, frame_back=0):
        super().__init__(level=level)
        self._frame_back = frame_back

    def emit(self, record):
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame, depth = sys._getframe(1), 1
        while frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        depth += self._frame_back
        safe_logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def logger_config(
    log_level: Optional[str] = None,
    file_path: Optional[str] = None,
) -> Dict[str, Any]:
    # 延迟导入，避免与 options.py（reload_opt=setup）循环依赖。
    # 文件路径和默认等级只认 OPTION.log；产品前缀由调用方 set_env_prefix。
    from .options import OPTION

    level = (log_level or OPTION.log.level or "INFO").upper()
    path = OPTION.log.file if file_path is None else file_path
    if path:
        additional_params: Dict[str, Any] = {"sink": path}
    else:
        additional_params = {"sink": sys.stdout}
    return {
        "handlers": [{
            **additional_params,
            "level": level,
            "format": (
                "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
                "<yellow>{process}</yellow> | "
                "<level>{level: <8}</level> | "
                "<red>{extra[request-id]}</red> | "
                "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
                "<level>{message}</level>"
            ),
            "colorize": level == "DEBUG",
            "diagnose": False,
            "backtrace": False,
        }],
        "patcher": _patch_record,
    }


def setup(level="INFO"):
    from .options import OPTION

    level = (level or OPTION.log.level or "INFO").upper()
    logger.remove()
    logging.root.handlers = [InterceptHandler(frame_back=0)]
    for name in list(logging.root.manager.loggerDict.keys()):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True
    config = logger_config(log_level=level, file_path=OPTION.log.file)
    logger.configure(**config)
