"""Unit tests for quota normalize + isolated fetch error paths."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from quotas.credentials import Credential  # noqa: E402
from quotas.fetch import (  # noqa: E402
    fetch_all_quotas,
    normalize_codex_usage,
    normalize_grok_billing,
    normalize_kimi_usages,
    parse_opencode_go_dashboard,
)
from quotas.models import SERVICE_NAMES  # noqa: E402

FIXTURES = ROOT / "fixtures" / "quotas"


class NormalizeTests(unittest.TestCase):
    def test_codex_fixture_fields(self):
        payload = json.loads((FIXTURES / "codex_usage.json").read_text())
        rec = normalize_codex_usage(payload)
        self.assertEqual(rec.name, "codex")
        self.assertEqual(rec.status, "ok")
        self.assertEqual(rec.used_percent, 42.0)
        self.assertEqual(rec.remaining_percent, 58.0)
        self.assertIsNotNone(rec.reset_at)
        self.assertTrue(rec.windows)

    def test_grok_fixture_fields(self):
        payload = json.loads((FIXTURES / "grok_billing.json").read_text())
        rec = normalize_grok_billing(payload)
        self.assertEqual(rec.name, "grok")
        self.assertEqual(rec.status, "ok")
        self.assertEqual(rec.used_percent, 80.0)
        self.assertEqual(rec.remaining_percent, 20.0)
        self.assertIsNotNone(rec.reset_at)

    def test_kimi_fixture_fields(self):
        payload = json.loads((FIXTURES / "kimi_usages.json").read_text())
        rec = normalize_kimi_usages(payload)
        self.assertEqual(rec.name, "kimi")
        self.assertEqual(rec.status, "ok")
        self.assertIsNotNone(rec.used_percent)
        self.assertIsNotNone(rec.remaining_percent)
        # used 30 / limit 100 -> 30% used, 70% remaining
        self.assertAlmostEqual(rec.used_percent, 30.0)
        self.assertAlmostEqual(rec.remaining_percent, 70.0)
        self.assertIsNotNone(rec.reset_at)
        labels = {str(w.get("label")) for w in rec.windows}
        self.assertIn("week", labels)
        self.assertIn("5h", labels)

    def test_codex_labels_week_from_window_seconds(self):
        payload = json.loads((FIXTURES / "codex_usage.json").read_text())
        rec = normalize_codex_usage(payload)
        self.assertTrue(rec.windows)
        self.assertEqual(rec.windows[0]["label"], "week")
        self.assertAlmostEqual(rec.windows[0]["remaining_percent"], 58.0)

    def test_opencode_go_dashboard_parse(self):
        html = (FIXTURES / "opencode_go_dashboard.html").read_text()
        windows = parse_opencode_go_dashboard(html)
        self.assertIn("rolling", windows)
        self.assertEqual(windows["rolling"]["usagePercent"], 12.5)
        self.assertEqual(windows["weekly"]["usagePercent"], 40.0)
        self.assertEqual(windows["monthly"]["usagePercent"], 55.0)

    def test_missing_credential_does_not_abort_others(self):
        """fetch_all_quotas isolates failures; missing creds → unavailable."""
        creds = {
            "codex": Credential(service="codex", present=False, kind="missing"),
            "grok": Credential(service="grok", present=False, kind="missing"),
            "kimi": Credential(service="kimi", present=False, kind="missing"),
            "opencode-go": Credential(service="opencode-go", present=False, kind="missing"),
        }

        def boom_http(*a, **k):
            raise AssertionError("http should not be called when creds missing")

        records = fetch_all_quotas(creds, http=boom_http)
        self.assertEqual([r.name for r in records], list(SERVICE_NAMES))
        for r in records:
            self.assertEqual(r.status, "unavailable")
            self.assertTrue(r.detail)

    def test_partial_http_failure_isolated(self):
        """One provider HTTP failure does not crash siblings."""
        payloads = {
            "https://chatgpt.com/backend-api/codex/usage": (
                200,
                {},
                (FIXTURES / "codex_usage.json").read_bytes(),
            ),
            "https://cli-chat-proxy.grok.com/v1/billing?format=credits": (
                500,
                {},
                b"upstream error",
            ),
        }

        def fake_http(url, **kwargs):
            for key, val in payloads.items():
                if url.startswith(key.split("?")[0]) or url == key:
                    return val
            if "kimi.com" in url:
                return 401, {}, b"no key"
            if "opencode.ai" in url:
                return 200, {}, b'{"data":[]}'
            return 404, {}, b"nope"

        creds = {
            "codex": Credential(
                service="codex",
                present=True,
                kind="oauth",
                access_token="t",
                account_id="a",
            ),
            "grok": Credential(service="grok", present=True, kind="oauth", access_token="t"),
            "kimi": Credential(service="kimi", present=True, kind="api_key", api_key="sk-kimi-x"),
            "opencode-go": Credential(
                service="opencode-go",
                present=True,
                kind="api_key",
                api_key="sk-x",
            ),
        }
        records = fetch_all_quotas(creds, http=fake_http)
        by = {r.name: r for r in records}
        self.assertEqual(by["codex"].status, "ok")
        self.assertEqual(by["codex"].used_percent, 42.0)
        self.assertEqual(by["grok"].status, "error")
        self.assertIn("500", by["grok"].detail)
        self.assertEqual(by["kimi"].status, "error")
        # opencode-go key works but no dashboard → unavailable (not crash)
        self.assertEqual(by["opencode-go"].status, "unavailable")


if __name__ == "__main__":
    unittest.main()
