"""组件 module 元数据：Pydantic 模型，由组件实例化并在 register 时传入。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..lib.model_base import BaseModel

__all__ = [
    "ProtocolItem",
    "OperateNoticeItem",
    "ModuleDescriptor",
    "set_module_descriptor",
    "get_module_descriptor",
]

_current: Optional["ModuleDescriptor"] = None


class ProtocolItem(BaseModel):
    mandatoryDependence: bool
    protocol: str
    protocolVersion: str


class OperateNoticeItem(BaseModel):
    operate: str
    syncOrAsyn: str
    url: str
    type: str


class ModuleDescriptor(BaseModel):
    """平台 install_module 所需的组件元数据。"""

    protocolList: List[ProtocolItem]
    useProtocolList: List[ProtocolItem]
    operateNoticeConfig: List[OperateNoticeItem]
    upgradeInfo: List[Any] = []
    autoUseProtocol: List[Any] = []
    moduleTag: str = "SYSTEM"
    moduleVersion: str
    moduleName: str
    moduleNames: Dict[str, str]
    moduleType: str
    description: str
    serverName: str
    openPath: str = "/"
    createPath: str = "/"
    editPath: str = "/"
    menuPath: str = "/"
    systemEnv: Optional[str] = None
    pageElementType: str = "PAGE"
    pageElementName: str
    pagePath: str
    pageDesc: Dict[str, str]
    pageOpenPath: str

    def to_register_dict(self, system_env: str = "") -> dict:
        """序列化为 install_module API 请求体；system_env 非空时覆盖 systemEnv。"""
        data = self.dict()
        if system_env:
            data["systemEnv"] = system_env
        return data


def set_module_descriptor(module: ModuleDescriptor) -> None:
    """设置当前进程使用的组件元数据（建议在 import deepfos_platform 之前调用）。"""
    global _current
    _current = module


def get_module_descriptor() -> ModuleDescriptor:
    """返回已设置的组件元数据。"""
    if _current is None:
        raise RuntimeError(
            "ModuleDescriptor 未设置。请在组件启动时调用 "
            "deepfos_platform.conf.modulecfg.set_module_descriptor(module)，"
            "或在 register_module / sync_register_module 中传入 module 参数。"
        )
    return _current
