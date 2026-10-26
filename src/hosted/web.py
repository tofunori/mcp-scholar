"""Website of the hosted server: sign-in and the "Mes clés" page.

Pages are in French because they are shown to students; everything else
follows the project's English convention.
"""

import base64
import hashlib
import hmac
import html
import json
import logging
import secrets
import time
from typing import Callable, Optional
from urllib.parse import urlencode

import httpx
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from .keystore import KEY_FIELDS, KeyStore, clean_key
from .oauth import WEB_CALLBACK_PATH
from .settings import HostedSettings, Identity, PROVIDERS

logger = logging.getLogger(__name__)

SESSION_COOKIE = "scholar_session"
STATE_COOKIE = "scholar_oauth_state"
SESSION_TTL = 7 * 24 * 3600
STATE_TTL = 600


# --- Signed cookies -------------------------------------------------------


class Signer:
    """HMAC-signed, expiring JSON values for cookies."""

    def __init__(self, key: bytes):
        self._key = key

    def _mac(self, data: bytes) -> str:
        return hmac.new(self._key, data, hashlib.sha256).hexdigest()

    def sign(self, value: dict, ttl: int) -> str:
        payload = dict(value, exp=int(time.time()) + ttl)
        data = base64.urlsafe_b64encode(json.dumps(payload).encode())
        return f"{data.decode()}.{self._mac(data)}"

    def unsign(self, token: Optional[str]) -> Optional[dict]:
        if not token or "." not in token:
            return None
        data, mac = token.rsplit(".", 1)
        if not hmac.compare_digest(mac, self._mac(data.encode())):
            return None
        try:
            payload = json.loads(base64.urlsafe_b64decode(data))
        except ValueError:
            return None
        if not isinstance(payload, dict) or payload.get("exp", 0) < time.time():
            return None
        return payload

    def csrf_token(self, session: dict) -> str:
        return self._mac(f"csrf:{session['uid']}:{session['exp']}".encode())


# --- Pages ----------------------------------------------------------------

_STYLE = """
:root { color-scheme: light dark; --fg:#1f2328; --muted:#59636e; --bg:#ffffff;
  --card:#f6f8fa; --line:#d1d9e0; --accent:#0969da; --ok:#1a7f37; }
@media (prefers-color-scheme: dark) { :root { --fg:#e6edf3; --muted:#9198a1;
  --bg:#0d1117; --card:#151b23; --line:#3d444d; --accent:#4493f8; --ok:#3fb950; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg);
  font:16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 720px; margin: 0 auto; padding: 32px 16px 64px; }
h1 { font-size: 1.6rem; margin: 0 0 4px; } h2 { font-size: 1.15rem; margin-top: 32px; }
p.muted, .muted { color: var(--muted); }
.card { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:16px; margin:16px 0; }
code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .9em; }
pre { background:var(--card); border:1px solid var(--line); border-radius:6px; padding:12px; overflow-x:auto; }
label { display:block; font-weight:600; margin-top:16px; }
input[type=password], input[type=text] { width:100%; padding:8px 10px; margin-top:4px;
  border:1px solid var(--line); border-radius:6px; background:var(--bg); color:var(--fg); font:inherit; }
.field-note { font-size:.9rem; color:var(--muted); margin:4px 0 0; }
.saved { color: var(--ok); font-weight: 600; font-size: .9rem; }
button, .button { display:inline-block; margin-top:20px; padding:8px 16px; border-radius:6px;
  border:1px solid var(--accent); background:var(--accent); color:#fff; font:inherit;
  text-decoration:none; cursor:pointer; }
button.secondary { background:transparent; color:var(--accent); }
.row { display:flex; gap:12px; flex-wrap:wrap; align-items:center; }
a { color: var(--accent); }
"""


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html lang='fr'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{html.escape(title)}</title><style>{_STYLE}</style></head>"
        f"<body><main>{body}</main></body></html>",
        status_code=status,
        headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY"},
    )


def _connect_instructions(base_url: str) -> str:
    mcp_url = html.escape(f"{base_url}/mcp")
    return f"""
<h2>Ajouter Scholar à Claude</h2>
<ol>
  <li>Dans Claude (application ou web), ouvrez <b>Customize &gt; Connectors</b>,
      puis <b>+ Add</b> &gt; <b>Add custom connector</b>.</li>
  <li>Nom : <code>Scholar</code>. URL : <code>{mcp_url}</code></li>
  <li>Cliquez sur <b>Connect</b> et connectez-vous avec le même compte qu'ici.
      Si Claude demande comment s'identifier, choisissez <b>Register automatically</b>.</li>
</ol>
<h2>Ajouter Scholar à Codex</h2>
<p>Ajoutez ces lignes dans <code>~/.codex/config.toml</code> :</p>
<pre>[mcp_servers.scholar]
url = "{mcp_url}"</pre>
<p>Puis lancez <code>codex mcp login scholar</code> et connectez-vous.</p>
"""


