"""Expose the configured runtime capacity without changing provider behavior."""

from pydantic_ai.models import Model
from pydantic_ai.models.wrapper import WrapperModel


class ContextWindowModel(WrapperModel):
    """Keep SDK context ratios aligned with the persisted model configuration.

    WrapperModel delegates transport, settings, profile and identity unchanged.
    Only the capacity differs: private routes and administrator overrides must
    not silently inherit the public catalog's context window.
    """

    def __init__(self, wrapped: Model | str, context_window_tokens: int):
        if isinstance(context_window_tokens, bool) or context_window_tokens <= 0:
            raise ValueError("context_window_tokens must be positive")
        super().__init__(wrapped)
        self._configured_context_window = context_window_tokens

    @property
    def context_window(self) -> int:
        return self._configured_context_window
