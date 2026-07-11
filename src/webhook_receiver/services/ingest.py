"""Idempotent persistence of an accepted delivery (FR-1, FR-5, NFR-3).

The whole file is one idea: **let Postgres decide whether this event is new.**

The tempting alternative is a `SELECT` to check, then an `INSERT` if absent. It
passes every test written against it until two workers -- or two redeliveries of
the same event, which providers send concurrently -- run the `SELECT` at the same
instant. Both see nothing. Both insert. The check-then-act is not atomic, so it
fails under precisely the conditions it exists to handle.

`INSERT ... ON CONFLICT DO NOTHING RETURNING id` is atomic. The second delivery
loses the race inside the database, returns no row, and we know it was a
duplicate because the `RETURNING` came back empty. No read, no race.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from webhook_receiver.adapters.orm import WebhookEvent
from webhook_receiver.domain.events import IncomingEvent

log = structlog.get_logger(__name__)

DEDUP_CONSTRAINT = "uq_webhook_event_source_idempotency_key"


@dataclass(frozen=True, slots=True)
class IngestResult:
    event_id: int
    duplicate: bool


async def ingest_event(session: AsyncSession, event: IncomingEvent) -> IngestResult:
    """Persist `event`, or recognise it as a redelivery. Returns its row id.

    Never raises on a duplicate: FR-5 requires a repeat delivery to be answered
    `200`, because it is not an error. The provider did what it promised.
    """
    statement = (
        insert(WebhookEvent)
        .values(
            source=event.source,
            external_id=event.external_id,
            idempotency_key=event.idempotency_key,
            event_type=event.event_type,
            entity_type=event.entity_type,
            entity_id=event.entity_id,
            payload=event.payload,
            headers=event.headers,
            signature_verified=event.signature_verified,
            occurred_at=event.occurred_at,
            provider_sequence=event.provider_sequence,
        )
        # Name the constraint rather than the columns: if someone drops or
        # renames it, this statement fails loudly instead of silently inserting
        # duplicates.
        .on_conflict_do_nothing(constraint=DEDUP_CONSTRAINT)
        .returning(WebhookEvent.id)
    )

    inserted_id = (await session.execute(statement)).scalar_one_or_none()

    if inserted_id is not None:
        log.info(
            "ingest.accepted",
            event_id=inserted_id,
            source=event.source,
            event_type=event.event_type,
            entity_id=event.entity_id,
        )
        return IngestResult(event_id=inserted_id, duplicate=False)

    # DO NOTHING suppressed the insert, so the row already existed. Read its id
    # for the response. This SELECT is not part of the dedup decision -- that was
    # already made, atomically, above.
    existing_id = (
        await session.execute(
            select(WebhookEvent.id).where(
                WebhookEvent.source == event.source,
                WebhookEvent.idempotency_key == event.idempotency_key,
            )
        )
    ).scalar_one()

    log.info(
        "ingest.duplicate",
        event_id=existing_id,
        source=event.source,
        event_type=event.event_type,
    )
    return IngestResult(event_id=existing_id, duplicate=True)
