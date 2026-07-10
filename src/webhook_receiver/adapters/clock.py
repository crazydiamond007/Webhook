"""Injectable clock (SPEC §6.4).

Time is an input, not an ambient fact. FR-4 rejects signatures outside a window,
so a test for "stale by 301 seconds" must be able to state that exactly rather
than sleep for five minutes and hope.

Everything here returns timezone-aware UTC. A naive datetime compared against an
aware one raises at runtime, and ruff's DTZ rules keep them out of the codebase.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    """The only source of 'now' in the application."""

    def now(self) -> datetime:
        """Timezone-aware, UTC."""
        ...


class SystemClock:
    """The real clock. Used everywhere except tests."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """A clock stopped at an instant, for tests and for `--sql` migrations.

    Lives in the adapter rather than in tests/ so the integration suite and the
    load test can share it without importing across the test boundary.
    """

    def __init__(self, instant: datetime) -> None:
        if instant.tzinfo is None:
            msg = "FixedClock requires a timezone-aware datetime"
            raise ValueError(msg)
        self._instant = instant

    def now(self) -> datetime:
        return self._instant

    def advance(self, seconds: float) -> None:
        self._instant += timedelta(seconds=seconds)
