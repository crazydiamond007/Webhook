"""FR-3 and FR-4, without a database or a server.

Signature verification is pure: bytes, a secret, and a clock in. Every branch is
reachable here, and the stale-timestamp case is exact -- `FixedClock` states
"301 seconds late" rather than sleeping five minutes to reach it.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from webhook_receiver.adapters.clock import FixedClock
from webhook_receiver.api.signature import (
    MalformedSignatureHeaderError,
    SignatureMismatchError,
    TimestampOutsideToleranceError,
    expected_signature,
    verify_signature,
)

SECRET = "whsec_test"
TOLERANCE = 300
NOW = datetime(2026, 7, 10, 12, 0, 0, tzinfo=UTC)


def _header(timestamp: int, *signatures: str) -> str:
    return ",".join([f"t={timestamp}", *(f"v1={s}" for s in signatures)])


def _signed(body: bytes, *, at: int, secret: str = SECRET) -> str:
    return _header(at, expected_signature(secret, at, body))


def _verify(header: str | None, *, body: bytes = b'{"id":"evt_1"}', now: datetime = NOW) -> None:
    verify_signature(
        secret=SECRET,
        raw_body=body,
        raw_header=header,
        now=now,
        tolerance_seconds=TOLERANCE,
    )


class TestValid:
    def test_a_correct_signature_verifies(self) -> None:
        body = b'{"id":"evt_1"}'
        _verify(_signed(body, at=int(NOW.timestamp())), body=body)

    def test_signature_at_the_edge_of_tolerance_still_verifies(self) -> None:
        # Exactly `tolerance` seconds old is inside the window; the rejection is
        # strictly greater-than.
        clock = FixedClock(NOW)
        at = int(NOW.timestamp()) - TOLERANCE
        _verify(_signed(b"{}", at=at), body=b"{}", now=clock.now())

    def test_one_matching_signature_among_several_verifies(self) -> None:
        # Key rotation: the provider sends both the old and new signatures.
        at = int(NOW.timestamp())
        good = expected_signature(SECRET, at, b"{}")
        header = _header(at, "0" * 64, good)
        _verify(header, body=b"{}")


class TestInvalid:
    def test_a_tampered_body_is_rejected(self) -> None:
        at = int(NOW.timestamp())
        header = _signed(b'{"amount":100}', at=at)
        with pytest.raises(SignatureMismatchError):
            _verify(header, body=b'{"amount":999}')

    def test_the_wrong_secret_is_rejected(self) -> None:
        at = int(NOW.timestamp())
        header = _signed(b"{}", at=at, secret="whsec_attacker")
        with pytest.raises(SignatureMismatchError):
            _verify(header, body=b"{}")

    def test_a_missing_header_is_rejected(self) -> None:
        with pytest.raises(MalformedSignatureHeaderError):
            _verify(None)

    @pytest.mark.parametrize(
        "header",
        [
            "v1=deadbeef",  # no timestamp
            "t=1720000000",  # no signature
            "t=not-a-number,v1=deadbeef",  # timestamp not an int
            "garbage",  # no key=value at all
            "",  # empty
        ],
    )
    def test_a_malformed_header_is_rejected(self, header: str) -> None:
        with pytest.raises(MalformedSignatureHeaderError):
            _verify(header)

    def test_an_oversized_header_is_rejected_before_hashing(self) -> None:
        with pytest.raises(MalformedSignatureHeaderError):
            _verify("t=1720000000," + "v1=" + "a" * 2000)


class TestStale:
    def test_a_stale_timestamp_is_rejected(self) -> None:
        at = int(NOW.timestamp()) - (TOLERANCE + 1)
        with pytest.raises(TimestampOutsideToleranceError):
            _verify(_signed(b"{}", at=at), body=b"{}")

    def test_a_future_timestamp_is_rejected_too(self) -> None:
        # A future timestamp is as suspicious as a stale one: without this a
        # captured request could be given an arbitrarily long future life.
        at = int(NOW.timestamp()) + (TOLERANCE + 1)
        with pytest.raises(TimestampOutsideToleranceError):
            _verify(_signed(b"{}", at=at), body=b"{}")

    def test_the_timestamp_is_checked_before_the_mac(self) -> None:
        # A stale request with a *valid-shaped but wrong* signature must fail on
        # the timestamp, so we never spend a SHA on an attacker-sized body.
        at = int(NOW.timestamp()) - (TOLERANCE + 1)
        header = _header(at, "0" * 64)
        with pytest.raises(TimestampOutsideToleranceError):
            _verify(header, body=b"{}")