def register_routes(
    mcp,
    settings: HostedSettings,
    keystore: KeyStore,
    http_client: Callable[[], httpx.AsyncClient] = lambda: httpx.AsyncClient(timeout=15),
) -> None:
    """Add the website routes to the FastMCP server."""
    signer = Signer(settings.derive_key("web-session"))
    provider = PROVIDERS[settings.provider]
    redirect_uri = f"{settings.base_url}{WEB_CALLBACK_PATH}"
    secure_cookies = settings.base_url.startswith("https://")

    def set_cookie(response: Response, name: str, value: str, ttl: int) -> None:
        response.set_cookie(
            name, value, max_age=ttl, httponly=True, secure=secure_cookies, samesite="lax"
        )

    def session_of(request: Request) -> Optional[dict]:
        session = signer.unsign(request.cookies.get(SESSION_COOKIE))
        if session is None:
            return None
        identity = Identity(
            user_id=session["uid"], email=session.get("email"), login=session.get("login")
        )
        # Re-check on every page so removing someone from the list takes effect.
        return session if settings.allowlist.permits(identity) else None

    @mcp.custom_route("/", methods=["GET"])
    async def home(request: Request) -> Response:
        session = session_of(request)
        if session:
            account = (
                f"<p>Connecté : <b>{html.escape(session['display'])}</b>. "
                "<a href='/keys'>Gérer mes clés API</a></p>"
            )
        else:
            account = (
                f"<a class='button' href='/login'>Se connecter avec {provider['label']}</a>"
            )
        return _page(
            "Scholar",
            "<h1>Scholar</h1>"
            "<p class='muted'>Recherche d'articles scientifiques dans OpenAlex, Semantic "
            "Scholar, Crossref, Europe PMC, NASA ADS, CORE et Scopus, depuis Claude ou "
            "Codex.</p>"
            "<div class='card'><b>1.</b> Connectez-vous et ajoutez vos propres clés API. "
            "Vos recherches utilisent uniquement vos clés; les sources sans clé sont "
            "ignorées (Crossref et Europe PMC n'en demandent pas).<br>"
            "<b>2.</b> Ajoutez le connecteur dans Claude ou Codex (voir plus bas).</div>"
            f"{account}{_connect_instructions(settings.base_url)}",
        )

    @mcp.custom_route("/login", methods=["GET"])
    async def login(request: Request) -> Response:
        state = secrets.token_urlsafe(24)
        query = urlencode(
            {
                "client_id": settings.client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": " ".join(provider["scopes"]),
                "state": state,
            }
        )
        response = RedirectResponse(f"{provider['authorize_url']}?{query}", status_code=302)
        set_cookie(response, STATE_COOKIE, signer.sign({"state": state}, STATE_TTL), STATE_TTL)
        return response

    async def fetch_identity(code: str) -> Optional[Identity]:
        async with http_client() as client:
            token_response = await client.post(
                provider["token_url"],
                data={
                    "client_id": settings.client_id,
                    "client_secret": settings.client_secret,
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
                headers={"Accept": "application/json"},
            )
            if token_response.status_code != 200:
                logger.warning("Sign-in token exchange failed: HTTP %s", token_response.status_code)
                return None
            access_token = token_response.json().get("access_token")
            if not access_token:
                return None
            user_response = await client.get(
                provider["userinfo_url"],
                headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            )
            if user_response.status_code != 200:
                logger.warning("Sign-in user lookup failed: HTTP %s", user_response.status_code)
                return None
            user = user_response.json()
        if settings.provider == "google":
            return Identity.from_google(user)
        return Identity.from_github(user)

    @mcp.custom_route(WEB_CALLBACK_PATH, methods=["GET"])
    async def callback(request: Request) -> Response:
        expected = signer.unsign(request.cookies.get(STATE_COOKIE))
        state = request.query_params.get("state", "")
        code = request.query_params.get("code")
        if not expected or not code or not hmac.compare_digest(expected["state"], state):
            return _page(
                "Connexion expirée",
                "<h1>Connexion expirée</h1><p><a href='/login'>Réessayer</a></p>",
                400,
            )
        try:
            identity = await fetch_identity(code)
        except httpx.HTTPError as e:
            logger.warning("Sign-in failed: %s", type(e).__name__)
            identity = None
        if identity is None:
            return _page(
                "Connexion impossible",
                "<h1>Connexion impossible</h1><p>Le fournisseur n'a pas confirmé votre "
                "compte. <a href='/login'>Réessayer</a></p>",
                502,
            )
        if not settings.allowlist.permits(identity):
            logger.warning("Refused web sign-in for %s (not in the allowlist)", identity.display)
            return _page(
                "Accès refusé",
                f"<h1>Accès refusé</h1><p>Le compte <b>{html.escape(identity.display)}</b> "
                "n'est pas autorisé. Demandez à votre enseignant de l'ajouter, ou "
                "connectez-vous avec votre compte universitaire.</p>",
                403,
            )
        session = {
            "uid": identity.user_id,
            "display": identity.display,
            "email": identity.email,
            "login": identity.login,
        }
        response = RedirectResponse("/keys", status_code=303)
        set_cookie(response, SESSION_COOKIE, signer.sign(session, SESSION_TTL), SESSION_TTL)
        response.delete_cookie(STATE_COOKIE)
        return response

    def keys_page(session: dict, message: str = "", status: int = 200) -> HTMLResponse:
        stored = keystore.get(session["uid"])
        fields = []
        for name, meta in KEY_FIELDS.items():
            saved = (
                f" <span class='saved'>✓ enregistrée</span>"
                f" <label style='display:inline;font-weight:400'>"
                f"<input type='checkbox' name='clear_{name}'> effacer</label>"
                if name in stored
                else ""
            )
            fields.append(
                f"<label for='{name}'>{html.escape(meta['label'])}{saved}</label>"
                f"<input type='password' id='{name}' name='{name}' autocomplete='off' "
                f"placeholder='{'Laisser vide pour garder la clé actuelle' if name in stored else 'Coller la clé ici'}'>"
                f"<p class='field-note'>{html.escape(meta['note'])} "
                f"<a href='{html.escape(meta['help_url'])}' target='_blank' rel='noopener'>Obtenir une clé</a></p>"
            )
        csrf = signer.csrf_token(session)
        return _page(
            "Mes clés API · Scholar",
            "<h1>Mes clés API</h1>"
            f"<p class='muted'>Connecté : {html.escape(session['display'])}</p>"
            f"{message}"
            "<p>Vos clés sont chiffrées sur le serveur et servent uniquement à vos "
            "propres recherches. Personne d'autre ne les utilise.</p>"
            "<form method='post' action='/keys'>"
            f"<input type='hidden' name='csrf' value='{csrf}'>"
            + "".join(fields)
            + "<div class='row'><button type='submit'>Enregistrer</button>"
            "<button class='secondary' type='submit' name='action' value='clear_all'>"
            "Effacer toutes mes clés</button></div></form>"
            "<form method='post' action='/logout'>"
            f"<input type='hidden' name='csrf' value='{csrf}'>"
            "<button class='secondary' type='submit'>Se déconnecter</button></form>"
            f"{_connect_instructions(settings.base_url)}",
            status,
        )

    @mcp.custom_route("/keys", methods=["GET", "POST"])
    async def keys(request: Request) -> Response:
        session = session_of(request)
        if session is None:
            return RedirectResponse("/login", status_code=303)
        if request.method == "GET":
            return keys_page(session)

        form = await request.form()
        if not hmac.compare_digest(str(form.get("csrf", "")), signer.csrf_token(session)):
            return keys_page(session, "<p><b>Formulaire expiré, réessayez.</b></p>", 400)

        if form.get("action") == "clear_all":
            keystore.delete(session["uid"])
            return keys_page(session, "<p class='saved'>Toutes vos clés ont été effacées.</p>")

        updated = keystore.get(session["uid"])
        try:
            for name in KEY_FIELDS:
                value = clean_key(str(form.get(name, "")))
                if value:
                    updated[name] = value
                elif form.get(f"clear_{name}"):
                    updated.pop(name, None)
        except ValueError:
            return keys_page(
                session,
                "<p><b>Une des clés contient des caractères invalides. Rien n'a été "
                "enregistré.</b></p>",
                400,
            )
        keystore.set(session["uid"], session["display"], updated)
        return keys_page(session, "<p class='saved'>Clés enregistrées.</p>")

    @mcp.custom_route("/logout", methods=["POST"])
    async def logout(request: Request) -> Response:
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie(SESSION_COOKIE)
        return response
