from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic_ai.tools import ToolDefinition

from deep_agent.agent.context.estimator import (
    TOOL_DEFINITION_OVERHEAD_TOKENS,
    TokenEstimator,
)
from deep_agent.agent.tool_discovery import (
    build_search_documents,
    rank_tool_documents,
)


@dataclass(frozen=True)
class ShadowReport:
    case_count: int
    recall_at_3: float
    recall_at_5: float
    forbidden_at_3: int
    estimated_schema_reduction: float


def load_fixture(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _definitions(payload: dict[str, Any]) -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name=item["name"],
            description=item.get("description"),
            parameters_json_schema={
                "type": "object",
                "properties": {
                    name: {"type": "string", "description": description}
                    for name, description in item.get("parameters", {}).items()
                },
            },
        )
        for item in payload["tools"]
    ]


def evaluate_fixture(path: Path) -> ShadowReport:
    payload = load_fixture(path)
    definitions = _definitions(payload)
    documents = build_search_documents(definitions, server_names=["deepfos"])
    required_hits_3 = 0
    required_hits_5 = 0
    required_total = 0
    forbidden_hits = 0
    for case in payload["cases"]:
        ranked_5 = rank_tool_documents(case["query"], documents, limit=5)
        ranked_3 = ranked_5[:3]
        required = set(case["required"])
        forbidden = set(case.get("forbidden", []))
        required_total += len(required)
        required_hits_3 += len(required & set(ranked_3))
        required_hits_5 += len(required & set(ranked_5))
        forbidden_hits += len(forbidden & set(ranked_3))

    estimator = TokenEstimator.conservative()
    tool_tokens = [
        TOOL_DEFINITION_OVERHEAD_TOKENS + estimator.text(tool)
        for tool in definitions
    ]
    full_tokens = sum(tool_tokens)
    disclosed_tokens = sum(sorted(tool_tokens, reverse=True)[:3])
    return ShadowReport(
        case_count=len(payload["cases"]),
        recall_at_3=required_hits_3 / max(1, required_total),
        recall_at_5=required_hits_5 / max(1, required_total),
        forbidden_at_3=forbidden_hits,
        estimated_schema_reduction=1 - disclosed_tokens / max(1, full_tokens),
    )


if __name__ == "__main__":
    fixture = Path(__file__).parents[1] / "fixtures" / "tool_discovery_shadow.json"
    print(json.dumps(evaluate_fixture(fixture).__dict__, ensure_ascii=False, indent=2))
