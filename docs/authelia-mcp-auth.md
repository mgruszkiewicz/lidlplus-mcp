# Authenticating the Lidl MCP server with Authelia

Claude's custom-connector flow requires **OAuth 2.0 with Dynamic Client
Registration (RFC 7591)**. Authelia's built-in OIDC provider only supports
statically-configured clients, so we put FastMCP's `OIDCProxy` in between:
Claude registers dynamically against the MCP server, and the MCP server
federates the actual login upstream to Authelia using one static client.

```
Claude mobile app ──DCR──▶ lidl-mcp (OIDCProxy) ──static client──▶ Authelia
                                              ◀──ID token + claims──
```

## Prerequisites

- Authelia ≥ 4.38 (OIDC provider GA).
- A public HTTPS hostname for the MCP server — e.g.
  `https://lidl-mcp.example.com`. TLS must be terminated by your reverse
  proxy / tunnel; FastMCP itself speaks plain HTTP on `:8765`.
- Authelia already reachable on its own HTTPS hostname — e.g.
  `https://auth.example.com`.

## 1. Register a static client in Authelia

Add to `configuration.yml` under `identity_providers.oidc.clients`:

```yaml
identity_providers:
  oidc:
    clients:
      - client_id: lidl-mcp
        client_name: Lidl MCP Bridge
        client_secret: '$pbkdf2-sha512$310000$...'   # generate with: authelia crypto hash generate pbkdf2 --variant sha512 --password 'YOUR_LONG_RANDOM_SECRET'
        public: false
        authorization_policy: two_factor              # or one_factor — your call
        redirect_uris:
          - https://lidl-mcp.example.com/auth/callback
        scopes:
          - openid
          - email
          - profile
          - offline_access
        response_types:
          - code
        grant_types:
          - authorization_code
          - refresh_token
        require_pkce: true
        pkce_challenge_method: S256
        token_endpoint_auth_method: client_secret_basic
        consent_mode: implicit
```

Notes:
- The **plaintext** secret is what the MCP server uses
  (`LIDL_OIDC_CLIENT_SECRET`); Authelia stores only the PBKDF2 hash.
- Restrict who can reach this client by adding an
  `access_control` rule that requires the right group on
  `lidl-mcp.example.com`.

Reload Authelia (`docker compose restart authelia` or `systemctl reload`).

Sanity check the discovery endpoint is live:

```bash
curl -s https://auth.example.com/.well-known/openid-configuration | jq .issuer
# → "https://auth.example.com"
```

## 2. Configure the MCP server

Set these env vars (in `.env`, `docker run -e ...`, or your compose file):

| Variable | Example | Notes |
|---|---|---|
| `LIDL_OIDC_ISSUER` | `https://auth.example.com` | Triggers auth — leave unset to run without auth (local dev only). |
| `LIDL_OIDC_CLIENT_ID` | `lidl-mcp` | Matches `client_id` in Authelia. |
| `LIDL_OIDC_CLIENT_SECRET` | `YOUR_LONG_RANDOM_SECRET` | The plaintext secret you hashed for Authelia. |
| `LIDL_MCP_PUBLIC_URL` | `https://lidl-mcp.example.com` | The externally-visible base URL — used as `base_url` and to build the callback. |
| `LIDL_OIDC_REQUIRED_SCOPES` | `openid email profile` | Space-separated. Defaults to those three. |

Restart the MCP server. On boot you should see FastMCP log that it discovered
the OIDC config; hitting
`https://lidl-mcp.example.com/.well-known/oauth-protected-resource` should
return a JSON pointer to the authorization server metadata.

## 3. Reverse proxy / tunnel

Whatever you use to expose Authelia works for the MCP server too. Cloudflare
Tunnel example:

```yaml
# ~/.cloudflared/config.yml
ingress:
  - hostname: lidl-mcp.example.com
    service: http://localhost:8765
  - service: http_status:404
```

The MCP server must be reachable at `LIDL_MCP_PUBLIC_URL` from the public
internet — Claude's servers initiate the OAuth dance, not your phone.

## 4. Connect from Claude

In the Claude mobile app (or claude.ai) → **Settings → Connectors → Add
custom connector**:

- **Name:** Lidl Bridge
- **URL:** `https://lidl-mcp.example.com/mcp`

Claude will follow the `WWW-Authenticate` challenge from the MCP server,
discover the OAuth metadata, register dynamically, and pop an Authelia login
window. After 2FA you should see `list_receipts` / `get_receipt` /
`list_coupons` available.

## Locking down to specific users

The `access_control` rule on `lidl-mcp.example.com` is the cleanest gate
(Authelia enforces it before the OIDC flow even completes). Example:

```yaml
access_control:
  default_policy: deny
  rules:
    - domain: lidl-mcp.example.com
      policy: two_factor
      subject:
        - 'user:mateusz'        # or 'group:lidl-users'
```

If you also want the MCP server to refuse tokens that don't have a specific
claim, that's a follow-up — `OIDCProxy` exposes a `token_verifier` hook for
custom checks.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Claude shows "couldn't connect" with no login prompt | `LIDL_MCP_PUBLIC_URL` not reachable from the internet, or doesn't match the hostname in Authelia's `redirect_uris`. |
| Authelia returns `invalid_client` | Wrong `client_id` / plaintext secret mismatch with the hash in Authelia. |
| Authelia returns `invalid_redirect_uri` | `redirect_uris` in Authelia must be exactly `<LIDL_MCP_PUBLIC_URL>/auth/callback`. |
| Login succeeds but Claude says "no tools" | Tools require an authenticated session, but the access token didn't include `openid` scope — check `LIDL_OIDC_REQUIRED_SCOPES`. |
| Endless redirect loop | Clock skew between Authelia and the MCP container — make sure both have NTP. |
