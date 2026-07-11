"""HMAC-SHA256 signature verification (FR-3, FR-4).

Scheme, matching Stripe's:

    X-Webhook-Signature: t=1720000000,v1=<hex>,v1=<hex during key rotation>

The signed payload is ``f"{t}.{raw_body}"`` -- the timestamp is *inside* the MAC,
so an attacker cannot take a valid capture and move it forward in time. Binding
them is the entire point of FR-4; a timestamp that were merely sent alongside the
signature could be rewritten at will.

Two properties this module exists to guarantee:

* **Constant-time comparison.** `hmac.compare_digest`, never `==`. A byte-by-byte
  comparison leaks how much of a forged signature was correct, and an attacker who
  can measure that recovers a valid signature one byte at a time.
* **A single failure mode.** Every rejection raises a `SignatureError` subclass,
  and the API answers all of them with an identical bare `401`. An unknown source,
  a malformed header, a stale timestamp, and a bad MAC are indistinguishable from
  outside. Anything else is an oracle: "unknown source" would tell an attacker
  which providers we are configured for.

Raw bytes throughout. The MAC covers the exact bytes on the wire; re-serialising
parsed JSON would change the whitespace and break every signature.
"""

from __future__ import annotations

import hmac
from datetime import datetime
from hashlib import sha256
from typing import Final

SIGNATURE_HEADER: Final = "X-Webhook-Signature"

_TIMESTAMP_FIELD: Final = "t"
_SIGNATURE_FIELD: Final = "v1"
_MAX_HEADER_BYTES: Final = 1024


class SignatureError(Exception):
    """Base for every verification failure. The API maps all of these to 401."""


class MalformedSignatureHeaderError(SignatureError):
    """The header is absent, oversized, or not `t=...,v1=...`."""


class TimestampOutsideToleranceError(SignatureError):
    """FR-4: the signed timestamp is too far from now, in either direction."""


class SignatureMismatchError(SignatureError):
    """FR-3: no provided signature matches one computed with our secret."""


def expected_signature(secret: str, timestamp: int, raw_body: bytes) -> str:
    """The hex HMAC-SHA256 of `timestamp.raw_body`. Exposed so tests can sign."""
    signed_payload = str(timestamp).encode("ascii") + b"." + raw_body
    return hmac.new(secret.encode("utf-8"), signed_payload, sha256).hexdigest()


def _parse_header(raw_header: str) -> tuple[int, tuple[str, ...]]:
    """Split `t=...,v1=...,v1=...` into a timestamp and every offered signature.

    Multiple `v1` values are permitted so a provider can rotate signing keys
    without a window of rejected deliveries.
    """
    if len(raw_header.encode("utf-8")) > _MAX_HEADER_BYTES:
        msg = "signature header too large"
        raise MalformedSignatureHeaderError(msg)

    timestamp: int | None = None
    signatures: list[str] = []

    for part in raw_header.split(","):
        key, separator, value = part.strip().partition("=")
        if not separator:
            msg = f"malformed segment in signature header: {part!r}"
            raise MalformedSignatureHeaderError(msg)

        if key == _TIMESTAMP_FIELD:
            try:
                timestamp = int(value)
            except ValueError as exc:
                msg = f"signature timestamp is not an integer: {value!r}"
                raise MalformedSignatureHeaderError(msg) from exc
        elif key == _SIGNATURE_FIELD:
            signatures.append(value)
        # Unknown fields are ignored on purpose: it lets the provider add a `v2`
        # scheme without breaking clients that only understand `v1`.

    if timestamp is None:
        msg = "signature header has no `t` field"
        raise MalformedSignatureHeaderError(msg)
    if not signatures:
        msg = "signature header has no `v1` field"
        raise MalformedSignatureHeaderError(msg)

    return timestamp, tuple(signatures)


def _check_timestamp(timestamp: int, now: datetime, tolerance_seconds: int) -> None:
    skew = abs(now.timestamp() - timestamp)
    if skew > tolerance_seconds:
        # Both directions. A future timestamp is as suspicious as a stale one and
        # would otherwise extend a captured request's usable life indefinitely.
        msg = f"signed timestamp is {skew:.0f}s away, tolerance is {tolerance_seconds}s"
        raise TimestampOutsideToleranceError(msg)


def verify_signature(
    *,
    secret: str,
    raw_body: bytes,
    raw_header: str | None,
    now: datetime,
    tolerance_seconds: int,
) -> None:
    """Return None if the request is authentic; raise `SignatureError` otherwise.

    Returning nothing rather than a bool: a caller cannot forget to check an
    exception the way they can forget to check a return value.
    """
    if raw_header is None:
        msg = f"missing {SIGNATURE_HEADER} header"
        raise MalformedSignatureHeaderError(msg)

    timestamp, offered = _parse_header(raw_header)

    # Cheap and independent of the secret, so do it before spending a SHA on a
    # body that a replaying attacker controls the size of.
    _check_timestamp(timestamp, now, tolerance_seconds)

    expected = expected_signature(secret, timestamp, raw_body)

    # compare_digest against *every* offered signature, and only then decide.
    # A short-circuiting `any()` would return on the first match, which is fine,
    # but `sum` keeps the number of comparisons independent of which one matched.
    matches = sum(hmac.compare_digest(expected, candidate) for candidate in offered)
    if matches == 0:
        msg = "no offered signature matches"
        raise SignatureMismatchError(msg)
