"""辅助模型使用场景目录与解析。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from deep_agent.db.stores.auxiliary_model import AuxiliaryModelStoreBase
    from deep_agent.db.stores.llm_model import LlmModelStoreBase

TITLE_USE_CASE = "title"


@dataclass(frozen=True)
class AuxiliaryModelUseCase:
    use_case: str
    label: str
    description: str


AUXILIARY_MODEL_CATALOG: dict[str, AuxiliaryModelUseCase] = {
    TITLE_USE_CASE: AuxiliaryModelUseCase(
        use_case=TITLE_USE_CASE,
        label="标题生成",
        description="会话标题",
    ),
}


def is_known_use_case(use_case: str) -> bool:
    return use_case in AUXILIARY_MODEL_CATALOG


async def resolve_llm_model_for_use_case(
    auxiliary_store: AuxiliaryModelStoreBase,
    llm_model_store: LlmModelStoreBase,
    use_case: str,
):
    row = await auxiliary_store.get_by_use_case(use_case)
    if row is None or not row.llm_model_id:
        return None
    return await llm_model_store.get_by_id(row.llm_model_id)
