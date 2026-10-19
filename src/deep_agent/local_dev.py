from __future__ import annotations

import argparse
import os
import stat
from collections.abc import Awaitable, Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from deep_agent.local_security import (
    LOCAL_CONSOLE_ADMIN_SCOPE_KEY,
    LOCAL_CURRENT_USERNAME_SCOPE_KEY,
    enable_local_runtime,
)


DEFAULT_CONFIG_FILE = Path("~/.config/deep-agent/test.env").expanduser()
LOCAL_IDENTITY_KEYS = frozenset(
    {
        "DEEP_AGENT_LOCAL_USER",
        "DEEP_AGENT_LOCAL_USERNAME",
        "DEEP_AGENT_LOCAL_TOKEN",
        "DEEP_AGENT_LOCAL_TOKEN_COOKIE",
        "DEEP_AGENT_LOCAL_ADMIN_BYPASS",
    }
)
PROTECTED_HEADERS = frozenset(
    {
        b"user",
        b"authorization",
        b"cookie",
        b"token",
        b"x-user-token",
    }
)

ASGIApp = Callable[
    [MutableMapping[str, Any], Callable[..., Awaitable[Any]], Callable[..., Awaitable[Any]]],
    Awaitable[None],
]
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


def _read_only_diagnostic_post(path: str) -> bool:
    """Allow only the JSON aliases of existing administrator diagnostic reads."""
    prefix = "/api/v1/diagnostics/"
    if not path.startswith(prefix):
        return False
    parts = path[len(prefix):].split("/")
    if parts == ["logs"] or parts == ["sessions"] or parts == ["usage"]:
        return True
    if len(parts) == 2:
        return parts[0] == "sessions" or (parts[0] == "usage" and parts[1] in {"details", "sessions", "export"})
    if len(parts) == 3:
        return (parts[0] == "sessions" and parts[2] == "events" and bool(parts[1])) or (
            parts[0] == "channels" and bool(parts[1]) and parts[2] in {"overview", "peers", "sessions"})
    return False


@dataclass(frozen=True)
class LocalIdentity:
    user: str
    username: str | None = None
    token: str | None = field(default=None, repr=False)
    token_cookie: str | None = None
    console_admin_bypass: bool = False

    @classmethod
    def from_mapping(cls, values: Mapping[str, str | None]) -> "LocalIdentity":
        user = str(values.get("DEEP_AGENT_LOCAL_USER") or "").strip()
        username = str(values.get("DEEP_AGENT_LOCAL_USERNAME") or "").strip()
        token = str(values.get("DEEP_AGENT_LOCAL_TOKEN") or "").strip()
        raw_bypass = str(
            values.get("DEEP_AGENT_LOCAL_ADMIN_BYPASS") or ""
        ).strip().lower()
        if raw_bypass and raw_bypass not in _TRUE_VALUES | _FALSE_VALUES:
            raise ValueError(
                "DEEP_AGENT_LOCAL_ADMIN_BYPASS must be a boolean value"
            )
        console_admin_bypass = raw_bypass in _TRUE_VALUES
        missing = ["DEEP_AGENT_LOCAL_USER"] if not user else []
        if not token and not console_admin_bypass:
            missing.append("DEEP_AGENT_LOCAL_TOKEN")
        if missing:
            raise ValueError(f"Local development config is missing: {', '.join(missing)}")
        if token and token.lower().startswith("bearer "):
            token = token[7:].strip()
        if not token and not console_admin_bypass:
            raise ValueError("DEEP_AGENT_LOCAL_TOKEN must contain a token value")
        token_cookie = str(values.get("DEEP_AGENT_LOCAL_TOKEN_COOKIE") or "").strip() or None
        return cls(
            user=user,
            username=username or ("本地用户" if console_admin_bypass else None),
            token=token or None,
            token_cookie=token_cookie,
            console_admin_bypass=console_admin_bypass,
        )

    def request_headers(self) -> tuple[tuple[bytes, bytes], ...]:
        headers = [(b"user", self.user.encode("utf-8"))]
        if self.token:
            token = self.token.encode("latin-1")
            headers.extend(
                [
                    (b"authorization", b"Bearer " + token),
                    (b"x-user-token", token),
                ]
            )
        if self.token_cookie and self.token:
            headers.append(
                (
                    b"cookie",
                    self.token_cookie.encode("latin-1")
                    + b"="
                    + self.token.encode("latin-1"),
                )
            )
        return tuple(headers)


