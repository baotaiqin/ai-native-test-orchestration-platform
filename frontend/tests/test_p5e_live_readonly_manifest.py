"""Targeted manifest-contract tests for the P5-E H4 read-only UI audit."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from p5e_live_readonly_playwright import DEFAULT_MANIFEST, read_manifest_context


class ManifestContractTest(unittest.TestCase):
    def test_real_manifest_selects_healed_version_instead_of_source_version(self) -> None:
        context = read_manifest_context(DEFAULT_MANIFEST)
        self.assertEqual(context["source_web_case_version_id"], 6)
        self.assertEqual(context["healed_web_case_version_id"], 7)
        self.assertNotEqual(
            context["source_web_case_version_id"],
            context["healed_web_case_version_id"],
        )

    def test_passed_manifest_without_healed_version_is_rejected(self) -> None:
        source = json.loads(DEFAULT_MANIFEST.read_text(encoding="utf-8"))
        invalid = copy.deepcopy(source)
        invalid["ids"].pop("healed_web_case_version_id")
        ledger = (DEFAULT_MANIFEST.parent / "ai-call-ledger.json").read_text(
            encoding="utf-8"
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            attempts = root / "attempts"
            attempts.mkdir()
            payload = json.dumps(invalid, ensure_ascii=False).encode("utf-8")
            result_path = root / "result.json"
            result_path.write_bytes(payload)
            (attempts / f"{invalid['acceptance_id']}.json").write_bytes(payload)
            (root / "ai-call-ledger.json").write_text(ledger, encoding="utf-8")

            with self.assertRaisesRegex(
                AssertionError, "healed_web_case_version_id"
            ):
                read_manifest_context(result_path)


if __name__ == "__main__":
    unittest.main()
