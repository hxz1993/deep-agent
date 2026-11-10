from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
import unicodedata
import zipfile
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfReader

from deep_agent import __version__
from deep_agent.developer_resources import (
    GENERATED_ROOT, PDF_PATH, ROOT, ResourceUnavailableError, list_resources, read_resource,
)
from deep_agent.skills.package import parse_skill_zip
from scripts.build_developer_resources import SKILL_CODE, build_outputs, source_documents


class DeveloperResourcePackageTests(unittest.TestCase):
    def test_deterministic_archive_matches_shipped_resources_and_catalog(self):
        first = build_outputs()
        self.assertEqual(first, build_outputs())
        for path, content in first.items():
            with self.subTest(file=path.name):
                self.assertEqual(path.read_bytes(), content, "Run scripts/build_developer_resources.py --write")
        items = list_resources()
        self.assertEqual([item["code"] for item in items], ["initialization-guide", "development-skill"])
        for item in items:
            with self.subTest(code=item["code"]):
                resource = read_resource(item["code"])
                self.assertEqual(item["version"], __version__)
                self.assertEqual(len(resource.content), item["size_bytes"])
                self.assertEqual(hashlib.sha256(resource.content).hexdigest(), item["sha256"])
                self.assertEqual(item["download_url"], f"api/v1/developer-resources/{item['code']}/download")
        guide = PdfReader(io.BytesIO(read_resource("initialization-guide").content))
        self.assertEqual(items[0]["page_count"], len(guide.pages))
        guide_text = unicodedata.normalize("NFKC", "".join(page.extract_text() for page in guide.pages))
        guide_text = "".join(guide_text.split())
        self.assertIn("首次初始化请忽略「应用配置」", guide_text)
        self.assertEqual(guide_text.count("应用配置"), 1)

    def test_skill_is_portable_complete_and_parseable(self):
        resource = read_resource("development-skill")
        documents = source_documents()  # Validates all Markdown links and private-path/credential patterns.
        with zipfile.ZipFile(io.BytesIO(resource.content)) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(len(archive.namelist()), len(documents))
            self.assertEqual(resource.metadata["document_count"], sum(name.endswith(".md") for name in documents))
            for name, content in documents.items():
                self.assertEqual(archive.read(f"{SKILL_CODE}/{name}"), content)
                self.assertNotIn(b"{{DEEP_AGENT_VERSION}}", content)
                self.assertNotIn(".codex/", content.decode())
            for name in ("README.md", "SKILL.md", "references/source/backend/index.md", "references/source/frontend/index.md",
                         "references/04-skill-python-files.md", "references/06-structured-workflows.md",
                         "references/07-spreadsheet-assistant.md"):
                self.assertIn(name, documents)
        with tempfile.TemporaryDirectory(prefix="TMP to delete-skill-parse-") as temp:
            parsed = parse_skill_zip(resource.content, workdir=Path(temp))
            self.assertEqual(parsed.code, SKILL_CODE)

    def test_unknown_codes_and_corrupt_deployment_never_resolve_arbitrary_paths(self):
        for code in ("../manifest.json", "/etc/passwd", "development-skill.zip", ""):
            with self.subTest(code=code), self.assertRaises(KeyError):
                read_resource(code)
        with tempfile.TemporaryDirectory(prefix="TMP to delete-developer-resource-") as temp:
            root = Path(temp)
            with patch("deep_agent.developer_resources.GENERATED_ROOT", root):
                with self.assertRaisesRegex(ResourceUnavailableError, "开发资料暂不可用"):
                    read_resource("development-skill")
                manifest = json.loads((GENERATED_ROOT / "manifest.json").read_text())
                (root / "manifest.json").write_text(json.dumps(manifest))
                (root / "deepagent-agents-dev.zip").write_bytes(b"incomplete")
                with self.assertRaises(ResourceUnavailableError):
                    read_resource("development-skill")
                manifest["version"] = "0.0.0"
                (root / "manifest.json").write_text(json.dumps(manifest))
                with self.assertRaises(ResourceUnavailableError):
                    read_resource("initialization-guide")

    def test_distribution_owns_the_resources_without_public_skill_archive(self):
        repo = ROOT.parents[2]
        self.assertEqual(PDF_PATH, repo / "console/downloads/spreadsheet-initialization.pdf")
        self.assertTrue(GENERATED_ROOT.is_relative_to(repo / "src/deep_agent"))
        self.assertFalse(GENERATED_ROOT.is_relative_to(repo / "console"))
        self.assertRegex((repo / "deploy/jenkins/Jenkinsfile.gateway").read_text(), r"git[^\n]+archive[^\n]+HEAD")
        self.assertFalse(any((repo / "console").rglob("deepagent-agents-dev*.zip")))


if __name__ == "__main__":
    unittest.main()
