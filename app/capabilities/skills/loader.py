from __future__ import annotations

import importlib
from pathlib import Path

from app.capabilities.registry import Capability
from app.capabilities.skills.base import Skill
from app.core.logging import get_logger

logger = get_logger(__name__)


def load_skill_capabilities(skills_dir: str) -> list[Capability]:
    """Discover skills under ``skills_dir``.

    Each immediate subdirectory that is an importable package and defines a
    module-level ``SKILL: Skill`` becomes one capability. ``skills_dir`` must be
    importable as a top-level package (the project root is on ``sys.path`` when
    running uvicorn from there).
    """
    base = Path(skills_dir)
    if not base.is_dir():
        logger.info("Skills directory %r not found; skipping skill loading.", skills_dir)
        return []

    package_name = base.name
    capabilities: list[Capability] = []

    for entry in sorted(base.iterdir()):
        if not entry.is_dir() or entry.name.startswith((".", "_")):
            continue
        module_name = f"{package_name}.{entry.name}"
        try:
            module = importlib.import_module(module_name)
        except Exception:  # noqa: BLE001 - one bad skill should not kill startup
            logger.exception("Failed to import skill module %r", module_name)
            continue

        skill = getattr(module, "SKILL", None)
        if not isinstance(skill, Skill):
            logger.warning("Module %r has no module-level SKILL: Skill; skipping.", module_name)
            continue

        capabilities.append(skill.to_capability())
        logger.info("Loaded skill %r", skill.name)

    return capabilities
