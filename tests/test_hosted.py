"""Offline tests for the hosted multi-user mode (sign-in, allowlist, keys)."""

import asyncio
import re
import sqlite3
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastmcp import FastMCP
from fastmcp.server.auth import AccessToken
from fastmcp.server.auth.providers.google import GoogleProvider
from starlette.testclient import TestClient

from src import server_http
from src.config import config
from src.hosted import Allowlist, HostedSettings, Identity, KeyStore
from src.hosted.keystore import clean_key
from src.hosted.oauth import WEB_CALLBACK_PATH, build_auth_provider
from src.hosted.web import SESSION_COOKIE, register_routes
from src.rate_limiting import reset_limiters
from src.services import Orchestrator
from src.sources import SemanticScholarSource

SECRET = "s" * 40


@pytest.fixture(autouse=True)
def _fresh_limiters():
    reset_limiters()
    yield
    reset_limiters()


def make_settings(tmp_path, **overrides) -> HostedSettings:
    values = dict(
        provider="google",
        client_id="client.apps.googleusercontent.com",
        client_secret="client-secret",
        base_url="http://localhost:8323",
        secret_key=SECRET,
        allowlist=Allowlist(domains=frozenset({"uni.example"})),
        data_dir=tmp_path,
        contact_email="prof@uni.example",
    )
    values.update(overrides)
    return HostedSettings(**values).validated()


def google_user(email="ana@uni.example", verified=True, account_id="111"):
    return {"id": account_id, "email": email, "verified_email": verified, "name": "Ana"}


# --- Allowlist and identity ---------------------------------------------------


def test_allowlist_matches_domain_email_and_github_login():
    allow = Allowlist(
        emails=frozenset({"guest@gmail.com"}),
        domains=frozenset({"uni.example"}),
        github_users=frozenset({"octocat"}),
    )
    assert allow.permits(Identity.from_google(google_user("ana@uni.example")))
    assert allow.permits(Identity.from_google(google_user("Guest@Gmail.com")))
    assert allow.permits(Identity.from_github({"id": 1, "login": "OctoCat"}))
    assert not allow.permits(Identity.from_google(google_user("eve@other.example")))
    assert not allow.permits(Identity.from_github({"id": 2, "login": "someone"}))
    assert not allow.permits(None)


def test_unverified_google_email_is_not_trusted():
    identity = Identity.from_google(google_user(verified=False))
    assert identity.email is None
    assert not Allowlist(domains=frozenset({"uni.example"})).permits(identity)


def test_allowlist_from_env(monkeypatch):
    monkeypatch.setenv("SCHOLAR_ALLOWED_DOMAINS", "@Uni.Example, other.example")
    monkeypatch.setenv("SCHOLAR_ALLOWED_EMAILS", "")
    monkeypatch.delenv("SCHOLAR_ALLOWED_GITHUB_USERS", raising=False)
    allow = Allowlist.from_env()
    assert allow.domains == {"uni.example", "other.example"}
    assert not allow.allow_all

    monkeypatch.setenv("SCHOLAR_ALLOWED_EMAILS", "*")
    assert Allowlist.from_env().allow_all


def test_hosted_mode_refuses_to_start_half_configured(tmp_path, monkeypatch):
    monkeypatch.delenv("SCHOLAR_AUTH", raising=False)
    assert HostedSettings.from_env() is None

    with pytest.raises(ValueError) as err:
        make_settings(tmp_path, secret_key="short", allowlist=Allowlist())
    assert "SCHOLAR_SECRET_KEY" in str(err.value)
    assert "SCHOLAR_ALLOWED_EMAILS" in str(err.value)


def test_derived_keys_differ_per_purpose(tmp_path):
    settings = make_settings(tmp_path)
    assert settings.derive_key("keystore") != settings.derive_key("web-session")
    assert settings.derive_key("keystore") == make_settings(tmp_path).derive_key("keystore")


# --- Key store -------------------------------------------------------------------


def test_keystore_round_trip_is_encrypted_at_rest(tmp_path):
    settings = make_settings(tmp_path)
    store = KeyStore(tmp_path / "keys.db", settings.derive_key("keystore"))
    store.set("google:1", "ana@uni.example", {"openalex_api_key": "OA-SECRET", "bogus": "x"})

    assert store.get("google:1") == {"openalex_api_key": "OA-SECRET"}
    assert store.get("google:2") == {}
    raw = sqlite3.connect(tmp_path / "keys.db").execute("SELECT keys FROM user_keys").fetchone()[0]
    assert b"OA-SECRET" not in raw

    # A different secret cannot read the keys.
    other = make_settings(tmp_path, secret_key="t" * 40)
    assert KeyStore(tmp_path / "keys.db", other.derive_key("keystore")).get("google:1") == {}

    store.set("google:1", "ana@uni.example", {})
    assert store.users() == []


def test_clean_key_rejects_garbage():
    assert clean_key("  abc123 \n") == "abc123"
    with pytest.raises(ValueError):
        clean_key("abc def")
    with pytest.raises(ValueError):
        clean_key("x" * 300)


# --- No fallback to the operator's keys --------------------------------------------


