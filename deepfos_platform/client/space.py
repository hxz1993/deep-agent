from typing import List, Union, Sequence, Optional, Any

from .base import BaseAPI, ComponentAPI, get
from ..options import OPTION
from .models import ModuleInfo, SpModuleRegisterDetailVo, AppInfoBySpaceResp, UserInfoVo
from functools import cached_property

__all__ = ['SpaceAPI']

T_STR_MAYBE_LIST = Union[str, List[str]]


class SpaceAPI(BaseAPI):
    def __init__(self, header=None):
        super().__init__(header, OPTION.server.space)
        self.header.update({"Content-Type": "application/json;charset=UTF8"})

    @cached_property
    def module(self):
        return SpaceModuleAPI(self)

    @cached_property
    def app(self):
        return SpaceAppAPI(self)

    @cached_property
    def user(self):
        return SpaceUserAPI(self)

    @cached_property
    def user_group(self):
        return SpaceUserGroupAPI(self)


class SpaceModuleAPI(ComponentAPI):
    endpoint = '/api/space/module'

    @get('detail', resp_model=ModuleInfo, allow_none=True, raise_false=False)
    def get_module_detail(self, module_id):
        return {'param': {
            'moduleId': module_id,
        }}

    @get('register-list/usable', resp_model=List[SpModuleRegisterDetailVo])
    def get_usable_module(self, ):
        """
        查询已注册的可用组件列表（组）接口

        查询已注册的可用组件列表（组件状态为可用）
        """
        return {}


class SpaceAppAPI(ComponentAPI):
    endpoint = '/api/space/app'

    @get('query-app-info-list/by-space-id', resp_model=List[AppInfoBySpaceResp])
    def get_valid_app(self):
        return {}


class SpaceUserAPI(ComponentAPI):
    endpoint = '/api/space/user-info'

    @get('query/batch', resp_model=Optional[List[UserInfoVo]])
    def query_batch(self, user_ids: Sequence[str]):
        """
        根据用户id列表批量查询用户信息

        根据用户id列表批量查询用户的个人信息,可根据status字段筛选查询
        启用或者禁用的用户信息
        """
        user_ids = ','.join(user_ids)
        return {'param': {'userIdList': user_ids}}

    @get('check-user-exist/batch', resp_model=List[Any])
    def check_user_exist_batch(self, user_ids: List[str], status: str = None):
        """
        批量校验用户在当前空间是否存在

        批量校验一组用户在当前空间是否存在，存在返回其状态
        """
        return {'param': {'status': status, 'userIdList': user_ids}}


    @get('check-user/is-admin')
    def check_user_is_admin(self, user_id: str):
        return {'param': {'userId': user_id}}


class SpaceUserGroupAPI(ComponentAPI):
    endpoint = '/api/space/group-info'

    @get('query')
    def query(self, groupInfoId: str, status: str = None):
        """
        根据id查询单个用户组信息

        根据id查询单个用户组信息,以及该用户组下的所有用户的基本信息
        """
        return {'param': {'groupInfoId': groupInfoId, 'status': status}}
