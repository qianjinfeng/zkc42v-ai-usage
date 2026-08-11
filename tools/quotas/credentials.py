"""Discover local account credentials for quota providers (secrets never logged)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class Credential:
    """Opaque credential handle for one service."""

    service: str
    present: bool
    kind: str = ""  # oauth | api_key | missing
    path: Optional[str] = None
    # raw secret material — never print
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    account_id: Optional[str] = None
    api_key: Optional[str] = None
    expires_at: Optional[str] = None
    oidc_issuer: Optional[str] = None
    oidc_client_id: Optional[str] = None
    auth_entry_key: Optional[str] = None  # for grok multi-entry auth.json
    extra: dict[str, Any] = field(default_factory=dict)

    def redacted(self) -> dict[str, Any]:
        """Safe summary for logs/tests (no secrets)."""
        return {
            "service": self.service,
            "present": self.present,
            "kind": self.kind,
            "path": self.path,
            "has_access_token": bool(self.access_token),
            "has_refresh_token": bool(self.refresh_token),
            "has_api_key": bool(self.api_key),
            "has_account_id": bool(self.account_id),
            "expires_at": self.expires_at,
            "oidc_issuer": self.oidc_issuer,
            "oidc_client_id": self.oidc_client_id,
            "extra_keys": sorted(self.extra.keys()),
        }


def _home() -> Path:
    return Path(os.path.expanduser("~"))


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def discover_codex(home: Optional[Path] = None) -> Credential:
    home = home or _home()
    path = home / ".codex" / "auth.json"
    if not path.is_file():
        return Credential(service="codex", present=False, kind="missing", path=str(path))
    try:
        data = _read_json(path)
    except Exception:
        return Credential(service="codex", present=False, kind="missing", path=str(path))
    tokens = data.get("tokens") or {}
    access = tokens.get("access_token") if isinstance(tokens, dict) else None
    refresh = tokens.get("refresh_token") if isinstance(tokens, dict) else None
    account_id = tokens.get("account_id") if isinstance(tokens, dict) else None
    if not access:
        return Credential(service="codex", present=False, kind="missing", path=str(path))
    return Credential(
        service="codex",
        present=True,
        kind="oauth",
        path=str(path),
        access_token=access,
        refresh_token=refresh,
        account_id=account_id,
        expires_at=data.get("last_refresh"),
        extra={"auth_mode": data.get("auth_mode")},
    )


def discover_grok(home: Optional[Path] = None) -> Credential:
    home = home or _home()
    path = home / ".grok" / "auth.json"
    if not path.is_file():
        return Credential(service="grok", present=False, kind="missing", path=str(path))
    try:
        data = _read_json(path)
    except Exception:
        return Credential(service="grok", present=False, kind="missing", path=str(path))
    if not isinstance(data, dict) or not data:
        return Credential(service="grok", present=False, kind="missing", path=str(path))
    # Prefer entries with a usable access token ("key")
    best_key = None
    best = None
    for k, v in data.items():
        if not isinstance(v, dict):
            continue
        token = v.get("key") or v.get("access_token") or v.get("access")
        if token:
            best_key, best = k, v
            break
    if not best:
        return Credential(service="grok", present=False, kind="missing", path=str(path))
    return Credential(
        service="grok",
        present=True,
        kind="oauth",
        path=str(path),
        access_token=best.get("key") or best.get("access_token") or best.get("access"),
        refresh_token=best.get("refresh_token"),
        expires_at=best.get("expires_at") or (
            str(best["expires"]) if isinstance(best.get("expires"), (int, float)) else None
        ),
        oidc_issuer=best.get("oidc_issuer") or "https://auth.x.ai",
        oidc_client_id=best.get("oidc_client_id"),
        auth_entry_key=best_key,
        extra={
            "auth_mode": best.get("auth_mode"),
            "email": best.get("email"),
            "user_id": best.get("user_id"),
        },
    )


def _kimi_key_from_env() -> Optional[str]:
    for name in ("KIMI_CODING_API_KEY", "KIMI_API_KEY"):
        v = os.environ.get(name, "").strip()
        if v:
            return v
    return None


def _kimi_key_from_files(home: Path) -> tuple[Optional[str], Optional[str]]:
    """Search common local config files for a Coding Plan key (sk-kimi-...)."""
    candidates = [
        home / ".config" / "kimi" / "credentials.json",
        home / ".config" / "kimi-code" / "credentials.json",
        home / ".kimi" / "credentials.json",
        home / ".kimi" / "config.json",
        home / ".config" / "kimi" / "config.toml",
        home / ".config" / "kimi-cli" / "config.toml",
        home / ".kimi" / "config.toml",
        home / ".kimi-code" / "config.toml",
    ]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        # JSON
        if path.suffix == ".json":
            try:
                data = json.loads(text)
            except Exception:
                data = None
            if isinstance(data, dict):
                for k in ("api_key", "apiKey", "KIMI_API_KEY", "KIMI_CODING_API_KEY", "key"):
                    v = data.get(k)
                    if isinstance(v, str) and v.strip():
                        return v.strip(), str(path)
        # TOML-ish / plain: look for sk-kimi- or key = "..."
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("#"):
                continue
            if "sk-kimi-" in s:
                # extract quoted or bare token
                for part in s.replace("=", " ").replace(":", " ").split():
                    tok = part.strip("\"'")
                    if tok.startswith("sk-kimi-"):
                        return tok, str(path)
            low = s.lower()
            if "api_key" in low or "apikey" in low:
                for part in s.replace("=", " ").replace(":", " ").split():
                    tok = part.strip("\"'")
                    if tok.startswith("sk-") and len(tok) > 10:
                        return tok, str(path)
    return None, None


def _kimi_oauth_from_kimi_code(home: Path) -> Optional[Credential]:
    """Load device-login OAuth tokens written by the official kimi-code CLI."""
    path = home / ".kimi-code" / "credentials" / "kimi-code.json"
    if not path.is_file():
        return None
    try:
        data = _read_json(path)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    access = data.get("access_token")
    refresh = data.get("refresh_token")
    if not access and not refresh:
        return None
    expires_at = data.get("expires_at")
    if isinstance(expires_at, (int, float)):
        expires_at = str(int(expires_at))
    elif expires_at is not None:
        expires_at = str(expires_at)
    return Credential(
        service="kimi",
        present=True,
        kind="oauth",
        path=str(path),
        access_token=access if isinstance(access, str) else None,
        refresh_token=refresh if isinstance(refresh, str) else None,
        expires_at=expires_at,
        # Public kimi-code CLI OAuth client id (device-code login).
        oidc_client_id=os.environ.get(
            "KIMI_CODE_OAUTH_CLIENT_ID", "17e5f671-d194-4dfb-9706-5516cb48c098"
        ),
        extra={
            "base_url": os.environ.get(
                "KIMI_BASE_URL",
                os.environ.get("KIMI_CODE_BASE_URL", "https://api.kimi.com/coding/v1"),
            ),
            "oauth_host": os.environ.get(
                "KIMI_CODE_OAUTH_HOST",
                os.environ.get("KIMI_OAUTH_HOST", "https://auth.kimi.com"),
            ),
            "scope": data.get("scope"),
            "token_type": data.get("token_type"),
        },
    )


def discover_kimi(home: Optional[Path] = None) -> Credential:
    home = home or _home()
    # 1) Explicit API keys win (env / config files)
    key = _kimi_key_from_env()
    path = None
    if key:
        path = "env:KIMI_CODING_API_KEY|KIMI_API_KEY"
    else:
        key, path = _kimi_key_from_files(home)
    if key:
        return Credential(
            service="kimi",
            present=True,
            kind="api_key",
            path=path,
            api_key=key,
            extra={"base_url": os.environ.get("KIMI_BASE_URL", "https://api.kimi.com/coding/v1")},
        )
    # 2) Official kimi-code CLI login (~/.kimi-code/credentials/kimi-code.json)
    oauth = _kimi_oauth_from_kimi_code(home)
    if oauth is not None:
        return oauth
    return Credential(service="kimi", present=False, kind="missing", path=None)


def discover_opencode_go(home: Optional[Path] = None) -> Credential:
    home = home or _home()
    auth_path = home / ".local" / "share" / "opencode" / "auth.json"
    account_path = home / ".local" / "share" / "opencode" / "account.json"
    key = None
    path = None
    if auth_path.is_file():
        try:
            data = _read_json(auth_path)
            entry = data.get("opencode-go") if isinstance(data, dict) else None
            if isinstance(entry, dict):
                key = entry.get("key") or entry.get("api_key")
                path = str(auth_path)
        except Exception:
            pass
    if not key and account_path.is_file():
        try:
            data = _read_json(account_path)
            accounts = data.get("accounts") if isinstance(data, dict) else None
            active = (data.get("active") or {}).get("opencode-go") if isinstance(data, dict) else None
            if isinstance(accounts, dict):
                if active and active in accounts:
                    cred = accounts[active].get("credential") or {}
                    key = cred.get("key")
                    path = str(account_path)
                else:
                    for acc in accounts.values():
                        if isinstance(acc, dict) and acc.get("serviceID") == "opencode-go":
                            cred = acc.get("credential") or {}
                            key = cred.get("key")
                            path = str(account_path)
                            break
        except Exception:
            pass
    # Optional dashboard scrape material
    workspace_id = os.environ.get("OPENCODE_GO_WORKSPACE_ID", "").strip()
    auth_cookie = os.environ.get("OPENCODE_GO_AUTH_COOKIE", "").strip()
    go_cfg_paths = [
        home / ".config" / "opencode" / "opencode-quota" / "opencode-go.json",
        home / ".config" / "opencode-quota" / "opencode-go.json",
    ]
    for cfg_path in go_cfg_paths:
        if workspace_id and auth_cookie:
            break
        if not cfg_path.is_file():
            continue
        try:
            cfg = _read_json(cfg_path)
            if isinstance(cfg, dict):
                workspace_id = workspace_id or str(cfg.get("workspaceId") or "").strip()
                auth_cookie = auth_cookie or str(cfg.get("authCookie") or "").strip()
        except Exception:
            pass

    if not key and not (workspace_id and auth_cookie):
        return Credential(service="opencode-go", present=False, kind="missing", path=str(auth_path))
    return Credential(
        service="opencode-go",
        present=True,
        kind="api_key" if key else "dashboard",
        path=path or str(auth_path),
        api_key=key,
        extra={
            "workspace_id": workspace_id or None,
            "auth_cookie": auth_cookie or None,
            "has_dashboard": bool(workspace_id and auth_cookie),
        },
    )


def discover_credentials(home: Optional[Path] = None) -> dict[str, Credential]:
    """Return credentials for all four services."""
    home = home or _home()
    return {
        "codex": discover_codex(home),
        "grok": discover_grok(home),
        "kimi": discover_kimi(home),
        "opencode-go": discover_opencode_go(home),
    }
