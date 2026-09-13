from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.db.base import Base


class Runner(Base):
    __tablename__ = "runners"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE','REVOKED')",
            name="runners_status_values",
        ),
        Index("ix_runners_status_last_heartbeat", "status", "last_heartbeat_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    os: Mapped[str | None] = mapped_column(String(128), nullable=True)
    cpu: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ram: Mapped[str | None] = mapped_column(String(128), nullable=True)
    disk: Mapped[str | None] = mapped_column(String(128), nullable=True)
    python_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chrome_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    playwright_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    java_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    jmeter_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    credential_digest: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    heartbeat_interval_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=30, server_default="30"
    )
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    tags: Mapped[list["RunnerTag"]] = relationship(
        back_populates="runner", cascade="all, delete-orphan"
    )
    capabilities: Mapped[list["RunnerCapability"]] = relationship(
        back_populates="runner", cascade="all, delete-orphan"
    )
    slots: Mapped[list["RunnerSlot"]] = relationship(
        back_populates="runner", cascade="all, delete-orphan"
    )


class RunnerRegistrationToken(Base):
    __tablename__ = "runner_registration_tokens"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE','CONSUMED','REVOKED','EXPIRED')",
            name="runner_registration_tokens_status_values",
        ),
        Index("ix_runner_registration_tokens_status_expires_at", "status", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token_digest: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    consumed_runner_id: Mapped[str | None] = mapped_column(
        ForeignKey("runners.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    consumed_runner: Mapped[Runner | None] = relationship(foreign_keys=[consumed_runner_id])


class RunnerTag(Base):
    __tablename__ = "runner_tags"
    __table_args__ = (Index("ix_runner_tags_tag", "tag"),)

    runner_id: Mapped[str] = mapped_column(
        ForeignKey("runners.id", ondelete="CASCADE"), primary_key=True
    )
    tag: Mapped[str] = mapped_column(String(64), primary_key=True)

    runner: Mapped[Runner] = relationship(back_populates="tags")


class RunnerCapability(Base):
    __tablename__ = "runner_capabilities"
    __table_args__ = (
        CheckConstraint(
            "capability IN ('API','WEB','SQL','SCRIPT','SSE','JMETER')",
            name="runner_capabilities_name_values",
        ),
        CheckConstraint(
            "status IN ('READY','UNAVAILABLE')",
            name="runner_capabilities_status_values",
        ),
        Index("ix_runner_capabilities_status", "status"),
    )

    runner_id: Mapped[str] = mapped_column(
        ForeignKey("runners.id", ondelete="CASCADE"), primary_key=True
    )
    capability: Mapped[str] = mapped_column(String(16), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(256), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    runner: Mapped[Runner] = relationship(back_populates="capabilities")


class RunnerSlot(Base):
    __tablename__ = "runner_slots"
    __table_args__ = (
        CheckConstraint(
            "slot_type IN ('API','WEB','PERFORMANCE')",
            name="runner_slots_type_values",
        ),
        CheckConstraint("total >= 0", name="runner_slots_total_nonnegative"),
        CheckConstraint(
            "available >= 0 AND available <= total",
            name="runner_slots_available_bounds",
        ),
    )

    runner_id: Mapped[str] = mapped_column(
        ForeignKey("runners.id", ondelete="CASCADE"), primary_key=True
    )
    slot_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    total: Mapped[int] = mapped_column(Integer, nullable=False)
    available: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    runner: Mapped[Runner] = relationship(back_populates="slots")
