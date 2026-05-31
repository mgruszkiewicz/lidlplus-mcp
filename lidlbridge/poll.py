"""Cron entry point. Lists receipts via the stored refresh token, diffs
against state, and writes any new ones as JSON files for downstream
agents to pick up."""

from __future__ import annotations

import json
import sys

from lidlplus_api import LidlPlusApi

from .config import Config, load
from .state import load_seen, save_seen


def _ticket_id(entry: dict) -> str | None:
    for key in ("id", "ticketId", "receiptId"):
        if entry.get(key):
            return str(entry[key])
    return None


def _iter_recent_ids(lidl: LidlPlusApi, max_pages: int = 4) -> list[str]:
    """Walk the paginated receipts list newest-first.

    Stops once a page returns nothing or max_pages is reached. The first
    page is usually enough; extra pages exist so the very first poll
    after auth can backfill a little."""
    ids: list[str] = []
    for page in range(1, max_pages + 1):
        resp = lidl.receipts(only_favorite=False, pageNumber=page) or {}
        batch = resp.get("tickets", []) if isinstance(resp, dict) else resp
        if not batch:
            break
        for entry in batch:
            ticket_id = _ticket_id(entry)
            if ticket_id:
                ids.append(ticket_id)
        size = resp.get("size") if isinstance(resp, dict) else None
        if size and len(batch) < size:
            break
    return ids


def poll(cfg: Config) -> int:
    if not cfg.token_file.exists():
        print(
            f"No refresh token at {cfg.token_file}. Run `lidl-auth` first.",
            file=sys.stderr,
        )
        return 1

    refresh_token = cfg.token_file.read_text().strip()
    lidl = LidlPlusApi(
        language=cfg.language,
        country=cfg.country,
        refresh_token=refresh_token,
    )

    seen = load_seen(cfg.state_file)
    recent_ids = _iter_recent_ids(lidl)
    new_ids = [tid for tid in recent_ids if tid not in seen]

    if not new_ids:
        print("No new receipts.")
        return 0

    cfg.receipts_dir.mkdir(parents=True, exist_ok=True)
    print(f"Found {len(new_ids)} new receipt(s).")
    for ticket_id in new_ids:
        detail = lidl.receipt(ticket_id)
        out_path = cfg.receipts_dir / f"{ticket_id}.json"
        out_path.write_text(json.dumps(detail, indent=2, ensure_ascii=False))
        print(f"  wrote {out_path}")
        seen.add(ticket_id)

    save_seen(cfg.state_file, seen)

    # If lidlplus-api rotated the refresh token during the call, persist it.
    current_token = getattr(lidl, "refresh_token", None)
    if current_token and current_token != refresh_token:
        cfg.token_file.write_text(current_token)
        cfg.token_file.chmod(0o600)

    return 0


def main() -> int:
    return poll(load())


if __name__ == "__main__":
    raise SystemExit(main())
