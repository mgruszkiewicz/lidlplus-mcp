"""Background scheduler that runs poll + coupons fetch a few times a day.

Enabled by setting `LIDL_SCHEDULE=1`. Fires at every Nth hour
(`LIDL_SCHEDULE_EVERY`, default 4) within a wall-clock window
[`LIDL_SCHEDULE_START_HOUR`, `LIDL_SCHEDULE_END_HOUR`] in `LIDL_SCHEDULE_TZ`
(default Europe/Warsaw). With the defaults that's 09:00, 13:00, 17:00, 21:00.

Runs in a daemon thread so it shares the MCP process — one container,
no cron sidecar.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import coupons as coupons_mod
from . import poll as poll_mod
from .config import load

log = logging.getLogger("lidlbridge.scheduler")


def _hours_window() -> tuple[int, int, int]:
    start = int(os.environ.get("LIDL_SCHEDULE_START_HOUR", "9"))
    end = int(os.environ.get("LIDL_SCHEDULE_END_HOUR", "21"))
    every = int(os.environ.get("LIDL_SCHEDULE_EVERY", "4"))
    return start, end, every


def _scheduled_hours() -> list[int]:
    start, end, every = _hours_window()
    return list(range(start, end + 1, every))


def _next_run(now: datetime) -> datetime:
    hours = _scheduled_hours()
    for h in hours:
        candidate = now.replace(hour=h, minute=0, second=0, microsecond=0)
        if candidate > now:
            return candidate
    tomorrow = (now + timedelta(days=1)).replace(
        hour=hours[0], minute=0, second=0, microsecond=0
    )
    return tomorrow


def _run_jobs() -> None:
    cfg = load()
    try:
        rc = poll_mod.poll(cfg)
        log.info("lidl-poll exit=%s", rc)
    except Exception:
        log.exception("lidl-poll crashed")
    try:
        rc = coupons_mod.fetch(cfg)
        log.info("lidl-coupons exit=%s", rc)
    except Exception:
        log.exception("lidl-coupons crashed")


def _loop(tz: ZoneInfo) -> None:
    log.info(
        "scheduler started; hours=%s tz=%s",
        _scheduled_hours(),
        tz.key,
    )
    if os.environ.get("LIDL_SCHEDULE_RUN_ON_START", "1") == "1":
        _run_jobs()
    while True:
        now = datetime.now(tz)
        target = _next_run(now)
        sleep_s = max(1.0, (target - now).total_seconds())
        log.info("scheduler sleeping until %s (%.0fs)", target.isoformat(), sleep_s)
        time.sleep(sleep_s)
        _run_jobs()


def maybe_start_scheduler() -> threading.Thread | None:
    if os.environ.get("LIDL_SCHEDULE", "0") != "1":
        return None
    tz = ZoneInfo(os.environ.get("LIDL_SCHEDULE_TZ", "Europe/Warsaw"))
    t = threading.Thread(target=_loop, args=(tz,), name="lidl-scheduler", daemon=True)
    t.start()
    return t
