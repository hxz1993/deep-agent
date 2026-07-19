from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from deep_agent.api.v1.request_context import router as request_context_router
from deep_agent.local_dev import LocalIdentity, LocalIdentityAdapter, load_local_config


class HeaderCaptureApp:
    def __init__(self) -> None:
        self.scope = None

    async def __call__(self, scope, receive, send) -> None:
        self.scope = scope


def header_map(scope) -> dict[str, str]:
    return {
        name.decode("latin-1"): value.decode("latin-1")
        for name, value in scope["headers"]
    }


class LocalIdentityAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_configured_identity_overrides_browser_credentials(self) -> None:
        target = HeaderCaptureApp()
        adapter = LocalIdentityAdapter(
            target,
            LocalIdentity(
                user="configured-user",
                token="configured-token",
                token_cookie="test-cookie",
            ),
        )
        scope = {
            "type": "http",
            "headers": [
                (b"user", b"spoofed-user"),
                (b"authorization", b"Bearer spoofed-token"),
                (b"x-user-token", b"spoofed-token"),
                (b"cookie", b"spoofed-cookie=value"),
            ],
        }

        async def unused(*args, **kwargs):
            return None

        await adapter(scope, unused, unused)
        headers = header_map(target.scope)

        self.assertEqual(headers["user"], "configured-user")
        self.assertEqual(headers["authorization"], "Bearer configured-token")
        self.assertEqual(headers["x-user-token"], "configured-token")
        self.assertEqual(headers["cookie"], "test-cookie=configured-token")

    async def test_debug_context_headers_are_preserved(self) -> None:
        target = HeaderCaptureApp()
        adapter = LocalIdentityAdapter(target, LocalIdentity(user="user", token="token"))
        scope = {
            "type": "http",
            "headers": [
                (b"app", b"app-from-console"),
                (b"space", b"space-from-console"),
                (b"language", b"zh-cn"),
            ],
        }

        async def unused(*args, **kwargs):
            return None

        await adapter(scope, unused, unused)
        headers = header_map(target.scope)

        self.assertEqual(headers["app"], "app-from-console")
        self.assertEqual(headers["space"], "space-from-console")
        self.assertEqual(headers["language"], "zh-cn")

    async def test_lifespan_scope_is_not_modified(self) -> None:
        target = HeaderCaptureApp()
        adapter = LocalIdentityAdapter(target, LocalIdentity(user="user", token="token"))
        scope = {"type": "lifespan"}

        async def unused(*args, **kwargs):
            return None

        await adapter(scope, unused, unused)
        self.assertIs(target.scope, scope)


class LocalIdentityRequestContextTests(unittest.TestCase):
    def test_request_context_uses_local_identity_and_console_context(self) -> None:
        app = FastAPI()
        app.include_router(request_context_router, prefix="/api/v1")
        local_app = LocalIdentityAdapter(
            app,
            LocalIdentity(user="configured-user", token="configured-token"),
        )

        with TestClient(local_app) as client:
            response = client.get(
                "/api/v1/request-context",
                headers={
                    "user": "spoofed-user",
                    "authorization": "Bearer spoofed-token",
                    "app": "app-from-console",
                    "space": "space-from-console",
                    "language": "zh-cn",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "authenticated": True,
                "user": "configured-user",
                "app": "app-from-console",
                "space": "space-from-console",
                "language": "zh-cn",
            },
        )


class LocalConfigTests(unittest.TestCase):
    def test_identity_repr_does_not_expose_token(self) -> None:
        identity = LocalIdentity(user="user", token="secret-token")
        self.assertNotIn("secret-token", repr(identity))

    def test_bearer_prefix_is_normalized(self) -> None:
        identity = LocalIdentity.from_mapping(
            {
                "DEEP_AGENT_LOCAL_USER": "user",
                "DEEP_AGENT_LOCAL_TOKEN": "Bearer token-value",
            }
        )
        self.assertEqual(identity.token, "token-value")

    def test_user_and_token_are_required(self) -> None:
        with self.assertRaisesRegex(ValueError, "DEEP_AGENT_LOCAL_TOKEN"):
            LocalIdentity.from_mapping({"DEEP_AGENT_LOCAL_USER": "user"})

    def test_external_file_loads_runtime_but_not_identity_into_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local.env"
            path.write_text(
                "\n".join(
                    [
                        "DEEP_AGENT_LOCAL_USER=file-user",
                        "DEEP_AGENT_LOCAL_TOKEN=file-token",
                        "DEEP_AGENT_DBMS_HOSTNAME=test-db",
                    ]
                ),
                encoding="utf-8",
            )
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
            keys = [
                "DEEP_AGENT_LOCAL_USER",
                "DEEP_AGENT_LOCAL_TOKEN",
                "DEEP_AGENT_DBMS_HOSTNAME",
                "DEEP_AGENT_LOAD_DOTENV",
                "DEEP_AGENT_LOG_LEVEL",
            ]
            previous = {key: os.environ.get(key) for key in keys}
            try:
                for key in keys:
                    os.environ.pop(key, None)
                identity = load_local_config(path)
                self.assertEqual(identity.user, "file-user")
                self.assertEqual(identity.token, "file-token")
                self.assertEqual(os.environ["DEEP_AGENT_DBMS_HOSTNAME"], "test-db")
                self.assertEqual(os.environ["DEEP_AGENT_LOAD_DOTENV"], "false")
                self.assertEqual(os.environ["DEEP_AGENT_LOG_LEVEL"], "WARNING")
                self.assertNotIn("DEEP_AGENT_LOCAL_USER", os.environ)
                self.assertNotIn("DEEP_AGENT_LOCAL_TOKEN", os.environ)
            finally:
                for key in keys:
                    if previous[key] is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = previous[key]

    def test_repository_config_file_is_rejected(self) -> None:
        repository_file = Path(__file__).resolve().parents[2] / ".local-dev-test.env"
        with patch.object(Path, "is_file", return_value=True), patch.object(
            Path,
            "stat",
            return_value=os.stat_result((0o100600, 0, 0, 0, 0, 0, 0, 0, 0, 0)),
        ):
            with self.assertRaisesRegex(ValueError, "outside the repository"):
                load_local_config(repository_file)

    def test_group_or_world_readable_config_is_rejected(self) -> None:
        if os.name != "posix":
            self.skipTest("POSIX file modes are required")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local.env"
            path.write_text(
                "DEEP_AGENT_LOCAL_USER=user\nDEEP_AGENT_LOCAL_TOKEN=token\n",
                encoding="utf-8",
            )
            path.chmod(0o644)
            with self.assertRaises(PermissionError):
                load_local_config(path)


if __name__ == "__main__":
    unittest.main()
