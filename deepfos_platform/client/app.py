from typing import List, Optional, Union

from functools import cached_property

from ..conf.modulecfg import get_module_descriptor
from .models import (
    ElementInfo, ElementInfoResp, BaseElementInfo,
    ElementCreationReq, ElementCreationResp, ElementUpdateReq,
    ElementInfoWithId, ElementPermissionInfoResp,
    ModuleCreationInfo, ElementExistsResp, ElementImExportCallBack,
)
from ..options import OPTION
from .base import BaseAPI, post, get, ComponentAPI

__all__ = ['AppAPI']


# -----------------------------------------------------------------------------
# Typing
T_ELEINFO_OR_BASE = Union[ElementInfo, BaseElementInfo]
T_ELEINFO_MAYBE_LIST = Union[T_ELEINFO_OR_BASE, List[ElementInfo]]


class AppAPI(BaseAPI):
    def __init__(self, header=None):
        super().__init__(header, OPTION.server.app)
        self.header.update({"Content-Type": "application/json;charset=UTF8"})

    @cached_property
    def elements(self):
        return ElementsAPI(self)

    @cached_property
    def element_premission(self):
        return ElementPremissionAPI(self)

    @cached_property
    def create_element(self):
        return ElementCreateAPI(self)

    @cached_property
    def folders(self):
        return FoldersAPI(self)

    @cached_property
    def element_info(self):
        return ElementInfoAPI(self)


class ElementInfoAPI(ComponentAPI):
    endpoint = '/element-info'

    @post('import/callback')
    def import_callback(self, element: ElementImExportCallBack):
        return {'body': element.dict()}

    @post('business-data/export/callback')
    def export_callback(self, element: ElementImExportCallBack):
        return {'body': element.dict()}


class ElementPremissionAPI(ComponentAPI):
    endpoint = '/element'

    @get('permission/by-element-info', resp_model=List[ElementPermissionInfoResp], allow_none=True)
    def get_element_permission_info(self, ele_info: ElementInfoWithId):
        return {'param': ele_info.dict(exclude_none=True)}


class ElementCreateAPI(ComponentAPI):
    endpoint = 'create/element'

    @get('coercion/lack', resp_model=List[ModuleCreationInfo], allow_none=True)
    def get_create_elements(self):
        return {}


class ElementsAPI(ComponentAPI):
    endpoint = '/elements'

    @post('get-element-info-by-type', resp_model=List[ElementInfoResp], allow_none=True)
    def get_element_info(self, ele_info: T_ELEINFO_MAYBE_LIST):
        if not isinstance(ele_info, List):
            ele_info = [ele_info]
        return {'body': [ele.dict() for ele in ele_info]}

    @post('get-element-info-by-id', resp_model=List[ElementInfoResp], allow_none=True)
    def get_element_info_by_id(self, *ele_ids: str):
        return {'body': [ele for ele in ele_ids]}

    @post('get-element-info-by-type/filter', resp_model=List[ElementInfoResp], allow_none=True)
    def get_element_info_filter(self, ele_info: T_ELEINFO_MAYBE_LIST):
        if not isinstance(ele_info, List):
            ele_info = [ele_info]
        return {'body': [ele.dict() for ele in ele_info]}

    @staticmethod
    def _get_description(desc):
        if desc is None:
            return None

        new_desc = {
            k: v
            for k, v in desc.dict(by_alias=True).items() if v
        }

        if not new_desc:
            return None
        return new_desc

    @post('update-element')
    def update_element(self, ele_info: ElementUpdateReq):
        ele_info.elementNameDesAll = self._get_description(ele_info.elementNameDesAll)
        return {'body': ele_info}

    @post('create-element', resp_model=ElementCreationResp)
    def create_element(self, ele_info: ElementCreationReq):
        ele_info.elementNameDesAll = self._get_description(ele_info.elementNameDesAll)
        return {'body': ele_info}

    @get('check-element-exist')
    def check_element_exist(self, ele_info: T_ELEINFO_OR_BASE):
        param = dict(
            elementType=ele_info.elementType,
            elementName=ele_info.elementName,
        )
        if ele_info.folderId:
            param.update({"folderId": ele_info.folderId})
        else:
            param.update({"path": ele_info.path})

        return {'param': param}

    @post('check-element-exist-by-list', resp_model=List[ElementExistsResp])
    def batch_check_element_exist(self, ele_info: List[T_ELEINFO_OR_BASE]):
        return {'body': [ele.dict() for ele in ele_info]}

    @get('element-infos', resp_model=List[ElementInfoResp], allow_none=True)
    def element_infos(self, element_type: Optional[str] = None):
        if element_type is None:
            element_type = get_module_descriptor().moduleType
        return {'body': {"elementType": element_type}}

    @post('del-element', allow_none=True)
    def delete_element(self, ele_info: T_ELEINFO_MAYBE_LIST):
        if not isinstance(ele_info, List):
            ele_info = [ele_info]
        return {'body': [ele.dict() for ele in ele_info]}


class FoldersAPI(ComponentAPI):
    endpoint = '/folders'

    @get('get-folder-full')
    def get_folder_full(self, folder_id):
        return {'body': {"folderId": folder_id}}

    @get('get-folder-id/by-full-path')
    def get_folder_id(self, path):
        return {'body': {"fullPath": path}}
