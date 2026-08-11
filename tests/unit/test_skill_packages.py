from __future__ import annotations

import io
import shutil
import stat
import unittest
import zipfile

from docx import Document
from openpyxl import Workbook

from deep_agent.skills.catalog import list_builtin_skills
from deep_agent.skills.package import SkillPackageError, parse_skill_zip


def skill_zip(
    *,
    name: str = "example-skill",
    wrapper: bool = True,
    scripts: bool = False,
    office: bool = False,
) -> bytes:
    buffer = io.BytesIO()
    prefix = f"{name}/" if wrapper else ""
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            prefix + "SKILL.md",
            f"---\nname: {name}\ndescription: Example deferred Skill.\n---\n\n# Body\n\nSecret body marker.",
        )
        archive.writestr(prefix + "references/guide.md", "# Guide\nRead only what is needed.")
        archive.writestr(prefix + "assets/template.json", '{"value": null}')
        archive.writestr(
            prefix + "assets/image.png",
            b"\x89PNG\r\n\x1a\n",
        )
        if office:
            document = Document()
            document.add_paragraph("Skill Word 第一段")
            document.add_paragraph("Skill Word 第二段")
            docx_output = io.BytesIO()
            document.save(docx_output)
            archive.writestr(prefix + "references/guide.docx", docx_output.getvalue())

            workbook = Workbook()
            workbook.active.title = "数据"
            workbook.active.append(["名称", "值"])
            workbook.active.append(["甲", 1])
            xlsx_output = io.BytesIO()
            workbook.save(xlsx_output)
            archive.writestr(prefix + "assets/template.xlsx", xlsx_output.getvalue())
        if scripts:
            archive.writestr(prefix + "scripts/run.py", "raise SystemExit('never execute')")
    return buffer.getvalue()


class SkillPackageTests(unittest.TestCase):
    def _cleanup(self, parsed) -> None:
        shutil.rmtree(parsed.package_root.parent, ignore_errors=True)

    def test_standard_package_is_validated_and_indexed(self) -> None:
        parsed = parse_skill_zip(skill_zip())
        try:
            self.assertEqual(parsed.code, "example-skill")
            self.assertEqual(parsed.schema_version, "1.0")
            by_path = {item["path"]: item for item in parsed.resource_index}
            self.assertTrue(by_path["references/guide.md"]["readable"])
            self.assertTrue(by_path["assets/template.json"]["readable"])
            self.assertFalse(by_path["assets/image.png"]["readable"])
            self.assertFalse(parsed.contains_scripts)
        finally:
            self._cleanup(parsed)

    def test_scripts_are_stored_but_marked_non_readable(self) -> None:
        parsed = parse_skill_zip(skill_zip(scripts=True))
        try:
            script = next(item for item in parsed.resource_index if item["category"] == "script")
            self.assertTrue(parsed.contains_scripts)
            self.assertEqual(script["path"], "scripts/run.py")
        finally:
            self._cleanup(parsed)

    def test_path_traversal_is_rejected(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("SKILL.md", "---\nname: bad\ndescription: bad\n---")
            archive.writestr("../escape.txt", "bad")
        with self.assertRaisesRegex(SkillPackageError, "Unsafe ZIP path"):
            parse_skill_zip(buffer.getvalue())

    def test_symbolic_links_are_rejected(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("SKILL.md", "---\nname: bad\ndescription: bad\n---")
            info = zipfile.ZipInfo("assets/link")
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, "../secret")
        with self.assertRaisesRegex(SkillPackageError, "Symbolic links"):
            parse_skill_zip(buffer.getvalue())

    def test_unsupported_reference_type_is_rejected(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(
                "SKILL.md",
                "---\nname: example-skill\ndescription: Example.\n---\n",
            )
            archive.writestr("references/archive.bin", b"not model-readable")
        with self.assertRaisesRegex(SkillPackageError, "Unsupported reference type"):
            parse_skill_zip(buffer.getvalue())

    def test_library_with_multiple_skills_requires_split_packages(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name in ("skill-one", "skill-two"):
                archive.writestr(
                    f"{name}/SKILL.md",
                    f"---\nname: {name}\ndescription: {name}\n---\n",
                )
        with self.assertRaisesRegex(
            SkillPackageError,
            "contains multiple Skills.*one ZIP per Skill",
        ):
            parse_skill_zip(buffer.getvalue())

    def test_builtin_skills_are_valid_harness_packages(self) -> None:
        skills = list_builtin_skills()
        self.assertEqual(
            {item.code for item in skills},
            {"deepagent-diagnostics", "deepfos-readonly-analysis"},
        )
        self.assertTrue(all(item.resource_index for item in skills))


if __name__ == "__main__":
    unittest.main()
