from __future__ import annotations

import re
from dataclasses import dataclass

import yaml

from deep_agent.core.logging import get_logger

logger = get_logger(__name__)

# Matches a leading YAML frontmatter block delimited by '---' lines, capturing
# the YAML payload and the markdown body that follows it.
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)
_NAME_RE = re.compile(r"^[a-z0-9-]{1,64}$")


@dataclass
class ParsedSkill:
    """A SKILL.md parsed into its metadata and body."""

    name: str
    description: str
    body: str
    auto_invoke: bool


def parse_skill_md(text: str) -> ParsedSkill | None:
    """Parse a Cursor ``SKILL.md`` document.

    Returns ``None`` (with a warning logged) when the frontmatter is missing or a
    required field is absent/invalid, so one malformed skill never breaks startup.
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        logger.warning("SKILL.md has no YAML frontmatter; skipping.")
        return None

    raw_meta, body = match.group(1), match.group(2)
    try:
        meta = yaml.safe_load(raw_meta) or {}
    except yaml.YAMLError:
        logger.exception("Invalid YAML frontmatter in SKILL.md")
        return None
    if not isinstance(meta, dict):
        logger.warning("SKILL.md frontmatter is not a mapping; skipping.")
        return None

    name = meta.get("name")
    if not isinstance(name, str) or not _NAME_RE.match(name):
        logger.warning("SKILL.md 'name' missing or invalid (%r); skipping.", name)
        return None

    description = meta.get("description")
    if not isinstance(description, str) or not description.strip():
        logger.warning("SKILL.md %r has empty 'description'; skipping.", name)
        return None

    # Default true, matching Cursor: a skill only auto-loads when it opts in.
    auto_invoke = not bool(meta.get("disable-model-invocation", True))

    return ParsedSkill(
        name=name,
        description=description.strip(),
        body=body.strip(),
        auto_invoke=auto_invoke,
    )
