from runner.cli import main
from runner.config import default_tags
from runner.snapshot import collect_snapshot


def capability_snapshot() -> dict[str, object]:
    """兼容旧入口，返回可直接发送给后端的脱敏快照。"""

    return collect_snapshot("runner", tags=default_tags())


if __name__ == "__main__":
    raise SystemExit(main())
