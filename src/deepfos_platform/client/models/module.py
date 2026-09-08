from typing import Optional

from .base import BaseModel

__all__ = [
    'ModuleInfo',
    'SpModuleRegisterDetailVo',
    'AppInfoBySpaceResp',
]


class ModuleInfo(BaseModel):
    moduleId: str
    moduleName: str
    moduleType: str
    moduleVersion: str
    serverName: str


class SpModuleRegisterDetailVo(BaseModel):
    """Sp Module Register Detail Vo

    .. admonition:: 引用接口

        - **GET** ``/api/space/module/detail`` (Response: 200)
        - **GET** ``/api/space/module/detail/list`` (Response: 200)
        - **GET** ``/api/space/module/register-list/usable`` (Response: 200)
        - **GET** ``/api/space/module/register-list/usable/coercion-lack`` (Response: 200)
    """
    #: 创建时间
    createTime: Optional[str]
    #: 创建人
    createUser: Optional[str]
    #: 主键
    id: Optional[int]
    #: 修改时间
    modifyTime: Optional[str]
    #: 修改人
    modifyUser: Optional[int]
    #: 组件id
    moduleId: Optional[str]
    #: 组件名称
    moduleName: Optional[str]
    #: 组件标签
    moduleTag: Optional[str]
    #: 组件类型
    moduleType: Optional[str]
    #: 组件版本
    moduleVersion: Optional[str]
    #: 注册时间
    registerTime: Optional[str]
    #: 服务名
    serverName: Optional[str]
    #: 空间id
    space: Optional[str]
    #: 状态
    status: Optional[int]
    #: 状态名
    statusName: Optional[str]


class AppInfoBySpaceResp(BaseModel):
    appName: Optional[str]
    appType: Optional[str]
    id: str
