from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from deep_agent.core.exceptions import ToolPackageError


@dataclass(frozen=True)
class ToolInfo:
    """Display metadata for one stable model-facing tool protocol."""

    name: str
    display_name: str | None = None
    description: str = ""
    execution_type: str = "function"
    enabled: bool = True


@dataclass
class ToolPackage:
    """Product-level selection unit that contributes Pydantic AI toolsets."""

    code: str
    category: str
    toolsets: list[Any] = field(default_factory=list)
    label: str | None = None
    source: str | None = None
    description: str = ""
    tools: list[ToolInfo] = field(default_factory=list)
    default_enabled: bool = True
    enabled: bool = True
    tool_catalog_status: str = "complete"


class ToolPackageRegistry:
    """Holds product tool packages and returns their selected toolsets."""

    def __init__(self) -> None:
        self._packages: dict[str, ToolPackage] = {}

    def register(self, package: ToolPackage) -> None:
        if package.code in self._packages:
            raise ToolPackageError(f"Duplicate tool package code: {package.code}")
        self._packages[package.code] = package

    def replace_by_category(self, category: str, packages: list[ToolPackage]) -> None:
        self._packages = {
            code: package
            for code, package in self._packages.items()
            if package.category != category
        }
        for package in packages:
            self.register(package)

    def codes(self) -> list[str]:
        return list(self._packages)

    def get(self, code: str) -> ToolPackage:
        if code not in self._packages:
            raise ToolPackageError(f"Unknown tool package: {code}")
        return self._packages[code]

    def toolsets(self, codes: list[str] | None = None) -> list[Any]:
        if codes is None:
            packages = [
                package
                for package in self._packages.values()
                if package.enabled and package.default_enabled
            ]
        else:
            packages = [
                self._packages[code]
                for code in codes
                if code in self._packages and self._packages[code].enabled
            ]
        return [toolset for package in packages for toolset in package.toolsets]

    def describe(self) -> list[dict[str, Any]]:
        return [describe_tool_package(package) for package in self._packages.values()]


def describe_tool_package(package: ToolPackage) -> dict[str, Any]:
    return {
        "code": package.code,
        "category": package.category,
        "label": package.label,
        "source": package.source,
        "description": package.description,
        "enabled": package.enabled,
        "default_enabled": package.default_enabled,
        "tool_count": len(package.tools),
        "tool_catalog_status": package.tool_catalog_status,
        "tools": [
            {
                "name": tool.name,
                "display_name": tool.display_name or tool.name,
                "description": tool.description,
                "execution_type": tool.execution_type,
                "enabled": tool.enabled,
            }
            for tool in package.tools
        ],
    }
