"""A safe arithmetic calculator tool.

Expressions are parsed with the ``ast`` module and evaluated against a strict
allowlist of node types and operators. ``eval`` is never used, so the tool
cannot execute arbitrary code, access names, or call functions — a deliberate
example of building a tool that is useful *and* safe by construction.
"""

from __future__ import annotations

import ast
import operator
from typing import Any

from forge.tools.base import tool

_BIN_OPS: dict[type[ast.operator], Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS: dict[type[ast.unaryop], Any] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _eval(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 100:
            raise ValueError("exponent too large")
        return float(_BIN_OPS[type(node.op)](left, right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return float(_UNARY_OPS[type(node.op)](_eval(node.operand)))
    raise ValueError(f"unsupported expression element: {type(node).__name__}")


@tool
def calculator(expression: str) -> str:
    """Evaluate a basic arithmetic expression and return the result.

    Supports + - * / // % ** and parentheses over numeric literals only.

    Args:
        expression: The arithmetic expression to evaluate, e.g. "2 + 3 * (4 - 1)".
    """
    try:
        tree = ast.parse(expression, mode="eval")
        result = _eval(tree)
    except (SyntaxError, ValueError, ZeroDivisionError) as exc:
        return f"Error: could not evaluate {expression!r} ({exc})"
    # Present integers without a trailing ".0".
    if result == int(result):
        return str(int(result))
    return str(result)
