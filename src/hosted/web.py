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

# Same visual world as thierrystpierre.com: a light "paper" theme, Archivo for
# text, IBM Plex Mono for labels, sections separated by rules instead of boxes.
SITE_URL = "https://thierrystpierre.com"

_FONTS = (
    "<link rel='preconnect' href='https://fonts.gstatic.com' crossorigin>"
    "<link rel='stylesheet' href='https://fonts.googleapis.com/css2?family=Archivo:"
    "wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap'>"
)

_STYLE = """
:root{color-scheme:light;--bg:#eef1f3;--panel:#fff;--panel-2:#f7f9fa;--ink:#0e1417;
  --mut:#61707a;--line:#d5dde1;--acc:#1b4965;--acc-soft:#9db6c4;--ok:#2d6a4f;--err:#9b2226;
  --mono:"IBM Plex Mono",ui-monospace,Menlo,monospace}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:"Archivo",system-ui,-apple-system,sans-serif;
  font-size:15px;line-height:1.62;-webkit-font-smoothing:antialiased}
a{color:var(--acc)}
a:focus-visible,button:focus-visible,input:focus-visible{outline:2px solid var(--acc);outline-offset:2px}
.col{max-width:660px;margin:0 auto;padding:0 26px}
header.top{background:var(--panel);border-bottom:1px solid var(--line);position:sticky;top:0;z-index:10}
header.top .col{display:flex;align-items:center;justify-content:space-between;gap:18px;height:54px}
.mark{font-family:var(--mono);font-size:.78rem;font-weight:500;text-decoration:none;color:var(--ink)}
.mark span{color:var(--mut)}
nav{display:flex;gap:1px;flex-wrap:wrap}
nav a{font-family:var(--mono);font-size:.73rem;color:var(--mut);text-decoration:none;padding:5px 9px;
  border-radius:2px;transition:color .15s,background .15s}
nav a:hover{color:var(--ink);background:var(--panel-2)}
nav a[aria-current]{color:var(--acc)}
main{padding:56px 0 24px}
h1{font-size:1.68rem;font-weight:600;letter-spacing:-.024em;line-height:1.15;margin:0 0 7px}
.role{font-family:var(--mono);font-size:.75rem;color:var(--acc);margin:0 0 14px}
.lede{margin:0 0 15px;max-width:62ch}
.muted{color:var(--mut)}
section{border-top:1px solid var(--line);padding:32px 0 0;margin-top:32px}
.oeil{font-family:var(--mono);font-size:.64rem;letter-spacing:.13em;text-transform:uppercase;
  color:var(--mut);margin:0 0 20px}
.etape{display:grid;grid-template-columns:32px 1fr;gap:14px;padding:11px 0;border-bottom:1px dotted var(--line)}
.etape:last-child{border-bottom:0}
.etape .n{font-family:var(--mono);font-size:.72rem;color:var(--acc-soft);padding-top:3px}
.etape p{margin:0;font-size:.9rem}
code,pre{font-family:var(--mono);font-size:.8rem}
code{background:var(--panel-2);border:1px solid var(--line);border-radius:2px;padding:1px 5px;word-break:break-all}
pre{background:var(--panel);border:1px solid var(--line);padding:12px 14px;overflow-x:auto;line-height:1.5}
.cle{padding:14px 0;border-bottom:1px dotted var(--line)}
.cle:last-of-type{border-bottom:0}
label{display:block;font-weight:600;font-size:.92rem}
.tete-cle{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.statut{font-family:var(--mono);font-size:.66rem;color:var(--ok)}
label.effacer{display:inline-flex;align-items:center;gap:4px;font-family:var(--mono);font-size:.66rem;
  font-weight:400;color:var(--mut);margin-left:auto;cursor:pointer}
input[type=password],input[type=text]{width:100%;padding:8px 10px;margin-top:6px;border:1px solid var(--line);
  border-radius:2px;background:var(--panel);color:var(--ink);font-family:var(--mono);font-size:.8rem}
input[type=password]:focus{border-color:var(--acc);outline:0}
.field-note{font-size:.82rem;color:var(--mut);margin:5px 0 0}
.field-note a{text-decoration:none;border-bottom:1px solid var(--acc-soft)}
.message{font-family:var(--mono);font-size:.75rem;padding:9px 12px;border-left:2px solid var(--ok);
  background:var(--panel);margin:0 0 20px}
.message.erreur{border-left-color:var(--err)}
button,.button{display:inline-block;font-family:var(--mono);font-size:.75rem;padding:8px 14px;border-radius:2px;
  border:1px solid var(--acc);background:var(--acc);color:#fff;text-decoration:none;cursor:pointer;
  transition:background .15s,color .15s}
button:hover,.button:hover{background:#13364b}
button.secondary{background:var(--panel);color:var(--acc)}
button.secondary:hover{background:var(--panel-2)}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:22px}
form.inline{display:inline}
footer{border-top:1px solid var(--line);margin-top:48px;padding:26px 0 60px;color:var(--mut);
  font-family:var(--mono);font-size:.71rem}
@media (max-width:560px){.col{padding:0 18px}header.top{position:static}
  header.top .col{height:auto;flex-wrap:wrap;padding-top:12px;padding-bottom:12px}main{padding-top:36px}}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
"""


