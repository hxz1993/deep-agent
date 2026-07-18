from __future__ import annotations

from fastapi import FastAPI, Request

TEST_USER_ID = "integration-test-user"


def install_test_user_header(app: FastAPI) -> None:
    """Add an authenticated user header to integration-test requests."""
    if getattr(app.state, "test_user_header_installed", False):
        return
    app.state.test_user_header_installed = True

    @app.middleware("http")
    async def add_test_user_header(request: Request, call_next):
        headers = request.scope["headers"]
        if not any(name.lower() == b"user" for name, _ in headers):
            headers.append((b"user", TEST_USER_ID.encode()))
        return await call_next(request)
