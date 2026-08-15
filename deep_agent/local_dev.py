from __future__ import annotations

import argparse
import os
import stat
from collections.abc import Awaitable, Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_FILE = Path("~/.config/deep-agent/test.env").expanduser()
LOCAL_IDENTITY_KEYS = frozenset(
    {
        "DEEP_AGENT_LOCAL_USER",
        "DEEP_AGENT_LOCAL_TOKEN",
        "DEEP_AGENT_LOCAL_TOKEN_COOKIE",
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


@dataclass(frozen=True)
class LocalIdentity:
    user: str
    token: str = field(repr=False)
    token_cookie: str | None = None

    @classmethod
    def from_mapping(cls, values: Mapping[str, str | None]) -> "LocalIdentity":
        user = str(values.get("DEEP_AGENT_LOCAL_USER") or "").strip()
        token = str(values.get("DEEP_AGENT_LOCAL_TOKEN") or "").strip()
        missing = [
            name
            for name, value in (
                ("DEEP_AGENT_LOCAL_USER", user),
                ("DEEP_AGENT_LOCAL_TOKEN", token),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"Local development config is missing: {', '.join(missing)}")
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        if not token:
            raise ValueError("DEEP_AGENT_LOCAL_TOKEN must contain a token value")
        token_cookie = str(values.get("DEEP_AGENT_LOCAL_TOKEN_COOKIE") or "").strip() or None
        return cls(user=user, token=token, token_cookie=token_cookie)

    def request_headers(self) -> tuple[tuple[bytes, bytes], ...]:
        token = self.token.encode("latin-1")
        headers = [
            (b"user", self.user.encode("utf-8")),
            (b"authorization", b"Bearer " + token),
            (b"x-user-token", token),
        ]
        if self.token_cookie:
            headers.append(
                (
                    b"cookie",
                    self.token_cookie.encode("latin-1") + b"=" + token,
                )
            )
        return tuple(headers)


class LocalIdentityAdapter:
    """Apply a trusted local identity before requests enter the FastAPI app."""

    def __init__(self, app: ASGIApp, identity: LocalIdentity) -> None:
        self.app = app
        self.identity = identity

    async def __call__(
        self,
        scope: MutableMapping[str, Any],
        receive: Callable[..., Awaitable[Any]],
        send: Callable[..., Awaitable[Any]],
    ) -> None:
        if scope.get("type") not in {"http", "websocket"}:
            await self.app(scope, receive, send)
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
        await self.app(forwarded_scope, receive, send)


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


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


def load_local_config(path: str | Path) -> LocalIdentity:
    from dotenv import dotenv_values

    resolved = _validate_config_file(path)
    file_values = {
        key: value
        for key, value in dotenv_values(resolved).items()
        if value is not None
    }
    identity = LocalIdentity.from_mapping(file_values)

    for key, value in file_values.items():
        if key not in LOCAL_IDENTITY_KEYS:
            os.environ.setdefault(key, value)
    # The external file is the complete local runtime profile. Do not silently
    # merge it with the repository's ignored .env file.
    os.environ["DEEP_AGENT_LOAD_DOTENV"] = "false"
    # Some downstream SDK clients log request headers at INFO. Keep the local
    # identity quiet unless the developer explicitly opts into more logging.
    os.environ.setdefault("DEEP_AGENT_LOG_LEVEL", "WARNING")
    return identity


def build_local_app(config_file: str | Path) -> LocalIdentityAdapter:
    identity = load_local_config(config_file)
    from deep_agent.main import app

    return LocalIdentityAdapter(app, identity)


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
    local_app = build_local_app(args.config)
    preflight_sandbox_interpreter(ensure=not args.no_sandbox_env)

    import uvicorn

    # This entry point is intentionally loopback-only and must never be used as
    # the container/production command.
    uvicorn.run(local_app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
