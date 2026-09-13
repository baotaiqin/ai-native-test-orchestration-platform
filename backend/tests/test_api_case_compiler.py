from app.modules.api_definitions.models import ApiDefinition
from app.modules.test_cases.case_compiler import compile_case_intents
from app.modules.test_cases.schemas import ApiCaseExpectedClaim, ApiCaseIntent


def _definition(
    definition_id: int,
    method: str,
    path: str,
    *,
    request_schema: dict | None = None,
    response_schema: dict | None = None,
    protected: bool = False,
) -> ApiDefinition:
    return ApiDefinition(
        id=definition_id,
        project_id=1,
        name=f"{method} {path}",
        method=method,
        path=path,
        parameters=[],
        request_schema=request_schema,
        response_schema=response_schema or {},
        auth_info=(
            {
                "requirements": [{"bearerAuth": []}],
                "schemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}},
            }
            if protected
            else {"requirements": [], "schemes": {}}
        ),
        tags=[],
        source="SWAGGER",
        contract_hash=str(definition_id) * 64,
        status="ACTIVE",
        created_by="test",
    )


def _contracts() -> list[ApiDefinition]:
    login = _definition(
        1,
        "POST",
        "/api/login",
        request_schema={
            "type": "object",
            "required": ["username", "password"],
            "properties": {
                "username": {"type": "string", "example": "demo"},
                "password": {"type": "string", "format": "password"},
            },
        },
        response_schema={
            "200": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "data": {
                            "type": "object",
                            "properties": {"token": {"type": "string"}},
                        }
                    },
                }
            }
        },
    )
    products = _definition(
        2,
        "GET",
        "/api/products",
        response_schema={
            "200": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {"id": {"type": "integer"}},
                            },
                        }
                    },
                }
            }
        },
        protected=True,
    )
    create_order = _definition(
        3,
        "POST",
        "/api/orders",
        request_schema={
            "type": "object",
            "required": ["product_id", "quantity"],
            "properties": {
                "product_id": {"type": "integer"},
                "quantity": {"type": "integer", "minimum": 1, "maximum": 10},
            },
        },
        response_schema={
            "201": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "data": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "integer"},
                                "status": {"type": "string", "enum": ["CREATED"]},
                            },
                        }
                    },
                }
            }
        },
        protected=True,
    )
    delete_order = _definition(
        4,
        "DELETE",
        "/api/orders/{order_id}",
        response_schema={"200": {"schema": {"type": "object", "properties": {}}}},
        protected=True,
    )
    return [login, products, create_order, delete_order]


def test_compiler_builds_auth_dependencies_assertions_and_cleanup() -> None:
    intent = ApiCaseIntent(
        case_key="CREATE_ORDER_SUCCESS",
        checkpoint_keys=["CP-01"],
        title="成功创建订单",
        api_definition_id=3,
        scenario_type="POSITIVE",
        expected_status=201,
        expected_claims=[
            ApiCaseExpectedClaim(field="data.status", operator="EQ", value="CREATED")
        ],
        cleanup_required=True,
        confidence=0.91,
    )

    compilation = compile_case_intents(
        [intent],
        _contracts(),
        [{"name": "DEMO_PASSWORD", "type": "PASSWORD"}],
    )

    assert compilation.diagnostics == []
    assert compilation.result is not None
    case = compilation.result.cases[0]
    assert case.request is not None
    assert case.request.url == "{{base_url}}/api/orders"
    assert case.request.auth.token == "{{token}}"
    assert case.request.body.content["product_id"] == "{{product_id}}"
    assert [item.type.value for item in case.pre_actions] == ["GET_TOKEN", "API_SETUP"]
    assert case.pre_actions[0].request.body.content["password"] == "{{secret.DEMO_PASSWORD}}"
    assert case.pre_actions[1].request.url == "{{base_url}}/api/products"
    assert case.pre_actions[1].expression == "$.items[0].id"
    assert case.extractors[0].name == "order_id"
    assert case.extractors[0].expression == "$.data.id"
    assert case.cleanup[0].url == "{{base_url}}/api/orders/{{order_id}}"
    assert case.cleanup[0].auth.credential_ref == "{{token}}"
    assert case.assertions[1].expression == "$.data.status"


