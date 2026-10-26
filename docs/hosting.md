# Hosting Scholar for a class

Hosted mode turns `src/server_http.py` into a small multi-user service:

- People sign in with **Google** or **GitHub**, both on the website and from
  Claude or Codex (MCP OAuth). Only accounts on your allowlist get in.
- Each person saves **their own API keys** on `https://<your-host>/keys`.
  Their tool calls use only those keys. Sources they have no key for are
  skipped; Crossref and Europe PMC need no key. The keys in the server's
  environment are never used for anyone.
- Keys are encrypted at rest (Fernet, key derived from `SCHOLAR_SECRET_KEY`)
  and never logged. Each API key gets its own rate limiter.

The recommended setup runs in Docker behind a Cloudflare Tunnel, so no port
is opened on the host or the router.

## 1. Create the OAuth app

Pick one provider and set `SCHOLAR_AUTH` to match.

**Google** (Google Cloud console > APIs & Services > Credentials):

1. OAuth consent screen: user type *External*, scopes `openid` and
   `userinfo.email`. Add yourself as a test user while testing, then publish
   the app so students can sign in.
2. Create an *OAuth client ID* of type *Web application* with these two
   authorized redirect URIs:
   - `https://<your-host>/auth/callback` (Claude and Codex)
   - `https://<your-host>/auth/callback/site` (the website)

**GitHub** (Settings > Developer settings > OAuth Apps > New OAuth App):
set the *Authorization callback URL* to `https://<your-host>/auth/callback`.
The website's callback is a sub-path of it, so one URL covers both.

## 2. Configure

```bash
cp .env.hosted.example .env.hosted
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # SCHOLAR_SECRET_KEY
```

| Variable | Meaning |
|---|---|
| `SCHOLAR_AUTH` | `google` or `github` (unset = personal mode, no sign-in) |
| `SCHOLAR_OAUTH_CLIENT_ID`, `SCHOLAR_OAUTH_CLIENT_SECRET` | From step 1 |
| `SCHOLAR_BASE_URL` | Public URL, e.g. `https://scholar.example.org` (no `/mcp`) |
| `SCHOLAR_SECRET_KEY` | 32+ random characters. Changing it signs everyone out and makes stored keys unreadable |
| `SCHOLAR_ALLOWED_DOMAINS` | e.g. `uni.example` lets every verified `@uni.example` Google account in |
| `SCHOLAR_ALLOWED_EMAILS` | Individual addresses; `*` lets anyone in |
| `SCHOLAR_ALLOWED_GITHUB_USERS` | GitHub usernames |
| `OPENALEX_MAILTO` | Contact email sent to Crossref and Europe PMC |
| `SCHOLAR_HOST`, `SCHOLAR_PORT` | Listen address (Docker image: `0.0.0.0:8323`) |

The server refuses to start when hosted mode is half configured, including
an empty allowlist. Do not put your own source API keys in `.env.hosted`.

## 3. Run it (Synology NAS or any Docker host)

1. Cloudflare dashboard > Zero Trust > Networks > Tunnels: create a tunnel,
   add a public hostname (e.g. `scholar.example.org`) pointing to
   `http://scholar:8323`, and copy the tunnel token into `.env.tunnel` as
   `TUNNEL_TOKEN=...`.
2. `docker compose up -d --build` (on Synology: Container Manager > Project >
   Create, pointing at this folder).
3. Open `https://<your-host>/`, sign in, and check that `/keys` loads.

`compose.yaml` runs the server as an unprivileged user with a read-only file
system, no Linux capabilities, and a single writable named volume
(`scholar-data`) for the key database and OAuth state. Only the tunnel
container talks to the internet. Never publish the NAS admin ports
(5000/5001) through the tunnel.

## 4. Day to day

- **Add or remove someone:** edit the allowlist in `.env.hosted`, then
  `docker compose up -d`. Removed accounts lose access to the site and the
  MCP endpoint at once.
- **Update:** `git pull && docker compose up -d --build`.
- **Back up:** the `scholar-data` volume (`keys.db` is useless without
  `SCHOLAR_SECRET_KEY`, so back that up separately).
- **Students:** send them [the student guide](guide-etudiants.md).
