"""Settings, user identity and access allowlist for the hosted server.

The hosted mode turns server_http.py into a multi-user service: people sign
in with Google or GitHub, store their own API keys on the /keys page, and
every tool call runs with the caller's keys only.
"""

import base64
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# Provider endpoints used by the website's own sign-in flow. The MCP sign-in
# goes through FastMCP's GoogleProvider/GitHubProvider instead.
PROVIDERS = {
    "google": {
        "label": "Google",
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "userinfo_url": "https://www.googleapis.com/oauth2/v2/userinfo",
        "scopes": ["openid", "https://www.googleapis.com/auth/userinfo.email"],
    },
    "github": {
        "label": "GitHub",
        "authorize_url": "https://github.com/login/oauth/authorize",
        "token_url": "https://github.com/login/oauth/access_token",
        "userinfo_url": "https://api.github.com/user",
        "scopes": ["read:user"],
    },
}


def _split(value: str) -> frozenset[str]:
    return frozenset(
        item.strip().lower() for item in value.replace(";", ",").split(",") if item.strip()
    )


@dataclass(frozen=True)
class Identity:
    """A signed-in user, the same whether they came through MCP or the site."""

    user_id: str  # "<provider>:<stable account id>"
    email: Optional[str] = None  # set only when the provider verified it
    login: Optional[str] = None  # GitHub username
    name: Optional[str] = None

    @property
    def display(self) -> str:
        return self.email or self.login or self.name or self.user_id

    @classmethod
    def from_google(cls, userinfo: dict) -> Optional["Identity"]:
        account_id = userinfo.get("id") or userinfo.get("sub")
        if not account_id:
            return None
        email = userinfo.get("email") if userinfo.get("verified_email") else None
        return cls(
            user_id=f"google:{account_id}",
            email=email.lower() if email else None,
            name=userinfo.get("name"),
        )

    @classmethod
    def from_github(cls, user: dict) -> Optional["Identity"]:
        if not user.get("id"):
            return None
        # GitHub only lets people make a verified address public.
        email = user.get("email")
        login = user.get("login")
        return cls(
            user_id=f"github:{user['id']}",
            email=email.lower() if email else None,
            login=login.lower() if login else None,
            name=user.get("name"),
        )

    @classmethod
    def from_claims(cls, provider: str, claims: dict) -> Optional["Identity"]:
        """Build the identity from FastMCP's verified access-token claims."""
        if provider == "google":
            return cls.from_google(claims.get("google_user_data") or {})
        if provider == "github":
            return cls.from_github(claims.get("github_user_data") or {})
        return None


@dataclass(frozen=True)
class Allowlist:
    """Who may use the hosted server. Empty means nobody (fail closed)."""

    emails: frozenset[str] = frozenset()
    domains: frozenset[str] = frozenset()
    github_users: frozenset[str] = frozenset()
    allow_all: bool = False

    @classmethod
    def from_env(cls) -> "Allowlist":
        emails = _split(os.getenv("SCHOLAR_ALLOWED_EMAILS", ""))
        return cls(
            emails=emails - {"*"},
            domains=frozenset(
                d.lstrip("@") for d in _split(os.getenv("SCHOLAR_ALLOWED_DOMAINS", ""))
            ),
            github_users=_split(os.getenv("SCHOLAR_ALLOWED_GITHUB_USERS", "")),
            allow_all="*" in emails,
        )

    @property
    def is_empty(self) -> bool:
        return not (self.allow_all or self.emails or self.domains or self.github_users)

    def permits(self, identity: Optional[Identity]) -> bool:
        if identity is None:
            return False
        if self.allow_all:
            return True
        if identity.login and identity.user_id.startswith("github:"):
            if identity.login in self.github_users:
                return True
        if identity.email:
            if identity.email in self.emails:
                return True
            if identity.email.rsplit("@", 1)[-1] in self.domains:
                return True
        return False


@dataclass(frozen=True)
class HostedSettings:
    provider: str
    client_id: str
    client_secret: str
    base_url: str
    secret_key: str
    allowlist: Allowlist
    data_dir: Path
    # Contact email sent to Crossref / Europe PMC on behalf of every user.
    contact_email: str = ""
    errors: tuple[str, ...] = field(default=())

    @classmethod
    def from_env(cls) -> Optional["HostedSettings"]:
        """Read SCHOLAR_AUTH and friends; None when hosted mode is off."""
        provider = os.getenv("SCHOLAR_AUTH", "").strip().lower()
        if provider in ("", "none", "off"):
            return None

        settings = cls(
            provider=provider,
            client_id=os.getenv("SCHOLAR_OAUTH_CLIENT_ID", "").strip(),
            client_secret=os.getenv("SCHOLAR_OAUTH_CLIENT_SECRET", "").strip(),
            base_url=os.getenv("SCHOLAR_BASE_URL", "").strip().rstrip("/"),
            secret_key=os.getenv("SCHOLAR_SECRET_KEY", "").strip(),
            allowlist=Allowlist.from_env(),
            data_dir=Path(os.getenv("DATA_DIR", "./data")),
            contact_email=os.getenv("OPENALEX_MAILTO", "").strip(),
        )
        return settings.validated()

    def validated(self) -> "HostedSettings":
        errors = []
        if self.provider not in PROVIDERS:
            errors.append(f"SCHOLAR_AUTH must be one of {sorted(PROVIDERS)} or none")
        if not self.client_id or not self.client_secret:
            errors.append("SCHOLAR_OAUTH_CLIENT_ID and SCHOLAR_OAUTH_CLIENT_SECRET are required")
        if not self.base_url.startswith(("https://", "http://localhost", "http://127.0.0.1")):
            errors.append("SCHOLAR_BASE_URL must be the public https:// URL of the server")
        if len(self.secret_key) < 32:
            errors.append("SCHOLAR_SECRET_KEY must be at least 32 characters")
        if self.allowlist.is_empty:
            errors.append(
                "Set SCHOLAR_ALLOWED_EMAILS, SCHOLAR_ALLOWED_DOMAINS or "
                "SCHOLAR_ALLOWED_GITHUB_USERS (use SCHOLAR_ALLOWED_EMAILS=* to allow anyone)"
            )
        if errors:
            raise ValueError("Hosted mode misconfigured:\n- " + "\n- ".join(errors))
        return self

    def derive_key(self, purpose: str) -> bytes:
        """A separate Fernet-format key per purpose, derived from the secret."""
        raw = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"scholar-mcp",
            info=purpose.encode(),
        ).derive(self.secret_key.encode())
        return base64.urlsafe_b64encode(raw)
