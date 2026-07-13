from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from deep_agent.agent.auxiliary_models import AuxiliaryModelUseCase


class AuxiliaryModelUpdate(BaseModel):
    llm_model_id: str | None = Field(
        default=None,
        description="Dedicated LLM model preset id; null means reuse the chat main model (auto).",
    )


class AuxiliaryModelResponse(BaseModel):
    use_case: str
    label: str
    description: str
    mode: Literal["auto", "dedicated"]
    llm_model_id: str | None = None
    llm_model_name: str | None = None
    model: str | None = None
    base_url: str | None = None
    api_key_set: bool = False
    updated_at: datetime | None = None

    @classmethod
    def from_catalog_and_row(
        cls,
        meta: AuxiliaryModelUseCase,
        *,
        llm_model_id: str | None,
        llm_model_name: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        api_key_set: bool = False,
        updated_at: datetime | None = None,
    ) -> AuxiliaryModelResponse:
        mode: Literal["auto", "dedicated"] = "dedicated" if llm_model_id else "auto"
        return cls(
            use_case=meta.use_case,
            label=meta.label,
            description=meta.description,
            mode=mode,
            llm_model_id=llm_model_id,
            llm_model_name=llm_model_name,
            model=model,
            base_url=base_url,
            api_key_set=api_key_set,
            updated_at=updated_at,
        )

    @classmethod
    def from_catalog_only(cls, meta: AuxiliaryModelUseCase) -> AuxiliaryModelResponse:
        return cls.from_catalog_and_row(meta, llm_model_id=None)