class LocalIdentityAdapter:
    """Apply a trusted local identity before requests enter the FastAPI app."""

    def __init__(
        self, app: ASGIApp, identity: LocalIdentity, *, read_only_preview: bool = False,
    ) -> None:
        self.app = app
        self.identity = identity
        self.read_only_preview = read_only_preview

    async def __call__(
        self,
        scope: MutableMapping[str, Any],
        receive: Callable[..., Awaitable[Any]],
        send: Callable[..., Awaitable[Any]],
    ) -> None:
        if scope.get("type") not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        if self.read_only_preview:
            if scope.get("type") == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            method = scope.get("method", "GET").upper()
            readonly_query = method == "POST" and _read_only_diagnostic_post(scope.get("path", ""))
            if method not in {"GET", "HEAD", "OPTIONS"} and not readonly_query:
                from starlette.responses import JSONResponse

                await JSONResponse(
                    {"detail": "当前为本地 Test 只读预览，不支持删除、修改或执行会话。"}, status_code=403,
                    headers={"X-Deep-Agent-Read-Only": "1"},
                )(scope, receive, send)
                return

        forwarded_scope = dict(scope)
        incoming_headers = scope.get("headers") or []
        headers = [
            (bytes(name).lower(), bytes(value))
            for name, value in incoming_headers
            if bytes(name).lower() not in PROTECTED_HEADERS
        ]
        headers.extend(self.identity.request_headers())
        forwarded_scope["headers"] = headers
        if self.identity.console_admin_bypass:
            forwarded_scope[LOCAL_CONSOLE_ADMIN_SCOPE_KEY] = True
        local_username = self.identity.username or (
            "本地用户" if self.identity.console_admin_bypass else None
        )
        if local_username:
            forwarded_scope[LOCAL_CURRENT_USERNAME_SCOPE_KEY] = local_username
        await self.app(forwarded_scope, receive, send)


def _repository_root() -> Path:
    from deep_agent.core.paths import REPO_ROOT

    return REPO_ROOT


def _validate_config_file(path: str | Path) -> Path:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Local development config does not exist: {resolved}")
    try:
        resolved.relative_to(_repository_root())
    except ValueError:
        pass
    else:
        raise ValueError("Local development config must be stored outside the repository")
    if os.name == "posix" and stat.S_IMODE(resolved.stat().st_mode) & 0o077:
        raise PermissionError(f"Local development config must use chmod 600: {resolved}")
    return resolved


def load_local_profile(path: str | Path) -> tuple[Path, dict[str, str]]:
    from dotenv import dotenv_values

    resolved = _validate_config_file(path)
    file_values = {
        key: value
        for key, value in dotenv_values(resolved).items()
        if value is not None
    }
    return resolved, file_values


def load_local_config(path: str | Path) -> LocalIdentity:
    _resolved, file_values = load_local_profile(path)
    identity = LocalIdentity.from_mapping(file_values)

    for key, value in file_values.items():
        if key not in LOCAL_IDENTITY_KEYS:
            # The external file is the complete local runtime profile. Its
            # values must win over an ambient shell or a previous dev command.
            os.environ[key] = value
    # Do not silently merge the profile with the repository's ignored .env.
    os.environ["DEEP_AGENT_LOAD_DOTENV"] = "false"
    # Some downstream SDK clients log request headers at INFO. Keep the local
    # identity quiet unless the developer explicitly opts into more logging.
    os.environ.setdefault("DEEP_AGENT_LOG_LEVEL", "WARNING")
    return identity


def build_local_app(
    config_file: str | Path, *, read_only_preview: bool = False,
) -> LocalIdentityAdapter:
    identity = load_local_config(config_file)
    enable_local_runtime()
    from deep_agent.main import app

    if read_only_preview:
        from deep_agent.local_preview import preview_lifespan
        from deep_agent.diagnostics.runtime import observe_lifespan

        app.router.lifespan_context = observe_lifespan(preview_lifespan, "api")
    return LocalIdentityAdapter(app, identity, read_only_preview=read_only_preview)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run DeepAgent locally with a private Test runtime profile.",
    )
    parser.add_argument(
        "--config",
        default=os.environ.get("DEEP_AGENT_LOCAL_CONFIG", str(DEFAULT_CONFIG_FILE)),
        help="External chmod-600 dotenv file containing runtime and identity settings.",
    )
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--read-only-preview", action="store_true",
        help="Read existing data only: no migrations, recovery, gateway, or write requests.",
    )
    parser.add_argument(
        "--no-sandbox-env",
        action="store_true",
        help=(
            "Do not auto-create the sandbox venv when subprocess-dev is enabled "
            "and the interpreter is missing (auto-setup is on by default)."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    from deep_agent.sandbox.venv import preflight_sandbox_interpreter

    args = _parser().parse_args(argv)
    local_app = build_local_app(args.config, read_only_preview=args.read_only_preview)
    if not args.read_only_preview:
        preflight_sandbox_interpreter(ensure=not args.no_sandbox_env)

    import uvicorn

    # This entry point is intentionally loopback-only and must never be used as
    # the container/production command.
    uvicorn.run(local_app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
