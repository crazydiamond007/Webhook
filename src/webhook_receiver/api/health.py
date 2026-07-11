"""Liveness and readiness probes (FR-21).

The distinction matters to an orchestrator and is routinely got wrong:

* ``/healthz`` -- "is this process alive?" It touches nothing external. If it
  checked the database, a database blip would make Kubernetes kill and restart
  every healthy app pod, turning a partial outage into a total one.
* ``/readyz``  -- "should this process receive traffic?" It checks the database,
  because an app that cannot write an event cannot honour NFR-3 and must be
  taken out of the load balancer rather than accept deliveries it will drop.
"""

from __future__ import annotations

from typing import Literal

import structlog
from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from webhook_receiver.api.state import AppStateDep

router = APIRouter(tags=["ops"])
log = structlog.get_logger(__name__)


class LivenessResponse(BaseModel):
    status: Literal["alive"]


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    database: Literal["ok", "unreachable"]


@router.get("/healthz", response_model=LivenessResponse, summary="Liveness probe")
async def healthz() -> LivenessResponse:
    """Alive if the event loop can answer. Deliberately checks no dependency."""
    return LivenessResponse(status="alive")


async def _database_reachable(engine: AsyncEngine) -> bool:
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError) as exc:
        # Both, and neither more (SPEC §6.6 forbids a bare except):
        #   SQLAlchemyError -- the pool or the server rejected us.
        #   OSError         -- the connection never got that far. asyncpg raises
        #                      ConnectionRefusedError / socket.gaierror straight
        #                      through, since there is no DBAPI cursor yet for
        #                      SQLAlchemy to wrap the failure in. Builtin
        #                      TimeoutError is an OSError too, so it lands here.
        # A readiness probe reports; it never swallows. The class name diagnoses
        # it -- the DSN would leak a password into the logs (NFR-6).
        log.warning("readiness.database_unreachable", error_class=type(exc).__name__)
        return False
    return True


@router.get("/readyz", response_model=ReadinessResponse, summary="Readiness probe")
async def readyz(response: Response, state: AppStateDep) -> ReadinessResponse:
    """503 when the database is unreachable, so the LB stops sending us traffic."""
    if not await _database_reachable(state.engine):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadinessResponse(status="not_ready", database="unreachable")

    return ReadinessResponse(status="ready", database="ok")
