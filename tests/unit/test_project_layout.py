"""Structural migration contracts without starting services or reading profiles."""
from __future__ import annotations

import ast
import configparser
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPO_ROOT / "src"


class ProjectLayoutTest(unittest.TestCase):
    def _safe_environment(self, *, source_path: bool = False) -> dict[str, str]:
        # Never inherit a developer's credentials, database, profile or import path.
        environment = {
            key: os.environ[key]
            for key in ("PATH", "SYSTEMROOT", "WINDIR")
            if key in os.environ
        }
        environment.update({
            "DEEP_AGENT_LOAD_DOTENV": "false",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
        })
        if source_path:
            environment["PYTHONPATH"] = str(SOURCE_ROOT)
        return environment

    def _python(self, *arguments: str, cwd: Path = REPO_ROOT,
                source_path: bool = False) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, "-B", *arguments],
            cwd=cwd,
            env=self._safe_environment(source_path=source_path),
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def _probe(self, source: str, *, cwd: Path = REPO_ROOT,
               source_path: bool = False) -> dict:
        return json.loads(self._python(
            "-c", textwrap.dedent(source), cwd=cwd, source_path=source_path,
        ).stdout)

    def test_legacy_paths_are_relative_links_to_one_physical_copy(self):
        links = {
            "deep_agent": "src/deep_agent",
            "deepfos_platform": "src/deepfos_platform",
            "maintenance": "src/maintenance",
            "deepagent_files.py": "src/deepagent_files.py",
            "frontend/console": "console",
            "frontend/vendor": "console/vendor",
            "mcps": "mcp_servers",
            "alembic": "migrations",
            "docker-compose.yml": "deploy/docker-compose.yml",
            "src/deep_agent/gunicorn.conf.py": "deploy/gunicorn.conf.py",
            "docs/guides/jenkins": "deploy/jenkins",
        }
        for legacy_name, canonical_name in links.items():
            with self.subTest(path=legacy_name):
                legacy = REPO_ROOT / legacy_name
                canonical = REPO_ROOT / canonical_name
                self.assertTrue(legacy.is_symlink(), f"{legacy_name} duplicates source")
                self.assertFalse(Path(os.readlink(legacy)).is_absolute())
                self.assertTrue(canonical.exists(), canonical_name)
                self.assertFalse(canonical.is_symlink(), canonical_name)
                self.assertTrue(legacy.samefile(canonical))

    def test_public_packages_resolve_to_src_from_both_supported_import_roots(self):
        probe = """
            import importlib
            import json
            from pathlib import Path
            names = ('deep_agent', 'deepfos_platform', 'maintenance', 'deepagent_files')
            print(json.dumps({
                name: str(Path(importlib.import_module(name).__file__).resolve())
                for name in names
            }))
        """
        expected = {
            name: str(SOURCE_ROOT / name / "__init__.py")
            for name in ("deep_agent", "deepfos_platform", "maintenance")
        }
        expected["deepagent_files"] = str(SOURCE_ROOT / "deepagent_files.py")
        # Existing commands still work at the repository root without PYTHONPATH.
        self.assertEqual(self._probe(probe), expected)
        # The new source root must also work from a directory without root links.
        with tempfile.TemporaryDirectory(
            prefix="TMP_to_delete-layout-import-", dir=REPO_ROOT,
        ) as directory:
            self.assertEqual(
                self._probe(probe, cwd=Path(directory), source_path=True), expected,
            )

    def test_runtime_paths_point_at_the_repository_and_canonical_resources(self):
        actual = self._probe("""
            import json
            from deep_agent.core import paths
            from deep_agent.capabilities.mcp.builtin_config import builtin_server_path
            from deep_agent.sandbox.venv import sandbox_setup_script
            from deep_agent.skills.catalog import BUILTIN_SKILLS_ROOT
            from maintenance.config_backup import repository_root
            names = ('REPO_ROOT', 'SOURCE_ROOT', 'CONSOLE_ROOT', 'MCP_SERVERS_ROOT',
                     'DEEP_AGENT_ROOT')
            result = {name: str(getattr(paths, name)) for name in names}
            result.update(
                maintenance_root=str(repository_root()),
                builtin_server=str(builtin_server_path('deepcube')),
                builtin_skills=str(BUILTIN_SKILLS_ROOT),
                sandbox_setup=str(sandbox_setup_script()),
            )
            print(json.dumps(result))
        """)
        self.assertEqual(actual, {
            "REPO_ROOT": str(REPO_ROOT),
            "SOURCE_ROOT": str(SOURCE_ROOT),
            "CONSOLE_ROOT": str(REPO_ROOT / "console"),
            "MCP_SERVERS_ROOT": str(REPO_ROOT / "mcp_servers"),
            "DEEP_AGENT_ROOT": str(REPO_ROOT),
            "maintenance_root": str(REPO_ROOT),
            "builtin_server": str(REPO_ROOT / "mcp_servers/deepcube/server.py"),
            "builtin_skills": str(SOURCE_ROOT / "deep_agent/builtin_skills"),
            "sandbox_setup": str(REPO_ROOT / "scripts/sandbox_venv/setup.sh"),
        })

    def test_resource_files_remain_accessible_through_legacy_paths(self):
        resources = {
            "frontend/console/index.html": "console/index.html",
            "frontend/vendor/marked.min.js": "console/vendor/marked.min.js",
            "frontend/vendor/mermaid.min.js": "console/vendor/mermaid.min.js",
            "mcps/deepcube/server.py": "mcp_servers/deepcube/server.py",
            "alembic/env.py": "migrations/env.py",
            "deep_agent/gunicorn.conf.py": "deploy/gunicorn.conf.py",
            "deep_agent/builtin_skills/deepagent-diagnostics/SKILL.md":
                "src/deep_agent/builtin_skills/deepagent-diagnostics/SKILL.md",
        }
        for legacy, canonical in resources.items():
            with self.subTest(resource=legacy):
                self.assertTrue((REPO_ROOT / canonical).is_file())
                self.assertTrue((REPO_ROOT / legacy).samefile(REPO_ROOT / canonical))

    def test_gateway_legacy_imports_share_canonical_modules_in_either_order(self):
        for legacy_first in (True, False):
            with self.subTest(legacy_first=legacy_first):
                actual = self._probe("""
                    import importlib
                    import json
                    from pathlib import Path
                    from unittest.mock import patch

                    names = ('config', 'deepagent_client', 'messages', 'runtime',
                             'sdk_logger', 'app')
                    result = {}
                    # Module loading may construct an ASGI object, but must never
                    # read the real profile, connect to a database or bind a port.
                    with (
                        patch('dotenv.load_dotenv', side_effect=AssertionError('profile read')),
                        patch('socket.socket.connect', side_effect=AssertionError('network access')),
                        patch('socket.socket.bind', side_effect=AssertionError('listener started')),
                    ):
                        for name in names:
                            legacy_name = 'deep_agent.channel_gateway.' + name
                            canonical_name = 'deep_agent.channels.gateway.' + name
                            order = (legacy_name, canonical_name) if LEGACY_FIRST else (
                                canonical_name, legacy_name)
                            first, second = (importlib.import_module(item) for item in order)
                            result[name] = {
                                'same_module': first is second,
                                'path': str(Path(second.__file__).resolve()),
                            }
                        old_entrypoint = importlib.import_module('deep_agent.channel_gateway.__main__')
                        new_entrypoint = importlib.import_module('deep_agent.channels.gateway.__main__')
                        result['same_main'] = old_entrypoint.main is new_entrypoint.main
                        old_app = importlib.import_module('deep_agent.channel_gateway.app')
                        new_app = importlib.import_module('deep_agent.channels.gateway.app')
                        result['same_app'] = old_app.app is new_app.app
                        result['lifespan_started'] = hasattr(new_app.app.state, 'gateway')
                    print(json.dumps(result))
                """.replace("LEGACY_FIRST", repr(legacy_first)))
                for name in ("config", "deepagent_client", "messages", "runtime", "sdk_logger", "app"):
                    self.assertEqual(actual[name], {
                        "same_module": True,
                        "path": str(SOURCE_ROOT / "deep_agent/channels/gateway" / f"{name}.py"),
                    })
                self.assertTrue(actual["same_main"])
                self.assertTrue(actual["same_app"])
                self.assertFalse(actual["lifespan_started"])

    def test_existing_command_help_works_without_loading_runtime_configuration(self):
        modules = (
            "deep_agent.local_dev",
            "deep_agent.combined_runtime",
            "deep_agent.channel_gateway",
            "deep_agent.channels.gateway",
            "maintenance.config_backup",
            "maintenance.rebaseline_database",
            "maintenance.session_history_backup",
            "maintenance.channel_origins",
            "maintenance.channel_peer_metadata",
        )
        for module in modules:
            with self.subTest(module=module):
                result = self._python("-m", module, "--help")
                self.assertIn("usage:", result.stdout.lower())
                self.assertIn("--help", result.stdout)

    def test_migration_directory_retains_the_same_two_revision_chain(self):
        revision_chain = {}
        for path in sorted((REPO_ROOT / "migrations/versions").glob("*.py")):
            values = {}
            for statement in ast.parse(path.read_text(encoding="utf-8")).body:
                if isinstance(statement, ast.Assign):
                    targets = statement.targets
                elif isinstance(statement, ast.AnnAssign):
                    targets = [statement.target]
                else:
                    continue
                for target in targets:
                    if isinstance(target, ast.Name) and target.id in {"revision", "down_revision"}:
                        values[target.id] = ast.literal_eval(statement.value)
            if "revision" in values:
                revision_chain[values["revision"]] = values["down_revision"]
                self.assertTrue((REPO_ROOT / "alembic/versions" / path.name).samefile(path))
        self.assertEqual(revision_chain, {"001": None, "002": "001"})
        configuration = configparser.ConfigParser(defaults={"here": str(REPO_ROOT)})
        configuration.read(REPO_ROOT / "alembic.ini", encoding="utf-8")
        self.assertEqual(
            Path(configuration.get("alembic", "script_location")).resolve(),
            REPO_ROOT / "migrations",
        )

    def test_sandbox_worker_pythonpath_contains_only_the_source_root(self):
        actual = self._probe("""
            import json
            import os
            from unittest.mock import patch
            from deep_agent.sandbox.subprocess_dev.env import build_worker_env
            with patch.dict(os.environ, {
                'PATH': '/usr/bin',
                'PYTHONPATH': '/host-private-modules' + os.pathsep + '/another-host-path',
                'PYTHONHOME': '/host-python',
                'DEEP_AGENT_TEST_SECRET': 'must-not-reach-worker',
            }, clear=True):
                environment = build_worker_env()
            print(json.dumps(environment))
        """)
        self.assertEqual(actual["PYTHONPATH"].split(os.pathsep), [str(SOURCE_ROOT)])
        self.assertNotIn("PYTHONHOME", actual)
        self.assertNotIn("DEEP_AGENT_TEST_SECRET", actual)
        self.assertEqual(actual["PYTHONNOUSERSITE"], "1")


if __name__ == "__main__":
    unittest.main()
