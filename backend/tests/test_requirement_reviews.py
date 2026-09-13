import json
from collections.abc import Generator

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db_session
from app.main import app
from app.modules.model_center.models import (
    ModelConfiguration,
    ModelProviderConnection,
    ProjectModelBinding,
)
from app.modules.projects.models import Project, ProjectBusinessCounter, ProjectMember
from app.modules.prompt_center.models import (
    AiCallLog,
    OutputSchema,
    PromptDefinition,
    PromptVersion,
)
from app.modules.requirement_reviews.models import RequirementReview
from app.modules.requirements.models import (
    Requirement,
    RequirementDocumentVersion,
    RequirementVersion,
)
from app.modules.secrets.models import Secret
from tests.auth_helpers import install_test_auth, uninstall_test_auth

REVIEW_RESULT = {
    "clarity_issues": ["锁定时长未说明"],
    "ambiguity": ["连续失败的统计周期不明确"],
    "missing_rules": ["缺少解锁规则"],
    "exception_gaps": ["未定义验证码服务异常处理"],
    "testability": ["失败次数可以通过边界值验证"],
    "acceptance_criteria_suggestions": ["连续失败 5 次锁定 30 分钟"],
    "overall_summary": "需求具备基础可测试性，但异常与锁定规则需要补充。",
}

REVISION_PLAN_RESULT = {
    "content_revisions": [{
        "target_requirement_code": "REQ-0002",
        "suggestion_ids": ["S-01", "S-02"],
        "reason": "登录需求需要补充锁定规则。",
        "proposed_markdown": "# 用户登录\n失败 5 次锁定 30 分钟。",
    }],
    "additions": [{
        "client_key": "manual-unlock",
        "suggestion_ids": ["S-03"],
        "parent_requirement_code": "REQ-0002",
        "insert_after_requirement_code": "REQ-0003",
        "title": "人工解锁",
        "requirement_type": "RULE",
        "proposed_markdown": "# 人工解锁\n管理员可以解除账号锁定。",
        "reason": "评审指出缺少人工解锁规则。",
    }],
    "deletions": [{
        "target_requirement_code": "REQ-0003",
        "suggestion_ids": ["S-04"],
        "delete_mode": "SUBTREE",
        "reason": "现有解锁需求与新增规则重复。",
    }],
    "unresolved": [{
        "suggestion_ids": ["S-05", "S-06"],
        "reason": "需要产品确认后再决定归属。",
    }],
}


@pytest.fixture
def review_client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    tables = (
        Project.__table__, ProjectMember.__table__, ProjectBusinessCounter.__table__,
        Secret.__table__,
        ModelProviderConnection.__table__,
        ModelConfiguration.__table__, ProjectModelBinding.__table__,
        OutputSchema.__table__, PromptDefinition.__table__, PromptVersion.__table__,
        Requirement.__table__, RequirementVersion.__table__, AiCallLog.__table__,
        RequirementReview.__table__, RequirementDocumentVersion.__table__,
    )
    for table in tables:
        table.create(engine)
    install_test_auth(engine)

    def override() -> Generator[Session, None, None]:
        with testing_session() as session:
            yield session

    def handler(request: httpx.Request) -> httpx.Response:
        payload = request.content.decode(errors="ignore")
        result = (
            REVISION_PLAN_RESULT
            if "RequirementRevisionPlanResult" in payload
            else REVIEW_RESULT
        )
        return httpx.Response(
            200,
            json={
                "id": "requirement-review-response",
                "choices": [{"message": {"content": json.dumps(result)}}],
                "usage": {"prompt_tokens": 800, "completion_tokens": 400},
            },
        )

    from app.modules.ai_gateway import service as gateway_service

    monkeypatch.setattr(
        gateway_service,
        "_build_client",
        lambda timeout: httpx.Client(transport=httpx.MockTransport(handler), timeout=timeout),
    )
    app.dependency_overrides[get_db_session] = override
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        uninstall_test_auth(engine)
        for table in reversed(tables):
            table.drop(engine)
        engine.dispose()


def _document_nodes(items: list[dict]) -> list[dict]:
    nodes: list[dict] = []

    def append(children: list[dict], parent_client_id: str | None) -> None:
        for index, item in enumerate(children):
            client_id = f"existing-{item['id']}"
            nodes.append(
                {
                    "client_id": client_id,
                    "requirement_id": item["id"],
                    "parent_client_id": parent_client_id,
                    "title": item["title"],
                    "type": item["type"],
                    "markdown_content": item["current_version"]["markdown_content"],
                    "order_index": index,
                }
            )
            append(item["children"], client_id)

    append(items, None)
    return nodes


