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
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from deep_agent.options import OPTION

if TYPE_CHECKING:
    from deep_agent.agent.spec import AgentSpec

logger = logging.getLogger(__name__)

# 标题生成提示词模板
_TITLE_PROMPT = (
    "Generate a short, descriptive title (3-7 words) for a conversation "
    "that starts with the following exchange. The title should capture the "
    "main topic or intent. Write the title in the same language the user is "
    "writing in. Return ONLY the title text, nothing else. No quotes, no "
    "punctuation at the end, no prefixes."
)


def _build_title_model() -> Optional[Model]:
    """根据配置构建标题生成专用的 LLM 模型实例。

    优先使用 DEEP_AGENT_TITLE_MODEL 配置的专用模型；
    未配置时返回 None，由调用方使用主模型。
    """
    model_str = OPTION.agent.title_model
    if not model_str:
        return None

    # 构建 OpenAI 兼容 provider
    base_url = OPTION.agent.title_model_base_url or None
    api_key = OPTION.agent.title_model_api_key or None
    provider = OpenAIProvider(base_url=base_url, api_key=api_key)
    return OpenAIChatModel(model_str, provider=provider)


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


def _resolve_title_model(main_model: Model | str | None = None) -> Model | str | None:
    """专用标题模型优先，否则回退对话主模型。"""
    dedicated = _build_title_model()
    if dedicated is not None:
        return dedicated
    if main_model:
        logger.debug("Using main chat model for title generation")
        return main_model
    return None


def resolve_main_model_for_title(spec: AgentSpec) -> Model | str | None:
    """解析对话主模型供标题回退；已配置专用标题模型时跳过。"""
    if OPTION.agent.title_model or not spec.model:
        return None
    try:
        from deep_agent.agent.factory import _resolve_model

        return _resolve_model(spec)
    except Exception as exc:
        logger.debug("Cannot resolve main model for title generation: %s", exc)
        return None


async def generate_title(
    user_text: str,
    assistant_text: str,
    *,
    main_model: Model | str | None = None,
) -> Optional[str]:
    """从首轮对话生成标题，失败返回 None。

    流程：
    1. 构建提示词（用户消息 + 助手响应）
    2. 调用专用标题模型；未配置时回退到 ``main_model``（对话主模型）
    3. 清洗 LLM 输出
    4. 失败时记录日志，返回 None

    Args:
        user_text: 用户首条消息文本
        assistant_text: 助手首次响应文本
        main_model: 当前会话的对话主模型（Agent.model），供专用模型未配置时回退
    Returns:
        生成的标题字符串，失败则 None
    """
    user_snippet = (user_text or "")[:500]
    assistant_snippet = (assistant_text or "")[:500]
    user_content = f"User: {user_snippet}\n\nAssistant: {assistant_snippet}"

    model = _resolve_title_model(main_model)
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
