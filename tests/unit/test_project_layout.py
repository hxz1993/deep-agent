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
    def _safe_environment(self) -> dict[str, str]:
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
            "PYTHONPATH": str(SOURCE_ROOT),
        })
        return environment

    def _python(self, *arguments: str, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, "-B", *arguments],
            cwd=cwd,
            env=self._safe_environment(),
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def _probe(self, source: str, *, cwd: Path = REPO_ROOT) -> dict:
        return json.loads(self._python(
            "-c", textwrap.dedent(source), cwd=cwd,
        ).stdout)

    def test_legacy_paths_have_been_removed(self):
        legacy_paths = (
            "deep_agent", "deepfos_platform", "maintenance", "deepagent_files.py",
            "frontend", "mcps", "alembic", "docker-compose.yml",
            "src/deep_agent/gunicorn.conf.py", "src/deep_agent/channel_gateway",
            "docs/guides/jenkins",
            "src/deep_agent/code_mode", "src/deep_agent/web_tools",
            "src/deep_agent/capabilities/web", "src/deep_agent/capabilities/skills",
            "src/deep_agent/capabilities/attachments", "src/deep_agent/capabilities/cursor_skills",
            "src/deep_agent/db/stores/message", "src/deep_agent/db/stores/auxiliary_model",
            "src/deep_agent/db/stores/output_contract", "src/deep_agent/db/stores/context_checkpoint",
            "src/deep_agent/lib", "src/deep_agent/capabilities/native",
            "src/deep_agent/agent/models", "src/deep_agent/agent/provider_registry.py",
            "src/deep_agent/channels/gateway/messages.py",
            "src/deep_agent/channels/gateway/sdk_logger.py", "console/prototypes",
            "tests/integration/helpers/models.py", "scripts/__init__.py",
            "src/deep_agent/builtin_agent_packages",
        )
        for legacy_name in legacy_paths:
            with self.subTest(path=legacy_name):
                legacy = REPO_ROOT / legacy_name
                self.assertFalse(legacy.exists(), f"legacy entry remains: {legacy_name}")
                self.assertFalse(legacy.is_symlink(), f"broken legacy link remains: {legacy_name}")

    def test_canonical_directories_are_physical_sources(self):
        for name in (
            "src/deep_agent", "src/deepfos_platform", "src/maintenance",
            "console", "console/vendor", "mcp_servers", "migrations", "deploy",
            "src/deep_agent/channels/gateway", "deploy/jenkins",
            "src/deep_agent/agent_packages/builtin",
            "src/deep_agent/ptc", "src/deep_agent/web",
            "src/deep_agent/models", "src/deep_agent/capabilities/general",
        ):
            with self.subTest(path=name):
                path = REPO_ROOT / name
                self.assertTrue(path.is_dir())
                self.assertFalse(path.is_symlink())

    def test_ptc_public_module_uses_the_canonical_path_without_a_legacy_alias(self):
        actual = self._probe("""
            import importlib.util
            import json
            from pathlib import Path
            import deep_agent.ptc as ptc
            print(json.dumps({
                'path': str(Path(ptc.__file__).resolve()),
                'implementation': ptc.FilePayloadResolvingToolset.__module__,
                'legacy_available': importlib.util.find_spec('deep_agent.code_mode') is not None,
            }))
        """)
        self.assertEqual(actual, {
            "path": str(SOURCE_ROOT / "deep_agent/ptc/__init__.py"),
            "implementation": "deep_agent.ptc.file_payload",
            "legacy_available": False,
        })

    def test_public_packages_resolve_to_src_with_the_same_environment_from_any_cwd(self):
        probe = """
            import importlib
            import json
            import sys
            from pathlib import Path
            names = ('deep_agent', 'deepfos_platform', 'maintenance', 'deepagent_files')
            original_search_path = list(sys.path)
            modules = {
                name: str(Path(importlib.import_module(name).__file__).resolve())
                for name in names
            }
            assert sys.path == original_search_path, 'public packages changed the import path'
            print(json.dumps(modules))
        """
        expected = {
            name: str(SOURCE_ROOT / name / "__init__.py")
            for name in ("deep_agent", "deepfos_platform", "maintenance")
        }
        expected["deepagent_files"] = str(SOURCE_ROOT / "deepagent_files.py")
        # Local commands and deployed commands both load the explicit source root.
        self.assertEqual(self._probe(probe), expected)
        with tempfile.TemporaryDirectory(
            prefix="TMP_to_delete-layout-import-", dir=REPO_ROOT,
        ) as directory:
            self.assertEqual(
                self._probe(probe, cwd=Path(directory)), expected,
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

    def test_resource_files_exist_at_canonical_paths(self):
        for name in (
            "console/index.html", "console/vendor/marked.min.js",
            "console/vendor/mermaid.min.js", "mcp_servers/deepcube/server.py",
            "migrations/env.py", "deploy/gunicorn.conf.py", "deploy/docker-compose.yml",
            "src/deepagent_files.py",
            "tests/helpers/models.py",
            "src/deep_agent/builtin_skills/deepagent-diagnostics/SKILL.md",
        ):
            with self.subTest(resource=name):
                path = REPO_ROOT / name
                self.assertTrue(path.is_file())
                self.assertFalse(path.is_symlink())

    def test_console_prototypes_are_kept_outside_the_production_static_root(self):
        self.assertFalse(any((REPO_ROOT / "console").rglob("*Prototype*")))

    def test_gateway_imports_only_the_canonical_modules_without_starting_lifespan(self):
        actual = self._probe("""
            import importlib
            import importlib.util
            import json
            from pathlib import Path
            from unittest.mock import patch

            names = ('config', 'deepagent_client', 'runtime', 'app', '__main__')
            result = {}
            with (
                patch('dotenv.load_dotenv', side_effect=AssertionError('profile read')),
                patch('socket.socket.connect', side_effect=AssertionError('network access')),
                patch('socket.socket.bind', side_effect=AssertionError('listener started')),
            ):
                for name in names:
                    module = importlib.import_module('deep_agent.channels.gateway.' + name)
                    result[name] = str(Path(module.__file__).resolve())
                result['legacy_available'] = importlib.util.find_spec('deep_agent.channel_gateway') is not None
                app_module = importlib.import_module('deep_agent.channels.gateway.app')
                result['lifespan_started'] = hasattr(app_module.app.state, 'gateway')
            print(json.dumps(result))
        """)
        for name in ("config", "deepagent_client", "runtime", "app", "__main__"):
            self.assertEqual(actual[name], str(SOURCE_ROOT / "deep_agent/channels/gateway" / f"{name}.py"))
        self.assertFalse(actual["legacy_available"])
        self.assertFalse(actual["lifespan_started"])

    def test_shared_model_and_general_tool_modules_have_explicit_owners(self):
        actual = self._probe("""
            import json
            from deep_agent.models import OpenAICompatibleVideoChatModel
            from deep_agent.models.provider_registry import ProviderRuntimeConfig
            from deep_agent.models.models_dev import ModelsDevBundle
            from deep_agent.capabilities.general import build_native_tool_package
            print(json.dumps({value.__name__: value.__module__ for value in (
                OpenAICompatibleVideoChatModel, ProviderRuntimeConfig,
                ModelsDevBundle, build_native_tool_package,
            )}))
        """)
        self.assertEqual(actual, {
            "OpenAICompatibleVideoChatModel": "deep_agent.models.openai_compatible_video",
            "ProviderRuntimeConfig": "deep_agent.models.provider_registry",
            "ModelsDevBundle": "deep_agent.models.models_dev",
            "build_native_tool_package": "deep_agent.capabilities.general.builtin",
        })

    def test_mcp_subprocess_constants_do_not_load_runtime_or_configuration(self):
        actual = self._probe("""
            import json
            import sys
            from deep_agent.core.mcp_constants import (
                MCP_REGISTRY_JSON_ENV, MCP_SERVER_TTL_ENV, REQUEST_HEADERS_ENV,
            )
            assert 'deep_agent.options' not in sys.modules
            assert 'deep_agent.capabilities' not in sys.modules
            print(json.dumps([MCP_REGISTRY_JSON_ENV, MCP_SERVER_TTL_ENV, REQUEST_HEADERS_ENV]))
        """)
        self.assertEqual(actual, [
            "DEEP_AGENT_MCP_REGISTRY_JSON", "DEEP_AGENT_MCP_SERVER_TTL",
            "DEEP_AGENT_MCP_REQUEST_HEADERS",
        ])

    def test_canonical_command_help_works_without_loading_runtime_configuration(self):
        modules = (
            "deep_agent.local_dev",
            "deep_agent.combined_runtime",
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

    def test_migration_directory_retains_the_published_revision_chain(self):
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
        self.assertEqual(revision_chain, {"001": None, "002": "001", "003": "002", "004": "003", "005": "004", "006": "005", "007": "006", "008": "007", "009": "008", "010": "009", "011": "010", "012": "011", "013": "012"})
        configuration = configparser.ConfigParser(defaults={"here": str(REPO_ROOT)})
        configuration.read(REPO_ROOT / "alembic.ini", encoding="utf-8")
        self.assertEqual(
            Path(configuration.get("alembic", "script_location")).resolve(),
            REPO_ROOT / "migrations",
        )
        self.assertEqual(
            Path(configuration.get("alembic", "prepend_sys_path")).resolve(),
            SOURCE_ROOT,
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
