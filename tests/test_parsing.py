"""Unit tests for the pure JSON/HTML parsing helpers in mcp_server."""

from __future__ import annotations

from datetime import time

import pytest

from lidlbridge import mcp_server as m


# --- _articles_from_html -------------------------------------------------

def test_articles_parsed_from_span_pairs():
    html = (
        '<span class="article" data-art-id="5530547" '
        'data-art-description="Masło z pol.mlecz." data-unit-price="4,99">'
        "Masło z pol.mlecz.</span>"
        '<span class="article" data-art-id="5530547" '
        'data-art-description="Masło z pol.mlecz." data-unit-price="4,99">'
        "3 * 4.99 14.97 C</span>"
    )
    items = m._articles_from_html(html)
    assert len(items) == 1
    item = items[0]
    assert item["id"] == "5530547"
    assert item["description"] == "Masło z pol.mlecz."
    assert item["quantity"] == "3"
    assert item["unit_price"] == "4.99"
    assert item["line_total"] == "14.97"


def test_articles_description_only_row_is_skipped():
    # A single description span with no "qty * unit total" body yields nothing.
    html = (
        '<span class="article" data-art-id="1" '
        'data-art-description="Lonely">Lonely</span>'
    )
    assert m._articles_from_html(html) == []


def test_articles_comma_decimals_normalised_to_dots():
    html = (
        '<span class="article" data-art-id="9" data-art-description="X">'
        "1 * 2,50 2,50 C</span>"
    )
    (item,) = m._articles_from_html(html)
    assert item["unit_price"] == "2.50"
    assert item["line_total"] == "2.50"


@pytest.mark.parametrize("value", [None, "", "<div>no articles here</div>"])
def test_articles_empty_inputs(value):
    assert m._articles_from_html(value) == []


# --- _parse_date ----------------------------------------------------------

def test_parse_date_none_returns_none():
    assert m._parse_date(None) is None


def test_parse_date_bare_date_is_midnight():
    parsed = m._parse_date("2026-02-10")
    assert parsed is not None
    assert parsed.time() == time(0, 0)
    assert parsed.tzinfo is not None  # always tz-aware


def test_parse_date_end_of_day_widening():
    parsed = m._parse_date("2026-02-10", end_of_day=True)
    assert parsed is not None
    assert parsed.hour == 23 and parsed.minute == 59 and parsed.second == 59


def test_parse_date_end_of_day_keeps_explicit_time():
    # A non-midnight timestamp must not be widened.
    parsed = m._parse_date("2026-02-10T08:30:00", end_of_day=True)
    assert parsed is not None
    assert parsed.hour == 8 and parsed.minute == 30


def test_parse_date_bad_value_raises():
    with pytest.raises(ValueError):
        m._parse_date("not-a-date")


# --- _parse_receipt_date --------------------------------------------------

def test_parse_receipt_date_handles_z_suffix():
    parsed = m._parse_receipt_date("2026-02-10T12:00:00Z")
    assert parsed is not None
    assert parsed.utcoffset() is not None  # Z resolved to +00:00


@pytest.mark.parametrize("value", [None, "", "garbage"])
def test_parse_receipt_date_bad_inputs_return_none(value):
    assert m._parse_receipt_date(value) is None


# --- _receipt_summary -----------------------------------------------------

def test_receipt_summary_maps_fields_and_counts_items():
    data = {
        "id": "abc",
        "date": "2026-02-10T08:30:00",
        "totalAmount": "23.96",
        "store": {"id": "PL1776", "name": "Kraków"},
        "htmlPrintedReceipt": (
            '<span class="article" data-art-id="1" data-art-description="A">'
            "1 * 1.00 1.00 C</span>"
        ),
    }
    s = m._receipt_summary(data)
    assert s == {
        "id": "abc",
        "date": "2026-02-10T08:30:00",
        "total": "23.96",
        "currency": "PLN",
        "store": "Kraków",
        "store_id": "PL1776",
        "item_count": 1,
    }


def test_receipt_summary_tolerates_missing_store_and_html():
    s = m._receipt_summary({"id": "x", "date": "2026-01-01"})
    assert s["store"] is None
    assert s["store_id"] is None
    assert s["item_count"] == 0
