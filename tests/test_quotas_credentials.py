"""Unit tests for credential discovery (missing vs present shapes)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from quotas.credentials import (  # noqa: E402
    discover_codex,
    discover_credentials,
    discover_grok,
    discover_kimi,
    discover_opencode_go,
)


class CredentialDiscoveryTests(unittest.TestCase):
    def test_missing_home_yields_absent_for_all(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            creds = discover_credentials(home)
            self.assertEqual(set(creds), {"codex", "grok", "kimi", "opencode-go"})
            for name, c in creds.items():
                self.assertFalse(c.present, name)
                self.assertEqual(c.kind, "missing")
                red = c.redacted()
                self.assertFalse(red["has_access_token"])
                self.assertFalse(red["has_api_key"])
                # secrets never appear in redacted dump
                blob = json.dumps(red)
                self.assertNotIn("sk-", blob)
                self.assertNotIn("eyJ", blob)

    def test_codex_present_shape(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            p = home / ".codex" / "auth.json"
            p.parent.mkdir(parents=True)
            p.write_text(
                json.dumps(
                    {
                        "auth_mode": "chatgpt",
                        "tokens": {
                            "id_token": "id.secret",
                            "access_token": "access.secret.token",
                            "refresh_token": "rt.secret",
                            "account_id": "acct-1",
                        },
                        "last_refresh": "2026-08-01T00:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            c = discover_codex(home)
            self.assertTrue(c.present)
            self.assertEqual(c.kind, "oauth")
            self.assertEqual(c.access_token, "access.secret.token")
            self.assertEqual(c.account_id, "acct-1")
            red = c.redacted()
            self.assertTrue(red["has_access_token"])
            self.assertNotIn("access.secret", json.dumps(red))

    def test_grok_present_shape(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            p = home / ".grok" / "auth.json"
            p.parent.mkdir(parents=True)
            p.write_text(
                json.dumps(
                    {
                        "https://auth.x.ai::client": {
                            "key": "eyJaccess",
                            "refresh_token": "refreshxyz",
                            "expires_at": "2026-08-09T00:00:00Z",
                            "oidc_issuer": "https://auth.x.ai",
                            "oidc_client_id": "client-1",
                            "auth_mode": "oidc",
                        }
                    }
                ),
                encoding="utf-8",
            )
            c = discover_grok(home)
            self.assertTrue(c.present)
            self.assertEqual(c.access_token, "eyJaccess")
            self.assertEqual(c.oidc_client_id, "client-1")
            self.assertNotIn("eyJaccess", json.dumps(c.redacted()))

    def test_kimi_from_env(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            old = os.environ.get("KIMI_API_KEY")
            os.environ["KIMI_API_KEY"] = "sk-kimi-test-key-123"
            try:
                c = discover_kimi(home)
            finally:
                if old is None:
                    os.environ.pop("KIMI_API_KEY", None)
                else:
                    os.environ["KIMI_API_KEY"] = old
            self.assertTrue(c.present)
            self.assertEqual(c.kind, "api_key")
            self.assertEqual(c.api_key, "sk-kimi-test-key-123")
            self.assertNotIn("sk-kimi-test", json.dumps(c.redacted()))

    def test_kimi_missing_without_env(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            # scrub env for this process
            saved = {k: os.environ.pop(k) for k in list(os.environ) if k.startswith("KIMI_")}
            try:
                c = discover_kimi(home)
                self.assertFalse(c.present)
            finally:
                os.environ.update(saved)

    def test_kimi_from_kimi_code_oauth(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            p = home / ".kimi-code" / "credentials" / "kimi-code.json"
            p.parent.mkdir(parents=True)
            p.write_text(
                json.dumps(
                    {
                        "access_token": "eyJaccess.kimi",
                        "refresh_token": "eyJrefresh.kimi",
                        "expires_at": 1786245000,
                        "scope": "kimi-code",
                        "token_type": "Bearer",
                        "expires_in": 900,
                    }
                ),
                encoding="utf-8",
            )
            saved = {k: os.environ.pop(k) for k in list(os.environ) if k.startswith("KIMI_")}
            try:
                c = discover_kimi(home)
            finally:
                os.environ.update(saved)
            self.assertTrue(c.present)
            self.assertEqual(c.kind, "oauth")
            self.assertEqual(c.access_token, "eyJaccess.kimi")
            self.assertEqual(c.refresh_token, "eyJrefresh.kimi")
            self.assertEqual(c.expires_at, "1786245000")
            self.assertEqual(c.oidc_client_id, "17e5f671-d194-4dfb-9706-5516cb48c098")
            red = json.dumps(c.redacted())
            self.assertNotIn("eyJaccess", red)
            self.assertNotIn("eyJrefresh", red)

    def test_opencode_go_from_auth_json(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            p = home / ".local" / "share" / "opencode" / "auth.json"
            p.parent.mkdir(parents=True)
            p.write_text(
                json.dumps({"opencode-go": {"type": "api", "key": "sk-opencode-test"}}),
                encoding="utf-8",
            )
            c = discover_opencode_go(home)
            self.assertTrue(c.present)
            self.assertEqual(c.api_key, "sk-opencode-test")
            self.assertNotIn("sk-opencode", json.dumps(c.redacted()))


if __name__ == "__main__":
    unittest.main()
