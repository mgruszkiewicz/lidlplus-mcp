"""Tests for the refresh_data tool.

`poll()` and `coupons.fetch()` hit the real Lidl Plus API, so we
monkeypatch them. Each fake optionally writes a data file (mimicking a
successful download) and returns an exit code, letting us assert that
refresh_data wires downloads to the freshly-served data and reports
failures without crashing.
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


def _active_coupon_section():
    now = datetime.now(timezone.utc)
    return [{
        "name": "S",
        "promotions": [{
            "title": "Fresh",
            "discount": {"title": "-20%"},
            "validity": {
                "start": (now - timedelta(days=1)).isoformat(),
                "end": (now + timedelta(days=1)).isoformat(),
            },
        }],
    }]


def test_refresh_all_downloads_then_returns_fresh_data(
    receipts_dir: Path, coupons_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    def fake_poll(cfg):
        write_receipt(cfg.receipts_dir, "fresh", date="2026-06-01T09:00:00")
        return 0

    def fake_fetch(cfg):
        write_coupons(coupons_dir, sections=_active_coupon_section())
        return 0

    monkeypatch.setattr(m.poll_mod, "poll", fake_poll)
    monkeypatch.setattr(m.coupons_mod, "fetch", fake_fetch)

    result = m.refresh_data()

    assert sorted(result["refreshed"]) == ["coupons", "receipts"]
    assert result["errors"] == {}
    # The newly downloaded data is included in the same response.
    assert result["receipts"]["count"] == 1
    assert result["receipts"]["receipts"][0]["id"] == "fresh"
    assert result["coupons"]["count"] == 1
    assert result["coupons"]["coupons"][0]["title"] == "Fresh"


def test_refresh_receipts_only_skips_coupons(
    receipts_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    called = {"poll": False, "fetch": False}

    def fake_poll(cfg):
        called["poll"] = True
        return 0

    def fake_fetch(cfg):
        called["fetch"] = True
        return 0

    monkeypatch.setattr(m.poll_mod, "poll", fake_poll)
    monkeypatch.setattr(m.coupons_mod, "fetch", fake_fetch)

    result = m.refresh_data(target="receipts")

    assert called == {"poll": True, "fetch": False}
    assert result["refreshed"] == ["receipts"]
    assert "receipts" in result
    assert "coupons" not in result


def test_refresh_coupons_only_skips_receipts(
    coupons_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    called = {"poll": False, "fetch": False}
    monkeypatch.setattr(m.poll_mod, "poll", lambda cfg: called.__setitem__("poll", True) or 0)
    monkeypatch.setattr(m.coupons_mod, "fetch", lambda cfg: called.__setitem__("fetch", True) or 0)

    result = m.refresh_data(target="coupons")

    assert called == {"poll": False, "fetch": True}
    assert result["refreshed"] == ["coupons"]
    assert "coupons" in result
    assert "receipts" not in result


def test_refresh_nonzero_exit_recorded_as_error(
    receipts_dir: Path, coupons_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(m.poll_mod, "poll", lambda cfg: 1)  # e.g. missing token
    monkeypatch.setattr(m.coupons_mod, "fetch", lambda cfg: 0)

    result = m.refresh_data()

    assert result["refreshed"] == ["coupons"]
    assert "receipts" in result["errors"]
    assert "code 1" in result["errors"]["receipts"]
    # Data is still served (from the cached snapshot, here empty).
    assert result["receipts"]["count"] == 0


def test_refresh_exception_is_caught_and_reported(
    receipts_dir: Path, coupons_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    def boom(cfg):
        raise RuntimeError("network down")

    monkeypatch.setattr(m.poll_mod, "poll", boom)
    monkeypatch.setattr(m.coupons_mod, "fetch", lambda cfg: 0)

    # Must not propagate — the tool degrades gracefully.
    result = m.refresh_data()

    assert "receipts" in result["errors"]
    assert "network down" in result["errors"]["receipts"]
    assert result["refreshed"] == ["coupons"]


def test_refresh_bad_target_raises():
    with pytest.raises(ValueError, match="Bad target"):
        m.refresh_data(target="nonsense")
