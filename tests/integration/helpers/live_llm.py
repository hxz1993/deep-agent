from __future__ import annotations

import os
import unittest


def title_llm_configured() -> bool:
    return bool(os.environ.get("DEEP_AGENT_TITLE_MODEL") and os.environ.get("DEEP_AGENT_TITLE_MODEL_API_KEY"))


def skip_unless_title_llm(test_item):
    return unittest.skipUnless(
        title_llm_configured(),
        "requires DEEP_AGENT_TITLE_MODEL and DEEP_AGENT_TITLE_MODEL_API_KEY",
    )(test_item)
