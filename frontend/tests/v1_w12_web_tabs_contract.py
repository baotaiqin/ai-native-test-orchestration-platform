"""Validate the shared frontend tab-action factory against the Backend schema."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.modules.web_cases.schemas import WebCaseContent  # noqa: E402


NODE_FACTORY_PROBE = r"""
import { buildSync } from './frontend/node_modules/esbuild/lib/main.js';

const bundle = buildSync({
  entryPoints: ['src/utils/web-actions.ts'],
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
const newTab = api.makeWebAction('NEW_TAB');
newTab.url = 'https://tabs.example.test/{{runtime.path}}';
newTab.value = 'details_1';
const switchTab = api.makeWebAction('SWITCH_TAB');
switchTab.value = 'details_1';
const closeTab = api.makeWebAction('CLOSE_TAB');
closeTab.value = 'main';
console.log(JSON.stringify({
  actionTypes: api.WEB_ACTION_TYPES,
  valid: [newTab, switchTab, closeTab],
  summaries: [newTab, switchTab, closeTab].map(api.webActionSummary),
  aliases: {
    alpha: api.validWebTabAlias('alpha-_1'),
    mainForNew: api.validWebTabAlias('main', false),
    mainForExisting: api.validWebTabAlias('main', true),
    leadingDigit: api.validWebTabAlias('1alpha'),
    tooLong: api.validWebTabAlias('a'.repeat(65)),
  },
  urls: {
    https: api.validWebUrlOrTemplate('https://tabs.example.test/{{runtime.path}}'),
    template: api.validWebUrlOrTemplate('{{runtime.target_url}}'),
    relative: api.validWebUrlOrTemplate('/relative'),
    credentials: api.validWebUrlOrTemplate('https://user:pass@tabs.example.test'),
    malformedTemplate: api.validWebUrlOrTemplate('https://tabs.example.test/{{bad template}}'),
  },
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


def web_case_payload(actions: list[dict[str, object]]) -> dict[str, object]:
    return {
        "start_url": "https://synthetic.invalid",
        "actions": actions,
        "assertions": [],
    }


def test_tab_action_real_factory_contract() -> None:
    output = real_factory_output()
    actions = output["valid"]

    assert output["actionTypes"][4:7] == ["NEW_TAB", "SWITCH_TAB", "CLOSE_TAB"]
    assert set(actions[0]) == {"type", "url", "value", "timeout_ms", "failure_policy"}
    assert all(set(item) == {"type", "value", "timeout_ms", "failure_policy"} for item in actions[1:])
    assert all(item["timeout_ms"] == 30000 and item["failure_policy"] == "STOP" for item in actions)
    assert output["aliases"] == {
        "alpha": True,
        "mainForNew": False,
        "mainForExisting": True,
        "leadingDigit": False,
        "tooLong": False,
    }
    assert output["urls"] == {
        "https": True,
        "template": True,
        "relative": False,
        "credentials": False,
        "malformedTemplate": False,
    }
    assert output["summaries"] == [
        "NEW_TAB · details_1 · https://tabs.example.test/{{runtime.path}}",
        "SWITCH_TAB · 切换到 details_1",
        "CLOSE_TAB · 关闭 main",
    ]

    parsed = WebCaseContent.model_validate(web_case_payload(actions))
    assert [action.type for action in parsed.actions] == ["NEW_TAB", "SWITCH_TAB", "CLOSE_TAB"]

    for invalid in (
        {"type": "NEW_TAB", "url": "https://tabs.example.test", "value": "main"},
        {"type": "NEW_TAB", "url": "/relative", "value": "alpha"},
        {"type": "SWITCH_TAB", "value": "1alpha"},
        {"type": "CLOSE_TAB", "value": "alpha", "locator": {"strategy": "css", "value": "#stale"}},
    ):
        with pytest.raises(ValueError):
            WebCaseContent.model_validate(web_case_payload([invalid]))
