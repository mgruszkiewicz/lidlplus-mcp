"""Tests for the read tools: list_receipts, get_receipt, list_coupons.

These exercise the full path from on-disk JSON to the dict an agent
receives, using the isolated `data_dir` fixture.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from lidlbridge import mcp_server as m
from tests.conftest import write_coupons, write_receipt


@pytest.fixture
def receipts_dir(data_dir: Path) -> Path:
    return data_dir / "receipts"


@pytest.fixture
def coupons_dir(data_dir: Path) -> Path:
    return data_dir / "coupons"


# --- list_receipts --------------------------------------------------------

def test_list_receipts_empty(receipts_dir: Path):
    result = m.list_receipts()
    assert result == {"count": 0, "receipts": []}


def test_list_receipts_returns_summaries_newest_first(receipts_dir: Path):
    write_receipt(receipts_dir, "old", date="2026-01-01T10:00:00")
    write_receipt(receipts_dir, "new", date="2026-03-01T10:00:00")
    write_receipt(receipts_dir, "mid", date="2026-02-01T10:00:00")

    result = m.list_receipts()
    assert result["count"] == 3
    assert [r["id"] for r in result["receipts"]] == ["new", "mid", "old"]


def test_list_receipts_limit(receipts_dir: Path):
    for i in range(5):
        write_receipt(receipts_dir, f"r{i}", date=f"2026-01-0{i + 1}T10:00:00")
    result = m.list_receipts(limit=2)
    assert result["count"] == 2


def test_list_receipts_date_window_inclusive(receipts_dir: Path):
    write_receipt(receipts_dir, "before", date="2026-01-31T23:00:00")
    write_receipt(receipts_dir, "inside", date="2026-02-15T12:00:00")
    write_receipt(receipts_dir, "after", date="2026-03-01T00:00:01")

    result = m.list_receipts(start_date="2026-02-01", end_date="2026-02-28")
    assert [r["id"] for r in result["receipts"]] == ["inside"]


def test_list_receipts_single_day_window_covers_whole_day(receipts_dir: Path):
    # start==end==a bare date must still match a mid-day receipt that day.
    write_receipt(receipts_dir, "today", date="2026-02-10T18:45:00")
    result = m.list_receipts(start_date="2026-02-10", end_date="2026-02-10")
    assert [r["id"] for r in result["receipts"]] == ["today"]


def test_list_receipts_skips_corrupt_json(receipts_dir: Path):
    write_receipt(receipts_dir, "good", date="2026-02-10T10:00:00")
    (receipts_dir / "broken.json").write_text("{ not valid json")
    result = m.list_receipts()
    assert result["count"] == 1
    assert result["receipts"][0]["id"] == "good"


# --- get_receipt ----------------------------------------------------------

def test_get_receipt_returns_items_and_coupons(receipts_dir: Path):
    write_receipt(
        receipts_dir,
        "abc",
        date="2026-02-10T10:00:00",
        items=[
            {"id": "1", "description": "Masło", "qty": "3",
             "unit": "4,99", "unit_dot": "4.99", "total": "14.97"},
        ],
        coupons_used=[{"title": "Masło 82%", "couponTitle": "0,99 zł"}],
    )
    result = m.get_receipt("abc")
    assert result["id"] == "abc"
    assert result["item_count"] == 1
    assert result["items"][0]["description"] == "Masło"
    assert result["items"][0]["quantity"] == "3"
    assert result["coupons_used"] == [{"title": "Masło 82%", "discount": "0,99 zł"}]


def test_get_receipt_missing_raises(receipts_dir: Path):
    with pytest.raises(ValueError, match="not found"):
        m.get_receipt("does-not-exist")


# --- list_coupons ---------------------------------------------------------

def _iso(dt: datetime) -> str:
    return dt.isoformat()


def test_list_coupons_no_file_returns_empty(coupons_dir: Path):
    result = m.list_coupons()
    assert result == {"fetched_at": None, "store_id": None, "count": 0, "coupons": []}


def test_list_coupons_parses_store_and_timestamp_from_filename(coupons_dir: Path):
    write_coupons(
        coupons_dir,
        store_id="PL1944",
        stamp="20260525T152246Z",
        sections=[{"name": "Nabiał", "promotions": []}],
    )
    result = m.list_coupons()
    assert result["store_id"] == "PL1944"
    assert result["fetched_at"] == "20260525T152246Z"


def test_list_coupons_flattens_sections(coupons_dir: Path):
    now = datetime.now(timezone.utc)
    section = {
        "name": "Nabiał",
        "promotions": [
            {
                "title": "Masło",
                "brand": "Pilos",
                "discount": {"title": "-30%", "description": "Taniej"},
                "validity": {
                    "start": _iso(now - timedelta(days=1)),
                    "end": _iso(now + timedelta(days=1)),
                },
            }
        ],
    }
    write_coupons(coupons_dir, sections=[section])

    result = m.list_coupons()
    assert result["count"] == 1
    c = result["coupons"][0]
    assert c["title"] == "Masło"
    assert c["brand"] == "Pilos"
    assert c["discount"] == "-30%"
    assert c["description"] == "Taniej"
    assert c["section"] == "Nabiał"


def test_list_coupons_active_only_filters_expired(coupons_dir: Path):
    now = datetime.now(timezone.utc)
    expired = {
        "title": "Expired",
        "validity": {
            "start": _iso(now - timedelta(days=10)),
            "end": _iso(now - timedelta(days=1)),
        },
        "discount": {},
    }
    future = {
        "title": "NotYet",
        "validity": {
            "start": _iso(now + timedelta(days=1)),
            "end": _iso(now + timedelta(days=10)),
        },
        "discount": {},
    }
    active = {
        "title": "Active",
        "validity": {
            "start": _iso(now - timedelta(days=1)),
            "end": _iso(now + timedelta(days=1)),
        },
        "discount": {},
    }
    write_coupons(
        coupons_dir,
        sections=[{"name": "S", "promotions": [expired, future, active]}],
    )

    active_only = m.list_coupons(active_only=True)
    assert {c["title"] for c in active_only["coupons"]} == {"Active"}

    everything = m.list_coupons(active_only=False)
    assert {c["title"] for c in everything["coupons"]} == {"Expired", "NotYet", "Active"}


def test_list_coupons_uses_latest_snapshot(coupons_dir: Path):
    # Two snapshots; the lexicographically-last filename (newest stamp) wins.
    write_coupons(
        coupons_dir, stamp="20260101T000000Z",
        sections=[{"name": "old", "promotions": []}],
    )
    write_coupons(
        coupons_dir, stamp="20260601T000000Z",
        sections=[{"name": "new", "promotions": []}],
    )
    result = m.list_coupons()
    assert result["fetched_at"] == "20260601T000000Z"
