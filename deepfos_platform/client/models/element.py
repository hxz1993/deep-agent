from typing import List, Optional, Union

from ...lib.pydantic_v1 import Field, validator
from .base import BaseModel

__all__ = [
    "BaseElementInfo",
    "ElementInfo",
    "ElementWithServerInfo",
    "ElementInfoResp",
    "ElementUpdateReq",
    "ElementCreationReq",
    "ElementCreationResp",
    "ElementDescription",
    "ElementInfoWithId",
    "ElementPermissionInfoResp",
    "ModuleCreationInfo",
    "ElementExistsResp",
    "ElementImExportCallBack",
    "ElementInfoWithFilter",
]


# -----------------------------------------------------------------------------
# 元素基础要素
class CustomContents(BaseModel):
    customContentBlobs: Optional[List[str]]
    customContents: Optional[List[str]]


class CustomContentFilter(BaseModel):
    jsPath: CustomContents


class BaseElementInfo(BaseModel):
    elementName: str = Field(..., description='元素名/编码')
    elementType: str = Field(..., description='元素类型')
    folderId: str = Field(None, description='文件夹id')
    elementId: Union[str, int] = Field(None, description='元素id')


class ElementInfo(BaseElementInfo):
    path: str = Field(None, description='文件路径')


class ElementInfoWithId(ElementInfo):
    elementName: str = Field(None, description='元素名/编码')
    elementType: str = Field(None, description='元素类型')
    elementInfoId: str = Field(None, description='元素id 元素id存在时以元素id为准')


class ElementDescription(BaseModel):
    zh_cn: str = Field(None, alias='zh-cn', description='中文描述')
    en: str = Field(None, description='英文描述')

    class Config:
        allow_population_by_field_name = True


class ElementInfoWithFilter(ElementInfo):
    filterRelationContentJson: bool = True
    filterContentBlobKey: Optional[List[str]]
    filterContentKey: Optional[List[str]]


# -----------------------------------------------------------------------------
# 批量获取组件服务名
class ElementWithServerInfo(BaseElementInfo):
    enable: bool
    serverName: str = Field(..., description='服务名')


# -----------------------------------------------------------------------------
# 元素实例更新
class ElementUpdateReq(BaseElementInfo):
    customContents: dict = Field(None, description='自定义json')
    customContentBlobs: dict = Field(None, description='元素自定义数据内容-大数据 blob存储')
    moduleId: str = Field(..., description='模块id')
    elementNameDesAll: ElementDescription = Field(None, description='所有语种元素描述')
    customContentOpeFilterKey: CustomContentFilter = Field(None, description='在元素复制/导出时排除的自定义字段')
    openPath: str = Field(None, description='组件元素实例打开路径')
    pageServerName: str = Field(None, description='PAGE元素实例对应服务名')
    path: str = Field(None, description='文件路径')


# -----------------------------------------------------------------------------
# 元素信息查询 (返回结果)
class ElementInfoResp(ElementUpdateReq):
    # elementId: int = Field(..., description='元素/编码id')
    folderId: str = Field(..., description='文件夹id')
    moduleVersion: str = Field(None, description='模块版本')
    serverName: str = Field(..., description='服务名')
    elementNameDes: str = Field(None, description='当前语种元素描述')


# -----------------------------------------------------------------------------
# 元素实例创建
class ElementCreationReq(ElementUpdateReq):
    businessDataFlag: bool = Field(True, description='业务数据标记，true: 带业务数据，false: 不带业务数据')
    autoConfirm: bool = Field(None, description='自动确认元素')


class ElementCreationResp(BaseElementInfo):
    businessDataFlag: bool = Field(
        description='业务数据标记，true: 带业务数据，false: 不带业务数据'
    )
    confirm: bool = Field(description='确认标记')
    elError: bool = Field(description='错误标记')
    elWarning: bool = Field(description='警告标记')
    # id: int = Field(description='id')
    moduleId: str = Field(description='模块id')
    status: bool = Field(description='状态')


# -----------------------------------------------------------------------------
# 元素权限
class ElementPermissionInfoResp(ElementInfoWithId):
    exist: bool = Field(None, description='元素是否存在')
    permission: int = Field(None, description='元素权限 使用1/使用和编辑2/全部3')

    @validator('permission', always=True)
    def normalize_permission(cls, v):
        if v is None:
            return -1
        return v


class ModuleCreationInfo(BaseModel):
    moduleType: str = Field(None, description='组件类型')
    moduleTag: str = Field(None, description='组件标签')
    moduleVersion: str = Field(None, description='组件版本')
    moduleName: str = Field(None, description='组件名称')
    moduleId: str = Field(None, description='组件id')
    moduleElementId: str = Field(None, description='组件对应的元素id')


class ElementExistsResp(ElementInfo):
    existFlag: bool
    errMsg: Optional[str]


class ElementImExportCallBack(ElementInfo):
    message: str = Field(None, description='异常消息')
    opeId: str = Field(None, description='主记录id')
    status: bool = Field(True, description='组件处理是否成功')
