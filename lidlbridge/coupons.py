"""Fetch currently-available coupons for a Lidl Plus store and dump them as JSON."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

from lidlplus_api import LidlPlusApi

from .config import Config, load


def _resolve_store_id(lidl: LidlPlusApi) -> str | None:
    if env := os.environ.get("LIDL_STORE_ID", "").strip():
        return env
    # Most recent receipt — that's the store the user actually shops at.
    try:
        resp = lidl.receipts(only_favorite=False, pageNumber=1) or {}
        tickets = resp.get("tickets", []) if isinstance(resp, dict) else resp
        if tickets:
            detail = lidl.receipt(str(tickets[0].get("id") or tickets[0].get("ticketId")))
            if sid := (detail.get("store") or {}).get("id"):
                return str(sid)
    except Exception:
        pass
    # Fall back to the first store the API lists.
    stores = lidl.get_stores() or []
    items = stores.get("stores") if isinstance(stores, dict) else stores
    if items:
        first = items[0]
        for key in ("storeKey", "id", "storeId", "store_id"):
            if first.get(key):
                return str(first[key])
    return None


def fetch(cfg: Config) -> int:
    if not cfg.token_file.exists():
        print(f"No refresh token at {cfg.token_file}. Run `lidl-auth` first.", file=sys.stderr)
        return 1

    refresh_token = cfg.token_file.read_text().strip()
    lidl = LidlPlusApi(language=cfg.language, country=cfg.country, refresh_token=refresh_token)

    store_id = _resolve_store_id(lidl)
    if not store_id:
        print("Could not determine a store id. Set LIDL_STORE_ID in .env.", file=sys.stderr)
        return 2

    print(f"Fetching coupons for store {store_id}...")
    payload = lidl.coupons(store_id)

    out_dir = cfg.receipts_dir.parent / "coupons"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"coupons-{store_id}-{stamp}.json"
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"Wrote {out_path}")

    current_token = getattr(lidl, "refresh_token", None)
    if current_token and current_token != refresh_token:
        cfg.token_file.write_text(current_token)
        cfg.token_file.chmod(0o600)

    return 0


def main() -> int:
    return fetch(load())


if __name__ == "__main__":
    raise SystemExit(main())
