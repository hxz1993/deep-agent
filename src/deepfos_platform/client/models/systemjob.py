from typing import List, Optional

from ...lib.pydantic_v1 import Field
from .base import BaseModel

__all__ = [
    'JobReqInfo',
    'JobStatusReq',
    'JobContent',
    'TaskStatusReq',
]


class JobContent(BaseModel):
    #: 结束时间
    endTime: Optional[str]
    #: 作业明细名称英文
    jobContentNameEn: Optional[str]
    #: 作业明细名称中文
    jobContentNameZhcn: Optional[str]
    #: 作业明细名称繁体
    jobContentNameZhtw: Optional[str]
    #: 单个作业明细区分标识
    key: str
    #: 日志文本内容
    log: Optional[str]
    #: 开始时间
    startTime: Optional[str]
    #: 状态 WAIT：等待/GO：运行中/SUCCESS：成功/FAIL：失败/PART_FAIL：部分失败/STOP：暂停
    status: Optional[str]


class JobReqInfo(BaseModel):
    id: str
    user: str
    space: str
    app: str
    contentNum: Optional[int]
    endContentNum: Optional[int]
    jobNameZhcn: Optional[str]
    jobNameEn: Optional[str]
    jobNameXx: Optional[str]
    jobObjZhcn: Optional[str]
    jobObjEn: Optional[str]
    jobObjXx: Optional[str]
    readTime: Optional[str]
    startTime: Optional[str]
    endTime: Optional[str]
    log: Optional[str]
    logFile: Optional[str]
    status: str
    timeOut: Optional[str]
    callbackUrl: Optional[str]
    callbackPostBody: Optional[str]
    jobContents: Optional[List[JobContent]]


class JobStatusReq(BaseModel):
    jobId: str
    opt: str
    callbackPostBody: Optional[dict]


class TaskStatusReq(BaseModel):
    jobId: str
    taskCode: str
    operation: str
    customParams: Optional[dict]
    jobContents: Optional[List[dict]]