def _scope(client: TestClient) -> tuple[dict[str, str], dict[str, int]]:
    login = client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "admin123"}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    project = client.post(
        "/api/v1/projects", headers=headers,
        json={"name": "需求评审项目", "code": "REVIEW_FLOW_TEST"},
    ).json()
    connection = client.post(
        "/api/v1/model-center/connections", headers=headers,
        json={
            "name": "需求评审渠道", "provider": "OPENAI",
            "protocol_type": "OPENAI_COMPATIBLE", "base_url": "http://mock-provider/v1",
        },
    ).json()
    model = client.post(
        "/api/v1/model-center", headers=headers,
        json={
            "name": "评审模型", "connection_id": connection["id"],
            "model_vendor": "OPENAI", "model_name": "review-model",
            "model_type": "TEXT", "supports_structured_output": True,
        },
    ).json()
    client.put(
        "/api/v1/model-center/bindings/project", headers=headers,
        json={
            "project_id": project["id"], "task_type": "REQUIREMENT_REVIEW",
            "primary_model_id": model["id"], "max_fallback": 0,
        },
    ).raise_for_status()
    schema = client.post(
        "/api/v1/ai/output-schemas", headers=headers,
        json={
            "name": "RequirementReviewResult",
            "schema_json": {
                "type": "object",
                "required": [
                    "clarity_issues", "ambiguity", "missing_rules", "exception_gaps",
                    "testability", "acceptance_criteria_suggestions", "overall_summary",
                ],
                "additionalProperties": False,
                "properties": {
                    **{
                        key: {"type": "array", "items": {"type": "string"}}
                        for key in (
                            "clarity_issues", "ambiguity", "missing_rules",
                            "exception_gaps", "testability",
                            "acceptance_criteria_suggestions",
                        )
                    },
                    "overall_summary": {"type": "string"},
                },
            },
        },
    ).json()
    prompt = client.post(
        "/api/v1/prompt-center", headers=headers,
        json={
            "name": "需求评审默认 Prompt", "code": "REVIEW_FLOW_PROMPT",
            "task_type": "REQUIREMENT_REVIEW", "system_prompt": "输出评审 JSON",
            "user_template": "评审需求：{{ requirement }}",
            "output_schema_id": schema["id"],
        },
    ).json()
    parent = client.post(
        "/api/v1/requirements", headers=headers,
        json={
            "project_id": project["id"], "title": "账号体系",
            "markdown_content": "# 账号体系\n统一账号规则。",
        },
    ).json()
    requirement = client.post(
        "/api/v1/requirements", headers=headers,
        json={
            "project_id": project["id"], "parent_id": parent["id"],
            "title": "用户登录", "markdown_content": "# 用户登录\n失败 5 次锁定。",
        },
    ).json()
    child = client.post(
        "/api/v1/requirements", headers=headers,
        json={
            "project_id": project["id"], "parent_id": requirement["id"],
            "title": "账号解锁", "markdown_content": "# 账号解锁\n锁定 30 分钟后自动解锁。",
        },
    )
    child.raise_for_status()
    tree = client.get(
        "/api/v1/requirements",
        headers=headers,
        params={"project_id": project["id"]},
    ).json()["items"]
    baseline = client.post(
        f"/api/v1/requirements/projects/{project['id']}/document-versions",
        headers=headers,
        json={"nodes": _document_nodes(tree), "change_summary": "初始完整需求文档"},
    )
    baseline.raise_for_status()
    return headers, {
        "project_id": project["id"], "prompt_id": prompt["id"],
        "requirement_id": requirement["id"], "version_id": requirement["current_version_id"],
    }


