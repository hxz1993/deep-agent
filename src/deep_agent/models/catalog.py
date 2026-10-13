"""models.dev normalization and database-backed catalog presentation."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any, Literal

from deep_agent.db.models import LlmModel, LlmProvider, ModelCatalog

CatalogView = Literal["model", "provider"]

PROVIDER_ALLOWLIST = frozenset(
    {
        "alibaba",
        "anthropic",
        "deepseek",
        "google",
        "minimax",
        "minimax-cn",
        "moonshotai",
        "openai",
        "stepfun",
        "xai",
        "zhipuai",
    }
)

PROVIDER_ADAPTERS: dict[str, str] = {
    "openai": "openai",
    "anthropic": "anthropic",
    "google": "google",
    "deepseek": "deepseek",
    "alibaba": "openai_compatible",
    "moonshotai": "openai_compatible",
    "zhipuai": "openai_compatible",
    "minimax": "anthropic_compatible",
    "minimax-cn": "anthropic_compatible",
    "stepfun": "openai_compatible",
    "xai": "openai_compatible",
}

PROVIDER_API_DEFAULTS = {
    "xai": "https://api.x.ai/v1",
}

LAB_ALIASES = {
    "minimax-cn": "minimax",
    "moonshot": "moonshotai",
    "qwen": "alibaba",
    "zai": "zhipuai",
    "zhipu": "zhipuai",
}

LAB_LABELS = {
    "alibaba": "Alibaba",
    "anthropic": "Anthropic",
    "deepseek": "DeepSeek",
    "google": "Google",
    "minimax": "MiniMax",
    "moonshotai": "Moonshot AI",
    "openai": "OpenAI",
    "stepfun": "StepFun",
    "xai": "xAI",
    "zhipuai": "Zhipu AI",
}

CAPABILITY_KEYS = (
    "attachment",
    "reasoning",
    "tool_call",
    "structured_output",
    "temperature",
    "open_weights",
    "interleaved",
)
MODALITIES = {"text", "image", "audio", "video"}
_METADATA_EXCLUDED_KEYS = {"cost"}
_CATALOG_DATE_PATTERN = re.compile(r"^\d{4}(?:-\d{2})?(?:-\d{2})?$")


class CatalogValidationError(ValueError):
    pass


@dataclass(frozen=True)
class CatalogRefreshStats:
    row_count: int
    model_count: int
    provider_count: int
    lab_count: int
    refreshed_at: datetime


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    integer = int(value)
    return integer if integer > 0 and integer == value else None


def _catalog_date(value: object, *, field_name: str) -> str | None:
    normalized = _text(value)
    if normalized is None:
        return None
    if not _CATALOG_DATE_PATTERN.fullmatch(normalized):
        raise CatalogValidationError(f"{field_name} 不是 YYYY、YYYY-MM 或 YYYY-MM-DD：{normalized}")
    parts = [int(part) for part in normalized.split("-")]
    if len(parts) >= 2 and not 1 <= parts[1] <= 12:
        raise CatalogValidationError(f"{field_name} 月份非法：{normalized}")
    if len(parts) == 3 and not 1 <= parts[2] <= 31:
        raise CatalogValidationError(f"{field_name} 日期非法：{normalized}")
    return normalized


def _descending_date_key(value: str | None) -> tuple[int, int, int, int]:
    if not value:
        return (1, 0, 0, 0)
    parts = [int(part) for part in value.split("-")]
    year, month, day = (*parts, *([0] * (3 - len(parts))))
    return (0, -year, -month, -day)


def _record(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for raw in value:
        item = _text(raw)
        if item is None:
            continue
        normalized = item.lower()
        if normalized not in MODALITIES or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _slug(value: str) -> str:
    result = []
    previous_dash = False
    for char in value.strip().lower():
        if char.isalnum():
            result.append(char)
            previous_dash = False
        elif not previous_dash:
            result.append("-")
            previous_dash = True
    return "".join(result).strip("-")


def normalize_lab_id(value: str) -> str:
    slug = _slug(value)
    return LAB_ALIASES.get(slug, slug)


def format_lab_label(lab_id: str) -> str:
    return LAB_LABELS.get(lab_id, lab_id.replace("-", " ").title())


def _without_price(value: object) -> object:
    if isinstance(value, list):
        return [_without_price(item) for item in value]
    if not isinstance(value, Mapping):
        return value
    return {
        str(key): _without_price(item)
        for key, item in value.items()
        if str(key) not in _METADATA_EXCLUDED_KEYS
    }


def _deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged = deepcopy(dict(base))
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _read_labs(payload: object) -> dict[str, dict[str, Any]]:
    if isinstance(payload, list):
        entries: Iterable[tuple[str | None, object]] = (
            (_text(item.get("id")) if isinstance(item, Mapping) else None, item)
            for item in payload
        )
    elif isinstance(payload, Mapping):
        nested = payload.get("labs")
        source = nested if isinstance(nested, (list, Mapping)) else payload
        if isinstance(source, list):
            entries = (
                (_text(item.get("id")) if isinstance(item, Mapping) else None, item)
                for item in source
            )
        else:
            entries = ((str(key), value) for key, value in source.items())
    else:
        entries = ()

    result: dict[str, dict[str, Any]] = {}
    for fallback, raw in entries:
        item = _record(raw)
        lab_id = normalize_lab_id(
            _text(item.get("id"))
            or _text(item.get("slug"))
            or fallback
            or _text(item.get("name"))
            or ""
        )
        if lab_id:
            result[lab_id] = item
    return result


def _canonical_index(payload: object) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    nested = payload.get("models") if isinstance(payload, Mapping) else None
    source = nested if isinstance(nested, (list, Mapping)) else payload
    if isinstance(source, Mapping):
        entries: Iterable[tuple[str | None, object]] = (
            (str(key), value) for key, value in source.items()
        )
    elif isinstance(source, list):
        entries = ((None, value) for value in source)
    else:
        entries = ()
    for fallback_id, raw in entries:
        model = _record(raw)
        model_id = _text(model.get("id")) or fallback_id
        if model_id and "/" in model_id:
            model.setdefault("id", model_id)
            result[model_id.lower()] = model
    return result


def _canonical_for_provider_model(
    canonical: Mapping[str, dict[str, Any]],
    *,
    provider_id: str,
    provider_model_id: str,
    provider_model: Mapping[str, Any],
) -> tuple[str, dict[str, Any]]:
    lab_id = normalize_lab_id(provider_id)
    upstream_id = _text(provider_model.get("id")) or provider_model_id
    candidates = [
        upstream_id if "/" in upstream_id else "",
        provider_model_id if "/" in provider_model_id else "",
        f"{lab_id}/{upstream_id}",
        f"{lab_id}/{provider_model_id}",
    ]
    for candidate in candidates:
        if candidate and candidate.lower() in canonical:
            model = canonical[candidate.lower()]
            return _text(model.get("id")) or candidate, model
    canonical_id = f"{lab_id}/{upstream_id}"
    fallback = dict(provider_model)
    fallback["id"] = canonical_id
    return canonical_id, fallback


def _limits(payload: Mapping[str, Any]) -> tuple[int | None, int | None]:
    limit = _record(payload.get("limit"))
    return _positive_int(limit.get("context")), _positive_int(limit.get("output"))


def _modalities(payload: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    modalities = _record(payload.get("modalities"))
    return _string_list(modalities.get("input")), _string_list(modalities.get("output"))


def _capabilities(payload: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in CAPABILITY_KEYS:
        value = payload.get(key)
        if isinstance(value, (bool, str, dict)):
            result[key] = deepcopy(value)
    reasoning_options = payload.get("reasoning_options")
    if isinstance(reasoning_options, list):
        result["reasoning_options"] = deepcopy(reasoning_options)
    return result


def capability_tags(capabilities: Mapping[str, Any]) -> list[str]:
    return [
        key
        for key in CAPABILITY_KEYS
        if capabilities.get(key) is True or (key == "interleaved" and capabilities.get(key))
    ]


def provider_runtime_fields(
    provider_id: str,
    provider_model_id: str,
    *,
    provider_npm: str | None,
    provider_api_url: str | None,
) -> tuple[str, str, str | None]:
    adapter = PROVIDER_ADAPTERS.get(provider_id)
    if adapter is None:
        raise CatalogValidationError(f"Provider 尚无运行适配：{provider_id}")
    if adapter in {"openai", "openai_compatible"}:
        runtime_prefix = "openai"
    elif adapter in {"anthropic", "anthropic_compatible"}:
        runtime_prefix = "anthropic"
    else:
        runtime_prefix = adapter
    base_url = provider_api_url or PROVIDER_API_DEFAULTS.get(provider_id)
    return adapter, f"{runtime_prefix}:{provider_model_id}", base_url


def normalize_models_dev_bundle(
    models_payload: object,
    providers_payload: Mapping[str, Any],
    labs_payload: object,
    *,
    refreshed_at: datetime | None = None,
) -> tuple[list[dict[str, Any]], CatalogRefreshStats]:
    canonical = _canonical_index(models_payload)
    embedded_labs = (
        models_payload.get("labs")
        if isinstance(models_payload, Mapping)
        else None
    )
    labs = _read_labs(embedded_labs) if embedded_labs is not None else {}
    labs.update(_read_labs(labs_payload))
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    now = refreshed_at or datetime.now(timezone.utc)

    provider_items = providers_payload.get("providers")
    if isinstance(provider_items, Mapping):
        providers_payload = provider_items

    for provider_key, provider_raw in providers_payload.items():
        provider = _record(provider_raw)
        provider_id = (_text(provider.get("id")) or str(provider_key)).lower()
        if provider_id not in PROVIDER_ALLOWLIST:
            continue
        provider_models = provider.get("models")
        if not isinstance(provider_models, Mapping):
            raise CatalogValidationError(f"Provider {provider_id} 的 models 不是对象")

        provider_label = _text(provider.get("name")) or provider_id
        provider_npm = _text(provider.get("npm"))
        provider_api_url = _text(provider.get("api"))
        provider_doc_url = _text(provider.get("doc"))

        for model_key, model_raw in provider_models.items():
            provider_model = _record(model_raw)
            provider_model_id = str(model_key).strip()
            if not provider_model_id:
                raise CatalogValidationError(f"Provider {provider_id} 存在空模型 ID")
            if provider_model.get("status") == "deprecated":
                continue

            canonical_id, canonical_model = _canonical_for_provider_model(
                canonical,
                provider_id=provider_id,
                provider_model_id=provider_model_id,
                provider_model=provider_model,
            )
            effective = _deep_merge(canonical_model, provider_model)
            input_modalities, output_modalities = _modalities(effective)
            if "text" not in input_modalities or "text" not in output_modalities:
                continue

            canonical_context, canonical_output = _limits(canonical_model)
            context_window, max_output = _limits(effective)
            context_window = context_window or canonical_context
            max_output = max_output or canonical_output
            canonical_context = canonical_context or context_window
            canonical_output = canonical_output or max_output
            if not all((context_window, max_output, canonical_context, canonical_output)):
                # models.dev also contains image generation and other non-chat
                # products whose token limits are zero or absent. They are not
                # DeepAgent runtime candidates and must not block a full refresh.
                continue
            assert context_window is not None
            assert max_output is not None
            assert canonical_context is not None
            assert canonical_output is not None
            if max_output > context_window or canonical_output > canonical_context:
                raise CatalogValidationError(
                    f"{provider_id}/{provider_model_id} 的最大输出超过上下文窗口"
                )

            lab_id = normalize_lab_id(canonical_id.split("/", 1)[0])
            lab = labs.get(lab_id, {})
            lab_label = (
                _text(lab.get("name"))
                or _text(lab.get("title"))
                or format_lab_label(lab_id)
            )
            lab_description = _text(lab.get("description"))
            model_label = (
                _text(canonical_model.get("name"))
                or _text(effective.get("name"))
                or provider_model_id
            )
            model_description = (
                _text(canonical_model.get("description"))
                or _text(effective.get("description"))
            )
            release_date = _catalog_date(
                canonical_model.get("release_date") or effective.get("release_date"),
                field_name=f"{canonical_id} release_date",
            )
            capabilities = _capabilities(effective)
            model_provider = _record(provider_model.get("provider"))
            effective_npm = _text(model_provider.get("npm")) or provider_npm
            effective_api = _text(model_provider.get("api")) or provider_api_url
            adapter, _runtime_model, effective_api = provider_runtime_fields(
                provider_id,
                provider_model_id,
                provider_npm=effective_npm,
                provider_api_url=effective_api,
            )

            key = (provider_id, provider_model_id)
            if key in seen:
                raise CatalogValidationError(
                    f"模型目录存在重复组合：{provider_id}/{provider_model_id}"
                )
            seen.add(key)
            rows.append(
                {
                    "provider_id": provider_id,
                    "provider_model_id": provider_model_id,
                    "provider_label": provider_label,
                    "provider_npm": effective_npm,
                    "provider_api_url": effective_api,
                    "provider_doc_url": provider_doc_url,
                    "lab_id": lab_id,
                    "lab_label": lab_label,
                    "lab_description": lab_description,
                    "canonical_model_id": canonical_id,
                    "model_label": model_label,
                    "model_description": model_description,
                    "family": _text(canonical_model.get("family"))
                    or _text(effective.get("family")),
                    "release_date": release_date,
                    "canonical_context_window_tokens": canonical_context,
                    "canonical_max_output_tokens": canonical_output,
                    "context_window_tokens": context_window,
                    "max_output_tokens": max_output,
                    "input_modalities": input_modalities,
                    "output_modalities": output_modalities,
                    "capabilities": capabilities,
                    "status": _text(effective.get("status")),
                    "lab_metadata": _without_price(lab),
                    "model_metadata": _without_price(canonical_model),
                    "provider_metadata": _without_price(
                        {
                            "adapter": adapter,
                            "env": provider.get("env"),
                            "doc": provider_doc_url,
                            "provider": model_provider,
                            "reasoning_options": effective.get("reasoning_options"),
                            "experimental": effective.get("experimental"),
                        }
                    ),
                    "source": "models_dev",
                    "refreshed_at": now,
                }
            )

    if not rows:
        raise CatalogValidationError("Provider 白名单过滤后没有可用的文本模型")

    stats = CatalogRefreshStats(
        row_count=len(rows),
        model_count=len({row["canonical_model_id"] for row in rows}),
        provider_count=len({row["provider_id"] for row in rows}),
        lab_count=len({row["lab_id"] for row in rows}),
        refreshed_at=now,
    )
    return rows, stats


def export_models_dev_catalog(rows: Iterable[ModelCatalog]) -> dict[str, Any]:
    """Build a catalog JSON payload that can be imported without losing normalized data."""
    models: dict[str, dict[str, Any]] = {}
    providers: dict[str, dict[str, Any]] = {}
    labs: dict[str, dict[str, Any]] = {}

    for row in sorted(
        rows,
        key=lambda item: (
            item.canonical_model_id,
            item.provider_id,
            item.provider_model_id,
        ),
    ):
        canonical = deepcopy(row.model_metadata or {})
        canonical["id"] = row.canonical_model_id
        canonical.setdefault("name", row.model_label)
        if row.model_description:
            canonical.setdefault("description", row.model_description)
        if row.family:
            canonical.setdefault("family", row.family)
        if row.release_date:
            canonical.setdefault("release_date", row.release_date)
        canonical.setdefault(
            "modalities",
            {
                "input": list(row.input_modalities or []),
                "output": list(row.output_modalities or []),
            },
        )
        canonical.setdefault(
            "limit",
            {
                "context": row.canonical_context_window_tokens,
                "output": row.canonical_max_output_tokens,
            },
        )
        models.setdefault(row.canonical_model_id, canonical)

        provider_metadata = deepcopy(row.provider_metadata or {})
        provider = providers.setdefault(
            row.provider_id,
            {
                "id": row.provider_id,
                "name": row.provider_label,
                "npm": row.provider_npm,
                "api": row.provider_api_url,
                "doc": row.provider_doc_url,
                "env": provider_metadata.get("env"),
                "models": {},
            },
        )
        provider_model: dict[str, Any] = {
            "id": row.canonical_model_id,
            "name": row.model_label,
            "modalities": {
                "input": list(row.input_modalities or []),
                "output": list(row.output_modalities or []),
            },
            "limit": {
                "context": row.context_window_tokens,
                "output": row.max_output_tokens,
            },
            **deepcopy(row.capabilities or {}),
        }
        if row.model_description:
            provider_model["description"] = row.model_description
        if row.family:
            provider_model["family"] = row.family
        if row.release_date:
            provider_model["release_date"] = row.release_date
        if row.status:
            provider_model["status"] = row.status
        model_provider = provider_metadata.get("provider")
        if isinstance(model_provider, Mapping) and model_provider:
            provider_model["provider"] = deepcopy(dict(model_provider))
        for key in ("reasoning_options", "experimental"):
            value = provider_metadata.get(key)
            if value is not None:
                provider_model[key] = deepcopy(value)
        provider["models"][row.provider_model_id] = provider_model

        lab = deepcopy(row.lab_metadata or {})
        lab["id"] = row.lab_id
        lab.setdefault("name", row.lab_label)
        if row.lab_description:
            lab.setdefault("description", row.lab_description)
        labs.setdefault(row.lab_id, lab)

    return {
        "models": models,
        "providers": providers,
        "labs": [labs[key] for key in sorted(labs)],
    }


_CATALOG_PROVIDER_TYPE_ALIASES = {
    "minimax-cn": "minimax",
    "moonshot": "moonshotai",
    "qwen": "alibaba",
    "zhipu": "zai",
    "zhipuai": "zai",
}


def catalog_provider_type(provider_id: str) -> str:
    """Map a models.dev provider code to the explicit local provider type."""
    normalized = provider_id.strip().lower()
    return _CATALOG_PROVIDER_TYPE_ALIASES.get(normalized, normalized)


def _model_code_aliases(value: str) -> set[str]:
    normalized = value.strip().lower()
    if not normalized:
        return set()
    result = {normalized}
    for separator in (":", "/"):
        if separator in normalized:
            suffix = normalized.split(separator, 1)[1]
            if suffix:
                result.add(suffix)
    return result


def _configured_presets(
    models: Iterable[LlmModel],
    providers: Iterable[LlmProvider],
) -> dict[tuple[str, str], str]:
    provider_types = {provider.id: provider.provider_type for provider in providers}
    result: dict[tuple[str, str], str] = {}
    for model in models:
        provider_type = provider_types.get(model.provider_id)
        if not provider_type:
            continue
        for model_code in _model_code_aliases(model.model):
            result.setdefault((provider_type, model_code), model.name)
    return result


def _row_tags(row: ModelCatalog) -> list[str]:
    return capability_tags(row.capabilities or {})


def _row_modalities(values: list[str] | None) -> list[str]:
    """Hide retired values from catalog rows refreshed by older releases."""
    return [value for value in (values or []) if value in MODALITIES]


def _provider_option(
    row: ModelCatalog,
    configured: Mapping[tuple[str, str], str],
) -> dict[str, Any]:
    adapter, runtime_model, base_url = provider_runtime_fields(
        row.provider_id,
        row.provider_model_id,
        provider_npm=row.provider_npm,
        provider_api_url=row.provider_api_url,
    )
    return {
        "provider_id": row.provider_id,
        "provider_label": row.provider_label,
        "provider_model_id": row.provider_model_id,
        "provider_npm": row.provider_npm,
        "provider_api_url": base_url,
        "provider_doc_url": row.provider_doc_url,
        "adapter": adapter,
        "runtime_model": runtime_model,
        "context_window_tokens": row.context_window_tokens,
        "max_output_tokens": row.max_output_tokens,
        "input_modalities": _row_modalities(row.input_modalities),
        "output_modalities": _row_modalities(row.output_modalities),
        "tags": _row_tags(row),
        "status": row.status,
        "configured_preset": next(
            (
                configured[(catalog_provider_type(row.provider_id), model_code)]
                for model_code in _model_code_aliases(runtime_model)
                if (catalog_provider_type(row.provider_id), model_code) in configured
            ),
            None,
        ),
        "metadata": row.provider_metadata or {},
    }


def _matches(
    row: ModelCatalog,
    *,
    q: str,
    lab_id: str,
    provider_id: str,
    modality: str,
) -> bool:
    if lab_id and row.lab_id.lower() != lab_id:
        return False
    if provider_id and row.provider_id.lower() != provider_id:
        return False
    if modality and modality not in {item.lower() for item in _row_modalities(row.input_modalities)}:
        return False
    if not q:
        return True
    haystack = " ".join(
        (
            row.lab_id,
            row.lab_label,
            row.canonical_model_id,
            row.model_label,
            row.provider_id,
            row.provider_label,
            row.provider_model_id,
            row.family or "",
        )
    ).lower()
    return q in haystack


def build_catalog_page(
    rows: list[ModelCatalog],
    llm_models: list[LlmModel],
    llm_providers: list[LlmProvider],
    *,
    view: CatalogView,
    q: str | None,
    lab_id: str | None,
    provider_id: str | None,
    modality: str | None,
    offset: int,
    limit: int,
) -> dict[str, Any]:
    configured = _configured_presets(llm_models, llm_providers)
    q_norm = (q or "").strip().lower()
    lab_norm = normalize_lab_id(lab_id or "")
    provider_norm = (provider_id or "").strip().lower()
    modality_norm = (modality or "").strip().lower()
    filtered = [
        row
        for row in rows
        if _matches(
            row,
            q=q_norm,
            lab_id=lab_norm,
            provider_id=provider_norm,
            modality=modality_norm,
        )
    ]
    facets = {
        "labs": [
            {"id": key, "label": label, "count": len(ids)}
            for key, (label, ids) in sorted(
                {
                    lab: (
                        next(row.lab_label for row in rows if row.lab_id == lab),
                        {row.canonical_model_id for row in rows if row.lab_id == lab},
                    )
                    for lab in {row.lab_id for row in rows}
                }.items(),
                key=lambda item: item[1][0].lower(),
            )
        ],
        "providers": [
            {"id": key, "label": label, "count": len(ids)}
            for key, (label, ids) in sorted(
                {
                    provider: (
                        next(row.provider_label for row in rows if row.provider_id == provider),
                        {row.canonical_model_id for row in rows if row.provider_id == provider},
                    )
                    for provider in {row.provider_id for row in rows}
                }.items(),
                key=lambda item: item[1][0].lower(),
            )
        ],
    }

    if view == "provider":
        grouped: dict[str, list[ModelCatalog]] = defaultdict(list)
        for row in filtered:
            grouped[row.provider_id].append(row)
        items = []
        for key, provider_rows in grouped.items():
            first = provider_rows[0]
            models = [
                {
                    "lab_id": row.lab_id,
                    "lab_label": row.lab_label,
                    "canonical_model_id": row.canonical_model_id,
                    "model_label": row.model_label,
                    "provider_model_id": row.provider_model_id,
                    "release_date": row.release_date,
                    "context_window_tokens": row.context_window_tokens,
                    "max_output_tokens": row.max_output_tokens,
                    "input_modalities": _row_modalities(row.input_modalities),
                    "tags": _row_tags(row),
                }
                for row in sorted(
                    provider_rows,
                    key=lambda item: (
                        _descending_date_key(item.release_date),
                        item.model_label.lower(),
                    ),
                )
            ]
            adapter, _runtime, api_url = provider_runtime_fields(
                first.provider_id,
                first.provider_model_id,
                provider_npm=first.provider_npm,
                provider_api_url=first.provider_api_url,
            )
            items.append(
                {
                    "kind": "provider",
                    "provider_id": key,
                    "provider_label": first.provider_label,
                    "provider_npm": first.provider_npm,
                    "provider_api_url": api_url,
                    "provider_doc_url": first.provider_doc_url,
                    "adapter": adapter,
                    "model_count": len(models),
                    "models": models,
                    "metadata": first.provider_metadata or {},
                }
            )
        items.sort(key=lambda item: item["provider_label"].lower())
    else:
        grouped = defaultdict(list)
        for row in filtered:
            grouped[row.canonical_model_id].append(row)
        items = []
        for canonical_id, model_rows in grouped.items():
            first = model_rows[0]
            providers = [
                _provider_option(row, configured)
                for row in sorted(model_rows, key=lambda item: item.provider_label.lower())
            ]
            items.append(
                {
                    "kind": "model",
                    "lab_id": first.lab_id,
                    "lab_label": first.lab_label,
                    "lab_description": first.lab_description,
                    "canonical_model_id": canonical_id,
                    "model_label": first.model_label,
                    "model_description": first.model_description,
                    "family": first.family,
                    "release_date": first.release_date,
                    "context_window_tokens": first.canonical_context_window_tokens,
                    "max_output_tokens": first.canonical_max_output_tokens,
                    "input_modalities": _row_modalities(first.input_modalities),
                    "output_modalities": _row_modalities(first.output_modalities),
                    "tags": _row_tags(first),
                    "provider_count": len(providers),
                    "providers": providers,
                    "metadata": first.model_metadata or {},
                }
            )
        items.sort(
            key=lambda item: (
                _descending_date_key(item["release_date"]),
                item["lab_label"].lower(),
                item["model_label"].lower(),
            )
        )

    total = len(items)
    page = items[offset : offset + limit]
    refreshed_at = max((row.refreshed_at for row in rows), default=None)
    return {
        "view": view,
        "items": page,
        "total": total,
        "offset": offset,
        "limit": limit,
        "facets": facets,
        "refreshed_at": refreshed_at,
    }
