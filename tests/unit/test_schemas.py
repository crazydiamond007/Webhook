"""The wire boundary: envelope validation, header redaction, key resolution.

Pure functions, so no server. The point of interest is what each one *refuses*:
a non-object body, a credential header, an over-long idempotency key.
"""

from __future__ import annotations

import pytest

from webhook_receiver.api.schemas import (
    parse_envelope,
    redact_headers,
    resolve_idempotency_key,
)
from webhook_receiver.domain.events import MalformedPayloadError

VALID_BODY = (
    b'{"id":"evt_1","type":"balance.credited","occurred_at":"2026-07-10T12:00:00Z",'
    b'"entity":{"type":"account","id":"acct_1"},"data":{"amount":500}}'
)


class TestParseEnvelope:
    def test_a_valid_envelope_parses(self) -> None:
        envelope = parse_envelope(VALID_BODY)

        assert envelope.id == "evt_1"
        assert envelope.type == "balance.credited"
        assert envelope.entity.id == "acct_1"
        assert envelope.data == {"amount": 500}

    def test_non_json_is_rejected(self) -> None:
        with pytest.raises(MalformedPayloadError, match="not valid JSON"):
            parse_envelope(b"not json at all")

    def test_a_json_array_is_rejected(self) -> None:
        with pytest.raises(MalformedPayloadError, match="must be a JSON object"):
            parse_envelope(b"[1, 2, 3]")

    def test_a_missing_required_field_names_the_field_not_the_value(self) -> None:
        # NFR-6: the error must not echo the payload back into a log. It names
        # `entity`, never the surrounding data.
        body = b'{"id":"evt_1","type":"x","occurred_at":"2026-07-10T12:00:00Z"}'
        with pytest.raises(MalformedPayloadError) as excinfo:
            parse_envelope(body)

        assert "entity" in str(excinfo.value)
        assert "evt_1" not in str(excinfo.value)


class TestRedactHeaders:
    def test_only_allowlisted_headers_survive(self) -> None:
        redacted = redact_headers(
            {
                "Content-Type": "application/json",
                "User-Agent": "Stripe/1.0",
                "Authorization": "Bearer sk_live_secret",
                "X-Webhook-Signature": "t=1,v1=deadbeef",
            }
        )

        assert redacted == {"content-type": "application/json", "user-agent": "Stripe/1.0"}

    def test_a_credential_header_is_dropped(self) -> None:
        # The allowlist is the whole defence: a new credential header nobody
        # anticipated is dropped by default rather than stored.
        assert "authorization" not in redact_headers({"Authorization": "secret"})
        assert "x-webhook-signature" not in redact_headers({"X-Webhook-Signature": "t=1,v1=x"})


class TestResolveIdempotencyKey:
    def test_defaults_to_the_envelope_id(self) -> None:
        envelope = parse_envelope(VALID_BODY)
        assert resolve_idempotency_key(envelope, None) == "evt_1"

    def test_an_explicit_header_overrides_the_envelope_id(self) -> None:
        envelope = parse_envelope(VALID_BODY)
        assert resolve_idempotency_key(envelope, "client-chosen-key") == "client-chosen-key"

    def test_a_blank_override_is_rejected(self) -> None:
        envelope = parse_envelope(VALID_BODY)
        with pytest.raises(MalformedPayloadError):
            resolve_idempotency_key(envelope, "   ")

    def test_an_over_long_override_is_rejected(self) -> None:
        envelope = parse_envelope(VALID_BODY)
        with pytest.raises(MalformedPayloadError):
            resolve_idempotency_key(envelope, "k" * 256)
