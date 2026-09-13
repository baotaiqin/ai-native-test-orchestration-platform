from app.infrastructure.db.session import engine_options


def test_mysql_platform_connections_pin_session_timezone_to_utc() -> None:
    assert engine_options("mysql+pymysql://user:pass@db/platform") == {
        "pool_pre_ping": True,
        "connect_args": {"init_command": "SET time_zone = '+00:00'"},
    }
    assert engine_options("sqlite://") == {"pool_pre_ping": True}
