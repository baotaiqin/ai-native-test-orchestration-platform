import re

from app.core.exceptions import ResourceConflictError

_COMMENT_MARKERS = ("--", "#", "/*", "*/")
_DANGEROUS_PATTERNS = (
    r"\bINTO\s+OUTFILE\b",
    r"\bINTO\s+DUMPFILE\b",
    r"\bLOAD_FILE\b",
    r"\bSLEEP\s*\(",
    r"\bBENCHMARK\s*\(",
    r"\bGET_LOCK\s*\(",
    r"\bRELEASE_LOCK\s*\(",
    r"\bLOAD\s+DATA\b",
    r"\bCALL\b",
    r"\bGRANT\b",
    r"\bREVOKE\b",
)
_TRANSACTION_WORDS = (
    r"\b(BEGIN|START\s+TRANSACTION|COMMIT|ROLLBACK|SAVEPOINT|RELEASE\s+SAVEPOINT)\b"
)


def _normalized_sql(statement: str) -> tuple[str, str]:
    if not isinstance(statement, str):
        raise ResourceConflictError("SQL Cleanup 语句必须是字符串")
    statement = statement.strip()
    if "{{" in statement or "}}" in statement:
        raise ResourceConflictError(
            "SQL Cleanup 不允许在 SQL 文本中使用 Runtime 模板，请使用 params"
        )
    if any(marker in statement for marker in _COMMENT_MARKERS):
        raise ResourceConflictError("SQL Cleanup 不允许使用 SQL 注释语法")
    if statement.endswith(";"):
        statement = statement[:-1].strip()
    if not statement or ";" in statement:
        raise ResourceConflictError("SQL Cleanup 仅允许单条 SQL")
    normalized = re.sub(r"\s+", " ", statement).upper()
    if re.search(_TRANSACTION_WORDS, normalized):
        raise ResourceConflictError("SQL Cleanup 不允许事务控制语句")
    if any(re.search(pattern, normalized) for pattern in _DANGEROUS_PATTERNS):
        raise ResourceConflictError("SQL Cleanup 包含禁止的文件、系统或锁操作")
    return statement, normalized


def _validate_mutation(statement: str, allowed: set[str], label: str) -> str:
    statement, normalized = _normalized_sql(statement)
    keyword = normalized.split(" ", 1)[0]
    if keyword not in allowed:
        raise ResourceConflictError(f"{label} 仅允许 {', '.join(sorted(allowed))}")
    if re.search(r"\b(ALTER|CREATE|DROP|TRUNCATE|RENAME|GRANT|REVOKE|USE)\b", normalized):
        raise ResourceConflictError(f"{label} 禁止 DDL、权限或会话控制语句")
    return statement


def validate_sql_cleanup(statement: str) -> str:
    return _validate_mutation(statement, {"UPDATE", "DELETE"}, "SQL Cleanup")


def validate_sql_execute(statement: str) -> str:
    return _validate_mutation(statement, {"INSERT", "UPDATE", "DELETE"}, "SQL Execute")


def validate_sql_query(statement: str) -> str:
    statement, normalized = _normalized_sql(statement)
    if not re.match(r"^(SELECT|WITH|SHOW|DESCRIBE|EXPLAIN)\b", normalized):
        raise ResourceConflictError("SQL_QUERY 仅允许只读查询")
    if normalized.startswith("WITH") and re.search(
        r"\b(INSERT|UPDATE|DELETE|REPLACE|ALTER|DROP|TRUNCATE|CREATE)\b", normalized
    ):
        raise ResourceConflictError("SQL_QUERY 仅允许只读查询")
    return statement
