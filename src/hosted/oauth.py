"""MCP sign-in for the hosted server: FastMCP's Google/GitHub OAuth proxy
plus the access allowlist."""

import logging
from typing import Optional

from cryptography.fernet import Fernet

from .settings import HostedSettings, Identity, PROVIDERS

logger = logging.getLogger(__name__)

# Claude, Codex and other MCP clients come back here after signing in.
# The website's own sign-in uses WEB_CALLBACK_PATH, a sub-path of this one,
# so a GitHub OAuth app (which allows a single callback URL and its
# sub-paths) serves both.
MCP_CALLBACK_PATH = "/auth/callback"
WEB_CALLBACK_PATH = "/auth/callback/site"


def build_auth_provider(settings: HostedSettings):
    """Return a FastMCP auth provider that also enforces the allowlist."""
    from key_value.aio.stores.disk import DiskStore
    from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

    if settings.provider == "google":
        from fastmcp.server.auth.providers.google import GoogleProvider as base
    else:
        from fastmcp.server.auth.providers.github import GitHubProvider as base

    class AllowlistedProvider(base):
        async def load_access_token(self, token: str):
            access_token = await super().load_access_token(token)
            if access_token is None:
                return None
            identity = Identity.from_claims(settings.provider, access_token.claims or {})
            if not settings.allowlist.permits(identity):
                logger.warning(
                    "Refused MCP access for %s (not in the allowlist)",
                    identity.display if identity else "unknown account",
                )
                return None
            return access_token

    # Client registrations and upstream tokens survive restarts, encrypted
    # with a key derived from SCHOLAR_SECRET_KEY.
    storage = FernetEncryptionWrapper(
        key_value=DiskStore(directory=str(settings.data_dir / "oauth")),
        fernet=Fernet(settings.derive_key("oauth-storage")),
    )
    return AllowlistedProvider(
        client_id=settings.client_id,
        client_secret=settings.client_secret,
        base_url=settings.base_url,
        redirect_path=MCP_CALLBACK_PATH,
        required_scopes=PROVIDERS[settings.provider]["scopes"],
        client_storage=storage,
        jwt_signing_key=settings.derive_key("mcp-jwt").decode(),
    )


def current_identity(settings: HostedSettings) -> Optional[Identity]:
    """Identity of the user behind the current MCP request."""
    from fastmcp.server.dependencies import get_access_token

    access_token = get_access_token()
    if access_token is None:
        return None
    return Identity.from_claims(settings.provider, access_token.claims or {})
