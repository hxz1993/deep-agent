from __future__ import annotations

from pydantic import BaseModel, Field


class ModelCatalogItem(BaseModel):
    key: str = Field(description="Lookup key, usually provider/model.")
    provider: str = Field(default="", description="models.dev provider id.")
    model_id: str = Field(description="Model id within the provider.")
    provider_label: str | None = Field(default=None, description="Human-readable provider name when known.")
    input_modalities: list[str] = Field(default_factory=list)
    max_context: int | None = Field(default=None, description="Context window when known.")
    source: str = Field(description="models_dev | bundled | snapshot")
    configured_preset: str | None = Field(
        default=None,
        description="Display name of matching LLM preset in DB, if any.",
    )


class ModelCatalogResponse(BaseModel):
    items: list[ModelCatalogItem]
    total: int
    source: str = Field(description="Resolved catalog source: models_dev, bundled, or snapshot.")
    source_label: str = Field(description="Human-readable source description.")
    offset: int = 0
    limit: int = 100
    providers: list[str] = Field(
        default_factory=list,
        description="Distinct providers in the loaded catalog (before pagination).",
    )
