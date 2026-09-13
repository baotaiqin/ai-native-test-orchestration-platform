from fastapi import APIRouter

from app.modules.ai_gateway.router import router as ai_gateway_router
from app.modules.api_definitions.router import router as api_definitions_router
from app.modules.auth.router import router as auth_router
from app.modules.ci_cd.router import ci_router
from app.modules.ci_cd.router import management_router as ci_cd_router
from app.modules.dashboard.router import router as dashboard_router
from app.modules.database_connections.router import (
    router as database_connections_router,
)
from app.modules.datasets.router import router as datasets_router
from app.modules.defect_drafts.router import router as defect_drafts_router
from app.modules.demo_bootstrap.router import router as demo_bootstrap_router
from app.modules.environments.router import router as environments_router
from app.modules.evidence.router import router as evidence_router
from app.modules.model_center.router import router as model_center_router
from app.modules.performance.router import router as performance_router
from app.modules.projects.router import router as projects_router
from app.modules.prompt_center.ai_router import router as ai_router
from app.modules.prompt_center.router import router as prompt_center_router
from app.modules.reports.router import router as reports_router
from app.modules.requirement_links.router import router as requirement_links_router
from app.modules.requirement_reviews.router import router as requirement_reviews_router
from app.modules.requirements.router import router as requirements_router
from app.modules.resource_registry.router import router as resource_registry_router
from app.modules.runners.router import router as runners_router
from app.modules.runs.router import router as runs_router
from app.modules.scenarios.router import router as scenarios_router
from app.modules.schedules.router import router as schedules_router
from app.modules.secrets.router import router as secrets_router
from app.modules.system.router import router as system_router
from app.modules.test_cases.router import router as test_cases_router
from app.modules.test_plans.router import router as test_plans_router
from app.modules.web_cases.router import case_router as web_cases_router
from app.modules.web_cases.router import element_router as web_elements_router
from app.modules.web_cases.router import page_router as web_pages_router
from app.modules.web_cases.router import profile_router as session_profiles_router
from app.modules.web_design.router import router as web_design_router
from app.modules.web_recordings.router import router as web_recordings_router

api_router = APIRouter()
api_router.include_router(ai_gateway_router, prefix="/ai", tags=["ai-gateway"])
api_router.include_router(ai_router, prefix="/ai", tags=["ai-infrastructure"])
api_router.include_router(
    api_definitions_router, prefix="/api-definitions", tags=["api-definitions"]
)
api_router.include_router(auth_router, prefix="/auth", tags=["auth"])
api_router.include_router(ci_cd_router, prefix="/ci-cd", tags=["ci-cd"])
api_router.include_router(ci_router, prefix="/ci", tags=["ci-external"])
api_router.include_router(
    database_connections_router,
    prefix="/database-connections",
    tags=["database-connections"],
)
api_router.include_router(datasets_router, prefix="/datasets", tags=["datasets"])
api_router.include_router(demo_bootstrap_router, prefix="/demo", tags=["demo-bootstrap"])
api_router.include_router(defect_drafts_router, tags=["defect-drafts"])
api_router.include_router(dashboard_router, prefix="/dashboard", tags=["dashboard"])
api_router.include_router(environments_router, prefix="/environments", tags=["environments"])
api_router.include_router(evidence_router, prefix="/evidence", tags=["evidence"])
api_router.include_router(model_center_router, prefix="/model-center", tags=["model-center"])
api_router.include_router(performance_router, prefix="/performance", tags=["performance"])
api_router.include_router(projects_router, prefix="/projects", tags=["projects"])
api_router.include_router(prompt_center_router, prefix="/prompt-center", tags=["prompt-center"])
api_router.include_router(requirements_router, prefix="/requirements", tags=["requirements"])
api_router.include_router(requirement_links_router, tags=["requirement-links"])
api_router.include_router(reports_router, prefix="/reports", tags=["reports"])
api_router.include_router(
    resource_registry_router, prefix="/resource-registry", tags=["resource-registry"]
)
api_router.include_router(runners_router, prefix="/runners", tags=["runners"])
api_router.include_router(runs_router, prefix="/runs", tags=["runs"])
api_router.include_router(schedules_router, prefix="/schedules", tags=["schedules"])
api_router.include_router(scenarios_router, prefix="/scenarios", tags=["scenarios"])
api_router.include_router(
    requirement_reviews_router, prefix="/requirements", tags=["requirement-reviews"]
)
api_router.include_router(secrets_router, prefix="/secrets", tags=["secrets"])
api_router.include_router(system_router, prefix="/system", tags=["system"])
api_router.include_router(test_cases_router, prefix="/test-cases", tags=["test-cases"])
api_router.include_router(test_plans_router, prefix="/test-plans", tags=["test-plans"])
api_router.include_router(web_pages_router, prefix="/web-pages", tags=["web-pages"])
api_router.include_router(web_elements_router, prefix="/web-elements", tags=["web-elements"])
api_router.include_router(
    session_profiles_router, prefix="/session-profiles", tags=["session-profiles"]
)
api_router.include_router(web_cases_router, prefix="/web-cases", tags=["web-cases"])
api_router.include_router(web_design_router, prefix="/web-design", tags=["web-design"])
api_router.include_router(web_recordings_router, prefix="/web-recordings", tags=["web-recordings"])
