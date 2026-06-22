from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from deep_agent.capabilities.cursor_skills.parser import parse_skill_md


@dataclass
class SkillEntry:
    """A discovered Cursor skill: its metadata plus the directory it lives in."""

    name: str
    description: str
    body: str
    auto_invoke: bool
    directory: Path


def _split_dirs(dirs: str) -> list[str]:
    return [d.strip() for d in dirs.split(",") if d.strip()]


def discover_cursor_skills(skills_dirs: str) -> list[SkillEntry]:
    """Scan comma-separated directories for ``<name>/SKILL.md`` skills.

    Directories are scanned in order; the first occurrence of a skill name wins
    and later duplicates are skipped with a warning.
    """
    entries: dict[str, SkillEntry] = {}

    for dir_str in _split_dirs(skills_dirs):
        base = Path(dir_str)
        if not base.is_dir():
            logger.info("Cursor skills dir {!r} not found; skipping.", dir_str)
            continue

        for entry in sorted(base.iterdir()):
            if not entry.is_dir() or entry.name.startswith((".", "_")):
                continue
            skill_md = entry / "SKILL.md"
            if not skill_md.is_file():
                continue

            try:
                text = skill_md.read_text(encoding="utf-8")
            except OSError:
                logger.exception("Could not read {}", skill_md)
                continue

            parsed = parse_skill_md(text)
            if parsed is None:
                logger.warning("Skipping invalid skill at {}", skill_md)
                continue
            if parsed.name in entries:
                logger.warning(
                    "Duplicate cursor skill {!r} at {}; keeping the first.", parsed.name, entry
                )
                continue

            entries[parsed.name] = SkillEntry(
                name=parsed.name,
                description=parsed.description,
                body=parsed.body,
                auto_invoke=parsed.auto_invoke,
                directory=entry.resolve(),
            )

    return list(entries.values())
