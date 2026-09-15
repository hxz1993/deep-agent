"""Portable, offline Chinese chart defaults for the isolated Python worker."""
from __future__ import annotations

import importlib.abc
import importlib.machinery
import sys
from pathlib import Path

FONT_PATH = Path(__file__).resolve().parent / "fonts" / "NotoSansCJKsc-Regular.otf"
FONT_FAMILY = "Noto Sans CJK SC"


def configure_plotting() -> None:
    """Register the shipped font for this process; user rcParams may override it.

    Do not install fonts globally, write to matplotlib's package directory, or
    download anything during a tool invocation. Basic workers without optional
    pydata packages can still execute ordinary Python.
    """
    try:
        import matplotlib
    except ModuleNotFoundError as exc:
        if exc.name == "matplotlib":
            return
        raise
    from matplotlib import font_manager

    font_manager.fontManager.addfont(str(FONT_PATH))
    matplotlib.use("Agg")
    matplotlib.rcParams["font.family"] = ["sans-serif"]
    matplotlib.rcParams["font.sans-serif"] = [
        FONT_FAMILY,
        *[name for name in matplotlib.rcParams["font.sans-serif"] if name != FONT_FAMILY],
    ]


class _PlottingLoader(importlib.abc.Loader):
    def __init__(self, loader):
        self.loader = loader

    def __getattr__(self, name):
        return getattr(self.loader, name)

    def create_module(self, spec):
        return self.loader.create_module(spec)

    def exec_module(self, module):
        self.loader.exec_module(module)
        configure_plotting()


class _PlottingFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname != "matplotlib":
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path, target)
        if spec is not None and spec.loader is not None:
            spec.loader = _PlottingLoader(spec.loader)
            sys.meta_path.remove(self)
        return spec


def install_plotting_defaults() -> None:
    """Apply defaults on first matplotlib import, including indirect imports.

    Avoid font discovery and plotting dependencies for ordinary Python calls.
    Registration occurs after matplotlib itself loads and before pyplot loads.
    """
    sys.meta_path.insert(0, _PlottingFinder())
