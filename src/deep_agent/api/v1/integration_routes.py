"""Integration configuration responses must never be cached by clients."""
from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute


class IntegrationRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request):
            try:
                response = await original(request)
            except HTTPException as exc:
                exc.headers = {**(exc.headers or {}), "Cache-Control": "no-store"}
                raise
            except RequestValidationError as exc:
                # Avoid returning rejected configuration values in validation
                # payloads. The editor needs the reason, not a copy of its data.
                messages = [error["msg"] for error in exc.errors()[:3]]
                response = JSONResponse({"detail": {
                    "code": "integration_validation_error",
                    "message": "集成配置无效：" + "；".join(messages),
                }}, status_code=422)
            response.headers["Cache-Control"] = "no-store"
            return response

        return handle
