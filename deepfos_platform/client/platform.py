from functools import cached_property

from .base import BaseAPI, post, ComponentAPI
from ..options import OPTION

__all__ = ['PlatformAPI']


class PlatformAPI(BaseAPI):
    def __init__(self, header=None):
        super().__init__(header, OPTION.server.platform_file)
        self.header.update({"Content-Type": "application/json;charset=UTF8"})

    @cached_property
    def file(self):
        return File(self)


class File(ComponentAPI):
    endpoint = '/files'

    @post("upload/content")
    def upload_file_content(self, file_content: str, file_name: str):
        return {'body': {
            'body': file_content,
            'fileName': file_name,
            'fileType': 'LOG'
        }}
