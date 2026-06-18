import os
import pathlib

import importlib
from fastapi import APIRouter


__all__ = [
    "api_router",
]

api_router = APIRouter(prefix="/api/v1")


def collect_modules():
    for f in os.listdir(pathlib.Path(__file__).parent):
        if f.endswith('.py') and f != '__init__.py':
            mod_name = f[:-3]
            mod = importlib.import_module("." + mod_name, package=__package__)
            yield mod



for module in collect_modules():
    if (router := getattr(module, "router", None)) is None:
        continue


    if (tag := getattr(module, "tag", None)) is None:
        tag = module.__name__
    api_router.include_router(router, tags=[tag])
