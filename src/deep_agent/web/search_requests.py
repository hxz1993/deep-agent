"""Model-facing parameters for the native Tavily, Exa, and You.com Search APIs."""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_SEARCH_RESULTS = 5


class TavilySearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: Annotated[str, Field(min_length=1)]
    max_results: Annotated[int, Field(ge=0, le=20)] = DEFAULT_SEARCH_RESULTS
    search_depth: Literal["basic", "advanced", "fast", "ultra-fast"] | None = None
    chunks_per_source: Annotated[int, Field(ge=1, le=3)] | None = None
    topic: Literal["general", "news", "finance"] | None = None
    time_range: Literal["day", "week", "month", "year", "d", "w", "m", "y"] | None = None
    start_date: str | None = None
    end_date: str | None = None
    include_published_date: bool | None = None
    filter_by_published_date: bool | None = None
    max_age_hours: int | None = None
    include_answer: bool | Literal["basic", "advanced"] | None = None
    include_raw_content: bool | Literal["markdown", "text"] | None = None
    include_images: bool | None = None
    include_image_descriptions: bool | None = None
    include_favicon: bool | None = None
    include_domains: list[str] | None = None
    exclude_domains: list[str] | None = None
    include_domains_mode: Literal["restrict", "prefer"] | None = None
    country: str | None = None
    language: str | None = None
    filter_by_language: bool | None = None
    auto_parameters: bool | None = None
    exact_match: bool | None = None
    include_usage: bool | None = None
    safe_search: bool | None = None


class ExaSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: Annotated[str, Field(min_length=1)]
    numResults: Annotated[int, Field(ge=1, le=100)] = DEFAULT_SEARCH_RESULTS
    type: Literal["auto", "lite", "deep", "deep-lite", "instant", "fast", "deep-reasoning"] | None = None
    category: str | None = None
    userLocation: str | dict[str, Any] | None = Field(
        default=None, description="Country code or native coordinate object with latitude and longitude.",
    )
    includeDomains: list[str] | None = None
    excludeDomains: list[str] | None = None
    startPublishedDate: str | None = None
    endPublishedDate: str | None = None
    startCrawlDate: str | None = None
    endCrawlDate: str | None = None
    includeText: list[str] | None = None
    excludeText: list[str] | None = None
    moderation: bool | None = None
    additionalQueries: list[str] | None = None
    objective: str | None = None
    systemPrompt: str | None = None
    contents: dict[str, Any] | None = Field(
        default=None, description="Native content options, such as {text: true, highlights: true} or summary options.",
    )
    outputSchema: dict[str, Any] | None = Field(
        default=None, description="Native JSON Schema for supported deep-search types.",
    )
    compliance: Literal["hipaa"] | None = None


class YouSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: Annotated[str, Field(min_length=1)]
    count: Annotated[int, Field(ge=1, le=100)] = DEFAULT_SEARCH_RESULTS
    knowledge: Literal["core"] | None = None
    extraction: dict[str, Any] | None = Field(
        default=None,
        description='Native extraction options. Set extraction_mode to "highlights" or "full_page". '
                    "Highlights support up to 50 results.",
    )
    freshness: str | None = Field(
        default=None, description="day, week, month, year, or a YYYY-MM-DDtoYYYY-MM-DD range.",
    )
    country: str | None = None
    language: str | None = None
    include_domains: list[str] | None = None
    exclude_domains: list[str] | None = None
    boost_domains: list[str] | None = None
    strict_operators: bool | None = None
    crawl_timeout: Annotated[int, Field(ge=1, le=60)] | None = None
    offset: Annotated[int, Field(ge=0, le=9)] | None = None
    safesearch: Literal["off", "moderate", "strict"] | None = None
