"""FastMCP server exposing local Lidl Plus receipts and coupons to AI agents."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, time
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from .config import load
from .scheduler import maybe_start_scheduler

log = logging.getLogger("lidlbridge.mcp")

_ARTICLE_SPAN_RE = re.compile(
    r'<span([^>]*?\bclass="article"[^>]*?)>([^<]*)</span>'
)
_ATTR_RE = re.compile(r'data-([a-z\-]+)="([^"]*)"')
# Second line of each article pair has body like:  "    5 * 0.79 3.95 C"
_QTY_LINE_RE = re.compile(
    r'(?P<qty>\d+(?:[.,]\d+)?)\s*\*\s*(?P<unit>\d+(?:[.,]\d+)?)\s+(?P<total>\d+(?:[.,]\d+)?)'
)


def _cfg():
    return load()


def _coupons_dir() -> Path:
    return _cfg().receipts_dir.parent / "coupons"


def _parse_date(value: str | None, *, end_of_day: bool = False) -> datetime | None:
    """Parse an inclusive bound.

    A bare `YYYY-MM-DD` is widened so a single-day window
    (start=end=today) actually covers that whole day: start→00:00,
    end→23:59:59.999999. Result is always tz-aware (local zone) so it
    can be compared with the tz-aware timestamps stored on receipts.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Bad date {value!r}; use YYYY-MM-DD or ISO 8601") from exc
    # Widen to end-of-day when the caller gave us a date (or a midnight
    # timestamp, which agents often produce when "today" gets converted
    # to ISO). Without this, end_date="today" silently excludes every
    # receipt with a non-zero time component.
    if end_of_day and parsed.time() == time(0, 0):
        parsed = datetime.combine(parsed.date(), time.max, tzinfo=parsed.tzinfo)
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def _parse_receipt_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _load_receipt(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _articles_from_html(html: str | None) -> list[dict[str, Any]]:
    """Parse line items from the embedded receipt HTML.

    Each article occupies two consecutive `<span class="article">` rows that
    share `data-art-id`: the first holds the description, the second holds
    body text like `"5 * 0.79 3.95"`. Single-unit items omit
    `data-art-quantity`, so we read qty from that body line when present.
    """
    if not html:
        return []
    items: list[dict[str, Any]] = []
    for attr_blob, body in _ARTICLE_SPAN_RE.findall(html):
        attrs = dict(_ATTR_RE.findall(attr_blob))
        qty_match = _QTY_LINE_RE.search(body)
        if not qty_match:
            # First row of the pair (description-only). Skip; the totals row
            # carries qty + line_total.
            continue
        items.append(
            {
                "id": attrs.get("art-id", ""),
                "description": (attrs.get("art-description") or "").strip(),
                "quantity": qty_match["qty"].replace(",", "."),
                "unit_price": qty_match["unit"].replace(",", "."),
                "line_total": qty_match["total"].replace(",", "."),
            }
        )
    return items


def _receipt_summary(data: dict[str, Any]) -> dict[str, Any]:
    store = data.get("store") or {}
    items = _articles_from_html(data.get("htmlPrintedReceipt"))
    return {
        "id": data.get("id"),
        "date": data.get("date"),
        "total": data.get("totalAmount"),
        "currency": "PLN",
        "store": store.get("name"),
        "store_id": store.get("id"),
        "item_count": len(items),
    }


def _latest_coupons_file() -> Path | None:
    d = _coupons_dir()
    if not d.exists():
        return None
    files = sorted(d.glob("coupons-*.json"))
    return files[-1] if files else None


def _build_auth():
    """Optional OIDC/OAuth proxy in front of the MCP server.

    Activated when LIDL_OIDC_ISSUER + client credentials are set. Designed
    for Authelia (or any OIDC-compliant IdP) — FastMCP's OIDCProxy fronts
    a single statically-registered client and fakes RFC 7591 Dynamic
    Client Registration for Claude.
    """
    issuer = os.environ.get("LIDL_OIDC_ISSUER", "").strip()
    if not issuer:
        return None

    from fastmcp.server.auth.oidc_proxy import OIDCProxy

    base_url = os.environ["LIDL_MCP_PUBLIC_URL"].rstrip("/")
    client_id = os.environ["LIDL_OIDC_CLIENT_ID"]
    client_secret = os.environ["LIDL_OIDC_CLIENT_SECRET"]
    scopes = os.environ.get("LIDL_OIDC_REQUIRED_SCOPES", "openid email profile")

    config_url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    return OIDCProxy(
        config_url=config_url,
        client_id=client_id,
        client_secret=client_secret,
        base_url=base_url,
        required_scopes=[s for s in scopes.split() if s],
        verify_id_token=True,
    )


mcp = FastMCP("lidlbridge", auth=_build_auth())


@mcp.tool
def list_receipts(
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """List receipts with minimal fields.

    Args:
        start_date: Inclusive lower bound (YYYY-MM-DD or ISO 8601).
            A bare date is treated as 00:00 local time.
        end_date: Inclusive upper bound (YYYY-MM-DD or ISO 8601).
            A bare date covers the whole day (until 23:59:59 local), so
            passing start_date=end_date=today returns today's receipts.
        limit: Max receipts to return, newest first. Defaults to 50.

    Returns a dict with `count` and `receipts` — each entry is
    {id, date, total, currency, store, store_id, item_count}.
    Use `get_receipt(id)` to fetch the full item list for one receipt.
    """
    start = _parse_date(start_date)
    end = _parse_date(end_date, end_of_day=True)
    receipts_dir = _cfg().receipts_dir
    log.info(
        "list_receipts args start_date=%r end_date=%r limit=%d -> start=%s end=%s dir=%s",
        start_date, end_date, limit, start, end, receipts_dir,
    )
    out: list[dict[str, Any]] = []
    seen = 0
    dropped_no_data = 0
    dropped_before = 0
    dropped_after = 0
    dropped_no_date = 0
    for path in receipts_dir.glob("*.json"):
        seen += 1
        data = _load_receipt(path)
        if not data:
            dropped_no_data += 1
            continue
        raw_date = data.get("date")
        when = _parse_receipt_date(raw_date)
        if when is not None and when.tzinfo is None:
            when = when.astimezone()
        if (start or end) and when is None:
            dropped_no_date += 1
            log.debug("drop %s: unparseable date %r", path.name, raw_date)
            continue
        if start and when < start:
            dropped_before += 1
            log.debug("drop %s: when=%s < start=%s", path.name, when, start)
            continue
        if end and when > end:
            dropped_after += 1
            log.debug("drop %s: when=%s > end=%s", path.name, when, end)
            continue
        out.append(_receipt_summary(data))
    log.info(
        "list_receipts result seen=%d kept=%d dropped(before=%d after=%d no_date=%d no_data=%d)",
        seen, len(out), dropped_before, dropped_after, dropped_no_date, dropped_no_data,
    )
    out.sort(key=lambda r: r.get("date") or "", reverse=True)
    out = out[: max(0, limit)]
    return {"count": len(out), "receipts": out}


@mcp.tool
def get_receipt(receipt_id: str) -> dict[str, Any]:
    """Return one receipt with its parsed line items.

    Args:
        receipt_id: The `id` field from `list_receipts`.
    """
    path = _cfg().receipts_dir / f"{receipt_id}.json"
    data = _load_receipt(path)
    if not data:
        raise ValueError(f"Receipt {receipt_id} not found")
    summary = _receipt_summary(data)
    summary["items"] = _articles_from_html(data.get("htmlPrintedReceipt"))
    summary["coupons_used"] = [
        {"title": c.get("title"), "discount": c.get("couponTitle")}
        for c in data.get("couponsUsed") or []
    ]
    return summary


@mcp.tool
def list_coupons(active_only: bool = True) -> dict[str, Any]:
    """List currently available coupons from the latest snapshot.

    Args:
        active_only: When true (default), keep only coupons whose validity
            window covers the current time.

    Returns {fetched_at, store_id, count, coupons:[{title, brand, discount,
    description, valid_from, valid_to, section}]}.
    """
    path = _latest_coupons_file()
    if not path:
        return {"fetched_at": None, "store_id": None, "count": 0, "coupons": []}

    payload = _load_receipt(path) or {}
    # Derive store id and timestamp from filename: coupons-<store>-<stamp>.json
    parts = path.stem.split("-", 2)
    store_id = parts[1] if len(parts) > 1 else None
    fetched_at = parts[2] if len(parts) > 2 else None

    now = datetime.now().astimezone()
    out: list[dict[str, Any]] = []
    for section in payload.get("sections", []) or []:
        section_name = section.get("name")
        for promo in section.get("promotions", []) or []:
            validity = promo.get("validity") or {}
            start = _parse_receipt_date(validity.get("start"))
            end = _parse_receipt_date(validity.get("end"))
            if active_only:
                if start and start > now:
                    continue
                if end and end < now:
                    continue
            discount = promo.get("discount") or {}
            out.append(
                {
                    "title": promo.get("title"),
                    "brand": promo.get("brand"),
                    "discount": discount.get("title"),
                    "description": discount.get("description"),
                    "valid_from": validity.get("start"),
                    "valid_to": validity.get("end"),
                    "section": section_name,
                }
            )
    return {
        "fetched_at": fetched_at,
        "store_id": store_id,
        "count": len(out),
        "coupons": out,
    }


def main() -> int:
    level = os.environ.get("LIDL_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    host = os.environ.get("LIDL_MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("LIDL_MCP_PORT", "8765"))
    maybe_start_scheduler()
    mcp.run(transport="http", host=host, port=port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
