from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.time import utc_now_naive
from app.infrastructure.db.base import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE','DISABLED')", name="users_status_values"),
        CheckConstraint(
            "platform_role IN ('ADMIN','USER')", name="users_platform_role_values"
        ),
        CheckConstraint("auth_version >= 1", name="users_auth_version_positive"),
        Index("ix_users_status_platform_role", "status", "platform_role"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    platform_role: Mapped[str] = mapped_column(String(16), nullable=False, default="USER")
    auth_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now_naive, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utc_now_naive,
        server_default=func.now(),
        onupdate=utc_now_naive,
    )

    sessions: Mapped[list["AuthSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        CheckConstraint("auth_version >= 1", name="auth_sessions_auth_version_positive"),
        CheckConstraint("expires_at > issued_at", name="auth_sessions_expiry_after_issue"),
        Index("ix_auth_sessions_user_revoked_expires", "user_id", "revoked_at", "expires_at"),
    )

    session_digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    auth_version: Mapped[int] = mapped_column(Integer, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped[User] = relationship(back_populates="sessions")


class AuthAdminGuard(Base):
    __tablename__ = "auth_admin_guards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
