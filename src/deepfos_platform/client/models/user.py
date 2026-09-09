from typing import Optional

from .base import BaseModel

__all__ = ['UserInfoVo',]


class UserInfoVo(BaseModel):
    #: 头像
    avatar: Optional[str]
    #: 邮箱
    email: Optional[str]
    #: 手机号
    mobilePhone: Optional[str]
    #: 昵称
    nickName: Optional[str]
    #: 是否为sso用户
    ssoUser: Optional[bool]
    #: 状态
    status: Optional[str]
    #: 用户id
    userId: Optional[str]
    #: 用户名
    userName: Optional[str]
