"""The injectable clock (SPEC §6.4).

Small, but the guarantees it makes -- always tz-aware, `advance` moves forward --
are what let every time-dependent test state its scenario exactly.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from webhook_receiver.adapters.clock import FixedClock, SystemClock


def test_system_clock_returns_timezone_aware_utc() -> None:
    now = SystemClock().now()
    assert now.tzinfo is not None
    assert now.utcoffset() == UTC.utcoffset(None)


def test_fixed_clock_returns_its_instant() -> None:
    instant = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    assert FixedClock(instant).now() == instant


def test_fixed_clock_advances() -> None:
    clock = FixedClock(datetime(2026, 7, 10, 12, 0, tzinfo=UTC))
    clock.advance(90)
    assert clock.now() == datetime(2026, 7, 10, 12, 1, 30, tzinfo=UTC)


def test_fixed_clock_rejects_a_naive_datetime() -> None:
    # A naive instant would silently compare wrong against the aware `now` that
    # signature verification uses; refuse it at construction.
    with pytest.raises(ValueError, match="timezone-aware"):
        FixedClock(datetime(2026, 7, 10, 12, 0))  # noqa: DTZ001
