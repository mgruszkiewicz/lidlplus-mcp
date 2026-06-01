"""Shared fixtures for the MCP server tests.

Every test runs against a throwaway data directory: we point the
`LIDL_*` env vars at a temp tree before the tools call `config.load()`,
so nothing touches the real `./data`. The tools read config fresh on
each call (`_cfg()` -> `load()`), so setting the env per-test is enough.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated data tree with env vars pointed at it.

    Layout mirrors production: ``<root>/receipts`` and ``<root>/coupons``
    (the coupons dir is derived as ``receipts_dir.parent / "coupons"``).
    """
    receipts = tmp_path / "receipts"
    coupons = tmp_path / "coupons"
    receipts.mkdir()
    coupons.mkdir()

    monkeypatch.setenv("LIDL_RECEIPTS_DIR", str(receipts))
    monkeypatch.setenv("LIDL_STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setenv("LIDL_TOKEN_FILE", str(tmp_path / "refresh_token"))
    return tmp_path


def _article_html(rows: list[dict[str, Any]]) -> str:
    """Build receipt HTML the way Lidl does: two `article` spans per item.

    The first span carries the description text, the second carries the
    `"<qty> * <unit> <total>"` body line. Both share `data-art-id`.
    """
    spans: list[str] = []
    for i, r in enumerate(rows):
        attrs = (
            f'class="article" data-art-id="{r["id"]}" '
            f'data-art-description="{r["description"]}" '
            f'data-unit-price="{r["unit"]}"'
        )
        # Description row (no qty body -> parser skips it).
        spans.append(f'<span id="line_{i}a" {attrs}>{r["description"]}</span>')
        # Totals row (qty * unit total) -> parser emits one item.
        body = f'{r["qty"]} * {r["unit_dot"]} {r["total"]} C'
        spans.append(f'<span id="line_{i}b" {attrs}>{body}</span>')
    return "<div>" + "".join(spans) + "</div>"


def write_receipt(
    receipts_dir: Path,
    receipt_id: str,
    *,
    date: str,
    total: str = "14.97",
    store_id: str = "PL1776",
    store_name: str = "Kraków, ul. Mogilska 116",
    items: list[dict[str, Any]] | None = None,
    coupons_used: list[dict[str, Any]] | None = None,
) -> Path:
    """Write a receipt JSON shaped like a real Lidl Plus ticket."""
    payload: dict[str, Any] = {
        "id": receipt_id,
        "date": date,
        "totalAmount": total,
        "store": {"id": store_id, "name": store_name},
        "couponsUsed": coupons_used or [],
        "htmlPrintedReceipt": _article_html(items) if items else None,
    }
    path = receipts_dir / f"{receipt_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False))
    return path


def write_coupons(
    coupons_dir: Path,
    *,
    store_id: str = "PL1944",
    stamp: str = "20260525T152246Z",
    sections: list[dict[str, Any]],
) -> Path:
    """Write a coupons snapshot file named `coupons-<store>-<stamp>.json`."""
    path = coupons_dir / f"coupons-{store_id}-{stamp}.json"
    path.write_text(json.dumps({"sections": sections}, ensure_ascii=False))
    return path
