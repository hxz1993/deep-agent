"""Request-scoped model HTTP settings; SDKs retain their client ownership."""
from pydantic_ai.settings import ModelSettings


def model_http_settings(settings: ModelSettings | None = None) -> ModelSettings:
    # httpx2 2.7's Python <3.14 zstd adapter shares one ZstdDecompressor
    # across response decoders. Concurrent title/main responses can corrupt
    # each other's context. Negotiate uncompressed model responses until that
    # dependency is fixed; never patch a global decoder or retry a partial Run.
    result: ModelSettings = dict(settings or {})
    headers = {key: value for key, value in result.get("extra_headers", {}).items()
               if key.lower() != "accept-encoding"}
    result["extra_headers"] = {**headers, "Accept-Encoding": "identity"}
    return result