def _page(title: str, body: str, status: int = 200, current: str = "") -> HTMLResponse:
    links = [("/", "scholar"), ("/keys", "mes clés"), (f"{SITE_URL}/logiciels/", "← logiciels")]
    nav = "".join(
        f"<a href='{href}'{' aria-current=page' if href == current else ''}>{label}</a>"
        for href, label in links
    )
    return HTMLResponse(
        "<!doctype html><html lang='fr'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{html.escape(title)}</title>{_FONTS}<style>{_STYLE}</style></head>"
        "<body><header class='top'><div class='col'>"
        f"<span><a class='mark' href='{SITE_URL}/'>stpierre.t</a> "
        "<a class='mark' href='/'><span>/ scholar</span></a></span>"
        f"<nav>{nav}</nav></div></header>"
        f"<main class='col'>{body}</main>"
        "<footer><div class='col'>Scholar MCP · "
        "<a href='https://github.com/tofunori/mcp-scholar'>github.com/tofunori/mcp-scholar</a> · "
        f"<a href='{SITE_URL}'>Thierry St-Pierre</a></div></footer></body></html>",
        status_code=status,
        headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY"},
    )


def _message(text: str, error: bool = False) -> str:
    return f"<p class='message{' erreur' if error else ''}'>{text}</p>"


def _connect_instructions(base_url: str) -> str:
    mcp_url = html.escape(f"{base_url}/mcp")
    return f"""
<section><p class='oeil'>Ajouter Scholar à Claude</p>
<div class='etape'><span class='n'>01</span><p>Dans Claude (application ou web), ouvrez
  <b>Customize &gt; Connectors</b>, puis <b>+ Add</b> &gt; <b>Add custom connector</b>.</p></div>
<div class='etape'><span class='n'>02</span><p>Nom : <code>Scholar</code>. URL : <code>{mcp_url}</code></p></div>
<div class='etape'><span class='n'>03</span><p>Cliquez sur <b>Connect</b> et connectez-vous avec le même
  compte qu'ici. Si Claude demande comment s'identifier, choisissez <b>Register automatically</b>.</p></div>
</section>
<section><p class='oeil'>Ajouter Scholar à Codex</p>
<p>Ajoutez ces lignes dans <code>~/.codex/config.toml</code> :</p>
<pre>[mcp_servers.scholar]
url = "{mcp_url}"</pre>
<p>Puis lancez <code>codex mcp login scholar</code> et connectez-vous.</p>
</section>
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
                f"<p class='muted'>Connecté : <b>{html.escape(session['display'])}</b></p>"
                "<a class='button' href='/keys'>Gérer mes clés API</a>"
            )
        else:
            account = (
                f"<a class='button' href='/login'>Se connecter avec {provider['label']}</a>"
            )
        return _page(
            "Scholar",
            "<h1>Scholar</h1>"
            "<p class='role'>serveur MCP · recherche bibliographique</p>"
            "<p class='lede'>Recherche d'articles scientifiques dans OpenAlex, Semantic "
            "Scholar, Crossref, Europe PMC, NASA ADS, CORE et Scopus, depuis Claude ou "
            "Codex.</p>"
            f"{account}"
            "<section><p class='oeil'>Comment ça marche</p>"
            "<div class='etape'><span class='n'>01</span><p>Connectez-vous et ajoutez vos "
            "propres clés API. Vos recherches utilisent uniquement vos clés; les sources "
            "sans clé sont ignorées (Crossref et Europe PMC n'en demandent pas).</p></div>"
            "<div class='etape'><span class='n'>02</span><p>Ajoutez le connecteur dans "
            "Claude ou Codex (voir plus bas).</p></div></section>"
            f"{_connect_instructions(settings.base_url)}",
            current="/",
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
                "<span class='statut'>✓ enregistrée</span>"
                f"<label class='effacer'><input type='checkbox' name='clear_{name}'> effacer</label>"
                if name in stored
                else ""
            )
            fields.append(
                f"<div class='cle'><div class='tete-cle'><label for='{name}'>{html.escape(meta['label'])}</label>{saved}</div>"
                f"<input type='password' id='{name}' name='{name}' autocomplete='off' "
                f"placeholder='{'Laisser vide pour garder la clé actuelle' if name in stored else 'Coller la clé ici'}'>"
                f"<p class='field-note'>{html.escape(meta['note'])} "
                f"<a href='{html.escape(meta['help_url'])}' target='_blank' rel='noopener'>Obtenir une clé</a></p></div>"
            )
        csrf = signer.csrf_token(session)
        return _page(
            "Mes clés API · Scholar",
            "<h1>Mes clés API</h1>"
            f"<p class='role'>{html.escape(session['display'])}</p>"
            f"{message}"
            "<p class='lede'>Vos clés sont chiffrées sur le serveur et servent uniquement à "
            "vos propres recherches. Personne d'autre ne les utilise.</p>"
            "<section><p class='oeil'>Clés par source</p>"
            "<form method='post' action='/keys'>"
            f"<input type='hidden' name='csrf' value='{csrf}'>"
            + "".join(fields)
            + "<div class='row'><button type='submit'>Enregistrer</button>"
            "<button class='secondary' type='submit' name='action' value='clear_all'>"
            "Effacer toutes mes clés</button></div></form>"
            "<form method='post' action='/logout' class='row'>"
            f"<input type='hidden' name='csrf' value='{csrf}'>"
            "<button class='secondary' type='submit'>Se déconnecter</button></form></section>"
            f"{_connect_instructions(settings.base_url)}",
            status,
            current="/keys",
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
            return keys_page(session, _message("Formulaire expiré, réessayez.", error=True), 400)

        if form.get("action") == "clear_all":
            keystore.delete(session["uid"])
            return keys_page(session, _message("Toutes vos clés ont été effacées."))

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
                _message(
                    "Une des clés contient des caractères invalides. Rien n'a été enregistré.",
                    error=True,
                ),
                400,
            )
        keystore.set(session["uid"], session["display"], updated)
        return keys_page(session, _message("Clés enregistrées."))

    @mcp.custom_route("/logout", methods=["POST"])
    async def logout(request: Request) -> Response:
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie(SESSION_COOKIE)
        return response
