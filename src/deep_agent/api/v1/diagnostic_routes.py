"""Response privacy shared by administrator diagnostic read routes."""
from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute


class NoStoreRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request):
            try:
                response = await original(request)
            except HTTPException as exc:
                exc.headers = {**(exc.headers or {}), "Cache-Control": "no-store"}
                raise
            except RequestValidationError:
                # Do not echo invalid query input into diagnostic error payloads.
                response = JSONResponse(
                    {"detail": "诊断查询参数无效，请检查时间、筛选或分页"}, status_code=422,
                )
            response.headers["Cache-Control"] = "no-store"
            return response
        return handle
