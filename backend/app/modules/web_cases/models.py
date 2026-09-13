from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.time import utc_now_naive
from app.infrastructure.db.base import Base


class WebCase(Base):
    __tablename__ = "web_cases"
    __table_args__ = (
        UniqueConstraint("project_id", "code", name="uq_web_cases_project_code"),
        CheckConstraint(
            "status IN ('DRAFT','APPROVED','ARCHIVED')", name="web_cases_status_values"
        ),
        Index("ix_web_cases_project_status_updated", "project_id", "status", "updated_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT")
    current_version_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "web_case_versions.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_web_cases_current_version_id_web_case_versions",
        ),
        nullable=True,
    )
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive
    )

    versions: Mapped[list["WebCaseVersion"]] = relationship(
        back_populates="web_case",
        cascade="all, delete-orphan",
        order_by="WebCaseVersion.version_no",
        foreign_keys="WebCaseVersion.web_case_id",
    )


class WebCaseVersion(Base):
    __tablename__ = "web_case_versions"
    __table_args__ = (
        UniqueConstraint("web_case_id", "version_no", name="uq_web_case_versions_case_version"),
        CheckConstraint("version_no > 0", name="web_case_versions_version_positive"),
        CheckConstraint(
            "status IN ('DRAFT','APPROVED','RETIRED')",
            name="web_case_versions_status_values",
        ),
        CheckConstraint(
            "status <> 'APPROVED' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)",
            name="web_case_versions_approval_metadata_required",
        ),
        Index("ix_web_case_versions_case_created", "web_case_id", "created_at"),
        Index("ix_web_case_versions_case_status", "web_case_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    web_case_id: Mapped[int] = mapped_column(
        ForeignKey("web_cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[dict] = mapped_column(JSON, nullable=False)
    change_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now_naive)

    web_case: Mapped[WebCase] = relationship(
        back_populates="versions", foreign_keys=[web_case_id]
    )


class WebPage(Base):
    __tablename__ = "web_pages"
    __table_args__ = (
        UniqueConstraint("project_id", "code", name="uq_web_pages_project_code"),
        CheckConstraint("status IN ('ACTIVE','ARCHIVED')", name="web_pages_status_values"),
        Index("ix_web_pages_project_status", "project_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    url_pattern: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive
    )

    elements: Mapped[list["WebElement"]] = relationship(
        back_populates="page", cascade="all, delete-orphan", order_by="WebElement.id"
    )


class WebElement(Base):
    __tablename__ = "web_elements"
    __table_args__ = (
        UniqueConstraint("page_id", "name", name="uq_web_elements_page_name"),
        CheckConstraint("status IN ('ACTIVE','ARCHIVED')", name="web_elements_status_values"),
        Index("ix_web_elements_project_status", "project_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    page_id: Mapped[int] = mapped_column(
        ForeignKey("web_pages.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    element_type: Mapped[str] = mapped_column(String(32), nullable=False, default="OTHER")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    current_version_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(
            "web_element_versions.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_web_elements_current_version_id_web_element_versions",
        ),
        nullable=True,
    )
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive
    )

    page: Mapped[WebPage] = relationship(back_populates="elements")
    versions: Mapped[list["WebElementVersion"]] = relationship(
        back_populates="element",
        cascade="all, delete-orphan",
        order_by="WebElementVersion.version_no",
        foreign_keys="WebElementVersion.element_id",
    )


class WebElementVersion(Base):
    __tablename__ = "web_element_versions"
    __table_args__ = (
        UniqueConstraint(
            "element_id", "version_no", name="uq_web_element_versions_element_version"
        ),
        CheckConstraint("version_no > 0", name="web_element_versions_version_positive"),
        Index("ix_web_element_versions_element_created", "element_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    element_id: Mapped[int] = mapped_column(
        ForeignKey("web_elements.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    element_type: Mapped[str] = mapped_column(String(32), nullable=False, default="OTHER")
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now_naive)

    element: Mapped[WebElement] = relationship(
        back_populates="versions", foreign_keys=[element_id]
    )
    locators: Mapped[list["WebElementLocator"]] = relationship(
        back_populates="element_version",
        cascade="all, delete-orphan",
        order_by="WebElementLocator.priority",
    )


class WebElementLocator(Base):
    __tablename__ = "element_locators"
    __table_args__ = (
        UniqueConstraint(
            "element_version_id", "priority", name="uq_element_locators_version_priority"
        ),
        CheckConstraint("priority > 0 AND priority <= 20", name="element_locators_priority_bounds"),
        CheckConstraint(
            "strategy IN ('css','xpath','text','role','label','placeholder','test_id')",
            name="element_locators_strategy_values",
        ),
        CheckConstraint(
            "source IN ('MANUAL','IMPORTED','HEALED')", name="element_locators_source_values"
        ),
        Index("ix_element_locators_version_priority", "element_version_id", "priority"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    element_version_id: Mapped[int] = mapped_column(
        ForeignKey("web_element_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    strategy: Mapped[str] = mapped_column(String(32), nullable=False)
    value: Mapped[str] = mapped_column(String(2000), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="MANUAL")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now_naive)

    element_version: Mapped[WebElementVersion] = relationship(back_populates="locators")


class SessionProfile(Base):
    __tablename__ = "session_profiles"
    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_session_profiles_project_name"),
        CheckConstraint("status IN ('ACTIVE','ARCHIVED')", name="session_profiles_status_values"),
        CheckConstraint(
            "expires_at IS NULL OR expires_at > created_at",
            name="session_profiles_expiry_after_create",
        ),
        Index(
            "ix_session_profiles_project_environment_status",
            "project_id",
            "environment_id",
            "status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    environment_id: Mapped[int | None] = mapped_column(
        ForeignKey("environments.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    storage_state_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    storage_state_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    refresh_ttl_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=86400)
    profile_metadata: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    login_web_case_id: Mapped[int | None] = mapped_column(
        ForeignKey("web_cases.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    login_web_case_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("web_case_versions.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    expiry_condition: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    success_condition: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive
    )
