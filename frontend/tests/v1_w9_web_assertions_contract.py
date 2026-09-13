"""Validate the real shared frontend assertion factory against the current Backend schema."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
BACKEND_SCHEMA = ROOT / "backend" / "app" / "modules" / "web_cases" / "schemas.py"
sys.path.insert(0, str(ROOT / "backend"))

from app.modules.web_cases.schemas import WebCaseContent  # noqa: E402


NODE_FACTORY_PROBE = r"""
import { buildSync } from './frontend/node_modules/esbuild/lib/main.js';

const bundle = buildSync({
  entryPoints: ['src/utils/web-assertions.ts'],
  absWorkingDir: process.cwd() + '/frontend',
  tsconfig: 'tsconfig.app.json',
  bundle: true,
  write: false,
  format: 'esm',
  platform: 'node',
});
const api = await import(
  'data:text/javascript;base64,' + Buffer.from(bundle.outputFiles[0].text).toString('base64')
);
const newTypes = ['ASSERT_HIDDEN', 'ASSERT_CLICKABLE'];
const valid = newTypes.map((type, index) => {
  const assertion = api.makeWebAssertion(type);
  assertion.locator.value = index === 0 ? '#loading-mask' : '[data-testid="submit"]';
  return assertion;
});
const switchedFromExpected = newTypes.map((type) => {
  const before = api.makeWebAssertion('ASSERT_TEXT');
  before.expected = 'stale expected value';
  return api.makeWebAssertion(type);
});
const labels = Object.fromEntries(
  ['ASSERT_VISIBLE', 'ASSERT_EXISTS', 'ASSERT_ENABLED', ...newTypes]
    .map((type) => [type, api.webAssertionTypeLabel(type)])
);
console.log(JSON.stringify({
  assertionTypes: api.WEB_ASSERTION_TYPES,
  valid,
  switchedFromExpected,
  labels,
  summaries: valid.map((assertion) => api.webAssertionSummary(assertion)),
  expectedValidation: valid.map((assertion) => api.validWebAssertionExpected(assertion)),
}));
"""


def real_factory_output() -> dict[str, object]:
    completed = subprocess.run(
        ["node", "--input-type=module", "--eval", NODE_FACTORY_PROBE],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr[:2000]
    return json.loads(completed.stdout)


def web_case_payload(assertions: list[dict[str, object]]) -> dict[str, object]:
    return {
        "start_url": "https://synthetic.invalid",
        "actions": [
            {
                "type": "RELOAD",
                "timeout_ms": 30000,
                "failure_policy": "STOP",
            }
        ],
        "assertions": assertions,
    }


def test_hidden_and_clickable_real_factory_contract() -> None:
    output = real_factory_output()
    valid = output["valid"]
    switched = output["switchedFromExpected"]

    assert output["assertionTypes"] == [
        "ASSERT_VISIBLE",
        "ASSERT_EXISTS",
        "ASSERT_ENABLED",
        "ASSERT_HIDDEN",
        "ASSERT_CLICKABLE",
        "ASSERT_TEXT",
        "ASSERT_TEXT_EQUAL",
        "ASSERT_INPUT_VALUE",
        "ASSERT_URL",
        "ASSERT_TITLE",
    ]
    assert output["expectedValidation"] == [True, True]
    assert all(set(item) == {"type", "locator", "timeout_ms"} for item in valid)
    assert all(item["timeout_ms"] == 30000 for item in valid)
    assert all(set(item) == {"type", "locator", "timeout_ms"} for item in switched)
    assert all("expected" not in item for item in switched)

    labels = output["labels"]
    assert labels == {
        "ASSERT_VISIBLE": "ASSERT_VISIBLE · 元素存在且可见",
        "ASSERT_EXISTS": "ASSERT_EXISTS · 元素存在（可隐藏）",
        "ASSERT_ENABLED": "ASSERT_ENABLED · 元素处于启用状态",
        "ASSERT_HIDDEN": "ASSERT_HIDDEN · 元素隐藏或不存在",
        "ASSERT_CLICKABLE": "ASSERT_CLICKABLE · 元素可点击（仅试探，不执行点击）",
    }
    assert output["summaries"] == [
        "元素隐藏或不存在",
        "元素可点击（仅试探，不执行点击）",
    ]

    parsed = WebCaseContent.model_validate(web_case_payload(valid))
    assert [assertion.type for assertion in parsed.assertions] == [
        "ASSERT_HIDDEN",
        "ASSERT_CLICKABLE",
    ]

    missing_locator = dict(valid[0])
    missing_locator.pop("locator")
    with pytest.raises(ValueError):
        WebCaseContent.model_validate(web_case_payload([missing_locator]))

    invalid_timeout = dict(valid[1])
    invalid_timeout["timeout_ms"] = 100.5
    with pytest.raises(ValueError):
        WebCaseContent.model_validate(web_case_payload([invalid_timeout]))

    print(json.dumps({
        "status": "passed",
        "backend_schema_sha256": hashlib.sha256(BACKEND_SCHEMA.read_bytes()).hexdigest(),
        "factory_output": valid,
        "switch_output": switched,
        "missing_locator_rejected": True,
        "non_integer_timeout_rejected": True,
        "labels": labels,
    }, ensure_ascii=False))