def test_compiler_reuses_project_username_secret_for_login_dependency() -> None:
    intent = ApiCaseIntent(
        case_key="QUERY_PRODUCTS",
        checkpoint_keys=["CP-01"],
        title="登录后查询商品",
        api_definition_id=2,
        scenario_type="POSITIVE",
        expected_status=200,
    )

    compilation = compile_case_intents(
        [intent],
        _contracts(),
        [
            {"name": "AI_TEST_USERNAME", "type": "PASSWORD"},
            {"name": "AI_TEST_PASSWORD", "type": "PASSWORD"},
        ],
    )

    assert compilation.diagnostics == []
    assert compilation.result is not None
    login = compilation.result.cases[0].pre_actions[0]
    assert login.request.body.content["username"] == "{{secret.AI_TEST_USERNAME}}"
    assert login.request.body.content["password"] == "{{secret.AI_TEST_PASSWORD}}"


def test_compiler_keeps_valid_cases_when_another_intent_cannot_compile() -> None:
    valid = ApiCaseIntent(
        case_key="CREATE_ORDER_SUCCESS",
        checkpoint_keys=["CP-01"],
        title="成功创建订单",
        api_definition_id=3,
        scenario_type="POSITIVE",
        expected_status=201,
        cleanup_required=True,
    )
    invalid = ApiCaseIntent(
        case_key="UNKNOWN_RESPONSE",
        checkpoint_keys=["CP-02"],
        title="契约未定义响应",
        api_definition_id=3,
        scenario_type="NEGATIVE",
        expected_status=418,
    )

    compilation = compile_case_intents(
        [valid, invalid],
        _contracts(),
        [{"name": "DEMO_PASSWORD", "type": "PASSWORD"}],
    )

    assert compilation.result is not None
    assert [item.title for item in compilation.result.cases] == ["成功创建订单"]
    assert compilation.compiled_case_keys == ["CREATE_ORDER_SUCCESS"]
    assert len(compilation.diagnostics) == 1
    assert "418" in compilation.diagnostics[0].message


def test_compiler_replaces_fixed_sensitive_login_value_with_project_secret() -> None:
    intent = ApiCaseIntent(
        case_key="LOGIN_SUCCESS",
        checkpoint_keys=["CP-LOGIN"],
        title="使用正确凭据登录成功",
        api_definition_id=1,
        scenario_type="POSITIVE",
        input_mutations=[
            {
                "field": "username",
                "location": "BODY",
                "strategy": "FIXED",
                "value": "demo",
            },
            {
                "field": "password",
                "location": "BODY",
                "strategy": "FIXED",
                "value": "must-not-enter-dsl",
            },
        ],
        expected_status=200,
    )

    compilation = compile_case_intents(
        [intent],
        _contracts(),
        [{"name": "DEMO_PASSWORD", "type": "PASSWORD"}],
    )

    assert compilation.diagnostics == []
    assert compilation.result is not None
    case = compilation.result.cases[0]
    assert case.request is not None
    assert case.request.body.content == {
        "username": "demo",
        "password": "{{secret.DEMO_PASSWORD}}",
    }
    assert case.pre_actions == []


