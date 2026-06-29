from functools import cached_property
from typing import Union

from aiohttp import FormData

from .base import BaseAPI, ContentType, ComponentAPI, get, post
from .models.platform_file import FileUplodRes
from ..options import OPTION

__all__ = ['PlatformAPI']

FILE_TYPE_ATTACHMENT = "ATT"


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

    @post('upload', resp_model=FileUplodRes)
    def upload(self, file_type: str, file_name: str, file: Union[str, bytes]):
        data = FormData(quote_fields=False)
        data.add_field('file', file, filename=file_name)
        return {
            'body': data,
            'param': {'fileType': file_type},
            'header': {'Content-Type': 'multipart/form-data', **data._writer.headers},
        }

    @get('download', content_type=ContentType.bytes, data_wrapped=False)
    def download(self, file_id: str):
        return {'param': {'fileId': file_id}}