def test_generate_edit_accept_and_freeze(review_client: TestClient) -> None:
    headers, ids = _scope(review_client)
    generated = review_client.post(
        f"/api/v1/requirements/{ids['requirement_id']}/ai-reviews", headers=headers,
        json={
            "prompt_id": ids["prompt_id"], "include_parent": True,
            "additional_instructions": "重点关注账号锁定。",
        },
    )
    assert generated.status_code == 202
    queued_review = generated.json()
    assert queued_review["generation_status"] == "QUEUED"
    review = review_client.get(
        f"/api/v1/requirements/{ids['requirement_id']}/ai-reviews", headers=headers,
    ).json()["items"][0]
    assert review["generation_status"] == "SUCCEEDED"
    assert review["status"] == "DRAFT"
    assert review["structured_result"]["missing_rules"] == ["缺少解锁规则"]
    assert review["context_snapshot"]["parent"]["title"] == "账号体系"
    assert review["context_snapshot"]["descendants"][0]["title"] == "账号解锁"
    assert review["context_snapshot"]["descendants"][0]["id"] > 0
    assert review["context_snapshot"]["document_version"]["version_no"] == 1
    review_id = review["id"]

    edited_result = {**REVIEW_RESULT, "overall_summary": "人工调整后的评审结论。"}
    edited = review_client.patch(
        f"/api/v1/requirements/ai-reviews/{review_id}", headers=headers,
        json={"human_result": edited_result, "decision_note": "已核对"},
    )
    assert edited.status_code == 200
    assert edited.json()["human_result"]["overall_summary"] == "人工调整后的评审结论。"

    accepted = review_client.post(
        f"/api/v1/requirements/ai-reviews/{review_id}/decision", headers=headers,
        json={"action": "ACCEPT"},
    )
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "ACCEPTED"
    assert accepted.json()["reviewed_by"] == "dev-admin"

    revision_plan_task = review_client.post(
        f"/api/v1/requirements/ai-reviews/{review_id}/revision-plan",
        headers=headers,
    )
    assert revision_plan_task.status_code == 202
    planned_review = review_client.get(
        f"/api/v1/requirements/ai-reviews/{review_id}", headers=headers,
    )
    assert planned_review.status_code == 200
    plan_state = planned_review.json()["context_snapshot"]["revision_plan"]
    assert plan_state["status"] == "SUCCEEDED"
    assert plan_state["result"]["content_revisions"][0]["target_requirement_code"] == "REQ-0002"
    assert plan_state["result"]["additions"][0]["parent_requirement_code"] == "REQ-0002"
    assert plan_state["result"]["deletions"][0]["target_requirement_code"] == "REQ-0003"

    tree = review_client.get(
        "/api/v1/requirements",
        headers=headers,
        params={"project_id": ids["project_id"]},
    ).json()["items"]
    outside_scope_nodes = _document_nodes(tree)
    outside_scope_nodes[0]["markdown_content"] += "\n不应被本次评审修改。"
    outside_scope = review_client.post(
        f"/api/v1/requirements/projects/{ids['project_id']}/document-versions",
        headers=headers,
        json={
            "nodes": outside_scope_nodes,
            "change_summary": "错误修改评审范围之外的父需求",
            "source_review_id": review_id,
        },
    )
    assert outside_scope.status_code == 409

    reviewed_nodes = _document_nodes(tree)
    review_node = next(
        node for node in reviewed_nodes
        if node["requirement_id"] == ids["requirement_id"]
    )
    reviewed_nodes.append({
        "client_id": "new-review-rule",
        "parent_client_id": review_node["client_id"],
        "title": "人工解锁规则",
        "type": "RULE",
        "markdown_content": "# 人工解锁规则\n管理员可人工解锁。",
        "order_index": 1,
    })
    version_two = review_client.post(
        f"/api/v1/requirements/projects/{ids['project_id']}/document-versions",
        headers=headers,
        json={
            "nodes": reviewed_nodes,
            "change_summary": "根据评审完善登录规则",
            "source_review_id": review_id,
        },
    )
    assert version_two.status_code == 201
    assert version_two.json()["document_version"]["version_no"] == 2
    assert version_two.json()["document_version"]["source_review_id"] == review_id

    frozen = review_client.patch(
        f"/api/v1/requirements/ai-reviews/{review_id}", headers=headers,
        json={"human_result": REVIEW_RESULT},
    )
    assert frozen.status_code == 409
    requirement = review_client.get(
        f"/api/v1/requirements/{ids['requirement_id']}", headers=headers
    )
    assert requirement.json()["current_version_id"] == ids["version_id"]


