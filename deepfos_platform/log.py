import logging
import os
import sys
from typing import Any, Dict, Optional

from loguru import logger

from .lib.context import get_request_id

__all__ = ["setup", "InterceptHandler", "logger_config"]


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
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def logger_config(log_level: Optional[str] = None) -> Dict[str, Any]:
    level = (log_level or os.getenv("LOG_LEVEL", "INFO")).upper()
    file_path = os.getenv("DP_LOG_FILE_PATH")
    if file_path:
        additional_params: Dict[str, Any] = {"sink": file_path}
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
        }],
        "patcher": lambda record: record["extra"].update({"request-id": get_request_id()}),
    }


def setup(level="INFO"):
    level = level.upper()
    logger.remove()
    logging.root.handlers = [InterceptHandler(frame_back=0)]
    for name in list(logging.root.manager.loggerDict.keys()):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True
    config = logger_config(log_level=level)
    logger.configure(**config)
