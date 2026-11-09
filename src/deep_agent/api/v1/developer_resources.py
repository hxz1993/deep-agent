from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from starlette.responses import JSONResponse, Response

from deep_agent.api.v1.admin import CONSOLE_ADMIN_DEPENDENCIES
from deep_agent.developer_resources import ResourceUnavailableError, list_resources, read_resource

router = APIRouter(dependencies=CONSOLE_ADMIN_DEPENDENCIES)
tag = "Developer resources"
_UNAVAILABLE_HEADERS = {"Cache-Control": "no-store", "X-DeepAgent-Error": "developer_resources_unavailable"}


@router.get("/developer-resources", summary="列出开发助手资料")
def developer_resources() -> JSONResponse:
    try:
        items = list_resources()
    except ResourceUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc), headers=_UNAVAILABLE_HEADERS) from exc
    return JSONResponse({"items": items}, headers={"Cache-Control": "no-store"})


@router.get("/developer-resources/{code}/download", summary="下载开发助手资料")
def download_developer_resource(code: str) -> Response:
    try:
        resource = read_resource(code)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="开发资料不存在") from exc
    except ResourceUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc), headers=_UNAVAILABLE_HEADERS) from exc
    metadata = resource.metadata
    return Response(
        content=resource.content,
        media_type=metadata["media_type"],
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(metadata['filename'], safe='')}",
        },
    )
