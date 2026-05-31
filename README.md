# lidlplus-mcp

Minimal scaffold around [`zsobix/lidlplus-api`](https://github.com/zsobix/lidlplus-api)
that polls Lidl Plus for new receipts (and coupons) and drops each one as a
JSON files.

> Unofficial. Reverse-engineered. Can break whenever Lidl ships a redesign — the
> upstream library was already out of date when this repo was set up; see
> [Login flow](#login-flow-may-2026) below.

## Layout

- `lidlbridge/auth.py` — one-time interactive Playwright login → saves refresh token.
- `lidlbridge/poll.py` — cron entry point. Lists receipts, diffs against `state.json`, writes new ones to `data/receipts/`.
- `lidlbridge/coupons.py` — on-demand fetch of currently available coupons for a store.
- `lidlbridge/state.py` — tiny seen-IDs store.
- `lidlbridge/config.py` — `.env` loader.

## Setup

I recommend using astral `uv` package manager for the ease of install
```bash
uv sync
playwright install chromium

cp .env.example .env
# edit .env: confirm LIDL_COUNTRY / LIDL_LANGUAGE (credentials are typed by
#            hand in the browser, not stored here)
# optional: LIDL_STORE_ID=PL1776   # pin a specific store for `lidl-coupons`
```

## First-time login

```bash
uv run lidl-auth
```

A real Chrome window opens (channel="chrome", persistent profile under
`data/chrome-profile/` so cookies and device-trust survive between runs).
**You sign in by hand** — type your email and password and clear any
"Przekroczyliśmy nasze możliwości" / captcha / 2FA challenge directly in the
visible browser. We never touch the credential fields (scripted entry trips
Lidl's bot protection), so nothing needs to be stored in `.env`.

Once you finish logging in, Lidl redirects through its OAuth callback;
`auth.py` is watching for that redirect, grabs the authorization code, and
exchanges it for a refresh token — which lands in `data/refresh_token`
(chmod 600). It waits up to 10 minutes for you to complete the login.

If Chrome isn't installed, edit `lidlbridge/auth.py` and drop the
`channel="chrome"` arg to fall back to bundled Chromium (more likely to trip
bot detection).

### How the token is captured

We don't automate the form, so there are no login selectors to maintain. The
only integration point is the OAuth redirect: `_parse_code` waits for the
`https://accounts.lidl.com/connect/authorize/...` callback and reads the
`code=` parameter from its `location` header, then `_authorization_code`
swaps that code (plus the PKCE verifier) for the refresh token. As long as
Lidl keeps that OAuth callback shape, redesigns of the login form don't affect
us.

## Polling receipts

```bash
uv run lidl-poll
```

Writes one `data/receipts/<ticket_id>.json` per new receipt and updates
`data/state.json`. Exit code 0 on success, non-zero if the refresh token
is missing or rejected — that's your signal to re-run `lidl-auth`.

Notes on the underlying endpoint:
- `lidl.receipts(...)` hits `tickets.lidlplus.com/api/v2/<country>/tickets`
  and returns `{"tickets": [...], "totalCount": N, "size": M}`, **not** a bare
  list — `poll.py` unwraps `tickets`.
- The upstream method uses American spelling (`only_favorite=False`); the
  British spelling will `TypeError`.

## Fetching coupons

```bash
uv run lidl-coupons
```

Writes `data/coupons/coupons-<store_id>-<UTC-timestamp>.json`. The payload is
shaped `{"sections": [{"name": ..., "promotions": [...]}, ...]}`.

Store resolution order:
1. `LIDL_STORE_ID` from `.env` (e.g. `PL1776`).
2. The `store.id` on your most recent receipt — the store you actually shop at.
3. The first store returned by `get_stores()` (rarely what you want).

To pin your home store, grab the `store.id` from any `data/receipts/*.json` and
set it as `LIDL_STORE_ID`.

## Cron example

```cron
*/30 * * * * cd /home/user/lidlplus-mcp && uv run lidl-poll >> data/poll.log 2>&1
```

`lidl-coupons` is one-shot — run it on demand or schedule it daily; coupons
don't change minute-to-minute.

## MCP server

```bash
uv run lidl-mcp   # serves http://127.0.0.1:8765/mcp
```

Override host/port with `LIDL_MCP_HOST` / `LIDL_MCP_PORT`. Reads from
`data/receipts/` and `data/coupons/` only — does not call the Lidl API.

Tools exposed:
- `list_receipts(start_date?, end_date?, limit=50)` — minimal fields per
  receipt (id, date, total, store, item_count), newest first.
- `get_receipt(receipt_id)` — full detail incl. parsed line items + coupons used.
- `list_coupons(active_only=True)` — flattened view of the latest coupons
  snapshot (title, brand, discount, validity window).

Point Claude at it with:
```bash
claude mcp add --transport http lidlbridge http://127.0.0.1:8765/mcp
```

### Exposing it remotely (Authelia OIDC)

For the Claude mobile/web apps the server has to be reachable over public
HTTPS *and* gated behind OAuth with Dynamic Client Registration. We use
FastMCP's `OIDCProxy` to fake DCR in front of a static Authelia client.

See `docs/authelia-mcp-auth.md` for the full walkthrough. Quick env summary:

```
LIDL_OIDC_ISSUER=https://auth.example.com
LIDL_OIDC_CLIENT_ID=lidl-mcp
LIDL_OIDC_CLIENT_SECRET=...        # plaintext; the PBKDF2 hash goes into Authelia
LIDL_MCP_PUBLIC_URL=https://lidl-mcp.example.com
```

### Docker Compose (with Traefik)

`compose.yml` ships labels for a pre-existing Traefik instance on an
external `traefik` network with a `websecure` (HTTPS) entrypoint and a
`letsencrypt` cert resolver. Set the hostname and run:

```bash
echo 'LIDL_MCP_HOSTNAME=lidl-mcp.example.com' >> .env
docker compose up -d --build
```

Traefik will terminate TLS and route to the container's `:8765`. If your
Traefik network or entrypoint names differ, edit the labels in
`compose.yml`.

### Docker (plain)

```bash
docker build -t lidl-mcp .
docker run --rm -p 8765:8765 \
  -v "$PWD/data:/app/data" \
  --env-file .env \
  lidl-mcp
```

The image runs `lidl-mcp` by default and starts an in-process scheduler
that calls `lidl-poll` + `lidl-coupons` every 4 h between 09:00 and 21:00
in `Europe/Warsaw` (tune via `LIDL_SCHEDULE_*` env vars; set
`LIDL_SCHEDULE=0` to disable). The `refresh_token` file produced by
`lidl-auth` on the host must live in the mounted `data/` volume — auth is
still a one-time interactive step that you run on your laptop, not in the
container.

## Next steps (not built yet)

- Agent ingestion: point your agent watcher at `data/receipts/`.
- Token-refresh monitoring: alert when `lidl-poll` exits non-zero so you know to re-auth.
