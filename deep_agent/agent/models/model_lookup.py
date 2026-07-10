"""Shared model key normalization for modality metadata lookup."""

from __future__ import annotations

from urllib.parse import urlparse

_DEFAULT_PROVIDER_PREFIXES = (
    "openai",
    "anthropic",
    "google",
    "deepseek",
    "moonshot",
    "moonshotai",
    "qwen",
    "zhipu",
    "zhipuai",
    "xai",
    "mistral",
)

_PROVIDER_ALIASES: dict[str, tuple[str, ...]] = {
    "moonshot": ("moonshotai", "moonshotai-cn"),
    "zhipu": ("zhipuai",),
    "qwen": ("alibaba",),
}

_BASE_URL_PROVIDER_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("moonshotai", ("moonshot.cn", "moonshot.ai")),
    ("moonshotai-cn", ("moonshot.cn", "moonshot.ai")),
    ("deepseek", ("deepseek.com",)),
    ("openai", ("openai.com",)),
    ("anthropic", ("anthropic.com",)),
    ("google", ("googleapis.com", "generativelanguage.googleapis.com")),
    ("qwen", ("dashscope.aliyuncs.com",)),
    ("zhipuai", ("bigmodel.cn", "zhipuai.cn")),
)


def expand_provider_aliases(provider: str) -> list[str]:
    aliases = [provider]
    aliases.extend(_PROVIDER_ALIASES.get(provider, ()))
    return aliases


def infer_providers_from_base_url(base_url: str | None) -> list[str]:
    if not base_url:
        return []
    host = urlparse(base_url).netloc.lower()
    if not host:
        return []
    providers: list[str] = []
    for provider, fragments in _BASE_URL_PROVIDER_HINTS:
        if any(fragment in host for fragment in fragments):
            providers.append(provider)
    return providers


def build_model_lookup_keys(model: str, base_url: str | None = None) -> list[str]:
    """Build candidate keys for bundle / models.dev lookup.

    Tries multiple formats:
    - Original model string (e.g., 'openai:gpt-4o')
    - With '/' separator (e.g., 'openai/gpt-4o')
    - Bare model name (e.g., 'gpt-4o')
    - Common provider prefixes (e.g., 'openai/gpt-4o')
    - Provider inferred from ``base_url`` (e.g., moonshot.cn -> 'moonshotai/kimi-k2.6')
    """
    candidates: list[str] = []
    seen: set[str] = set()

    def add(key: str) -> None:
        if key and key not in seen:
            seen.add(key)
            candidates.append(key)

    add(model)

    if ":" in model:
        prefix, _, rest = model.partition(":")
        add(f"{prefix}/{rest}")
        add(rest)
    elif "/" in model:
        add(model.split("/", 1)[-1])
    else:
        bare = model
        for prefix in _DEFAULT_PROVIDER_PREFIXES:
            add(f"{prefix}/{bare}")
        for provider in infer_providers_from_base_url(base_url):
            add(f"{provider}/{bare}")

    return candidates
