import base64

import pytest
from pydantic import ValidationError

from app.modules.web_cases.schemas import WebCaseContent


def _content(actions: list[dict[str, object]]) -> WebCaseContent:
    return WebCaseContent.model_validate(
        {"start_url": "https://example.test", "actions": actions}
    )


def test_extended_web_actions_are_strict_and_versionable() -> None:
    base = {"timeout_ms": 1000, "failure_policy": "STOP"}
    locator = {"strategy": "css", "value": "#source", "element_version_id": None}
    content = _content(
        [
            {**base, "type": "DRAG_DROP", "locator": locator, "value": "#target"},
            {
                **base,
                "type": "UPLOAD",
                "locator": locator,
                "key": "sample.txt",
                "value": base64.b64encode(b"managed upload").decode(),
            },
            {**base, "type": "DOWNLOAD", "locator": locator, "value": "report.csv"},
            {**base, "type": "WAIT_TIME", "value": "250"},
            {**base, "type": "WAIT_TEXT", "locator": locator, "value": "ready"},
            {**base, "type": "COOKIE", "key": "sid", "value": "opaque"},
            {**base, "type": "LOCAL_STORAGE", "key": "theme", "value": "dark"},
            {**base, "type": "SESSION_STORAGE", "key": "step", "value": "one"},
            {**base, "type": "JS_EVAL", "value": "() => document.title"},
        ]
    )

    assert [action.type for action in content.actions] == [
        "DRAG_DROP",
        "UPLOAD",
        "DOWNLOAD",
        "WAIT_TIME",
        "WAIT_TEXT",
        "COOKIE",
        "LOCAL_STORAGE",
        "SESSION_STORAGE",
        "JS_EVAL",
    ]


@pytest.mark.parametrize("value", ["0", "600001", "1.5", "-1", ""])
def test_wait_time_rejects_unbounded_or_non_integer_values(value: str) -> None:
    with pytest.raises(ValidationError):
        _content(
            [
                {
                    "type": "WAIT_TIME",
                    "value": value,
                    "timeout_ms": 1000,
                    "failure_policy": "STOP",
                }
            ]
        )


def test_upload_rejects_host_paths_and_invalid_content() -> None:
    locator = {"strategy": "css", "value": "#file", "element_version_id": None}
    with pytest.raises(ValidationError):
        _content(
            [
                {
                    "type": "UPLOAD",
                    "locator": locator,
                    "key": "../secret.txt",
                    "value": "not-base64",
                    "timeout_ms": 1000,
                    "failure_policy": "STOP",
                }
            ]
        )


def test_extended_deterministic_web_assertions_are_versionable() -> None:
    locator = {"strategy": "css", "value": ".item", "element_version_id": None}
    baseline = base64.b64encode(b"\x89PNG\r\n\x1a\nbaseline").decode()
    content = WebCaseContent.model_validate(
        {
            "start_url": "https://example.test",
            "actions": [
                {
                    "type": "GOTO",
                    "url": "https://example.test",
                    "timeout_ms": 1000,
                    "failure_policy": "STOP",
                }
            ],
            "assertions": [
                {
                    "type": "ASSERT_ATTRIBUTE",
                    "locator": locator,
                    "key": "data-status",
                    "expected": "ready",
                    "timeout_ms": 1000,
                },
                {
                    "type": "ASSERT_ELEMENT_COUNT",
                    "locator": locator,
                    "expected": "2",
                    "timeout_ms": 1000,
                },
                {
                    "type": "ASSERT_DOWNLOAD_SUCCESS",
                    "expected": "report.csv",
                    "timeout_ms": 1000,
                },
                {
                    "type": "ASSERT_NETWORK_REQUEST",
                    "expected": "/api/orders",
                    "timeout_ms": 1000,
                },
                {
                    "type": "ASSERT_SCREENSHOT_VISUAL_COMPARE",
                    "expected": baseline,
                    "timeout_ms": 1000,
                },
                {
                    "type": "ASSERT_AI_SEMANTIC",
                    "prompt_id": 7,
                    "criteria": "页面说明订单已经创建",
                    "confidence_threshold": 0.85,
                    "timeout_ms": 1000,
                },
            ],
        }
    )

    assert [assertion.type for assertion in content.assertions] == [
        "ASSERT_ATTRIBUTE",
        "ASSERT_ELEMENT_COUNT",
        "ASSERT_DOWNLOAD_SUCCESS",
        "ASSERT_NETWORK_REQUEST",
        "ASSERT_SCREENSHOT_VISUAL_COMPARE",
        "ASSERT_AI_SEMANTIC",
    ]


def test_visual_compare_rejects_non_png_baseline() -> None:
    with pytest.raises(ValidationError):
        WebCaseContent.model_validate(
            {
                "start_url": "https://example.test",
                "actions": [
                    {
                        "type": "GOTO",
                        "url": "https://example.test",
                        "timeout_ms": 1000,
                        "failure_policy": "STOP",
                    }
                ],
                "assertions": [
                    {
                        "type": "ASSERT_SCREENSHOT_VISUAL_COMPARE",
                        "expected": base64.b64encode(b"not png").decode(),
                        "timeout_ms": 1000,
                    }
                ],
            }
        )


def test_browser_config_is_strict_and_versionable() -> None:
    content = WebCaseContent.model_validate(
        {
            "start_url": "https://example.test",
            "actions": [
                {
                    "type": "GOTO",
                    "url": "https://example.test",
                    "timeout_ms": 1000,
                    "failure_policy": "STOP",
                }
            ],
            "browser_config": {
                "window_width": 1440,
                "window_height": 900,
                "language": "zh-CN",
                "user_agent": "V1-Test-Agent",
                "proxy": {
                    "server": "http://proxy.example.test:8080",
                    "username": "{{secret.PROXY_USER}}",
                    "password": "{{secret.PROXY_PASSWORD}}",
                },
                "download_path": "downloads/reports",
            },
        }
    )

    assert content.browser_config.window_width == 1440
    assert content.browser_config.proxy is not None
    assert content.browser_config.proxy.password == "{{secret.PROXY_PASSWORD}}"


@pytest.mark.parametrize(
    "browser_config",
    [
        {"window_width": 1},
        {"language": "bad language"},
        {"download_path": "../outside"},
        {"proxy": {"server": "http://user:pass@proxy.example", "username": None, "password": None}},
        {"proxy": {"server": "http://proxy.example", "username": "raw", "password": "raw"}},
    ],
)
def test_browser_config_rejects_unsafe_values(browser_config: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        WebCaseContent.model_validate(
            {
                "start_url": "https://example.test",
                "actions": [
                    {
                        "type": "GOTO",
                        "url": "https://example.test",
                        "timeout_ms": 1000,
                        "failure_policy": "STOP",
                    }
                ],
                "browser_config": browser_config,
            }
        )
