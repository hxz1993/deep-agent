"""自动生成会话标题。

在第一次对话交换完成后异步运行，不阻塞用户响应。
失败时回退到 first_user_prompt() 的文本提取逻辑。
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Optional

from pydantic_ai import Agent
from pydantic_ai.models import Model

from deep_agent.agent.auxiliary_models import TITLE_USE_CASE, resolve_llm_model_for_use_case
from deep_agent.options import OPTION

if TYPE_CHECKING:
    from deep_agent.agent.spec import AgentSpec
    from deep_agent.db.stores.auxiliary_model import AuxiliaryModelStoreBase
    from deep_agent.db.stores.llm_model import LlmModelStoreBase

logger = logging.getLogger(__name__)

# 标题生成提示词模板
_TITLE_PROMPT = (
    "Generate a short, descriptive title (3-7 words) for a conversation "
    "that starts with the following exchange. The title should capture the "
    "main topic or intent. Write the title in the same language the user is "
    "writing in. Return ONLY the title text, nothing else. No quotes, no "
    "punctuation at the end, no prefixes."
)


async def _resolve_dedicated_title_model(
    auxiliary_store: AuxiliaryModelStoreBase | None = None,
    llm_model_store: LlmModelStoreBase | None = None,
) -> Model | str | None:
    if auxiliary_store is None or llm_model_store is None:
        try:
            from deep_agent.core.context import get_context

            ctx = get_context()
            auxiliary_store = ctx.stores.auxiliary_model
            llm_model_store = ctx.stores.llm_model
        except RuntimeError:
            return None

    llm_row = await resolve_llm_model_for_use_case(
        auxiliary_store,
        llm_model_store,
        TITLE_USE_CASE,
    )
    if llm_row is None:
        return None

    from deep_agent.agent.factory import _resolve_model
    from deep_agent.agent.spec import AgentSpec

    return _resolve_model(AgentSpec.from_llm_model(llm_row))


async def resolve_title_model(
    *,
    main_model: Model | str | None = None,
    spec: AgentSpec | None = None,
    auxiliary_store: AuxiliaryModelStoreBase | None = None,
    llm_model_store: LlmModelStoreBase | None = None,
) -> Model | str | None:
    """专用标题模型优先，否则回退对话主模型（显式传入或从 spec 解析）。"""
    dedicated = await _resolve_dedicated_title_model(auxiliary_store, llm_model_store)
    if dedicated is not None:
        return dedicated
    if main_model is not None:
        logger.debug("Using main chat model for title generation")
        return main_model
    if spec is not None and spec.model:
        try:
            from deep_agent.agent.factory import _resolve_model

            return _resolve_model(spec)
        except Exception as exc:
            logger.debug("Cannot resolve main model for title generation: %s", exc)
    return None


def _clean_title(raw: str) -> Optional[str]:
    """清洗标题文本：去除思考块、引号、前缀、末尾标点。

    Args:
        raw: LLM 输出的原始标题文本
    Returns:
        清洗后的标题，为空则返回 None
    """
    if not raw:
        return None

    # 步骤 1：去除思考块
    cleaned = re.sub(r"thinking.*?thinking", "", raw, flags=re.DOTALL).strip()
    # 步骤 2：去除引号包裹
    cleaned = cleaned.strip("\"'")
    # 步骤 3：去除 "Title:" 前缀
    if cleaned.lower().startswith("title:"):
        cleaned = cleaned[6:].strip()
    # 步骤 4：限制最大长度（先截断，再清理末尾标点）
    truncated = False
    if len(cleaned) > 80:
        cleaned = cleaned[:77] + "..."
        truncated = True
    # 步骤 5：去除末尾标点（截断后有 "..." 时不再清理，避免误删省略号）
    if not truncated:
        cleaned = cleaned.rstrip(".。！!？?")

    return cleaned.strip() or None


async def generate_title(
    user_text: str,
    assistant_text: str,
    *,
    main_model: Model | str | None = None,
    spec: AgentSpec | None = None,
    auxiliary_store: AuxiliaryModelStoreBase | None = None,
    llm_model_store: LlmModelStoreBase | None = None,
) -> Optional[str]:
    """从首轮对话生成标题，失败返回 None。"""
    user_snippet = (user_text or "")[:500]
    assistant_snippet = (assistant_text or "")[:500]
    user_content = f"User: {user_snippet}\n\nAssistant: {assistant_snippet}"

    model = await resolve_title_model(
        main_model=main_model,
        spec=spec,
        auxiliary_store=auxiliary_store,
        llm_model_store=llm_model_store,
    )
    if not model:
        logger.info("No title model or main model available, skipping AI title generation")
        return None

    timeout = OPTION.agent.title_generation_timeout
    try:
        agent = Agent(model, system_prompt=_TITLE_PROMPT, output_type=str)
        result = await asyncio.wait_for(agent.run(user_content), timeout=timeout)
        raw = result.output if isinstance(result.output, str) else str(result.output or "")
        title = _clean_title(raw)
        if title:
            logger.debug("Generated title: %s", title)
            return title
        logger.warning("Title generation returned empty after cleaning")
        return None
    except TimeoutError:
        logger.warning("Title generation timed out after %.1fs", timeout)
        return None
    except Exception as exc:
        logger.warning("Title generation failed: %s", exc)
        logger.debug("Title generation traceback", exc_info=True)
        return None
