import os
from collections.abc import Generator

# 测试进程内默认关闭 Runner 断线自动收口后台线程，避免任何 TestClient(app)
# 的 lifespan 启动真实协调器并触碰真实 MySQL/Redis。专项测试通过
# dependency_overrides 注入自己的协调器。
os.environ.setdefault("APP_RUNNER_DISCONNECT_SCAN_ENABLED", "false")
os.environ.setdefault("APP_SCHEDULE_SCAN_ENABLED", "false")
os.environ.setdefault("APP_WEBHOOK_SCAN_ENABLED", "false")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.api.deps import get_db_session  # noqa: E402
from app.main import app  # noqa: E402
from tests.auth_helpers import install_test_auth, uninstall_test_auth  # noqa: E402


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    install_test_auth(engine)

    def override_db() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_db_session] = override_db
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        uninstall_test_auth(engine)
        engine.dispose()
