from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class CatalogFacet(BaseModel):
    id: str
    label: str
    count: int


class CatalogFacets(BaseModel):
    labs: list[CatalogFacet] = Field(default_factory=list)
    providers: list[CatalogFacet] = Field(default_factory=list)


class CatalogProviderOption(BaseModel):
    provider_id: str
    provider_label: str
    provider_model_id: str
    provider_npm: str | None = None
    provider_api_url: str | None = None
    provider_doc_url: str | None = None
    adapter: str
    runtime_model: str
    context_window_tokens: int
    max_output_tokens: int
    input_modalities: list[str] = Field(default_factory=list)
    output_modalities: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    status: str | None = None
    configured_preset: str | None = None
    metadata: dict = Field(default_factory=dict)


class ModelCatalogModelItem(BaseModel):
    kind: Literal["model"] = "model"
    lab_id: str
    lab_label: str
    lab_description: str | None = None
    canonical_model_id: str
    model_label: str
    model_description: str | None = None
    family: str | None = None
    release_date: str | None = None
    context_window_tokens: int
    max_output_tokens: int
    input_modalities: list[str] = Field(default_factory=list)
    output_modalities: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    provider_count: int
    providers: list[CatalogProviderOption] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class CatalogProviderModelSummary(BaseModel):
    lab_id: str
    lab_label: str
    canonical_model_id: str
    model_label: str
    provider_model_id: str
    release_date: str | None = None
    context_window_tokens: int
    max_output_tokens: int
    input_modalities: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class ModelCatalogProviderItem(BaseModel):
    kind: Literal["provider"] = "provider"
    provider_id: str
    provider_label: str
    provider_npm: str | None = None
    provider_api_url: str | None = None
    provider_doc_url: str | None = None
    adapter: str
    model_count: int
    models: list[CatalogProviderModelSummary] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class ModelCatalogResponse(BaseModel):
    view: Literal["model", "provider"]
    items: list[ModelCatalogModelItem | ModelCatalogProviderItem]
    total: int
    offset: int = 0
    limit: int = 50
    facets: CatalogFacets = Field(default_factory=CatalogFacets)
    refreshed_at: datetime | None = None


class ModelCatalogRefreshResponse(BaseModel):
    rows: int
    models: int
    providers: int
    labs: int
    refreshed_at: datetime
