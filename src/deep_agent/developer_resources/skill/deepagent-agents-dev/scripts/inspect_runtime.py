#!/usr/bin/env python3
"""Inspect the Python runtime used for DeepAgent and DeepCube without exposing secrets."""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import os
import platform
import site
import sys
from pathlib import Path
from typing import Any


PACKAGES = (
    ("deep-agent", "deep_agent"),
    ("deepcube", "deepcube"),
    ("deepfos", "deepfos"),
    ("pydantic-ai-slim", "pydantic_ai"),
    ("pydantic-ai-harness", "pydantic_ai_harness"),
    ("pydantic", "pydantic"),
)


def distribution_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def module_location(name: str) -> str | None:
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, AttributeError, ValueError):
        return None
    if spec is None:
        return None
    if spec.origin and spec.origin not in {"built-in", "frozen"}:
        return str(Path(spec.origin).resolve())
    locations = list(spec.submodule_search_locations or ())
    return str(Path(locations[0]).resolve()) if locations else spec.origin


def is_site_package(path: str | None, site_paths: list[str]) -> bool | None:
    if not path:
        return None
    resolved = Path(path).resolve()
    return any(resolved == Path(base).resolve() or Path(base).resolve() in resolved.parents for base in site_paths)


def inspect() -> dict[str, Any]:
    site_paths = [path for path in site.getsitepackages() if path]
    user_site = site.getusersitepackages()
    if user_site:
        site_paths.append(user_site)

    packages: list[dict[str, Any]] = []
    for distribution, module in PACKAGES:
        location = module_location(module)
        packages.append(
            {
                "distribution": distribution,
                "version": distribution_version(distribution),
                "module": module,
                "module_location": location,
                "from_site_packages": is_site_package(location, site_paths),
            }
        )

    return {
        "executable": str(Path(sys.executable).resolve()),
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "machine": platform.machine(),
        "platform": platform.platform(),
        "cwd": os.getcwd(),
        "site_packages": site_paths,
        "packages": packages,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args()
    result = inspect()

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print(f"Python: {result['python']} ({result['executable']})")
    print(f"Platform: {result['platform']} / {result['machine']}")
    for package in result["packages"]:
        version = package["version"] or "not installed"
        location = package["module_location"] or "module not found"
        source = "site-packages" if package["from_site_packages"] else "outside site-packages"
        print(f"- {package['distribution']}: {version}")
        print(f"  {package['module']}: {location} ({source})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
