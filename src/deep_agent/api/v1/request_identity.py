from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, status


def require_request_user(
    user: Annotated[
        str | None,
        Header(description="Authenticated DeepFOS platform user id."),
    ] = None,
) -> str:
    """Return the authenticated platform user forwarded by the gateway."""
    normalized = (user or "").strip()
    if not normalized:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authenticated user header",
        )
    return normalized


RequestUserDep = Annotated[str, Depends(require_request_user)]
