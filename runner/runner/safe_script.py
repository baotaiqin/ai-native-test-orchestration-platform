"""Small AST-only evaluator for the reviewed Scenario Python Script subset.

This module never compiles or executes user supplied Python.  It parses the
script and evaluates a deliberately small expression/statement vocabulary.
"""

from __future__ import annotations

import ast
import json
import operator
from typing import Any

MAX_SCRIPT_CHARS = 5_000
MAX_AST_NODES = 1_000
MAX_CONTEXT_KEYS = 200
MAX_CONTEXT_BYTES = 64_000
MAX_VALUE_ITEMS = 10_000

_BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
}
_COMPARE_OPERATORS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Lt: operator.lt,
    ast.In: lambda left, right: left in right,
    ast.NotIn: lambda left, right: left not in right,
}
_FUNCTIONS = {
    "len": len,
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "min": min,
    "max": max,
    "sum": sum,
    "round": round,
}


class SafeScriptError(Exception):
    """Stable, non-sensitive error boundary for script validation/evaluation."""


class SafeScriptBudgetExceeded(SafeScriptError):
    def __init__(self, steps: int, max_steps: int) -> None:
        self.steps = steps
        self.max_steps = max_steps
        super().__init__("Python Script 超过受限计算预算")


def _check_json_size(value: Any) -> None:
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise SafeScriptError("Python Script 结果不是 JSON 安全值") from exc
    if len(encoded.encode("utf-8")) > MAX_CONTEXT_BYTES:
        raise SafeScriptError("Python Script 结果超过大小限制")


def _check_sequence_growth(left: Any, right: Any) -> None:
    if isinstance(left, (str, list, tuple)) and isinstance(right, int) and right >= 0:
        if len(left) * right > MAX_VALUE_ITEMS:
            raise SafeScriptError("Python Script 结果超过大小限制")
    if isinstance(right, (str, list, tuple)) and isinstance(left, int) and left >= 0:
        if len(right) * left > MAX_VALUE_ITEMS:
            raise SafeScriptError("Python Script 结果超过大小限制")


class _SafeEvaluator:
    def __init__(self, context: dict[str, Any], max_steps: int) -> None:
        self.context = context
        self.names: dict[str, Any] = {"context": context}
        self.steps = 0
        self.max_steps = max_steps

    def tick(self) -> None:
        self.steps += 1
        if self.steps > self.max_steps:
            raise SafeScriptBudgetExceeded(self.steps, self.max_steps)

    def expression(self, node: ast.AST) -> Any:
        self.tick()
        if isinstance(node, ast.Constant):
            if not isinstance(node.value, (str, int, float, bool, type(None))):
                raise SafeScriptError("Python Script 常量类型不受支持")
            return node.value
        if isinstance(node, ast.Name):
            if node.id in self.names:
                return self.names[node.id]
            raise SafeScriptError("Python Script 名称不可用")
        if isinstance(node, ast.List):
            return [self.expression(item) for item in node.elts]
        if isinstance(node, ast.Tuple):
            return tuple(self.expression(item) for item in node.elts)
        if isinstance(node, ast.Dict):
            if any(key is None for key in node.keys):
                raise SafeScriptError("Python Script 不支持字典展开")
            result = {
                self.expression(key): self.expression(value)
                for key, value in zip(node.keys, node.values, strict=True)
            }
            _check_json_size(result)
            return result
        if isinstance(node, ast.Subscript):
            try:
                return self.expression(node.value)[self.expression(node.slice)]
            except (IndexError, KeyError, TypeError) as exc:
                raise SafeScriptError("Python Script 下标访问失败") from exc
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
            left = self.expression(node.left)
            right = self.expression(node.right)
            _check_sequence_growth(left, right)
            try:
                result = _BINARY_OPERATORS[type(node.op)](left, right)
            except (ArithmeticError, TypeError, ValueError) as exc:
                raise SafeScriptError("Python Script 运算失败") from exc
            _check_json_size(result)
            return result
        if isinstance(node, ast.UnaryOp):
            value = self.expression(node.operand)
            try:
                if isinstance(node.op, ast.Not):
                    return not value
                if isinstance(node.op, ast.USub):
                    return -value
                if isinstance(node.op, ast.UAdd):
                    return +value
            except (ArithmeticError, TypeError, ValueError) as exc:
                raise SafeScriptError("Python Script 运算失败") from exc
        if isinstance(node, ast.BoolOp):
            values = [self.expression(item) for item in node.values]
            if isinstance(node.op, ast.And):
                return all(values)
            if isinstance(node.op, ast.Or):
                return any(values)
        if isinstance(node, ast.Compare):
            left = self.expression(node.left)
            for operation, comparator in zip(node.ops, node.comparators, strict=True):
                right = self.expression(comparator)
                function = _COMPARE_OPERATORS.get(type(operation))
                if function is None:
                    raise SafeScriptError("Python Script 比较操作不受支持")
                try:
                    passed = function(left, right)
                except (TypeError, ValueError) as exc:
                    raise SafeScriptError("Python Script 比较失败") from exc
                if not passed:
                    return False
                left = right
            return True
        if isinstance(node, ast.IfExp):
            branch = node.body if self.expression(node.test) else node.orelse
            return self.expression(branch)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            function = _FUNCTIONS.get(node.func.id)
            if function is None or node.keywords:
                raise SafeScriptError("Python Script 调用了未授权函数")
            try:
                result = function(*(self.expression(item) for item in node.args))
            except (ArithmeticError, TypeError, ValueError) as exc:
                raise SafeScriptError("Python Script 函数调用失败") from exc
            _check_json_size(result)
            return result
        raise SafeScriptError("Python Script 表达式不受支持")

    def assign(self, target: ast.expr, value: Any) -> None:
        if isinstance(target, ast.Name):
            if target.id in _FUNCTIONS or target.id == "context":
                raise SafeScriptError("Python Script 不能覆盖保留名称")
            self.names[target.id] = value
            return
        if (
            isinstance(target, ast.Subscript)
            and isinstance(target.value, ast.Name)
            and target.value.id == "context"
        ):
            key = self.expression(target.slice)
            if not isinstance(key, str) or not key or len(key) > 128:
                raise SafeScriptError("Context Key 无效")
            if key not in self.context and len(self.context) >= MAX_CONTEXT_KEYS:
                raise SafeScriptError("Python Script Context 变量过多")
            self.context[key] = value
            _check_json_size(self.context)
            return
        raise SafeScriptError("Python Script 只能赋值给局部变量或 context[key]")

    def statement(self, node: ast.stmt) -> None:
        self.tick()
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            self.assign(node.targets[0], self.expression(node.value))
            return
        if isinstance(node, ast.If):
            branch = node.body if self.expression(node.test) else node.orelse
            for statement in branch:
                self.statement(statement)
            return
        if isinstance(node, ast.Expr):
            self.expression(node.value)
            return
        raise SafeScriptError("Python Script 语句不受支持")


