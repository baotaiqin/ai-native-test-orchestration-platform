import ast
import operator
from typing import Any

from app.core.exceptions import ResourceConflictError

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
    ast.LtE: operator.le,
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


class SafeScriptBudgetExceeded(ResourceConflictError):
    """Deterministic script budget exhaustion, mapped to Preview TIMEOUT."""

    def __init__(self, steps: int, max_steps: int) -> None:
        self.steps = steps
        self.max_steps = max_steps
        super().__init__("Python Script 超过最大计算步骤")


class _SafeEvaluator:
    def __init__(self, context: dict[str, Any], max_steps: int = 500) -> None:
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
            return node.value
        if isinstance(node, ast.Name):
            if node.id in self.names:
                return self.names[node.id]
            raise ResourceConflictError(f"Python Script 名称不可用：{node.id}")
        if isinstance(node, ast.List):
            return [self.expression(item) for item in node.elts]
        if isinstance(node, ast.Tuple):
            return tuple(self.expression(item) for item in node.elts)
        if isinstance(node, ast.Dict):
            return {
                self.expression(key): self.expression(value)
                for key, value in zip(node.keys, node.values, strict=True)
            }
        if isinstance(node, ast.Subscript):
            return self.expression(node.value)[self.expression(node.slice)]
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
            left = self.expression(node.left)
            right = self.expression(node.right)
            result = _BINARY_OPERATORS[type(node.op)](left, right)
            if isinstance(result, (str, list, tuple, dict)) and len(result) > 10000:
                raise ResourceConflictError("Python Script 结果超过大小限制")
            return result
        if isinstance(node, ast.UnaryOp):
            value = self.expression(node.operand)
            if isinstance(node.op, ast.Not):
                return not value
            if isinstance(node.op, ast.USub):
                return -value
            if isinstance(node.op, ast.UAdd):
                return +value
        if isinstance(node, ast.BoolOp):
            values = [self.expression(item) for item in node.values]
            return all(values) if isinstance(node.op, ast.And) else any(values)
        if isinstance(node, ast.Compare):
            left = self.expression(node.left)
            for operation, comparator in zip(node.ops, node.comparators, strict=True):
                right = self.expression(comparator)
                function = _COMPARE_OPERATORS.get(type(operation))
                if function is None or not function(left, right):
                    return False
                left = right
            return True
        if isinstance(node, ast.IfExp):
            branch = node.body if self.expression(node.test) else node.orelse
            return self.expression(branch)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            function = _FUNCTIONS.get(node.func.id)
            if function is None or node.keywords:
                raise ResourceConflictError("Python Script 调用了未授权函数")
            return function(*(self.expression(item) for item in node.args))
        raise ResourceConflictError(
            f"Python Script 包含不支持的表达式：{type(node).__name__}"
        )

    def assign(self, target: ast.expr, value: Any) -> None:
        if isinstance(target, ast.Name):
            if target.id in _FUNCTIONS or target.id == "context":
                raise ResourceConflictError("Python Script 不能覆盖保留名称")
            self.names[target.id] = value
            return
        if (
            isinstance(target, ast.Subscript)
            and isinstance(target.value, ast.Name)
            and target.value.id == "context"
        ):
            key = self.expression(target.slice)
            if not isinstance(key, str) or not key:
                raise ResourceConflictError("Context Key 必须是非空字符串")
            self.context[key] = value
            return
        raise ResourceConflictError("Python Script 只能赋值给局部变量或 context[key]")

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
        raise ResourceConflictError(
            f"Python Script 包含不支持的语句：{type(node).__name__}"
        )


def _validate_expression_shape(node: ast.AST) -> None:
    if isinstance(node, (ast.Constant, ast.Name)):
        return
    if isinstance(node, (ast.List, ast.Tuple)):
        for item in node.elts:
            _validate_expression_shape(item)
        return
    if isinstance(node, ast.Dict):
        for key, value in zip(node.keys, node.values, strict=True):
            if key is not None:
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
            raise ResourceConflictError("Python Script 包含不支持的比较操作")
        return
    if isinstance(node, ast.IfExp):
        _validate_expression_shape(node.test)
        _validate_expression_shape(node.body)
        _validate_expression_shape(node.orelse)
        return
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id not in _FUNCTIONS or node.keywords:
            raise ResourceConflictError("Python Script 调用了未授权函数")
        for argument in node.args:
            _validate_expression_shape(argument)
        return
    raise ResourceConflictError(
        f"Python Script 包含不支持的表达式：{type(node).__name__}"
    )


def validate_safe_script(script: str) -> None:
    if not script.strip() or len(script) > 5000:
        raise ResourceConflictError("Python Script 为空或超过 5000 字符")
    try:
        tree = ast.parse(script, mode="exec")
    except SyntaxError as exc:
        raise ResourceConflictError("Python Script 语法错误") from exc
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
        raise ResourceConflictError(
            f"Python Script 包含不支持的语句：{type(statement).__name__}"
        )

    for statement in tree.body:
        validate_statement(statement)


def run_safe_script(
    script: str, context: dict[str, Any], *, max_steps: int = 500
) -> dict[str, Any]:
    if not script.strip() or len(script) > 5000:
        raise ResourceConflictError("Python Script 为空或超过 5000 字符")
    try:
        tree = ast.parse(script, mode="exec")
    except SyntaxError as exc:
        raise ResourceConflictError("Python Script 语法错误") from exc
    evaluator = _SafeEvaluator(context, max_steps=max_steps)
    for statement in tree.body:
        evaluator.statement(statement)
    return context
