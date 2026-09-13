from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings

settings = get_settings()


def engine_options(database_url: str) -> dict[str, object]:
    options: dict[str, object] = {"pool_pre_ping": True}
    if database_url.lower().startswith("mysql+pymysql://"):
        # MySQL DATETIME has no timezone. Pin every platform connection to UTC so
        # legacy server_default=NOW() columns and application UTC-naive values use
        # the same persistence basis regardless of the host timezone.
        options["connect_args"] = {"init_command": "SET time_zone = '+00:00'"}
    return options


engine = create_engine(settings.database_url, **engine_options(settings.database_url))
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
