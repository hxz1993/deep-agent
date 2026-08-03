from __future__ import annotations

from deep_agent.core.context import AppContextDep
from deep_agent.db.stores.agent import AgentStoreBase
from deep_agent.db.stores.auxiliary_model import AuxiliaryModelStoreBase
from deep_agent.db.stores.llm_model import LlmModelStoreBase
from deep_agent.db.stores.mcp import McpServerStoreBase
from deep_agent.db.stores.model_catalog import ModelCatalogStoreBase
from deep_agent.db.stores.ui_tool import UiToolStoreBase
from deep_agent.db.stores.ui_tool_package import UiToolPackageStoreBase


def get_agent_store(ctx: AppContextDep) -> AgentStoreBase:
    return ctx.stores.agent


def get_llm_model_store(ctx: AppContextDep) -> LlmModelStoreBase:
    return ctx.stores.llm_model


def get_model_catalog_store(ctx: AppContextDep) -> ModelCatalogStoreBase:
    return ctx.stores.model_catalog


def get_auxiliary_model_store(ctx: AppContextDep) -> AuxiliaryModelStoreBase:
    return ctx.stores.auxiliary_model


def get_mcp_store(ctx: AppContextDep) -> McpServerStoreBase:
    return ctx.stores.mcp


def get_ui_tool_store(ctx: AppContextDep) -> UiToolStoreBase:
    return ctx.stores.ui_tool


def get_ui_tool_package_store(ctx: AppContextDep) -> UiToolPackageStoreBase:
    return ctx.stores.ui_tool_package
