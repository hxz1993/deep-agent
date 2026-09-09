"""Pydantic v1 API 统一入口；安装 Pydantic 2 时通过 pydantic.v1 子模块兼容。"""
import pydantic as _pydantic_pkg

PYDANTIC_V2_INSTALLED = int(_pydantic_pkg.__version__.split(".")[0]) >= 2

try:
    from pydantic.v1 import (  # type: ignore[no-redef]
        BaseModel,
        Field,
        validator,
        ValidationError,
        parse_obj_as,
        parse_raw_as,
        PrivateAttr,
        create_model,
    )
except (ImportError, AttributeError):
    from pydantic import (
        BaseModel,
        Field,
        validator,
        ValidationError,
        parse_obj_as,
        parse_raw_as,
        PrivateAttr,
        create_model,
    )

__all__ = [
    "PYDANTIC_V2_INSTALLED",
    "BaseModel",
    "Field",
    "validator",
    "ValidationError",
    "parse_obj_as",
    "parse_raw_as",
    "PrivateAttr",
    "create_model",
]
