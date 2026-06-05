"""Example skill: a tiny safe arithmetic calculator.

This demonstrates the skill authoring pattern:
  1. Create a FunctionToolset (carry usage guidance via `instructions`).
  2. Register tools on it.
  3. Expose a module-level `SKILL = Skill(...)`.

The loader picks it up automatically; enabling capability "skill:calculator"
injects the instructions and exposes the tools.
"""

from __future__ import annotations

import ast
import operator

from pydantic_ai import FunctionToolset

from app.capabilities.registry import ToolInfo
from app.capabilities.skills.base import Skill
from app.core.logging import get_logger

_toolset = FunctionToolset(
    instructions="For any arithmetic, call the calculate tool instead of computing in your head."
)
logger = get_logger(__name__)

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.FloorDiv: operator.floordiv,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _eval(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        return _BIN_OPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_eval(node.operand))
    raise ValueError("Unsupported or unsafe expression")


@_toolset.tool_plain
def calculate(expression: str) -> float:
    """Safely evaluate an arithmetic expression, e.g. "2 * (3 + 4) ** 2"."""
    logger.info(f"Calculating expression: {expression}")
    return _eval(ast.parse(expression, mode="eval").body)


SKILL = Skill(
    name="calculator",
    description="Safe arithmetic expression evaluation.",
    toolset=_toolset,
    tools=[ToolInfo("calculate", "Evaluate an arithmetic expression.")],
)
