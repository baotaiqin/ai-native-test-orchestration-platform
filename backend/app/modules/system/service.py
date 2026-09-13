from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.logging import get_logger
from app.infrastructure.db.session import engine
from app.modules.system.schemas import DatabaseHealth

logger = get_logger(__name__)


def get_database_health() -> DatabaseHealth:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1")).scalar_one()
            database = connection.execute(text("SELECT DATABASE()"))
            return DatabaseHealth(status="ok", database=database.scalar_one_or_none())
    except SQLAlchemyError as exc:
        logger.error(
            "database_readiness_failed",
            extra={
                "event": "DATABASE_READINESS_FAILED",
                "error_type": type(exc).__name__,
            },
        )
        return DatabaseHealth(status="unavailable")
