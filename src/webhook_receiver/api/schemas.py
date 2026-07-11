"""Wire format for ingestion: the envelope we accept, and what we answer.

Pydantic lives here, at the edge. It validates untrusted input and produces a
pure `IncomingEvent` for the layers beneath (SPEC §4).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Final, Literal

from pydantic import BaseModel, Field, ValidationError

from webhook_receiver.domain.events import IncomingEvent, JsonObject, MalformedPayloadError

# Headers worth keeping for debugging a delivery. Everything else is dropped:
# the row is queried by operators, and a stored `Authorization` or signature
# header would be a credential sitting in a table (NFR-6).
_HEADER_ALLOWLIST: Final[frozenset[str]] = frozenset(
    {
        "content-type",
        "content-length",
        "user-agent",
        "idempotency-key",
        "x-request-id",
        "x-correlation-id",
    }
)

IDEMPOTENCY_KEY_HEADER: Final = "Idempotency-Key"
_MAX_IDEMPOTENCY_KEY_LENGTH: Final = 255


class EventEntity(BaseModel):
    """Which business entity the event concerns. Drives per-entity locking (FR-9)."""

    type: str = Field(min_length=1, max_length=64)
    id: str = Field(min_length=1, max_length=255)


class WebhookEnvelope(BaseModel):
    """The provider-agnostic envelope this service accepts.

    A real integration would have one adapter per provider mapping their shape
    onto this. Keeping the canonical form explicit means the rest of the system
    never branches on `source`.
    """

    id: str = Field(min_length=1, max_length=255, description="Provider's event id")
    type: str = Field(min_length=1, max_length=128, description="e.g. balance.credited")
    occurred_at: datetime = Field(description="When the event happened, per the provider")
    entity: EventEntity
    data: JsonObject = Field(default_factory=dict)
    # FR-10: present only where the provider supplies a sequence. `occurred_at`
    # is the fallback ordering key.
    sequence: int | None = Field(default=None, ge=0)


class IngestResponse(BaseModel):
    """`200` whether or not the event was new -- see FR-5.

    A duplicate is not an error. The provider did exactly what it promised, and
    answering anything but `2xx` would make it redeliver again, forever.
    """

    status: Literal["accepted"]
    event_id: int
    duplicate: bool = Field(description="True when this delivery hit the dedup constraint")


def redact_headers(headers: dict[str, str]) -> JsonObject:
    """Keep only the allowlisted headers, lowercased.

    Allowlist, not denylist: a denylist silently stores the next credential
    header some provider invents.
    """
    return {
        name.lower(): value for name, value in headers.items() if name.lower() in _HEADER_ALLOWLIST
    }


def parse_envelope(raw_body: bytes) -> WebhookEnvelope:
    """Validate the body. Raises `MalformedPayloadError` on anything unusable."""
    try:
        decoded = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        msg = f"body is not valid JSON: {exc}"
        raise MalformedPayloadError(msg) from exc

    if not isinstance(decoded, dict):
        msg = "body must be a JSON object"
        raise MalformedPayloadError(msg)

    try:
        return WebhookEnvelope.model_validate(decoded)
    except ValidationError as exc:
        # errors(), not str(exc): the latter embeds the offending input values,
        # which is the payload we promised never to log (NFR-6).
        fields = ", ".join(".".join(str(p) for p in error["loc"]) for error in exc.errors())
        msg = f"envelope failed validation on: {fields}"
        raise MalformedPayloadError(msg) from exc


def resolve_idempotency_key(envelope: WebhookEnvelope, header_value: str | None) -> str:
    """FR-5: the provider's event id, unless the caller overrides it.

    The override exists so a client retrying a request it never saw the response
    to can force the same dedup key.
    """
    if header_value is None:
        return envelope.id

    key = header_value.strip()
    if not key or len(key) > _MAX_IDEMPOTENCY_KEY_LENGTH:
        msg = f"{IDEMPOTENCY_KEY_HEADER} must be 1..{_MAX_IDEMPOTENCY_KEY_LENGTH} characters"
        raise MalformedPayloadError(msg)
    return key


def to_incoming_event(
    *,
    source: str,
    envelope: WebhookEnvelope,
    payload: JsonObject,
    headers: JsonObject,
    idempotency_key: str,
    signature_verified: bool,
) -> IncomingEvent:
    """Cross the boundary: validated wire object -> pure domain object."""
    return IncomingEvent(
        source=source,
        external_id=envelope.id,
        idempotency_key=idempotency_key,
        event_type=envelope.type,
        entity_type=envelope.entity.type,
        entity_id=envelope.entity.id,
        payload=payload,
        headers=headers,
        signature_verified=signature_verified,
        occurred_at=envelope.occurred_at,
        provider_sequence=envelope.sequence,
    )