def test_reject_keeps_ai_result_and_audit(review_client: TestClient) -> None:
    headers, ids = _scope(review_client)
    generated = review_client.post(
        f"/api/v1/requirements/{ids['requirement_id']}/ai-reviews", headers=headers,
        json={"prompt_id": ids["prompt_id"]},
    ).json()
    rejected = review_client.post(
        f"/api/v1/requirements/ai-reviews/{generated['id']}/decision", headers=headers,
        json={"action": "REJECT", "decision_note": "建议不适用"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "REJECTED"
    assert (
        rejected.json()["structured_result"]["overall_summary"]
        == REVIEW_RESULT["overall_summary"]
    )
    tree = review_client.get(
        "/api/v1/requirements",
        headers=headers,
        params={"project_id": ids["project_id"]},
    ).json()["items"]
    invalid_version = review_client.post(
        f"/api/v1/requirements/projects/{ids['project_id']}/document-versions",
        headers=headers,
        json={
            "nodes": _document_nodes(tree),
            "source_review_id": generated["id"],
        },
    )
    assert invalid_version.status_code == 409

    reviews = review_client.get(
        f"/api/v1/requirements/{ids['requirement_id']}/ai-reviews", headers=headers
    )
    assert reviews.json()["total"] == 1
    project_reviews = review_client.get(
        f"/api/v1/requirements/projects/{ids['project_id']}/ai-reviews",
        headers=headers,
        params={"page": 1, "page_size": 10},
    )
    assert project_reviews.status_code == 200
    assert project_reviews.json()["total"] == 1
    assert project_reviews.json()["page"] == 1
    assert project_reviews.json()["page_size"] == 10
    assert project_reviews.json()["items"][0]["requirement_title"] == "用户登录"
    assert project_reviews.json()["items"][0]["requirement_version_no"] == 1
    calls = review_client.get(
        f"/api/v1/ai/calls?project_id={ids['project_id']}", headers=headers
    )
    assert calls.json()["total"] == 1


def test_delete_completed_review_keeps_ai_call_audit(
    review_client: TestClient,
) -> None:
    headers, ids = _scope(review_client)
    generated = review_client.post(
        f"/api/v1/requirements/{ids['requirement_id']}/ai-reviews",
        headers=headers,
        json={"prompt_id": ids["prompt_id"]},
    )
    assert generated.status_code == 202
    reviews = review_client.get(
        f"/api/v1/requirements/{ids['requirement_id']}/ai-reviews",
        headers=headers,
    ).json()
    review_id = reviews["items"][0]["id"]
    assert reviews["items"][0]["generation_status"] == "SUCCEEDED"

    deleted = review_client.delete(
        f"/api/v1/requirements/ai-reviews/{review_id}", headers=headers
    )
    assert deleted.status_code == 204
    remaining = review_client.get(
        f"/api/v1/requirements/projects/{ids['project_id']}/ai-reviews",
        headers=headers,
    ).json()
    assert remaining["total"] == 0
    calls = review_client.get(
        f"/api/v1/ai/calls?project_id={ids['project_id']}", headers=headers
    ).json()
    assert calls["total"] == 1


def test_delete_running_review_is_rejected(
    review_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers, ids = _scope(review_client)
    from app.modules.requirement_reviews import router as review_router

    monkeypatch.setattr(review_router, "process_review_task", lambda *args: None)
    created = review_client.post(
        f"/api/v1/requirements/{ids['requirement_id']}/ai-reviews",
        headers=headers,
        json={"prompt_id": ids["prompt_id"]},
    )
    assert created.status_code == 202
    assert created.json()["generation_status"] == "QUEUED"

    deleted = review_client.delete(
        f"/api/v1/requirements/ai-reviews/{created.json()['id']}", headers=headers
    )
    assert deleted.status_code == 409
    assert "正在生成" in deleted.json()["message"]


def test_background_failure_is_visible_in_review_records(
    review_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers, ids = _scope(review_client)

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("provider timeout", request=request)

    from app.modules.ai_gateway import service as gateway_service

    monkeypatch.setattr(
        gateway_service,
        "_build_client",
        lambda timeout: httpx.Client(
            transport=httpx.MockTransport(timeout_handler), timeout=timeout
        ),
    )
    created = review_client.post(
        f"/api/v1/requirements/{ids['requirement_id']}/ai-reviews",
        headers=headers,
        json={"prompt_id": ids["prompt_id"]},
    )
    assert created.status_code == 202
    records = review_client.get(
        f"/api/v1/requirements/projects/{ids['project_id']}/ai-reviews",
        headers=headers,
    ).json()
    assert records["active_count"] == 0
    assert records["items"][0]["generation_status"] == "FAILED"
    assert records["items"][0]["error_message"] == "模型调用超时"
    assert records["items"][0]["structured_result"] is None
