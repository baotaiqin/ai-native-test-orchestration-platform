from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class RequirementType(StrEnum):
    FEATURE = "FEATURE"
    RULE = "RULE"
    ACCEPTANCE_CRITERIA = "ACCEPTANCE_CRITERIA"
    SECTION = "SECTION"


class RequirementStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class RequirementVerificationType(StrEnum):
    AUTO = "AUTO"
    API = "API"
    WEB = "WEB"
    PERFORMANCE = "PERFORMANCE"
    PLATFORM = "PLATFORM"
    MANUAL = "MANUAL"


class RequirementAutomationReadiness(StrEnum):
    READY = "READY"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    MANUAL_ONLY = "MANUAL_ONLY"


class SourceType(StrEnum):
    MANUAL = "MANUAL"
    MARKDOWN = "MARKDOWN"


class RequirementCreate(BaseModel):
    project_id: int = Field(gt=0)
    parent_id: int | None = Field(default=None, gt=0)
    title: str = Field(min_length=2, max_length=255)
    type: RequirementType = RequirementType.FEATURE
    verification_type: RequirementVerificationType = RequirementVerificationType.AUTO
    automation_readiness: RequirementAutomationReadiness = (
        RequirementAutomationReadiness.READY
    )
    markdown_content: str = Field(default="", max_length=200000)


class RequirementVersionCreate(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=255)
    markdown_content: str = Field(max_length=200000)
    change_summary: str | None = Field(default=None, max_length=500)


class RequirementVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    requirement_id: int
    version_no: int
    markdown_content: str
    content_hash: str
    source_type: SourceType
    source_filename: str | None
    change_summary: str | None
    created_by: str
    created_at: datetime


class RequirementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    parent_id: int | None
    code: str
    title: str
    type: RequirementType
    verification_type: RequirementVerificationType
    automation_readiness: RequirementAutomationReadiness
    order_index: int
    status: RequirementStatus
    current_version_id: int | None
    created_by: str
    created_at: datetime
    updated_at: datetime
    current_version: RequirementVersionResponse | None = None


class RequirementTreeNode(RequirementResponse):
    outline_number: str = ""
    children: list["RequirementTreeNode"] = Field(default_factory=list)


class RequirementTreeResponse(BaseModel):
    items: list[RequirementTreeNode]
    total: int
    current_document_version: "RequirementDocumentVersionResponse | None" = None


class MarkdownImportRequest(BaseModel):
    project_id: int = Field(gt=0)
    filename: str | None = Field(default=None, max_length=255)
    content: str = Field(min_length=1, max_length=1000000)


class MarkdownPreviewNode(BaseModel):
    temp_id: str
    parent_temp_id: str | None
    title: str
    level: int = Field(ge=1, le=6)
    markdown_content: str
    order_index: int


class MarkdownPreviewResponse(BaseModel):
    filename: str | None
    nodes: list[MarkdownPreviewNode]
    total: int


class RequirementImportResponse(BaseModel):
    root_items: list[RequirementTreeNode]
    created_count: int
    document_version: "RequirementDocumentVersionResponse | None" = None


class RequirementDocumentNodeDraft(BaseModel):
    client_id: str = Field(min_length=1, max_length=64)
    requirement_id: int | None = Field(default=None, gt=0)
    parent_client_id: str | None = Field(default=None, max_length=64)
    title: str = Field(min_length=2, max_length=255)
    type: RequirementType = RequirementType.FEATURE
    verification_type: RequirementVerificationType = RequirementVerificationType.AUTO
    automation_readiness: RequirementAutomationReadiness = (
        RequirementAutomationReadiness.READY
    )
    markdown_content: str = Field(default="", max_length=200000)
    order_index: int = Field(default=0, ge=0)


class RequirementDocumentVersionCreate(BaseModel):
    nodes: list[RequirementDocumentNodeDraft] = Field(min_length=1, max_length=2000)
    change_summary: str | None = Field(default=None, max_length=500)
    source_review_id: int | None = Field(default=None, gt=0)


class RequirementDocumentVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    version_no: int
    snapshot: list[dict]
    content_hash: str
    source_type: SourceType
    source_filename: str | None
    change_summary: str | None
    source_review_id: int | None
    created_by: str
    created_at: datetime


class RequirementDocumentPublishResponse(BaseModel):
    document_version: RequirementDocumentVersionResponse
    root_items: list[RequirementTreeNode]
    active_count: int


class RequirementDiffResponse(BaseModel):
    requirement_id: int
    from_version: int
    to_version: int
    content_changed: bool
    unified_diff: str
    additions: int
    deletions: int
