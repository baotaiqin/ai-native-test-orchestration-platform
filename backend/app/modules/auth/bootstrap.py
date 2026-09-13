import argparse
import json
from collections.abc import Sequence

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.infrastructure.db.session import SessionLocal
from app.modules.auth.service import bootstrap_development_admin


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="显式初始化历史 dev-admin 平台管理员（不会覆盖既有用户）"
    )
    parser.add_argument(
        "--confirm-bootstrap",
        action="store_true",
        help="确认只执行一次安全初始化",
    )
    parser.add_argument("--display-name", default="开发管理员")
    args = parser.parse_args(argv)
    if not args.confirm_bootstrap:
        parser.error("必须显式提供 --confirm-bootstrap")

    settings = get_settings()
    with SessionLocal() as session:
        try:
            result = bootstrap_development_admin(
                session,
                username=settings.dev_admin_username,
                password=settings.dev_admin_password,
                display_name=args.display_name,
            )
        except (AppError, ValueError) as exc:
            message = exc.message if isinstance(exc, AppError) else str(exc)
            print(json.dumps({"status": "ERROR", "message": message}, ensure_ascii=False))
            return 1
    print(
        json.dumps(
            {
                "status": result.status,
                "user_id": result.user_id,
                "username": result.username,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
