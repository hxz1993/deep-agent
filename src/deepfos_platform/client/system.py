from typing import List, Union

from .base import BaseAPI, ComponentAPI, post
from .models.systemjob import JobReqInfo
from ..options import OPTION
from functools import cached_property

__all__ = ['SystemAPI', 'JobAPI']

T_STR_MAYBE_LIST = Union[str, List[str]]


class SystemAPI(BaseAPI):
    def __init__(self, header=None):
        super().__init__(header, OPTION.server.system)
        self.header.update({"Content-Type": "application/json;charset=UTF8"})

    @cached_property
    def module(self):
        return ModuleAPI(self)

    @cached_property
    def job(self):
        return JobAPI(self)

    @cached_property
    def mail(self):
        return MailAPI(self)


class ModuleAPI(ComponentAPI):
    endpoint = '/module'

    @post('install-module')
    def install_module(self, module_cfg: dict):
        return {'body': module_cfg}


class JobAPI(ComponentAPI):
    endpoint = '/'

    @post('job-create')
    def create_job(self, job_create: JobReqInfo):
        return {'body': job_create}

    @post('job-update')
    def update_job(self, job_update: JobReqInfo):
        return {'body': job_update}


class MailAPI(ComponentAPI):
    endpoint = '/s/email'

    @post('send-single')
    def send_single(self, content: str, subject: str, receivers: str):
        return {'body': dict(content=content, subject=subject, toEmail=receivers)}