def test_compiler_keeps_explicit_invalid_sensitive_input_ephemeral() -> None:
    intent = ApiCaseIntent(
        case_key="LOGIN_PASSWORD_INVALID",
        checkpoint_keys=["CP-LOGIN"],
        title="错误密码登录失败",
        api_definition_id=1,
        scenario_type="NEGATIVE",
        input_mutations=[
            {
                "field": "password",
                "location": "BODY",
                "strategy": "INVALID_TYPE",
            }
        ],
        expected_status=200,
    )

    compilation = compile_case_intents(
        [intent],
        _contracts(),
        [{"name": "DEMO_PASSWORD", "type": "PASSWORD"}],
    )

    assert compilation.diagnostics == []
    assert compilation.result is not None
    case = compilation.result.cases[0]
    assert case.request is not None
    assert case.request.body.content["password"] == "{{invalid_password}}"
    assert [item.type.value for item in case.pre_actions] == ["FAKER"]


def test_compiler_treats_fixed_sensitive_value_as_invalid_in_negative_case() -> None:
    intent = ApiCaseIntent(
        case_key="LOGIN_PASSWORD_INVALID_FIXED",
        checkpoint_keys=["CP-LOGIN"],
        title="错误密码登录失败",
        api_definition_id=1,
        scenario_type="NEGATIVE",
        input_mutations=[{
            "field": "password",
            "location": "BODY",
            "strategy": "FIXED",
            "value": "ai-must-not-persist-this-value",
        }],
        expected_status=200,
    )

    compilation = compile_case_intents(
        [intent],
        _contracts(),
        [{"name": "DEMO_PASSWORD", "type": "PASSWORD"}],
    )

    assert compilation.diagnostics == []
    assert compilation.result is not None
    case = compilation.result.cases[0]
    assert case.request is not None
    assert case.request.body.content["password"] == "{{invalid_password}}"
    assert [item.name for item in case.pre_actions] == ["invalid_password"]


def test_compiler_infers_fixed_sensitive_value_for_compact_negative_intent() -> None:
    intent = ApiCaseIntent(
        case_key="LOGIN_PASSWORD_INVALID_COMPACT",
        checkpoint_keys=["CP-LOGIN"],
        title="错误密码登录失败",
        api_definition_id=1,
        scenario_type="NEGATIVE",
        input_mutations=[{
            "field": "password",
            "value": "ai-must-not-persist-this-value",
        }],
        expected_status=200,
    )

    compilation = compile_case_intents(
        [intent],
        _contracts(),
        [{"name": "DEMO_PASSWORD", "type": "PASSWORD"}],
    )

    assert compilation.diagnostics == []
    assert compilation.result is not None
    case = compilation.result.cases[0]
    assert case.request is not None
    assert case.request.body.content["password"] == "{{invalid_password}}"
    assert [item.name for item in case.pre_actions] == ["invalid_password"]


def test_compiler_generates_unique_key_for_repeatable_reliability_case() -> None:
    flaky = _definition(
        5,
        "GET",
        "/api/flaky",
        response_schema={
            "503": {
                "schema": {
                    "type": "object",
                    "properties": {"error": {"type": "string"}},
                }
            }
        },
    )
    flaky.parameters = [{
        "name": "key",
        "in": "query",
        "required": True,
        "schema": {"type": "string"},
    }]
    intent = ApiCaseIntent(
        case_key="FLAKY_FIRST_FAILURE",
        checkpoint_keys=["CP-FLAKY"],
        title="首次请求返回临时失败",
        api_definition_id=5,
        scenario_type="RELIABILITY",
        input_mutations=[{
            "field": "key",
            "location": "QUERY",
            "strategy": "FIXED",
            "value": "fixed-key-would-leak-state-between-runs",
        }],
        expected_status=503,
        expected_claims=[ApiCaseExpectedClaim(field="error", operator="EXISTS")],
    )

    compilation = compile_case_intents([intent], [flaky], [])

    assert compilation.diagnostics == []
    assert compilation.result is not None
    case = compilation.result.cases[0]
    assert case.request is not None
    assert case.request.query_params[0].value == "{{unique_key}}"
    assert case.pre_actions[0].name == "unique_key"
    assert case.pre_actions[0].generator.value == "uuid"
    assert case.test_data["compiler_version"]
