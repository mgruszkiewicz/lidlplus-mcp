# lidlplus-mcp

Minimal scaffold around [`zsobix/lidlplus-api`](https://github.com/zsobix/lidlplus-api)
that polls Lidl Plus for new receipts (and coupons) and drops each one as a
JSON files.  
![screenshot showcasing the usage of mcp in Claude](https://i.issei.space/a0z8Kj3G.jpg)

Althrought it has a `mcp` in it's name, it doesn't needs to be used as MCP for a LLM - you can also use it standalone with manual polling.

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
uv run playwright install chromium

cp .env.example .env
# edit .env: confirm LIDL_COUNTRY / LIDL_LANGUAGE
# optional: LIDL_STORE_ID=PL1776   # pin a specific store for `lidl-coupons`
```
After initial authentication, you can also start the mcp server/script in the docker container instead.

### 1. First-time login

```bash
uv run lidl-auth
```

A real Chrome window opens (channel="chrome", persistent profile under
`data/chrome-profile/` so cookies and device-trust survive between runs).
**You sign in by hand** — type your email and password and clear any
"Przekroczyliśmy nasze możliwości" / captcha / 2FA challenge directly in the
visible browser.

**If this step is not working due to the bot protection, you will need to get the auth token manually.**
1. Run the `uv run lidl-auth --manual`
2. Copy the provided URL and open it in Chrome (for some reason Firefox doesn't allow you to navigate to website which have a invalid handler)
3. Open Network Console in Chrome
4. Login to your Lidl Plus account as normal
5. After clicking on "Next" on password prompt, the button should change to disabled and in network console you should see a blocked request (due to missing handler) starting with `callback?code=xyz`. Click on it with a right mouse button and copy the URL.
![screenshot showing up the open chrome network console with preview of callback url](https://i.issei.space/l4TbMREu.jpg)  
6. Paste the URL into the prompt of the script and press enter, this should authenticate you.


Once you finish logging in, Lidl redirects through its OAuth callback;
`auth.py` is watching for that redirect, grabs the authorization code, and
exchanges it for a refresh token — which lands in `data/refresh_token`
(chmod 600). It waits up to 10 minutes for you to complete the login.

### 2. Polling receipts

To poll existing reciepts from your account, you can run

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

### 3. Fetching coupons
If you want to get a available coupons for your region/shop, you can the `lidl-coupons` command

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

## Example of polling reciepts in cron

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


## Notes
* This project is unofficial and is not associated with Lidl. It is using a package which reverse engineered the API, so it can break anytime or might get your account banned (but i didn't hear about such a cases for personal usage)
