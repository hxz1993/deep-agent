"""Build or check the single Coding Skill ZIP and its authenticated catalog."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import zipfile
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / "src"))

from deep_agent import __version__
from deep_agent.developer_resources import GENERATED_ROOT, PDF_PATH, ROOT

SKILL_CODE = "deepagent-agents-dev"
SKILL_ROOT = ROOT / "skill" / SKILL_CODE


def source_documents() -> dict[str, bytes]:
    documents: dict[str, bytes] = {}
    for path in sorted(SKILL_ROOT.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(SKILL_ROOT).as_posix()
        if path.is_symlink() or path.suffix not in {".md", ".py"}:
            raise ValueError(f"Unexpected package resource: {relative}")
        text = path.read_text(encoding="utf-8").replace("{{DEEP_AGENT_VERSION}}", __version__)
        if re.search(r"/Users/|[A-Z]:\\Users\\|sk-[A-Za-z0-9_-]{20,}|-----BEGIN .*PRIVATE KEY", text):
            raise ValueError(f"Private path or credential pattern: {relative}")
        if path.suffix == ".md":
            for target in re.findall(r"\]\(([^)]+)\)", text):
                target = target.split("#", 1)[0]
                if not target or "://" in target:
                    continue
                resolved = (path.parent / target).resolve()
                if not resolved.is_relative_to(SKILL_ROOT.resolve()) or not resolved.is_file():
                    raise ValueError(f"Broken/outside package link {relative}: {target}")
        documents[relative] = text.encode("utf-8")
    if "SKILL.md" not in documents:
        raise ValueError("Missing SKILL.md")
    return documents


def build_outputs() -> dict[Path, bytes]:
    from pypdf import PdfReader

    documents = source_documents()
    memory = io.BytesIO()
    with zipfile.ZipFile(memory, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, content in documents.items():
            info = zipfile.ZipInfo(f"{SKILL_CODE}/{name}", (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content, compresslevel=9)
    zipped = memory.getvalue()
    pdf = PDF_PATH.read_bytes()

    def metadata(code: str, name: str, filename: str, mime: str, content: bytes, **extra: object) -> dict:
        return dict(code=code, name=name, filename=filename, media_type=mime, version=__version__,
                    size_bytes=len(content), sha256=hashlib.sha256(content).hexdigest(),
                    download_url=f"api/v1/developer-resources/{code}/download", **extra)

    manifest = {
        "version": __version__,
        "items": [
            metadata("initialization-guide", "电子表格助手初始化", "DeepAgent-电子表格助手初始化.pdf", "application/pdf", pdf,
                     page_count=len(PdfReader(io.BytesIO(pdf)).pages)),
            metadata("development-skill", "DeepAgent 开发 Skill", f"{SKILL_CODE}-{__version__}.zip",
                     "application/zip", zipped, document_count=sum(name.endswith(".md") for name in documents)),
        ],
    }
    return {
        GENERATED_ROOT / f"{SKILL_CODE}.zip": zipped,
        GENERATED_ROOT / "manifest.json": (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Write generated product resources; default checks")
    args = parser.parse_args()
    stale = []
    for path, content in build_outputs().items():
        if args.write:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        elif not path.is_file() or path.read_bytes() != content:
            stale.append(str(path.relative_to(REPOSITORY)))
    if stale:
        print("Developer resources need rebuilding:", ", ".join(stale))
        return 1
    print("Developer resources built" if args.write else "Developer resources verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