def test_orchestrator_without_config_keys_ignores_operator_keys(monkeypatch):
    monkeypatch.setattr(config, "scopus_api_key", "OPERATOR-SCOPUS")
    monkeypatch.setattr(config, "openalex_api_key", "OPERATOR-OPENALEX")
    monkeypatch.setattr(config, "s2_api_key", "OPERATOR-S2")

    orch = Orchestrator(use_config_keys=False, openalex_api_key="STUDENT-OA")
    assert orch.openalex_api_key == "STUDENT-OA"
    assert orch.scopus_api_key == ""
    assert orch.s2_api_key == ""
    assert "scopus" not in orch.get_available_sources()

    assert Orchestrator().scopus_api_key == "OPERATOR-SCOPUS"


def test_semantic_scholar_empty_key_does_not_read_env(monkeypatch):
    monkeypatch.setenv("S2_API_KEY", "OPERATOR-S2")
    assert SemanticScholarSource("").api_key is None
    assert SemanticScholarSource().api_key == "OPERATOR-S2"


def test_hosted_get_orchestrator_uses_the_callers_keys(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    store = KeyStore(tmp_path / "keys.db", settings.derive_key("keystore"))
    store.set("google:111", "ana@uni.example", {"openalex_api_key": "ANA-KEY"})
    monkeypatch.setattr(config, "openalex_api_key", "OPERATOR-OPENALEX")
    monkeypatch.setattr(server_http, "hosted", settings)
    monkeypatch.setattr(server_http, "keystore", store)

    caller = {"identity": Identity.from_google(google_user())}
    monkeypatch.setattr(server_http, "current_identity", lambda s: caller["identity"])
    assert server_http.get_orchestrator().openalex_api_key == "ANA-KEY"

    caller["identity"] = Identity.from_google(google_user("bob@uni.example", account_id="222"))
    assert server_http.get_orchestrator().openalex_api_key == ""

    caller["identity"] = None
    with pytest.raises(PermissionError):
        server_http.get_orchestrator()


# --- MCP sign-in enforces the allowlist ----------------------------------------------


def test_mcp_provider_rejects_accounts_outside_the_allowlist(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    provider = build_auth_provider(settings)
    claims = {"google_user_data": google_user()}

    async def fake_load(self, token):
        return AccessToken(token=token, client_id="c", scopes=["openid"], claims=claims)

    monkeypatch.setattr(GoogleProvider, "load_access_token", fake_load)
    assert asyncio.run(provider.load_access_token("t")) is not None

    claims["google_user_data"] = google_user("eve@other.example")
    assert asyncio.run(provider.load_access_token("t")) is None


# --- Website sign-in and the keys page ---------------------------------------------------


@pytest.fixture
def site(tmp_path):
    settings = make_settings(tmp_path)
    store = KeyStore(tmp_path / "keys.db", settings.derive_key("keystore"))
    upstream = {"user": google_user()}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            assert parse_qs(request.content.decode())["code"] == ["good-code"]
            return httpx.Response(200, json={"access_token": "upstream-token"})
        if request.url.host == "www.googleapis.com":
            assert request.headers["authorization"] == "Bearer upstream-token"
            return httpx.Response(200, json=upstream["user"])
        return httpx.Response(404)

    mcp = FastMCP("test")
    register_routes(
        mcp, settings, store,
        http_client=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    client = TestClient(mcp.http_app(), base_url="http://localhost:8323")
    return client, store, upstream


def sign_in(client) -> httpx.Response:
    login = client.get("/login", follow_redirects=False)
    assert login.status_code == 302
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
    return client.get(
        WEB_CALLBACK_PATH, params={"code": "good-code", "state": state}, follow_redirects=False
    )


def csrf_of(page: str) -> str:
    return re.search(r"name='csrf' value='([0-9a-f]+)'", page).group(1)


def test_keys_page_requires_sign_in(site):
    client, _, _ = site
    assert client.get("/", follow_redirects=False).status_code == 200
    response = client.get("/keys", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_sign_in_then_save_and_clear_keys(site):
    client, store, _ = site
    callback = sign_in(client)
    assert callback.status_code == 303
    assert SESSION_COOKIE in callback.cookies

    page = client.get("/keys")
    assert page.status_code == 200
    assert "ana@uni.example" in page.text
    csrf = csrf_of(page.text)

    saved = client.post("/keys", data={"csrf": csrf, "openalex_api_key": " OA-1 ", "s2_api_key": "S2-1"})
    assert saved.status_code == 200
    assert store.get("google:111") == {"openalex_api_key": "OA-1", "s2_api_key": "S2-1"}
    assert "OA-1" not in saved.text  # never echoed back

    # Empty field keeps the key; the per-field checkbox clears it.
    client.post("/keys", data={"csrf": csrf, "clear_s2_api_key": "on"})
    assert store.get("google:111") == {"openalex_api_key": "OA-1"}

    client.post("/keys", data={"csrf": csrf, "action": "clear_all"})
    assert store.get("google:111") == {}


def test_keys_post_without_csrf_is_rejected(site):
    client, store, _ = site
    sign_in(client)
    response = client.post("/keys", data={"openalex_api_key": "OA-1"})
    assert response.status_code == 400
    assert store.get("google:111") == {}


def test_sign_in_outside_the_allowlist_is_refused(site):
    client, _, upstream = site
    upstream["user"] = google_user("eve@other.example")
    response = sign_in(client)
    assert response.status_code == 403
    assert SESSION_COOKIE not in response.cookies


def test_callback_with_forged_state_is_refused(site):
    client, _, _ = site
    client.get("/login", follow_redirects=False)
    response = client.get(WEB_CALLBACK_PATH, params={"code": "good-code", "state": "forged"})
    assert response.status_code == 400