def _validate_expression_shape(node: ast.AST) -> None:
    if isinstance(node, (ast.Constant, ast.Name)):
        return
    if isinstance(node, (ast.List, ast.Tuple)):
        for item in node.elts:
            _validate_expression_shape(item)
        return
    if isinstance(node, ast.Dict):
        for key, value in zip(node.keys, node.values, strict=True):
            if key is None:
                raise SafeScriptError("Python Script 不支持字典展开")
            _validate_expression_shape(key)
            _validate_expression_shape(value)
        return
    if isinstance(node, ast.Subscript):
        _validate_expression_shape(node.value)
        _validate_expression_shape(node.slice)
        return
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
        _validate_expression_shape(node.left)
        _validate_expression_shape(node.right)
        return
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.Not, ast.USub, ast.UAdd)):
        _validate_expression_shape(node.operand)
        return
    if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
        for value in node.values:
            _validate_expression_shape(value)
        return
    if isinstance(node, ast.Compare):
        _validate_expression_shape(node.left)
        for comparator in node.comparators:
            _validate_expression_shape(comparator)
        if any(type(operation) not in _COMPARE_OPERATORS for operation in node.ops):
            raise SafeScriptError("Python Script 比较操作不受支持")
        return
    if isinstance(node, ast.IfExp):
        _validate_expression_shape(node.test)
        _validate_expression_shape(node.body)
        _validate_expression_shape(node.orelse)
        return
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id not in _FUNCTIONS or node.keywords:
            raise SafeScriptError("Python Script 调用了未授权函数")
        for argument in node.args:
            _validate_expression_shape(argument)
        return
    raise SafeScriptError("Python Script 包含不支持的语法")


def _parse(script: str) -> ast.Module:
    if not isinstance(script, str) or not script.strip() or len(script) > MAX_SCRIPT_CHARS:
        raise SafeScriptError("Python Script 为空或超过 5000 字符")
    try:
        tree = ast.parse(script, mode="exec")
    except SyntaxError as exc:
        raise SafeScriptError("Python Script 语法错误") from exc
    if sum(1 for _ in ast.walk(tree)) > MAX_AST_NODES:
        raise SafeScriptError("Python Script 结构超过安全上限")
    return tree


def validate_safe_script(script: str) -> None:
    tree = _parse(script)

    def validate_statement(statement: ast.stmt) -> None:
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            _validate_expression_shape(statement.value)
            return
        if isinstance(statement, ast.If):
            _validate_expression_shape(statement.test)
            for child in [*statement.body, *statement.orelse]:
                validate_statement(child)
            return
        if isinstance(statement, ast.Expr):
            _validate_expression_shape(statement.value)
            return
        raise SafeScriptError("Python Script 语句不受支持")

    for statement in tree.body:
        validate_statement(statement)


def run_safe_script(
    script: str, context: dict[str, Any], *, max_steps: int = 500
) -> dict[str, Any]:
    if not isinstance(context, dict) or len(context) > MAX_CONTEXT_KEYS:
        raise SafeScriptError("Python Script Context 无效")
    _check_json_size(context)
    validate_safe_script(script)
    evaluator = _SafeEvaluator(context, max(1, min(max_steps, 500)))
    tree = _parse(script)
    for statement in tree.body:
        evaluator.statement(statement)
    _check_json_size(context)
    return context
