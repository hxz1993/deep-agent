from __future__ import annotations

from fastapi import FastAPI

from deep_agent.api.v1.admin import require_console_admin
from deep_agent.security import ConsoleAdminAccess, ENTERPRISE_ADMIN_ROLE


def allow_console_admin(app: FastAPI) -> None:
    """Keep non-auth API tests focused on their original management contract."""

    def allowed() -> ConsoleAdminAccess:
        return ConsoleAdminAccess(
            allowed=True,
            roles=(ENTERPRISE_ADMIN_ROLE,),
        )

    app.dependency_overrides[require_console_admin] = allowed


__all__ = ["allow_console_admin"]
