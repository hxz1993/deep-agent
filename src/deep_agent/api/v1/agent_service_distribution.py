"""Unreleased CLI/SDK distribution draft. It may be redesigned or removed."""
from __future__ import annotations

import os
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from deep_agent.agent_service.security import public_base_url
from deep_agent.api.v1.agent_service import admin

# The API collector only mounts a module's `router`. Keep the draft opt-in.
draft_router = APIRouter(prefix="/agent-service")
PREFIX = "/api/v1/agent-service"


def distribution():
    version = os.getenv("DEEP_AGENT_AGENT_SERVICE_CLIENT_VERSION", "")
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)+(?:a[0-9]+|b[0-9]+|rc[0-9]+)?", version):
        return None, None, None
    filename = f"deepfos_agent_client-{version}-py3-none-any.whl"
    local = os.getenv("DEEP_AGENT_AGENT_SERVICE_CLIENT_LOCAL_WHEEL", "")
    wheel = Path(local) if local and Path(local).name == filename and Path(local).is_file() else None
    url = os.getenv("DEEP_AGENT_AGENT_SERVICE_CLIENT_WHEEL_URL", "")
    if url:
        try:
            url = public_base_url(url)
        except ValueError:
            return version, None, None
        return version, url if url.endswith(f"/{filename}") else None, None
    return version, f"{PREFIX}/client-packages/{filename}" if wheel else None, wheel


@draft_router.get("/client-config", dependencies=admin)
async def client_config():
    try:
        base = public_base_url(os.getenv("DEEP_AGENT_AGENT_SERVICE_PUBLIC_BASE_URL", ""))
    except ValueError:
        base = None
    version, url, local_wheel = distribution()
    if local_wheel:
        url = f"{base}{url}" if base else None
    return {
        "service_url": base,
        "mcp_url": f"{base}{PREFIX}/mcp" if base else None,
        "wheel_version": version,
        "wheel_url": url,
        "python_min": "3.10",
    }


@draft_router.get("/client-packages/{filename}", include_in_schema=False)
async def download_client(filename: str):
    _, _, wheel = distribution()
    if not wheel or filename != wheel.name:
        raise HTTPException(404, "客户端制品不存在")
    return FileResponse(wheel, media_type="application/octet-stream", filename=wheel.name,
                        headers={"X-Content-Type-Options": "nosniff"})
